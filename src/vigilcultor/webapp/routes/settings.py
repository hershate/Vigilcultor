"""Settings page: vault status, GitHub App credentials, agent executor/provider."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from vigilcultor.auth.roles import Role
from vigilcultor.auth.session import require
from vigilcultor.gh.client import GithubAppClient
from vigilcultor.secrets import vault
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import AppCredentials, User
from vigilcultor.webapp.app import render

router = APIRouter()

viewer_required = require(Role.VIEWER)
admin_required = require(Role.ADMIN)


def _credentials() -> AppCredentials | None:
    with get_session_maker()() as session:
        return session.get(AppCredentials, 1)


def _flash(message: str, **params: str) -> RedirectResponse:
    query = urlencode({"flash": message, **params})
    return RedirectResponse(f"/settings?{query}", status_code=303)


@router.get("/settings")
def settings_page(request: Request, user: User = Depends(viewer_required)):
    creds = _credentials()
    from vigilcultor.agent.executor import find_executor, verify_executor
    from vigilcultor.orchestrator.runner import load_provider

    executor_path = find_executor()
    executor_ok, executor_reason = verify_executor()
    provider = load_provider()
    return render(
        request,
        "settings.html",
        {
            "title": "page.settings.title",
            "desc": "page.settings.desc",
            "flash": request.query_params.get("flash"),
            "flash_slug": request.query_params.get("slug", ""),
            "flash_count": request.query_params.get("count", ""),
            "flash_detail": request.query_params.get("detail", ""),
            "vault_configured": vault.is_configured(),
            "vault_unlocked": vault.is_unlocked(),
            "app_id": creds.app_id if creds else None,
            "has_credentials": creds is not None,
            "webhook_url": str(request.base_url).rstrip("/") + "/webhook/github",
            "executor_path": str(executor_path) if executor_path else None,
            "executor_ok": executor_ok,
            "executor_reason": executor_reason,
            "provider_base_url": (provider or {}).get("base_url"),
            "provider_model": (provider or {}).get("model"),
            "provider_configured": provider is not None,
        },
    )


@router.post("/settings/agent-provider")
def save_agent_provider(
    request: Request,
    user: User = Depends(admin_required),
    base_url: str = Form(...),
    auth_token: str = Form(""),
    api_key: str = Form(""),
    model: str = Form(""),
):
    base_url = base_url.strip()
    auth_token = auth_token.strip()
    api_key = api_key.strip()
    model = model.strip()
    if not base_url:
        return _flash("settings.provider_invalid")
    if not auth_token and not api_key:
        return _flash("settings.provider_invalid")
    if not vault.is_configured() or not vault.is_unlocked():
        return _flash("settings.vault_locked_hint")
    config = {"base_url": base_url, "model": model or None}
    if auth_token:
        config["auth_token"] = auth_token
    if api_key:
        config["api_key"] = api_key
    try:
        blob = vault.encrypt(json.dumps(config, ensure_ascii=False))
    except vault.VaultError:
        return _flash("settings.vault_locked_hint")
    from vigilcultor.store.kv import kv_set

    kv_set("agent:provider", blob)
    return _flash("settings.saved")


@router.post("/settings/app-credentials")
def save_app_credentials(
    request: Request,
    user: User = Depends(admin_required),
    app_id: int = Form(...),
    private_key: str = Form(...),
    webhook_secret: str = Form(...),
):
    if not vault.is_configured() or not vault.is_unlocked():
        return _flash("settings.vault_locked_hint")
    try:
        private_key_enc = vault.encrypt(private_key.strip())
        webhook_secret_enc = vault.encrypt(webhook_secret.strip())
    except vault.VaultError:
        return _flash("settings.vault_locked_hint")

    with get_session_maker()() as session:
        creds = session.get(AppCredentials, 1)
        if creds is None:
            session.add(
                AppCredentials(
                    id=1,
                    app_id=app_id,
                    private_key_enc=private_key_enc,
                    webhook_secret_enc=webhook_secret_enc,
                )
            )
        else:
            creds.app_id = app_id
            creds.private_key_enc = private_key_enc
            creds.webhook_secret_enc = webhook_secret_enc
        session.commit()
    return _flash("settings.saved")


@router.post("/settings/app-credentials/test")
def test_app_credentials(request: Request, user: User = Depends(admin_required)):
    creds = _credentials()
    if creds is None:
        return _flash("settings.no_credentials")
    if not vault.is_unlocked():
        return _flash("settings.vault_locked_hint")
    try:
        private_key = vault.decrypt(creds.private_key_enc)
        client = GithubAppClient(app_id=creds.app_id, private_key_pem=private_key)
        app = client.get_app()
        count = len(client.list_installations())
    except vault.VaultError:
        return _flash("settings.vault_locked_hint")
    except Exception as exc:  # noqa: BLE001 - surface transport/API failures to the panel
        return _flash("settings.test_failed", detail=str(exc)[:200])
    return _flash("settings.test_ok", slug=app.get("slug", "?"), count=str(count))
