"""任务面板交互测试：详情页、取消、重跑、成本统计渲染。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from vigilcultor.rules.engine import seed_default_rules
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import EventRecord, Installation, Project, Rule, Task, TaskRun

PROJECT = "acme/delta"
SETUP = {
    "username": "admin",
    "password": "pass-123456",
    "password2": "pass-123456",
    "master_password": "master-pass-123",
    "master_password2": "master-pass-123",
}


def _client(tmp_env) -> TestClient:
    from vigilcultor.webapp.app import create_app

    client = TestClient(create_app(), follow_redirects=False)
    client.post("/setup", data=SETUP)
    return client


def _seed_task(state: str = "running") -> int:
    with get_session_maker()() as session:
        session.add(Installation(installation_id=41, account_login="acme",
                                 account_type="Organization"))
        project = Project(full_name=PROJECT, installation_id=41)
        session.add(project)
        event = EventRecord(gh_delivery_id="d-ui", event_type="issues",
                            action="labeled", dedup_key="k-ui", payload_json="{}")
        session.add(event)
        session.commit()
        seed_default_rules()
        rule_id = session.scalars(select(Rule.id)).first()
        task = Task(project_id=project.id, rule_id=rule_id, event_id=event.id,
                    issue_number=3, label="需要维护", action="ai_task", state=state)
        session.add(task)
        session.commit()
        session.add(TaskRun(task_id=task.id, iter_no=1, exit_status="in_review",
                            input_tokens=1200, output_tokens=300, pr_url="u"))
        session.commit()
        return task.id


def _patch_cancel(monkeypatch):
    from vigilcultor.orchestrator import intervene
    from vigilcultor.webapp.routes import tasks as tasks_routes

    monkeypatch.setattr(intervene, "_stop_containers", lambda tid, pm=None: None)
    monkeypatch.setattr(intervene, "_preserve_branch", lambda *a: None)
    monkeypatch.setattr(tasks_routes.intervene if hasattr(tasks_routes, "intervene")
                        else intervene, "cancel_task",
                        lambda tid, reason="manual": reason == "manual")


def test_task_detail_renders_runs_and_cost(tmp_env):
    client = _client(tmp_env)
    task_id = _seed_task()
    page = client.get(f"/tasks/{task_id}")
    assert page.status_code == 200
    assert "1200/300 tok" in page.text  # token 用量
    assert "执行记录" in page.text or "Runs" in page.text


def test_cancel_action_redirects(tmp_env, monkeypatch):
    client = _client(tmp_env)
    task_id = _seed_task()
    _patch_cancel(monkeypatch)
    response = client.post(f"/tasks/{task_id}/cancel")
    assert response.status_code == 303
    assert "tasks.cancelled" in response.headers["location"]


def test_rerun_action_requeues(tmp_env):
    client = _client(tmp_env)
    task_id = _seed_task(state="failed")
    response = client.post(f"/tasks/{task_id}/rerun")
    assert response.status_code == 303
    with get_session_maker()() as session:
        assert session.get(Task, task_id).state == "queued"


def test_tasks_list_shows_cost_table(tmp_env):
    client = _client(tmp_env)
    _seed_task()
    page = client.get("/tasks")
    assert page.status_code == 200
    assert "1200" in page.text  # 聚合 token 出现
