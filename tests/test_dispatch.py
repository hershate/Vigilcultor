"""Dispatcher tests: permission gate, debounce, poll actor resolution."""

from __future__ import annotations

import json

from sqlalchemy import select

from vigilcultor.gh.events import dumps
from vigilcultor.orchestrator.dispatch import dispatch_event
from vigilcultor.rules.engine import seed_default_rules
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import EventRecord, Installation, Project, Task

PROJECT = "acme/alpha"
LABEL = "需要维护"


class FakeInstallationClient:
    def __init__(self, permission: str = "write", timeline: list[dict] | None = None):
        self._permission = permission
        self._timeline = timeline or []

    def get_permission(self, full_name: str, username: str) -> str:
        return self._permission if username == "alice" else "read"

    def list_issue_events(self, full_name: str, issue_number: int) -> list[dict]:
        return self._timeline


def seed(tmp_env, monkeypatch, *, permission: str = "write", timeline: list[dict] | None = None):
    fake = FakeInstallationClient(permission, timeline)
    with get_session_maker()() as session:
        session.add(
            Installation(
                installation_id=11, account_login="acme", account_type="Organization"
            )
        )
        session.add(Project(full_name=PROJECT, installation_id=11))
        session.commit()
    seed_default_rules()
    from vigilcultor.gh import service

    monkeypatch.setattr(service, "installation_client_for", lambda iid: fake)
    return fake


def store_event(
    *,
    actor: str | None = "alice",
    source: str = "webhook",
    number: int = 7,
    label: str = LABEL,
) -> int:
    payload = {
        "action": "labeled",
        "issue": {"number": number, "id": 5000 + number, "updated_at": "2026-10-08T12:00:00Z"},
        "label": {"name": label},
        "repository": {"full_name": PROJECT},
        "sender": {"login": actor} if actor else {},
    }
    record = EventRecord(
        gh_delivery_id=f"d-{number}-{source}-{label}",
        source=source,
        event_type="issues",
        action="labeled",
        repo_full_name=PROJECT,
        actor_login=actor,
        dedup_key=f"k-{number}-{source}-{label}",
        payload_json=dumps(payload),
    )
    with get_session_maker()() as session:
        session.add(record)
        session.commit()
        return record.id


def tasks_count() -> int:
    with get_session_maker()() as session:
        return len(session.scalars(select(Task)).all())


def test_collaborator_creates_task(tmp_env, monkeypatch):
    seed(tmp_env, monkeypatch)
    event_id = store_event()
    outcome = dispatch_event(event_id)
    assert outcome.reason == "created"
    assert outcome.task_id is not None
    with get_session_maker()() as session:
        task = session.get(Task, outcome.task_id)
    assert task.state == "queued"
    assert task.label == LABEL
    assert task.action == "ai_task"
    assert task.budget_usd == 200  # default $2 in cents
    with get_session_maker()() as session:
        assert session.get(EventRecord, event_id).processed


def test_non_collaborator_blocked(tmp_env, monkeypatch):
    seed(tmp_env, monkeypatch)
    event_id = store_event(actor="stranger")
    outcome = dispatch_event(event_id, collaborator_check=lambda repo, actor: actor == "alice")
    assert outcome.reason == "not_collaborator"
    assert tasks_count() == 0


def test_no_active_project(tmp_env, monkeypatch):
    seed(tmp_env, monkeypatch)
    event_id = store_event()
    with get_session_maker()() as session:
        session.execute(
            select(Project).where(Project.full_name == PROJECT)
        )
        project = session.scalars(select(Project)).one()
        project.state = "paused"
        session.commit()
    # dispatch re-reads the event; project lookup happens inside
    outcome = dispatch_event(event_id)
    assert outcome.reason == "no_project"


def test_busy_issue_ignored(tmp_env, monkeypatch):
    seed(tmp_env, monkeypatch)
    first = dispatch_event(store_event())
    assert first.reason == "created"
    second = dispatch_event(store_event())  # same issue+label, fresh delivery id
    assert second.reason == "busy_issue"
    assert tasks_count() == 1


def test_cooldown_window(tmp_env, monkeypatch):
    seed(tmp_env, monkeypatch)
    first = dispatch_event(store_event(number=9))
    assert first.reason == "created"
    with get_session_maker()() as session:
        task = session.get(Task, first.task_id)
        task.state = "failed"  # not running anymore
        session.commit()
    again = dispatch_event(store_event(number=9))
    assert again.reason == "cooldown"
    assert tasks_count() == 1


def test_processed_event_idempotent(tmp_env, monkeypatch):
    seed(tmp_env, monkeypatch)
    event_id = store_event()
    assert dispatch_event(event_id).reason == "created"
    assert dispatch_event(event_id).reason == "already_processed"


def test_unmatched_label_no_rule(tmp_env, monkeypatch):
    seed(tmp_env, monkeypatch)
    event_id = store_event(label="unknown-label")
    outcome = dispatch_event(event_id, collaborator_check=lambda repo, actor: True)
    assert outcome.reason == "no_rule"


def test_poll_event_resolves_actor_from_timeline(tmp_env, monkeypatch):
    timeline = [
        {"event": "labeled", "label": {"name": LABEL}, "actor": {"login": "alice"}}
    ]
    seed(tmp_env, monkeypatch, timeline=timeline)

    event_id = store_event(actor=None, source="poll")
    outcome = dispatch_event(event_id)
    assert outcome.reason == "created"


def test_poll_event_without_actor_fails_closed(tmp_env, monkeypatch):
    seed(tmp_env, monkeypatch, timeline=[])  # timeline has no labeled entry

    event_id = store_event(actor=None, source="poll")
    outcome = dispatch_event(event_id)
    assert outcome.reason == "not_collaborator"
    assert tasks_count() == 0


def test_no_credentials_fails_closed(tmp_env, monkeypatch):
    seed(tmp_env, monkeypatch)
    from vigilcultor.gh import service

    monkeypatch.setattr(service, "installation_client_for", lambda iid: None)
    event_id = store_event()
    assert dispatch_event(event_id).reason == "not_collaborator"


def test_unsupported_event_marked_processed(tmp_env, monkeypatch):
    seed(tmp_env, monkeypatch)
    record = EventRecord(
        gh_delivery_id="d-x",
        source="webhook",
        event_type="push",
        action=None,
        repo_full_name=PROJECT,
        dedup_key="k-x",
        payload_json=json.dumps({}),
    )
    with get_session_maker()() as session:
        session.add(record)
        session.commit()
        record_id = record.id
    assert dispatch_event(record_id).reason == "unsupported"
    with get_session_maker()() as session:
        assert session.get(EventRecord, record_id).processed
