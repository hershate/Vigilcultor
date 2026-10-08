"""Polling compensation: catch label changes missed when webhooks are unreachable.

The poller walks active projects, lists issues updated since the last run,
compares label snapshots and synthesizes ``issues/labeled`` events (source=
"poll"). Polled events carry no actor (the REST timeline does not include who
labeled), so downstream authorization treats them conservatively.

First run only seeds snapshots (no events) to avoid a burst of stale triggers.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select

from vigilcultor.gh.client import GithubAppClient, InstallationClient
from vigilcultor.gh.events import dumps, normalize_event
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.kv import kv_get, kv_set
from vigilcultor.store.models import EventRecord, Installation, Project

logger = logging.getLogger(__name__)

LAST_RUN_KEY = "poll:last_run"
SNAPSHOT_PREFIX = "poll:labels"


@dataclass
class PollResult:
    installations_synced: int = 0
    issues_scanned: int = 0
    events_stored: int = 0
    projects_polled: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def default_installation_client_factory(app_client: GithubAppClient):
    """Real wiring: installation token cache per installation id."""

    def build(installation_id: int) -> InstallationClient:
        from vigilcultor.gh.client import CachedInstallationToken

        token = CachedInstallationToken(app_client, installation_id)
        return InstallationClient(token.get)

    return build


class Poller:
    def __init__(
        self,
        app_client: GithubAppClient,
        installation_client_factory: Callable[[int], InstallationClient] | None = None,
    ) -> None:
        self._app_client = app_client
        self._client_factory = installation_client_factory or default_installation_client_factory(
            app_client
        )

    def poll_once(self) -> PollResult:
        result = PollResult()
        result.installations_synced = self._sync_installations()

        last_run = kv_get(LAST_RUN_KEY)
        with get_session_maker()() as session:
            projects = session.scalars(
                select(Project).where(Project.state == "active")
            ).all()
            project_rows = [(p.id, p.full_name, p.installation_id) for p in projects]

        for project_id, full_name, installation_id in project_rows:
            try:
                stored, scanned = self._poll_project(
                    project_id, full_name, installation_id, last_run
                )
            except Exception:  # noqa: BLE001 - one project failing must not stop the rest
                logger.exception("poll failed for %s", full_name)
                continue
            result.events_stored += stored
            result.issues_scanned += scanned
            result.projects_polled.append(full_name)

        kv_set(LAST_RUN_KEY, _now_iso())
        return result

    def _sync_installations(self) -> int:
        installations = self._app_client.list_installations()
        with get_session_maker()() as session:
            for info in installations:
                row = session.get(Installation, info.installation_id)
                if row is None:
                    session.add(
                        Installation(
                            installation_id=info.installation_id,
                            account_login=info.account_login,
                            account_type=info.account_type,
                            repository_selection=info.repository_selection,
                        )
                    )
                else:
                    row.account_login = info.account_login
                    row.account_type = info.account_type
                    row.repository_selection = info.repository_selection
            session.commit()
        return len(installations)

    def _poll_project(
        self, project_id: int, full_name: str, installation_id: int, last_run: str | None
    ) -> tuple[int, int]:
        """Returns (events stored, issues scanned) for one project."""
        client = self._client_factory(installation_id)
        issues = client.list_issues(full_name, since=last_run)
        stored = 0
        for issue in issues:
            labels = sorted(label["name"] for label in issue.get("labels", []))
            key = f"{SNAPSHOT_PREFIX}:{project_id}:{issue['number']}"
            previous = kv_get(key)
            kv_set(key, labels)
            if previous is None or last_run is None:
                continue  # first sight or first run: seed snapshot only
            added = [name for name in labels if name not in previous]
            for name in added:
                payload = {
                    "action": "labeled",
                    "issue": {
                        "number": issue["number"],
                        "id": issue.get("id"),
                        "updated_at": issue.get("updated_at"),
                        "title": issue.get("title"),
                    },
                    "label": {"name": name},
                    "repository": {"full_name": full_name},
                    "sender": {},
                    "polled": True,
                }
                normalized = normalize_event("issues", payload)
                stored += self._store_if_new(normalized, payload)
        return stored, len(issues)

    def _store_if_new(self, normalized, payload: dict) -> int:
        with get_session_maker()() as session:
            exists = session.scalar(
                select(EventRecord.id).where(EventRecord.dedup_key == normalized.dedup_key)
            )
            if exists:
                return 0
            session.add(
                EventRecord(
                    gh_delivery_id=f"poll:{normalized.dedup_key}",
                    source="poll",
                    event_type=normalized.event_type,
                    action=normalized.action,
                    repo_full_name=normalized.repo_full_name,
                    actor_login=normalized.actor_login,
                    dedup_key=normalized.dedup_key,
                    payload_json=dumps(payload),
                )
            )
            session.commit()
            record_id = (
                session.scalar(
                    select(EventRecord.id).where(
                        EventRecord.dedup_key == normalized.dedup_key
                    )
                )
                or 0
            )
        if record_id:
            self._safe_dispatch(record_id)
        return 1

    def _safe_dispatch(self, event_id: int) -> None:
        from vigilcultor.orchestrator.dispatch import dispatch_event

        try:
            dispatch_event(event_id)
        except Exception:  # noqa: BLE001
            logger.exception("dispatch failed for polled event #%s", event_id)
