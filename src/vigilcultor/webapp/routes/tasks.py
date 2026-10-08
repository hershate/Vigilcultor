"""Tasks page: queued/running work items triggered by labels."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from vigilcultor.auth.roles import Role
from vigilcultor.auth.session import require
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import Project, Task, TaskRun, User
from vigilcultor.webapp.app import render

router = APIRouter()

viewer_required = require(Role.VIEWER)


@router.get("/tasks")
def tasks_page(request: Request, user: User = Depends(viewer_required)):
    with get_session_maker()() as session:
        rows = session.execute(
            select(Task, Project.full_name)
            .join(Project, Task.project_id == Project.id)
            .order_by(Task.id.desc())
            .limit(100)
        ).all()
        task_ids = [task.id for task, _ in rows]
        latest_runs: dict[int, TaskRun] = {}
        if task_ids:
            for run in session.scalars(
                select(TaskRun).where(TaskRun.task_id.in_(task_ids)).order_by(TaskRun.id)
            ):
                latest_runs[run.task_id] = run  # id 升序遍历，最后留下最新
        tasks = []
        for task, full_name in rows:
            run = latest_runs.get(task.id)
            tasks.append(
                {
                    "id": task.id,
                    "project": full_name,
                    "issue": task.issue_number,
                    "label": task.label,
                    "action": task.action,
                    "state": task.state,
                    "priority": task.priority,
                    "budget_usd": task.budget_usd / 100,
                    "created_at": task.created_at,
                    "error": task.error,
                    "run_state": run.exit_status if run else None,
                    "tokens": (run.input_tokens, run.output_tokens) if run else None,
                    "pr_url": run.pr_url if run else None,
                }
            )
    return render(
        request,
        "tasks.html",
        {"title": "page.tasks.title", "desc": "page.tasks.desc", "tasks": tasks},
    )
