"""Mule Intel Report: JSON and PDF.

The JSON is the source of truth; the PDF renders it. Identifiers that were
not confirmed by read-back are kept out of the NCRP-ready block.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .schema import IntelField
from .session import CallSession

REPORT_VERSION = "1.0"

LEGAL_SECTIONS = [
    {
        "code": "BNS 318",
        "title": "Cheating",
        "basis": "Deceiving the victim into transferring money under a false pretext of a criminal case.",
    },
    {
        "code": "BNS 319",
        "title": "Cheating by personation",
        "basis": "Caller pretended to be an officer of a government agency.",
    },
    {
        "code": "IT Act 66D",
        "title": "Cheating by personation using a computer resource",
        "basis": "Suggested where the impersonation used phone, video call or messaging apps.",
    },
]

LABELS = {
    "account_number": "Bank account",
    "ifsc": "IFSC",
    "upi_id": "UPI ID",
    "phone_number": "Phone number",
}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _fmt_inr(value: str) -> str:
    n = int(value)
    s = str(n)
    if len(s) <= 3:
        return f"Rs {s}"
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "Rs " + ",".join(groups + [tail])


def _identifier(kind: str, f: IntelField) -> dict:
    return {
        "type": kind,
        "value": f.value,
        "confidence": f.confidence,
        "confirmed_by_readback": f.confirmed if kind in ("account_number", "ifsc", "upi_id") else None,
        "repeat_sessions": f.repeat_sessions,
        "source": f.source,
        "evidence": {"quote": f.quote, "turn_index": f.turn_index, "t_seconds": f.t},
    }


def build_report(session: CallSession) -> dict:
    intel = session.intel
    transcript = [t.model_dump(mode="json") for t in session.turns]
    transcript_hash = hashlib.sha256(
        json.dumps(transcript, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()

    identifiers = []
    for kind in ("account_number", "ifsc", "upi_id"):
        f = getattr(intel, kind)
        if f:
            identifiers.append(_identifier(kind, f))
    identifiers += [_identifier("phone_number", p) for p in intel.phone_numbers]

    def confirmed(kind: str) -> str | None:
        f = getattr(intel, kind)
        return f.value if f and f.confirmed else None

    unconfirmed = [
        i["type"] for i in identifiers if i["confirmed_by_readback"] is False
    ]

    return {
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "session_id": session.id,
        "call": {
            "started_at": _iso(session.started_at),
            "time_wasted_seconds": session.time_wasted(),
            "turns": len(session.turns),
        },
        "claimed_agency": intel.claimed_agency.model_dump(mode="json") if intel.claimed_agency else None,
        "amount_demanded": intel.amount.model_dump(mode="json") if intel.amount else None,
        "apps_requested": [a.model_dump(mode="json") for a in intel.apps_requested],
        "scam_stage": session.stage.value if session.stage else None,
        "stage_timeline": [e.model_dump(mode="json") for e in session.stage_history],
        "identifiers": identifiers,
        "unconfirmed_identifiers": unconfirmed,
        "legal_sections": LEGAL_SECTIONS,
        "chain_of_custody": {
            "hash_algorithm": "SHA-256",
            "audio_sha256": session.audio_sha256(),
            "transcript_sha256": transcript_hash,
            "note": "Audio hash covers the raw caller audio bytes received, in order."
            if session.audio_sha256()
            else "Text-mode call: no audio captured; transcript hash provided.",
        },
        # Proposed layout for NCRP / Suspect Registry submission. Not a live integration.
        "ncrp_fields": {
            "category": "Online Financial Fraud / Digital Arrest",
            "impersonated_agency": intel.claimed_agency.value if intel.claimed_agency else None,
            "amount_demanded_inr": int(intel.amount.value) if intel.amount else None,
            "suspect_bank_account": confirmed("account_number"),
            "suspect_ifsc": confirmed("ifsc"),
            "suspect_upi_id": confirmed("upi_id"),
            "suspect_mobile_numbers": [p.value for p in intel.phone_numbers],
            "platforms_used": [a.value for a in intel.apps_requested],
        },
        "transcript": transcript,
        "disclaimer": (
            "Generated by the Paatti honeypot prototype. Identifiers were stated by the caller on a "
            "honeypot line; verify with the issuing bank before action. NCRP field layout is a proposed "
            "format, not a live integration."
        ),
    }


def render_pdf(report: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Mule Intel Report {report['session_id']}", author="Paatti",
    )
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=18, spaceAfter=2, alignment=0)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=10, textColor=colors.HexColor("#555555"))
    mono = ParagraphStyle("m", parent=body, fontName="Courier", fontSize=8, leading=10)

    def p(text: object, style=body) -> Paragraph:
        return Paragraph(escape(str(text)) if text is not None else "-", style)

    def table(rows, widths, header=True):
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        style = [
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]
        if header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#efe7da")))
        t.setStyle(TableStyle(style))
        return t

    agency = report["claimed_agency"]["value"] if report["claimed_agency"] else "-"
    amount = _fmt_inr(report["amount_demanded"]["value"]) if report["amount_demanded"] else "-"
    story = [
        Paragraph("Mule Intel Report", h1),
        p(f"Session {report['session_id']} | generated {report['generated_at']} | report v{report['report_version']}", small),
        Spacer(1, 6),
        table(
            [
                [p("Claimed agency"), p(agency), p("Amount demanded"), p(amount)],
                [p("Furthest stage"), p((report["scam_stage"] or "-").title()), p("Time wasted"),
                 p(f"{int(report['call']['time_wasted_seconds'] // 60)} min {int(report['call']['time_wasted_seconds'] % 60)} s")],
                [p("Call started (UTC)"), p(report["call"]["started_at"]), p("Apps requested"),
                 p(", ".join(a["value"] for a in report["apps_requested"]) or "-")],
            ],
            [32 * mm, 55 * mm, 32 * mm, 59 * mm], header=False,
        ),
        Paragraph("Identifiers", h2),
    ]

    rows = [[p("Type"), p("Value"), p("Confidence"), p("Read-back"), p("Repeat")]]
    for i in report["identifiers"]:
        rb = {True: "Confirmed", False: "NOT confirmed", None: "n/a"}[i["confirmed_by_readback"]]
        rows.append([
            p(LABELS.get(i["type"], i["type"])), p(i["value"], mono), p(f"{i['confidence']:.2f}"), p(rb),
            p(", ".join(i["repeat_sessions"]) or "-"),
        ])
    if len(rows) == 1:
        rows.append([p("No identifiers captured"), p(""), p(""), p(""), p("")])
    story.append(table(rows, [30 * mm, 52 * mm, 24 * mm, 30 * mm, 42 * mm]))
    if report["unconfirmed_identifiers"]:
        story.append(p("Unconfirmed identifiers are excluded from the NCRP-ready fields.", small))

    story.append(Paragraph("Evidence quotes", h2))
    ev = [[p("t (s)"), p("Field"), p("Verbatim quote")]]
    evidence_items = list(report["identifiers"])
    for key in ("claimed_agency", "amount_demanded"):
        if report[key]:
            evidence_items.append({"type": key, "evidence": {"quote": report[key]["quote"], "t_seconds": report[key]["t"]}})
    for a in report["apps_requested"]:
        evidence_items.append({"type": "app_requested", "evidence": {"quote": a["quote"], "t_seconds": a["t"]}})
    for i in sorted(evidence_items, key=lambda x: x["evidence"]["t_seconds"]):
        ev.append([p(f"{i['evidence']['t_seconds']:.0f}"), p(LABELS.get(i["type"], i["type"].replace("_", " "))),
                   p(f"“{i['evidence']['quote']}”")])
    story.append(table(ev, [16 * mm, 32 * mm, 130 * mm]))

    story.append(Paragraph("Scam stage timeline", h2))
    tl = [[p("t (s)"), p("Stage"), p("Turn")]] + [
        [p(f"{e['t']:.0f}"), p(e["stage"].title()), p(e["turn_index"])] for e in report["stage_timeline"]
    ]
    story.append(table(tl, [16 * mm, 40 * mm, 20 * mm]))

    story.append(Paragraph("Applicable legal sections", h2))
    story.append(table(
        [[p("Section"), p("Offence"), p("Basis")]]
        + [[p(s["code"]), p(s["title"]), p(s["basis"])] for s in report["legal_sections"]],
        [24 * mm, 55 * mm, 99 * mm],
    ))

    coc = report["chain_of_custody"]
    story.append(Paragraph("Chain of custody", h2))
    story.append(table(
        [
            [p("Audio SHA-256"), p(coc["audio_sha256"] or "none (text mode)", mono)],
            [p("Transcript SHA-256"), p(coc["transcript_sha256"], mono)],
        ],
        [36 * mm, 142 * mm], header=False,
    ))
    story.append(p(coc["note"], small))

    story.append(Paragraph("NCRP-ready fields (proposed format)", h2))
    nc = report["ncrp_fields"]
    story.append(table(
        [[p(k.replace("_", " ")), p(", ".join(map(str, v)) if isinstance(v, list) else v, mono)] for k, v in nc.items()],
        [50 * mm, 128 * mm], header=False,
    ))

    story.append(Paragraph("Transcript", h2))
    for t in report["transcript"]:
        who = "SCAMMER" if t["role"] == "scammer" else "PAATTI"
        story.append(p(f"[{t['t']:.0f}s] {who}: {t['text']}", body))

    story.append(Spacer(1, 8))
    story.append(p(report["disclaimer"], small))
    doc.build(story)
    return buf.getvalue()
