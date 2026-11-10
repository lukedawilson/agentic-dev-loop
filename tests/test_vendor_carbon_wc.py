"""Integrity guard for the vendored Carbon Web Components tree.

The viz pages load Carbon from ``viz/assets/vendor/`` (plan 064). The tree
is pinned by ``manifest.json``; this suite runs the vendor tool's offline
``--check`` so a corrupted, drifted, or incomplete tree fails CI.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_vendored_carbon_tree_matches_manifest():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "vendor_carbon_wc.py"),
         "--check"], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_manifest_records_pin_and_fetch_date():
    import json
    manifest = json.loads(
        (ROOT / "src" / "agentic_dev_loop" / "viz" / "vendor"
         / "manifest.json").read_text(encoding="ascii"))
    assert manifest["version"] == "2.65.0"
    assert manifest["fetched"]  # date string, recorded at vendor time
    for entry in manifest["entries"]:
        assert f"{entry}.min.js" in manifest["files"]
    assert "themes.css" in manifest["files"]
