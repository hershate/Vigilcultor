"""Branding guard tests."""

from __future__ import annotations

from pathlib import Path

import pytest

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


@pytest.mark.parametrize("layout", ["site-packages", "package-root"])
def test_installed_layout_exemptions(tmp_path, layout):
    """豁免按模块身份匹配：site-packages 与包目录为根两种安装态布局通用。"""
    if layout == "site-packages":
        pkg = tmp_path / "lib" / "site-packages" / "vigilcultor"
    else:
        pkg = tmp_path  # 安装态扫描根即包目录
    (pkg / "agent").mkdir(parents=True)
    (pkg / "agent" / "compat.py").write_text(
        f'ENV = "{forbidden().upper()}_BASE_URL"  # 豁免模块\n', encoding="utf-8"
    )
    (pkg / "branding").mkdir()
    (pkg / "branding" / "__init__.py").write_text(
        f'FORBIDDEN = ("{forbidden()}",)  # 策略定义自身豁免\n', encoding="utf-8"
    )
    (pkg / "webapp").mkdir()
    (pkg / "webapp" / "oops.py").write_text(
        f'x = "{forbidden()}"\n', encoding="utf-8"  # 应命中
    )
    violations = scan_repo(tmp_path if layout == "package-root" else pkg.parent)
    assert len(violations) == 1
    assert violations[0][0].as_posix().endswith("webapp/oops.py")
