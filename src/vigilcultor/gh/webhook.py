"""Webhook signature verification (X-Hub-Signature-256, HMAC-SHA256)."""

from __future__ import annotations

import hashlib
import hmac


def verify_signature(webhook_secret: str, body: bytes, signature_header: str | None) -> bool:
    """Constant-time verification of GitHub's HMAC-SHA256 webhook signature."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        webhook_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)
