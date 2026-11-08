# Business Rules - Unit 12 (Run-History Reporting CLI)

Numbered rules the implementation must honor. Rules trace to the intent doc (D1-D9, metrics catalog), the FD decisions (FD-Q1..FD-Q3), and the validated model (V1-V20).

## Access and safety

- BR-1 READ-ONLY. The reporting module never opens any run/state file for writing: sqlite via URI `file:...?mode=ro&immutable=1` (V17), text files opened 'r'. It never creates -wal/-shm/-journal files, never writes anywhere under the runs root or state dir. (FR-12.8)
- BR-2 WHITELISTED READS. Inside a run dir, read only: loop-checkpoints.sqlite (+ -wal/-shm as sqlite internals), attempt-brief.md, implementation-summary.md, review-findings.md, qa-report.md, last-error.md. exports/ is NEVER enumerated, no matter what (V15). Inside the state dir, read only sandbox-audit.jsonl and sandbox-audit.jsonl.1.
- BR-3 DISCOVERY. Run dirs = children of `$AGENTIC_DEV_LOOP_RUNS_ROOT` if set, else `~/.agentic-dev-loop/runs`, that contain loop-checkpoints.sqlite. Dirs without it are skipped silently; a missing runs root yields an empty report with a notice, not an error.
- BR-4 UNREADABLE DEGRADATION. A thread whose checkpoints fail to decode becomes 'unreadable' with the error note in the report; the report still renders everything else (U7). A missing audit file only degrades audit-sourced metrics, never aborts the report (D4).

## Reading and normalization

- BR-5 THREAD GROUPING. Rows are grouped by thread_id within each run dir. ts = decoded msgpack blob ts (ISO). Thread identity for display = subject channel where present, else thread_id (FD-Q1: no workspace/pipeline attribution).
- BR-6 CUMULATIVE STATE. The latest checkpoint per thread carries the cumulative channel_values (V4); that is the source for final attempt, outcome, failed, failed_step, step_verdicts, brief_retries (V5).
- BR-7 STATUS CLASSIFICATION. success iff outcome=='success'; else failed iff failed==True; else running iff the thread is the latest in its dir and has pending nodes (reuse run_status.py classification, V16/U6); else interrupted. Mapping note for the intent's pass/fail/crash vocabulary: pass=success, fail+crash=failed, abandoned/superseded=interrupted, in-flight=running.
- BR-8 ATTEMPT SEGMENTATION. Attempt boundaries = writes where step_verdicts resets to {'brief': None} (V7). Attempt number = attempt channel value at segment start. Order = (checkpoint_ns, idx). Attempt verdict sequence = ordered step_verdicts snapshots at each step's task_path within the segment.
- BR-9 PROSE ATTRIBUTION. Prose artifacts describe the LATEST thread in their dir only (V10); they contribute facts to that thread alone.

## Verdicts and provenance

- BR-10 COMMAND VERDICTS. deps/restore/test/commit verdicts are exit-code mechanical facts: 0 => pass, nonzero => fail (V8). Never parsed from output text (D7).
- BR-11 AGENT VERDICTS. Structured record_verdict output => [mechanical]. Legacy prose (artifacts 'VERDICT: ...', notes '## qa output ...') => [parsed] via tolerant keyword parse; unparseable => unavailable (V9, U2).
- BR-12 PROVENANCE LABELS. Every rendered metric carries exactly one of [mechanical] / [parsed] / [claimed]; aggregates mixing sources state their composition. [claimed] is reserved for dev self-report (implementation-summary.md) and only appears in the claim cross-check metric.
- BR-13 NO FABRICATION. Unavailable data renders as 'unavailable (<reason>)'. Metrics never infer, extrapolate, or guess (D4).

## Metrics (from the intent catalog, made concrete)

- BR-14 TDD - GATE SIGNAL. Per attempt: red-then-green iff the attempt's verdict sequence contains a test fail followed later by a test pass in the same attempt. Reported: % attempts with red-then-green; % threads with at least one red-then-green attempt; attempts with zero test verdicts (never reached the test step). Source: step_verdicts reconstruction (V7, V8). Provenance [mechanical]. (FD-Q3 option A)
- BR-15 TDD - DEV LANE. Dev-step commands matching the test-command pattern (recipe test command + defaults: pytest, 'python -m pytest', 'dotnet test', 'npm test', 'cargo test'; substring match on the command, D7) are counted from audit rows joined to the thread by timestamp overlap; red-then-green within the dev lane = a nonzero-exit test command followed by a zero-exit one in the same dev step. Provenance [mechanical]; rendered unavailable when no audit rows overlap the thread (rotation, V13) or when overlap is ambiguous (U1).
- BR-16 TDD - CLAIM CROSS-CHECK. Dev self-report of test activity (implementation-summary.md mentioning tests) vs mechanical test-verdict signal; agreement rate reported with [claimed] vs [mechanical] split. Parse tolerant (U2).
- BR-17 REVIEW EFFECTIVENESS. Defect yield = review findings count per review-pass attempt ([parsed] from review-findings.md, latest thread only). Review-missed = review verdict pass followed by qa verdict fail in the same attempt ([mechanical]). Findings-fixed = review fail followed by dev pass-2 then re-review in the same attempt ([mechanical]). Rubber-stamp = review verdict pass with an empty review artifacts entry for that attempt ([mechanical]).
- BR-18 QA EFFECTIVENESS. Catch rate = attempts with review pass then qa fail / attempts reaching qa ([mechanical]). Environmental rate = share of qa verdicts equal to ENV ([mechanical] structured / [parsed] legacy). Unverifiable rate = share of UNVERIFIABLE. QA pass rate = qa pass verdicts / qa verdicts rendered ([mechanical]/[parsed]).
- BR-19 LOOP HEALTH. Thread count; success/failed/interrupted/running rates ([mechanical]); attempt-count distribution and retry_target frequency (brief vs dev) ([mechanical]); per-step durations = ts deltas between consecutive step checkpoints per thread ([mechanical], permanent), with audit duration_seconds as the command-level source when rows survive ([mechanical], else unavailable); verdict distribution per step (pass/fail/env/unverifiable counts); brief_retries counts ([mechanical]).
- BR-20 AGGREGATE DENOMINATORS. Every rate in the aggregate report states its denominator (n) and is computed over the FILTERED set only; per-run deep dive shows the same metrics per thread.

## Filters and rendering

- BR-21 FILTERS v1. --since/--until: ISO datetimes, inclusive start, exclusive end, applied to thread ts_start; both optional. --status: any of success/failed/interrupted/running; repeatable or comma list; default all. Filters combine with AND. Workspace/pipeline filters are deferred (FD-Q1).
- BR-22 MARKDOWN OUTPUT. ASCII-only, no em dash (repo rule). Fixed section order: aggregate report = header, filter summary, loop health, TDD, review, QA, thread table; deep dive = dir header, thread sections (attempt verdict tables), metric sections, artifact excerpts. Unavailable entries render inline with reasons.
- BR-23 CLI SURFACE. `agentic-dev-loop report` (aggregate) and `agentic-dev-loop report <run-id>` (deep dive; run-id = 12-hex dir name), plus --since/--until/--status. Output to stdout only (FD-Q2). Wrapper `.opencode/commands/loop-report.md` passes `$ARGUMENTS` through, like /run-status.
