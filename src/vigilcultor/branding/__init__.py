"""Outward-facing branding guard.

The product must never surface certain upstream brand terms in public artifacts
(source, templates, docs, CI, release notes). The only exemption points are
this module itself (it defines the policy) and ``vigilcultor.agent.compat``
(M4, wire-compat literals required by the agent executor config surface).
"""

from __future__ import annotations

from pathlib import Path

FORBIDDEN_TERMS = ("claude", "anthropic")

EXEMPT_PREFIXES = (
    "src/vigilcultor/branding/",
    "src/vigilcultor/agent/compat.py",
)


def _exempt(rel: str) -> bool:
    """按模块身份豁免：源码仓（src/ 前缀）、site-packages（vigilcultor/ 前缀）、
    安装态包目录为根（无前缀）三种布局通用。"""
    if any(rel.startswith(prefix) for prefix in EXEMPT_PREFIXES):
        return True
    parts = rel.split("/")
    if "branding" in parts[:-1]:  # 策略定义目录自身
        return True
    return parts[-2:] == ["agent", "compat.py"]  # 唯一豁免模块

SCANNED_SUFFIXES = {".py", ".html", ".md", ".toml", ".yml", ".yaml", ".css", ".js", ".txt"}

SKIP_DIRS = {".git", ".venv", "node_modules", "TEMP", "dist", "__pycache__", "note"}


def scan_text(text: str) -> list[str]:
    """Return forbidden terms present in *text* (case-insensitive)."""
    lowered = text.lower()
    return [term for term in FORBIDDEN_TERMS if term in lowered]


def scan_repo(root: Path) -> list[tuple[Path, int, str]]:
    """Return ``(file, line, term)`` violations under *root*."""
    violations: list[tuple[Path, int, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        if _exempt(rel):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(lines, start=1):
            for term in scan_text(line):
                violations.append((path.relative_to(root), lineno, term))
    return violations


def check(repo_root: Path | None = None) -> int:
    """CLI entry: print violations, return non-zero when any are found."""
    if repo_root is not None:
        root = repo_root
    else:
        # 源码仓：向上找 pyproject.toml；安装态（site-packages）退到包父目录
        root = Path(__file__).resolve().parents[1]
        for candidate in root.parents:
            if (candidate / "pyproject.toml").is_file():
                root = candidate
                break
    violations = scan_repo(root)
    for rel, lineno, term in violations:
        print(f"{rel}:{lineno}: forbidden brand term '{term}'")
    if violations:
        return 1
    print("brand check: clean")
    return 0
