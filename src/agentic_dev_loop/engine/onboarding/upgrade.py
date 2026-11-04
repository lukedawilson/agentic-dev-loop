"""``agentic-dev-loop upgrade``: install the latest engine and sync the pin.

Installs the latest release with pipx and rewrites
``agentic-dev-loop/.engine-version`` to the installed version, so the host
install and the committed pin converge in one action.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from agentic_dev_loop.core.profiles import ProfileError, parse_version

__all__ = ["run_upgrade"]


def _resolve(name: str, env: dict | None) -> str | None:
    if env is not None:
        return shutil.which(name, path=env.get("PATH", ""))
    return shutil.which(name)


def run_upgrade(argv: list[str], *, workspace: Path | None = None,
                env: dict | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentic-dev-loop upgrade",
        description="Install the latest engine release and rewrite the "
                    "project pin file to match.")
    parser.parse_args(argv)

    root = Path(workspace or Path.cwd()).resolve()
    pipx = _resolve("pipx", env)
    if pipx is None:
        print("error: pipx is not on PATH; the engine install must be "
              "pipx-managed", file=sys.stderr)
        return 1

    result = subprocess.run(
        [pipx, "install", "--force", "agentic-dev-loop"],
        capture_output=True, text=True, check=False, env=env)
    if result.returncode != 0:
        detail = (result.stderr or "unknown error").strip()
        print(f"error: pipx install failed: {detail}", file=sys.stderr)
        return 1

    cli = _resolve("agentic-dev-loop", env)
    if cli is None:
        print("error: agentic-dev-loop is not on PATH", file=sys.stderr)
        return 1
    version_result = subprocess.run(
        [cli, "--version"], capture_output=True, text=True,
        check=False, env=env)
    if version_result.returncode != 0:
        detail = (version_result.stderr or "unknown error").strip()
        print(f"error: could not read the installed version: {detail}",
              file=sys.stderr)
        return 1

    tokens = version_result.stdout.split()
    version = tokens[-1] if tokens else ""
    try:
        parse_version(version)
    except ProfileError:
        print("error: unreadable version from --version output: "
              f"{version_result.stdout.strip()!r}", file=sys.stderr)
        return 1

    out = root / "agentic-dev-loop"
    out.mkdir(parents=True, exist_ok=True)
    (out / ".engine-version").write_text(version + "\n", encoding="utf-8")
    print(f"upgraded agentic-dev-loop to {version}; .engine-version updated")
    return 0
