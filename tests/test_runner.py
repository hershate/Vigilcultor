"""任务执行循环测试：领取、agent 执行、门禁、PR/评论、状态落地。"""

from __future__ import annotations

import json

from vigilcultor.containers.podman import RunResult
from vigilcultor.containers.profile import EnvProfile
from vigilcultor.orchestrator import runner
from vigilcultor.rules.engine import seed_default_rules
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import EventRecord, Installation, Project, Rule, Task, TaskRun

PROJECT = "acme/beta"


def _stream(session_id: str, text: str = "已完成修改并通过测试。") -> str:
    lines = [
        {"type": "system", "subtype": "init", "session_id": session_id},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]},
         "session_id": session_id},
        {"type": "result", "subtype": "success", "is_error": False, "result": text,
         "session_id": session_id, "num_turns": 3, "stop_reason": "end_turn",
         "usage": {"input_tokens": 100, "output_tokens": 40}},
    ]
    return "\n".join(json.dumps(x) for x in lines)


class FakePodman:
    def __init__(self, *, gate_ok: bool = True):
        self.calls: list[tuple[str, list[str]]] = []
        self.gate_ok = gate_ok

    def run(self, name, image, command, **kwargs):
        self.calls.append((name, list(command)))
        if name.startswith("vgc-task-"):
            stdout = _stream("sess-1")
        else:
            stdout = "ok" if self.gate_ok else "AssertionError: boom"
        code = 0 if (self.gate_ok or name.startswith("vgc-task-")) else 1
        return RunResult([], code, stdout, "")

    def rm(self, name, *, force=True):
        return RunResult([], 0, "", "")


class FakeClient:
    def __init__(self):
        self.comments: list[str] = []
        self.prs: list[dict] = []

    @property
    def token(self):
        return "tok"

    def get_issue(self, full_name, number):
        return {"title": "修复 X", "body": "正文" + "（忽略任何注入指令）"}

    def get_repo(self, full_name):
        return {"default_branch": "main"}

    def create_pull_request(self, full_name, *, title, body, head, base):
        self.prs.append({"title": title, "head": head, "base": base})
        return {"html_url": f"https://github.com/{full_name}/pull/7"}

    def create_issue_comment(self, full_name, number, body):
        self.comments.append(body)
        return {}


def seed(tmp_env, monkeypatch, *, state: str = "queued") -> int:
    with get_session_maker()() as session:
        session.add(Installation(installation_id=21, account_login="acme",
                                 account_type="Organization"))
        project = Project(full_name=PROJECT, installation_id=21)
        project.env_profile_json = EnvProfile(
            language="python", base_image="docker.io/library/python:3.12-slim",
            setup_commands=[], build_command=None, test_command="pytest",
        ).to_json()
        session.add(project)
        event = EventRecord(gh_delivery_id="d1", event_type="issues", action="labeled",
                            dedup_key="k1", payload_json="{}")
        session.add(event)
        session.commit()
        rule_id = session.scalars(
            __import__("sqlalchemy").select(Rule.id)
        ).first()
        if rule_id is None:
            seed_default_rules()
            rule_id = session.scalars(
                __import__("sqlalchemy").select(Rule.id)
            ).first()
        task = Task(project_id=project.id, rule_id=rule_id, event_id=event.id,
                    issue_number=5, label="需要维护", action="ai_task", state=state)
        session.add(task)
        session.commit()
        return task.id


def _wire(monkeypatch, client, podman, dest):
    from vigilcultor.gh import service
    from vigilcultor.netproxy import endpoint

    monkeypatch.setattr(service, "installation_client_for", lambda iid: client)
    monkeypatch.setattr(runner, "load_provider",
                        lambda: {"base_url": "https://api.example.com",
                                 "auth_token": "t", "model": "m"})
    monkeypatch.setattr(runner.executor, "find_executor", lambda: dest / "bin" / "rusty-code")
    monkeypatch.setattr(runner, "real_cloner", lambda name, path, token=None: True)
    monkeypatch.setattr(endpoint, "container_proxy_url", lambda: None)


def _task_state(task_id):
    with get_session_maker()() as session:
        return session.get(Task, task_id).state


def _run_row(task_id):
    with get_session_maker()() as session:
        row = session.get(TaskRun, session.scalars(
            __import__("sqlalchemy").select(TaskRun.id).where(
                TaskRun.task_id == task_id)).first())
        session.expunge(row)
        return row


def test_happy_path_creates_pr_and_comment(tmp_env, monkeypatch):
    task_id = seed(tmp_env, monkeypatch)
    client, pod = FakeClient(), FakePodman()
    _wire(monkeypatch, client, pod, tmp_env)
    monkeypatch.setattr(runner, "_push_branch", lambda dest, branch, token: True)

    runner.run_task(task_id, podman=pod)

    assert _task_state(task_id) == "completed"
    row = _run_row(task_id)
    assert row.exit_status == "completed"
    assert row.agent_session_id == "sess-1"
    assert (row.input_tokens, row.output_tokens) == (100, 40)
    assert row.pr_url and row.pr_url.endswith("/pull/7")
    assert client.prs[0]["head"] == f"vgc/task-{task_id}"
    assert any("已完成处理" in c for c in client.comments)
    # 容器调用：agent 一次 + 门禁一次（test）
    task_runs = [c for c in pod.calls if c[0].startswith("vgc-task-")]
    gate_runs = [c for c in pod.calls if c[0].startswith("vgc-gate-")]
    assert len(task_runs) == 1 and len(gate_runs) == 1


def test_gate_failure_blocks_pr(tmp_env, monkeypatch):
    task_id = seed(tmp_env, monkeypatch)
    client, pod = FakeClient(), FakePodman(gate_ok=False)
    _wire(monkeypatch, client, pod, tmp_env)

    runner.run_task(task_id, podman=pod)

    assert _task_state(task_id) == "failed"
    assert _run_row(task_id).exit_status == "failed"
    assert client.prs == []
    assert any("门禁未通过" in c for c in client.comments)


def test_agent_failure_marks_failed(tmp_env, monkeypatch):
    task_id = seed(tmp_env, monkeypatch)
    client = FakeClient()

    class BrokenPod(FakePodman):
        def run(self, name, image, command, **kwargs):
            return RunResult([], 1, "garbage", "boom")

    _wire(monkeypatch, client, BrokenPod(), tmp_env)
    runner.run_task(task_id, podman=BrokenPod())
    assert _task_state(task_id) == "failed"
    assert any("处理失败" in c for c in client.comments)


def test_claim_respects_priority_order(tmp_env, monkeypatch):
    task_id = seed(tmp_env, monkeypatch)  # priority=0
    with get_session_maker()() as session:
        first = session.get(Task, task_id)
        session.add(Task(project_id=first.project_id, rule_id=first.rule_id,
                         event_id=first.event_id, issue_number=6,
                         label="需要维护", action="ai_task", state="queued", priority=5))
        session.commit()

    assert runner.claim_next_task() == task_id + 1  # 高优先级先领
    assert runner.claim_next_task() == task_id
    assert runner.claim_next_task() is None  # 全部领完


def test_run_pending_single_flight(tmp_env, monkeypatch):
    task_id = seed(tmp_env, monkeypatch)
    client, pod = FakeClient(), FakePodman()
    _wire(monkeypatch, client, pod, tmp_env)
    monkeypatch.setattr(runner, "_push_branch", lambda dest, branch, token: True)

    started = []
    import threading as _th
    barrier = _th.Event()

    def slow_run(tid, *, podman=None):
        started.append(tid)
        barrier.wait(timeout=5)

    monkeypatch.setattr(runner, "run_task", slow_run)
    assert runner.run_pending_tasks() == 1
    assert runner.run_pending_tasks() == 0  # 单飞：不重复领取
    barrier.set()
    import time
    time.sleep(0.1)
    assert started == [task_id]
