"""Project onboarding: clone, profile, image build, baseline, label alignment.

The four first-run checks from the requirements:
1. environment profile (language/build/test detection),
2. image build verification,
3. baseline test/build run,
4. label scan aligned against the rule base.

Every external dependency (cloner, podman runner, installation client) is
injectable so the flow is fully testable without network or containers.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from vigilcultor.config import data_dir
from vigilcultor.containers.imagegen import container_tag, generate_containerfile
from vigilcultor.containers.podman import Podman
from vigilcultor.containers.profile import EnvProfile, detect_profile
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import Project, Rule

LOG_TAIL_CHARS = 2000


@dataclass
class OnboardReport:
    project_id: int
    full_name: str
    cloned: bool = False
    language: str | None = None
    image_built: bool = False
    build_tail: str = ""
    baseline: dict = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    rule_labels: list[str] = field(default_factory=list)
    suggested_rules: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.image_built


def clone_dir_for(full_name: str) -> Path:
    slug = full_name.replace("/", "__")
    return data_dir() / "projects" / slug


def real_cloner(
    full_name: str, dest: Path, token_provider: Callable[[], str] | None = None
) -> bool:
    """Clone (or fast-forward) via the git CLI; token passed as an auth header."""
    dest.mkdir(parents=True, exist_ok=True)
    header = []
    if token_provider is not None:
        header = ["-c", f"http.extraHeader=Authorization: Bearer {token_provider()}"]
    if (dest / ".git").exists():
        argv = ["git", "-C", str(dest), "pull", "--ff-only"]
    else:
        argv = ["git", *header, "clone", f"https://github.com/{full_name}.git", str(dest)]
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    return completed.returncode == 0


def onboard_project(
    project_id: int,
    *,
    cloner: Callable[[str, Path], bool] | None = None,
    podman: Podman | None = None,
    labels_loader: Callable[[str], list[str]] | None = None,
) -> OnboardReport:
    from vigilcultor.gh import service

    with get_session_maker()() as session:
        project = session.get(Project, project_id)
        if project is None:
            return OnboardReport(project_id, "?", error="project_not_found")
        full_name = project.full_name
        installation_id = project.installation_id

    report = OnboardReport(project_id, full_name)
    podman = podman or Podman()

    # 1) clone
    dest = clone_dir_for(full_name)

    def _clone(name: str, path: Path) -> bool:
        client = service.installation_client_for(installation_id)
        if client is None:
            return real_cloner(name, path)
        return real_cloner(name, path, client.token)

    cloner = cloner or _clone
    try:
        report.cloned = cloner(full_name, dest)
    except Exception as exc:  # noqa: BLE001
        report.error = f"clone_failed: {exc}"
        return report
    if not report.cloned:
        report.error = "clone_failed"
        return report

    # 2) profile
    files = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()][:2000]
    profile = detect_profile(files, read_file=lambda name: (dest / name).read_text("utf-8"))
    if profile is None:
        report.error = "profile_undetected"
        return report
    report.language = profile.language

    # 3) image build verification
    containerfile = generate_containerfile(profile)
    (dest / "Containerfile").write_text(containerfile, encoding="utf-8")
    tag = container_tag(full_name)
    build = podman.build(tag, str(dest))
    report.image_built = build.ok
    report.build_tail = build.stderr[-LOG_TAIL_CHARS:] or build.stdout[-LOG_TAIL_CHARS:]
    if not build.ok:
        report.error = "image_build_failed"
        _persist_profile(project_id, profile, report)
        return report

    # 4) baseline: build & test commands inside the project image
    for kind, command in (("build", profile.build_command), ("test", profile.test_command)):
        if not command:
            report.baseline[kind] = "skipped"
            continue
        result = podman.run(
            f"vgc-onboard-{project_id}", tag, ["sh", "-c", command],
            volumes={str(dest): "/workspace"}, workdir="/workspace",
        )
        podman.rm(f"vgc-onboard-{project_id}")
        report.baseline[kind] = "ok" if result.ok else "failed"
        report.baseline[f"{kind}_tail"] = (result.stdout + result.stderr)[-LOG_TAIL_CHARS:]

    # 5) label alignment against the rule base
    loader = labels_loader or _default_labels_loader(installation_id)
    try:
        report.labels = loader(full_name)
    except Exception:  # noqa: BLE001 - label alignment is advisory, never fatal
        report.labels = []
    with get_session_maker()() as session:
        report.rule_labels = [
            rule.label
            for rule in session.scalars(select(Rule).where(Rule.project_id.is_(None))).all()
        ]
    report.suggested_rules = [
        label for label in report.labels if label not in set(report.rule_labels)
    ]

    _persist_profile(project_id, profile, report)
    return report


def _default_labels_loader(installation_id: int) -> Callable[[str], list[str]]:
    from vigilcultor.gh import service

    def load(full_name: str) -> list[str]:
        client = service.installation_client_for(installation_id)
        if client is None:
            return []
        return client.list_labels(full_name)

    return load


def _persist_profile(project_id: int, profile: EnvProfile, report: OnboardReport) -> None:
    with get_session_maker()() as session:
        project = session.get(Project, project_id)
        if project is None:
            return
        project.env_profile_json = profile.to_json()
        session.commit()
