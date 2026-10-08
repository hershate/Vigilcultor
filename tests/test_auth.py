"""Password hashing, roles and session token tests."""

from __future__ import annotations

from vigilcultor.auth.password import hash_password, verify_password
from vigilcultor.auth.roles import Role, at_least
from vigilcultor.auth.session import issue_token, parse_token


def test_password_roundtrip():
    hashed = hash_password("s3cretpass!")
    assert verify_password("s3cretpass!", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("s3cretpass!", "not-a-hash")


def test_role_ordering():
    assert at_least(Role.ADMIN, Role.OPERATOR)
    assert at_least(Role.OPERATOR, Role.VIEWER)
    assert not at_least(Role.VIEWER, Role.OPERATOR)
    assert not at_least(Role.OPERATOR, Role.ADMIN)


def test_token_roundtrip(tmp_env):
    token = issue_token(42)
    assert parse_token(token) == 42
    assert parse_token("garbage") is None
