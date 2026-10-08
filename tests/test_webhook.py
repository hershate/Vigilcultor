"""Webhook endpoint tests: signature, dedup, storage."""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from vigilcultor.secrets import vault
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import AppCredentials, EventRecord

SECRET = "whsec_test_123"


def sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def prepared_client(tmp_env) -> TestClient:
    """App with admin user, unlocked vault and stored app credentials."""
    from vigilcultor.webapp.app import create_app

    client = TestClient(create_app(), follow_redirects=False)
    client.post("/setup", data={"username": "admin", "password": "s3cretpass!"})
    vault.set_master_password("master-pass-123")
    vault.unlock("master-pass-123")
    with get_session_maker()() as session:
        session.add(
            AppCredentials(
                id=1,
                app_id=123,
                private_key_enc=vault.encrypt("-----BEGIN FAKE KEY-----"),
                webhook_secret_enc=vault.encrypt(SECRET),
            )
        )
        session.commit()
    return client


def payload_issues_labeled() -> dict:
    return {
        "action": "labeled",
        "issue": {"number": 7, "id": 555, "updated_at": "2026-10-08T10:00:00Z"},
        "label": {"name": "需要维护"},
        "repository": {"full_name": "acme/alpha"},
        "sender": {"login": "alice"},
    }


def post_event(client: TestClient, payload: dict, delivery: str = "d-1", event: str = "issues"):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhook/github",
        content=body,
        headers={
            "x-github-delivery": delivery,
            "x-github-event": event,
            "x-hub-signature-256": sign(body),
            "content-type": "application/json",
        },
    )


def test_valid_event_stored(tmp_env):
    client = prepared_client(tmp_env)
    response = post_event(client, payload_issues_labeled())
    assert response.status_code == 202
    with get_session_maker()() as session:
        rows = session.scalars(select(EventRecord)).all()
    assert len(rows) == 1
    assert rows[0].event_type == "issues"
    assert rows[0].action == "labeled"
    assert rows[0].repo_full_name == "acme/alpha"
    assert rows[0].actor_login == "alice"
    assert json.loads(rows[0].payload_json)["label"]["name"] == "需要维护"


def test_duplicate_delivery_ignored(tmp_env):
    client = prepared_client(tmp_env)
    assert post_event(client, payload_issues_labeled(), delivery="d-1").status_code == 202
    assert post_event(client, payload_issues_labeled(), delivery="d-1").status_code == 200
    with get_session_maker()() as session:
        assert len(session.scalars(select(EventRecord)).all()) == 1


def test_invalid_signature_rejected(tmp_env):
    client = prepared_client(tmp_env)
    body = json.dumps(payload_issues_labeled()).encode()
    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "x-github-delivery": "d-2",
            "x-github-event": "issues",
            "x-hub-signature-256": "sha256=" + "0" * 64,
        },
    )
    assert response.status_code == 401


def test_missing_credentials_503(tmp_env):
    from vigilcultor.webapp.app import create_app

    client = TestClient(create_app(), follow_redirects=False)
    client.post("/setup", data={"username": "admin", "password": "s3cretpass!"})
    # no vault/credentials configured
    body = json.dumps(payload_issues_labeled()).encode()
    response = client.post(
        "/webhook/github",
        content=body,
        headers={"x-github-delivery": "d-3", "x-github-event": "issues"},
    )
    assert response.status_code == 503
