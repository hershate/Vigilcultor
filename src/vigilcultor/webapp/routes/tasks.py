"""Tasks page: queued/running work items triggered by labels."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from vigilcultor.auth.roles import Role
from vigilcultor.auth.session import require
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import Project, Task, User
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
        tasks = [
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
            }
            for task, full_name in rows
        ]
    return render(
        request,
        "tasks.html",
        {"title": "page.tasks.title", "desc": "page.tasks.desc", "tasks": tasks},
    )
