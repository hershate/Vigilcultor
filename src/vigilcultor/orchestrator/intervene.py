"""人工干预：取消（面板/issue 关闭）、保留现场分支、抢占与重跑。

需求 15 节四项干预 + 8 节失败策略：
- 面板手动取消；issue 关闭自动取消其进行中任务；
- 取消后保留废弃分支供找回（推送 vgc/preserved/<task> 并记录）；
- 高优先级标签可抢占同项目队列中的当前任务；
- 一键重跑（手动 requeue，队列头部）。
"""

from __future__ import annotations

import subprocess

from sqlalchemy import select

from vigilcultor.containers.onboard import clone_dir_for
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import Project, Task

ACTIVE_STATES = {"queued", "running", "gating", "iterating", "in_review"}

CONTAINER_NAMES = ("vgc-task-{task_id}", "vgc-gate-{task_id}")


def _preserve_branch(task_id: int, full_name: str, token: str | None) -> str | None:
    """取消/抢占时把工作区当前状态提交到保留分支并推送（找不到则跳过）。"""
    dest = clone_dir_for(full_name)
    if not (dest / ".git").exists():
        return None
    branch = f"vgc/preserved/task-{task_id}"
    steps = [
        ["git", "-C", str(dest), "add", "-A"],
        ["git", "-C", str(dest), "commit", "-m", f"Vigilcultor 保留现场（任务 {task_id} 取消）"],
        ["git", "-C", str(dest), "branch", "-f", branch],
    ]
    push = ["git", "push", "origin", f"{branch}:{branch}"]
    if token:
        push = [
            "git",
            "-c",
            f"http.extraHeader=Authorization: Bearer {token}",
            "push",
            "origin",
            f"{branch}:{branch}",
        ]
    for argv in steps + [push]:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=300, cwd=dest)
        if proc.returncode != 0 and argv[1:2] != ["commit"]:
            return None  # commit 允许失败（无变更即无需保留）
    return branch


def _stop_containers(task_id: int, podman=None) -> None:
    if podman is None:
        from vigilcultor.containers.podman import Podman

        podman = Podman()
    for pattern in CONTAINER_NAMES:
        name = pattern.format(task_id=task_id)
        if podman.exists(name):
            podman.stop(name)
            podman.rm(name)


def cancel_task(task_id: int, *, reason: str = "manual", podman=None) -> bool:
    """取消任务：停容器、保现场分支、终态 cancelled；非进行中任务忽略。"""
    with get_session_maker()() as session:
        task = session.get(Task, task_id)
        if task is None or task.state not in ACTIVE_STATES:
            return False
        project = session.get(Project, task.project_id)
        full_name = project.full_name
        installation_id = project.installation_id
        task.state = "cancelled"
        task.error = f"cancelled:{reason}"
        session.commit()
    _stop_containers(task_id, podman)
    branch = None
    try:
        from vigilcultor.gh import service

        client = service.installation_client_for(installation_id)
        branch = _preserve_branch(task_id, full_name, client.token if client else None)
    except Exception:  # noqa: BLE001 - 现场保留尽力而为，不影响取消
        branch = None
    if branch:
        with get_session_maker()() as session:
            task = session.get(Task, task_id)
            if task is not None:
                task.preserved_branch = branch
                session.commit()
    return True


def cancel_tasks_for_closed_issue(full_name: str, issue_number: int) -> list[int]:
    """issue 关闭 → 自动取消其进行中任务（需求 15 节）。"""
    with get_session_maker()() as session:
        project = session.scalar(
            select(Project).where(Project.full_name == full_name)
        )
        if project is None:
            return []
        task_ids = session.scalars(
            select(Task.id).where(
                Task.project_id == project.id,
                Task.issue_number == issue_number,
                Task.state.in_(ACTIVE_STATES),
            )
        ).all()
    return [tid for tid in task_ids if cancel_task(tid, reason="issue_closed")]


def requeue_task(task_id: int) -> bool:
    """一键重跑：终态任务重新入队（沿用预算，迭代号继续递增）。"""
    with get_session_maker()() as session:
        task = session.get(Task, task_id)
        if task is None or task.state in ACTIVE_STATES:
            return False
        task.state = "queued"
        task.error = None
        session.commit()
    return True


def preempt_if_needed() -> int | None:
    """同项目内存在比进行中任务更高优先级的排队任务 → 抢占当前（保现场）。

    返回被抢占的任务 id；无抢占返回 None。
    """
    with get_session_maker()() as session:
        row = session.execute(
            select(Task.id, Task.project_id, Task.priority)
            .where(Task.state == "running")
            .order_by(Task.id.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        running_id, project_id, running_priority = row
        higher = session.scalar(
            select(Task.id)
            .where(
                Task.project_id == project_id,
                Task.state == "queued",
                Task.priority > running_priority,
            )
            .order_by(Task.priority.desc(), Task.created_at.asc())
            .limit(1)
        )
    if higher is None:
        return None
    cancel_task(running_id, reason="preempted")
    return running_id