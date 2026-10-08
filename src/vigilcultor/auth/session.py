"""Cookie sessions signed with itsdangerous; user lookup against the store."""

from __future__ import annotations

import datetime as dt

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from vigilcultor.auth.roles import Role, at_least
from vigilcultor.config import load_or_create_secret_key
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import User

COOKIE = "vgc_session"
SESSION_TTL = dt.timedelta(days=7)

_signer_cache: dict[str, TimestampSigner] = {}


def _signer() -> TimestampSigner:
    key = load_or_create_secret_key()
    if key not in _signer_cache:
        _signer_cache[key] = TimestampSigner(key)
    return _signer_cache[key]


def issue_token(user_id: int) -> str:
    return _signer().sign(str(user_id)).decode("ascii")


def parse_token(token: str) -> int | None:
    try:
        value = _signer().unsign(token, max_age=int(SESSION_TTL.total_seconds()))
    except (BadSignature, SignatureExpired):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def current_user(request: Request) -> User | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    user_id = parse_token(token)
    if user_id is None:
        return None
    with get_session_maker()() as session:
        user = session.get(User, user_id)
        if user is None or user.disabled:
            return None
        return user


def require(minimum: str | Role = Role.VIEWER):
    """Route dependency: authenticated user with at least *minimum* role.

    Unauthenticated requests raise 401 (the app redirects them to /login);
    insufficient role raises 403.
    """

    def dependency(request: Request) -> User:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401)
        if not at_least(user.role, minimum):
            raise HTTPException(status_code=403)
        return user

    return dependency
