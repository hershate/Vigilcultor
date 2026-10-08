"""FastAPI application factory and shared rendering helpers."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from vigilcultor import __version__
from vigilcultor.auth.session import current_user
from vigilcultor.i18n import lang_from, t
from vigilcultor.store.db import init_db

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def render(
    request: Request, name: str, context: dict | None = None, status_code: int = 200
) -> HTMLResponse:
    ctx = {
        "request": request,
        "t": t,
        "lang": lang_from(request),
        "version": __version__,
        "user": current_user(request),
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def create_app() -> FastAPI:
    init_db()
    app = FastAPI(
        title="Vigilcultor",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(401)
    async def _redirect_to_login(request: Request, exc: StarletteHTTPException):
        return RedirectResponse("/login", status_code=303)

    from vigilcultor.webapp.routes import auth, pages, webhook

    app.include_router(auth.router)
    app.include_router(webhook.router)
    app.include_router(pages.router)
    return app
