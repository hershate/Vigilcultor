"""打包完整性回归：源码直接导入的第三方库必须声明为运行时依赖。

背景：httpx 曾只在 dev 组，测试全绿但 wheel 安装后 `vigilcultor all`
直接 ModuleNotFoundError（2026-10-08 systemd 启动实装发现，v0.1.1 受影响）。
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 导入名 → 声明用的发行版名（不一致的映射在此登记）
IMPORT_TO_DIST = {
    "argon2": "argon2-cffi",
    "jwt": "pyjwt",
    "multipart": "python-multipart",
    "starlette": "fastapi",  # fastapi 紧绑定传递提供，随 fastapi 声明
}


def _third_party_imports() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "src" / "vigilcultor").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names - set(sys.stdlib_module_names) - {"vigilcultor"}


def test_direct_imports_are_declared_runtime_deps() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared: set[str] = set()
    for req in pyproject["project"]["dependencies"]:
        # 只取依赖说明符的裸名（忽略 >= 等约束与 extras）
        name = req.split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip()
        declared.add(name.lower())

    imports = _third_party_imports()
    missing = {
        IMPORT_TO_DIST.get(imp, imp) for imp in imports
    } - declared
    assert not missing, f"源码直接导入但未声明为运行时依赖：{sorted(missing)}"
