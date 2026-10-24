# Known Issues

## 1. Orphaned loop containers after a run ends (2026-10-24)

**Symptom:** A loop run finishes (or is abandoned) but its docker container
pair (`<run-hash>-exec`, `<run-hash>-exec-net`) stays up with `sleep infinity`
and a step process still attached.

**Observed case:** The yosk-cms run container `0521b47c...` was left running
with a `dotnet test` step that started at 18:04 and was still "running" three
hours later with 8 CPU-seconds of total CPU time - hung, not working. There
was no matching run directory under `~/.agentic-dev-loop/runs/`, so the
owning run was already gone from state. The 24-hour `a6a80b5b` pair from the
agent-engine browser verification work also lingered after its work was done.

**Why it matters:** Orphaned containers hold CPU and memory and keep the
workspace bind-mounted; a hung step can look like a live run and confuse
debugging.

**Diagnosis:** `docker top <hash>-exec` shows a step process alive with static
CPU time, and `~/.agentic-dev-loop/runs/<hash>/` does not exist.

**Resolution:** `docker rm -f <hash>-exec <hash>-exec-net`. Safe: containers
are disposable and the workspace is a bind mount, not container storage.

**Status:** Open. The engine does not reliably reap containers when a run ends
or is abandoned. Candidate for a future plan: cleanup on run end, or a step
timeout that kills stuck processes.
