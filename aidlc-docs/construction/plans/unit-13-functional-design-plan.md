# Functional Design Plan - Unit 13 (Reporting Viz Extension)

Unit intent: `aidlc-docs/unit-13-intent-viz-reporting.md` (approved 2026-11-08). Hard dependency on unit-12 (COMPLETE): consumes `reporting/sources.py` + `reporting/metrics.py` directly, no duplicated computation.
Normative ground: live reads of viz/server.py, engine/cli.py, viz/page.py, tools/build_viz_page.py, viz/assets/*, reporting/{sources,metrics,render}.py (unit-12 API), engine/graph.py:292 viz call.

## Requirements mapping (trace source: unit-13 intent doc)

| ID | Requirement |
| --- | --- |
| FR-13.1 | Extend the existing viz server with a historical reporting view; new endpoints /runs and /runs/<id> |
| FR-13.2 | Endpoints serve the unit-12 metrics module (sources.py + metrics.py) as data; no duplicated computation |
| FR-13.3 | New viz view: runs list + aggregate metrics with filter controls + per-run detail |
| FR-13.4 | Filter controls match unit-12 v1 filters: --since/--until/--status (pipeline/workspace filters are deferred by unit-12 FD-Q1; the intent's D8 mention is superseded) |
| FR-13.5 | Historical backfill: existing runs render on first load; new runs appear as they land |
| FR-13.6 | Styling = unit-9 hand-written skin and tokens; no new framework or dependency |
| FR-13.7 | Provenance labels ([mechanical]/[parsed]/[claimed]) shown next to each metric in the UI |
| FR-13.8 | Read-only GET endpoints on the existing localhost viz server; no new auth surface; inherits unit-12 graceful degradation (unavailable data labeled, never fabricated) |

## Execution Checklist

- [x] Analyze unit context (unit-12 module API, viz server/live page structure, build script, launcher, graph.py integration)
- [x] Confirm no new external surfaces (internal-only: no EXTSRF probe needed; validated-model.md omitted)
- [x] Generate `aidlc-docs/construction/unit-13/functional-design/domain-entities.md` (view model, descent audit)
- [x] Generate `aidlc-docs/construction/unit-13/functional-design/business-rules.md` (endpoints, data flow, degradation, styling, refresh)
- [x] Generate `aidlc-docs/construction/unit-13/functional-design/business-logic-model.md` (request flow, view composition, build pipeline)
- [x] Resolve open design decisions FD-Q1..FD-Q4 (answers recorded below; FD-Q4 charting deferred to user-run selection)
- [x] Present Functional Design completion message and await approval (approved 2026-11-09)

## Open Design Decisions

### Question FD-Q1: view placement and navigation
The viz server is closure-built around one live topology (per-run). The historical view has no pipeline dependency (reads run state via reporting.sources). Options:

A) Same server, separate page: add /runs (runs view) and /runs/<id> (detail view) routes to the existing handler; the live page gets a small "History" link in its header. Standalone launcher gains a history mode that serves /runs without requiring --pipeline. (recommended: one viz server, live + history side by side, matches intent's "extend the existing viz server")

B) Same server, embedded section: the live page grows a runs section on the same page (tabs: Live | History). Single page, but the live page is already dense and tied to one run's EventSource lifecycle.

[Answer]: A: same server, separate page. Add /runs and /runs/<id> routes to the existing handler; the live page gets a small "History" link in its header. The standalone launcher gains a history mode serving /runs without requiring --pipeline.

### Question FD-Q2: data loading and refresh
/runs and /runs/<id> return JSON computed on request from reporting.sources/metrics (live read of the run state). For "new runs appear as they land":

A) JSON on every request + client-side poll: the runs page re-fetches /runs every 10s while open; server recomputes per request (no caching). Simple, always fresh, no state. (recommended)

B) Server-side snapshot at start + manual refresh button: one read at server start, refresh only on demand. Cheaper per request, but stale until the user acts.

[Answer]: B, modified: no polling. Refresh triggers = switching to the tab (visibilitychange) and a manual refresh button; each refresh fetches /runs fresh (server computes per request, no caching, no timer).

### Question FD-Q3: detail view contents and filter controls
A) Structured metric cards: /runs/<id> serves the per-thread data (attempts, verdict sequences, per-metric values with provenance); the detail page renders them as styled cards/tables with provenance badges next to every metric. Filter controls (date range inputs + status dropdown) live on the runs list page and apply to the list + aggregate. (recommended: structured, matches unit-9 skin, provenance visible per metric)

B) Rendered markdown: reuse unit-12 render.render_markdown(deep_dive=True) inside a styled container. Fast, but provenance/formatting is fixed by the markdown and the page has no structured control.

C) Minimal list + markdown: list page structured, detail page = markdown deep dive. Mixed.

[Answer]: A with charts. Structured metric cards/provenance badges per FD-Q3-A, PLUS charts on the detail view (and aggregate view where useful). Charting software selection required (user directive 2026-11-09) - resolved separately below as FD-Q4; any external charting library makes EXTSRF apply (probe pinned artifact before commitment).

### Question FD-Q4: charting software selection
Constraints: the viz page is served from localhost with no internet dependency (CDN breaks offline usage); no build step (tools/build_viz_page.py only inlines local assets); theme via the unit-9 CSS tokens (dark/light); repo test_repo_ascii.py forbids non-ASCII committed files (minified JS is ASCII, fine). Chart needs: per-attempt verdict sequence strips, step duration bars, status/verdict distributions, TDD red-green timelines, aggregate trend lines. Small fixed set.

[Answer]: Resolved by user-run selection (2026-11-09): ECharts 6.1.0, common bundle (echarts.common.min.js, 715,020 bytes, SHA256 61f13280aba3161f77fc801bf5f9c786f55f10432153a0683fc5cc0700b013d5), vendored and pinned, SVG renderer. Chart.js 4.6.1 = documented runner-up; hand-roll = documented fallback. Full process (criteria, longlist, community verification, spike validated-model table, gates): planning-docs/unit-13-charting-selection.md. EXTSRF satisfied by the Phase 5 probe; re-probe before any version bump. Build follow-ups recorded: build_viz_page.py must inline the vendor file; add resize handler (inst.resize()), getComputedStyle token bridge + re-render on theme change, and the min-width: 0 grid rule. aria option is a validated no-op for SVG (do not rely on it).
