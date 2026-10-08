"""GitHub client tests against a mock transport."""

from __future__ import annotations

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from vigilcultor.gh.client import CachedInstallationToken, GithubAppClient, InstallationClient

APP_ID = 123456


@pytest.fixture(scope="module")
def private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def build_client(private_key_pem, handler) -> GithubAppClient:
    return GithubAppClient(
        app_id=APP_ID,
        private_key_pem=private_key_pem,
        transport=httpx.MockTransport(handler),
        base_url="https://api.test",
    )


def public_pem(private_key_pem: str) -> str:
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def test_app_jwt_is_valid_rs256(private_key_pem):
    client = build_client(private_key_pem, lambda request: httpx.Response(200, json={}))
    token = client._app_headers()["Authorization"].removeprefix("Bearer ")
    assert pyjwt.get_unverified_header(token)["alg"] == "RS256"
    payload = pyjwt.decode(token, public_pem(private_key_pem), algorithms=["RS256"])
    assert payload["iss"] == str(APP_ID)


def test_list_installations_and_token(private_key_pem):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/app":
            return httpx.Response(200, json={"slug": "vigilcultor-test"})
        if request.url.path == "/app/installations":
            assert request.headers["authorization"].startswith("Bearer ")
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 99,
                        "account": {"login": "acme", "type": "Organization"},
                        "repository_selection": "selected",
                    }
                ],
            )
        if request.url.path == "/app/installations/99/access_tokens":
            return httpx.Response(
                201, json={"token": "ghs_test", "expires_at": "2026-01-01T00:00:00Z"}
            )
        return httpx.Response(404)

    client = build_client(private_key_pem, handler)
    assert client.get_app()["slug"] == "vigilcultor-test"
    installations = client.list_installations()
    assert installations[0].installation_id == 99
    assert installations[0].account_login == "acme"

    token, expires_at = client.installation_token(99)
    assert token == "ghs_test"
    assert expires_at.year == 2026


def test_installation_client_repos_and_issues():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/installation/repositories":
            return httpx.Response(
                200,
                json={
                    "repositories": [
                        {
                            "full_name": "acme/alpha",
                            "default_branch": "main",
                            "private": False,
                            "fork": False,
                        },
                        {
                            "full_name": "acme/beta-fork",
                            "default_branch": "master",
                            "private": True,
                            "fork": True,
                            "parent": {"full_name": "orig/beta"},
                        },
                    ]
                },
            )
        if request.url.path == "/repos/acme/alpha/issues":
            return httpx.Response(
                200,
                json=[
                    {"number": 1, "title": "bug", "labels": [{"name": "需要维护"}]},
                    {"number": 2, "pull_request": {"url": "x"}},  # PR rows are filtered out
                ],
            )
        return httpx.Response(404)

    client = InstallationClient(
        lambda: "ghs_x",
        transport=httpx.MockTransport(handler),
        base_url="https://api.test",
    )
    repos = client.list_repos()
    assert [r.full_name for r in repos] == ["acme/alpha", "acme/beta-fork"]
    assert repos[1].fork and repos[1].parent_full_name == "orig/beta"

    issues = client.list_issues("acme/alpha")
    assert len(issues) == 1 and issues[0]["number"] == 1


def test_cached_token_renews(private_key_pem):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(201, json={"token": f"ghs_{len(calls)}",
                                         "expires_at": "2999-01-01T00:00:00Z"})

    app_client = build_client(private_key_pem, handler)
    cached = CachedInstallationToken(app_client, 7)
    assert cached.get() == "ghs_1"
    assert cached.get() == "ghs_1"  # cached, no second call
    assert len(calls) == 1
