# 053: Viz page stream watchdog (zombie EventSource recovery)

> **Status: COMPLETED**
behind the repo ask-before-coding gate.

## Problem

The viz page can silently stop updating mid-run while the engine and viz
server stay perfectly healthy. Investigated live on 2026-11-02 during the
plan-052 dogfood run (engine PID 62426, viz on 127.0.0.1:8321). Two
occurrences, same signature.

Observed symptoms: page fully interactive (expanding, resizing, verdict
badges render), the loop-total wall clock keeps ticking, but the running
stage's live time freezes, its console stops updating, and the run-log
panel has no entry at all. The terminal continues to show output, and a
second tab on the same URL (Chrome) updates fine. DevTools shows no
errors. A 60s `lsof` watch showed zero browser connections and zero
reconnect attempts against the healthy LISTEN socket.

Root cause chain (established by code reading plus Chromium source
review): the Brave tab was backgrounded while the user worked in the
terminal, Chromium froze the tab, and the TCP socket to the viz server
was torn down silently below the loader layer. The loader never reported
the failure, so `EventSource` never fired `onerror` and never scheduled
its built-in reconnect. The `EventSource` stays in `readyState OPEN`
forever: a zombie connection. Blink schedules reconnects only from
`networkRequestEnded()` (loader `didFinishLoading`/`didFail` paths); it
has no socket-polling timer, so a below-the-loader teardown is invisible
to it.

Not a regression in this repo's code: the page never had any recovery
path. The trigger is a usage pattern (tab backgrounded long enough to
freeze, thawed while a long run is still active). The page relies
entirely on Chromium's built-in EventSource retry, which this failure
mode bypasses.

## Scope

Page-side only: the embedded `PAGE` constant in
`src/agentic_dev_loop/viz/server.py`. No server or engine code changes.

The page gains a watchdog: it detects "no event arrived for 10s while a
run is active" and recovers by closing the dead `EventSource` and
opening a fresh one (the fresh connection receives the full backlog
replay and rebuilds the page). Two triggers: the existing 1s total
render interval, and a `visibilitychange` listener (the primary trigger
for the freeze-thaw incident, since frozen tabs do not run timers).

Out of scope: server-side SSE socket write timeout hardening (a
different, hypothetical failure; the server was exonerated in this
incident), any change to `viz_demo.py`, any engine or graph change.

## Context

Verified facts:

- The page creates exactly one `EventSource` (`var es = new
  EventSource("/events")`, server.py line 1197) and sets `es.onerror`
  (line 1206) to log "event stream interrupted". There is no
  `es.close()`, no recreate, no `visibilitychange`/`online`/`offline`/
  `pageshow`/`pagehide` listener, no watchdog, no reload anywhere in the
  page script. This was verified by grep and code reading.
- Heartbeats are published every 2.0s while a node runs
  (`HeartbeatThread`), so during a live run any 10s gap in received
  events is conclusive evidence the stream is dead, regardless of
  `readyState`. The zombie mode keeps `readyState` at OPEN, so the
  watchdog must not consult `readyState`.
- `renderTotal()` (line 612) ticks `#total`; its interval is started
  inside the `run_started` case (line 1061), currently only when
  `run_started` carries `started_at` (plan 051 D4 fallback). `run_started`
  also calls `clearConsoles()` and `resetVerdicts()` (lines 1063-1064),
  which is how a fresh connection's replay rebuilds the page.
- The `run_finished` case (line 1139) stops the interval and freezes
  `#total`.
- `handle(ev)` is the event dispatcher (line 1052); `onmessage` calls
  it. Stamping time there (or in the onmessage wrapper) covers every
  event type.
- `tests/test_viz.py` has two relevant layers: substring assertions on
  `PAGE` (`TestServer.test_page_constant_*`) and real execution of the
  page script under node with a DOM shim (`TestPageScriptBehavior`,
  HARNESS lines 862-1078). Harness facts that matter here:
  - `globalThis.EventSource` is stubbed (line 1011): it keeps only the
    latest instance in `esInstance` and has no `close()` and no instance
    list. The watchdog needs both.
  - `globalThis.setInterval` captures `intervalFn` (line 1007);
    `Date.now` is faked as `fakeNow` (lines 1009-1010); `__tick` events
    advance `fakeNow` and invoke `intervalFn` (main loop lines
    1054-1058). `test_loop_total_ticks_then_freezes` already drives the
    ticker this way.
  - `window.addEventListener` is a no-op stub (line 969); it must
    capture listeners so tests can fire `visibilitychange`.
  - There is no `window.location` stub; `location.reload()` needs one
    for the last-resort reload path.
  - `STATIC_IDS` (line 949) pre-creates stub elements by id; the banner
    div id must be added there.
  - `fire(ev)` (line 1031) dispatches `esInstance.onmessage`.
- `tests/test_viz.py::TestServer` already has the marker-test style
  (`test_page_constant_has_heartbeat`, `test_page_constant_has_total`,
  etc.) for pinning new page features.
- The backend replay guarantees recovery works: `VizBus.subscribe()`
  replays the backlog (non-console, non-heartbeat, maxlen 500) before
  live events, and `run_started` is the first replayed event. A fresh
  connection mid-run therefore rebuilds the whole page state
  (run_started, node_started/finished pairs, state snapshots, last-500
  console lines).
- All committed content must be ASCII (em dash ban;
  `tests/test_repo_ascii.py` plus a pre-commit hook).

## Design decisions

**D1. Silence detection, not readyState.** Every received event stamps
`lastEventAt = Date.now()` at the top of the onmessage handler. While a
run is active (`runActive`, set by `run_started`, cleared by
`run_finished`), `Date.now() - lastEventAt > 10000` means the stream is
dead. 10s is five missed heartbeats: comfortably beyond the 2.0s
cadence, short enough to notice quickly. `es.readyState` is never
consulted (zombies stay OPEN).

**D2. Detection rides the existing interval.** The `run_started` case
starts `totalTimer = setInterval(renderTotal, 1000)` unconditionally
while a run is live (currently gated on `started_at`). `renderTotal()`
already hides `#total` when `started_at` is absent, so its rendering
behavior is unchanged; the interval now also runs the watchdog check:
`if (runActive && Date.now() - lastEventAt > 10000) { recover(); }`.
This is a deliberate, documented change to plan 051's fallback: the
interval now runs during live runs even without `started_at`. The
existing test `test_loop_total_fallback_without_started_at` asserts
`timer is False` in that case; it must be updated to assert the timer
runs and the total stays hidden. No new timers are added.

**D3. Recovery = close and recreate.** `recover()` shows the banner,
increments `reconnectAttempts`, and if `reconnectAttempts > 3` calls
`location.reload()` (last resort). Otherwise it calls `es.close()` and
creates a fresh `EventSource("/events")` via a shared `connectStream()`
helper, then sets `lastEventAt = Date.now()` so the fresh connection
gets the full 10s grace period before the watchdog can fire again. The
fresh connection replays the backlog, and the replayed `run_started`
rebuilds the page via the existing `clearConsoles()`/`resetVerdicts()`
path. Any event received resets `reconnectAttempts = 0` and hides the
banner. If no event arrives within the grace period (server down,
network gone), the watchdog fires again and the attempts counter climbs
toward reload.

**D4. `visibilitychange` is the primary trigger for the incident
scenario.** Frozen tabs do not run timers, so the interval cannot catch
the freeze-thaw zombie. Register
`document.addEventListener("visibilitychange", function () { if
(runActive && Date.now() - lastEventAt > 10000) { recover(); } })`.
The check is symmetric (fires on any visibility change, tests
`lastEventAt`), so no `document.hidden` read or hidden-tracking is
needed: if the tab was merely backgrounded but not frozen, events kept
arriving and `lastEventAt` is fresh, so nothing happens. The interval
remains the catch-all for foreground stalls (silent socket death while
visible).

**D5. Watchdog only during active runs.** Between page load and the
first `run_started`, and after `run_finished`, silence is legitimate
(no heartbeats flow when no node runs). `runActive` gates every
detection path, so a freshly opened page with no run in progress never
reloads or reconnects.

**D6. Visible banner.** A fixed banner div
(`<div id="stream-banner">`, hidden by default) with ASCII text
"connection lost, reconnecting...". Shown by `recover()`, hidden on the
next received event. Positioned at the top of the page body (inserted
right after `<body>` at line 527), high z-index, distinct background so
it is obvious over the dark page.

**D7. Accepted mid-run reset.** The replay's `run_started` resets the
page (consoles cleared and rebuilt from the last-500 backlog, verdicts
reset, step times reset per plan 051). A mid-run recovery therefore
swaps the live-scrolling console for the backlog view of the same
console and the run continues live from there. Accepted tradeoff: a
brief, visible reset beats a silently dead page, and the reconnect
banner plus the replay make the reset legible. Documented, not hidden.

**D8. Shared `connectStream()` helper.** The current inline
`var es = new EventSource(...)` plus handler assignment (lines
1197-1210) moves into one function that creates the EventSource, wires
`onmessage` (stamp, reset attempts, hide banner, then `handle(ev)`) and
`onerror` (existing `logLine("event stream interrupted")`), and returns
it. `recover()` reuses it, avoiding duplicated handler wiring.

## Changes

Only `src/agentic_dev_loop/viz/server.py` (inside `PAGE`):

- HTML: add `<div id="stream-banner"></div>` right after `<body>` (line
  527), plus its CSS rule (hidden by default, fixed, top, centered,
  high z-index, warning background, ASCII text content).
- JS state: `var lastEventAt = 0; var reconnectAttempts = 0; var
  runActive = false;`
- New `connectStream()` per D8; replace line 1197 and the surrounding
  handler assignments with `var es = connectStream();`.
- `onmessage` wrapper stamps `lastEventAt`, resets attempts, hides the
  banner, then calls `handle(ev)`.
- `run_started` case (line 1054): set `runActive = true`; start
  `totalTimer` unconditionally (remove the `runStartWall !== null`
  gate at line 1061); keep `renderTotal()` hiding `#total` when
  `started_at` is absent.
- `run_finished` case (line 1139): set `runActive = false` (interval
  already stopped there).
- `renderTotal()` (line 612): after the existing total logic, run the
  D2 watchdog check.
- New `recover()` per D3.
- New `visibilitychange` listener per D4 (registered at script scope).
- All new string literals are ASCII.

No changes to `translate()`, the bus, the server handler, the graph
stream loop, `viz_demo.py`, or any test fixture outside
`tests/test_viz.py`.

## Tests (TDD: red, then green, then refactor)

Harness additions in `TestPageScriptBehavior.HARNESS`
(`tests/test_viz.py`):

1. `EventSource` stub: add `this.close = function () { closeCalls++; }`;
   push every instance to `esInstances` (keep `esInstance` = latest so
   `fire()` targets the newest stream, which is the correct semantic
   after a recreate).
2. Capture listeners: change `window.addEventListener` to record into
   `window._listeners[type]` (keep the no-op default); add
   `window.location = { reload: function () { reloadCalls++; } }`.
3. Add `"stream-banner"` to `STATIC_IDS` (line 949) so
   `el("stream-banner")` resolves.
4. Expose `closeCalls`, `reloadCalls`, `esInstances` on globalThis (or
   via snapshot helpers) for assertions.

New behavior tests (red first):

1. `test_silence_while_run_active_recreates_stream`: run_started (with
   started_at), node_started dev, a heartbeat, then `__tick` events
   advancing `fakeNow` past 10s with no events. Assert
   `esInstances.length >= 2`, the first instance's `close` was called,
   the banner is visible (its `style.display` is not "none"), and no
   reload happened.
2. `test_silence_when_idle_does_not_recreate`: run_started then
   run_finished, then ticks far past 60s. Assert `esInstances.length
   == 1`, banner hidden, no reload.
3. `test_recovery_rearms_after_replay`: trigger a recreate as in test
   1, then fire the replay (run_started, node_started) through the new
   instance, then tick 9s. Assert no second recreate and the banner is
   hidden again. Then tick past 10s: assert a second recreate (proving
   the watchdog re-armed after the replay).
4. `test_repeated_failures_reload`: run_started, node_started, then
   repeatedly tick past the threshold with no events arriving; after
   the fourth fire (attempts > 3), assert `reloadCalls == 1` and no
   further recreates were attempted after reload.
5. `test_visibility_return_triggers_recovery`: run_started,
   node_started, heartbeat; advance `fakeNow` past 10s without ticks;
   invoke the captured `visibilitychange` listener; assert one
   recreate. Companion assertion: fire the listener immediately after
   a fresh event (recent `lastEventAt`) and assert zero recreates.
6. Update `test_loop_total_fallback_without_started_at`: the timer
   assertion flips to `True` (interval runs per D2) while `#total`
   stays hidden, matching the new D2 contract.

Marker tests in `TestServer` (same style as existing
`test_page_constant_*`):

7. `test_page_constant_has_stream_watchdog`: `"stream-banner"`,
   `"connection lost"`, `"visibilitychange"`, `"location.reload"`,
   `"lastEventAt"`, and at least two `"new EventSource"` occurrences
   (creation plus reconnect) in `PAGE`.
8. Existing page tests stay green: verdict badges, console capture,
   copy button, step times, heartbeat display.

## Edge cases

- Run finishes while the page is disconnected: the page missed
  `run_finished`, so `runActive` stays true and the watchdog keeps
  firing. The fresh connection replays the backlog ending in
  `run_finished`, which clears `runActive` and stops the cycle. Self
  healing. If the server is down, reloads continue until it is back.
- Tab backgrounded but not frozen: heartbeats keep arriving,
  `lastEventAt` stays fresh, no false recovery.
- Tab frozen while idle (no active run): `runActive` false, nothing
  fires on thaw.
- Recovery during topology-not-ready: the `pendingEvents` buffer
  already handles pre-topology events; the fresh stream replays
  regardless.
- Backlog truncation: a very long disconnect can evict the tail of the
  500-event backlog; the replay then starts from the surviving events.
  The page still rebuilds from what the server retained.
- Heartbeats are live-only (never replayed): after a reconnect the
  running stage's live time appears again with the next heartbeat
  (within 2s); its accumulated `stepTimes` come from replayed
  `node_finished` events (plan 051 behavior, unchanged).
- Double recovery (interval and visibilitychange firing near
  simultaneously): both paths funnel through `recover()`, which bumps
  the shared attempts counter; worst case is one extra recreate, never
  two live EventSources (the old one is closed before the new one is
  created).
- `--no-viz` runs: no page, no impact.

## Constraints and notes

- All added source and this doc are ASCII; no em dashes.
- Page-side only: `src/agentic_dev_loop/viz/server.py` (the `PAGE`
  constant) and `tests/test_viz.py`. No tooling paths, no
  `pipeline.yaml`, no `sdk_agents/` changes.
- TDD is required: harness extensions and red tests above before
  implementation, green, then refactor only if duplication appears.
- Manual smoke verification (the Chromium freeze itself cannot be
  automated here): run `python viz_demo.py`, open the page, then kill
  the demo server process; the page should show the banner and
  reconnect. Restart the demo server; the page should rebuild from the
  replay and continue live. A freeze-thaw reproduction is best-effort:
  background the tab for several minutes during a long run, then
  foreground it and confirm the banner plus recovery instead of a dead
  page.
