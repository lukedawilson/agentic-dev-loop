"""055 Task 7: doctor checks and exit-code contract."""

from __future__ import annotations

import json
from pathlib import Path

from agentic_dev_loop.core.profiles import (
    PremiseFile,
    Profile,
    ProfileIsolation,
)
from agentic_dev_loop.engine.onboarding.checks import collect_checks
from agentic_dev_loop.engine.onboarding.doctor import run_doctor


class FakeProbes:
    def __init__(self, **overrides):
        self.docker = True
        self.daemon = (True, "1.45", "linux", "")
        self.image = True
        self.git = True
        self.release = "0.10.0"
        self.service = True
        self.command = True
        for key, value in overrides.items():
            setattr(self, key, value)

    def docker_present(self):
        return self.docker

    def daemon_info(self):
        return self.daemon

    def image_ok(self, image):
        return self.image

    def git_repo(self, workspace):
        return self.git

    def latest_release(self):
        return self.release

    def service_reachable(self, host, port):
        return self.service

    def command_ok(self, command):
        return self.command


def _pipeline(workspace: Path, api_key_env: str = "PATH") -> Path:
    out = workspace / "agentic-dev-loop"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "pipeline.yaml"
    path.write_text(
        "name: fixture\n"
        "deps_policy: open\n"
        f"llm: {{model: openai/x, api_key_env: {api_key_env}}}\n"
        "agents_dir: agents\n"
        "steps:\n"
        "  - {name: dev, agent: dev, on_pass: success}\n",
        encoding="utf-8")
    return path


def _pin(workspace: Path, version: str) -> None:
    out = workspace / "agentic-dev-loop"
    out.mkdir(parents=True, exist_ok=True)
    (out / ".engine-version").write_text(version + "\n", encoding="utf-8")


def _profile(**kwargs) -> Profile:
    return Profile(
        name="python", engine_min="0.1.0",
        isolation=ProfileIsolation(image="img@sha256:abc", cpus=1),
        **kwargs,
    )


def test_happy_path_exits_zero_with_no_output(tmp_path: Path, capsys):
    _pipeline(tmp_path)
    _pin(tmp_path, "0.10.0")
    assert run_doctor(["--fast"], workspace=tmp_path,
                      probes=FakeProbes(), installed="0.10.0") == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_installed_pin_mismatch_is_hard_with_fix_text(tmp_path: Path, capsys):
    _pipeline(tmp_path)
    _pin(tmp_path, "0.9.2")
    code = run_doctor(["--fast"], workspace=tmp_path,
                      probes=FakeProbes(release="0.9.2"), installed="0.10.0")
    out = capsys.readouterr().out
    assert code == 1
    assert "HARD" in out
    assert "run agentic-dev-loop upgrade" in out


def test_missing_placeholder_on_path_is_hard(tmp_path: Path, capsys):
    _pipeline(tmp_path)
    _pin(tmp_path, "0.10.0")
    code = run_doctor(["--fast"], workspace=tmp_path,
                      probes=FakeProbes(docker=False), installed="0.10.0")
    assert code == 1
    assert "HARD" in capsys.readouterr().out


def test_env_file_is_hard(tmp_path: Path, capsys):
    _pipeline(tmp_path)
    _pin(tmp_path, "0.10.0")
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    code = run_doctor(["--fast"], workspace=tmp_path,
                      probes=FakeProbes(), installed="0.10.0")
    assert code == 1
    assert ".env" in capsys.readouterr().out


def test_env_example_is_whitelisted(tmp_path: Path):
    _pipeline(tmp_path)
    _pin(tmp_path, "0.10.0")
    (tmp_path / ".env.example").write_text("X=\n", encoding="utf-8")
    assert run_doctor(["--fast"], workspace=tmp_path,
                      probes=FakeProbes(), installed="0.10.0") == 0


def test_missing_premise_file_is_warning(tmp_path: Path):
    profile = _profile(premise_files=[
        PremiseFile(path="NuGet.Config", content="<configuration/>")])
    checks = collect_checks(profile, None, "0.10.0", True,
                            workspace=tmp_path, probes=FakeProbes())
    premise = [c for c in checks if "premise" in c.name]
    assert premise and not premise[0].ok
    assert premise[0].severity == "warn"


def test_missing_llm_key_is_warning(tmp_path: Path, monkeypatch):
    _pipeline(tmp_path, api_key_env="ABSENT_KEY_055")
    _pin(tmp_path, "0.10.0")
    monkeypatch.delenv("ABSENT_KEY_055", raising=False)
    code = run_doctor(["--fast"], workspace=tmp_path,
                      probes=FakeProbes(), installed="0.10.0")
    assert code == 0


def test_only_hard_failures_exit_one(tmp_path: Path):
    _pipeline(tmp_path)
    _pin(tmp_path, "0.10.0")
    assert run_doctor(["--fast"], workspace=tmp_path,
                      probes=FakeProbes(docker=False), installed="0.10.0") == 1
    assert run_doctor(["--fast"], workspace=tmp_path,
                      probes=FakeProbes(), installed="0.10.0") == 0


def test_json_shape(tmp_path: Path, capsys):
    _pipeline(tmp_path)
    _pin(tmp_path, "0.10.0")
    assert run_doctor(["--fast", "--json"], workspace=tmp_path,
                      probes=FakeProbes(), installed="0.10.0") == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list) and payload
    assert set(payload[0]) == {"name", "severity", "ok", "message", "fix_text"}
