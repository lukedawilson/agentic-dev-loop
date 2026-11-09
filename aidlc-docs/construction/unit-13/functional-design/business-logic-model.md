# Business Logic Model - Unit 13 (Reporting Viz Extension)

Control flow for the historical runs view. No new data sources: everything flows through unit-12 reporting.sources + reporting.metrics. Changes land in: viz/server.py (routes), viz/assets/ (new runs.html/runs.js, shared styles.css), tools/build_viz_page.py (second generated page), viz/__main__.py (--history mode).

## Request flows

### GET /runs/data (list + aggregate)
parse query (BR-5) -> discover() -> read_audit() -> per dir read_dir() (unreadable dirs contribute a notice, never abort) -> build_run_records -> apply_filters -> pure_metrics -> serialize RunsListPayload (BR-4) -> 200 JSON. Filter errors -> 400 JSON.

### GET /runs/<id>/data (detail)
find_run_dir(run_id) -> missing: 404 JSON (BR-6) -> read_dir + read_audit -> build_run_records for that dir -> pure_metrics -> serialize RunDetailPayload (threads, attempts, verdict sequences with provenance, artifacts presence, notice) -> 200 JSON.

### GET /runs and /runs/<id> (pages)
Serve the generated RUNS_PAGE HTML (200 text/html). The client-side JS decides list vs detail from location.pathname and fetches the matching /data route. Unknown-id detail renders the not-found state client-side.

## Handler integration (viz/server.py)

- do_GET gains the four routes before the existing ones (BR-2); /events and /topology keep their behavior in pipeline mode; in history mode they are absent (BR-17).
- The handler needs no topology data for the runs routes; the routes call reporting functions per request (BR-3), which use the default runs root and state dir (env-aware, unit-12 sources).
- serve_viz keeps its existing signature for pipeline mode (graph.py:292 call site unchanged). History mode uses a new entry that serves the runs page without a topology (BR-17).

## Launcher (viz/__main__.py)

- New flag --history: no load_config, no pipeline file required; starts the same server with the runs routes and / serving the runs page; prints the bound URL (existing port-fallback behavior).
- --pipeline mode: unchanged topology flow, plus the /runs routes and the live-page History link (BR-14).

## Client flow (viz/assets/runs.js)

load -> detect list vs detail from pathname -> fetch /runs/data or /runs/<id>/data -> render list (rows with status + summary), aggregate sections with MetricPayload cards and provenance badges (BR-12), filter controls bound to query params (BR-5) -> refresh on visibilitychange + manual button only (BR-13). Detail: render thread sections, attempt verdict sequences, artifact facts, chart slots (BR-15, deferred). Degradation: unavailable metrics render with reason (BR-8).

## Build pipeline (tools/build_viz_page.py)

Read runs.html (single marker) -> inline runs.js + styles.css + the vendored echarts.common.min.js -> emit RUNS_PAGE string into the generated module, alongside the existing PAGE emission (BR-18). Generated modules are never hand-edited.

## Error and degradation flow

| Failure | Behavior |
| --- | --- |
| Invalid since/until/status params | 400 JSON error (BR-5) |
| Unknown run id in /runs/<id>/data | 404 JSON; detail page shows not-found state (BR-6) |
| Unreadable run dir / missing artifacts | notice in payload; metrics unavailable with reason (BR-8) |
| Audit rotated away | unit-12 labels unavailable; UI shows reason (BR-8) |
| Live run in progress | snapshot semantics; running status shown from checkpoint state (BR-10) |
| Port busy | existing fallback to OS-assigned port + notice (BR-17) |

## Cross-unit surface

Consumes (unit-12, stable API): reporting.sources.{discover, find_run_dir, read_audit, read_dir, build_run_records}, reporting.metrics.{pure_metrics, apply_filters, STATUS_VALUES, Metric.as_dict}. Unit-13 adds nothing to the unit-12 API; if a serialization gap appears (e.g. RunRecord to-dict), the to-dict lives in unit-13's view layer or is proposed to unit-12 via a follow-up, never duplicated.

## Deferred (recorded, out of v1)

- Pipeline/workspace filter controls (unit-12 FD-Q1 deferral applies here too).
- Spike artifacts still live in the opencode temp dir; copy them into spikes/ during the build phase (per planning-docs/unit-13-charting-selection.md follow-ups).
