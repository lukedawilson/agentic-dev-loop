"""Trigger-script contract tests.

The trigger script must keep the pipx-managed engine install current:
a stale install rejects newer pipeline configs at load time (e.g.
`deps_policy` in a11 rejected by an a10 install), so an existing install
is upgraded on every run. Tests drive the real script with stub
executables on PATH - no network, no mocks of the script's logic.

The script is bash, so it is executed through `bash`.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "trigger-agentic-dev-loop.sh"


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required to run the trigger script")
    return bash


def _run_script(*args: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_bash(), str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


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


def test_trigger_upgrades_existing_install(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pipx_log = tmp_path / "pipx.log"
    _make_stub(
        bin_dir,
        "agentic-dev-loop",
        'echo "cli-ran: $*"',
    )
    _make_stub(
        bin_dir,
        "pipx",
        f'echo "$*" >> "{str(pipx_log)}"',
    )

    result = _run_script("--plan", "plan.md", env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    assert pipx_log.read_text().strip() == "upgrade --cooldown 0 --pip-args=--prerelease=allow agentic-dev-loop"
    # The upgrade happens before the loop is launched.
    assert "cli-ran:" in result.stdout


def test_trigger_fails_fast_when_upgrade_fails(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub(bin_dir, "agentic-dev-loop", "echo should-not-run")
    _make_stub(bin_dir, "pipx", "echo upgrade-broken >&2; exit 1")

    result = _run_script(env=_env_with(bin_dir))

    assert result.returncode != 0
    assert "pipx upgrade" in result.stderr
    assert "should-not-run" not in result.stdout


def test_trigger_errors_when_cli_absent():
    result = _run_script(env=_env_with())

    assert result.returncode != 0
    assert "pipx install" in result.stderr


def _stub_env(tmp_path, *, cli_body='echo "cli-ran: $*"', pipx_body="exit 0"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub(bin_dir, "agentic-dev-loop", cli_body)
    _make_stub(bin_dir, "pipx", pipx_body)
    return bin_dir


def test_trigger_rejects_both_flags(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script("--plan", "plan.md", "--aidlc-unit", "unit-3",
                         env=_env_with(bin_dir))

    assert result.returncode != 0
    assert ("pass exactly one of --plan <path> or --aidlc-unit <slug>, "
            "not both") in result.stderr
    assert "cli-ran:" not in result.stdout


def test_trigger_requires_exactly_one_flag(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script(env=_env_with(bin_dir))

    assert result.returncode != 0
    assert ("pass exactly one of --plan <path> or --aidlc-unit <slug>"
            in result.stderr)
    assert "cli-ran:" not in result.stdout


def test_trigger_forwards_aidlc_unit(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script("--aidlc-unit", "unit-3", env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    assert "cli-ran:" in result.stdout
    assert "--aidlc-unit unit-3" in result.stdout


def test_trigger_unit_positional_is_aidlc_shorthand(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script("unit-3", "--dry-run", env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    assert "--aidlc-unit unit-3" in result.stdout
    assert "--dry-run" in result.stdout


def test_trigger_passes_other_flags_through(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script("--plan", "plan.md", "--dry-run", "--max-attempts", "3",
                         env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    assert "--plan plan.md --dry-run --max-attempts 3" in result.stdout


def test_trigger_flag_missing_value_is_usage_error(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script("--plan", env=_env_with(bin_dir))

    assert result.returncode != 0
    assert "--plan requires a path" in result.stderr
    assert "cli-ran:" not in result.stdout


def test_trigger_equals_forms_accepted(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script("--aidlc-unit=unit-3", env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    assert "--aidlc-unit unit-3" in result.stdout
