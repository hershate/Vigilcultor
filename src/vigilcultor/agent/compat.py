"""执行器 Provider 兼容层 —— 全库唯一允许出现兼容字面量的豁免模块。

上游执行器只读集成 CC-switch v3 库（ccswitch.rs，本机源码核实）：
- 路径：env ``CC_SWITCH_DB`` 覆盖，默认 ``~/.cc-switch/cc-switch.db``
- providers 表 18 列（DDL 序），列 1=app_type、列 3=settings_config、
  列 7=sort_index、列 12=is_current
- settings_config = JSON 字符串 ``{"env": {ANTHROPIC_BASE_URL: …,
  ANTHROPIC_AUTH_TOKEN: …, ANTHROPIC_API_KEY: …, ANTHROPIC_MODEL: …}}``
- 执行器内置读取器为手写 legacy-journal 解析（无 WAL 支持）——本模块
  生成的库必须完全落盘：默认 journal 模式、提交即关闭、退出后不得残留
  ``-wal``/``-journal`` 文件，并显式校验。

所有 ANTHROPIC_*/claude 字面量只允许出现在本文件（branding 白名单）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

# ---- 豁免字面量（仅本模块）----
EXECUTOR_APP_TYPE = "claude"
ENV_BASE_URL = "ANTHROPIC_BASE_URL"
ENV_AUTH_TOKEN = "ANTHROPIC_AUTH_TOKEN"
ENV_API_KEY = "ANTHROPIC_API_KEY"
ENV_MODEL = "ANTHROPIC_MODEL"
ENV_DB_PATH = "CC_SWITCH_DB"
ENV_CONFIG_DIR = "ANTHROPIC_CONFIG_DIR"

# providers 表 18 列（CC-switch v3 DDL 序，列序即读取器索引）
_PROVIDERS_DDL = """
CREATE TABLE providers (
    id TEXT,
    app_type TEXT,
    name TEXT,
    settings_config TEXT,
    website_url TEXT,
    category TEXT,
    created_at TEXT,
    sort_index INTEGER,
    notes TEXT,
    icon TEXT,
    icon_color TEXT,
    meta TEXT,
    is_current INTEGER,
    in_failover_queue INTEGER,
    cost_multiplier REAL,
    limit_daily_usd REAL,
    limit_monthly_usd REAL,
    provider_type TEXT
)
"""


def write_provider_db(
    path: Path,
    *,
    base_url: str,
    auth_token: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    name: str = "Vigilcultor",
) -> Path:
    """生成单 Provider 的 CC-switch 兼容库（完全落盘、0600）。

    执行器要求至少一项连接信息非空；is_current=1 保证无歧义选中。
    """
    if not base_url:
        raise ValueError("base_url required")
    if not auth_token and not api_key:
        raise ValueError("auth_token or api_key required")
    env: dict[str, str] = {ENV_BASE_URL: base_url}
    if auth_token:
        env[ENV_AUTH_TOKEN] = auth_token
    if api_key:
        env[ENV_API_KEY] = api_key
    if model:
        env[ENV_MODEL] = model

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute(_PROVIDERS_DDL)
        con.execute(
            "INSERT INTO providers (id, app_type, name, settings_config, sort_index,"
            " is_current) VALUES (?, ?, ?, ?, 0, 1)",
            ("vigilcultor", EXECUTOR_APP_TYPE, name, json.dumps({"env": env})),
        )
        con.commit()
    finally:
        con.close()

    # legacy-journal 读取器只看主文件：不得残留 WAL/回滚日志
    for suffix in ("-wal", "-journal", "-shm"):
        leftover = path.with_name(path.name + suffix)
        if leftover.exists():
            raise RuntimeError(f"provider db not fully checkpointed: {leftover}")
    os.chmod(path, 0o600)
    return path


def container_env(db_container_path: str, config_container_dir: str) -> dict[str, str]:
    """容器内执行器进程的 Provider 相关 env（库与配置目录重定位）。"""
    return {
        ENV_DB_PATH: db_container_path,
        ENV_CONFIG_DIR: config_container_dir,
    }


def connection_env(
    *,
    base_url: str,
    auth_token: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    """无头通路连接 env（源码核实 v0.19.35：``from_env_with_settings`` 直读
    env，不查 cc-switch 库；第一方基址被上游 D-3 设计性禁用，必须第三方端点）。

    凭据二选一：AUTH_TOKEN（Bearer）或 API_KEY（x-api-key）；同给时
    执行器以 API_KEY 优先。
    """
    if not base_url:
        raise ValueError("base_url required")
    if not auth_token and not api_key:
        raise ValueError("auth_token or api_key required")
    env = {ENV_BASE_URL: base_url}
    if auth_token:
        env[ENV_AUTH_TOKEN] = auth_token
    if api_key:
        env[ENV_API_KEY] = api_key
    if model:
        env[ENV_MODEL] = model
    return env
