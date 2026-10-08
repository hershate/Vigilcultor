"""Runtime wiring between stored credentials and API clients.

Module-level factories are the patch seam used by tests and by the future
scheduler/worker processes.
"""

from __future__ import annotations

from vigilcultor.gh.client import CachedInstallationToken, GithubAppClient, InstallationClient
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
