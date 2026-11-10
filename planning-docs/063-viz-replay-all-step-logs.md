# Plan 063: Viz replays every step's console log on connect, not just the current one

> **Status: COMPLETED**

## Problem

`VizBus` retains console lines in a single global backlog:
`self._console_backlog: deque = deque(maxlen=console_maxlen)` with
`console_maxlen=500` (plan 008's "replay split" design). A run whose steps
print more than 500 lines in total therefore retains only the most recent
500 lines, which are almost always the currently running (or last) step's
log. A tab that connects or refreshes mid-run replays only that step's
console; every earlier step's stage section shows "No output". The request:
on first connect, replay the logs for **all** steps, each into its own
stage console.

## Scope

One change in the backend bus, plus tests and docs. No frontend change.

- `src/agentic_dev_loop/viz/server.py`: per-node console retention in
  `VizBus` (the only file changed).
- `tests/test_viz.py`: red tests first, then green.
- `CHANGELOG.md`: one entry.
- This plan and its status flip.

Out of scope: the `/runs` history view (it never carried per-step console
logs, disk persistence of console output is a separate feature), replay
across runs (the bus is created fresh per run, see facts), and any change
to lifecycle retention.

## Context (verified facts)

- `VizBus.__init__` (server.py lines 173-178): `maxlen=500` lifecycle
  backlog, `console_maxlen=500` console backlog, `_seq` counter.
- `VizBus.publish` (lines 180-191): stamps `_seq` under the lock, routes
  `type == "console"` events to the console backlog, drops heartbeats from
  retention, and fans out live to subscribers.
- `VizBus.subscribe` (lines 193-202): replays the two backlogs merged in
  `_seq` order, then registers the subscriber. This is the only replay
  path; the SSE handler (`_events`, lines 494-516) writes the queue
  verbatim, so a bus-level change reaches the page unchanged.
- Console events carry a `node` attribute (set from `NodeWatch.current()`)
  or no node at all (idle output). `ConsoleCapture._publish` (lines
  299-307) builds `{"type": "console", "node": ..., "stream": ...,
  "text": ...}`.
- Frontend routing already supports per-node replay: `handleConsole`
  (page.js lines 604-611) sends `ev.node` lines to
  `ensureSection(ev.node)`'s stage console, and unattributed lines to the
  run log. The 006 race fix buffers all replayed events until topology is
  ready, and `clearConsoles()` on replayed `run_started` resets the
  sections first, so replay order (run_started, node events, console
  lines) already renders correctly. No page.js change is needed.
- The frontend caps each stage at `MAX_CONSOLE = 500` lines (page.js line
  30, `appendConsole` lines 576-602) with a "...N lines omitted" row, so
  server-side retention of the last 500 lines per node reproduces exactly
  what a tab open the whole run would have shown.
- The bus is created per run in `engine/graph.py` (`viz.VizBus()` at line
  288, run events published at 304-359), so retention never leaks between
  runs or into resumed runs.
- Existing tests that must stay green unchanged:
  - `TestVizBus.test_console_spam_evicts_only_console` (test_viz.py lines
    289-302): 600 node-less console events between `run_started` and
    `node_started`; replay keeps `run_started`, the last 500 console
    lines, `node_started`, in order.
  - `TestVizBus.test_console_maxlen_is_configurable` (lines 304-312):
    node-less console events with `console_maxlen=2` replay the last 2.
  - `TestVizBus.test_late_subscriber_gets_backlog_in_order`,
    `test_backlog_evicts_oldest_beyond_maxlen`,
    `test_heartbeat_live_only_not_replayed`, and
    `TestServer.test_sse_replays_backlog` (line 649) all keep their
    semantics.
- Both existing console tests publish events with no `node` key, so
  treating the missing node as its own retention bucket (`None`) keeps
  them green verbatim.

## Design

Replace the single console deque with per-node buckets:

- `self._console_backlogs: dict[str | None, deque]` maps the console
  event's `node` (missing node -> `None`) to a deque bounded by
  `console_maxlen`. Buckets are created lazily on first publish for a
  node, so the map stays bounded by the pipeline's step count.
- `publish()`: console events append `(seq, event)` to their node's
  bucket (default 500 per bucket).
- `subscribe()`: merge the lifecycle backlog plus every bucket's items in
  `_seq` order, exactly like today's sorted merge.
- The constructor keeps its signature `VizBus(maxlen=500,
  console_maxlen=500)`; the **meaning** of `console_maxlen` changes from
  "most recent console lines globally" to "most recent console lines per
  node (and per unattributed bucket)". The docstring states this.
- Lifecycle backlog unchanged (`maxlen=500` is generous: each step emits
  roughly two node events plus one state event, and each `state` event
  carries the full whitelisted `step_verdicts` map, so late tabs rebuild
  verdicts even if extreme retry loops exhausted the window).
- Heartbeats stay live-only, never replayed.

Memory bound: steps x 500 lines each. For a typical pipeline (under 10
steps) that is a few thousand retained lines in the worst case, and each
line is already capped at `MAX_LINE = 8000` chars by `ConsoleCapture`.
The bus dies with the run process. Accepted tradeoff; if it ever matters,
a later plan can add a total replay byte cap without changing this
structure.

Alternatives considered and rejected:

- Unbounded global console deque: simplest, but memory grows for the
  whole run and it abandons the bounded-memory principle of plan 008.
- Frontend fetch-on-demand per stage (`GET /console?node=x`): needs
  server-side retention anyway, plus a new endpoint and new page logic;
  the SSE replay already reaches the same place.
- Raising the global `console_maxlen`: still a single window biased to
  the newest lines; does not guarantee earlier steps' logs survive a long
  current step.

## Tasks

1. **Red** (tests/test_viz.py, `TestVizBus`):
   - `test_console_replay_retains_each_node_separately`: publish
     `run_started`; 600 console lines for node "dev"; 600 for node "qa";
     `node_started` for "qa". A new subscriber receives `run_started`,
     dev's last 500 lines, qa's last 500 lines, and `node_started`, all
     in publish order. Fails today: only qa's lines survive.
   - `test_console_replay_merges_nodes_in_publish_order`: interleave
     console lines across two nodes and a lifecycle event; the replayed
     order follows `_seq` across all buckets.
   - Run the suite to confirm both fail and all existing bus/server tests
     stay green.
2. **Green** (server.py): implement the per-node buckets as designed,
   update the `VizBus` docstring.
3. **Refactor**: confirm `test_console_spam_evicts_only_console` and
   `test_console_maxlen_is_configurable` pass without modification
   (node-less events -> the `None` bucket), then re-run the full
   `tests/test_viz.py` module plus the ASCII guard (`tests/test_repo_ascii.py`).
4. **Docs**: CHANGELOG.md entry describing the behavior change.
5. **Verification**: `./run-demo.sh` or a real pipeline with two
   chatty steps; open the viz page mid-run (or after run start with the
   first step already finished and >500 lines printed): every stage
   section must contain its step's output when expanded, not just the
   current step.

## Risks / notes

- Replay payload grows to steps x 500 lines; the SSE handler writes on
  its own thread and per-subscriber queues are already unbounded by
  design (plan 008), so a larger initial burst is the intended behavior.
- A single step that prints thousands of lines still replays only its
  last 500, matching the live view's own per-stage cap. Replay fidelity
  equals live-view fidelity by construction.
- `serve_history` constructs a plain `VizBus()` and is unaffected.
- Nothing in this plan is a tooling path; the loop commit sweeps all
  deliverables.
