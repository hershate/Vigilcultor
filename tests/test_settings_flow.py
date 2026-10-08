"""Settings flow tests: vault unlock/lock, app credentials save, connection test."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from vigilcultor.secrets import vault
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import AppCredentials, Rule

SETUP = {
    "username": "admin",
    "password": "s3cretpass!",
    "master_password": "master-pass-123",
    "master_password2": "master-pass-123",
}


def make_client(tmp_env) -> TestClient:
    from vigilcultor.webapp.app import create_app

    client = TestClient(create_app(), follow_redirects=False)
    client.post("/setup", data=SETUP)
    return client


def test_setup_seeds_vault_and_default_rule(tmp_env):
    client = make_client(tmp_env)
    assert vault.is_configured()
    assert vault.is_unlocked()
    with get_session_maker()() as session:
        rules = session.scalars(select(Rule)).all()
    assert rules and rules[0].label == "需要维护"
    # settings page renders with webhook hint
    page = client.get("/settings")
    assert page.status_code == 200
    assert "/webhook/github" in page.text


def test_setup_rejects_mismatched_master(tmp_env):
    from vigilcultor.webapp.app import create_app

    client = TestClient(create_app(), follow_redirects=False)
    bad = dict(SETUP, master_password2="different-pass")
    response = client.post("/setup", data=bad)
    assert response.status_code == 422


def test_lock_then_unlock_cycle(tmp_env):
    client = make_client(tmp_env)
    assert client.post("/vault/lock").status_code == 303
    assert not vault.is_unlocked()

    wrong = client.post("/vault/unlock", data={"password": "nope"})
    assert wrong.status_code == 401

    ok = client.post("/vault/unlock", data={"password": "master-pass-123"})
    assert ok.status_code == 303
    assert vault.is_unlocked()


def test_save_credentials_requires_unlocked_vault(tmp_env):
    client = make_client(tmp_env)
    client.post("/vault/lock")
    response = client.post(
        "/settings/app-credentials",
        data={"app_id": "42", "private_key": "PEM", "webhook_secret": "whsec"},
    )
    assert response.status_code == 303
    assert "vault_locked_hint" in response.headers["location"]


def test_save_and_test_credentials(tmp_env, monkeypatch):
    client = make_client(tmp_env)

    saved = client.post(
        "/settings/app-credentials",
        data={"app_id": "42", "private_key": "-----BEGIN KEY", "webhook_secret": "whsec_x"},
    )
    assert saved.status_code == 303
    assert "settings.saved" in saved.headers["location"]
    with get_session_maker()() as session:
        creds = session.get(AppCredentials, 1)
    assert creds is not None and creds.app_id == 42
    assert creds.private_key_enc != "-----BEGIN KEY"  # stored encrypted

    class FakeClient:
        def __init__(self, **kwargs): ...

        def get_app(self):
            return {"slug": "vigilcultor-ci"}

        def list_installations(self):
            return [object(), object()]

    from vigilcultor.webapp.routes import settings as settings_routes

    monkeypatch.setattr(settings_routes, "GithubAppClient", FakeClient)
    tested = client.post("/settings/app-credentials/test")
    assert tested.status_code == 303
    assert "test_ok" in tested.headers["location"]
    assert "vigilcultor-ci" in tested.headers["location"]


def test_viewer_cannot_manage(tmp_env):
    client = make_client(tmp_env)
    with get_session_maker()() as session:
        session.execute(
            __import__("sqlalchemy").text(
                "UPDATE users SET role='viewer' WHERE username='admin'"
            )
        )
        session.commit()
    response = client.post(
        "/settings/app-credentials",
        data={"app_id": "1", "private_key": "x", "webhook_secret": "y"},
    )
    assert response.status_code == 403
    assert client.get("/vault").status_code == 403


def test_agent_provider_saved_encrypted_and_shown(tmp_env):
    from vigilcultor.store.kv import kv_get

    client = make_client(tmp_env)
    saved = client.post(
        "/settings/agent-provider",
        data={
            "base_url": "https://api.example.com",
            "auth_token": "tok-1",
            "api_key": "",
            "model": "glm-x",
        },
    )
    assert saved.status_code == 303
    assert "settings.saved" in saved.headers["location"]
    blob = kv_get("agent:provider")
    assert isinstance(blob, str) and "tok-1" not in blob  # 密文存储

    page = client.get("/settings")
    assert page.status_code == 200
    assert "https://api.example.com" in page.text

    from vigilcultor.orchestrator.runner import load_provider

    provider = load_provider()
    assert provider["auth_token"] == "tok-1" and provider["model"] == "glm-x"

    # Provider 端点域名自动加入出口白名单（容器任务出网必需）
    from vigilcultor.netproxy.defaults import effective_allowlist

    assert "api.example.com" in effective_allowlist()
    assert "github.com" in effective_allowlist()  # 默认项不被覆盖


def test_agent_provider_requires_credential(tmp_env):
    client = make_client(tmp_env)
    bad = client.post(
        "/settings/agent-provider", data={"base_url": "https://api.example.com"}
    )
    assert bad.status_code == 303
    assert "provider_invalid" in bad.headers["location"]
