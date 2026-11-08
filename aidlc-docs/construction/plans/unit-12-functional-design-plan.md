# Functional Design Plan - Unit 12 (Run-History Reporting CLI)

Unit intent: `aidlc-docs/unit-12-intent-reporting-cli.md` (approved 2026-11-08; D1-D9 + metrics catalog are the requirements artifact).
Normative ground: live engine reads (run_status.py, graph.py, stages.py, isolation.py audit writer, config.py state_dir derivation) plus a real-data probe of the run state (2026-11-08, see validated-model.md).

## Requirements mapping (trace source: unit-12 intent doc)

| ID | Requirement |
| --- | --- |
| FR-12.1 | D1/D2: shipped in the library; unit-12 = CLI + opencode wrapper + markdown report (unit-13 = viz extension) |
| FR-12.2 | D3: broad operational dashboard: TDD, review, QA, loop-health metrics; cost/token out of scope |
| FR-12.3 | D4: backfill all legacy runs with graceful degradation; unavailable data labeled, never guessed |
| FR-12.4 | D5: mechanical-first engine; checkpoint store + audit JSONL are ground truth; prose parsed only for prose-only facts; every metric carries [mechanical]/[parsed]/[claimed] provenance |
| FR-12.5 | D6: aggregate report (no arg) + per-run deep dive (`report <run-id>`) |
| FR-12.6 | D7: TDD detection from exit codes + recipe test-command patterns, never test output text |
| FR-12.7 | D8: filters --since/--until, --status (v1; amended 2026-11-08 by FD-Q1: --pipeline and --workspace/--repo dropped for now) |
| FR-12.8 | Read-only: the reporting module never writes run/state dirs |
| FR-12.9 | Output: markdown report rendered by the opencode wrapper (`/loop-report`) |

## Execution Checklist

- [x] Analyze unit context (intent doc D1-D9, metrics catalog, run_status.py reader precedent, graph.py LoopState, stages.py verdict rules, isolation.py audit writer, config.py default_state_dir)
- [x] Probe external surfaces (real checkpoint dbs incl. 6cb529022644, sandbox-audit jsonl + rotation, all 27 run dirs; see validated-model.md)
- [x] Generate `aidlc-docs/construction/unit-12/functional-design/validated-model.md` (EXTSRF validated-model table, unverified assumptions)
- [x] Generate `aidlc-docs/construction/unit-12/functional-design/domain-entities.md` (concept model, descent audit)
- [x] Generate `aidlc-docs/construction/unit-12/functional-design/business-rules.md` (classification, metrics, provenance, filters, degradation)
- [x] Generate `aidlc-docs/construction/unit-12/functional-design/business-logic-model.md` (pipeline: discover -> read -> normalize -> metrics -> filter -> render)
- [x] Resolve open design decisions FD-Q1..FD-Q3 (answers recorded below; FD-Q1 dropped workspace/pipeline filters, FD-Q2 stdout only, FD-Q3 gate + dev-lane)
- [ ] Present Functional Design completion message and await approval

## Open Design Decisions

### Question FD-Q1: workspace/pipeline attribution for a run
The checkpoint store has no `workspace` or `pipeline` channel. Probe facts: `doc_sources` holds absolute workspace paths (permanent); `tooling_paths` holds workspace-relative paths (permanent); the run dir name is the first 12 hex of sha256(resolved pipeline-file path); sandbox-audit rows carry `pipeline` (config `name:`) but rotate away at 5 MB.

A) Engine patch: write `run-metadata.json` (workspace, pipeline name, thread, started_at) into the run dir at run start for future runs; legacy runs derive workspace from `doc_sources` and pipeline name from a live read of the workspace's pipeline.yaml when it still exists, else unavailable (recommended: exact attribution for future runs, graceful for legacy)

B) No engine change: derive workspace from `doc_sources`; pipeline name from live read of the workspace pipeline.yaml, else unavailable

C) No engine change: workspace from `doc_sources`; pipeline name only from surviving audit rows (unavailable for most legacy runs)

[Answer]: Drop the workspace/pipeline filter for now (user decision 2026-11-08). Unit-12 v1 does NOT ship --workspace/--repo or --pipeline filters, no run-metadata.json engine patch, and no pipeline-name resolution. Amendment to intent D8: v1 filters = --since/--until and --status only; workspace/pipeline filters deferred. Threads are identified in the report by subject, timestamps, and status.

### Question FD-Q2: report output destination
A) stdout only: `agentic-dev-loop report` prints the markdown to stdout; the opencode wrapper renders it in chat (recommended: smallest surface, no file handling, no accidental writes near run state)

B) stdout + `--out <path>`: also write the markdown to an optional user-chosen file

[Answer]: A: stdout only. `agentic-dev-loop report` prints the markdown to stdout; the opencode wrapper renders it in chat. No --out flag, no file writes.

### Question FD-Q3: TDD red-then-green signal source
Two mechanical sources exist. (1) Permanent: the pipeline test step's verdict sequence per attempt (`step_verdicts`; command-step exit code is the verdict, 0 => pass). (2) Rotating: sandbox-audit exit codes for every command executed inside dev/other steps (rotates at 5 MB; old runs lose it).

A) Gate verdicts as the primary TDD signal (red = test step fail, green = test step pass, per attempt, reconstructed from the write log), plus dev-lane audit-derived metrics (test-run counts, red-green within the dev lane) only when audit rows survive, labeled unavailable otherwise (recommended: matches D4 graceful degradation)

[Answer]: A: gate + dev-lane when available. Red-then-green = pipeline test step verdict sequence per attempt (permanent, mechanical). Dev-lane test-run counts and red-green inside the dev step come from audit exit codes, shown only when audit rows survive, labeled unavailable otherwise.
