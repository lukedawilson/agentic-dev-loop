#!/usr/bin/env python3
"""Vendor the pinned Carbon Web Components bundles into the viz assets.

Fetches the per-component ES module bundles from the pinned IBM CDN release,
walks their transitive relative chunk imports (the graph is flat and closed,
probed 2026-11-10: 58 files, zero bare imports), fetches the Carbon theme
sheet, ASCII-sanitises everything, SHA-256 pins every file in
``manifest.json``, and writes the tree to
``src/agentic_dev_loop/viz/assets/vendor/``.

The theme sheet (``themes.css``) is only served at a mutable ``latest`` URL
(versioned paths 404), so the vendored bytes plus their recorded SHA-256 and
fetch date are the pin. Re-vendoring is a deliberate manual step: run this
script without arguments and diff-review the result. Never auto-update.

``--check`` verifies the committed tree against the manifest without any
network access: every manifest file exists with a matching hash, every file
is ASCII, every relative import resolves inside the tree, no bare imports,
and no untracked files sit in the vendor dir.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "src" / "agentic_dev_loop" / "viz" / "vendor"
MANIFEST = VENDOR / "manifest.json"

VERSION = "2.65.0"
BASE = f"https://1.www.s81c.com/common/carbon/web-components/version/v{VERSION}"
THEMES_URL = ("https://1.www.s81c.com/common/carbon/web-components"
              "/tag/v2/latest/themes.css")

# Every custom-element entry point the viz pages may load. code-snippet and
# copy-button are vendored but not referenced (negligible bytes; keeps the
# door open without a re-vendor).
ENTRIES = [
    "accordion",
    "button",
    "checkbox",
    "code-snippet",
    "copy-button",
    "data-table",
    "link",
    "notification",
    "tag",
    "tile",
    "ui-shell",
]

IMPORT_RE = re.compile(r'(?:from|import)\s*"(\./[^"]+)"')
# Probed 2026-11-10: the graph's only non-ASCII bytes are a corrupted ß in a
# dead data-table CSS selector. Replace it; anything else fails loud.
NON_ASCII_REPLACEMENTS = {"ß": "ss"}


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def _sanitize(name: str, data: bytes) -> bytes:
    """Return ASCII-only bytes, applying only the recorded replacements."""
    text = data.decode("utf-8")
    for bad, good in NON_ASCII_REPLACEMENTS.items():
        text = text.replace(bad, good)
    try:
        return text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise SystemExit(
            f"{name}: unexpected non-ASCII content at {exc.start}: "
            f"{text[max(0, exc.start - 40):exc.start + 40]!r} - record a "
            f"replacement in NON_ASCII_REPLACEMENTS only after reviewing the "
            f"context") from exc


def _imports_of(name: str, data: bytes) -> list[str]:
    out = []
    for spec in IMPORT_RE.findall(data.decode("ascii")):
        # The graph is flat: every relative import is a sibling file.
        candidate = spec[len("./"):] if spec.startswith("./") else spec
        if "/" in candidate:
            raise SystemExit(
                f"{name}: import {spec!r} escapes the flat vendor tree - "
                f"the closed-graph assumption is broken, re-probe before "
                f"vendoring")
        out.append(candidate)
    return out


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_tree(files: dict[str, bytes]) -> None:
    VENDOR.mkdir(parents=True, exist_ok=True)
    for stale in VENDOR.iterdir():
        if stale.name != "manifest.json" and stale.name not in files:
            stale.unlink()
    for name, data in sorted(files.items()):
        (VENDOR / name).write_bytes(data)
    manifest = {
        "version": VERSION,
        "fetched": date.today().isoformat(),
        "base": BASE,
        "themes_url": THEMES_URL,
        "entries": ENTRIES,
        "files": {
            name: {"sha256": _sha256(data), "bytes": len(data)}
            for name, data in sorted(files.items())
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n",
                        encoding="ascii")


def fetch() -> None:
    files: dict[str, bytes] = {}
    queue = [f"{name}.min.js" for name in ENTRIES]
    while queue:
        name = queue.pop(0)
        if name in files:
            continue
        data = _sanitize(name, _fetch(f"{BASE}/{name}"))
        files[name] = data
        queue.extend(c for c in _imports_of(name, data) if c not in files)
    themes = _sanitize("themes.css", _fetch(THEMES_URL))
    files["themes.css"] = themes
    _write_tree(files)
    print(f"vendored {len(files)} files "
          f"({sum(len(d) for d in files.values())} bytes) into "
          f"{VENDOR.relative_to(ROOT)}")


def check() -> list[str]:
    """Return a list of integrity failures (empty means the tree is good)."""
    errors: list[str] = []
    if not MANIFEST.is_file():
        return [f"{MANIFEST.relative_to(ROOT)} is missing - run "
                f"tools/vendor_carbon_wc.py to vendor the Carbon bundles"]
    manifest = json.loads(MANIFEST.read_text(encoding="ascii"))
    expected = manifest["files"]
    for name, meta in expected.items():
        path = VENDOR / name
        if not path.is_file():
            errors.append(f"vendor/{name}: missing")
            continue
        data = path.read_bytes()
        digest = _sha256(data)
        if digest != expected[name]["sha256"]:
            errors.append(f"vendor/{name}: SHA256 {digest} != manifest "
                          f"{expected[name]['sha256']}")
        try:
            data.decode("ascii")
        except UnicodeDecodeError:
            errors.append(f"vendor/{name}: non-ASCII content")
            continue
        if name.endswith(".js"):
            for spec in IMPORT_RE.findall(data.decode("ascii")):
                target = spec[len("./"):] if spec.startswith("./") else spec
                if "/" in target:
                    errors.append(f"vendor/{name}: import {spec!r} escapes "
                                  f"the vendor tree")
                elif target not in expected:
                    errors.append(f"vendor/{name}: import {spec!r} not in "
                                  f"the manifest")
        if re.search(rb'(?:from|import)\s*"[^.][^"]*"', data):
            errors.append(f"vendor/{name}: bare module specifier import")
    for path in VENDOR.iterdir():
        if path.name != "manifest.json" and path.name not in expected:
            errors.append(f"vendor/{path.name}: not in the manifest")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify the vendored tree offline")
    args = parser.parse_args(argv)
    if args.check:
        errors = check()
        for error in errors:
            print(f"vendor check: {error}", file=sys.stderr)
        return 1 if errors else 0
    fetch()
    errors = check()
    if errors:
        for error in errors:
            print(f"vendor check: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
