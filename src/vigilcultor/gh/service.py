"""Runtime wiring between stored credentials and API clients.

Module-level factories are the patch seam used by tests and by the future
scheduler/worker processes.
"""

from __future__ import annotations

from vigilcultor.gh.client import (
    CachedInstallationToken,
    GithubAppClient,
    InstallationClient,
    RepoInfo,
)
from vigilcultor.secrets import vault
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import AppCredentials

_token_caches: dict[int, CachedInstallationToken] = {}


def build_app_client() -> GithubAppClient | None:
    """App client from stored credentials; None when unavailable (unconfigured/locked)."""
    with get_session_maker()() as session:
        creds = session.get(AppCredentials, 1)
    if creds is None:
        return None
    try:
        private_key = vault.decrypt(creds.private_key_enc)
    except vault.VaultError:
        return None
    return GithubAppClient(app_id=creds.app_id, private_key_pem=private_key)


def installation_client_for(installation_id: int) -> InstallationClient | None:
    """Installation client with a cached token; None when credentials are unusable."""
    app_client = build_app_client()
    if app_client is None:
        return None
    cached = _token_caches.get(installation_id)
    if cached is None or cached._app_client is not app_client:  # noqa: SLF001 - cache invalidation
        cached = CachedInstallationToken(app_client, installation_id)
        _token_caches[installation_id] = cached
    return InstallationClient(cached.get)


def reset_caches() -> None:
    _token_caches.clear()


def bot_login() -> str | None:
    """App 机器人账号名（``slug[bot]``）；凭据不可用时 None。"""
    app_client = build_app_client()
    if app_client is None:
        return None
    try:
        app = app_client.get_app()
    except Exception:  # noqa: BLE001 - 网络失败即无法识别自身评论
        return None
    slug = app.get("slug")
    return f"{slug}[bot]" if slug else None


def resolve_repo(full_name: str) -> tuple[int, RepoInfo] | None:
    """Find which installation owns *full_name*; None when absent everywhere."""
    from sqlalchemy import select

    from vigilcultor.store.db import get_session_maker
    from vigilcultor.store.models import Installation

    with get_session_maker()() as session:
        installation_ids = [
            row[0] for row in session.execute(select(Installation.installation_id)).all()
        ]
    for installation_id in installation_ids:
        client = installation_client_for(installation_id)
        if client is None:
            continue
        try:
            repos = client.list_repos()
        except Exception:  # noqa: BLE001 - one installation failing must not block others
            continue
        for repo in repos:
            if repo.full_name == full_name:
                return installation_id, repo
    return None
