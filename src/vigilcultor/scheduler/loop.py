"""Scheduler tick and loop: run the poller on an interval, fail-soft per tick."""

from __future__ import annotations

import logging
import time

from vigilcultor.gh import service
from vigilcultor.gh.poller import Poller
from vigilcultor.store.kv import kv_get

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SEC = 300
INTERVAL_KV_KEY = "poll:interval_sec"


def tick(poller: Poller | None = None) -> dict:
    """One scheduler pass; never raises (a broken tick must not kill the loop)."""
    summary: dict = {}
    if poller is None:
        app_client = service.build_app_client()
        if app_client is None:
            summary["skipped"] = "no_credentials"
            poller = None
        else:
            poller = Poller(app_client)
    if poller is not None:
        try:
            result = poller.poll_once()
        except Exception as exc:  # noqa: BLE001
            logger.exception("scheduler tick failed")
            summary["poll_error"] = str(exc)
        else:
            summary.update(
                {
                    "installations": result.installations_synced,
                    "projects": len(result.projects_polled),
                    "issues": result.issues_scanned,
                    "events": result.events_stored,
                }
            )
    try:
        from vigilcultor.orchestrator.runner import (
            poll_in_review_tasks,
            run_pending_tasks,
        )

        summary["tasks_review_polled"] = poll_in_review_tasks()
        summary["tasks_started"] = run_pending_tasks()
    except Exception as exc:  # noqa: BLE001 - 领取失败不影响轮询结果
        logger.exception("task claim failed")
        summary["tasks_error"] = str(exc)
    return summary


def interval() -> int:
    configured = kv_get(INTERVAL_KV_KEY)
    return int(configured) if configured else DEFAULT_INTERVAL_SEC


def run_loop(poller_factory=None, *, override_interval: int | None = None) -> None:  # noqa: ANN001
    """Block forever, ticking at the configured interval."""
    wait = override_interval or interval()
    logger.info("scheduler loop starting (interval=%ss)", wait)
    while True:
        poller = poller_factory() if poller_factory else None
        summary = tick(poller)
        logger.info("scheduler tick: %s", summary)
        time.sleep(wait)
