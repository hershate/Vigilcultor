"""Tasks pages: list with actions, per-task detail with runs, cost stats."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from vigilcultor.auth.roles import Role
from vigilcultor.auth.session import require
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import Project, Task, TaskRun, User
from vigilcultor.webapp.app import render

router = APIRouter()

viewer_required = require(Role.VIEWER)
admin_required = require(Role.ADMIN)

# 成本估算：每百万 token 单价（USD）；未列出的模型不计费（执行器不报成本）。
DEFAULT_PRICE_MAP = {"input": 0.0, "output": 0.0}


def _price_map() -> dict:
    from vigilcultor.store.kv import kv_get

    configured = kv_get("agent:price_map")
    return configured if isinstance(configured, dict) else DEFAULT_PRICE_MAP


def estimate_cost_usd(input_tokens: int, output_tokens: int, prices: dict) -> float:
    return round(
        input_tokens / 1_000_000 * float(prices.get("input", 0))
        + output_tokens / 1_000_000 * float(prices.get("output", 0)),
        4,
    )


def _flash(task_id: int, message: str) -> RedirectResponse:
    query = urlencode({"flash": message})
    return RedirectResponse(f"/tasks/{task_id}?{query}", status_code=303)


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
        {
            "title": "page.tasks.title",
            "desc": "page.tasks.desc",
            "tasks": tasks,
            "cost_stats": cost_stats(),
        },
    )


def cost_stats() -> list[dict]:
    """按项目/月份聚合 token 与估算成本（价格表可经 kv agent:price_map 配置）。"""
    prices = _price_map()
    with get_session_maker()() as session:
        rows = session.execute(
            select(
                Project.full_name,
                func.strftime("%Y-%m", TaskRun.started_at),
                func.sum(TaskRun.input_tokens),
                func.sum(TaskRun.output_tokens),
            )
            .join(Task, Task.project_id == Project.id)
            .join(TaskRun, TaskRun.task_id == Task.id)
            .group_by(Project.full_name, func.strftime("%Y-%m", TaskRun.started_at))
            .order_by(func.strftime("%Y-%m", TaskRun.started_at).desc())
            .limit(50)
        ).all()
    return [
        {
            "project": full_name,
            "month": month,
            "input_tokens": int(inp or 0),
            "output_tokens": int(outp or 0),
            "cost_usd": estimate_cost_usd(int(inp or 0), int(outp or 0), prices),
        }
        for full_name, month, inp, outp in rows
    ]


@router.get("/tasks/{task_id}")
def task_detail_page(
    request: Request, task_id: int, user: User = Depends(viewer_required)
):
    with get_session_maker()() as session:
        row = session.execute(
            select(Task, Project.full_name)
            .join(Project, Task.project_id == Project.id)
            .where(Task.id == task_id)
        ).first()
        if row is None:
            return render(request, "page.html", {"title": "page.tasks.title"}, status_code=404)
        task, full_name = row
        runs = session.scalars(
            select(TaskRun).where(TaskRun.task_id == task_id).order_by(TaskRun.iter_no)
        ).all()
        prices = _price_map()
        run_views = [
            {
                "iter_no": run.iter_no,
                "session_id": run.agent_session_id,
                "exit_status": run.exit_status,
                "tokens": (run.input_tokens, run.output_tokens),
                "cost_usd": estimate_cost_usd(run.input_tokens, run.output_tokens, prices),
                "pr_url": run.pr_url,
                "log_tail": run.log_tail,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
            for run in runs
        ]
        view = {
            "id": task.id,
            "project": full_name,
            "issue": task.issue_number,
            "label": task.label,
            "state": task.state,
            "priority": task.priority,
            "budget_usd": task.budget_usd / 100,
            "error": task.error,
            "preserved_branch": task.preserved_branch,
            "pr_number": task.pr_number,
            "created_at": task.created_at,
        }
    return render(
        request,
        "task_detail.html",
        {
            "title": "page.tasks.title",
            "desc": "page.tasks.desc",
            "task": view,
            "runs": run_views,
            "flash": request.query_params.get("flash"),
        },
    )


@router.post("/tasks/{task_id}/cancel")
def cancel_task_action(task_id: int, user: User = Depends(admin_required)):
    from vigilcultor.orchestrator.intervene import cancel_task

    ok = cancel_task(task_id, reason="manual")
    return _flash(task_id, "tasks.cancelled" if ok else "tasks.not_active")


@router.post("/tasks/{task_id}/rerun")
def rerun_task_action(task_id: int, user: User = Depends(admin_required)):
    from vigilcultor.orchestrator.intervene import requeue_task

    ok = requeue_task(task_id)
    return _flash(task_id, "tasks.requeued" if ok else "tasks.not_terminal")
