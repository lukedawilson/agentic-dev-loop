# Domain Entities - Unit 12 (Run-History Reporting CLI)

Concept model for the reporting domain. Every entity descends from real observed data (descent audit: each entity cites its source channel/file). No entity is invented for convenience.

## Entities

### RunStateDir (a workspace's accumulated run history)
A directory under the runs root (default `~/.agentic-dev-loop/runs/`), named by the first 12 hex of sha256(resolved pipeline path). Contains exactly one `loop-checkpoints.sqlite` (plus possible `-wal`/`-shm`) and optional prose artifacts.

- **Descent**: V1, V2, V15. Probed across all 27 real dirs.
- **Fields**: name (12-hex), path, has_wal/has_shm, prose artifacts present (attempt-brief.md, implementation-summary.md, review-findings.md, qa-report.md, last-error.md), thread list.
- **Constraint**: exports/ inside a run dir is NEVER enumerated (V15).

### Thread (one run of the loop)
All checkpoints and writes sharing one `thread_id` in one RunStateDir. One execution of the pipeline for one subject (fresh run or `--resume`).

- **Descent**: V2, V3, V4 (thread_id column; latest checkpoint per thread = cumulative channel_values).
- **Fields**: thread_id, run_dir name, ts_start/ts_end (first/last checkpoint blob ts), subject (channel), status (success/failed/interrupted/running), final channel_values (attempt, max_attempts, outcome, failed, failed_step, retry_target, step_verdicts, brief_hashes, brief_retries, doc_sources, tooling_paths, state_dir), attempt list, audit-joined rows (best effort).
- **Status values**: success = outcome=='success'; failed = failed==True; running = latest thread in dir with pending nodes; interrupted = anything else (superseded or abandoned; V16 classification, V20 observed cases).

### Attempt
One numbered pass through the pipeline stages within a Thread. Bounded by a step_verdicts reset (`{'brief': None}` retry write) or the thread start/end.

- **Descent**: V7 (verdict-reset cycles in the ordered write log); attempt counter = `attempt` channel (V5).
- **Fields**: thread_id, number, verdict sequence (ordered list of StepVerdict), retry_target (dev/brief), terminal step (success/qa fail/dev fail/...).

### StepVerdict
One (step, verdict) pair for one step in one attempt.

- **Descent**: V7, V8, V9 (step_verdicts channel snapshots at each step's task_path).
- **Fields**: step (brief/deps/dev/restore/test/review/qa/commit), verdict (pass/fail/env/unverifiable), provenance (mechanical for command steps and structured agent verdicts; parsed for legacy prose agent verdicts).
- **Command-step rule**: verdict = exit code (0 => pass) for deps/restore/test/commit (V8). Test step verdicts are therefore permanent mechanical facts.

### AuditRecord
One command-execution row from the GLOBAL `sandbox-audit.jsonl` (or its single rotated `.1`).

- **Descent**: V12, V13 (isolation.py writer; probed live rows).
- **Fields**: timestamp, pipeline, step, exit_code, timed_out, duration_seconds, container_id, request_digest. (Egress rows lack exit_code and are excluded, U4.)
- **Availability**: rotating store; rows older than the current file + one .1 are destroyed (V13). Metric availability is per-thread (rows join by timestamp overlap only, V14/U1).

### ProseArtifact
A markdown file in a run dir. Only readable as facts about the LATEST thread (V10).

- **Descent**: V10, V11.
- **Kinds**: attempt-brief.md (the brief), implementation-summary.md (dev self-report = self-claims), review-findings.md (review findings = defect list), qa-report.md (qa verdict + findings), last-error.md (hard-failure marker).
- **Parsed facts**: verdicts (VERDICT: keywords), review findings (defect yield), dev self-claims (for claim cross-check). Parse is tolerant; unparseable => unavailable (U2).

### RunRecord (normalized aggregate, unit of reporting)
The reporting module's input model for one Thread: identity + attempt list + audit-joined rows + parsed prose facts. Produced by sources.py, consumed by metrics.py. Contains NO raw checkpoints.

- **Descent**: composition of the above; every field traces to a validated source (V1-V20).

### Metric
One computed number or rate with provenance and basis.

- **Fields**: name, value, provenance ([mechanical] = exit codes, verdict channels, timestamps; [parsed] = tolerant prose extraction; [claimed] = dev self-report), basis (thread ids / attempt ids / step), availability (available / unavailable + reason e.g. 'audit rotated').
- **Rule**: rendered as-is; unavailable is rendered as 'unavailable (reason)', never fabricated (FR-12.3/D4).

### FilterSet
The v1 filters (D8 as amended by FD-Q1): --since/--until (datetimes, inclusive start, exclusive end, applied to thread ts_start), --status (any of success/failed/interrupted/running). Combinable; empty FilterSet = everything.

### Report
Rendered markdown (ASCII only, no em dash): either the aggregate report (no argument; per-thread rows + section aggregates with denominators) or the deep dive (arg = 12-hex run dir name; dir header, per-thread attempt verdict tables, per-metric sections with provenance, artifact excerpts).

## Relationship summary

RunStateDir 1-N Thread; Thread 1-N Attempt; Attempt 1-N StepVerdict; Thread 0-N AuditRecord (best-effort timestamp join); RunStateDir 0-1 ProseArtifact per kind (latest thread only); Thread -> 1 RunRecord; RunRecord -> N Metric; FilterSet filters RunRecords; Report renders filtered RunRecords and their Metrics.

## Out of scope (no entity)

Workspace/pipeline attribution (dropped by FD-Q1), token/cost data (no capture exists anywhere), test output text parsing (D7), any write path (reporting is read-only, FR-12.8), egress events, stamps/hooks/caches.
