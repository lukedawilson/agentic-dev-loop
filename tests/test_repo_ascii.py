"""Guards for the repo-wide em dash ban (AGENTS.md).

The em dash (U+2014) has repeatedly produced Windows CI encoding failures
(the cp1252 vs UTF-8 class). Two guards share one implementation,
`tools/check_no_em_dash.py`:

- this module runs the checker over every tracked file and proves the
  checker itself flags and clears correctly;
- `.pre-commit-config.yaml` runs the same script over staged files so a
  violating commit is blocked before it exists.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "tools" / "check_no_em_dash.py"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *arguments],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def test_checker_flags_a_file_with_an_em_dash(tmp_path):
    offending = tmp_path / "offending.md"
    offending.write_text("clean line\nbad \u2014 line\n", encoding="utf-8")
    clean = tmp_path / "clean.md"
    clean.write_text("no em dashes here\n", encoding="utf-8")

    result = _run(str(offending), str(clean))

    assert result.returncode == 1
    assert f"{offending}:2" in result.stdout
    assert str(clean) not in result.stdout


def test_checker_passes_clean_files(tmp_path):
    clean = tmp_path / "clean.md"
    clean.write_text("no em dashes here\n", encoding="utf-8")

    result = _run(str(clean))

    assert result.returncode == 0
    assert result.stdout == ""


def test_no_em_dash_in_any_tracked_file():
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("no .git directory - the guard covers tracked files only")
    result = _run()
    assert result.returncode == 0, result.stdout
