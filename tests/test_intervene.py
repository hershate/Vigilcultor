"""人工干预测试：取消/保留现场/issue 关闭联动/抢占/重跑。"""

from __future__ import annotations

from sqlalchemy import select

from vigilcultor.orchestrator import intervene
from vigilcultor.rules.engine import seed_default_rules
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import EventRecord, Installation, Project, Rule, Task

PROJECT = "acme/gamma"


def seed(tmp_env, *, state: str = "running", priority: int = 0) -> int:
    with get_session_maker()() as session:
        session.add(Installation(installation_id=31, account_login="acme",
                                 account_type="Organization"))
        project = Project(full_name=PROJECT, installation_id=31)
        session.add(project)
        event = EventRecord(gh_delivery_id="d9", event_type="issues", action="labeled",
                            dedup_key="k9", payload_json="{}")
        session.add(event)
        session.commit()
        seed_default_rules()
        rule_id = session.scalars(select(Rule.id)).first()
        task = Task(project_id=project.id, rule_id=rule_id, event_id=event.id,
                    issue_number=8, label="需要维护", action="ai_task",
                    state=state, priority=priority)
        session.add(task)
        session.commit()
        return task.id


def _state(task_id: int) -> str:
    with get_session_maker()() as session:
        return session.get(Task, task_id).state


class FakePodman:
    def __init__(self):
        self.stopped: list[str] = []

    def exists(self, name):
        return name == f"vgc-task-{self.task_id}" if hasattr(self, "task_id") else False

    def stop(self, name, *, timeout=20):
        self.stopped.append(name)
        return None

    def rm(self, name, *, force=True):
        return None


def _wire(monkeypatch, podman=None, preserve="vgc/preserved/task-1"):
    from vigilcultor.gh import service

    class FakeClient:
        token = "tok"

    monkeypatch.setattr(service, "installation_client_for", lambda iid: FakeClient())
    monkeypatch.setattr(intervene, "_stop_containers",
                        lambda tid, pm=None: None if podman is None else None)
    monkeypatch.setattr(intervene, "_preserve_branch",
                        lambda tid, full_name, token: preserve)


def test_cancel_running_task_preserves_branch(tmp_env, monkeypatch):
    task_id = seed(tmp_env)
    _wire(monkeypatch, preserve="vgc/preserved/task-9")
    assert intervene.cancel_task(task_id, reason="manual") is True
    assert _state(task_id) == "cancelled"
    with get_session_maker()() as session:
        task = session.get(Task, task_id)
    assert task.error == "cancelled:manual"
    assert task.preserved_branch == "vgc/preserved/task-9"


def test_cancel_ignores_terminal_task(tmp_env, monkeypatch):
    task_id = seed(tmp_env, state="completed")
    _wire(monkeypatch)
    assert intervene.cancel_task(task_id) is False
    assert _state(task_id) == "completed"


def test_issue_closed_cancels_active_tasks(tmp_env, monkeypatch):
    task_id = seed(tmp_env)
    _wire(monkeypatch, preserve=None)
    cancelled = intervene.cancel_tasks_for_closed_issue(PROJECT, 8)
    assert cancelled == [task_id]
    assert _state(task_id) == "cancelled"
    assert intervene.cancel_tasks_for_closed_issue("other/repo", 8) == []


def test_requeue_only_terminal(tmp_env):
    task_id = seed(tmp_env, state="failed")
    assert intervene.requeue_task(task_id) is True
    assert _state(task_id) == "queued"
    assert intervene.requeue_task(task_id) is False  # queued 非终态


def test_preempt_lower_priority_running(tmp_env, monkeypatch):
    low_id = seed(tmp_env, state="running", priority=0)
    with get_session_maker()() as session:
        project_id = session.get(Task, low_id).project_id
        rule_id = session.get(Task, low_id).rule_id
        event_id = session.get(Task, low_id).event_id
        session.add(Task(project_id=project_id, rule_id=rule_id, event_id=event_id,
                         issue_number=9, label="需要维护", action="ai_task",
                         state="queued", priority=5))
        session.commit()
    _wire(monkeypatch, preserve=None)
    preempted = intervene.preempt_if_needed()
    assert preempted == low_id
    assert _state(low_id) == "cancelled"
    with get_session_maker()() as session:
        task = session.get(Task, low_id)
    assert task.error == "cancelled:preempted"


def test_no_preempt_without_higher_priority(tmp_env, monkeypatch):
    seed(tmp_env, state="running", priority=5)
    _wire(monkeypatch)
    assert intervene.preempt_if_needed() is None
