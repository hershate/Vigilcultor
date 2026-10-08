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
        if any(rel.startswith(prefix) for prefix in EXEMPT_PREFIXES):
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
    root = repo_root or Path(__file__).resolve().parents[3]
    violations = scan_repo(root)
    for rel, lineno, term in violations:
        print(f"{rel}:{lineno}: forbidden brand term '{term}'")
    if violations:
        return 1
    print("brand check: clean")
    return 0
