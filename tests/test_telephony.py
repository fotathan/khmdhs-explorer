"""CTI telephony unit tests — pure/normalisation logic and the caller lookup.

No DB or Asterisk needed: number parsing and AMI event parsing are pure, and
lookup_caller is exercised against a fake cursor so we can assert the query
priority and the trailing-digits match key without a live schema.
"""
from app import telephony as t


# --------------------------------------------------------------------------- #
# Number normalisation
# --------------------------------------------------------------------------- #
def test_key_is_format_agnostic_for_the_same_greek_number():
    forms = ["+30 210 123 4567", "00302101234567", "2101234567", "210-123 4567"]
    keys = {t.match_key(f) for f in forms}
    assert len(keys) == 1, f"expected one key, got {keys}"


def test_mobile_and_landline_keys_differ():
    assert t.match_key("6971234567") != t.match_key("2101234567")


def test_e164_when_parseable():
    assert t.normalize_number("2101234567")["e164"] == "+302101234567"


def test_short_internal_code_has_no_usable_key():
    # A 4-digit extension must not resolve to a caller.
    assert len(t.match_key("1002")) < t.LOOKUP_MIN_DIGITS


def test_normalize_handles_empty_and_none():
    for v in (None, "", "   "):
        r = t.normalize_number(v)
        assert r["key"] == "" and r["e164"] is None


# --------------------------------------------------------------------------- #
# AMI event parsing
# --------------------------------------------------------------------------- #
def test_channel_extension():
    assert t.channel_extension("PJSIP/1002-00000003") == "1002"
    assert t.channel_extension("PJSIP/agent-b-0000abcd") == "agent"  # first token
    assert t.channel_extension("garbage") is None
    assert t.channel_extension(None) is None


def test_screenpop_from_dialbegin():
    ev = {"Event": "DialBegin", "DestChannel": "PJSIP/1002-000000a1",
          "CallerIDNum": "302101234567"}
    assert t.screenpop_from_event(ev) == ("1002", "302101234567")


def test_screenpop_ignores_other_events_and_missing_fields():
    assert t.screenpop_from_event({"Event": "Newstate"}) is None
    assert t.screenpop_from_event(
        {"Event": "DialBegin", "DestChannel": "PJSIP/1002-1"}) is None  # no caller
    assert t.screenpop_from_event(
        {"Event": "DialBegin", "CallerIDNum": "210"}) is None            # no dest


# --------------------------------------------------------------------------- #
# lookup_caller against a fake cursor
# --------------------------------------------------------------------------- #
class FakeCursor:
    """Returns queued rows in order; records the params each query saw."""
    def __init__(self, rows):
        self._rows = list(rows)
        self._last = None
        self.seen_params = []

    def execute(self, sql, params=None):
        self._last = self._rows.pop(0) if self._rows else None
        self.seen_params.append(params)

    def fetchone(self):
        return self._last


def test_lookup_prefers_customer_profile():
    cur = FakeCursor([{"user_id": 7, "full_name": "Maria", "company": "Acme"}])
    m = t.lookup_caller(cur, "2101234567")
    assert m["source"] == "customer"
    assert m["customer_user_id"] == 7
    assert m["url"] == "/admin/crm/7"
    # the match key was passed to SQL, format-agnostic
    assert cur.seen_params[0]["key"] == t.match_key("2101234567")


def test_lookup_falls_through_to_contractor():
    # customer_profile miss, customer_contact miss, economic_operator hit
    cur = FakeCursor([None, None, {"vat_number": "EL123456789", "name": "Beta AE"}])
    m = t.lookup_caller(cur, "+30 2310 000000")
    assert m["source"] == "contractor"
    assert m["customer_user_id"] is None
    assert m["url"] == "/contractor/EL123456789"


def test_lookup_returns_none_for_unmatched():
    cur = FakeCursor([None, None, None])
    assert t.lookup_caller(cur, "2101234567") is None


def test_lookup_skips_query_for_too_short_number():
    cur = FakeCursor([{"user_id": 1, "full_name": "x", "company": None}])
    assert t.lookup_caller(cur, "123") is None
    assert cur.seen_params == []  # never queried


# --------------------------------------------------------------------------- #
# Screen-pop routing (assigned manager -> fallback all admins)
# --------------------------------------------------------------------------- #
def test_route_to_manager_when_online():
    r = t.route_recipients("1002", manager_id=42, manager_online=True)
    assert r == {"exts": {"1002"}, "user_ids": {42}, "admins": False}


def test_route_falls_back_to_admins_when_manager_offline():
    r = t.route_recipients("1002", manager_id=42, manager_online=False)
    assert r == {"exts": {"1002"}, "user_ids": set(), "admins": True}


def test_route_broadcasts_when_no_manager():
    # unknown caller / customer with no assigned manager
    r = t.route_recipients("1002", manager_id=None, manager_online=False)
    assert r["admins"] is True and r["user_ids"] == set()
    assert r["exts"] == {"1002"}  # the ringing agent always included


def test_hub_push_dedupes_and_selects():
    import asyncio

    class FakeWS:
        def __init__(self): self.sent = []
        async def send_json(self, p): self.sent.append(p)

    hub = t._Hub()
    agent = FakeWS(); manager = FakeWS(); other = FakeWS()
    hub.add(agent, user_id=1, ext="1002", admin=True)     # ringing agent
    hub.add(manager, user_id=42, ext="1009", admin=True)  # the manager
    hub.add(other, user_id=7, ext="1050", admin=True)     # uninvolved admin
    reached = asyncio.run(
        hub.push({"type": "incoming"}, exts={"1002"}, user_ids={42}, admins=False))
    assert reached == 2
    assert len(agent.sent) == 1 and len(manager.sent) == 1 and other.sent == []
    assert hub.user_connected(42) and not hub.user_connected(999)


# --------------------------------------------------------------------------- #
# Recording correlation (DialBegin Uniqueid -> per-extension recording path),
# which feeds recording_path onto a logged call for post-call summarisation.
# --------------------------------------------------------------------------- #
def _svc():
    return t.TelephonyService(lambda: None)


def test_note_recording_maps_both_extensions_to_uniqueid_path():
    svc = _svc()
    svc._note_recording({
        "Event": "DialBegin", "Uniqueid": "call-abc",
        "Channel": "PJSIP/1002-0001", "DestChannel": "PJSIP/1001-0002",
        "CallerIDNum": "2101234567"})
    want = t.os.path.join(t.CALL_RECORDING_DIR, "call-abc" + t.CALL_RECORDING_EXT)
    assert svc.recent_recording_for("1002") == want   # the agent leg
    assert svc.recent_recording_for("1001") == want   # the customer leg
    assert svc.recent_recording_for("9999") is None    # unknown extension


def test_note_recording_ignores_non_dialbegin_and_missing_uniqueid():
    svc = _svc()
    svc._note_recording({"Event": "Hangup", "Uniqueid": "x", "Channel": "PJSIP/1002-1"})
    svc._note_recording({"Event": "DialBegin", "Channel": "PJSIP/1002-1"})  # no Uniqueid
    assert svc.recent_recording_for("1002") is None


def test_recent_recording_expires_after_max_age():
    svc = _svc()
    svc._note_recording({"Event": "DialBegin", "Uniqueid": "call-old",
                         "Channel": "PJSIP/1002-1", "DestChannel": "PJSIP/1001-2"})
    assert svc.recent_recording_for("1002", max_age_s=3600) is not None
    assert svc.recent_recording_for("1002", max_age_s=0) is None  # too old for a 0s window


# --------------------------------------------------------------------------- #
# Ephemeral SIP credentials (Phase 4, Option A — SIP_AUTH_MODE=realtime).
# DB-backed: mint/reuse/rotate/revoke against the test schema.
# --------------------------------------------------------------------------- #
def test_mint_creates_ps_auths_and_credential(db):
    from tests.helpers import make_user
    uid = make_user("agent_mint")
    cur = db.cursor()
    secret, exp = t.mint_or_reuse_credential(cur, uid, "1002", "1002-auth")
    assert secret and exp is not None
    cur.execute("SELECT username, password FROM proc.ps_auths WHERE id='1002-auth'")
    row = cur.fetchone()
    assert row["username"] == "1002" and row["password"] == secret   # Asterisk reads this
    cur.execute("SELECT secret FROM proc.sip_credential WHERE user_id=%s", (uid,))
    assert cur.fetchone()["secret"] == secret


def test_mint_reuses_secret_within_ttl(db):
    from tests.helpers import make_user
    uid = make_user("agent_reuse")
    cur = db.cursor()
    s1, _ = t.mint_or_reuse_credential(cur, uid, "1002", "1002-auth")
    s2, _ = t.mint_or_reuse_credential(cur, uid, "1002", "1002-auth")
    assert s1 == s2   # still fresh -> same secret, no needless rotation


def test_mint_rotates_when_expired(db):
    from tests.helpers import make_user
    uid = make_user("agent_rotate")
    cur = db.cursor()
    s1, _ = t.mint_or_reuse_credential(cur, uid, "1002", "1002-auth")
    cur.execute("UPDATE proc.sip_credential SET expires_at = now() - interval '1 hour' "
                "WHERE user_id=%s", (uid,))
    s2, _ = t.mint_or_reuse_credential(cur, uid, "1002", "1002-auth")
    assert s1 != s2   # expired -> new secret
    cur.execute("SELECT password FROM proc.ps_auths WHERE id='1002-auth'")
    assert cur.fetchone()["password"] == s2   # realtime auth updated to the new one


def test_revoke_deletes_both_rows(db):
    from tests.helpers import make_user
    uid = make_user("agent_revoke")
    cur = db.cursor()
    t.mint_or_reuse_credential(cur, uid, "1002", "1002-auth")
    t.revoke_credential(cur, uid)
    cur.execute("SELECT 1 FROM proc.ps_auths WHERE id='1002-auth'")
    assert cur.fetchone() is None
    cur.execute("SELECT 1 FROM proc.sip_credential WHERE user_id=%s", (uid,))
    assert cur.fetchone() is None


def test_set_password_revokes_credential(db):
    """Invalidating sessions (password change) also kills the softphone."""
    from tests.helpers import make_user
    from app import auth as _auth
    uid = make_user("agent_pwrevoke")
    cur = db.cursor()
    t.mint_or_reuse_credential(cur, uid, "1002", "1002-auth")
    _auth.set_password(cur, uid, "new-password-123")
    cur.execute("SELECT 1 FROM proc.sip_credential WHERE user_id=%s", (uid,))
    assert cur.fetchone() is None
