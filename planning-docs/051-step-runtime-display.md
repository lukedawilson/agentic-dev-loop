# Plan 051: Step run times and loop total in the viz sidebar

> **Status: APPROVED**

## Scope

Three additions to the live viz page (the embedded `PAGE` in
`src/agentic_dev_loop/viz/server.py`):

1. The entire loop's total wallclock time shown in the top-of-sidebar
   readout where the running node's elapsed line renders today
   (`#elapsed`: "test running - 0:27 elapsed"). The loop total replaces
   that per-node heartbeat line.
2. The currently running stage's live wallclock time shown in that
   stage's dropdown title bar (the `.stage-header` row), ticking via the
   existing backend heartbeat, in the same spot completed stages show
   theirs.
3. Each completed stage's accumulated run time shown in its dropdown
   title bar (the `.stage-time` span), summing across retries.

Out of scope: hours formatting (mm:ss rolls past 59:59, matching the
existing heartbeat line), per-step times for steps that errored (see D3),
any change to the graph panel itself.

## Context

Verified facts:

- The viz UI is one embedded HTML page: the `PAGE` constant in
  `src/agentic_dev_loop/viz/server.py`. The "side panel" is `#sidebar`
  (HTML lines 530-538); the "step dropdown headers" are the
  `.stage-header` buttons built by `ensureSection()` (lines 696-750,
  three spans: `.stage-glyph`, `.stage-name`, `.stage-status`) and
  re-rendered by `renderStageHeader()` (lines 752-772). Collapsed
  sections hide `.console-wrap`.
- The top-of-sidebar readout is `<div id="elapsed">` (line 534, CSS line
  469). The frontend heartbeat handler renders "`<node>` running -
  `<mm:ss>` elapsed" into it (lines 1073-1078), and `clearElapsed()`
  (lines 605-609) hides it; `clearElapsed()` is called on
  `node_finished` (line 1060) and `run_finished` (line 1109).
- Heartbeats come from `viz.HeartbeatThread` (server.py lines 315-338),
  publishing `{"type": "heartbeat", "node": <node>, "elapsed_seconds":
  <int>}` every 2.0s while a node runs. The bus marks heartbeats
  live-only (never replayed from the backlog; test
  `test_heartbeat_live_only_not_replayed`, test_viz.py line 286).
- Events published by `engine/graph.py` `run_pipeline` (lines 279-322):
  `run_started` (subject, max_attempts, thread_id, attempt), then per
  StreamPart `viz.translate()` emits `node_started` / `node_finished` /
  `state`, then `run_finished` (success, attempts).
- `viz.translate()` (server.py lines 100-129) is pure; it has no clock
  and its output is asserted byte-for-byte in
  `tests/test_viz.py::TestTranslate`. Timing must be attached where
  events are published (the `graph.py` stream loop), not inside
  `translate`.
- `graph.py` does not import `time` today (verified: imports are lines
  8-31). `import time` must be added.
- The frontend formatting helper `mmss(totalSeconds)` already exists
  (line 599) and tolerates minutes over 59.
- `tests/test_viz.py` has two layers: substring assertions against `PAGE`
  (`TestServer.test_page_constant_*`, including
  `test_page_constant_has_heartbeat` at 762-765 and
  `test_page_constant_clears_elapsed` at 767-769) and real execution of
  the page script under node with a DOM shim (`TestPageScriptBehavior`,
  HARNESS lines 853-1036). Harness specifics that matter here:
  - `STATIC_IDS` (line 936) pre-creates stub elements, including
    `"elapsed"`.
  - The harness stubs `setTimeout` but not `setInterval` /
    `clearInterval`; an un-cleared interval would keep the node process
    alive until the 60s subprocess timeout.
  - `fire(ev)` feeds events synchronously; snapshots are pushed on
    `state` events (lines 1020-1025) and once at the end (lines
    1026-1033). Existing snapshots carry only `badges` and `classes`.
  - No existing behavior test fires a `heartbeat` event, so the
    heartbeat UI path is currently untested at the page level.
- `tests/test_graph.py::TestVizStream` (line ~431 onward) runs a real
  pipeline with scripted fake agents and drains `VizBus` events via
  `drain_events`, asserting event shapes.
- `viz_demo.py` (repo root) fabricates its own events. `emit_node`
  (lines 51-88) publishes `node_started`, console, two `heartbeat`
  events with real `elapsed_seconds` (lines 73-74, 85-86), `state`, and
  `node_finished` (line 87, currently no `elapsed_seconds`).
  `run_scenario` (lines 91-128) publishes `run_started` (lines 98-99,
  no `started_at`) and `run_finished` (lines 111, 128, no
  `elapsed_seconds`).
- All committed content must be ASCII (em dash ban; `test_repo_ascii.py`
  plus a pre-commit hook). Existing non-ASCII page text is `\u`-escaped
  ASCII source (e.g. `\u2014`), which is fine.

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
loop structure argues for summation. Attempt bumps (`state` events with
a new `attempt`) must NOT reset `stepTimes`, matching how verdicts
survive loopbacks (existing test
`test_attempt_change_keeps_latest_results`).

**D3. One `.stage-time` span per header, right-aligned, whose content
depends on the stage's state.**
- `completed`: `mmss(stepTimes[node])` (the accumulated total).
- `running`: `mmss(liveElapsed[node])`, the live heartbeat value for
  the currently running node.
- anything else (pending, errored): empty.

`renderStageHeader()` owns the span's text so every re-render (collapse,
expand, status change) stays consistent. The running stage's time is
seeded to `0:00` on `node_started` and then refreshed by each
`heartbeat` event (at most every 2.0s). Option recorded but out of
scope: also show times for errored steps.

**D4. The loop total takes over the top readout.** The `#elapsed` div
is renamed `#total`; the heartbeat handler stops writing the
"`<node>` running" line there (that information now lives in the
running stage's header per D3). `#total` shows "total `<mm:ss>`":
- hidden until the first `run_started`;
- ticks every second from `ev.started_at` (wall clock) while the run is
  live;
- freezes at the backend `ev.elapsed_seconds` on `run_finished` and
  stays visible after the run ends.

If `run_started` carries no `started_at` (old/replayed streams, existing
test fixtures), no interval is started and the total appears only at
`run_finished`. `clearElapsed()` is deleted along with its two call
sites.

**D5. Durations are integer seconds** (`int(round(monotonic delta))`)
for `node_finished` and `run_finished`, so the existing `mmss()` needs
no change. Per-step rounding may drift fractionally from wall clock; the
loop total is computed directly from a `t0`, never summed from steps,
so the top number is accurate.

**D6. Missing-pair safety.** A `node_finished` with no matching
`node_started` (backlog truncation, resume re-entry) publishes no
`elapsed_seconds`; the frontend ignores the missing key. All durations
clamped to `>= 0`.

**D7. Heartbeat routing rules.** A `heartbeat` event updates
`liveElapsed[ev.node]` and re-renders that stage's header only when
`nodeIds.has(ev.node)` and `nodeStatus[ev.node] === "running"`
(preserves the current guard at line 1074). `node_started` resets
`liveElapsed` to a single entry `{node: 0}` (only one node runs at a
time); `run_started` resets both `stepTimes` and `liveElapsed` before
`clearConsoles()` re-renders the headers.

## Changes

### Backend: `src/agentic_dev_loop/engine/graph.py`

- Add `import time` at module top.
- In `run_pipeline`'s bus branch (lines ~279-322):
  - Capture `run_start = time.monotonic()` immediately before publishing
    `run_started`, and add `"started_at": time.time()` to that publish
    dict.
  - Maintain `step_starts: dict[str, float] = {}`; in the translate
    loop, on `node_started` record `step_starts[node] =
    time.monotonic()`; on `node_finished`, if the node is in
    `step_starts`, set
    `event["elapsed_seconds"] = max(0, int(round(time.monotonic() -
    step_starts[node])))` before `bus.publish(event)`.
  - In the `finally` block, add
    `"elapsed_seconds": max(0, int(round(time.monotonic() -
    run_start)))` to the `run_finished` publish dict (computed
    regardless of whether `final` state was recovered).
- `translate()` is untouched and stays pure.

### Frontend: `src/agentic_dev_loop/viz/server.py` `PAGE`

- CSS:
  - `.stage-header` changes from `display: block` to
    `display: flex; align-items: baseline` (keeps `width: 100%`,
    `text-align: left`, clickability). New
    `.stage-time { margin-left: auto; color: #555;
    font-variant-numeric: tabular-nums; }`.
  - Rename the `#elapsed` rule (line 469) to
    `#total { font-size: 14px; color: #1976d2; display: none; }`.
- HTML: rename `<div id="elapsed"></div>` (line 534) to
  `<div id="total"></div>`; position unchanged (after `#thread`, before
  `#verdicts`).
- JS:
  - State: `var stepTimes = {}; var liveElapsed = {}; var runStartWall =
    null; var totalTimer = null;`
  - `ensureSection`: append a `<span class="stage-time">` to the header
    after the status span.
  - `renderStageHeader`: after computing `word`, set
    `header.querySelector(".stage-time").textContent =
    (word === "completed" && stepTimes[node]) ? mmss(stepTimes[node])
    : (word === "running" && liveElapsed[node] !== undefined) ?
    mmss(liveElapsed[node]) : "";`
  - Delete `clearElapsed()` and its calls (node_finished line 1060,
    run_finished line 1109).
  - New `renderTotal()`: if `runStartWall === null`, hide `#total`;
    otherwise set text to `"total " + mmss(Math.max(0,
    Math.round(Date.now() / 1000 - runStartWall)))` and display it.
  - `run_started` case: `stepTimes = {}; liveElapsed = {};` (before
    `clearConsoles()` re-renders headers); stop any prior interval;
    `runStartWall = (typeof ev.started_at === "number") ?
    ev.started_at : null;` if set, `totalTimer = setInterval(renderTotal,
    1000);` then `renderTotal()`.
  - `node_started` case: after the `nodeIds.has(ev.node)` guard,
    `liveElapsed = {}; liveElapsed[ev.node] = 0;` (header shows 0:00
    until the first heartbeat, which arrives within 2.0s).
  - `node_finished` case: after the `nodeIds.has(ev.node)` guard and
    before `setStageStatus`, accumulate:
    `if (ev.elapsed_seconds !== undefined) { stepTimes[ev.node] =
    (stepTimes[ev.node] || 0) + ev.elapsed_seconds; }`
  - `heartbeat` case: replace the `#elapsed` write with
    `if (nodeIds.has(ev.node) && nodeStatus[ev.node] === "running") {
    liveElapsed[ev.node] = ev.elapsed_seconds; renderStageHeader(ev.node);
    }`
  - `run_finished` case: stop the interval (`clearInterval(totalTimer);
    totalTimer = null;`); if `ev.elapsed_seconds !== undefined`, set
    `runStartWall = null` and freeze `#total` at `"total " +
    mmss(ev.elapsed_seconds)` (stays visible).
  - All new string literals are ASCII ("total ", " ").

### Demo parity: `viz_demo.py`

- `emit_node`: add `"elapsed_seconds": int(time.monotonic() - started)`
  to the `node_finished` publish (line 87). Heartbeats already carry
  `elapsed_seconds`, so the running-stage live time works unchanged.
- `run_scenario`: capture `scenario_start = time.monotonic()` before
  publishing `run_started`; add `"started_at": time.time()` to the
  `run_started` publish (lines 98-99); add
  `"elapsed_seconds": int(time.monotonic() - scenario_start)` to both
  `run_finished` publishes (lines 111, 128).

## Tests (TDD: red, then green, then refactor)

1. `tests/test_graph.py::TestVizStream` (new):
   - `test_run_events_carry_timings`: scripted all-pass run with
     `viz_bus`; drain events. Assert `run_started["started_at"]` is a
     number within the test's `time.time()` window; `run_finished` has
     an int `elapsed_seconds >= 0`; every `node_finished` has an int
     `elapsed_seconds >= 0`.
   - Existing `TestTranslate` assertions stay green unchanged, proving
     enrichment lives in `graph.py`, not `translate`.
2. `tests/test_viz.py::TestServer` (substring tests):
   - Update `test_page_constant_clears_elapsed` (767-769) to
     `test_page_constant_has_total`: assert `'id="total"'` in `PAGE`
     (the `id="elapsed"` assertion goes away with the rename).
   - `test_page_constant_has_heartbeat` (762-765) stays green as-is
     ("elapsed" still appears via `elapsed_seconds` keys).
   - New `test_page_constant_has_step_time`: `"stepTimes"`,
     `".stage-time"`, `"stage-time"`, `"liveElapsed"` in `PAGE`.
   - New `test_page_constant_has_loop_total`: `"renderTotal"`,
     `"started_at"`, `"setInterval"`, `"clearInterval"`, `"total "` in
     `PAGE`.
3. `tests/test_viz.py::TestPageScriptBehavior` (harness additions plus
   four new tests):
   - Harness: replace `"elapsed"` with `"total"` in `STATIC_IDS` (line
     936); add stubs
     `globalThis.setInterval = function (fn) { intervalFn = fn; return
     1; }; globalThis.clearInterval = function () {};` (capture
     `intervalFn` in a var); add `var fakeNow = 0; Date.now =
     function () { return fakeNow; };`.
   - Harness snapshot extension: add helpers `stageTimes()` (map of
     stage id to `.stage-time` textContent) and `totalText()`
     (`#total` textContent and display style); include `times` and
     `total` in every snapshot push (the `state` snapshots at line 1023
     and the final snapshot at line 1031). Existing tests ignore the
     extra keys.
   - `test_step_header_shows_accumulated_time`: run_started
     (started_at=1000), node_started dev, node_finished dev
     elapsed_seconds 135, state pass, node_finished dev elapsed_seconds
     45 (retry), state pass. Assert the stage-dev header's `.stage-time`
     is `"3:00"` (135 + 45) and a never-run node's `.stage-time` is
     empty.
   - `test_running_stage_shows_live_heartbeat_time`: run_started,
     node_started dev, heartbeat dev elapsed_seconds 27, state
     `{"attempt": 1, "failed": false}` (snapshot trigger). Assert the
     stage-dev `.stage-time` is `"0:27"`; then node_finished dev
     elapsed_seconds 28 plus a state event, assert it reads `"0:28"`
     (accumulated, completed).
   - `test_loop_total_ticks_then_freezes`: run_started started_at=1000;
     set `fakeNow = 1065000` (65s later), invoke the captured
     `intervalFn`, fire a state event, assert `#total` is
     `"total 1:05"`; then run_finished elapsed_seconds 200; assert the
     final snapshot's `#total` is `"total 3:20"`.
   - `test_loop_total_fallback_without_started_at`: run_started without
     `started_at` (existing fixture shape), assert no interval was
     started and `#total` is hidden; run_finished elapsed_seconds 95;
     assert the final snapshot's `#total` is `"total 1:35"`.
   - All existing behavior tests stay green (no `started_at` sent, so no
     interval starts; `STATIC_IDS` rename keeps `el("total")` resolvable
     via the renamed stub).
4. Full pytest run (viz, graph, repo-ascii), then refactor only if
   duplication appears (e.g. a shared `resetTimings()`/`freezeTotal()`).

## Edge cases

- Node finished with no start: no `elapsed_seconds`; frontend ignores.
- Retries and loopbacks: `stepTimes` sums; verdict behavior unchanged.
- Running stage before the first heartbeat (up to 2.0s): seeded 0:00 by
  `node_started`, then updates live.
- Heartbeats are live-only: a tab opened mid-run does not replay them,
  so the running stage's live time appears after the next heartbeat and
  the completed-stage totals are correct immediately from replayed
  `node_finished` events.
- Tab opened mid-run: the backlog replays `run_started` WITH
  `started_at` (wall clock), so the ticking total is correct after
  replay.
- Attempt bumps: no `stepTimes` reset (only `run_started` resets); the
  total keeps ticking from the original `started_at`.
- Runs over an hour: mm:ss rolls past 59:59, matching the existing
  heartbeat line; explicitly out of scope to change.
- Failure path: `finally` still publishes `run_finished` with
  `elapsed_seconds` even when the final state could not be read; the
  frontend freezes the total either way.
- `--no-viz` runs: no events, no impact.
- After `run_finished`, the total stays visible (previously the top
  line disappeared via `clearElapsed`); this is the requested behavior.

## Constraints and notes

- All added source must be ASCII; this doc is ASCII. No em dashes
  (`tests/test_repo_ascii.py` plus the pre-commit hook). The existing
  page text renders an em dash via `\u2014` escapes, which stay as-is.
- No tooling paths are touched (changes are in `src/`, `tests/`, and
  `viz_demo.py`; `pipeline.yaml` and `sdk_agents/` are untouched), so
  the loop commit sweeps the deliverable normally. `viz_demo.py` sits
  at the repo root and is not a tooling path.
- Backward compatibility: all new event keys are additive;
  `translate()` output is unchanged, so other `translate` consumers are
  unaffected. The `#elapsed` div rename is internal to the page; no
  external consumer references it.
- TDD is required: write the red tests above before implementation.

## Verification checklist (pre-completion)

- [ ] pytest green: `tests/test_viz.py`, `tests/test_graph.py`,
      `tests/test_repo_ascii.py`.
- [ ] Manual demo: `python viz_demo.py --no-browser`; the top readout
      shows "total mm:ss" ticking and then freezing; the running stage's
      title bar ticks live; completed stages show accumulated times.
- [ ] A live CLI run with viz shows the same three behaviors.
- [ ] Existing page tests still green (no regressions in verdict
      badges, console capture, copy button).
