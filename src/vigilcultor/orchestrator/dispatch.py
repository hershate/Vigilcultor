"""Event → task dispatch: permission gate, rule match, debounce, task creation.

Debounce (three layers from the requirements):
1. cooldown window per (project, issue, label) — rule.cooldown_sec;
2. running/queued task for the same issue is ignored;
3. manual rerun is a panel action (arrives with the task views).

Polled events have no actor in the webhook sense; the dispatcher resolves the
labeling actor from the issue timeline before the permission gate, and fails
closed when the actor cannot be established.

M5 additions: issue-close cancellation, PR-driven iteration requeue
(review/comment/CI-failure), PR close finalization, and @bot comment commands.
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
BOT_COMMANDS = ("retry", "status", "skip")


@dataclass
class DispatchOutcome:
    task_id: int | None
    reason: str


def _bot_login() -> str | None:
    """App 机器人账号名（slug[bot]），用于识别自己的评论与 @指令。"""
    return service.bot_login()


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
            elif event.event_type == "issues" and event.action == "closed":
                outcome = _handle_issue_closed(session, event)
            elif event.event_type == "pull_request" and event.action == "closed":
                outcome = _handle_pr_closed(session, event)
            elif event.event_type == "pull_request_review" and event.action == "submitted":
                outcome = _handle_pr_feedback(session, event, collaborator_check)
            elif event.event_type == "issue_comment" and event.action == "created":
                outcome = _handle_comment(session, event, collaborator_check)
            elif event.event_type in ("check_run", "check_suite") and event.action in (
                "completed",
                "rerequested",
            ):
                outcome = _handle_ci_finished(session, event)
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


def _active_project(session, event: EventRecord) -> Project | None:
    return session.scalar(
        select(Project).where(
            Project.full_name == event.repo_full_name, Project.state == "active"
        )
    )


def _task_by_pr(session, pr_number: int, project_id: int) -> Task | None:
    return session.scalar(
        select(Task).where(
            Task.project_id == project_id, Task.pr_number == pr_number
        ).order_by(Task.id.desc())
    )


def _task_by_issue(session, issue_number: int, project_id: int) -> Task | None:
    return session.scalar(
        select(Task)
        .where(Task.project_id == project_id, Task.issue_number == issue_number)
        .order_by(Task.id.desc())
    )


def _handle_issue_closed(session, event: EventRecord) -> DispatchOutcome:
    from vigilcultor.orchestrator.intervene import cancel_tasks_for_closed_issue

    payload = json.loads(event.payload_json)
    issue_number = (payload.get("issue") or {}).get("number")
    if issue_number is None or _active_project(session, event) is None:
        return DispatchOutcome(None, "no_project")
    cancelled = cancel_tasks_for_closed_issue(event.repo_full_name, issue_number)
    return DispatchOutcome(None, "issue_closed_cancelled" if cancelled else "no_task")


def _handle_pr_closed(session, event: EventRecord) -> DispatchOutcome:
    payload = json.loads(event.payload_json)
    pull = payload.get("pull_request") or {}
    pr_number = pull.get("number")
    project = _active_project(session, event)
    if pr_number is None or project is None:
        return DispatchOutcome(None, "no_project")
    task = _task_by_pr(session, pr_number, project.id)
    if task is None or task.state not in RUNNING_STATES | {"in_review"}:
        return DispatchOutcome(None, "no_task")
    if pull.get("merged"):
        task.state = "completed"
        task.error = None
        reason = "pr_merged_completed"
    else:
        from vigilcultor.orchestrator.intervene import cancel_task

        session.commit()
        cancel_task(task.id, reason="pr_closed_unmerged")
        return DispatchOutcome(task.id, "pr_closed_cancelled")
    return DispatchOutcome(task.id, reason)


def _requeue_for_iteration(session, task: Task, trigger: str) -> None:
    from vigilcultor.orchestrator.runner import iterations_exceeded

    if iterations_exceeded(task.id):
        logger.warning("task #%s hit iteration cap (%s)", task.id, trigger)
        task.state = "failed"
        task.error = "max_iterations_exceeded"
        return
    logger.info("task #%s requeued for iteration (trigger=%s)", task.id, trigger)
    task.state = "queued"
    task.error = None


def _handle_pr_feedback(session, event: EventRecord, collaborator_check) -> DispatchOutcome:
    """PR review 提交 → 续接迭代（协作者门禁；空 review 忽略）。"""
    payload = json.loads(event.payload_json)
    review = payload.get("review") or {}
    pull = payload.get("pull_request") or {}
    pr_number = pull.get("number") or review.get("pull_request_url", "").rsplit("/", 1)[-1]
    if pr_number is None:
        return DispatchOutcome(None, "unsupported")
    pr_number = int(pr_number)
    project = _active_project(session, event)
    if project is None:
        return DispatchOutcome(None, "no_project")
    task = _task_by_pr(session, pr_number, project.id)
    if task is None or task.state != "in_review":
        return DispatchOutcome(None, "no_task")

    check = collaborator_check or _default_check(project.installation_id)
    if not check(event.repo_full_name, event.actor_login):
        return DispatchOutcome(None, "not_collaborator")
    if not (review.get("body") or "").strip():
        return DispatchOutcome(None, "empty_review")
    _requeue_for_iteration(session, task, "review")
    session.commit()
    return DispatchOutcome(task.id, "iteration_requeued")


def _handle_comment(session, event: EventRecord, collaborator_check) -> DispatchOutcome:
    """issue/PR 评论：先识别 @机器人 指令（retry/status/skip），否则作为 PR 反馈续接。"""
    payload = json.loads(event.payload_json)
    comment = payload.get("comment") or {}
    body = comment.get("body") or ""
    issue = payload.get("issue") or {}
    issue_number = issue.get("number")
    project = _active_project(session, event)
    if project is None or issue_number is None:
        return DispatchOutcome(None, "no_project")

    bot = _bot_login()
    if bot and event.actor_login == bot:
        return DispatchOutcome(None, "self_comment")

    check = collaborator_check or _default_check(project.installation_id)

    lowered = body.lower()
    command = None
    if bot and f"@{bot.lower()}" in lowered:
        for candidate in BOT_COMMANDS:
            if candidate in lowered:
                command = candidate
                break
    if command:
        if not check(event.repo_full_name, event.actor_login):
            return DispatchOutcome(None, "not_collaborator")
        return _run_bot_command(session, project, issue_number, command)

    is_pr = "pull_request" in issue
    if is_pr:
        task = _task_by_pr(session, issue_number, project.id)
        if task is None or task.state != "in_review":
            return DispatchOutcome(None, "no_task")
        if not check(event.repo_full_name, event.actor_login):
            return DispatchOutcome(None, "not_collaborator")
        _requeue_for_iteration(session, task, "comment")
        session.commit()
        return DispatchOutcome(task.id, "iteration_requeued")
    return DispatchOutcome(None, "issue_comment_ignored")


def _run_bot_command(session, project, issue_number: int, command: str) -> DispatchOutcome:
    from vigilcultor.orchestrator.intervene import cancel_task, requeue_task

    task = _task_by_issue(session, issue_number, project.id)
    if task is None:
        return DispatchOutcome(None, "no_task")
    if command == "retry":
        if requeue_task(task.id):
            return DispatchOutcome(task.id, "bot_retry")
        return DispatchOutcome(None, "not_retryable")
    if command == "skip":
        if cancel_task(task.id, reason="bot_skip"):
            return DispatchOutcome(task.id, "bot_skip")
        return DispatchOutcome(None, "not_cancellable")
    if command == "status":
        _comment_status(project, task)
        return DispatchOutcome(task.id, "bot_status")
    return DispatchOutcome(None, "unsupported")


def _comment_status(project, task: Task) -> None:
    client = service.installation_client_for(project.installation_id)
    if client is None:
        return
    lines = [f"任务 #{task.id} 状态：{task.state}"]
    if task.error:
        lines.append(f"最近错误：{task.error}")
    if task.pr_number:
        lines.append(f"PR：#{task.pr_number}")
    if task.preserved_branch:
        lines.append(f"保留分支：{task.preserved_branch}")
    try:
        client.create_issue_comment(project.full_name, task.issue_number, "\n".join(lines))
    except Exception:  # noqa: BLE001 - 状态回复尽力而为
        pass


def _handle_ci_finished(session, event: EventRecord) -> DispatchOutcome:
    """CI 失败于任务分支 → 续接修复迭代（非人类事件，不走协作者门禁）。"""
    payload = json.loads(event.payload_json)
    node = payload.get(event.event_type) or {}
    suite = node.get("check_suite") or {}
    head_branch = node.get("pull_requests") and None or suite.get("head_branch")
    if not head_branch:
        head_branch = payload.get("branch") or node.get("head_branch")
    conclusion = node.get("conclusion") or suite.get("conclusion")
    if event.action == "rerequested":
        conclusion = "failure"
    if conclusion != "failure" or not (head_branch or "").startswith("vgc/task-"):
        return DispatchOutcome(None, "ignored")
    try:
        task_id = int(head_branch.rsplit("-", 1)[-1])
    except ValueError:
        return DispatchOutcome(None, "ignored")
    task = session.get(Task, task_id)
    if task is None or task.state != "in_review":
        return DispatchOutcome(None, "no_task")
    _requeue_for_iteration(session, task, "ci_failure")
    session.commit()
    return DispatchOutcome(task.id, "iteration_requeued")
