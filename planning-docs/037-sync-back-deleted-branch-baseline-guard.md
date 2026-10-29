# 037-sync-back-deleted-branch-baseline-guard.md

> **Status: COMPLETED**

## Scope

Engine: a baseline guard in the sync_back "deleted" change branch so
that files added to the real repo after export materialization are
never unlinked from the real working tree. One branch in one function
plus tests.

## Context

The 039 deletion incident, Oct 29: dogfood run 6cb529022644
(implementing 036) was live while
planning-docs/039-step-custom-prompt.md was recreated and committed
host-side (a854fc7). The run's export had been materialized from a
working tree without 039. Every sync_back after the host commit
classified 039 as "deleted" and unlinked it from the real repo. Audit
evidence: deletions at 09:49:46, 09:54:37, 09:59:09, 10:04:29 UTC.
Had the run reached its commit action with the file deleted, `git add
-A` would have committed the deletion permanently.

Mechanism (sync_back.py lines 210-275): the diff compares export vs
real vs baseline for each declared path. Line 217-218:

    if exp is None and real is not None: changes[rel] = "deleted"

There is no baseline check in this branch. Line 270 unlinks the real
copy (`(real_repo / rel).unlink(missing_ok=True)`). The "modified"
branch (lines 221-227) has a baseline guard: when the real copy
diverged from the baseline, the change is a hard conflict and the host
wins (BR-4.4). The "deleted" branch has no such guard.

Design decision behind the behavior: faithful mirroring. The sandbox
is the agent's workspace of truth, so agent deletions propagate to the
real repo. The hole: "file added to the real repo after export
materialization" is indistinguishable from "file deleted by the agent
in the sandbox". Both present as exp=None, real=present. The tradeoff
was mirroring vs preserving host additions; the code chose mirroring.
This plan keeps mirroring for genuine agent deletions and closes the
host-addition hole.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | In the deleted branch, consult the baseline. A file absent from the baseline (base is None) and absent from the export but present in the real repo is a host-side addition, not an agent deletion: never unlink it | AGREED |
| D2 | Host-side additions are classified "host-added", recorded in the audit log with the path, and skipped. They are not surfaced as a sandbox error: the agent has never seen the file, so there is nothing for it to act on, and raising on every sync would spam the agent with an error it cannot resolve | AGREED |
| D3 | Genuine agent deletions still mirror: a file present in the baseline and deleted in the export is still unlinked from the real repo. The guard changes only the host-addition case | AGREED |
| D4 | No new persistence: reuse the existing baseline that the modified branch already compares against | AGREED |

## Changes

### 1. sync_back.py deleted branch (engine)

Before unlinking, look up the path in the baseline. If the path is
absent from the baseline, reclassify as "host-added": skip the unlink
and emit an audit-log entry naming the path.

### 2. Audit logging

The host-added event reuses the existing sync_back audit vocabulary
(the same log that records to_copy/to_delete syncs), so the skipped
path is discoverable in the sandbox audit log.

### 3. Tests

- A file present in the baseline and deleted in the export is still
  unlinked from the real repo (mirroring preserved).
- A file absent from the baseline, absent from the export, and present
  in the real repo is untouched and an audit event is emitted.
- Regression: the BR-4.4 modified-branch hard-conflict behavior is
  unchanged.

## Verification

- The three tests above.
- Full pytest suite green.
- ASCII only, no em dash, no consumer name (repo rules).

## Non-goals

- Changing mirroring semantics for genuine agent deletions.
- Changing the modified-branch conflict behavior.
- Changing export materialization or the baseline snapshot itself.
- Protecting untracked host files from the run's own commit action
  (a separate concern, if ever wanted).
