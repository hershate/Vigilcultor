"""GitHub App JWT authentication (RS256, short-lived)."""

from __future__ import annotations

import time

import jwt

# GitHub rejects tokens issued more than 60s in the future; issue with leeway.
_ISSUE_LEEWAY = 60
_DEFAULT_LIFETIME = 600  # seconds; GitHub caps exp-iat at 10 minutes


def create_app_jwt(private_key_pem: str, app_id: int, *, lifetime: int = _DEFAULT_LIFETIME) -> str:
    """Create a short-lived RS256 JWT authenticating as the GitHub App."""
    now = int(time.time()) - _ISSUE_LEEWAY
    payload = {"iat": now, "exp": now + lifetime, "iss": str(app_id)}
    return jwt.encode(payload, private_key_pem, algorithm="RS256")
