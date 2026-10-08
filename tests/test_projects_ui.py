"""Projects and tasks panel tests: onboarding dual entry, state control, task list."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from vigilcultor.gh.client import RepoInfo
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import Installation, Project, Task

SETUP = {
    "username": "admin",
    "password": "s3cretpass!",
    "master_password": "master-pass-123",
    "master_password2": "master-pass-123",
}

REPOS = {
    11: [
        RepoInfo(full_name="acme/alpha", default_branch="main"),
        RepoInfo(
            full_name="acme/beta-fork",
            default_branch="main",
            fork=True,
            parent_full_name="orig/beta",
        ),
    ],
    12: [RepoInfo(full_name="solo/gamma", default_branch="main")],
}


class FakeClient:
    def list_repos(self):
        return REPOS[self._installation_id]

    def __init__(self, installation_id):
        self._installation_id = installation_id


def make_client(tmp_env, monkeypatch) -> TestClient:
    from vigilcultor.gh import service
    from vigilcultor.webapp.app import create_app

    with get_session_maker()() as session:
        session.add(
            Installation(
                installation_id=11, account_login="acme", account_type="Organization"
            )
        )
        session.add(
            Installation(installation_id=12, account_login="solo", account_type="User")
        )
        session.commit()

    monkeypatch.setattr(service, "installation_client_for", lambda iid: FakeClient(iid))
    monkeypatch.setattr(
        service,
        "resolve_repo",
        lambda full_name: _resolve(full_name),
    )

    client = TestClient(create_app(), follow_redirects=False)
    client.post("/setup", data=SETUP)
    return client


def _resolve(full_name: str):
    for installation_id, repos in REPOS.items():
        for repo in repos:
            if repo.full_name == full_name:
                return installation_id, repo
    return None


def hosted() -> list[str]:
    with get_session_maker()() as session:
        return [p.full_name for p in session.scalars(select(Project)).all()]


def test_add_by_url_variants(tmp_env, monkeypatch):
    client = make_client(tmp_env, monkeypatch)
    for url in ("acme/alpha", "https://github.com/acme/beta-fork", "https://github.com/solo/gamma.git"):
        response = client.post("/projects/add", data={"url": url})
        assert response.status_code == 303
    assert sorted(hosted()) == ["acme/alpha", "acme/beta-fork", "solo/gamma"]

    # fork metadata recorded
    with get_session_maker()() as session:
        fork = session.scalar(select(Project).where(Project.full_name == "acme/beta-fork"))
    assert fork.is_fork and fork.parent_full_name == "orig/beta"


def test_add_by_url_not_found(tmp_env, monkeypatch):
    client = make_client(tmp_env, monkeypatch)
    response = client.post("/projects/add", data={"url": "nobody/missing"})
    assert "projects.not_found" in response.headers["location"]
    assert hosted() == []


def test_add_by_url_invalid(tmp_env, monkeypatch):
    client = make_client(tmp_env, monkeypatch)
    response = client.post("/projects/add", data={"url": "not a repo url"})
    assert "projects.invalid_url" in response.headers["location"]


def test_add_selected_batch(tmp_env, monkeypatch):
    client = make_client(tmp_env, monkeypatch)
    response = client.post(
        "/projects/add-selected", data={"repos": ["acme/alpha", "solo/gamma"]}
    )
    assert response.status_code == 303
    assert sorted(hosted()) == ["acme/alpha", "solo/gamma"]


def test_duplicate_add_reports_exists(tmp_env, monkeypatch):
    client = make_client(tmp_env, monkeypatch)
    client.post("/projects/add", data={"url": "acme/alpha"})
    response = client.post("/projects/add", data={"url": "acme/alpha"})
    assert "projects.exists" in response.headers["location"]
    assert len(hosted()) == 1


def test_state_transitions(tmp_env, monkeypatch):
    client = make_client(tmp_env, monkeypatch)
    client.post("/projects/add", data={"url": "acme/alpha"})
    project_id = 1
    assert client.post(f"/projects/{project_id}/state", data={"state": "paused"}).status_code == 303
    with get_session_maker()() as session:
        assert session.get(Project, project_id).state == "paused"
    archived = client.post(f"/projects/{project_id}/state", data={"state": "archived"})
    assert archived.status_code == 303
    with get_session_maker()() as session:
        assert session.get(Project, project_id).state == "archived"
    bad = client.post(f"/projects/{project_id}/state", data={"state": "hacked"})
    assert "projects.invalid_state" in bad.headers["location"]


def test_projects_page_lists_and_offers_checkboxes(tmp_env, monkeypatch):
    client = make_client(tmp_env, monkeypatch)
    client.post("/projects/add", data={"url": "acme/alpha"})
    page = client.get("/projects")
    assert page.status_code == 200
    assert "acme/alpha" in page.text
    assert "solo/gamma" in page.text  # offered as checkbox candidate
    assert "beta-fork" in page.text


def test_viewer_sees_no_management(tmp_env, monkeypatch):
    client = make_client(tmp_env, monkeypatch)
    with get_session_maker()() as session:
        session.execute(
            __import__("sqlalchemy").text("UPDATE users SET role='viewer' WHERE username='admin'")
        )
        session.commit()
    page = client.get("/projects")
    assert page.status_code == 200
    assert "/projects/add" not in page.text  # no forms for viewers
    assert client.post("/projects/add", data={"url": "acme/alpha"}).status_code == 403


def test_tasks_page_lists_rows(tmp_env, monkeypatch):
    client = make_client(tmp_env, monkeypatch)
    client.post("/projects/add", data={"url": "acme/alpha"})
    from vigilcultor.gh.events import dumps
    from vigilcultor.rules.engine import seed_default_rules
    from vigilcultor.store.models import EventRecord, Rule

    seed_default_rules()
    with get_session_maker()() as session:
        project = session.scalars(select(Project)).one()
        rule = session.scalars(select(Rule)).one()
        event = EventRecord(
            gh_delivery_id="d-1",
            source="webhook",
            event_type="issues",
            action="labeled",
            repo_full_name="acme/alpha",
            dedup_key="k-1",
            payload_json=dumps({}),
        )
        session.add(event)
        session.commit()
        session.add(
            Task(
                project_id=project.id,
                rule_id=rule.id,
                event_id=event.id,
                issue_number=7,
                label="需要维护",
                action="ai_task",
                state="queued",
                budget_usd=200,
            )
        )
        session.commit()
    page = client.get("/tasks")
    assert page.status_code == 200
    assert "acme/alpha" in page.text
    assert "#7" in page.text
    assert "$2.00" in page.text
