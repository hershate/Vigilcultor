"""Profile detection, Containerfile generation and podman wrapper tests."""

from __future__ import annotations

from vigilcultor.containers.imagegen import container_tag, generate_containerfile
from vigilcultor.containers.podman import Podman, RunResult
from vigilcultor.containers.profile import detect_profile


class TestProfile:
    def test_python_pyproject(self):
        profile = detect_profile(
            ["pyproject.toml", "src/x.py", "README.md"],
            read_file=lambda name: "[tool.pytest.ini_options]",
        )
        assert profile is not None
        assert profile.language == "python"
        assert profile.base_image.startswith("docker.io/library/python")
        assert profile.test_command == "pytest"
        assert profile.setup_commands == ["pip install -e ."]

    def test_python_requirements(self):
        profile = detect_profile(["requirements.txt", "app.py"])
        assert profile is not None
        assert profile.setup_commands == ["pip install -r requirements.txt"]
        assert profile.test_command is None

    def test_node_with_scripts(self):
        package_json = '{"scripts": {"build": "vite build", "test": "vitest"}}'
        profile = detect_profile(["package.json"], read_file=lambda name: package_json)
        assert profile is not None
        assert profile.language == "node"
        assert profile.build_command == "npm run build"
        assert profile.test_command == "npm test"

    def test_rust(self):
        profile = detect_profile(["Cargo.toml", "src/main.rs"])
        assert profile is not None
        assert profile.language == "rust"
        assert profile.test_command == "cargo test"

    def test_go(self):
        profile = detect_profile(["go.mod", "main.go"])
        assert profile is not None
        assert profile.language == "go"

    def test_priority_rust_over_python(self):
        profile = detect_profile(["Cargo.toml", "pyproject.toml"])
        assert profile is not None and profile.language == "rust"

    def test_unknown_returns_none(self):
        assert detect_profile(["README.md", "Makefile"]) is None


class TestImagegen:
    def test_containerfile_layout(self):
        profile = detect_profile(
            ["pyproject.toml"], read_file=lambda name: "pytest"
        )
        assert profile is not None
        text = generate_containerfile(profile)
        assert text.startswith("FROM docker.io/library/python")
        assert "WORKDIR /workspace" in text
        assert "RUN pip install -e ." in text
        assert text.endswith("\n")

    def test_tag_slug(self):
        assert container_tag("acme/Alpha_Beta") == "vgc-acme-alpha-beta"


class FakeRunner:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, argv, timeout=None):
        self.calls.append(argv)
        return RunResult(argv, self.returncode, self.stdout, self.stderr)


class TestPodman:
    def test_build_argv(self):
        runner = FakeRunner()
        Podman(runner).build("vgc-x", "/tmp/ctx")
        assert runner.calls[0] == [
            "podman", "build", "-t", "vgc-x", "-f", "Containerfile", "/tmp/ctx"
        ]

    def test_run_argv_options(self):
        runner = FakeRunner()
        Podman(runner).run(
            "c1",
            "vgc-x",
            ["pytest"],
            volumes={"/data": "/workspace"},
            env={"FOO": "bar"},
            cpus=2.0,
            memory="1g",
            workdir="/workspace",
            proxy="http://127.0.0.1:8080",
        )
        argv = runner.calls[0]
        assert argv[:4] == ["podman", "run", "--name", "c1"]
        assert "--rm" in argv
        assert "-v" in argv and "/data:/workspace" in argv
        assert "-e" in argv and "FOO=bar" in argv
        assert "--cpus" in argv and "2.0" in argv
        assert "--memory" in argv and "1g" in argv
        assert "HTTP_PROXY=http://127.0.0.1:8080" in argv
        assert argv[-2:] == ["vgc-x", "pytest"]

    def test_exec_and_lifecycle(self):
        runner = FakeRunner()
        pod = Podman(runner)
        pod.exec("c1", ["ls", "-la"])
        pod.stop("c1")
        pod.rm("c1")
        assert runner.calls[0][1] == "exec"
        assert runner.calls[1][1:4] == ["stop", "-t", "20"]
        assert runner.calls[2][1:] == ["rm", "-f", "c1"]

    def test_exists_matches_filter_output(self):
        runner = FakeRunner(stdout="c1\n")
        assert Podman(runner).exists("c1") is True
        runner2 = FakeRunner(stdout="")
        assert Podman(runner2).exists("c1") is False
