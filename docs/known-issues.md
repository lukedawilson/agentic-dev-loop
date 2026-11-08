# Known issues

## Unregistered containers

Containers that predate the engine's container registry carry no registry
record, so `agentic-dev-loop containers` classifies them `unregistered`.
Ownership is unproven, so `containers --reap` never removes them
automatically: they surface in the report and are removed only after
ownership is proven from the audit log.

## Verifying container ownership

Before removing any loop container:

1. Use `agentic-dev-loop containers --json` to resolve each engine container
   to its registry record, owner pid, and a `live`/`orphaned`/`stale-entry`
   verdict. Do not reason from `ps` output or static CPU time: I/O-bound
   steps (restore, test) burn almost no CPU while healthy, so a step that
   looks hung is usually just waiting on network or disk.
2. Run state lives under `~/.agentic-dev-loop/runs/<pipeline-hash>/` (or the
   pipeline's explicit `state_dir`), not inside the workspace.
3. If ownership cannot be proven, leave the container alone and surface it
   to the owner instead of removing it.

---

A run started over a dirty worktree skips its auto-commit by design (the
human commits); restarting over a killed run's uncommitted changes is
expected to skip the commit step.
