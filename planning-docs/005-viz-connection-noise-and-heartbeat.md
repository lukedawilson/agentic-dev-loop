# Viz connection-noise silence + running-node heartbeat - Implementation Plan

> **Status: COMPLETED** (2026-10-23) - shipped.

> **For agentic workers:** Use bite-sized task execution to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two defects surfaced during the first real long `--viz` loop run (the consumer, plan 017, 2026-09-01):

1. **Browser disconnects dump tracebacks into the loop's terminal.** A client that resets a keep-alive connection while the server is blocked reading the next request line raises `ConnectionResetError` from `handle_one_request → rfile.readline(65537)`. `viz.py` catches resets only on the SSE body path (`_events`); the plain request path has no catch, so `socketserver` prints `Exception occurred during processing of request` + a full traceback - noise that reads as a failure. Verified: traceback captured from the live loop's terminal; server and pipeline unaffected (subsequent `/topology` and `/events` requests served fine).
2. **A long-running node looks dead.** Events fire only at node boundaries (LangGraph `tasks`/`updates` stream parts translated in `run_pipeline`). The observed `dev` node ran 26+ minutes emitting nothing between `node_started` and its next event - the backlog replay was exactly `run_started` + `node_started(dev)`, while the process was provably alive (CPU time climbing, SDK child alive). The page shows a static "dev running" with no sign of life, indistinguishable from a hang.

**Architecture:**

- **Fix 1** is a one-method change in `viz.py`'s `Handler`: override `handle_one_request` to treat `ConnectionResetError`/`BrokenPipeError` as normal connection teardown (client went away) - return without raising. Everything else re-raises. Rejected alternative: overriding the server's `handle_error` to filter - that only redirects the noise; a client reset is not a server error at all. The `_events` catch at `viz.py:182` stays as is (it covers the SSE body path; the override covers the request-line path).
- **Fix 2** adds a liveness heartbeat: a `NodeWatch` (current node + start time, written by the stream-consumer thread, read by the heartbeat thread) and a daemon `HeartbeatThread` publishing `{"type": "heartbeat", "node": <name>, "elapsed_seconds": <int>}` every 2 s while a node is running. Both live in `viz.py` (protocol owner); `run_pipeline`'s stream loop updates the watch as it publishes `node_started`/`node_finished` events and stops the thread in the same `finally` that publishes `run_finished`. The page renders an elapsed line (`dev running - 26:14 elapsed`) that clears on `node_finished`/`run_finished`.

**Tech Stack:** unchanged - Python ≥3.12, pytest, LangGraph 1.2.9 (repo venv), stdlib-only server, Mermaid.js v11 via CDN. No new dependencies.

## Global Constraints

- TDD red→green→refactor for every task
- Mock policy unchanged (repo rule): no new seams; `PAGE` changes covered by the existing string-assertion pattern (`test_page_constant_*`); server changes covered in `tests/test_viz.py`
- Full suite green at the end of every task: `.venv-sandbox/bin/python -m pytest tests/ -q`
- Protocol amendment (documented in 001's style): new `heartbeat` event. All existing event shapes unchanged.

## Locked decisions (from user)

| Decision | Choice |
|---|---|
| Scope | Exactly fixes 1 + 2. No other viz changes. |
| Implementation timing | Plan + commit + push only (2026-09-01). No code until an explicit go. |
| Heartbeat interval | 2 s (constant in `viz.py`, not config-driven) |
| Heartbeat event shape | `{"type": "heartbeat", "node": <name>, "elapsed_seconds": <int>}` - `translate()` does not emit it; it is published by the heartbeat thread only |
| Noise fix level | `Handler.handle_one_request` override (not server-level `handle_error` filtering) |
| Elapsed display | Sidebar line `dev running - 26:14 elapsed` (mm:ss), shown while `nodeStatus[node] === "running"`, cleared on `node_finished`/`run_finished` |
| `viz_demo.py` | Publishes heartbeats during its scripted nodes so the elapsed UI is eyeballable without a real LLM run |

---

### Task 1: Silence connection-reset noise on the request path

**Files:**
- Modify: `src/agent_engine/viz.py` (`Handler` in `_handler_class`)
- Test: `tests/test_viz.py`

**Interface:**
- `Handler.handle_one_request(self)` override: `try: super().handle_one_request() except (ConnectionResetError, BrokenPipeError): return` - a reset/broken pipe on the request-line read means the client left; the connection closes via the enclosing `handle()` loop. Any other exception re-raises unchanged.

- [ ]**Step 1: Failing tests** - (a) unit: build a `Handler` instance (from `_handler_class`) whose fake `rfile.readline()` raises `ConnectionResetError`; assert `handle_one_request()` returns without raising. (b) propagation: fake raising `ValueError` → `pytest.raises(ValueError)`. (c) integration: real `serve_viz` server; raw-socket client connects and resets (SO_LINGER 0, close without sending a request line); `sys.stderr` captured via monkeypatch; after the request thread settles, assert `"Exception occurred during processing"` not in the captured stderr.
- [ ]**Step 2: Implement; green + refactor.**

### Task 2: `NodeWatch` + `HeartbeatThread`, wired into `run_pipeline`

**Files:**
- Modify: `src/agent_engine/viz.py` (new `NodeWatch`, `HeartbeatThread`), `src/agent_engine/graph.py` (`run_pipeline` stream loop)
- Test: `tests/test_viz.py`

**Interface:**
- `NodeWatch`: `start(node)`, `finish(node)`, `current() -> str | None`, `elapsed_seconds() -> int | None`. Lock-protected; `start`/`finish` are called only by the stream-consumer thread.
- `HeartbeatThread(watch, bus, interval=2.0)`: daemon; publishes `{"type": "heartbeat", "node": watch.current(), "elapsed_seconds": watch.elapsed_seconds()}` while `current()` is not `None`; `stop()` sets an `Event` checked each loop iteration. Publishes nothing when no node is running. Last-writer-wins on overlapping starts (the graph is sequential, so this is a documented non-issue).
- `run_pipeline` (inside `if bus is not None:`): as it publishes each translated event, `node_started` → `watch.start(node)`, `node_finished` → `watch.finish(node)`; start the heartbeat thread before the stream loop, `stop()` it in the existing `finally` (before/alongside `run_finished`).

- [ ]**Step 1: Failing tests** - (a) `NodeWatch`: current/elapsed None before start; elapsed grows after start; finish clears. (b) `HeartbeatThread` with `interval=0.01` and a stub watch: subscriber queue receives ≥2 heartbeat events with the exact shape (`type`/`node`/`elapsed_seconds`) while running; after `stop()`, no further events within a short settle window. (c) `run_pipeline` wiring: existing e2e/CLI tests still green (no behavior change without a bus).
- [ ]**Step 2: Implement; green + refactor.**

### Task 3: Page renders the elapsed line

**Files:**
- Modify: `src/agent_engine/viz.py` (`PAGE` JS + a small sidebar element)
- Test: `tests/test_viz.py`

**Interface:**
- Sidebar: an `#elapsed` line under the attempt row (hidden by default).
- JS `handle()`: `case "heartbeat"`: if `nodeStatus[ev.node] === "running"`, set `#elapsed` text to `ev.node + " running - " + mmss(ev.elapsed_seconds) + " elapsed"` (mm:ss helper) and show it. `node_finished` and `run_finished`: clear and hide `#elapsed`.

- [ ]**Step 1: Failing tests** - string assertions: `"heartbeat"`, `"elapsed_seconds"`, `"elapsed"` in `PAGE`; the elapsed line is cleared on `node_finished` (assert the clearing branch string exists).
- [ ]**Step 2: Implement; green + refactor.**

### Task 4: `viz_demo.py` publishes heartbeats

**Files:**
- Modify: `viz_demo.py`
- Test: `tests/test_viz.py` (demo event assertions, existing pattern)

**Interface:**
- `emit_node` starts a short-lived heartbeat thread (or publishes 1–2 heartbeat events inline around the existing sleeps) so the elapsed line appears during the demo's scripted nodes.

- [ ]**Step 1: Failing tests** - demo event stream includes ≥1 `heartbeat` event (existing demo-assertion pattern).
- [ ]**Step 2: Implement; green + refactor.**

### Task 5: Manual smoke

**Files:** none

- [ ]**Step 1:** `./run-demo.sh --no-browser` - during each scripted node the sidebar shows `X running - 0:0N elapsed`, cleared at node completion; no `Exception occurred during processing` traceback when the browser tab is closed mid-demo or when curl probes are aborted.
- [ ]**Step 2:** Real long-run eyeball (user-triggered, makes LLM calls): a construct run's `dev` node shows the ticking elapsed line; heartbeat frames are visible in the browser dev-tools EventSource stream at ~2 s cadence.

## Verification

1. `.venv-sandbox/bin/python -m pytest tests/ -q` green.
2. Manual smoke (Task 5) - **user eyeballs.**
3. Real `agent-engine <pipeline> --plan <doc>` run - **user triggers**: elapsed line ticks during long nodes; no request-path tracebacks on tab close/refresh.

## Amendments

- 2026-09-01: `heartbeat` event added to the protocol (001's protocol table predates it): `{"type": "heartbeat", "node": <name>, "elapsed_seconds": <int>}`. All existing event shapes unchanged.
