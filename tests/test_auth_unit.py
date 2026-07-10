"""Pure-unit tests for the auth primitives (no database)."""
from app import auth


def test_password_hash_roundtrip():
    h = auth.hash_password("s3cret-passphrase")
    assert h and h != "s3cret-passphrase"
    assert auth.verify_password("s3cret-passphrase", h)
    assert not auth.verify_password("wrong-passphrase", h)


def test_password_hash_is_salted():
    # Two hashes of the same password must differ (random salt) yet both verify.
    a = auth.hash_password("same-password")
    b = auth.hash_password("same-password")
    assert a != b
    assert auth.verify_password("same-password", a)
    assert auth.verify_password("same-password", b)


def test_password_policy():
    assert auth.password_ok("12345678")          # exactly 8 → ok
    assert not auth.password_ok("1234567")        # 7 → too short
    assert not auth.password_ok("x" * 201)        # over the cap
    assert not auth.password_ok(None)             # type guard
