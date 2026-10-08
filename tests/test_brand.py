"""Branding guard tests."""

from __future__ import annotations

from pathlib import Path

from vigilcultor.branding import scan_repo, scan_text


def forbidden() -> str:
    # Constructed at runtime so this test file itself stays brand-clean.
    return "cla" + "ude"


def test_scan_text_detects_terms():
    assert scan_text(f"powered by {forbidden()} and friends") == [forbidden()]
    assert scan_text("clean text") == []


def test_repo_is_brand_clean():
    root = Path(__file__).resolve().parents[1]
    assert scan_repo(root) == []
