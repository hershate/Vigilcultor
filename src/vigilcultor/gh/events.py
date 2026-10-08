"""Event normalization: raw GitHub webhook/poll payloads into a common shape."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass
class NormalizedEvent:
    event_type: str  # issues | issue_comment | pull_request | ...
    action: str | None
    repo_full_name: str | None
    actor_login: str | None
    dedup_key: str
    issue_number: int | None = None
    label_name: str | None = None
    payload: dict | None = None


def _content_key(parts: list[str | int | None]) -> str:
    raw = ":".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def normalize_event(event_type: str, payload: dict) -> NormalizedEvent:
    """Extract the common fields; unknown event types are stored as passthrough."""
    action = payload.get("action")
    repo = payload.get("repository") or {}
    repo_full_name = repo.get("full_name")
    sender = payload.get("sender") or {}
    actor_login = sender.get("login")

    issue = payload.get("issue") or {}
    issue_number = issue.get("number")
    label = payload.get("label") or {}
    label_name = label.get("name")

    if event_type == "issues":
        updated = issue.get("updated_at") or ""
        dedup = _content_key(
            ["issues", action, repo_full_name, issue_number, issue.get("id"), updated]
        )
    elif event_type == "issue_comment":
        comment = payload.get("comment") or {}
        dedup = _content_key(["issue_comment", action, repo_full_name, comment.get("id")])
    elif event_type == "pull_request":
        pull = payload.get("pull_request") or {}
        dedup = _content_key(
            ["pull_request", action, repo_full_name, pull.get("id"), pull.get("updated_at")]
        )
    else:
        dedup = _content_key([event_type, action, repo_full_name, payload.get("after") or ""])

    return NormalizedEvent(
        event_type=event_type,
        action=action,
        repo_full_name=repo_full_name,
        actor_login=actor_login,
        dedup_key=dedup,
        issue_number=issue_number,
        label_name=label_name,
        payload=payload,
    )


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
