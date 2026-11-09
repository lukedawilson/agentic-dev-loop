"""Trigger-script contract tests.

The trigger script owns the per-run engine gate (055): it runs
`agentic-dev-loop doctor --fast` before every run and repairs a failing
gate exactly once with `agentic-dev-loop upgrade` (install latest, sync
the pin), then fails hard if doctor still fails. Tests drive the real
script with stub executables on PATH - no network, no mocks of the
script's logic.

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


def _cli_stub_body(tmp_path: Path, doctor_fail_count: int,
                   upgrade_fail: bool = False) -> str:
    """A doctor-aware agentic-dev-loop stub.

    `doctor --fast` invocations (first arg `doctor`) fail the first
    `doctor_fail_count` times, then succeed; `upgrade` echoes upgrade-ran
    or fails when `upgrade_fail` is set; anything else is the real run and
    echoes `cli-ran: <args>`.
    """
    counter = tmp_path / "doctor.calls"
    upgrade_body = 'echo upgrade-broken >&2; exit 1' if upgrade_fail \
        else 'echo "upgrade-ran: $*"; exit 0'
    return f'''if [[ "$1" == doctor ]]; then
  echo x >> "{str(counter)}"
  n=$(wc -l < "{str(counter)}" | tr -d ' ')
  if [[ "$n" -le {doctor_fail_count} ]]; then
    exit 1
  fi
  exit 0
fi
if [[ "$1" == upgrade ]]; then
  {upgrade_body}
fi
echo "cli-ran: $*"'''


def _stub_env(tmp_path, *, doctor_fail_count=0, upgrade_fail=False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub(bin_dir, "agentic-dev-loop",
               _cli_stub_body(tmp_path, doctor_fail_count, upgrade_fail))
    return bin_dir


def _make_git_stub(bin_dir: Path, tmp_path: Path, dirty: bool) -> None:
    """A git stub that reports the pin dirty only when the marker file exists
    and records every invocation for assertions."""
    marker = tmp_path / "pin.dirty"
    log = tmp_path / "git.calls"
    body = f'''echo "$*" >> "{str(log)}"
if [[ "$1" == rev-parse ]]; then
  echo true
  exit 0
fi
if [[ "$1" == status ]]; then
  if [[ -f "{str(marker)}" ]]; then
    echo " M agentic-dev-loop/.engine-version"
  fi
  exit 0
fi
exit 0'''
    _make_stub(bin_dir, "git", body)
    if dirty:
        marker.touch()


def test_trigger_doctor_pass_skips_upgrade(tmp_path):
    """A clean doctor gate never touches upgrade; the run proceeds directly."""
    bin_dir = _stub_env(tmp_path)

    result = _run_script("--plan", "plan.md", env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    assert "upgrade-ran:" not in result.stdout
    assert "cli-ran:" in result.stdout


def test_trigger_upgrades_once_on_doctor_failure(tmp_path):
    """A failing doctor gate triggers exactly one engine upgrade."""
    counter = tmp_path / "doctor.calls"
    bin_dir = _stub_env(tmp_path, doctor_fail_count=1)
    # Hermetic git: the script's pin-commit block must never touch the real
    # repo (a dirty real pin would drive a real commit attempt here).
    _make_git_stub(bin_dir, tmp_path, dirty=False)

    result = _run_script("--plan", "plan.md", env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    assert "upgrade-ran: upgrade" in result.stdout
    # Doctor runs before the upgrade and once more after it, so two calls.
    assert counter.read_text().count("\n") == 2
    assert "cli-ran:" in result.stdout


def test_trigger_upgrade_commits_dirty_pin(tmp_path):
    """A successful upgrade commits the rewritten pin when it is dirty."""
    bin_dir = _stub_env(tmp_path, doctor_fail_count=1)
    _make_git_stub(bin_dir, tmp_path, dirty=True)

    result = _run_script("--plan", "plan.md", env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "git.calls").read_text()
    assert "add agentic-dev-loop/.engine-version" in calls
    assert "commit -m Upgrade engine" in calls


def test_trigger_upgrade_skips_commit_when_pin_clean(tmp_path):
    """A successful upgrade with a clean pin does not commit."""
    bin_dir = _stub_env(tmp_path, doctor_fail_count=1)
    _make_git_stub(bin_dir, tmp_path, dirty=False)

    result = _run_script("--plan", "plan.md", env=_env_with(bin_dir))

    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "git.calls").read_text()
    assert "commit" not in calls


def test_trigger_upgrade_failure_fails_hard(tmp_path):
    bin_dir = _stub_env(
        tmp_path,
        doctor_fail_count=99,
        upgrade_fail=True,
    )

    result = _run_script("--plan", "plan.md", env=_env_with(bin_dir))

    assert result.returncode != 0
    assert "upgrade failed" in result.stderr
    assert "cli-ran:" not in result.stdout


def test_trigger_doctor_still_failing_after_upgrade(tmp_path):
    bin_dir = _stub_env(
        tmp_path,
        doctor_fail_count=2,
    )

    result = _run_script("--plan", "plan.md", env=_env_with(bin_dir))

    assert result.returncode != 0
    assert "still fails after upgrading the engine" in result.stderr
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
    assert "cli-ran:" not in result.stdout


def test_trigger_requires_exactly_one_flag(tmp_path):
    bin_dir = _stub_env(tmp_path)

    result = _run_script(env=_env_with(bin_dir))

    assert result.returncode != 0
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
