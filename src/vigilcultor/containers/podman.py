"""Podman CLI wrapper (rootless). The command runner is injectable for tests."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class RunResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def real_runner(argv: list[str], timeout: int | None = None) -> RunResult:
    completed = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout
    )
    return RunResult(argv, completed.returncode, completed.stdout, completed.stderr)


class Podman:
    def __init__(self, runner: Callable[..., RunResult] | None = None) -> None:
        self._runner = runner or real_runner

    def _pod(self, *args: str, timeout: int | None = None) -> RunResult:
        return self._runner(["podman", *args], timeout=timeout)

    def version(self) -> str:
        result = self._pod("--version")
        return result.stdout.strip()

    def build(
        self, tag: str, context_dir: str, *, containerfile: str = "Containerfile"
    ) -> RunResult:
        return self._pod(
            "build", "-t", tag, "-f", containerfile, context_dir, timeout=3600
        )

    def run(
        self,
        name: str,
        image: str,
        command: list[str],
        *,
        volumes: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        cpus: float | None = None,
        memory: str | None = None,
        workdir: str | None = None,
        proxy: str | None = None,
        detach: bool = False,
    ) -> RunResult:
        args = ["run", "--name", name, "--rm"]
        if detach:
            args.append("--detach")
        for host, target in (volumes or {}).items():
            args += ["-v", f"{host}:{target}"]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        if cpus is not None:
            args += ["--cpus", str(cpus)]
        if memory is not None:
            args += ["--memory", memory]
        if workdir is not None:
            args += ["-w", workdir]
        if proxy is not None:
            args += [
                "-e", f"HTTP_PROXY={proxy}",
                "-e", f"HTTPS_PROXY={proxy}",
                "-e", "NO_PROXY=localhost,127.0.0.1",
            ]
        args.append(image)
        args += command
        return self._pod(*args, timeout=7200)

    def exec(self, name: str, command: list[str]) -> RunResult:
        return self._pod("exec", name, *command, timeout=7200)

    def stop(self, name: str, *, timeout: int = 20) -> RunResult:
        return self._pod("stop", "-t", str(timeout), name, timeout=60)

    def rm(self, name: str, *, force: bool = True) -> RunResult:
        args = ["rm"] + (["-f"] if force else []) + [name]
        return self._pod(*args, timeout=60)

    def exists(self, name: str) -> bool:
        result = self._pod(
            "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"
        )
        return name in result.stdout.split()
