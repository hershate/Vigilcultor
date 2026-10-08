"""Vault management page: unlock (after restart) and explicit lock."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from vigilcultor.auth.roles import Role
from vigilcultor.auth.session import require
from vigilcultor.secrets import vault
from vigilcultor.store.models import User
from vigilcultor.webapp.app import render

router = APIRouter()

admin_required = require(Role.ADMIN)


def _redirect(flash: str) -> RedirectResponse:
    return RedirectResponse(f"/settings?flash={flash}", status_code=303)


@router.get("/vault")
def vault_page(request: Request, user: User = Depends(admin_required)):
    return render(
        request,
        "vault.html",
        {
            "vault_configured": vault.is_configured(),
            "vault_unlocked": vault.is_unlocked(),
        },
    )


@router.post("/vault/unlock")
def vault_unlock(
    request: Request, user: User = Depends(admin_required), password: str = Form(...)
):
    if not vault.is_configured():
        return render(request, "vault.html", {"error": "vault.not_configured"}, status_code=400)
    if not vault.unlock(password):
        return render(request, "vault.html", {"error": "vault.wrong"}, status_code=401)
    return _redirect("vault.status.unlocked")


@router.post("/vault/lock")
def vault_lock(request: Request, user: User = Depends(admin_required)):
    vault.lock()
    return _redirect("vault.status.locked")
