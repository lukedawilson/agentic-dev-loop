"""Trigger-script contract tests.

The trigger script owns the per-run engine gate (055): it reads
`agentic-dev-loop/.engine-version`, runs `agentic-dev-loop doctor --fast`
before every run, repairs a pin/install mismatch exactly once with
`pipx install --force "agentic-dev-loop==<pin>"`, and never runs
`pipx upgrade` (the old upgrade-on-every-run contract is gone). Tests
drive the real script with stub executables on PATH - no network, no
mocks of the script's logic.

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


def _pin() -> str:
    return (REPO_ROOT / "agentic-dev-loop/.engine-version").read_text().strip()


def _cli_stub_body(tmp_path: Path, doctor_fail_count: int) -> str:
    """A doctor-aware agentic-dev-loop stub.

    `doctor --fast` invocations (first arg `doctor`) fail the first
    `doctor_fail_count` times, then succeed; anything else is the real run
    and echoes `cli-ran: <args>`.
    """
    counter = tmp_path / "doctor.calls"
    return f'''if [[ "$1" == doctor ]]; then
  echo x >> "{str(counter)}"
  n=$(wc -l < "{str(counter)}" | tr -d ' ')
  if [[ "$n" -le {doctor_fail_count} ]]; then
    exit 1
  fi
  exit 0
fi
echo "cli-ran: $*"'''


def _stub_env(tmp_path, *, doctor_fail_count=0, pipx_body="exit 0"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub(bin_dir, "agentic-dev-loop",
               _cli_stub_body(tmp_path, doctor_fail_count))
    _make_stub(bin_dir, "pipx", pipx_body)
    return bin_dir


def test_trigger_doctor_pass_skips_pipx(tmp_path):
    """A clean doctor gate never touches pipx; the run proceeds directly."""
    pipx_log = tmp_path / "pipx.log"
    bin_dir = _stub_env(
        tmp_path,
        pipx_body=f'echo "$*" >> "{str(pipx_log)}"',
    )

    result = _run_script("--plan", "plan.md", env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    assert not pipx_log.exists()
    assert "cli-ran:" in result.stdout


def test_trigger_repairs_pin_mismatch_once(tmp_path):
    """A failing doctor gate triggers exactly one pinned pipx repair."""
    pipx_log = tmp_path / "pipx.log"
    counter = tmp_path / "doctor.calls"
    bin_dir = _stub_env(
        tmp_path,
        doctor_fail_count=1,
        pipx_body=f'echo "$*" >> "{str(pipx_log)}"',
    )

    result = _run_script("--plan", "plan.md", env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    assert pipx_log.read_text().strip() == f"install --force agentic-dev-loop=={_pin()}"
    # Doctor runs before the repair and once more after it, so two calls.
    assert counter.read_text().count("\n") == 2
    assert "cli-ran:" in result.stdout


def test_trigger_repair_failure_fails_hard(tmp_path):
    bin_dir = _stub_env(
        tmp_path,
        doctor_fail_count=99,
        pipx_body="echo repair-broken >&2; exit 1",
    )

    result = _run_script("--plan", "plan.md", env=_env_with(bin_dir))

    assert result.returncode != 0
    assert "could not install agentic-dev-loop==" in result.stderr
    assert "cli-ran:" not in result.stdout


def test_trigger_doctor_still_failing_after_repair(tmp_path):
    bin_dir = _stub_env(
        tmp_path,
        doctor_fail_count=2,
        pipx_body="exit 0",
    )

    result = _run_script("--plan", "plan.md", env=_env_with(bin_dir))

    assert result.returncode != 0
    assert "still fails after repairing the engine pin" in result.stderr
    assert "cli-ran:" not in result.stdout


def test_trigger_errors_when_cli_absent():
    result = _run_script(env=_env_with())

    assert result.returncode != 0
    assert "pipx install" in result.stderr


def test_trigger_rejects_both_flags(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script("--plan", "plan.md", "--aidlc-unit", "unit-3",
                         env=_env_with(bin_dir))

    assert result.returncode != 0
    assert ("pass exactly one of --plan <path>, --aidlc-unit <slug>, or "
            "--resume <thread-id>") in result.stderr
    assert "cli-ran:" not in result.stdout


def test_trigger_requires_exactly_one_flag(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script(env=_env_with(bin_dir))

    assert result.returncode != 0
    assert ("pass exactly one of --plan <path>, --aidlc-unit <slug>, or "
            "--resume <thread-id>") in result.stderr
    assert "cli-ran:" not in result.stdout


def test_trigger_forwards_aidlc_unit(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script("--aidlc-unit", "unit-3", env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    assert "cli-ran:" in result.stdout
    assert "--aidlc-unit unit-3" in result.stdout


def test_trigger_forwards_resume(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script("--resume", "07a767a50de64529ac442759b3685a78",
                         env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    assert "cli-ran:" in result.stdout
    assert "--resume 07a767a50de64529ac442759b3685a78" in result.stdout
    assert "--plan" not in result.stdout
    assert "--aidlc-unit" not in result.stdout


def test_trigger_resume_equals_form(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script("--resume=07a767a50de64529ac442759b3685a78",
                         env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    assert "--resume 07a767a50de64529ac442759b3685a78" in result.stdout


def test_trigger_resume_missing_value_is_usage_error(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script("--resume", env=_env_with(bin_dir))

    assert result.returncode != 0
    assert "--resume requires a thread id" in result.stderr
    assert "cli-ran:" not in result.stdout


def test_trigger_rejects_resume_with_plan(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script("--resume", "abc123", "--plan", "plan.md",
                         env=_env_with(bin_dir))

    assert result.returncode != 0
    assert ("pass exactly one of --plan <path>, --aidlc-unit <slug>, or "
            "--resume <thread-id>") in result.stderr
    assert "cli-ran:" not in result.stdout


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
