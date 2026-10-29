# Plan 042: Container reaping and inspection

> **Status: COMPLETED**

## Scope

Run-end container reaping reliability and a host-side inspection command for
engine sandbox containers. Touches: `src/agentic_dev_loop/engine/isolation.py`
(registry ordering, close verification), `src/agentic_dev_loop/engine/graph.py`
(startup close scope), `src/agentic_dev_loop/engine/cli.py` (SIGTERM handling,
`containers` subcommand), a new `src/agentic_dev_loop/engine/containers.py`,
tests, `docs/known-issues.md`, and a short README paragraph. Out of scope:
the egress proxy, the viz server, config schema (no new keys), step-level
timeouts, and any automatic reaping of containers whose ownership is not
proven (see D6).

## Context

known-issues #1 (2026-10-24): a human removed a live run's container pair
after a misdiagnosis (static CPU time read as a hang; the wrong state dir
searched). The corrected diagnosis rules end with rule 4: if ownership cannot
be proven, leave the container alone and surface it to the owner. The issue's
status line names the remaining work: "The engine does not reliably reap
containers when a run ends or is abandoned, and there was no safe verification
protocol for declaring a container an orphan. Candidate for a future plan:
engine-side cleanup on run end or step timeout, plus a `loop containers`
inspection command that resolves each container to its owning run and
live/dead engine."

What already exists (verified in code, so this plan only closes the gaps):

- Startup orphan reaper (030 D4, 038 D6): `reap_orphaned_containers`
  (`engine/isolation.py:1779`) runs before every run's startup
  (`startup_sandbox`, `isolation.py:1925`). Exact by construction: it touches
  only registry names matching `_CONTAINER_NAME_RE` / `_NETWORK_NAME_RE`
  (`isolation.py:99-101`) whose owning pid is dead, with start-time matching
  to defeat pid recycling (`_pid_alive_matches`, `isolation.py:1765-1776`).
  Never raises.
- Registration: `_register_container` / `_register_network` append
  `container-registry.jsonl` records (run_id, name, kind, pid, process_start,
  created_at) to the host-global isolation state dir (default
  `~/.agentic-dev-loop/state`, `core/config.py:211`). Best-effort.
- Clean close: `DockerBackend.close()` (`isolation.py:1492-1524`) stops and
  `rm -f` every owned container, proxy container, and the internal network,
  merges enforcement events, then `_prune_registry()` drops this run's
  entries. It runs in a `finally` around the pipeline (`graph.py:169-174`),
  so normal completion, pipeline exceptions, and Ctrl-C all close the
  sandbox. Containers run with `--rm` (`isolation.py:704-705`).
- Docker labels: every container and network already carries
  `agentic-dev-loop=true` and `agentic-dev-loop.run=<run_id>`
  (`isolation.py:720-721`, `787-788`, `1088-1089`), so engine-owned docker
  objects are enumerable read-only without touching anything else.

The gaps this plan closes:

1. **Hard kills leak containers.** No signal handling exists in the engine or
   CLI. SIGKILL and default-handled SIGTERM terminate the process without
   running the `finally`, so the run's containers stay up until the next
   run's startup reaper, or forever if no next run starts. Ctrl-C
   (KeyboardInterrupt) is already covered; SIGKILL is unhandleable by nature.
2. **Partial startup leaks containers.** `startup_sandbox` creates the exec
   container (`isolation.py:1968`) before provisioning setup hooks
   (`isolation.py:1972-1975`). A provision failure raises without ever
   returning the backend, and `graph.py`'s try/finally only wraps
   `_run_pipeline` (`graph.py:169-174`), so `close()` never runs.
3. **Unregistered-orphan windows.** Registration happens after a successful
   create (`isolation.py:1056-1061`, `1120-1125`, `1300-1307`, `1337-1356`),
   and `close()` prunes registry entries even when `rm -f` failed
   (`isolation.py:1524`). A container can therefore exist with no registry
   entry, and the exact-by-construction reaper will never see it.
4. **No verification protocol for humans.** The incident's root was humans
   reasoning from wrong signals. There is no command that resolves each
   engine container to its owning run and live/dead engine.

The issue's "step timeout" phrase needs no change: a step timeout kills the
in-container command only (`engine/proc.py:45-48`, killpg); the container is
pooled per run by design.

## Decisions

- **D1** Register before create. Every container and network create site
  appends its registry record *before* the `docker run` / `docker network
  create` command, so any engine object that exists is registered (barring a
  kill between the append and the create, which creates nothing to leak: the
  stale entry is reaped as a no-op `rm -f` and pruned later). Stale entries
  stay tolerated; registration stays best-effort and never fails the run.
  **APPROVED (2026-10-29).**
- **D2** Verify before pruning. `close()` verifies each owned name after
  `rm -f` / `network rm` (via `docker ps -a --filter name=<name>` /
  `docker network inspect`): entries whose object is gone are pruned; entries
  whose object survives (daemon down, rm failure) are kept so the next
  startup reaper retries. Replaces the blanket `_prune_registry()`. Stays
  best-effort, idempotent, never raises (039 D6's teardown posture is kept).
  **APPROVED (2026-10-29).**
- **D3** Close-protected startup. `graph.run_pipeline` moves
  `startup_sandbox` inside the `try` (sandbox sentinel None), so any
  post-create startup failure still runs `close()`.
  **APPROVED (2026-10-29).**
- **D4** SIGTERM graceful shutdown. The CLI run path installs a handler: the
  first SIGTERM raises `SystemExit(143)` so the graph `finally` runs
  `close()`; a second SIGTERM while already terminating calls
  `os._exit(143)`. SIGHUP untouched. SIGKILL stays unhandleable and is
  documented as such: the startup reaper, `--rm`, and `containers --reap`
  are its safety net. **APPROVED (2026-10-29).**
- **D5** New `containers` subcommand: `agentic-dev-loop containers`
  (no pipeline argument) with `--reap`, `--json`, and `--state-dir`
  (default `~/.agentic-dev-loop/state`). The default list is read-only and
  resolves each engine object to its registry record, owner pid, pid liveness
  (start-time matched), and a verdict: `live`, `orphaned`, `stale-entry`
  (entry present, object gone), or `unregistered` (labeled docker object with
  no registry entry; ownership unproven, surfaced per diagnosis rule 4, never
  auto-reaped). `--reap` runs the existing registry-only pid-gated reaper and
  prints the names it removed. **APPROVED (2026-10-29).**
- **D6** No automatic reaping of unregistered containers. An automatic
  docker-ps sweep could kill another live run's container in the narrow
  create-then-register window, which is the exact incident class this plan
  exists to prevent. The labeled sweep is list-only; `--reap` never touches
  unregistered names. **APPROVED (2026-10-29).**

## Changes

**Change 1 (registry ordering, isolation.py).** Move every
`_register_container` / `_register_network` call to before its create
command, at all five sites: the legacy exec branch (`isolation.py:1056-1062`),
`_create_base_export_container` (`1120-1126`), the pooled profile start
(`1300-1307`), `_start_proxy_container` (`1337-1356`), and
`_ensure_run_network` (`1086-1096`). Update the registry docstrings: entries
are append-before-create; the reaper tolerates stale entries (a dead owner
whose create never happened gets a no-op `rm -f` and a pruned entry).

**Change 2 (close verification, isolation.py).** Replace the blanket
`_prune_registry()` call in `close()` (`isolation.py:1524`, definition at
`1538-1545`) with per-owned-name verification: for each container/network
this run owns, ask docker whether the name still exists; prune the entry
only when docker says it is gone. When docker itself is unreachable, keep
the entry (the reaper retries later; `rm -f` of a missing name is a no-op).
Keep the idempotent, never-raise posture of `close()`.

**Change 3 (startup close scope, graph.py).** In `run_pipeline`
(`graph.py:163-174`), initialize `sandbox = None` before the `try`, call
`startup_sandbox` inside it, and close only when the sentinel was replaced.
A provision-hook failure now reaps the exec container it created.

**Change 4 (SIGTERM, cli.py).** In `main` (`engine/cli.py:89-123`), on the
run path only (after the `--dry-run` / `--clean-stamps` branches, before the
deferred `graph` import), install the D4 handler and restore prior handlers
in a `finally`. The handler raises `SystemExit(143)` on the first SIGTERM and
`os._exit(143)` on a second, and a small module-level seam (the handler
callable plus an install/restore pair) keeps it unit-testable.

**Change 5 (containers command, new engine/containers.py + cli.py).** New
module `engine/containers.py` with `containers_report(state_dir, runner)`
returning a dict: registry entries annotated with pid liveness (reusing
`_pid_alive_matches`, `isolation.py:1765`) and presence verdicts; labeled
sweeps via `docker ps -a --filter label=agentic-dev-loop=true` and
`docker network ls --filter label=agentic-dev-loop=true`, with names not in
the registry listed as `unregistered` (ownership unproven). Extend
`reap_orphaned_containers` (`isolation.py:1779`) to optionally return the
list of names it reaped (backward compatible: callers today ignore the
return). In `cli.py`, intercept a first argv token `containers` before the
pipeline probe (`cli.py:90-102`), and add a subparser with `--reap`,
`--json`, and `--state-dir` (default `~/.agentic-dev-loop/state`, matching
`IsolationConfig.state_dir`, `core/config.py:211`). The subcommand loads no
pipeline, extensions, or SDK imports; exit code 0, verdicts are data.

**Change 6 (tests).** New `tests/test_containers.py` (fake-runner unit tests
for the report: verdicts per registry state, unregistered sweep, `--json`
shape, reap return value). Extend `tests/test_isolation.py`: entry exists
when create fails (D1), close keeps entries on rm failure and prunes on
verified removal (D2), reaper return value (D5). Extend
`tests/test_isolation_integration.py` where the existing reaper tests live
(`366-385`). Extend `tests/test_cli.py`: `containers --help` without a
pipeline, pipeline invocations unchanged, SIGTERM raises SystemExit(143)
and close runs (D4, via the handler seam).

**Change 7 (docs/known-issues.md).** Flip issue #1 Status to
"Closed by plan 042 (2026-10-29)" with a pointer to this plan, and update
the diagnosis rules to name `agentic-dev-loop containers` as the
verification protocol (rule 4 stays: unproven ownership is surfaced, never
removed).

**Change 8 (README.md).** One short paragraph under Usage documenting the
`containers` subcommand and the SIGTERM/SIGKILL reaping guarantees.

## Acceptance checks

1. For every create site, the registry entry exists even when the create
   command fails (fake runner returning nonzero): proves append-before-create.
2. `close()` after a failed `rm -f` keeps the registry entry; after verified
   removal it prunes it; a second `close()` is a no-op (BR-8.3).
3. A startup failure after container create (failing provision hook) reaches
   `close()` and reaps the created container.
4. SIGTERM during the run path raises `SystemExit(143)` and `close()` ran;
   a second SIGTERM takes the hard-exit path (unit-tested via the handler
   seam).
5. `containers --json` lists registry entries with verdicts (`live`,
   `orphaned`, `stale-entry`) and the labeled-unregistered sweep with an
   unproven-ownership verdict; output is valid JSON with stable keys.
6. `containers --reap` removes only dead-owner registry names and reports
   them; live-owner names and unregistered names are untouched.
7. `agentic-dev-loop containers --help` exits 0 with no pipeline argument;
   `agentic-dev-loop --help` and all existing pipeline invocations are
   unchanged.
8. known-issues.md #1 Status is flipped with a pointer to 042; the repo
   ASCII test (`tests/test_repo_ascii.py`) and the pre-commit em-dash hook
   pass.
9. Full suite green: `.venv/bin/python -m pytest tests/ -q -m "not docker"`
   (1168+ tests at baseline) plus docker-marked integration tests where the
   host allows.

## Verification

TDD red-first: changes 1-5 get their tests (acceptance checks 1-7) written
and confirmed failing before implementation, then green, then refactor.
Changes 7-8 are doc edits verified by acceptance check 8.

QA stage contract (dogfood loop): run
`.venv-sandbox/bin/python -m pytest tests/test_containers.py
tests/test_isolation.py tests/test_isolation_integration.py
tests/test_cli.py -m "not docker" -q -p no:cacheprovider` and report the
observed pass/skip counts and any failure text. For each decision D1-D6,
cite static file:line evidence from the changed files (a gate-record citation
is a FAIL). Change 7 is verified by a grep of the flipped Status line. The
only legitimate UNVERIFIABLE rows are the docker-daemon integration checks,
which the in-sandbox pytest run deselects with `-m "not docker"`.

## Follow-ups

- A pid-carrying label (`agentic-dev-loop.pid` + process start) on containers
  would make ownership of unregistered names provable, which could upgrade
  the read-only sweep into a safe automatic reap. Deferred: D6 keeps the
  sweep advisory until then.
- Consumer-side adoption of the `containers` command in operational runbooks
  (same shape as 031's consumer-side adoption note).

## Open questions

- None blocking. `--state-dir` defaults to `~/.agentic-dev-loop/state`; a
  pipeline with a custom `isolation.state_dir` must pass the flag, which the
  command's help text states.
