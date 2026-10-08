"""Runtime configuration: all persistent state lives under the data directory."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def data_dir() -> Path:
    """Data directory (override with VIGILCULTOR_DATA_DIR for tests/dev)."""
    root = os.environ.get("VIGILCULTOR_DATA_DIR")
    path = Path(root) if root else Path.home() / ".local" / "share" / "vigilcultor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "vigilcultor.db"


def secret_key_path() -> Path:
    return data_dir() / "secret.key"


def load_or_create_secret_key() -> str:
    """Stable per-install signing key for session cookies."""
    path = secret_key_path()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    path.chmod(0o600)
    return key
