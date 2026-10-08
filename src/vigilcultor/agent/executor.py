"""Executor discovery and pinned-version verification.

The Rust executor binary ships with the product Release and is mounted into
project containers. Version coupling is deliberate: each product version pins
exactly one verified executor version (see note/agent-framework.md).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# 钉死的执行器版本：升级必须重过验证门禁（冒烟 + 流程测试）
EXPECTED_VERSION = "0.19.35"

EXECUTOR_NAME = "rusty-code"


def _candidate_paths() -> list[Path]:
    from vigilcultor.store.kv import kv_get

    paths: list[Path] = []
    try:
        override = kv_get("agent:executor_path")
    except Exception:  # noqa: BLE001 - DB not initialised yet
        override = None
    if isinstance(override, str) and override:
        paths.append(Path(override))
    env_override = os.environ.get("VIGILCULTOR_EXECUTOR")
    if env_override:
        paths.append(Path(env_override))
    from vigilcultor.config import data_dir

    paths.append(data_dir() / "bin" / EXECUTOR_NAME)
    return paths


def find_executor() -> Path | None:
    """First existing executable candidate (kv override > env > bundled)."""
    for path in _candidate_paths():
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def executor_version(path: Path) -> str | None:
    """Parse `rusty-code --version` output ("Rusty Code vX.Y.Z …")."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, trusted path
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"v?(\d+\.\d+\.\d+)", proc.stdout)
    return match.group(1) if match else None


def verify_executor(path: Path | None = None) -> tuple[bool, str]:
    """Check presence + exact pinned version; returns (ok, reason)."""
    path = path or find_executor()
    if path is None:
        return False, "executor_not_found"
    version = executor_version(path)
    if version is None:
        return False, "version_unreadable"
    if version != EXPECTED_VERSION:
        return False, f"version_mismatch:{version}!={EXPECTED_VERSION}"
    return True, f"ok:{version}"
