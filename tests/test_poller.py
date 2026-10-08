"""Poller tests: installation sync, snapshot seeding, label-change detection, dedup."""

from __future__ import annotations

from sqlalchemy import select

from vigilcultor.gh.poller import LAST_RUN_KEY, Poller
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.kv import kv_get
from vigilcultor.store.models import EventRecord, Installation, Project


class FakeAppClient:
    def list_installations(self):
        from vigilcultor.gh.client import InstallationInfo

        return [
            InstallationInfo(
                installation_id=11,
                account_login="acme",
                account_type="Organization",
                repository_selection="selected",
            )
        ]


class FakeInstallationClient:
    def __init__(self, issues_by_repo: dict[str, list[dict]]):
        self._issues_by_repo = issues_by_repo

    def list_issues(self, full_name: str, *, since: str | None = None):
        return self._issues_by_repo.get(full_name, [])


def seed_project(tmp_env) -> None:
    with get_session_maker()() as session:
        session.add(
            Installation(
                installation_id=11, account_login="acme", account_type="Organization"
            )
        )
        session.add(Project(full_name="acme/alpha", installation_id=11))
        session.commit()


def issue_row(number: int, labels: list[str], updated: str) -> dict:
    return {
        "number": number,
        "id": 1000 + number,
        "title": f"issue {number}",
        "updated_at": updated,
        "labels": [{"name": name} for name in labels],
    }


def test_first_run_seeds_without_events(tmp_env):
    seed_project(tmp_env)
    issues = {"acme/alpha": [issue_row(1, ["bug"], "2026-10-08T10:00:00Z")]}
    poller = Poller(FakeAppClient(), lambda iid: FakeInstallationClient(issues))

    result = poller.poll_once()

    assert result.installations_synced == 1
    assert result.events_stored == 0
    assert result.issues_scanned == 1
    with get_session_maker()() as session:
        assert len(session.scalars(select(EventRecord)).all()) == 0
        assert session.get(Installation, 11) is not None
    assert kv_get("poll:labels:1:1") == ["bug"]
    assert kv_get(LAST_RUN_KEY) is not None


def test_label_change_emits_event_once(tmp_env):
    seed_project(tmp_env)
    issues = {"acme/alpha": [issue_row(1, ["bug"], "2026-10-08T10:00:00Z")]}
    poller = Poller(FakeAppClient(), lambda iid: FakeInstallationClient(issues))
    poller.poll_once()  # seed

    issues["acme/alpha"] = [issue_row(1, ["bug", "需要维护"], "2026-10-08T11:00:00Z")]
    second = poller.poll_once()

    assert second.events_stored == 1
    with get_session_maker()() as session:
        events = session.scalars(select(EventRecord)).all()
    assert len(events) == 1
    assert events[0].source == "poll"
    assert events[0].action == "labeled"
    assert events[0].repo_full_name == "acme/alpha"
    assert events[0].actor_login is None  # polled events carry no actor

    third = poller.poll_once()  # unchanged labels → no new events (same updated_at)
    assert third.events_stored == 0


def test_removed_only_labels_do_not_emit(tmp_env):
    seed_project(tmp_env)
    issues = {"acme/alpha": [issue_row(1, ["bug", "enhancement"], "2026-10-08T10:00:00Z")]}
    poller = Poller(FakeAppClient(), lambda iid: FakeInstallationClient(issues))
    poller.poll_once()

    issues["acme/alpha"] = [issue_row(1, ["bug"], "2026-10-08T11:00:00Z")]
    assert poller.poll_once().events_stored == 0


def test_paused_project_not_polled(tmp_env):
    seed_project(tmp_env)
    with get_session_maker()() as session:
        project = session.scalars(select(Project)).one()
        project.state = "paused"
        session.commit()
    issues = {"acme/alpha": [issue_row(1, ["bug"], "2026-10-08T10:00:00Z")]}
    poller = Poller(FakeAppClient(), lambda iid: FakeInstallationClient(issues))
    result = poller.poll_once()
    assert result.projects_polled == []


def test_webhook_and_poll_dedup_share_key(tmp_env):
    """A label already recorded by webhook is not re-stored by the poller."""
    from vigilcultor.gh.events import dumps, normalize_event

    seed_project(tmp_env)
    issues = {"acme/alpha": [issue_row(1, ["bug"], "2026-10-08T10:00:00Z")]}
    poller = Poller(FakeAppClient(), lambda iid: FakeInstallationClient(issues))
    poller.poll_once()  # seed snapshot ["bug"], set last_run

    # Simulate the webhook having stored the labeled event already.
    webhook_payload = {
        "action": "labeled",
        "issue": {"number": 1, "id": 1001, "updated_at": "2026-10-08T11:00:00Z"},
        "label": {"name": "需要维护"},
        "repository": {"full_name": "acme/alpha"},
        "sender": {"login": "alice"},
    }
    normalized = normalize_event("issues", webhook_payload)
    with get_session_maker()() as session:
        session.add(
            EventRecord(
                gh_delivery_id="wh-1",
                source="webhook",
                event_type="issues",
                action="labeled",
                repo_full_name="acme/alpha",
                actor_login="alice",
                dedup_key=normalized.dedup_key,
                payload_json=dumps(webhook_payload),
            )
        )
        session.commit()

    issues["acme/alpha"] = [issue_row(1, ["bug", "需要维护"], "2026-10-08T11:00:00Z")]
    result = poller.poll_once()
    assert result.events_stored == 0  # dedup key matched the webhook record
