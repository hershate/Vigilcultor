"""Dashboard pages: overview plus the projects/tasks/logs/settings boards."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from vigilcultor.auth.roles import Role
from vigilcultor.auth.session import require
from vigilcultor.store.models import User
from vigilcultor.webapp.app import render

router = APIRouter()

viewer_required = require(Role.VIEWER)


def _page(request: Request, title: str, desc: str) -> HTMLResponse:
    return render(request, "page.html", {"title": title, "desc": desc})


@router.get("/")
def overview(request: Request, user: User = Depends(viewer_required)):
    return _page(request, "page.overview.title", "page.overview.desc")


@router.get("/projects")
def projects(request: Request, user: User = Depends(viewer_required)):
    return _page(request, "page.projects.title", "page.projects.desc")


@router.get("/tasks")
def tasks(request: Request, user: User = Depends(viewer_required)):
    return _page(request, "page.tasks.title", "page.tasks.desc")


@router.get("/logs")
def logs(request: Request, user: User = Depends(viewer_required)):
    return _page(request, "page.logs.title", "page.logs.desc")


@router.get("/settings")
def settings(request: Request, user: User = Depends(viewer_required)):
    return _page(request, "page.settings.title", "page.settings.desc")
