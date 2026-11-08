# Business Logic Model - Unit 12 (Run-History Reporting CLI)

Control flow of the reporting pipeline. Module layout: `src/agentic_dev_loop/reporting/{__init__,sources,metrics,render}.py` plus a `report` subcommand in `engine/cli.py` and the wrapper `.opencode/commands/loop-report.md`. Unit-13 (viz) later consumes `reporting/metrics.py` directly, so sources/metrics/render are pure and importable (no CLI coupling, no prints).

## Pipeline

discover -> read -> normalize -> metrics -> filter -> render

### 1. Discover (sources.discover)
BR-3: list run dirs under the runs root containing loop-checkpoints.sqlite. Aggregate report: all dirs. Deep dive: the single dir named by `<run-id>` (BR-23; missing dir = clean error message, exit nonzero).

### 2. Read (sources.read_dir)
For each run dir, with BR-1/BR-2:
- Open `loop-checkpoints.sqlite` read-only immutable (V17).
- Load checkpoints + writes per thread_id (BR-5); decode msgpack blobs (V4). Decode failures mark the thread unreadable (BR-4).
- Read prose artifacts if present (BR-9).
- Read the global audit files once per invocation (not per dir): sandbox-audit.jsonl + .1, keeping rows that carry exit_code (U4, V12).

### 3. Normalize (sources.build_run_records)
Per thread -> one RunRecord (domain-entities.md):
- identity: thread_id, dir, subject, ts_start/ts_end
- status via BR-7 (reuse run_status.py classification)
- attempts via BR-8 (verdict-reset segmentation; ordered by (checkpoint_ns, idx))
- final channels via BR-6
- audit join: rows whose timestamp falls in [ts_start, ts_end], tagged per step; ambiguous overlaps excluded (U1); dev-lane command classification by test-command pattern (BR-15)
- prose facts: verdicts/findings/claims via tolerant parse (BR-9, BR-11)

### 4. Metrics (metrics.pure_metrics)
Pure functions: (run_records, audit_pool) -> metrics. Implements BR-14..BR-20. Output shape: a metrics dict per thread plus section aggregates, each entry with value, provenance, basis, availability. No I/O, no prints: unit-13 reuses this unchanged.

### 5. Filter (metrics.apply_filters)
BR-21: apply --since/--until (thread ts_start) and --status, AND-combined, to the run record set; aggregates recomputed over the filtered set (BR-20).

### 6. Render (render.render_markdown)
BR-22/BR-23: markdown, ASCII-only, fixed sections, provenance labels on every metric, unavailable rendered with reasons. Returns the string; the CLI prints it to stdout (FD-Q2). The wrapper renders it in chat.

## CLI flow

`agentic-dev-loop report [<run-id>] [--since ISO] [--until ISO] [--status {success,failed,interrupted,running}]`

argparse subcommand alongside run-status; pipeline above; print markdown; exit 0 on success (even when data is degraded - degradation is reported, not fatal), exit nonzero only on hard input errors (unknown run-id, malformed datetime).

## Error and degradation flow

| Failure | Behavior |
| --- | --- |
| Runs root missing | empty report + notice, exit 0 |
| Run dir has no sqlite | skipped (BR-3) |
| Checkpoint blob decode fails (one thread) | thread 'unreadable' + error note, others render (BR-4) |
| Audit files missing | audit-sourced metrics 'unavailable (rotation)' (BR-13) |
| Audit overlap ambiguous | those rows excluded; metrics degraded or unavailable (U1, BR-15) |
| Prose artifact missing/unparseable | parsed facts 'unavailable (no artifact / unparseable)' (U2) |
| Unknown run-id / malformed datetime | clean error, exit nonzero (BR-23) |

## Cross-unit surface

Unit-13 consumes: sources.discover/read_dir/build_run_records (for /runs and /runs/<id> data) and metrics.pure_metrics + apply_filters (for the aggregate). Render stays unit-12-only (markdown); unit-13 renders its own page. No duplicate computation: unit-13 never re-derives metrics.

## Deferred (recorded, out of v1)

- Workspace/pipeline attribution and filters (FD-Q1 user decision)
- --out file flag (FD-Q2 option B)
- Pluggable per-format test-output parser registry (D7 seam, intent deferred)
- Token/cost metrics (intent: separate future instrumentation unit)
- run-metadata.json engine patch (FD-Q1 option A)
