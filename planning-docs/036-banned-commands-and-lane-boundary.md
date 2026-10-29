# 036-banned-commands-and-lane-boundary.md

> **Status: COMPLETED**

Independent of 032-038; can land first or last.

## Scope

Engine (sandbox terminal executor, sync_back), review prompt copy, and
the consumer unit format (one new optional file per unit). Tooling-path
deliverables are called out.

## Context

Consumer run 5367f0c488d8: forbidden commands (git mutations, package
installs, browser downloads) are prompt-level rules today; violations
fail as shell errors and cost the agent investigation turns. The unit
lane allowlist exists in prose only: nothing enforces it, the dev agent
spent turns self-policing where the boundary is, and review could not
judge lane violations mechanically. The most recent review bounce found a
genuine defect whose clean fix lives in a file the engine lane is
forbidden to edit, forcing a workaround.

User decisions: mechanical banned-command enforcement agreed; the same
mechanical idea applied to the lane boundary agreed (machine-readable
allowlist, sync_back assertion first, mount-level prevention second).

Boundary statement: the restore-fail re-entry loop (22 dev re-entries for
an infra failure dev cannot fix) is deliberately OUT of scope here. The
deny-list makes forbidden attempts cheap but does not stop that loop; a
run-level circuit breaker is a separate plan.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | Banned-command deny-list enforced in `SandboxTerminalExecutor.__call__` before the sandbox request is built: match argv against the step's list, return a structured "[forbidden: <label>]" observation, no shell spawn | AGREED |
| D2 | Machine-readable lane allowlist: optional `aidlc-docs/construction/unit-NN/engine-allowlist.yaml` (allowed paths/globs; absent = unrestricted). sync_back asserts every merged path matches it (v1); read-only-mount prevention is v2, deferred | AGREED |
| D3 | Review prompt: injected allowlist artifact + rule that any diff outside the allowlist is a blocking lane violation | AGREED |

## Changes

### 1. Deny-list (engine)

- Engine defaults with labels: git mutations (commit, push, rebase,
  checkout, reset, tag), package installs (npm install, npm ci, pip
  install, uv add, pip-compile, dotnet add package), browser downloads
  (playwright, npx playwright), container operations (docker). `dotnet
  restore` and `dotnet add` are denied only while the step's network flag
  is off.
- Config: optional `forbidden_commands: [<regex>]` per step, merged with
  the defaults. No disable escape hatch: a consumer that genuinely needs
  one raises the question loudly instead.
- The check runs for any agent step; command steps are unaffected (they
  are declared infrastructure and already fail fast on exit code).

### 2. Lane boundary (engine + consumer unit format)

- v1 (required): the engine resolves `engine-allowlist.yaml` from the
  active unit directory (the aidlc extension already resolves unit docs);
  sync_back fails the step with a named cause in last-error.md when any
  merged/synced path falls outside it. This is the belt for all write
  paths, terminal and editor tools alike.
- v2 (deferred, sketched only): read-only export root with rw
  bind-mounts for the allowlisted paths (mount resolution already exists
  in mounts.py). Ship v1, evaluate v2 after one consumer run.
- Consumer unit format: create the allowlist file for the active unit
  (tooling path); the prose plan stays, the file is the machine truth.

### 3. Review prompt (tooling path)

Inject the allowlist into the review step context and add the rule: any
diff outside the allowlist is a blocking lane violation. Review then
judges lanes mechanically, and findings that require a forbidden file get
an explicit lane verdict instead of a silent workaround.

### 4. README.md documentation (tooling path)

Update the `pipeline.yaml` reference (README.md) to document
`forbidden_commands` (one YAML line, one table row, defaults list, no
escape hatch) and the unit allowlist file convention
(`engine-allowlist.yaml`, absent = unrestricted, v1 sync_back assertion).

## Tooling-path deliverables

The review prompt copy, the consumer unit's `engine-allowlist.yaml`, and
README.md. Human commits required.

## Verification

- TDD: executor tests with a fake sandbox asserting no spawn on a denied
  command and the structured observation shape; sync_back tests with a
  temp export tree (allowed path merges, violation fails the step with
  the named cause); config merge tests.
- Backward compatibility: no allowlist file = unrestricted; no
  `forbidden_commands` = defaults only; existing pipelines load unchanged.
- ASCII only; no consumer names in this repo.
- Post-deploy observation: forbidden commands fail instantly with the
  structured message; lane violations fail at sync_back instead of
  reaching review.
