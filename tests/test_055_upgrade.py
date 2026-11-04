"""055 follow-up: the ``upgrade`` command.

``agentic-dev-loop upgrade`` installs the latest engine release with pipx and
rewrites ``agentic-dev-loop/.engine-version`` to the installed version, so
the host install and the committed pin converge in one action. Tests drive
the real function with stub executables on PATH - no network, no mocks of
the upgrade logic.
"""

import os
from pathlib import Path

import pytest

from agentic_dev_loop.engine.onboarding.upgrade import run_upgrade


def _make_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/bash\n{body}\n")
    path.chmod(0o755)
    if not os.access(path, os.X_OK):
        pytest.skip("tmp filesystem is mounted noexec; stub executables cannot run")


def _env_with(bin_dir: Path | None = None) -> dict:
    entries = ["/usr/bin", "/bin"]
    if bin_dir is not None:
        entries.insert(0, str(bin_dir))
    return {**os.environ, "PATH": ":".join(entries)}


def _stub_env(tmp_path: Path, *, pipx_body: str, cli_body: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub(bin_dir, "pipx", pipx_body)
    _make_stub(bin_dir, "agentic-dev-loop", cli_body)
    return bin_dir


def test_upgrade_installs_latest_and_writes_pin(tmp_path, capsys):
    pipx_log = tmp_path / "pipx.log"
    bin_dir = _stub_env(
        tmp_path,
        pipx_body=f'echo "$*" >> "{pipx_log}"',
        cli_body='echo "agentic-dev-loop 0.10.4"',
    )

    code = run_upgrade([], workspace=tmp_path, env=_env_with(bin_dir))

    assert code == 0
    assert pipx_log.read_text().strip() == "install --force agentic-dev-loop"
    pin = tmp_path / "agentic-dev-loop" / ".engine-version"
    assert pin.read_text(encoding="utf-8") == "0.10.4\n"
    assert "upgraded agentic-dev-loop to 0.10.4" in capsys.readouterr().out


def test_upgrade_overwrites_existing_pin(tmp_path):
    out = tmp_path / "agentic-dev-loop"
    out.mkdir()
    (out / ".engine-version").write_text("0.10.2\n", encoding="utf-8")
    bin_dir = _stub_env(
        tmp_path,
        pipx_body="exit 0",
        cli_body='echo "agentic-dev-loop 0.10.4"',
    )

    code = run_upgrade([], workspace=tmp_path, env=_env_with(bin_dir))

    assert code == 0
    assert (out / ".engine-version").read_text(encoding="utf-8") == "0.10.4\n"


def test_upgrade_pipx_failure_is_loud(tmp_path, capsys):
    bin_dir = _stub_env(
        tmp_path,
        pipx_body="echo index-unreachable >&2; exit 1",
        cli_body='echo "agentic-dev-loop 0.10.4"',
    )

    code = run_upgrade([], workspace=tmp_path, env=_env_with(bin_dir))

    assert code == 1
    captured = capsys.readouterr()
    assert "index-unreachable" in captured.err
    assert not (tmp_path / "agentic-dev-loop" / ".engine-version").exists()


def test_upgrade_version_read_failure_is_loud(tmp_path, capsys):
    bin_dir = _stub_env(
        tmp_path,
        pipx_body="exit 0",
        cli_body="echo version-unknown >&2; exit 1",
    )

    code = run_upgrade([], workspace=tmp_path, env=_env_with(bin_dir))

    assert code == 1
    assert "could not read the installed version" in capsys.readouterr().err
    assert not (tmp_path / "agentic-dev-loop" / ".engine-version").exists()


def test_upgrade_unparseable_version_is_loud(tmp_path, capsys):
    bin_dir = _stub_env(
        tmp_path,
        pipx_body="exit 0",
        cli_body='echo "agentic-dev-loop nope"',
    )

    code = run_upgrade([], workspace=tmp_path, env=_env_with(bin_dir))

    assert code == 1
    assert "unreadable version" in capsys.readouterr().err
    assert not (tmp_path / "agentic-dev-loop" / ".engine-version").exists()


def test_upgrade_missing_pipx_is_loud(tmp_path, capsys):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub(bin_dir, "agentic-dev-loop", 'echo "agentic-dev-loop 0.10.4"')

    code = run_upgrade([], workspace=tmp_path, env=_env_with(bin_dir))

    assert code == 1
    assert "pipx is not on PATH" in capsys.readouterr().err
