"""SQLite persistence: calls, transcript, intel, stages, dispatches, mule registry.

The live call runs in memory (CallSession); every state change is written
through here so calls survive restarts and the mule registry links
identifiers across calls. See docs/DATABASE.md for the ER diagram.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .session import CallSession

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS calls (
    id                TEXT PRIMARY KEY,
    persona           TEXT NOT NULL,
    channel           TEXT NOT NULL CHECK (channel IN ('web', 'web-phone', 'phone')),
    caller_id         TEXT,
    started_at        REAL NOT NULL,
    ended_at          REAL,
    stage             TEXT CHECK (stage IN ('hook', 'fear', 'isolation', 'extraction')),
    time_wasted_s     REAL NOT NULL DEFAULT 0,
    audio_sha256      TEXT,
    updated_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id   TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    idx       INTEGER NOT NULL,
    role      TEXT NOT NULL CHECK (role IN ('scammer', 'paatti')),
    text      TEXT NOT NULL,
    t         REAL NOT NULL,
    UNIQUE (call_id, idx)
);

CREATE TABLE IF NOT EXISTS intel_fields (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id      TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    field        TEXT NOT NULL,
    value        TEXT NOT NULL,
    quote        TEXT NOT NULL,
    t            REAL NOT NULL,
    turn_idx     INTEGER NOT NULL,
    confidence   REAL NOT NULL,
    confirmed    INTEGER NOT NULL DEFAULT 0,
    source       TEXT NOT NULL,
    UNIQUE (call_id, field, value)
);

CREATE TABLE IF NOT EXISTS stage_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id   TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    stage     TEXT NOT NULL,
    t         REAL NOT NULL,
    turn_idx  INTEGER NOT NULL,
    UNIQUE (call_id, stage)
);

CREATE TABLE IF NOT EXISTS mule_identifiers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL CHECK (kind IN ('account_number', 'upi_id', 'phone_number')),
    value          TEXT NOT NULL,
    first_seen_at  REAL NOT NULL,
    UNIQUE (kind, value)
);

CREATE TABLE IF NOT EXISTS sightings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    identifier_id  INTEGER NOT NULL REFERENCES mule_identifiers(id) ON DELETE CASCADE,
    call_id        TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    seen_at        REAL NOT NULL,
    UNIQUE (identifier_id, call_id)
);

CREATE TABLE IF NOT EXISTS dispatches (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id                    TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    destination                TEXT NOT NULL CHECK (destination IN ('bank_fraud_desk', 'ncrp_1930')),
    reference                  TEXT NOT NULL UNIQUE,
    at                         REAL NOT NULL,
    seconds_since_first_capture REAL,
    within_golden_hour         INTEGER NOT NULL,
    payload_json               TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turns_call ON turns(call_id);
CREATE INDEX IF NOT EXISTS idx_intel_call ON intel_fields(call_id);
CREATE INDEX IF NOT EXISTS idx_sightings_identifier ON sightings(identifier_id);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---- calls -----------------------------------------------------------
    def save_session(self, s: CallSession) -> None:
        """Upsert the call and its child rows in one transaction."""
        intel_rows = []
        for name in ("claimed_agency", "amount", "account_number", "ifsc", "upi_id"):
            f = getattr(s.intel, name)
            if f:
                intel_rows.append((name, f))
        intel_rows += [("phone_number", f) for f in s.intel.phone_numbers]
        intel_rows += [("app_requested", f) for f in s.intel.apps_requested]
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO calls (id, persona, channel, caller_id, started_at, ended_at, stage,
                                      time_wasted_s, audio_sha256, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET persona=excluded.persona, channel=excluded.channel,
                     started_at=excluded.started_at, ended_at=excluded.ended_at, stage=excluded.stage,
                     time_wasted_s=excluded.time_wasted_s, audio_sha256=excluded.audio_sha256,
                     caller_id=excluded.caller_id, updated_at=excluded.updated_at""",
                (s.id, s.persona_key, s.channel, s.caller_id, s.started_at, s.ended_at,
                 s.stage.value if s.stage else None, s.time_wasted(), s.audio_sha256(), time.time()),
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO turns (call_id, idx, role, text, t) VALUES (?,?,?,?,?)",
                [(s.id, t.index, t.role, t.text, t.t) for t in s.turns],
            )
            self._conn.execute("DELETE FROM intel_fields WHERE call_id = ?", (s.id,))
            self._conn.executemany(
                """INSERT INTO intel_fields (call_id, field, value, quote, t, turn_idx, confidence, confirmed, source)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                [(s.id, name, f.value, f.quote, f.t, f.turn_index, f.confidence, int(f.confirmed), f.source)
                 for name, f in intel_rows],
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO stage_events (call_id, stage, t, turn_idx) VALUES (?,?,?,?)",
                [(s.id, e.stage.value, e.t, e.turn_index) for e in s.stage_history],
            )
            self._conn.executemany(
                """INSERT OR IGNORE INTO dispatches (call_id, destination, reference, at,
                       seconds_since_first_capture, within_golden_hour, payload_json) VALUES (?,?,?,?,?,?,?)""",
                [(s.id, d["destination"], d["reference"], d["at"], d.get("seconds_since_first_capture"),
                  int(d["within_golden_hour"]), json.dumps(d.get("identifiers_sent", {}))) for d in s.dispatches],
            )

    def _ensure_call_row(self, call_id: str) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO calls (id, persona, channel, started_at, updated_at)
               VALUES (?, 'paatti', 'web', ?, ?)""", (call_id, time.time(), time.time()))

    # ---- mule registry ---------------------------------------------------
    def record_sighting(self, kind: str, value: str, call_id: str) -> list[str]:
        """Register an identifier seen in a call; return the other calls it appeared in."""
        now = time.time()
        with self._lock, self._conn:
            self._ensure_call_row(call_id)
            self._conn.execute(
                "INSERT OR IGNORE INTO mule_identifiers (kind, value, first_seen_at) VALUES (?,?,?)", (kind, value, now))
            ident = self._conn.execute(
                "SELECT id FROM mule_identifiers WHERE kind=? AND value=?", (kind, value)).fetchone()["id"]
            self._conn.execute(
                "INSERT OR IGNORE INTO sightings (identifier_id, call_id, seen_at) VALUES (?,?,?)", (ident, call_id, now))
            rows = self._conn.execute(
                "SELECT call_id FROM sightings WHERE identifier_id=? AND call_id<>? ORDER BY seen_at",
                (ident, call_id)).fetchall()
        return [r["call_id"] for r in rows]

    def mule_registry(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT m.kind, m.value, m.first_seen_at, COUNT(s.id) AS calls
                   FROM mule_identifiers m JOIN sightings s ON s.identifier_id = m.id
                   GROUP BY m.id ORDER BY calls DESC, m.first_seen_at DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def call_history(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT c.id, c.persona, c.channel, c.caller_id, c.started_at, c.ended_at, c.stage,
                          c.time_wasted_s,
                          (SELECT COUNT(*) FROM intel_fields i WHERE i.call_id=c.id
                             AND i.field IN ('account_number','ifsc','upi_id') AND i.confirmed=1) AS confirmed_ids
                   FROM calls c ORDER BY c.started_at DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict:
        with self._lock:
            return {t: self._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 fixed names
                    for t in ("calls", "turns", "intel_fields", "stage_events", "mule_identifiers", "sightings",
                              "dispatches")}


class DbRepeatRegistry:
    """Drop-in for session.RepeatRegistry backed by the mule registry tables."""

    def __init__(self, db: Database):
        self.db = db

    def lookup_and_add(self, field: str, value: str, session_id: str) -> list[str]:
        return self.db.record_sighting(field, value, session_id)
