"""Inbound GitHub webhook endpoint: verify, normalize, store."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from vigilcultor.gh.events import dumps, normalize_event
from vigilcultor.gh.webhook import verify_signature
from vigilcultor.secrets import vault
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import AppCredentials, EventRecord

logger = logging.getLogger(__name__)

router = APIRouter()


def _webhook_secret() -> str | None:
    with get_session_maker()() as session:
        creds = session.get(AppCredentials, 1)
    if creds is None:
        return None
    try:
        return vault.decrypt(creds.webhook_secret_enc)
    except vault.VaultError:
        return None


@router.post("/webhook/github")
async def github_webhook(request: Request) -> JSONResponse:
    body = await request.body()
    delivery_id = request.headers.get("x-github-delivery", "")
    event_type = request.headers.get("x-github-event", "")
    signature = request.headers.get("x-hub-signature-256")

    secret = _webhook_secret()
    if secret is None:
        return JSONResponse(
            {"error": "app credentials not configured or vault locked"}, status_code=503
        )
    if not verify_signature(secret, body, signature):
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    if not event_type:
        return JSONResponse({"error": "missing event type"}, status_code=400)

    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    with get_session_maker()() as session:
        dup = session.scalar(
            select(EventRecord.id).where(EventRecord.gh_delivery_id == delivery_id)
        )
        if dup:
            return JSONResponse({"status": "duplicate"})

    normalized = normalize_event(event_type, payload)
    record = EventRecord(
        gh_delivery_id=delivery_id,
        source="webhook",
        event_type=normalized.event_type,
        action=normalized.action,
        repo_full_name=normalized.repo_full_name,
        actor_login=normalized.actor_login,
        dedup_key=normalized.dedup_key,
        payload_json=dumps(payload),
    )
    with get_session_maker()() as session:
        session.add(record)
        session.commit()
        record_id = record.id

    _safe_dispatch(record_id)

    logger.info(
        "webhook accepted: type=%s action=%s repo=%s",
        event_type,
        normalized.action,
        normalized.repo_full_name,
    )
    return JSONResponse({"status": "accepted"}, status_code=202)


def _safe_dispatch(event_id: int) -> None:
    """Dispatch must never break webhook acknowledgement."""
    from vigilcultor.orchestrator.dispatch import dispatch_event

    try:
        dispatch_event(event_id)
    except Exception:  # noqa: BLE001
        logger.exception("dispatch failed for event #%s", event_id)
