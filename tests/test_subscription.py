"""Subscription expiry — the signal the paywall (`_is_gated`) is built on."""
from app import auth
from tests.helpers import connect, expire_sub, grant, make_user


def test_no_subscription_is_none(db):
    uid = make_user("cust1", "pw-123456")
    assert auth.current_subscription(db.cursor(), uid) is None


def test_active_subscription(db):
    uid = make_user("cust2", "pw-123456")
    grant(uid, "pro", days=365)
    sub = auth.current_subscription(db.cursor(), uid)
    assert sub is not None
    assert sub["active"] is True
    assert sub["product_code"] == "pro"


def test_expired_subscription_is_inactive(db):
    uid = make_user("cust3", "pw-123456")
    expire_sub(uid, "pro")
    sub = auth.current_subscription(db.cursor(), uid)
    assert sub is not None
    assert sub["active"] is False


def test_latest_grant_wins(db):
    """current_subscription returns the grant with the greatest expires_at."""
    uid = make_user("cust4", "pw-123456")
    expire_sub(uid, "pro")          # an old, expired grant
    grant(uid, "pro", days=30)      # a newer, active grant
    sub = auth.current_subscription(db.cursor(), uid)
    assert sub["active"] is True
