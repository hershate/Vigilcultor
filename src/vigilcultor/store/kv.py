"""Typed helpers for the kv settings table."""

from __future__ import annotations

import json
from typing import Any

from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import Kv


def kv_get(key: str) -> Any | None:
    with get_session_maker()() as session:
        row = session.get(Kv, key)
    return json.loads(row.value) if row else None


def kv_set(key: str, value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    with get_session_maker()() as session:
        row = session.get(Kv, key)
        if row:
            row.value = encoded
        else:
            session.add(Kv(key=key, value=encoded))
        session.commit()
