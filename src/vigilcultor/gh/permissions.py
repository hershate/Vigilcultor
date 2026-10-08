"""Collaborator resolution: only write-capable actors may trigger actions."""

from __future__ import annotations

from vigilcultor.gh.client import InstallationClient

WRITE_PERMISSIONS = {"admin", "write"}


def is_write_permission(permission: str | None) -> bool:
    return permission in WRITE_PERMISSIONS


def actor_is_collaborator(
    client: InstallationClient, full_name: str, actor_login: str | None
) -> bool:
    """Polled events carry no actor and must never trigger actions."""
    if not actor_login:
        return False
    try:
        permission = client.get_permission(full_name, actor_login)
    except Exception:  # noqa: BLE001 - fail closed on API errors
        return False
    return is_write_permission(permission)
