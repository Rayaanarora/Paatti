from paatti.db import Database, DbRepeatRegistry
from paatti.session import CallSession


def test_save_session_and_history(tmp_path):
    db = Database(tmp_path / "t.db")
    reg = DbRepeatRegistry(db)
    s = CallSession(reg, channel="phone", caller_id="+919876543210")
    db.save_session(s)
    s.add_scammer_turn("This is CBI. Transfer Rs 50,000 to account 3456 7890 1234")
    s.add_paatti_turn(s.readback_line("account_number", "345678901234"))
    s.add_scammer_turn("yes correct")
    s.dispatches.append({"destination": "ncrp_1930", "reference": "NCRP-1", "at": 1.0,
                         "seconds_since_first_capture": 10.0, "within_golden_hour": True,
                         "identifiers_sent": {"suspect_bank_account": "345678901234"}})
    db.save_session(s)
    db.save_session(s)  # idempotent
    c = db.counts()
    assert c["calls"] == 1 and c["turns"] == 3 and c["dispatches"] == 1
    assert c["intel_fields"] == 3  # agency, amount, account
    hist = db.call_history()[0]
    assert hist["channel"] == "phone" and hist["confirmed_ids"] == 1 and hist["stage"] == "extraction"


def test_mule_registry_survives_restart(tmp_path):
    path = tmp_path / "t.db"
    db = Database(path)
    a = CallSession(DbRepeatRegistry(db))
    a.add_scammer_turn("account 3456 7890 1234")
    db.save_session(a)
    db.close()

    db2 = Database(path)  # simulated restart
    b = CallSession(DbRepeatRegistry(db2))
    b.add_scammer_turn("send to account 3456 7890 1234")
    assert b.intel.account_number.repeat_sessions == [a.id]
    top = db2.mule_registry()[0]
    assert top["value"] == "345678901234" and top["calls"] == 2
