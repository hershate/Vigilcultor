"""Shared fixtures: isolated data directory per test."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VIGILCULTOR_DATA_DIR", str(tmp_path))
    from vigilcultor.store import db

    db.reset_cache()
    yield tmp_path
    db.reset_cache()
