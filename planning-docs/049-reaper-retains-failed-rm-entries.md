# Plan 049: Startup reaper retains registry entries when removal fails

> **Status: PROPOSED**

## Scope

This repository (the engine) only. No pipeline.yaml surface, no prompt
change, no consumer action beyond picking up the new wheel (D6). The fix
is confined to the startup reaper in isolation.py, the shared
"object gone" confirmation helpers, and their unit tests.

## Context

### The incident

Four engine exec containers from a consumer-repo run (`7a47341e...`,
dev-dotnet image) survived on the host for 3 to 4 days after their run
ended. Their run's last activity in the sandbox audit is Oct 28
04:39 to 06:20 UTC, a day before plan 042 (container reaping) landed, so
the run died in the era where known-issues.md #2 was still open ("no
reliable reaping when a run ends or is abandoned"). That part is
expected: they predate the fix.

What is not expected is that the current reaper could never have touched
them. They carried no registry records: the registry file held only the
entries of the live run, and `containers_report` therefore classifies
them `unregistered`, which plan 042 D6 deliberately never auto-reaps
(ownership unproven). The four were idle (~0 percent CPU, one holding
1.77 GiB of the 11.67 GiB Docker VM) and were removed manually on
2026-10-31 by operator instruction, after ownership was proven from the
audit (no live engine process owns them; the only live process owns the
current run).

### Root cause (source-verified, isolation.py)

The startup reaper `reap_orphaned_containers` (isolation.py:1840-1885)
ignores the exit code of its removal commands. For a container:

    runner.run(["docker", "rm", "-f", name], timeout=60)
    reaped.append(name)
    continue

and the identical pattern for `docker network rm`. The entry is then
dropped from the registry rewrite unconditionally. If `docker rm -f`
fails (daemon busy, transient error), the object survives but its
registry record is gone, so no future startup reaper can ever see it
again. An invisible, permanent orphan. The exit codes were never logged,
so the exact loss path for the four `7a47341e` records cannot be
reconstructed with certainty, but this branch is the only code path that
drops a registry record without daemon confirmation.

The close path has the correct contract. `_verify_and_prune_registry`
(isolation.py:1564-1586) prunes an entry only after the daemon confirms
the object is gone via `_container_gone` (1589-1597: `docker ps -a
--filter name=... --format {{.Names}}`, true only on exit 0 and absence)
or `_network_gone` (1599-1605: `docker network inspect`, gone iff "No
such network"). Its docstring states the policy: "A surviving object
(daemon down, rm failure) keeps its entry so the next startup reaper
retries."

So the gap is asymmetric: close prunes on daemon-confirmed absence, the
startup reaper prunes on attempt. This plan makes the reaper adopt the
close path's confirmation semantics.

### Preserved behavior (must not change)

- Missing objects stay tolerated: a "no such container" rm still gets a
  pruned entry (`test_missing_container_tolerated`).
- Live-owner, malformed-name, empty-registry, and concurrent-run entries
  are left alone (pid-gated, regex-gated, read-only on empty).
- Unregistered (unrecorded) engine objects are still never auto-reaped
  (042 D6 stands).
- `containers --reap` still returns `{"reaped": [...]}`; `containers`
  (no flag) still shows retained entries as `orphaned`.

## Decisions

| # | Decision | Proposal | Answer |
|---|---|---|---|
| D1 | Daemon-confirmed pruning in the reaper | After each `docker rm -f` / `docker network rm` attempt, confirm absence with the same helpers the close path uses. Prune the entry only when the object is confirmed gone; otherwise keep the entry in `remaining` so the next startup reaper retries, and do not list the name as reaped. | PROPOSED |
| D2 | One "object gone" helper pair | Extract module-level `_container_gone(runner, name)` and `_network_gone(runner, name)` from the `DockerBackend` methods (which delegate to them). Single source of truth shared by `_verify_and_prune_registry` and the reaper; no duplicated logic. | PROPOSED |
| D3 | Accurate reporting | `reaped` (and its `_announce`) contains only confirmed removals. Retained entries stay in the registry and surface in the `containers` report as `orphaned`. The CLI `--reap` JSON shape is unchanged. | PROPOSED |
| D4 | TDD tests | Unit (FakeRunner seam): rm fails and object still present = entry retained, name not returned; rm fails and object absent = entry pruned (existing tolerated-missing contract); rm succeeds = entry pruned, name returned; network variants of both failure cases. Update the exact-calls assertions in the existing reaper tests (they now see a second confirm call). Close-path tests stay green unchanged, proving the D2 refactor is neutral. | PROPOSED |
| D5 | No config surface | Always-on fix, no pipeline.yaml keys (mirrors 048 D5). | PROPOSED |
| D6 | Rollout | Engine change lands through the loop itself from the published wheel (the dogfood pattern of 030/048). Consumer repos need no pipeline or prompt change; a pipx reinstall picks up the new wheel. | PROPOSED |

## Out of scope

- Reaping unregistered engine objects: 042 D6 stands; ownership unproven
  means surfaced, never auto-reaped.
- Retroactive cleanup of pre-042 orphans: the known four were removed
  manually on 2026-10-31; nothing in this plan changes how old,
  record-less containers are handled.
- Reconstructing the exact record-loss history for `7a47341e` (exit
  codes were never recorded; the audit has no reap events).
- The exited `yosk-cms-minio-init-1` compose container (not
  engine-managed).
- An audit event for reap decisions: deferred to Follow-ups.

## Changes (file by file)

### `src/agentic_dev_loop/engine/isolation.py`

- New module-level `_container_gone(runner, name)` and
  `_network_gone(runner, name)` carrying the current logic; the
  `DockerBackend._container_gone` / `_network_gone` methods become thin
  delegations (call sites unchanged).
- `reap_orphaned_containers`: after each rm attempt, call the matching
  confirm helper. Confirmed gone: append to `reaped`, prune the entry,
  announce as today. Not confirmed gone: keep the entry in `remaining`,
  no reaped entry, no reaped announce (optionally a distinct retained
  announce line).
- Update the function docstring: the "no-op rm and a pruned entry"
  contract now reads "a surviving object keeps its entry so the next
  startup reaper retries", matching the close path.

### `tests/test_isolation.py`

- `TestReaper` additions: rm-fails-object-survives retains the entry and
  returns nothing for it; network variant; rm-fails-object-absent prunes
  (tolerated-missing path now scripted through the confirm call).
- Update exact-calls assertions in `test_dead_pid_reaped_and_pruned`,
  `test_dead_network_reaped_and_pruned`, and
  `test_missing_container_tolerated` to include the confirm call
  (`docker ps -a --filter name=... --format {{.Names}}` /
  `docker network inspect`).
- `test_close_prunes_own_registry_entries` unchanged: it proves the D2
  refactor keeps close semantics identical.

### `tests/test_containers.py`

- `TestReapReturnValue.test_reap_returns_removed_names`: update the
  exact-calls assertion for the added confirm call. Other report tests
  unchanged (the report is read-only and unregistered handling is
  untouched).

### `tests/test_isolation_integration.py`

- `TestReaperE2E` unchanged: against a real daemon the rm succeeds and
  the confirm reports absence, so the test stays green and now also
  exercises the new confirm path end to end.

### Docs

- `README.md` / `CHANGELOG.md`: reaper confirmation fix noted.
- `COMPAT-SHIMS.md`: no entry (no SDK reliance).
- `docs/known-issues.md`: append a short note that pre-042 survivors
  without registry records are `unregistered` and removed only after
  proven ownership (the 2026-10-31 manual cleanup as the worked
  example).

## Risks

- One extra docker call per orphan per run start: bounded, only when
  orphan entries exist, and it replaces nothing hot.
- Daemon flapping between rm and confirm: the entry is retained and the
  next startup retries; a stale entry whose object is really gone is
  pruned on the next attempt (self-healing).
- The reaper is "best-effort, never raises" today; the confirm helpers
  also never raise, so that contract is preserved.
- Test churn is mechanical (exact-calls assertions gain one call each);
  the FakeRunner returns `ok("")` for unscripted calls, so tests that
  script only the rm result still behave predictably.

## Acceptance checks (sandbox-executable)

1. Unit: rm exits nonzero and the object still exists; entry retained,
   name not in the returned list, registry still holds the entry.
2. Unit: rm exits nonzero and the object is absent ("No such
   container" / "No such network"); entry pruned (tolerated-missing
   contract preserved).
3. Unit: rm exits 0; entry pruned and name returned (current behavior).
4. Unit: live-owner, malformed-name, empty-registry, and
   concurrent-run-entry cases unchanged.
5. Integration (docker-marked): `TestReaperE2E` green on a real daemon.
6. Full suite green; ASCII only, no em dash (repo rules).

## Verification

TDD per repo rule: red tests first for D1/D3 (rm-fails-object-survives
cases, both kinds), then green, then refactor into the D2 helper pair
with the close-path test as the neutral guard, then re-run the full
suite. The docker-marked integration test runs where docker is
available; CI/non-docker skips it as today.

## Follow-ups

- A reap-decision audit event (name, kind, verdict reaped/retained, rm
  exit code) so the next orphan's history is diagnosable from the audit
  alone, mirroring 048's proxy-lifecycle follow-up.
- Consider surfacing retained entries in `containers --reap` output
  (for example a `retained` list) once the audit event exists.
- Revisit 042 D6 if unregistered orphans accumulate again: ownership
  proof from the audit is manual work today.
