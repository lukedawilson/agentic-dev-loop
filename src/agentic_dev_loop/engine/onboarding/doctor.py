"""``agentic-dev-loop doctor``: host-side onboarding gate.

Prints one actionable line per non-pass check, hard failures first. Exit 1
iff at least one hard check fails; warnings never fail the run.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from dataclasses import asdict
from pathlib import Path

from agentic_dev_loop.core.config import load_config
from agentic_dev_loop.core.profiles import ProfileError, load_profile

from .checks import Check, HostProbes, collect_checks

__all__ = ["run_doctor"]


def _installed_version() -> str | None:
    try:
        return importlib.metadata.version("agentic-dev-loop")
    except importlib.metadata.PackageNotFoundError:
        return None


def _print_failures(checks: list[Check]) -> None:
    failures = [check for check in checks if not check.ok]
    for severity in ("hard", "warn"):
        for check in failures:
            if check.severity != severity:
                continue
            print(f"{severity.upper()}: {check.name}: {check.message}")
            if check.fix_text:
                print(f"  fix: {check.fix_text}")


def _doctor_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-dev-loop doctor",
        description="Probe this host and repo for onboarding prerequisites.")
    parser.add_argument("--fast", action="store_true",
                        help="skip checks needing more than one quick network "
                             "round trip")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON results")
    return parser


def run_doctor(argv: list[str], *, workspace: Path | None = None,
               probes: HostProbes | None = None,
               installed: str | None = None) -> int:
    args = _doctor_parser().parse_args(argv)

    root = Path(workspace or Path.cwd()).resolve()
    probes = probes or HostProbes()
    if installed is None:
        installed = _installed_version()

    pipeline = None
    profile = None
    extra: list[Check] = []
    pipeline_path = root / "agentic-dev-loop" / "pipeline.yaml"
    if pipeline_path.is_file():
        try:
            pipeline = load_config(pipeline_path)
        except Exception as exc:
            extra.append(Check("pipeline", "hard", False, str(exc),
                               "fix the pipeline config"))
        if pipeline is not None and pipeline.profile and installed:
            try:
                profile = load_profile(pipeline.profile, installed)
            except ProfileError as exc:
                extra.append(Check("profile", "hard", False, str(exc),
                                   "upgrade the engine or fix the profile"))

    checks = collect_checks(profile, pipeline, installed or "", args.fast,
                            workspace=root, probes=probes) + extra

    if args.json:
        print(json.dumps([asdict(check) for check in checks]))
    else:
        _print_failures(checks)
    return 1 if any(
        check.severity == "hard" and not check.ok for check in checks) else 0
