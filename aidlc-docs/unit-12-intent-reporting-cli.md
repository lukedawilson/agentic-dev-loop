# Unit 12 Intent: Reporting CLI (run-history metrics + opencode wrapper + markdown report)

- **Status**: INTENT (drafted 2026-11-08, brainstorming session); all design sections approved by user 2026-11-08
- **Source**: brainstorming session 2026-11-08 on run-history analytics; data inventory of ~/.agentic-dev-loop (27 historical runs)
- **Directive** (user, 2026-11-08, verbatim): "Start an intent for an aidlc unit on reporting.

The run history provides valuable data that could be used to help understand the performace of the dev loop, for example:

- Is TDD effective?
- Is code review effective?
- Is QA effective?
etc.

Let's do some brainstorming and then feed into the unit intent"
- **Follow-up directives** (verbatim):
  - "Actually, two units: - firs tunit: cli, opencode wrapper command, markdown report - second unit: viz extension" (reporting is split into unit-12, this doc, and unit-13, the viz extension: aidlc-docs/unit-13-intent-viz-reporting.md)
  - "Needs to be flexible enough to handle different test output formats (e.g. JUnit, NUnit, XTest, JSTest, whatever)"
  - "Need some filtering for the aggregate run too" (filter set answered: pipeline, date, status, workspace/repo)

## Background: what run history contains

Per run (~/.agentic-dev-loop/runs/<run_id>/, 27 runs today): attempt-brief.md,
implementation-summary.md, loop-checkpoints.sqlite (universal - present in every
run), review-findings.md and qa-report.md (only runs that reached those steps),
last-error.md (crashed runs), exports/ (workspace snapshot - huge, never to be
scanned by reporting). Global (~/.agentic-dev-loop/state/): sandbox-audit.jsonl
(per-execution timings and exit codes, rotated at 5MB, so only recent runs are
covered), egress-events jsonl, setup stamps.

The checkpoint store is the only universal source: a sample run holds 334
checkpoints and 1544 per-task writes; channels include subject, docs, artifacts,
attempt, step_verdicts, branch routing, and notes.

Key inventory finding: no token or cost usage data is captured anywhere in the
engine or the run history today.

## Decisions (from the brainstorm, user-approved)

- D1 Capability standing: permanent, shipped in the library (like viz).
- D2 Split: two units. Unit-12 = CLI + opencode wrapper command + markdown
  report (this doc). Unit-13 = viz extension (separate intent doc).
- D3 Metric scope: broad operational dashboard - the three effectiveness
  questions plus a loop-health baseline. Cost/token metrics OUT of scope: no
  usage data is captured anywhere; capture instrumentation is a separate
  future unit.
- D4 Backfill: reporting works over the existing 27 runs and all future runs.
  Missing or legacy data degrades gracefully and the report states what is
  unavailable.
- D5 Engine: mechanical-first. Ground truth = checkpoint store +
  sandbox-audit JSONL. Prose is parsed only for prose-only facts (verdicts,
  findings, self-claims). Every metric carries a provenance label:
  [mechanical], [parsed], or [claimed]. A claim is never presented as a fact.
- D6 Report scope: aggregate dashboard (no argument) + per-run deep dive
  (report <run-id>). One engine, two renderings.
- D7 Test output formats: TDD detection uses exit codes and command patterns,
  never test output text. Test-run commands are recognized via declarable
  patterns riding the existing recipe system (python.yaml / dotnet-node.yaml).
  JUnit/NUnit/xUnit/Jest all work because the output is never parsed. A
  pluggable per-format parser registry is a deferred seam, out of v1 scope.
- D8 Aggregate filters: --pipeline, --since/--until (date), --status
  (pass/fail/crash), --workspace/--repo, combinable. The workspace source is
  an FD item: no canonical per-run metadata exists today (the path appears in
  checkpoint writes, 175 matches in one sample run, but is not a top-level
  field); a small engine addition (a run-metadata.json written at run start)
  is the candidate for future runs, with checkpoint-derived fallback for
  legacy runs.
- D9 AI-DLC staging: lean per unit - Functional Design (with the EXTSRF probe)
  -> Code Generation (user-driven, TDD) -> Build and Test. NFR Requirements,
  NFR Design, and Infrastructure Design are N/A.

## Metrics catalog (v1)

### TDD effectiveness

- test-run detection: a command counts as a test run when it matches a
  recipe-declared test pattern
- red-then-green: at least one test run with non-zero exit before the final
  green test run [mechanical]
- % of runs with red-then-green; % with zero test runs at all (mechanical
  no-execution detection) [mechanical]
- test-run count per dev pass [mechanical]
- claim cross-check: the dev summary's self-reported "red before, green after"
  vs the mechanical signal; the agreement rate is itself reported
  [claimed vs mechanical]

### Code review effectiveness

- defect yield: runs where review reported at least one finding [parsed]
- review-missed rate: review PASS but QA subsequently FAIL [mechanical]
- findings-fixed rate: findings followed by a dev pass-2 and re-review
  [mechanical, from the checkpoint step sequence]
- rubber-stamp rate: review PASS with zero findings and zero terminal actions
  executed [mechanical]

### QA effectiveness

- catch rate: QA FAIL after review PASS (what QA uniquely caught) [mechanical]
- environmental-verdict rate (the planning-docs/031 pattern) [parsed]
- QA pass rate overall [parsed/mechanical]

### Loop-health baseline

- run count, success rate, crash rate (last-error.md runs) [mechanical]
- per-stage attempt counts and retries [mechanical]
- step durations [mechanical, from sandbox-audit; labeled unavailable when
  rotation lost them]
- verdict distribution; brief coverage retries [mechanical]

## Architecture

New module src/agentic_dev_loop/reporting/:

- sources.py: reads the run-state dir and normalizes each run into a "run
  record" (checkpoint sqlite decode, sandbox-audit, prose artifacts). Never
  scans exports/. Handles -wal/-shm files. Reporting is strictly read-only:
  it never writes into run dirs or the state dir.
- metrics.py: pure functions, run record -> provenance-labeled metrics dict.
- render.py: metrics -> markdown (aggregate dashboard; per-run deep dive).
- CLI: a report subcommand next to run-status. report = aggregate (honors the
  D8 filters), report <run-id> = deep dive.
- Opencode wrapper: .opencode/commands/loop-report.md (repo-local), shells out
  and renders the markdown, patterned on /run-status. The library-shipped
  surface is the CLI.

## Extension applicability

- External Surfaces Validation: YES. The LangGraph checkpoint blob format is
  an external surface. Functional Design probes real checkpoints from real
  runs (e.g. 6cb529022644): which writes channels and task values carry
  command results, exit statuses, and timings; a validated-model table is
  recorded before any architecture commitment.
- Property-Based Testing (partial): provenance invariants, metric dict
  round-trips, filter combinations.
- Security Baseline: mostly N/A (read-only local analysis; no new network
  surface in this unit).
- Resiliency Baseline (scoped, directional): graceful degradation on missing
  or legacy data is exactly the D4 requirement.
- Domain Standards: applies at Functional Design (run record, metric,
  provenance, and filter types - descent audit).

## Validation approach

- Fixtures built from real system dumps (actual run dirs), never invented
  (the 2026-11-05 postmortem rule).
- TDD red-green-refactor; pure-function tests on metrics.py; PBT per above.
- The [claimed] cross-check keeps self-reporting honest without trusting it
  (the unit-11 principle applied to reporting).

## Scope estimate

- Request type: enhancement (new capability).
- Scope: new reporting module + CLI subcommand + opencode wrapper command. No
  changes to run/execution paths (a read-only consumer).
- Complexity: moderate. The checkpoint decode is the risky part (external
  format); the metrics themselves are pure functions.

## Out of scope (recorded so they are not rediscovered)

- Token/cost capture and reporting (no data exists today; separate future
  instrumentation unit).
- Per-format test output parsing (parser registry seam, deferred).
- Any write into run dirs or the state dir (reporting is strictly read-only).
- Unit-13 viz work (separate unit, next in order).

## Workflow plan (unit-12)

| Stage | Artifact | Gate |
| --- | --- | --- |
| Functional Design | construction/plans/unit-12-functional-design-plan.md + construction/unit-12/functional-design/ | REVIEW REQUIRED; EXTSRF-01 probe of the checkpoint format recorded as the validated-model table; D8 workspace source resolved |
| Code Generation | construction/plans/unit-12-code-generation-plan.md + construction/unit-12/code/ | Plan approval before edits; user-driven; TDD red-green-refactor |
| Build and Test | construction/unit-12/build-and-test/ | Suite green; report command exercised against the real 27-run history |

## Open questions (answers recorded as given)

- Q1 Capability standing -> shipped in the library (user, 2026-11-08)
- Q2 Form -> both CLI and viz; split into two units by user directive
  (unit-12 CLI first, unit-13 viz second)
- Q3 Metric scope -> broad operational dashboard; cost/token out of scope
  (no capture exists)
- Q4 Backfill -> all runs including legacy, graceful degradation
- Q5 Engine -> A: mechanical-first with provenance labels
- Q6 Report scope -> aggregate + per-run deep dive
- Q7 Staging -> lean: Functional Design then user-driven code, per unit
- Q8 Test formats -> exit-code based, recipe-declared patterns, no output
  parsing
- Q9 Aggregate filters -> pipeline, date, status, workspace/repo
