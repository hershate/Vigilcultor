"""Event → task dispatch: permission gate, rule match, debounce, task creation.

Debounce (three layers from the requirements):
1. cooldown window per (project, issue, label) — rule.cooldown_sec;
2. running/queued task for the same issue is ignored;
3. manual rerun is a panel action (arrives with the task views).

Polled events have no actor in the webhook sense; the dispatcher resolves the
labeling actor from the issue timeline before the permission gate, and fails
closed when the actor cannot be established.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select

from vigilcultor.gh import service
from vigilcultor.gh.permissions import actor_is_collaborator
from vigilcultor.rules.engine import match_rule
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.kv import kv_get
from vigilcultor.store.models import EventRecord, Project, Rule, Task, utcnow

logger = logging.getLogger(__name__)

RUNNING_STATES = {"queued", "running", "gating", "iterating"}
DEFAULT_BUDGET_CENTS = 200  # $2.00 per task until providers configure budgets
BUDGET_KV_KEY = "tasks:budget_default_cents"


@dataclass
class DispatchOutcome:
    task_id: int | None
    reason: str  # created|no_project|no_client|not_collaborator|no_rule|busy_issue|cooldown


def _default_check(installation_id: int) -> Callable[[str, str | None], bool]:
    client = service.installation_client_for(installation_id)
    if client is None:
        return lambda repo, actor: False
    return lambda repo, actor: actor_is_collaborator(client, repo, actor)


def _resolve_actor_from_timeline(
    installation_id: int, repo: str, issue_number: int, label: str
) -> str | None:
    """Find who applied *label* via the issue events timeline (poll compensation)."""
    client = service.installation_client_for(installation_id)
    if client is None:
        return None
    try:
        events = client.list_issue_events(repo, issue_number)
    except Exception:  # noqa: BLE001 - fail closed on API errors
        return None
    labeled = [
        row
        for row in events
        if row.get("event") == "labeled" and (row.get("label") or {}).get("name") == label
    ]
    if not labeled:
        return None
    actor = labeled[-1].get("actor") or {}
    return actor.get("login")


def dispatch_event(
    event_id: int,
    *,
    collaborator_check: Callable[[str, str | None], bool] | None = None,
) -> DispatchOutcome:
    """Process one stored event; idempotent (processed events are skipped)."""
    with get_session_maker()() as session:
        event = session.get(EventRecord, event_id)
        if event is None:
            return DispatchOutcome(None, "missing")
        if event.processed:
            return DispatchOutcome(None, "already_processed")

        outcome = DispatchOutcome(None, "unsupported")
        try:
            if event.event_type == "issues" and event.action == "labeled":
                outcome = _dispatch_labeled(session, event, collaborator_check)
        finally:
            event.processed = True
            session.commit()
    if outcome.reason == "created":
        logger.info("task #%s created from event #%s", outcome.task_id, event_id)
    else:
        logger.info("event #%s dispatched: %s", event_id, outcome.reason)
    return outcome


def _dispatch_labeled(
    session, event: EventRecord, collaborator_check  # noqa: ANN001
) -> DispatchOutcome:
    payload = json.loads(event.payload_json)
    label = (payload.get("label") or {}).get("name")
    issue_number = (payload.get("issue") or {}).get("number")
    if not label or not issue_number:
        return DispatchOutcome(None, "unsupported")

    project = session.scalar(
        select(Project).where(
            Project.full_name == event.repo_full_name, Project.state == "active"
        )
    )
    if project is None:
        return DispatchOutcome(None, "no_project")

    check = collaborator_check or _default_check(project.installation_id)
    actor = event.actor_login
    if actor is None:
        actor = _resolve_actor_from_timeline(
            project.installation_id, event.repo_full_name, issue_number, label
        )
    if not check(event.repo_full_name, actor):
        return DispatchOutcome(None, "not_collaborator")

    rules = session.scalars(select(Rule)).all()
    rule = match_rule(
        rules=rules, project_id=project.id, label=label, actor_is_collaborator=True
    )
    if rule is None:
        return DispatchOutcome(None, "no_rule")

    return _create_with_debounce(session, project, rule, event, label, issue_number)


def _create_with_debounce(session, project, rule, event, label, issue_number) -> DispatchOutcome:  # noqa: ANN001
    busy = session.scalar(
        select(Task.id).where(
            Task.project_id == project.id,
            Task.issue_number == issue_number,
            Task.state.in_(RUNNING_STATES),
        )
    )
    if busy:
        return DispatchOutcome(None, "busy_issue")

    cooldown_started = utcnow() - dt.timedelta(seconds=rule.cooldown_sec)
    recent = session.scalar(
        select(Task.id).where(
            Task.project_id == project.id,
            Task.issue_number == issue_number,
            Task.label == label,
            Task.created_at >= cooldown_started,
        )
    )
    if recent:
        return DispatchOutcome(None, "cooldown")

    budget = kv_get(BUDGET_KV_KEY) or DEFAULT_BUDGET_CENTS
    task = Task(
        project_id=project.id,
        rule_id=rule.id,
        event_id=event.id,
        issue_number=issue_number,
        label=label,
        action=rule.action,
        state="queued",
        priority=rule.priority,
        budget_usd=int(budget),
    )
    session.add(task)
    session.commit()
    return DispatchOutcome(task.id, "created")
