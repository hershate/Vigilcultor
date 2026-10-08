"""Containerfile generation from an environment profile.

Base images are always fully qualified (docker.io/...) so user-level registry
mirror configuration applies uniformly.
"""

from __future__ import annotations

from vigilcultor.containers.profile import EnvProfile

_WORKDIR = "/workspace"


def generate_containerfile(profile: EnvProfile) -> str:
    """Render a single-stage Containerfile with dependencies pre-installed."""
    lines = [f"FROM {profile.base_image}", f"WORKDIR {_WORKDIR}"]
    if profile.setup_commands:
        joined = " && ".join(profile.setup_commands)
        lines.append("RUN " + joined)
    return "\n".join(lines) + "\n"


def container_tag(project_full_name: str) -> str:
    """Stable image tag for a project (e.g. vgc-acme-alpha)."""
    slug = project_full_name.replace("/", "-").replace("_", "-").lower()
    return f"vgc-{slug}"
