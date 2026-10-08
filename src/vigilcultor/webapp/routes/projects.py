"""Projects page: hosted repositories, onboarding (checkbox + URL dual entry), state control."""

from __future__ import annotations

import re
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from vigilcultor.auth.roles import Role
from vigilcultor.auth.session import require
from vigilcultor.gh import service
from vigilcultor.gh.client import RepoInfo
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import Installation, Project, User
from vigilcultor.webapp.app import render

router = APIRouter()

viewer_required = require(Role.VIEWER)
operator_required = require(Role.OPERATOR)

_URL_PATTERN = re.compile(
    r"^(?:https?://github\.com/)?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def _flash(message: str, **params: str) -> RedirectResponse:
    return RedirectResponse(f"/projects?{urlencode({'flash': message, **params})}", status_code=303)


def _installations_with_repos() -> list[tuple[Installation, list[RepoInfo]]]:
    """Installations plus their repositories; failures degrade to empty lists.

    Instances stay usable after the session closes (expire_on_commit=False).
    """
    with get_session_maker()() as session:
        installations = session.scalars(
            select(Installation).order_by(Installation.account_login)
        ).all()

    result: list[tuple[Installation, list[RepoInfo]]] = []
    for inst in installations:
        repos: list[RepoInfo] = []
        client = service.installation_client_for(inst.installation_id)
        if client is not None:
            try:
                repos = client.list_repos()
            except Exception:  # noqa: BLE001
                repos = []
        result.append((inst, repos))
    return result


@router.get("/projects")
def projects_page(request: Request, user: User = Depends(viewer_required)):
    with get_session_maker()() as session:
        hosted = session.execute(
            select(Project, Installation.account_login)
            .join(Installation, Project.installation_id == Installation.installation_id)
            .order_by(Project.full_name)
        ).all()
        hosted_rows = [
            {"id": p.id, "full_name": p.full_name, "state": p.state,
             "is_fork": p.is_fork, "parent": p.parent_full_name, "account": account}
            for p, account in hosted
        ]
        hosted_names = {row["full_name"] for row in hosted_rows}

    install_repos: list[tuple[Installation, list[RepoInfo]]] = []
    if user.role != "viewer":
        install_repos = _installations_with_repos()

    return render(
        request,
        "projects.html",
        {
            "title": "page.projects.title",
            "desc": "page.projects.desc",
            "flash": request.query_params.get("flash"),
            "flash_name": request.query_params.get("name", ""),
            "projects": hosted_rows,
            "install_repos": install_repos,
            "hosted_names": hosted_names,
            "can_manage": user.role != "viewer",
        },
    )


def _create_project(full_name: str) -> str:
    """Returns a flash key: projects.added / projects.exists / projects.not_found."""
    resolved = service.resolve_repo(full_name)
    if resolved is None:
        return "projects.not_found"
    installation_id, repo = resolved
    with get_session_maker()() as session:
        exists = session.scalar(select(Project.id).where(Project.full_name == full_name))
        if exists:
            return "projects.exists"
        session.add(
            Project(
                full_name=full_name,
                installation_id=installation_id,
                is_fork=repo.fork,
                parent_full_name=repo.parent_full_name,
            )
        )
        session.commit()
    return "projects.added"


@router.post("/projects/add")
def add_by_url(
    request: Request,
    user: User = Depends(operator_required),
    url: str = Form(...),
):
    match = _URL_PATTERN.match(url.strip())
    if not match:
        return _flash("projects.invalid_url")
    full_name = match.group(1)
    return _flash(_create_project(full_name), name=full_name)


@router.post("/projects/add-selected")
def add_selected(
    request: Request,
    user: User = Depends(operator_required),
    repos: list[str] = Form(default=[]),
):
    for full_name in repos:
        _create_project(full_name)
    return _flash("projects.added_batch", name=str(len(repos)))


@router.post("/projects/{project_id}/state")
def set_state(
    request: Request,
    project_id: int,
    user: User = Depends(operator_required),
    state: str = Form(...),
):
    if state not in {"active", "paused", "archived"}:
        return _flash("projects.invalid_state")
    with get_session_maker()() as session:
        project = session.get(Project, project_id)
        if project is None:
            return _flash("projects.not_found")
        project.state = state
        session.commit()
    return _flash("projects.state_changed", name=state)
