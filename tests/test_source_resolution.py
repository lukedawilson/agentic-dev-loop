"""The suite must exercise the working tree, not an installed wheel.

This repo dogfoods its own published wheel in .venv (deliberate). Plain
pytest would otherwise import the published code and silently test stale
behaviour. This test pins the import to src/ - it fails the moment the
conftest sys.path insertion stops working.
"""

import re
import subprocess
from pathlib import Path

import pytest

import agentic_dev_loop

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"


def test_import_resolves_to_working_tree():
    resolved = Path(agentic_dev_loop.__file__).resolve()
    assert resolved.is_relative_to(REPO_SRC), (
        f"agentic_dev_loop imported from {resolved}, not the working tree "
        f"({REPO_SRC}) - conftest's src/ sys.path insertion is not effective"
    )


def test_run_demo_sh_shadows_wheel_with_working_tree():
    """run-demo.sh must serve the working tree, not the published wheel.

    The venv dogfoods an installed (non-editable) wheel, so viz_demo.py would
    import stale code. The script must put src/ first on PYTHONPATH, mirroring
    what conftest does for the test suite.
    """
    script = (REPO_ROOT / "run-demo.sh").read_text()
    # Rejoin backslash continuations so the logical exec line is one line.
    logical_lines = script.replace("\\\n", " ").splitlines()
    exec_line = next(
        line for line in logical_lines
        if re.search(r"exec\s+", line) and "viz_demo.py" in line
    )
    assert 'PYTHONPATH="$ROOT/src' in exec_line, (
        "run-demo.sh does not put src/ first on PYTHONPATH - the demo would "
        "serve the installed wheel instead of the working tree"
    )


@pytest.mark.skipif(
    not (REPO_ROOT / ".venv" / "bin" / "python").exists(),
    reason="repo venv not present (e.g. bare CI checkout)",
)
def test_run_demo_sh_help_exits_cleanly():
    """--help must exit 0 without starting a server or opening a browser."""
    proc = subprocess.run(
        ["bash", str(REPO_ROOT / "run-demo.sh"), "--help"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"run-demo.sh --help failed ({proc.returncode}): {proc.stderr}"
    )
    assert "usage" in proc.stdout.lower()
