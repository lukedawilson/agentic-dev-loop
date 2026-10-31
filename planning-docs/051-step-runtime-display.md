# Plan 051: Step run times and loop total in the viz sidebar

> **Status: APPROVED**

## Scope

Two additions to the live viz page (the embedded `PAGE` in
`src/agentic_dev_loop/viz/server.py`):

1. Each step's accumulated run time shown in its sidebar dropdown header
   (the `.stage-header` collapsible row) once that step has completed.
2. The entire loop's total runtime shown near the top of the sidebar,
   ticking live while the run is in progress and frozen at the final value
   when the run finishes.

Out of scope: hours formatting (mm:ss rolls past 59:59, matching the
existing heartbeat line), per-step times for steps that errored (see D3),
any change to the graph panel itself.

## Context

Verified facts:

- The viz UI is one embedded HTML page: the `PAGE` constant in
  `src/agentic_dev_loop/viz/server.py`. The "side panel" is `#sidebar`; the
  "step dropdown headers" are the `.stage-header` buttons built by
  `ensureSection()` and re-rendered by `renderStageHeader()`. Each header
  holds three spans: `.stage-glyph`, `.stage-name`, `.stage-status`
  (server.py lines 696-772). Collapsed sections hide `.console-wrap`.
- Events published by `engine/graph.py` `run_pipeline` (lines 279-322):
  `run_started` (subject, max_attempts, thread_id, attempt), then per
  StreamPart `viz.translate()` emits `node_started` / `node_finished` /
  `state`, then `run_finished` (success, attempts).
- `viz.translate()` (server.py lines 100-129) is pure; it has no clock and
  its output is asserted byte-for-byte in `tests/test_viz.py::TestTranslate`.
  Timing must be attached where events are published (the `graph.py` stream
  loop), not inside `translate`.
- `NodeWatch` / `HeartbeatThread` already publish backend-measured
  `elapsed_seconds` for the currently running node, rendered in `#elapsed`.
  Backend-measured timing is the established pattern here.
- The SSE backlog (`VizBus`, maxlen 500) replays retained events to tabs
  opened mid-run. A frontend-measured duration would be wrong for replayed
  events (a replayed start and finish arrive in the same instant), so
  durations must be measured backend-side and carried on the events.
- `graph.py` does not import `time` today (verified: imports are lines
  8-31). `import time` must be added.
- The frontend formatting helper `mmss(totalSeconds)` already exists
  (PAGE, line ~599) and tolerates minutes over 59.
- `tests/test_viz.py` has two layers: substring assertions against `PAGE`
  (`TestServer.test_page_constant_*`) and real execution of the page script
  under node with a DOM shim (`TestPageScriptBehavior`, HARNESS at lines
  853-1036). The harness stubs `setTimeout` but not `setInterval` /
  `clearInterval`; an un-cleared interval would keep the node process alive
  until the 60s subprocess timeout.
- `tests/test_graph.py::TestVizStream` (line ~431 onward) runs a real
  pipeline with scripted fake agents and drains `VizBus` events via
  `drain_events`, asserting event shapes.
- `viz_demo.py` (repo root) fabricates its own events (`emit_node`,
  `run_scenario`); it does not go through `graph.py`, so it needs the same
  keys added for the demo page to show the new UI.
- All committed content must be ASCII (em dash ban; `test_repo_ascii.py`
  plus a pre-commit hook). Any non-ASCII separator in the page must be
  avoided entirely or `\u`-escaped ASCII source.

## Design decisions

**D1. Backend-measured durations carried on events.** `node_finished`
gains `elapsed_seconds`; `run_started` gains `started_at` (wall clock,
`time.time()`, epoch seconds); `run_finished` gains `elapsed_seconds`.
Rationale: replay correctness for tabs opened mid-run, Python-level
testability of event shapes, consistency with the heartbeat.

**D2. Per-step totals accumulate across retries.** The frontend sums
`elapsed_seconds` per node (`stepTimes[node] += ...`). A step that
loop-backs and re-runs accumulates; "total run time" means total time
spent in that step across the loop. The alternative (show only the last
run's duration) was rejected: the request says "total", and the attempt
loop structure argues for summation. Attempt bumps (`state` events with a
new `attempt`) must NOT reset `stepTimes`, matching how verdicts survive
loopbacks (existing test `test_attempt_change_keeps_latest_results`).

**D3. Step time renders in the header as a right-aligned `.stage-time`
span, shown only when `stageWord[node] === "completed"`.** Pending and
running steps show nothing. Option recorded but out of scope: also show
for "errored" steps.

**D4. Loop total is a new `#total` div near the top of the sidebar**
(between `#thread` and `#elapsed`). While the run is live it ticks every
second using `started_at`; on `run_finished` it freezes at the backend
`elapsed_seconds`. If `run_started` carries no `started_at` (old/replayed
streams, existing test fixtures), no interval is started and the total
appears only at `run_finished`. This keeps existing node-harness tests
green without hangs.

**D5. Durations are integer seconds** (`int(round(monotonic delta))`) so
the existing `mmss()` needs no change. Per-step rounding may drift
fractionally from wall clock; the loop total is computed directly from a
`t0`, never summed from steps, so the top number is accurate.

**D6. Missing-pair safety.** A `node_finished` with no matching
`node_started` (backlog truncation, resume re-entry) publishes no
`elapsed_seconds`; the frontend ignores the missing key. All durations
clamped to `>= 0`.

## Changes

### Backend: `src/agentic_dev_loop/engine/graph.py`

- Add `import time` at module top.
- In `run_pipeline`'s bus branch (lines ~279-322):
  - Capture `run_start = time.monotonic()` immediately before publishing
    `run_started`, and add `"started_at": time.time()` to that publish
    dict.
  - Maintain `step_starts: dict[str, float] = {}`; in the translate loop,
    on `node_started` record `step_starts[node] = time.monotonic()`; on
    `node_finished`, if the node is in `step_starts`, set
    `event["elapsed_seconds"] = max(0, int(round(time.monotonic() - step_starts[node])))`
    before `bus.publish(event)`.
  - In the `finally` block, add
    `"elapsed_seconds": max(0, int(round(time.monotonic() - run_start)))`
    to the `run_finished` publish dict (computed regardless of whether
    `final` state was recovered).
- `translate()` is untouched and stays pure.

### Frontend: `src/agentic_dev_loop/viz/server.py` `PAGE`

- CSS: `.stage-header` changes from `display: block` to
  `display: flex; align-items: baseline` (keeps `width: 100%`,
  `text-align: left`, clickability). New
  `.stage-time { margin-left: auto; color: #555; font-variant-numeric: tabular-nums; }`.
  New `#total { font-size: 14px; color: #1976d2; display: none; }`
  (mirrors `#elapsed`).
- HTML: add `<div id="total"></div>` after the `#thread` div and before
  `#elapsed`.
- JS:
  - State: `var stepTimes = {}; var runStartWall = null; var totalTimer = null;`
  - `ensureSection`: append a `<span class="stage-time">` to the header
    after the status span.
  - `renderStageHeader`: after computing `word` and `suffix`, set
    `header.querySelector(".stage-time").textContent =
    (word === "completed" && stepTimes[node]) ? mmss(stepTimes[node]) : "";`
  - New `renderTotal()`: if `runStartWall === null`, hide `#total`;
    otherwise set text to `"total " + mmss(Math.max(0, Math.round(Date.now() / 1000 - runStartWall)))`
    and display it.
  - `run_started` case: `stepTimes = {};` (before `clearConsoles()`
    re-renders headers); stop any prior interval; `runStartWall =
    (typeof ev.started_at === "number") ? ev.started_at : null;` if set,
    `totalTimer = setInterval(renderTotal, 1000);` then `renderTotal()`.
  - `node_finished` case: after the `nodeIds.has(ev.node)` guard and
    before `setStageStatus`, accumulate:
    `if (ev.elapsed_seconds !== undefined) { stepTimes[ev.node] =
    (stepTimes[ev.node] || 0) + ev.elapsed_seconds; }`
  - `run_finished` case: stop the interval (`clearInterval(totalTimer);
    totalTimer = null;`); if `ev.elapsed_seconds !== undefined`, set
    `runStartWall = null` and freeze `#total` at
    `"total " + mmss(ev.elapsed_seconds)`.
  - All new string literals are ASCII ("total ", " ").

### Demo parity: `viz_demo.py`

- `run_scenario`: publish `run_started` with `"started_at": time.time()`;
  capture a scenario start; publish `run_finished` with `elapsed_seconds`.
- `emit_node`: measure around each node's cadence and include
  `elapsed_seconds` on the `node_finished` publish (its existing
  `time.sleep` cadence makes the numbers non-trivial).

## Tests (TDD: red, then green, then refactor)

1. `tests/test_graph.py::TestVizStream` (new):
   - `test_run_events_carry_timings`: scripted all-pass run with
     `viz_bus`; drain events. Assert `run_started["started_at"]` is a
     number within the test's `time.time()` window; `run_finished` has an
     int `elapsed_seconds >= 0`; every `node_finished` has an int
     `elapsed_seconds >= 0`.
   - Existing `TestTranslate` assertions stay green unchanged, proving
     enrichment lives in `graph.py`, not `translate`.
2. `tests/test_viz.py::TestServer` (new substring tests):
   - `test_page_constant_has_step_time`: `"stepTimes"`, `".stage-time"`,
     `"stage-time"` in `PAGE`.
   - `test_page_constant_has_loop_total`: `'id="total"'`,
     `"started_at"`, `"renderTotal"`, `"setInterval"`, `"clearInterval"`
     in `PAGE`.
3. `tests/test_viz.py::TestPageScriptBehavior` (new tests; harness gains
   `setInterval`/`clearInterval` stubs that capture the callback, and a
   replaceable `Date.now`):
   - `test_step_header_shows_accumulated_time`: run_started
     (started_at=1000), node_started dev, node_finished dev
     elapsed_seconds 135, state pass, node_finished dev elapsed_seconds 45
     (retry), state pass. Assert `stage-dev` `.stage-time` textContent is
     `"3:00"` (135 + 45); assert a never-run node's `.stage-time` is
     empty.
   - `test_loop_total_ticks_then_freezes`: started_at=1000; advance fake
     `Date.now` to 1065000, invoke the captured interval callback, assert
     `#total` is `"total 1:05"`; then run_finished elapsed_seconds 200,
     assert `#total` is `"total 3:20"`.
   - `test_loop_total_fallback_without_started_at`: run_started without
     started_at (existing fixture shape), no interval started, `#total`
     hidden; run_finished elapsed_seconds 95 -> `"total 1:35"`.
   - All existing behavior tests stay green (no `started_at` sent, so no
     interval starts).
4. Full pytest run (viz, graph, repo-ascii), then refactor only if
   duplication appears (e.g. a shared `resetTimings()`/`freezeTotal()`).

## Edge cases

- Node finished with no start: no `elapsed_seconds`; frontend ignores.
- Retries and loopbacks: `stepTimes` sums; verdict behavior unchanged.
- Tab opened mid-run: backlog replays events WITH durations (backend
  measured), so totals are correct after replay; the live tick uses the
  `started_at` wall clock.
- Attempt bumps: no `stepTimes` reset (only `run_started` resets).
- Runs over an hour: mm:ss rolls past 59:59, matching the existing
  heartbeat line; explicitly out of scope to change.
- Failure path: `finally` still publishes `run_finished` with
  `elapsed_seconds` even when the final state could not be read; the
  frontend freezes the total either way.
- `--no-viz` runs: no events, no impact.

## Constraints and notes

- All added source must be ASCII; this doc is ASCII. No em dashes
  (`tests/test_repo_ascii.py` plus the pre-commit hook).
- No tooling paths are touched (changes are in `src/`, `tests/`, and
  `viz_demo.py`; `pipeline.yaml` and `sdk_agents/` are untouched), so the
  loop commit sweeps the deliverable normally. `viz_demo.py` sits at the
  repo root and is not a tooling path.
- Backward compatibility: all new event keys are additive; `translate()`
  output is unchanged, so other `translate` consumers are unaffected.
- TDD is required: write the red tests above before implementation.

## Verification checklist (pre-completion)

- [ ] pytest green: `tests/test_viz.py`, `tests/test_graph.py`,
      `tests/test_repo_ascii.py`.
- [ ] Manual demo: `python viz_demo.py --no-browser`; step headers show
      per-step times and the top total ticks then freezes.
- [ ] A live CLI run with viz shows header times and the frozen total at
      the end.
- [ ] Existing page tests still green (no regressions in verdict badges,
      console capture, copy button).
