"""Notification evaluation, allocation and durable provider transitions."""
from __future__ import annotations

import datetime as dt

import pytest

from app.api_v1.schemas import PushAlertInput
from app.push_provider import ReceiptResult, TicketResult
from tests.helpers import expire_sub, grant, make_user
from tests.test_mobile_api_auth import DEVICE


@pytest.fixture(autouse=True)
def _cleanup_worker_acts(db):
    yield
    db.cursor().execute(
        "DELETE FROM proc.procurement_act WHERE adam LIKE '26PROC-WORKER-%'")


def _login(client, username):
    response = client.post("/api/v1/auth/login", json={
        "username": username, "password": "pw-123456", "device": DEVICE})
    assert response.status_code == 200
    return response.json()["access_token"]


def _headers(token, **extra):
    return {"Authorization": f"Bearer {token}", **extra}


def _device(client, token):
    response = client.post("/api/v1/devices", headers=_headers(
        token, **{"Idempotency-Key": "worker-device-register-01"}), json={
            **DEVICE, "permission_status": "granted",
            "push_token": "ExpoPushToken[worker-secret-token]",
        })
    assert response.status_code == 200
    return int(response.json()["id"])


def _profile(cur, uid, q="worker-match"):
    from app import auth
    return auth.create_search_profile(
        cur, name="Worker profile", scope="customer", owner_id=uid,
        params={"q": q, "type": ["notice"]}, based_on_id=None,
        created_by=uid)


def _act(cur, suffix, now, title="worker-match procurement", deadline=None):
    adam = f"26PROC-WORKER-{suffix}"
    cur.execute("""INSERT INTO proc.procurement_act
      (adam,type,title,origin,data_source,submission_date,ingested_at,
       final_submission_date,cancelled)
      VALUES (%s,'notice',%s,'import','khmdhs',%s,%s,%s,false)""",
      (adam, title, now, now, deadline))
    return adam


def _subscribe(cur, uid, profile_id, *, now, mode="immediate", deadlines=False,
               new_matches=True):
    from app import mobile_notifications
    alert = mobile_notifications.put_push_alert(
        cur, user_id=uid, profile_id=profile_id, has_access=True,
        payload=PushAlertInput(
            active=True, delivery_mode=mode, new_matches=new_matches,
            deadlines=deadlines, lead_days=[7, 1]))
    cur.execute("""UPDATE proc.push_subscription SET last_cursor=%s
                   WHERE id=%s""", (now - dt.timedelta(hours=1), int(alert.id)))
    return int(alert.id)


def test_quiet_hours_cross_midnight_and_release_at_local_end():
    from app.notification_worker import quiet_release
    now = dt.datetime(2026, 9, 14, 20, 30, tzinfo=dt.timezone.utc)  # 23:30 Athens
    release = quiet_release(now, "Europe/Athens", dt.time(22), dt.time(8))
    assert release == dt.datetime(2026, 9, 15, 5, 0, tzinfo=dt.timezone.utc)
    midday = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    assert quiet_release(midday, "Europe/Athens", "22:00", "08:00") == midday


def test_evaluation_creates_one_event_and_delivery_then_advances_cursor(
    client, db,
):
    from app.notification_worker import evaluate_all
    uid = make_user("notification_worker_user")
    grant(uid)
    token = _login(client, "notification_worker_user")
    _device(client, token)
    cur = db.cursor()
    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    profile = _profile(cur, uid, q="worker-one")
    sub = _subscribe(cur, uid, profile, now=now)
    adam = _act(cur, "ONE", now - dt.timedelta(minutes=10),
                title="worker-one procurement")

    first = evaluate_all(cur, now)
    assert first["events"] == 1 and first["deliveries"] == 1
    second = evaluate_all(cur, now + dt.timedelta(minutes=1))
    assert second["events"] == 0 and second["deliveries"] == 0
    cur.execute("SELECT * FROM proc.notification_event WHERE user_id=%s", (uid,))
    event = cur.fetchone()
    assert event["adam"] == adam
    assert "worker-match" not in event["body"]
    assert event["context"]["saved_search_names"] == ["Worker profile"]
    cur.execute("SELECT last_cursor FROM proc.push_subscription WHERE id=%s", (sub,))
    assert cur.fetchone()["last_cursor"] == now + dt.timedelta(minutes=1)


def test_lapsed_accounts_advance_without_events_and_global_pause_is_inbox_only(
    client, db,
):
    from app.notification_worker import evaluate_all
    uid = make_user("notification_worker_lapsed")
    grant(uid)
    token = _login(client, "notification_worker_lapsed")
    _device(client, token)
    cur = db.cursor()
    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    profile = _profile(cur, uid, q="worker-lapsed")
    sub = _subscribe(cur, uid, profile, now=now)
    _act(cur, "LAPSED", now - dt.timedelta(minutes=5),
         title="worker-lapsed procurement")
    expire_sub(uid)
    assert evaluate_all(cur, now)["events"] == 0
    cur.execute("SELECT last_cursor FROM proc.push_subscription WHERE id=%s", (sub,))
    assert cur.fetchone()["last_cursor"] == now

    grant(uid)
    _act(cur, "PAUSED", now + dt.timedelta(minutes=5),
         title="worker-lapsed paused procurement")
    cur.execute("""UPDATE proc.mobile_notification_preference SET paused=true
                   WHERE user_id=%s""", (uid,))
    out = evaluate_all(cur, now + dt.timedelta(minutes=10))
    assert out["events"] == 1 and out["deliveries"] == 0


def test_default_cap_queues_five_individuals_and_one_overflow_summary(client, db):
    from app.notification_worker import evaluate_all
    uid = make_user("notification_worker_cap")
    grant(uid)
    token = _login(client, "notification_worker_cap")
    _device(client, token)
    cur = db.cursor()
    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    profile = _profile(cur, uid, q="cap-match")
    _subscribe(cur, uid, profile, now=now)
    for n in range(7):
        _act(cur, f"CAP-{n}", now - dt.timedelta(minutes=n + 1),
             title=f"cap-match procurement {n}")
    out = evaluate_all(cur, now)
    assert out["events"] == 7
    assert out["deliveries"] == 6
    assert out["overflow"] == 2
    cur.execute("""SELECT e.event_type,count(DISTINCT d.event_id) AS n
                   FROM proc.notification_delivery d
                   JOIN proc.notification_event e ON e.id=d.event_id
                   WHERE e.user_id=%s GROUP BY e.event_type""", (uid,))
    assert {row["event_type"]: row["n"] for row in cur.fetchall()} == {
        "new_match": 5, "daily_summary": 1}


def test_daily_mode_creates_inbox_items_but_pushes_one_summary(client, db):
    from app.notification_worker import evaluate_all
    uid = make_user("notification_worker_daily")
    grant(uid)
    token = _login(client, "notification_worker_daily")
    _device(client, token)
    cur = db.cursor()
    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    profile = _profile(cur, uid, q="worker-daily")
    _subscribe(cur, uid, profile, now=now, mode="daily")
    _act(cur, "DAILY", now - dt.timedelta(minutes=5),
         title="worker-daily procurement")
    out = evaluate_all(cur, now)
    assert out["events"] == 1 and out["deliveries"] == 0
    # The event was created after today's 08:30 Athens summary, so it rolls
    # into tomorrow's completed customer-local window.
    out = evaluate_all(cur, dt.datetime(
        2026, 9, 15, 5, 31, tzinfo=dt.timezone.utc))
    assert out["events"] == 0 and out["deliveries"] == 1
    cur.execute("""SELECT e.event_type,count(d.id) AS deliveries
                   FROM proc.notification_event e
                   LEFT JOIN proc.notification_delivery d ON d.event_id=e.id
                   WHERE e.user_id=%s GROUP BY e.event_type""", (uid,))
    assert {row["event_type"]: row["deliveries"] for row in cur.fetchall()} == {
        "new_match": 0, "daily_summary": 1}


def test_events_and_deliveries_carry_the_run_clock_so_the_cap_holds(client, db):
    """The worker judges everything by the `now` it is given — the summary
    window, the customer's local day for the cap, expiry. Rows stamped by the
    DB's now() instead sat on another clock: a second run the same day could not
    see the first run's deliveries and pushed past the cap."""
    from app.notification_worker import EVENT_TTL, evaluate_all
    uid = make_user("notification_worker_clock")
    grant(uid)
    token = _login(client, "notification_worker_clock")
    _device(client, token)
    cur = db.cursor()
    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    profile = _profile(cur, uid, q="clock-match")
    _subscribe(cur, uid, profile, now=now)
    for n in range(7):
        _act(cur, f"CLOCK-{n}", now - dt.timedelta(minutes=n + 1),
             title=f"clock-match procurement {n}")
    assert evaluate_all(cur, now)["deliveries"] == 6          # the cap, spent

    cur.execute("""SELECT e.created_at,e.expires_at,d.created_at AS queued_at
                   FROM proc.notification_event e
                   JOIN proc.notification_delivery d ON d.event_id=e.id
                   WHERE e.user_id=%s""", (uid,))
    rows = cur.fetchall()
    assert rows and all(r["created_at"] == now and r["queued_at"] == now
                        and r["expires_at"] == now + EVENT_TTL for r in rows)

    later = now + dt.timedelta(minutes=30)                    # same Athens day
    _act(cur, "CLOCK-LATE", later - dt.timedelta(minutes=10),
         title="clock-match procurement late")
    out = evaluate_all(cur, later)
    assert out["events"] == 1 and out["deliveries"] == 0 and out["overflow"] == 1

    tomorrow = now + dt.timedelta(days=1)                     # a fresh cap
    _act(cur, "CLOCK-NEXT", tomorrow - dt.timedelta(minutes=10),
         title="clock-match procurement next")
    assert evaluate_all(cur, tomorrow)["deliveries"] == 1


def test_deadline_ledger_prevents_duplicates_and_moved_deadline_rearms(
    client, db,
):
    from app.notification_worker import evaluate_all
    uid = make_user("notification_worker_deadline")
    grant(uid)
    token = _login(client, "notification_worker_deadline")
    _device(client, token)
    cur = db.cursor()
    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    profile = _profile(cur, uid, q="worker-deadline")
    _subscribe(cur, uid, profile, now=now, deadlines=True, new_matches=False)
    adam = _act(
        cur, "DEADLINE", now - dt.timedelta(days=1),
        title="worker-deadline procurement", deadline=now + dt.timedelta(days=7))
    assert evaluate_all(cur, now)["events"] == 1
    assert evaluate_all(cur, now + dt.timedelta(minutes=1))["events"] == 0

    moved = now + dt.timedelta(days=6)
    cur.execute("UPDATE proc.procurement_act SET final_submission_date=%s WHERE adam=%s",
                (moved, adam))
    assert evaluate_all(cur, now + dt.timedelta(minutes=2))["events"] == 1
    cur.execute("""SELECT count(*) AS n FROM proc.push_deadline_notice n
                   JOIN proc.push_subscription s ON s.id=n.push_subscription_id
                   WHERE s.user_id=%s""", (uid,))
    assert cur.fetchone()["n"] == 2


class Provider:
    def __init__(self):
        self.messages = []

    def send(self, messages):
        self.messages = messages
        return [TicketResult(True, f"expo-ticket-{index}")
                for index, _message in enumerate(messages, 1)]

    def receipts(self, ids):
        return {value: ReceiptResult("accepted") for value in ids}


class RejectProvider(Provider):
    def send(self, messages):
        self.messages = messages
        return [TicketResult(False, error_code="DeviceNotRegistered")
                for _message in messages]


class OutageProvider(Provider):
    def send(self, messages):
        from app.push_provider import ProviderTemporaryError
        raise ProviderTemporaryError("provider_network")


def test_delivery_submission_and_receipt_are_distinct_durable_states(
    client, db, monkeypatch,
):
    from app.notification_worker import check_receipts, evaluate_all, submit_due
    uid = make_user("notification_delivery_user")
    grant(uid)
    token = _login(client, "notification_delivery_user")
    _device(client, token)
    cur = db.cursor()
    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    profile = _profile(cur, uid, q="worker-delivery")
    _subscribe(cur, uid, profile, now=now)
    _act(cur, "DELIVERY", now - dt.timedelta(minutes=5),
         title="worker-delivery procurement")
    evaluate_all(cur, now)
    monkeypatch.setenv("PUSH_DELIVERY_ENABLED", "1")
    provider = Provider()
    submitted = submit_due(cur, provider, now)
    assert submitted["accepted"] == 1
    assert provider.messages[0]["to"] == "ExpoPushToken[worker-secret-token]"
    assert provider.messages[0]["data"]["event_id"].isdigit()
    cur.execute("SELECT state,terminal_at FROM proc.notification_delivery")
    row = cur.fetchone()
    assert row["state"] == "accepted" and row["terminal_at"] is None

    receipts = check_receipts(
        cur, provider, now + dt.timedelta(minutes=16))
    assert receipts["accepted"] == 1
    cur.execute("SELECT state,terminal_at FROM proc.notification_delivery")
    row = cur.fetchone()
    assert row["state"] == "accepted" and row["terminal_at"] is not None


def test_provider_delivery_is_off_by_default(db, monkeypatch):
    from app.notification_worker import submit_due
    monkeypatch.delenv("PUSH_DELIVERY_ENABLED", raising=False)
    assert submit_due(db.cursor())["disabled"] is True


def test_unregistered_provider_token_is_rejected_and_disabled(
    client, db, monkeypatch,
):
    from app.notification_worker import evaluate_all, submit_due
    uid = make_user("notification_unregistered")
    grant(uid)
    token = _login(client, "notification_unregistered")
    device_id = _device(client, token)
    cur = db.cursor()
    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    profile = _profile(cur, uid, q="worker-unregistered")
    _subscribe(cur, uid, profile, now=now)
    _act(cur, "UNREGISTERED", now - dt.timedelta(minutes=5),
         title="worker-unregistered procurement")
    evaluate_all(cur, now)
    monkeypatch.setenv("PUSH_DELIVERY_ENABLED", "1")
    out = submit_due(cur, RejectProvider(), now)
    assert out["rejected"] == 1
    cur.execute("""SELECT permission_status,push_token_ciphertext
                   FROM proc.mobile_device WHERE id=%s""", (device_id,))
    row = cur.fetchone()
    assert row["permission_status"] == "denied"
    assert row["push_token_ciphertext"] is None


def test_provider_outage_retries_without_losing_delivery(client, db, monkeypatch):
    from app.notification_worker import evaluate_all, submit_due
    uid = make_user("notification_provider_outage")
    grant(uid)
    token = _login(client, "notification_provider_outage")
    _device(client, token)
    cur = db.cursor()
    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    profile = _profile(cur, uid, q="worker-outage")
    _subscribe(cur, uid, profile, now=now)
    _act(cur, "OUTAGE", now - dt.timedelta(minutes=5),
         title="worker-outage procurement")
    evaluate_all(cur, now)
    monkeypatch.setenv("PUSH_DELIVERY_ENABLED", "1")
    out = submit_due(cur, OutageProvider(), now)
    assert out["retry"] == 1
    cur.execute("""SELECT state,attempt_count,next_attempt_at,terminal_at
                   FROM proc.notification_delivery""")
    row = cur.fetchone()
    assert row["state"] == "retry" and row["attempt_count"] == 1
    assert row["next_attempt_at"] > now and row["terminal_at"] is None


def test_expired_submission_lease_is_reclaimed(client, db, monkeypatch):
    from app.notification_worker import evaluate_all, submit_due
    uid = make_user("notification_lease_recovery")
    grant(uid)
    token = _login(client, "notification_lease_recovery")
    _device(client, token)
    cur = db.cursor()
    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    profile = _profile(cur, uid, q="worker-lease")
    _subscribe(cur, uid, profile, now=now)
    _act(cur, "LEASE", now - dt.timedelta(minutes=5),
         title="worker-lease procurement")
    evaluate_all(cur, now)
    cur.execute("""UPDATE proc.notification_delivery SET state='submitting',
                   lease_expires_at=%s,claimed_by='dead-worker'""",
                (now - dt.timedelta(seconds=1),))
    monkeypatch.setenv("PUSH_DELIVERY_ENABLED", "1")
    out = submit_due(cur, Provider(), now)
    assert out["accepted"] == 1
    cur.execute("SELECT state,attempt_count FROM proc.notification_delivery")
    row = cur.fetchone()
    assert row["state"] == "accepted" and row["attempt_count"] == 1


def test_delivery_expires_when_its_device_becomes_unavailable(client, db):
    from app.notification_worker import evaluate_all, expire_stale_deliveries
    uid = make_user("notification_device_unavailable")
    grant(uid)
    token = _login(client, "notification_device_unavailable")
    device_id = _device(client, token)
    cur = db.cursor()
    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    profile = _profile(cur, uid, q="worker-device-unavailable")
    _subscribe(cur, uid, profile, now=now)
    _act(cur, "DEVICE-UNAVAILABLE", now - dt.timedelta(minutes=5),
         title="worker-device-unavailable procurement")
    evaluate_all(cur, now)
    cur.execute("UPDATE proc.mobile_device SET enabled=false WHERE id=%s",
                (device_id,))
    assert expire_stale_deliveries(cur, now) == 1
    cur.execute("SELECT state,error_code FROM proc.notification_delivery")
    row = cur.fetchone()
    assert row["state"] == "expired"
    assert row["error_code"] == "device_unavailable"


def test_one_event_fans_out_to_two_devices_but_consumes_one_cap_slot(
    client, db,
):
    from app.notification_worker import evaluate_all
    uid = make_user("notification_two_devices")
    grant(uid)
    token = _login(client, "notification_two_devices")
    _device(client, token)
    second = {
        **DEVICE,
        "installation_id": "a3ec57da-3cb3-459e-b392-5c065f6f00a8",
    }
    second_login = client.post("/api/v1/auth/login", json={
        "username": "notification_two_devices", "password": "pw-123456",
        "device": second,
    })
    assert second_login.status_code == 200
    second_token = second_login.json()["access_token"]
    response = client.post("/api/v1/devices", headers=_headers(
        second_token, **{"Idempotency-Key": "worker-device-register-02"}), json={
            **second, "permission_status": "granted",
            "push_token": "ExpoPushToken[worker-second-token]",
        })
    assert response.status_code == 200
    cur = db.cursor()
    now = dt.datetime(2026, 9, 14, 10, 0, tzinfo=dt.timezone.utc)
    profile = _profile(cur, uid, q="worker-two-devices")
    _subscribe(cur, uid, profile, now=now)
    _act(cur, "TWO-DEVICES", now - dt.timedelta(minutes=5),
         title="worker-two-devices procurement")
    out = evaluate_all(cur, now)
    assert out["events"] == 1 and out["deliveries"] == 1
    cur.execute("SELECT count(*) AS n FROM proc.notification_delivery")
    assert cur.fetchone()["n"] == 2
