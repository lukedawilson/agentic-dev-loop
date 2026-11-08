# Unit 13 Intent: Reporting Viz Extension

- **Status**: INTENT (drafted 2026-11-08, brainstorming session); all design sections approved by user 2026-11-08
- **Source**: brainstorming session 2026-11-08; split from the combined reporting intent by user directive
- **Directive** (user, 2026-11-08, verbatim): "Actually, two units: - firs tunit: cli, opencode wrapper command, markdown report - second unit: viz extension"
- **Sibling**: unit-12 (reporting CLI + opencode wrapper + markdown report): aidlc-docs/unit-12-intent-reporting-cli.md

## Scope

Extend the existing viz server with a historical reporting view:

- New endpoints /runs and /runs/<id> serving the unit-12 metrics module.
- New viz view: runs list + aggregate metrics (with the unit-12 D8 filter
  controls: pipeline, date, status, workspace/repo) + per-run detail.
- Historical backfill: the existing 27 runs render on first load; new runs
  appear as they land.
- Styling: the unit-9 hand-written skin and token system; no new styling
  framework or dependencies.

## Architecture

- Endpoints in viz/server.py; view assets in viz/assets/ following the
  page.html / page.js / pipeline-layout.js conventions.
- Consumes reporting/metrics.py directly (import), one source of truth: no
  metric computation is duplicated in the viz.
- Provenance labels ([mechanical] / [parsed] / [claimed]) are shown in the UI
  next to each metric, so the visual view carries the same honesty guarantee
  as the markdown report.
- No new data sources: everything is already normalized by the unit-12
  sources.py run records.

## Extension applicability

- External Surfaces Validation: mostly internal (the metrics module and the
  viz server are both ours); the checkpoint-format probe belongs to unit-12.
  EXTSRF applies if any new external dependency is proposed (none expected).
- Security Baseline: read-only GET endpoints on the existing localhost viz
  server; no new auth surface beyond what viz already has.
- Resiliency Baseline (scoped, directional): inherits the unit-12 graceful
  degradation - unavailable metrics are labeled, never errors.
- Domain Standards: applies at Functional Design (view model types).

## Dependency

Hard dependency on the unit-12 metrics module (sources.py and metrics.py).
Unit ordering: unit-12 first, unit-13 after unit-12 closes.

## Workflow plan (unit-13)

Same lean shape as unit-12: Functional Design -> Code Generation (user-driven,
TDD) -> Build and Test, after unit-12 closes. NFR Requirements, NFR Design,
and Infrastructure Design are N/A.

## Open questions

None open at intent time. View layout details (list/aggregate/detail
navigation, filter control placement) are deferred to Functional Design.
