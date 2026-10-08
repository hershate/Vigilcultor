"""First-run setup, sign-in and sign-out."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from vigilcultor.auth.password import hash_password, verify_password
from vigilcultor.auth.roles import Role
from vigilcultor.auth.session import COOKIE, issue_token
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import User
from vigilcultor.webapp.app import render

router = APIRouter()

MIN_USERNAME = 2
MIN_PASSWORD = 8


def _user_count() -> int:
    with get_session_maker()() as session:
        return session.scalar(select(func.count(User.id))) or 0


def _login_redirect(user_id: int) -> RedirectResponse:
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE, issue_token(user_id), httponly=True, samesite="lax")
    return response


@router.get("/setup")
def setup_form(request: Request):
    if _user_count() > 0:
        return RedirectResponse("/login", status_code=303)
    return render(request, "setup.html")


@router.post("/setup")
def setup_create(request: Request, username: str = Form(...), password: str = Form(...)):
    if _user_count() > 0:
        return render(request, "login.html", {"notice": "setup.exists"}, status_code=400)
    if len(username.strip()) < MIN_USERNAME or len(password) < MIN_PASSWORD:
        return render(request, "setup.html", {"error": "setup.invalid"}, status_code=422)

    role = Role.ADMIN.value  # first registered user becomes the administrator
    with get_session_maker()() as session:
        exists = session.scalar(select(User.id).where(User.username == username.strip()))
        if exists:
            return render(request, "setup.html", {"error": "setup.taken"}, status_code=422)
        user = User(username=username.strip(), password_hash=hash_password(password), role=role)
        session.add(user)
        session.commit()
        user_id = user.id
    return _login_redirect(user_id)


@router.get("/login")
def login_form(request: Request):
    if _user_count() == 0:
        return RedirectResponse("/setup", status_code=303)
    error = "login.failed" if "error" in request.query_params else None
    return render(request, "login.html", {"error": error})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    with get_session_maker()() as session:
        user = session.scalar(select(User).where(User.username == username.strip()))
    if user is None or user.disabled or not verify_password(password, user.password_hash):
        return render(request, "login.html", {"error": "login.failed"}, status_code=401)
    return _login_redirect(user.id)


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response
