"""Web panel flow: first-run setup, login, dashboard pages."""

from __future__ import annotations

from fastapi.testclient import TestClient

SETUP = {
    "username": "admin",
    "password": "s3cretpass!",
    "master_password": "master-pass-123",
    "master_password2": "master-pass-123",
}


def make_client() -> TestClient:
    from vigilcultor.webapp.app import create_app

    return TestClient(create_app(), follow_redirects=False)


def test_unauthenticated_redirected_to_login(tmp_env):
    client = make_client()
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_first_run_setup_creates_admin(tmp_env):
    client = make_client()
    response = client.post(
        "/setup", data=SETUP
    )
    assert response.status_code == 303
    for path in ("/", "/projects", "/tasks", "/logs", "/settings"):
        assert client.get(path).status_code == 200, path
    assert client.post("/logout").status_code == 303


def test_login_rejects_bad_credentials(tmp_env):
    client = make_client()
    client.post("/setup", data=SETUP)
    client.post("/logout")
    response = client.post("/login", data={"username": "admin", "password": "nope"})
    assert response.status_code == 401
    ok = client.post("/login", data=SETUP)
    assert ok.status_code == 303


def test_setup_closed_after_first_user(tmp_env):
    client = make_client()
    client.post("/setup", data=SETUP)
    response = client.get("/setup")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
