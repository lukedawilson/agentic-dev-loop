"""The suite must exercise the working tree, not an installed wheel.

This repo dogfoods its own published wheel in .venv (deliberate). Plain
pytest would otherwise import the published code and silently test stale
behaviour. This test pins the import to src/ - it fails the moment the
conftest sys.path insertion stops working.
"""

from pathlib import Path

import agentic_dev_loop

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"


def test_import_resolves_to_working_tree():
    resolved = Path(agentic_dev_loop.__file__).resolve()
    assert resolved.is_relative_to(REPO_SRC), (
        f"agentic_dev_loop imported from {resolved}, not the working tree "
        f"({REPO_SRC}) - conftest's src/ sys.path insertion is not effective"
    )


def test_smoke_tools_bootstrap_the_working_tree():
    """Human-run smoke tools must import src/ ahead of the installed wheel.

    The venv dogfoods an installed (non-editable) wheel, so both the moved
    viz demo and the smoke runner put src/ first on sys.path - the same
    shadowing tests/conftest.py does for the suite.
    """
    smoke_dir = REPO_ROOT / "tests" / "smoke"
    for name in ("viz_demo.py", "run_smoke.py"):
        text = (smoke_dir / name).read_text(encoding="utf-8")
        assert 'SRC_DIR = REPO_ROOT / "src"' in text, name
        assert "sys.path.insert(0, str(SRC_DIR))" in text, name
