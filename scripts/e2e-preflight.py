#!/usr/bin/env python3
"""E2E 就绪预检：逐项检查真实闭环（issue 打「需要维护」→ 容器执行 → 门禁 → PR）
的前置条件，未就绪项给出具体下一步。

用法：scripts/e2e-preflight.py [面板地址]
退出码：0 = 全部必需项就绪；1 = 存在缺失（信息项缺失不影响退出码）。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PANEL_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
DATA_DIR = Path(os.environ.get("VIGILCULTOR_DATA_DIR", "~/.local/share/vigilcultor")).expanduser()
DB_PATH = DATA_DIR / "vigilcultor.db"
EXECUTOR = DATA_DIR / "bin" / "rusty-code"
EXECUTOR_VERSION = "0.19.35"

RESULTS: list[str] = []


def record(name: str, ok: bool, hint: str = "", required: bool = True) -> None:
    mark = "✅" if ok else ("❌" if required else "⚠️ ")
    RESULTS.append(f"{mark} {name}" + (f"  → {hint}" if not ok else ""))


def db() -> sqlite3.Connection | None:
    if not DB_PATH.is_file():
        return None
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    need = {"users", "app_credentials", "kv", "installations", "projects", "tasks"}
    return con if need <= tables else None


def main() -> int:
    print(f"Vigilcultor E2E 预检（数据目录 {DATA_DIR}）\n")

    con = db()
    record("数据库已初始化", con is not None, "先运行 vigilcultor init-db（或 ./install.sh）")

    users = creds = provider = 0
    installs = projects = 0
    project_names: list[str] = []
    if con is not None:
        users = con.execute("SELECT count(*) FROM users").fetchone()[0]
        creds = con.execute("SELECT count(*) FROM app_credentials").fetchone()[0]
        provider = con.execute("SELECT count(*) FROM kv WHERE key='agent:provider'").fetchone()[0]
        installs = con.execute("SELECT count(*) FROM installations").fetchone()[0]
        projects = con.execute("SELECT count(*) FROM projects").fetchone()[0]
        project_names = [r[0] for r in con.execute("SELECT full_name FROM projects")]

    record(
        "向导已完成（管理员账号存在）",
        users > 0,
        f"浏览器打开 {PANEL_URL}/setup 完成首次向导（设置主密码）",
    )
    record(
        "GitHub App 凭据已配置",
        creds > 0,
        "面板「设置」页填入 App ID / 私钥 / Webhook Secret（App 需已订阅 "
        "Issues、Issue comments、Pull requests、Pull request reviews、Check suites）",
    )
    record(
        "推理 Provider 已配置",
        provider > 0,
        "面板「设置」页「Agent Provider」填入 Base URL / API Key / 模型名",
    )
    record(
        "GitHub App 已安装到仓库",
        installs > 0,
        "在 GitHub App 设置页 Install App 到测试仓库（安装后面板「项目」页同步可见）",
    )
    record(
        "已添加项目",
        projects > 0,
        "面板「项目」页添加测试仓库并执行接入（onboard）",
    )

    ver = ""
    if EXECUTOR.is_file() and os.access(EXECUTOR, os.X_OK):
        try:
            ver = subprocess.run(
                [str(EXECUTOR), "--version"], capture_output=True, text=True, timeout=15
            ).stdout
        except (subprocess.SubprocessError, OSError):
            ver = ""
    record(
        f"执行器二进制（rusty-code {EXECUTOR_VERSION}）",
        EXECUTOR_VERSION in ver,
        f"放置 {EXECUTOR}（可执行 scripts/build-executor.sh 构建，或从 Release 下载）",
    )

    podman = shutil.which("podman")
    record("Podman 可用", podman is not None, "sudo apt install podman（rootless 配置后重跑）")

    if podman and project_names:
        missing = []
        for name in project_names:
            tag = "vgc-" + "-".join(name.lower().split("/"))
            r = subprocess.run(
                [podman, "image", "exists", tag], capture_output=True, timeout=30
            )
            if r.returncode != 0:
                missing.append(tag)
        record(
            "项目容器镜像已构建",
            not missing,
            f"缺 {', '.join(missing)}：面板「项目」页重新执行接入（onboard）",
        )
    elif podman and not project_names:
        record("项目容器镜像", True, required=False)  # 无项目时不检查，随 onboard 构建

    try:
        with urllib.request.urlopen(f"{PANEL_URL}/login", timeout=5) as resp:
            up = resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        up = False
    record(
        "面板服务运行中",
        up,
        f"systemctl --user start vigilcultor（或前台 vigilcultor all），随后确认 {PANEL_URL} 可达",
    )

    record(
        "保险库已解锁（运行时）",
        True,
        "服务重启后面板会要求解锁：访问 /vault 输入主密码",
        required=False,
    )
    record(
        "Webhook 公网可达（可选）",
        True,
        "实时事件需公网 URL 反代到面板；未配置时由调度器轮询兜底（E2E 仍可闭环）",
        required=False,
    )

    missing_required = sum(1 for line in RESULTS if line.startswith("❌"))
    for line in RESULTS:
        print(line)

    print()
    if missing_required:
        print(f"结论：{missing_required} 项必需条件缺失，按上方提示补齐后重跑本脚本。")
        return 1
    print("结论：全部必需项就绪 ✅  在测试仓库 issue 打「需要维护」标签即可触发首次闭环。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
