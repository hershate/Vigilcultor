"""Role model: admin > operator > viewer."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


ROLE_ORDER = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}

VALID_ROLES = {role.value for role in Role}


def is_valid(role: str) -> bool:
    return role in VALID_ROLES


def at_least(role: str | Role, minimum: str | Role) -> bool:
    """True when *role* satisfies *minimum* or above."""
    return ROLE_ORDER[Role(role)] >= ROLE_ORDER[Role(minimum)]
