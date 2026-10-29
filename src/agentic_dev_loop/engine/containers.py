"""Host-side container inspection and reaping (042 D5/D6).

Reads the engine's isolation-state container registry plus the labeled
engine-owned daemon objects, and resolves each entry to a verdict without
ever removing anything whose ownership is unproven. The only mutating path is
``reap_orphaned_containers`` (registry-only, pid-gated), which the CLI's
``containers --reap`` flag invokes explicitly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .isolation import (CommandRunner, _default_runner, _pid_alive_matches,
                        _read_registry)


def _labeled_names(runner: CommandRunner, kind: str) -> set[str]:
    """The engine-owned object names currently visible to the daemon."""
    if kind == "network":
        argv = ["docker", "network", "ls", "--filter",
                "label=agentic-dev-loop=true", "--format", "{{.Name}}"]
    else:
        argv = ["docker", "ps", "-a", "--filter",
                "label=agentic-dev-loop=true", "--format", "{{.Names}}"]
    res = runner.run(argv, timeout=60)
    if res.exit_code != 0:
        return set()
    return {line.strip() for line in res.stdout.splitlines() if line.strip()}


def containers_report(state_dir: str | Path,
                      runner: CommandRunner | None = None) -> dict[str, Any]:
    """Resolve registry entries and labeled-but-unregistered engine objects
    to verdicts (042 D5). Read-only: never removes anything.

    Registry entries become ``live`` (object present + owner pid alive with
    start-time match), ``orphaned`` (object present + owner pid dead), or
    ``stale-entry`` (entry present, object gone). Labeled objects with no
    registry entry become ``unregistered`` - ownership is unproven, so they
    are surfaced, never auto-reaped (D6)."""
    runner = runner or _default_runner
    entries = _read_registry(Path(state_dir))
    container_names = _labeled_names(runner, "container")
    network_names = _labeled_names(runner, "network")
    registry_names = {e.get("name") for e in entries
                      if isinstance(e.get("name"), str)}
    containers: list[dict[str, Any]] = []
    for entry in entries:
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        kind = entry.get("kind", "container")
        present = name in (network_names if kind == "network" else container_names)
        if not present:
            verdict = "stale-entry"
        else:
            pid = entry.get("pid")
            if isinstance(pid, int) and _pid_alive_matches(
                    pid, entry.get("process_start")):
                verdict = "live"
            else:
                verdict = "orphaned"
        containers.append({
            "name": name,
            "run_id": entry.get("run_id"),
            "kind": kind,
            "pid": entry.get("pid") if isinstance(entry.get("pid"), int) else None,
            "process_start": entry.get("process_start"),
            "created_at": entry.get("created_at"),
            "verdict": verdict,
        })
    unregistered: list[dict[str, Any]] = []
    for name in sorted(container_names - registry_names):
        unregistered.append({"name": name, "kind": "container",
                             "verdict": "unregistered"})
    for name in sorted(network_names - registry_names):
        unregistered.append({"name": name, "kind": "network",
                             "verdict": "unregistered"})
    return {"containers": containers, "unregistered": unregistered}
