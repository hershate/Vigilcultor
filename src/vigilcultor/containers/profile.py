"""Environment profile detection: language, base image, setup/build/test commands.

The detector works from a file listing (plus optional file reader for deeper
hints like npm scripts), so onboarding can run it against a fresh clone.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class EnvProfile:
    language: str
    base_image: str
    setup_commands: list[str]
    build_command: str | None
    test_command: str | None
    evidence: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> EnvProfile:
        data = json.loads(raw)
        return cls(
            language=data["language"],
            base_image=data["base_image"],
            setup_commands=list(data.get("setup_commands", [])),
            build_command=data.get("build_command"),
            test_command=data.get("test_command"),
            evidence=dict(data.get("evidence", {})),
        )


# Detection priority: compiled languages first (they define the toolchain).
_DETECTORS: list[str] = ["rust", "go", "node", "python"]


def detect_profile(
    files: list[str], read_file: Callable[[str], str] | None = None
) -> EnvProfile | None:
    names = {name.rsplit("/", 1)[-1] for name in files}
    for language in _DETECTORS:
        detector = globals()[f"_detect_{language}"]
        profile = detector(names, read_file)
        if profile is not None:
            return profile
    return None


def _read_json(reader: Callable[[str], str], name: str) -> dict:
    try:
        return json.loads(reader(name))
    except Exception:  # noqa: BLE001 - unreadable hint files degrade to defaults
        return {}


def _detect_python(names: set[str], reader) -> EnvProfile | None:  # noqa: ANN001
    if "pyproject.toml" in names:
        setup = ["pip install -e ."]
        test = "pytest" if _uses_pytest(reader) else None
        return EnvProfile(
            language="python",
            base_image="docker.io/library/python:3.12-slim",
            setup_commands=setup,
            build_command=None,
            test_command=test,
            evidence={"pyproject.toml": "python project"},
        )
    if "requirements.txt" in names:
        return EnvProfile(
            language="python",
            base_image="docker.io/library/python:3.12-slim",
            setup_commands=["pip install -r requirements.txt"],
            build_command=None,
            test_command=None,
            evidence={"requirements.txt": "python requirements"},
        )
    return None


def _uses_pytest(reader) -> bool:  # noqa: ANN001
    if reader is None:
        return True  # pyproject projects overwhelmingly use pytest; safe default
    try:
        content = reader("pyproject.toml")
    except Exception:  # noqa: BLE001
        return True
    return "pytest" in content


def _detect_node(names: set[str], reader) -> EnvProfile | None:  # noqa: ANN001
    if "package.json" not in names:
        return None
    scripts = _read_json(reader, "package.json").get("scripts", {}) if reader else {}
    setup = ["npm install"]
    build = "npm run build" if scripts.get("build") else None
    test = "npm test" if scripts.get("test") else None
    return EnvProfile(
        language="node",
        base_image="docker.io/library/node:20-slim",
        setup_commands=setup,
        build_command=build,
        test_command=test,
        evidence={"package.json": "node project"},
    )


def _detect_rust(names: set[str], reader) -> EnvProfile | None:  # noqa: ANN001
    if "Cargo.toml" not in names:
        return None
    return EnvProfile(
        language="rust",
        base_image="docker.io/library/rust:1-slim",
        setup_commands=["cargo fetch"],
        build_command="cargo build --release",
        test_command="cargo test",
        evidence={"Cargo.toml": "rust project"},
    )


def _detect_go(names: set[str], reader) -> EnvProfile | None:  # noqa: ANN001
    if "go.mod" not in names:
        return None
    return EnvProfile(
        language="go",
        base_image="docker.io/library/golang:1.23-bookworm",
        setup_commands=["go mod download"],
        build_command="go build ./...",
        test_command="go test ./...",
        evidence={"go.mod": "go project"},
    )
