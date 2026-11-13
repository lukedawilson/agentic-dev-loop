"""Regression guard: the built wheel must ship agentic_dev_loop/viz/vendor/.

The viz page loads five Carbon web-component modules from /vendor/*.min.js,
served from disk by server.py's _vendor_file(). pyproject.toml's
[tool.setuptools.package-data] must include "agentic_dev_loop.viz" =
["vendor/*"], otherwise wheel installs (pipx, pip from PyPI) lack vendor/ and
every /vendor/* endpoint 404s. Without the modules, cds-header / cds-tile /
cds-accordion never upgrade and half the viz renders blank (observed with
0.15.6 installed via pipx, 2026-11-10).

page.py needs no guard: it is generated and committed, and inlines every
asset from viz/assets/ (themes.css, echarts) at build time, so it always
ships complete inside the wheel. vendor/ is the only runtime disk read.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "src" / "agentic_dev_loop" / "viz" / "vendor"


def test_wheel_contains_viz_vendor_files(tmp_path: Path) -> None:
    expected = {
        f"agentic_dev_loop/viz/vendor/{p.name}"
        for p in VENDOR_DIR.iterdir()
        if p.is_file()
    }
    assert expected, "source-tree vendor/ is empty; run tools/vendor_carbon_wc.py"

    # --no-build-isolation keeps the suite offline: an isolated build env
    # installs the [build-system] backend from PyPI, which the sandbox denies.
    # The backend therefore comes from the dev extra (setuptools).
    if importlib.util.find_spec("setuptools") is None:
        pytest.skip("setuptools (dev extra) builds the wheel without isolation")
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
         "--no-build-isolation", "-w", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"pip wheel failed:\n{proc.stdout}\n{proc.stderr}"

    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel in {tmp_path}, got {wheels}"

    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())

    missing = sorted(expected - names)
    assert not missing, (
        f"wheel is missing viz vendor assets {missing}; add "
        '"agentic_dev_loop.viz" = ["vendor/*"] to [tool.setuptools.package-data] '
        "in pyproject.toml"
    )
