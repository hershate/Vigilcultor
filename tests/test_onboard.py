"""Onboarding flow tests with injected cloner/podman/labels."""

from __future__ import annotations

import json

from vigilcultor.containers.onboard import onboard_project
from vigilcultor.containers.podman import RunResult
from vigilcultor.rules.engine import seed_default_rules
from vigilcultor.store.db import get_session_maker
from vigilcultor.store.models import Installation, Project

PROJECT = "acme/alpha"


class FakePodman:
    def __init__(self):
        self.calls: list[list[str]] = []

    def build(self, tag, context_dir, *, containerfile="Containerfile"):
        self.calls.append(["build", tag])
        return RunResult(["build"], 0, "", "step1 step2 ok")

    def run(self, name, image, command, **kwargs):
        self.calls.append(["run", name, *command])
        return RunResult(["run"], 0, "all tests passed", "")

    def rm(self, name, *, force=True):
        self.calls.append(["rm", name])
        return RunResult(["rm"], 0, "", "")


def fake_cloner_factory(tmp_path):
    def cloner(full_name: str, dest) -> bool:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
        (dest / "main.py").write_text("print('hi')\n", encoding="utf-8")
        (dest / "test_main.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        return True

    return cloner


def seed(tmp_env) -> int:
    with get_session_maker()() as session:
        session.add(
            Installation(installation_id=11, account_login="acme", account_type="Organization")
        )
        session.add(Project(full_name=PROJECT, installation_id=11))
        session.commit()
        return session.scalar(
            __import__("sqlalchemy").select(Project.id).where(Project.full_name == PROJECT)
        )


def test_onboard_full_pass(tmp_env):
    project_id = seed(tmp_env)
    seed_default_rules()
    pod = FakePodman()
    report = onboard_project(
        project_id,
        cloner=fake_cloner_factory(tmp_env),
        podman=pod,
        labels_loader=lambda full_name: ["需要维护", "bug", "enhancement"],
    )
    assert report.ok
    assert report.cloned
    assert report.language == "python"
    assert report.image_built
    assert report.baseline["test"] == "ok"
    assert report.baseline["build"] == "skipped"
    assert report.labels == ["需要维护", "bug", "enhancement"]
    assert "需要维护" in report.rule_labels
    assert set(report.suggested_rules) == {"bug", "enhancement"}

    # profile persisted on the project row
    with get_session_maker()() as session:
        project = session.get(Project, project_id)
    stored = json.loads(project.env_profile_json)
    assert stored["language"] == "python"

    # container invocations: build + run(test) + rm
    kinds = [call[0] for call in pod.calls]
    assert kinds == ["build", "run", "rm"]


def test_onboard_clone_failure(tmp_env):
    project_id = seed(tmp_env)
    report = onboard_project(
        project_id,
        cloner=lambda name, dest: False,
        podman=FakePodman(),
        labels_loader=lambda name: [],
    )
    assert not report.ok
    assert report.error == "clone_failed"


def test_onboard_undetected_profile(tmp_env):
    project_id = seed(tmp_env)

    def cloner(name, dest) -> bool:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "README.md").write_text("plain docs\n", encoding="utf-8")
        return True

    report = onboard_project(
        project_id, cloner=cloner, podman=FakePodman(), labels_loader=lambda name: []
    )
    assert report.error == "profile_undetected"


def test_onboard_build_failure_persists_profile(tmp_env):
    project_id = seed(tmp_env)

    class BrokenPodman(FakePodman):
        def build(self, tag, context_dir, *, containerfile="Containerfile"):
            self.calls.append(["build", tag])
            return RunResult(["build"], 1, "", "error: nope")

    report = onboard_project(
        project_id,
        cloner=fake_cloner_factory(tmp_env),
        podman=BrokenPodman(),
        labels_loader=lambda name: [],
    )
    assert not report.ok
    assert report.error == "image_build_failed"
    with get_session_maker()() as session:
        project = session.get(Project, project_id)
    assert "python" in project.env_profile_json
