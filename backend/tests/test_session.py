from paatti.schema import Stage
from paatti.session import CallSession, RepeatRegistry


def make(registry=None):
    return CallSession(registry=registry or RepeatRegistry(), clock=FakeClock())


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        self.t += 5
        return self.t


def test_stage_only_moves_forward():
    s = make()
    s.add_scammer_turn("This is CBI, a case is registered against your Aadhaar")
    s.add_scammer_turn("An arrest warrant is issued")
    s.add_scammer_turn("Hello? This is CBI speaking")  # hook keywords again
    assert s.stage == Stage.FEAR
    assert [h.stage for h in s.stage_history] == [Stage.HOOK, Stage.FEAR]


def test_account_requires_readback_confirmation():
    s = make()
    s.add_scammer_turn("Transfer to account 3456 7890 1234 now")
    acct = s.intel.account_number
    assert acct and acct.value == "345678901234"
    assert not acct.confirmed
    assert s.pending_readback() == ("account_number", "345678901234")

    s.add_paatti_turn(s.readback_line("account_number", "345678901234"))
    s.add_scammer_turn("Yes yes correct, now transfer")
    assert s.intel.account_number.confirmed
    assert s.pending_readback() is None


def test_readback_correction_replaces_value_unconfirmed():
    s = make()
    s.add_scammer_turn("account 3456 7890 1234")
    s.add_paatti_turn(s.readback_line("account_number", "345678901234"))
    s.add_scammer_turn("No no, it is 3456 7890 1235")
    acct = s.intel.account_number
    assert acct.value == "345678901235"
    assert not acct.confirmed


def test_confirmation_without_readback_does_not_confirm():
    s = make()
    s.add_scammer_turn("account 3456 7890 1234")
    s.add_scammer_turn("yes correct")  # Paatti never read it back
    assert not s.intel.account_number.confirmed


def test_repeat_registry_flags_account_across_calls():
    reg = RepeatRegistry()
    a = make(reg)
    a.add_scammer_turn("account 3456 7890 1234")
    b = make(reg)
    b.add_scammer_turn("send to 3456 7890 1234 account")
    assert b.intel.account_number.repeat_sessions == [a.id]
    assert b.intel.account_number.confidence > a.intel.account_number.confidence


def test_time_wasted_and_audio_hash():
    s = make()
    s.add_audio_chunk(b"abc")
    s.add_audio_chunk(b"def")
    import hashlib

    assert s.audio_sha256() == hashlib.sha256(b"abcdef").hexdigest()
    assert s.time_wasted() > 0


def test_llm_finding_with_hallucinated_quote_is_rejected():
    from paatti.schema import Finding

    s = make()
    s.add_scammer_turn("Pay via UPI to cbi.case@okaxis")
    accepted = s.merge_findings(
        [Finding(field="ifsc", value="SBIN0001111", quote="IFSC SBIN0001111", turn_index=0)],
        source="llm",
    )
    assert accepted == 0
    assert s.intel.ifsc is None


def test_claimed_agency_not_overwritten_by_later_mentions():
    s = make()
    s.add_scammer_turn("This is Inspector Verma from CBI")
    s.add_scammer_turn("Pay to the RBI safe account, or police will come")
    assert s.intel.claimed_agency.value == "CBI"


def test_later_self_identification_overrides():
    s = make()
    s.add_scammer_turn("Calling from FedEx courier, your parcel is seized")
    s.add_scammer_turn("This is Sub Inspector Pawar, Mumbai Police")
    assert s.intel.claimed_agency.value == "Mumbai Police"


def test_mention_used_when_no_self_identification():
    s = make()
    s.add_scammer_turn("Your Aadhaar is in a CBI case")
    assert s.intel.claimed_agency.value == "CBI"
