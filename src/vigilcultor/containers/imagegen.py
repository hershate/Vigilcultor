"""Containerfile generation from an environment profile.

Two-phase design:
- build time: base image plus only the dependency manifests that can be
  satisfied without sources (requirements.txt, package.json, ...);
- run time: source tree is volume-mounted at /workspace and editable
  installs / test tooling happen per run (see runtime_setup).

Base images are always fully qualified (docker.io/...) so user-level registry
mirror configuration applies uniformly.
"""

from __future__ import annotations

from vigilcultor.containers.profile import EnvProfile

_WORKDIR = "/workspace"

_MANIFESTS = {
    "python": ["requirements.txt"],
    "node": ["package.json", "package-lock.json"],
    "rust": [],  # cargo fetch needs sources; deferred to run time
    "go": [],
}


def generate_containerfile(profile: EnvProfile, files: list[str] | None = None) -> str:
    """Render a Containerfile; *files* (relative paths) gate manifest copies."""
    present = set(files or [])
    lines = [f"FROM {profile.base_image}", f"WORKDIR {_WORKDIR}"]

    manifests = [name for name in _MANIFESTS.get(profile.language, []) if name in present]
    if profile.language == "python" and "requirements.txt" in manifests:
        lines.append("COPY requirements.txt .")
        lines.append("RUN pip install --no-cache-dir -r requirements.txt")
    elif profile.language == "node" and "package.json" in manifests:
        lines.append("COPY package.json package-lock.json* ./")
        lines.append("RUN npm install --ignore-scripts --no-audit --no-fund")
    return "\n".join(lines) + "\n"


def runtime_setup(profile: EnvProfile, files: list[str] | None = None) -> str | None:
    """Run-time dependency step executed before build/test commands."""
    present = set(files or [])
    if profile.language == "python":
        steps = []
        if "pyproject.toml" in present:
            steps.append("pip install --no-cache-dir -e .")
        if profile.test_command == "pytest":
            steps.append("pip install --no-cache-dir pytest")
        return " && ".join(steps) or None
    if profile.language == "rust":
        return "cargo fetch"
    if profile.language == "go":
        return "go mod download"
    if profile.language == "node" and "package.json" not in present:
        return None
    return None


def container_tag(project_full_name: str) -> str:
    """Stable image tag for a project (e.g. vgc-acme-alpha)."""
    slug = project_full_name.replace("/", "-").replace("_", "-").lower()
    return f"vgc-{slug}"
