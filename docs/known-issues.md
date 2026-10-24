# Known Issues

## 1. Loop containers removed while their run was live (2026-10-24)

**Symptom:** A docker container pair (`<run-hash>-exec`, `<run-hash>-exec-net`)
that belongs to a live loop run is mistaken for an orphan and removed. The
owning run then fails every step with
`Error response from daemon: No such container: <hash>-exec-net` and has to be
killed and restarted.

**Observed case:** The yosk-cms plan 002 run's containers (`0521b47c...`) were
removed after a misdiagnosis. Two wrong signals were treated as proof of
orphaning:

1. Static total CPU time (8 CPU-seconds over about 3 hours) was read as a
   hung process. It is not: restore/test steps spend most wall time waiting on
   network and I/O, burning almost no CPU. A healthy step looks identical.
2. No run dir under `~/.agentic-dev-loop/runs/<hash>/` was read as "owning run
   gone". Run state is workspace-local; the wrong location was searched. The
   container was live: a `dotnet test` step for the yosk-cms repo was attached
   to it.

The run was killed by its owner and restarted fresh. The `a6a80b5b` pair was
also removed under the same criteria; its orphaning was likewise not proven.

**Diagnosis rules (corrected):** Before removing any loop container pair:

1. Find the owning run's state dir in the run's OWN workspace
   (`.agentic-dev-loop/runs/` under the repo the run belongs to), not the
   agent-engine home path.
2. Check whether the engine process for that run is alive.
3. Treat static CPU time as normal for I/O-bound steps. It is not a hang
   signal.
4. If ownership cannot be proven, leave the container alone and surface it to
   the owner instead of removing it.

**Resolution for this incident:** The run was killed by the owner and started
fresh. A fresh start is sufficient: the engine creates new containers per run.
Note: a run started over a dirty worktree skips its auto-commit by design (the
human commits; see `actions.py`), which is what happens when restarting over a
killed run's uncommitted changes.

**Status:** Open. The engine does not reliably reap containers when a run ends
or is abandoned, and there was no safe verification protocol for declaring a
container an orphan. Candidate for a future plan: engine-side cleanup on run
end or step timeout, plus a `loop containers` inspection command that resolves
each container to its owning run and live/dead engine.
