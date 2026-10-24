"""Live graph visualization: StreamPart translation, a thread-safe event
bus, a config-driven topology renderer, and a stdlib-only HTTP/SSE server
serving an embedded Mermaid page.

The graph itself is untouched - every live update is derived from LangGraph's
``tasks``/``updates`` stream modes. The viz surface is served by default
(suppressed with ``--no-viz``); the
server is stdlib-only (TS-4): the HTTP/SSE server uses only the standard
library, and the Mermaid topology source is assembled directly from the
pipeline config - no LangChain renderer - while the page loads Mermaid.js v11
from a CDN.
"""

from __future__ import annotations

import errno
import json
import os
import queue
import sys
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agentic_dev_loop.core.config import PipelineConfig, Route, route_target

STATE_WHITELIST = ("attempt", "step_verdicts", "outcome", "failed")


@dataclass(frozen=True)
class _Edge:
    """One flowchart edge (stdlib-only replacement for LangChain's `Edge`)."""

    src: str
    dst: str
    label: str | None = None
    conditional: bool = False


def _render_mermaid(edges: list[_Edge]) -> str:
    """Assemble the Mermaid source for a list of edges - the exact string the
    old LangChain `draw_mermaid(nodes={}, ...)` renderer produced: a bare
    ``graph TD;`` header (no node declarations, no stadium shapes, no
    terminals), then one tab-indented edge line each."""
    lines = ["graph TD;"]
    for edge in edges:
        if edge.conditional:
            if edge.label:
                lines.append(f"\t{edge.src} -. &nbsp;{edge.label}&nbsp; .-> {edge.dst};")
            else:
                lines.append(f"\t{edge.src} -.-> {edge.dst};")
        else:
            lines.append(f"\t{edge.src} --> {edge.dst};")
    return "\n".join(lines) + "\n"


def topology_mermaid(cfg: PipelineConfig) -> tuple[str, list[str]]:
    """Build a single vertical flowchart for the configured steps.

    Returns ``(mermaid_source, node_ids)``. The happy path is one solid
    ``-->`` chain following each step's ``on_pass``; retries render as dotted
    ``-. label .->`` loop-backs, and a non-retry ``on_fail`` as an unlabeled
    dotted edge. Stdlib-only (TS-4): the source is assembled directly, with
    no node declarations and no terminal stadium shapes - byte-identical to
    the previous LangChain renderer's output.
    """
    chain: list[_Edge] = []
    back_edges: list[_Edge] = []
    referenced: set[str] = set()
    for step in cfg.steps:
        target = route_target(step.on_pass)
        if target is not None:
            chain.append(_Edge(step.name, target))
            referenced.add(target)
        if isinstance(step.on_fail, Route) and step.on_fail.retry:
            if step.verdicts is not None:
                label = step.verdicts.fail
            elif step.command is not None:
                label = "fail"
            else:
                label = "retry"
            back_edges.append(_Edge(step.name, step.on_fail.goto, label=label,
                                    conditional=True))
            referenced.add(step.on_fail.goto)
        else:
            target = route_target(step.on_fail)
            if target is not None:
                back_edges.append(_Edge(step.name, target, conditional=True))
                referenced.add(target)
    node_ids = [step.name for step in cfg.steps]
    for terminal in ("success", "failure"):
        if terminal in referenced:
            node_ids.append(terminal)
    return _render_mermaid(chain + back_edges), node_ids


def translate(part: dict) -> list[dict]:
    """Translate one v2 StreamPart into zero or more viz event dicts.

    ``tasks`` parts pair up by id: a part whose data carries ``triggers`` is a
    node start; one carrying ``error`` is the matching finish (``error`` is
    ``None`` on success). ``updates`` parts carry one LoopState delta per node
    and collapse to a single ``state`` event holding only whitelisted keys -
    ``docs``/``context``/``notes`` are large or internal and never shipped.
    """
    ptype = part.get("type")
    data = part.get("data")
    if not isinstance(data, dict):
        return []
    if ptype == "tasks":
        if "triggers" in data and "name" in data:
            return [{"type": "node_started", "node": data["name"]}]
        if "error" in data and "name" in data:
            return [{"type": "node_finished", "node": data["name"],
                     "ok": data["error"] is None}]
        return []
    if ptype == "updates":
        state: dict = {}
        for delta in data.values():
            if not isinstance(delta, dict):
                continue
            for key in STATE_WHITELIST:
                if key in delta:
                    state[key] = delta[key]
        return [{"type": "state", **state}]
    return []


class VizBus:
    """Thread-safe pub/sub with bounded backlogs.

    Nodes publish from the pipeline thread; each SSE connection consumes from
    its own thread. New subscribers first receive a replay of retained
    (lifecycle) and console events merged in publish order, so a browser tab
    opened mid-run catches up without console volume evicting lifecycle
    events. Heartbeats are delivered live but never replayed.
    """

    def __init__(self, maxlen: int = 500, console_maxlen: int = 500) -> None:
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._backlog: deque = deque(maxlen=maxlen)
        self._console_backlog: deque = deque(maxlen=console_maxlen)
        self._seq = 0

    def publish(self, event: dict) -> None:
        with self._lock:
            self._seq += 1
            seq = self._seq
            etype = event.get("type")
            if etype == "console":
                self._console_backlog.append((seq, event))
            elif etype != "heartbeat":
                self._backlog.append((seq, event))
            subscribers = list(self._subscribers)
        for sub in subscribers:
            sub.put(event)

    def subscribe(self) -> queue.Queue:
        sub: queue.Queue = queue.Queue()
        with self._lock:
            merged = sorted(
                list(self._backlog) + list(self._console_backlog),
                key=lambda item: item[0])
            for _seq, event in merged:
                sub.put(event)
            self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(sub)


MAX_LINE = 8000  # cap per captured console line - SDK state dumps are huge


class _Tee:
    """Write-through proxy for one stream. Forwards every write to ``target``
    unchanged, buffers to line boundaries, and publishes each complete line
    (plus any trailing partial line on detach) as a console event attributed
    to the node NodeWatch currently reports. ANSI escapes pass through
    verbatim - colour parsing is the frontend's job."""

    def __init__(self, capture: "ConsoleCapture", stream: str, target) -> None:
        self._capture = capture
        self._stream = stream
        self._target = target
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, s) -> int:
        if isinstance(s, bytes):
            s = s.decode("utf-8", "replace")
        with self._lock:
            self._target.write(s)
            self._buffer += s
            self._flush_lines()
        return len(s)

    def _flush_lines(self) -> None:
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._capture._publish(self._stream, line.rstrip("\r"))

    def _flush_partial(self) -> None:
        with self._lock:
            if self._buffer:
                self._capture._publish(self._stream, self._buffer.rstrip("\r"))
                self._buffer = ""

    def flush(self) -> None:
        self._target.flush()

    def isatty(self) -> bool:
        return self._target.isatty()

    def __getattr__(self, name):
        return getattr(self._target, name)


class ConsoleCapture:
    """Tees sys.stdout/sys.stderr into the viz bus, line by line, attributed
    to the node NodeWatch currently reports (None when idle).

    While attached, ``COLUMNS`` is forced to ``MAX_LINE`` so that rich-based
    writers (e.g. the SDK's conversation visualizer) do not hard-wrap output
    at a terminal-width fallback: under the capture, stdout is a pipe, so
    rich would otherwise bake 80-column newlines into the console stream that
    the browser's ``pre-wrap`` cannot rejoin. The previous value is restored
    on detach.
    """

    def __init__(self, bus: VizBus, watch: NodeWatch) -> None:
        self._bus = bus
        self._watch = watch
        self._tees: dict[str, _Tee] | None = None
        self._old_columns: str | None = None

    def attach(self) -> None:
        if self._tees is not None:
            raise RuntimeError("ConsoleCapture is already attached")
        self._old_columns = os.environ.get("COLUMNS")
        os.environ["COLUMNS"] = str(MAX_LINE)
        self._tees = {
            "stdout": _Tee(self, "stdout", sys.stdout),
            "stderr": _Tee(self, "stderr", sys.stderr),
        }
        sys.stdout = self._tees["stdout"]
        sys.stderr = self._tees["stderr"]

    def detach(self) -> None:
        if self._tees is None:
            return
        for stream, tee in self._tees.items():
            tee._flush_partial()
            tee.flush()
            setattr(sys, stream, tee._target)
        self._tees = None
        if self._old_columns is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = self._old_columns

    def _publish(self, stream: str, line: str) -> None:
        if len(line) > MAX_LINE:
            line = line[:MAX_LINE] + "…"
        self._bus.publish({
            "type": "console",
            "node": self._watch.current(),
            "stream": stream,
            "text": line,
        })


class NodeWatch:
    """Current running node + start time.

    Written by the stream-consumer thread as it publishes ``node_started`` /
    ``node_finished`` events; read by the heartbeat thread. ``start`` and
    ``finish`` are lock-protected; ``finish`` clears only its own node so a
    stale finish can never clobber a newer running node.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._node: str | None = None
        self._started: float | None = None

    def start(self, node: str) -> None:
        with self._lock:
            self._node = node
            self._started = time.monotonic()

    def finish(self, node: str) -> None:
        with self._lock:
            if self._node == node:
                self._node = None
                self._started = None

    def current(self) -> str | None:
        with self._lock:
            return self._node

    def elapsed_seconds(self) -> int | None:
        with self._lock:
            if self._started is None:
                return None
            return int(time.monotonic() - self._started)


class HeartbeatThread(threading.Thread):
    """Daemon thread publishing a liveness heartbeat while a node runs."""

    def __init__(self, watch: NodeWatch, bus: VizBus,
                 interval: float = 2.0) -> None:
        super().__init__(daemon=True)
        self._watch = watch
        self._bus = bus
        self._interval = interval
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            node = self._watch.current()
            if node is not None:
                self._bus.publish({
                    "type": "heartbeat",
                    "node": node,
                    "elapsed_seconds": self._watch.elapsed_seconds(),
                })
            self._stop_event.wait(self._interval)


def _handler_class(bus: VizBus, topology_mermaid: str,
                   subject: str, nodes: Iterable[str]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        viz_bus = bus
        viz_topology = topology_mermaid
        viz_subject = subject
        viz_nodes = list(nodes)

        def log_message(self, *_args) -> None:  # keep test output quiet
            pass

        def handle_one_request(self) -> None:
            try:
                super().handle_one_request()
            except (ConnectionResetError, BrokenPipeError):
                return

        def do_GET(self) -> None:
            if self.path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE)
            elif self.path == "/topology":
                body = json.dumps({"mermaid": self.viz_topology,
                                   "subject": self.viz_subject,
                                   "nodes": self.viz_nodes})
                self._send(200, "application/json", body)
            elif self.path == "/events":
                self._events()
            else:
                self.send_error(404)

        def _send(self, status: int, content_type: str, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            sub = self.viz_bus.subscribe()
            try:
                while True:
                    try:
                        event = sub.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b":hb\n\n")
                        self.wfile.flush()
                        continue
                    frame = f"data: {json.dumps(event)}\n\n".encode("utf-8")
                    self.wfile.write(frame)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                self.viz_bus.unsubscribe(sub)

    return Handler


class _ReuseSafeServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR permits binding an in-use port, which defeats
    # the busy-port fallback (the OS reassigned the SAME port - CI-red).
    # Disable reuse there; POSIX keeps it for TIME_WAIT rebinding.
    allow_reuse_address = sys.platform != "win32"


def serve_viz(bus: VizBus, topology_mermaid: str, subject: str,
              port: int, nodes: Iterable[str] = ()) -> tuple[_ReuseSafeServer, threading.Thread]:
    """Bind a loopback HTTP server on ``port`` and serve it on a daemon
    thread. Returns ``(httpd, thread)`` - the caller reads the real bound
    port from ``httpd.server_address[1]``.

    ``port`` is preferred, not fixed: when it is already in use
    (``errno.EADDRINUSE``) the server rebinds port 0 and lets the OS pick a
    free port. Any other bind error (e.g. ``EACCES`` on a privileged port)
    still fails loud, naming ``--viz-port``."""
    handler = _handler_class(bus, topology_mermaid, subject, nodes)
    try:
        httpd = _ReuseSafeServer(("127.0.0.1", port), handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE and port != 0:
            httpd = _ReuseSafeServer(("127.0.0.1", 0), handler)
        else:
            raise OSError(
                f"Could not bind the viz server to 127.0.0.1:{port} - the "
                f"--viz-port may already be in use. ({exc})") from exc
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agentic-dev-loop viz</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
         Roboto, Helvetica, Arial, sans-serif; display: flex; height: 100vh; }
  #graph-panel { flex: 1 1 auto; padding: 16px; overflow: auto; display: flex;
                 flex-direction: column; }
  #mermaid-container { flex: 1 1 auto; }
  #mermaid-container svg { max-width: 100%; height: auto; }
  .running > * { animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
  #run-banner { font-size: 18px; font-weight: 700; padding: 10px 14px;
                border-radius: 6px; margin-top: 10px; display: none; }
  .banner-success { background: #e8f5e9; color: #2e7d32; }
  .banner-failure { background: #ffebee; color: #c62828; }
  #divider { flex: 0 0 6px; cursor: col-resize; background: #e0e0e0; }
  #divider:hover, #divider.dragging { background: #9e9e9e; }
  #sidebar { flex: 0 0 320px; border-left: 1px solid #e0e0e0; padding: 16px;
             display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
  #subject { margin: 0; font-size: 16px; word-break: break-word; }
  #attempt { font-size: 14px; color: #555; }
  #thread { font-size: 14px; color: #555; }
  #thread code { font: 12px ui-monospace, SFMono-Regular, Menlo, monospace;
                 background: #f5f5f5; padding: 2px 6px; border-radius: 4px;
                 cursor: pointer; user-select: all; }
  #elapsed { font-size: 14px; color: #1976d2; display: none; }
  #verdicts { display: flex; flex-wrap: wrap; gap: 6px; }
  .badge { font-size: 12px; padding: 3px 8px; border-radius: 10px;
           background: #f0f0f0; color: #333; }
  .badge-pass { background: #e8f5e9; color: #2e7d32; }
  .badge-fail { background: #ffebee; color: #c62828; }
  .badge-unverifiable { background: #ede7f6; color: #4527a0; }
  .badge-unclear { background: #fff8e1; color: #f57f17; }
  #stages { flex: 1 1 auto; min-height: 0; overflow-y: auto; display: flex;
            flex-direction: column; gap: 8px; }
  .stage { border: 1px solid #e0e0e0; border-radius: 6px; overflow: hidden; }
  .stage-header { display: block; width: 100%; text-align: left; cursor: pointer;
                  font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI",
                  Roboto, Helvetica, Arial, sans-serif; padding: 6px 10px;
                  border: 0; background: #f7f7f7; }
  .stage-glyph { display: inline-block; width: 1.1em; }
  .stage-status { margin-left: 0.4em; }
  .status-pending { color: #555; }
  .status-running { color: #1976d2; }
  .status-pass { color: #2e7d32; }
  .status-fail { color: #b26a00; }
  .status-fatal { color: #c62828; }
  .status-unverifiable { color: #7e57c2; }
  .console-wrap { max-height: 240px; overflow-y: auto; background: #1e1e1e; }
  .stage-console { margin: 0; padding: 8px 10px; background: #1e1e1e;
                   color: #e0e0e0; font: 12px/1.5 ui-monospace, SFMono-Regular,
                   Menlo, monospace; white-space: pre-wrap; word-break: break-all; }
  .stage-console .cline { display: block; min-height: 1em; }
  .stage-console .omitted { color: #888; font-style: italic; }
  .stage-console .stage-placeholder { color: #888; font-style: italic; }
  /* float puts the button in the wrap's content box, which the scrollbar
     sits outside of - the scrollbar can never cover it; sticky keeps it
     pinned to the top-right corner while the console scrolls */
  .copy-btn { position: sticky; top: 6px; float: right; display: flex;
              align-items: center; justify-content: center; width: 26px;
              height: 26px; margin: 6px 6px 0 0; padding: 0; border: 0;
              border-radius: 4px; cursor: pointer; background: #333333;
              color: #f2f2f2; }
  .copy-btn:hover { background: #4d4d4d; color: #ffffff; }
  .copy-btn:focus-visible { outline: 2px solid #61d6d6; outline-offset: 1px; }
  .copy-btn svg { display: block; }
  .stage.collapsed .console-wrap { display: none; }
  #run-log { list-style: none; margin: 0; padding: 8px 0 0; flex: 0 0 auto;
             max-height: 25%; overflow-y: auto; border-top: 1px solid #e0e0e0;
             font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  #run-log li { padding: 2px 0; border-bottom: 1px solid #f3f3f3; }
  @media (max-width: 768px) {
    body { flex-direction: column; height: auto; }
    #divider { display: none; }
    #sidebar { flex: 0 0 auto; border-left: 0; border-top: 1px solid #e0e0e0; }
  }
</style>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"
        onerror="window.__mermaidFailed=true"></script>
</head>
<body>
  <div id="graph-panel">
    <div id="mermaid-container"><p>Loading graph…</p></div>
    <div id="run-banner"></div>
  </div>
  <div id="divider"></div>
  <aside id="sidebar">
    <h2 id="subject">agentic-dev-loop pipeline</h2>
    <div id="attempt">attempt ?/?</div>
    <div id="thread">thread id: <code id="thread-id" title="click to select">\u2014</code></div>
    <div id="elapsed"></div>
    <div id="verdicts"></div>
    <div id="stages"></div>
    <ul id="run-log"></ul>
  </aside>
<script>
(function () {
  var nodeStatus = {};
  var stageWord = {};
  var verdictSteps = new Set();
  var nodeIds = new Set();
  var currentVerdicts = {};
  var verdictFail = new Set();
  var verdictPass = new Set();
  var verdictUnverifiable = new Set();
  var fatalNodes = new Set();
  var maxAttempts = null;
  var attempt = null;
  var mermaidSource = null;
  var renderCount = 0;
  var mermaidFailed = !!window.__mermaidFailed;
  var pendingCompletion = null;
  var topologyReady = false;
  var pendingEvents = [];
  var MAX_CONSOLE = 500;
  var PALETTE = ["#0c0c0c", "#c50f1f", "#13a10e", "#c19c00", "#0037da",
                 "#881798", "#3a96dd", "#cccccc"];
  var BRIGHT = ["#767676", "#e74856", "#16c60c", "#f9f1a5", "#3b78ff",
                "#b4009e", "#61d6d6", "#f2f2f2"];
  var COPY_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="14"'
    + ' height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    + ' aria-hidden="true"><rect x="9" y="9" width="13" height="13"'
    + ' rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1'
    + ' 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
  var CHECK_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="14"'
    + ' height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
    + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    + ' aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg>';

  function el(id) { return document.getElementById(id); }

  function logLine(text) {
    var li = document.createElement("li");
    li.textContent = text;
    var log = el("run-log");
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
  }

  function logConsole(html) {
    var li = document.createElement("li");
    li.innerHTML = html;
    var log = el("run-log");
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
  }

  function updateAttempt() {
    el("attempt").textContent = attempt === null
      ? "attempt ?/" + (maxAttempts === null ? "?" : maxAttempts)
      : "attempt " + attempt + "/" + (maxAttempts === null ? "?" : maxAttempts);
  }

  function mmss(totalSeconds) {
    var s = totalSeconds % 60;
    var m = Math.floor(totalSeconds / 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function clearElapsed() {
    var line = el("elapsed");
    line.textContent = "";
    line.style.display = "none";
  }

  function statusOf(n) {
    if (nodeStatus[n] === "running") { return "running"; }
    if (fatalNodes.has(n)) { return "fatal"; }
    if (nodeStatus[n] === "fatal") { return "fatal"; }
    if (verdictFail.has(n)) { return "fail"; }
    if (verdictPass.has(n)) { return "pass"; }
    if (verdictUnverifiable.has(n)) { return "unverifiable"; }
    return nodeStatus[n] || "pending";
  }

  function updateBadges() {
    var box = el("verdicts");
    box.innerHTML = "";
    Array.from(verdictSteps).forEach(function (step) {
      var b = document.createElement("span");
      var v = currentVerdicts[step];
      b.className = "badge";
      if (v === "pass") { b.className += " badge-pass"; }
      else if (v === "fail") { b.className += " badge-fail"; }
      else if (v === "unverifiable") { b.className += " badge-unverifiable"; }
      else { b.className += " badge-unclear"; }
      b.textContent = step + ": " + (v === null || v === undefined ? "no verdict" : v);
      box.appendChild(b);
    });
  }

  function resetVerdicts() {
    verdictSteps.clear();
    verdictPass.clear();
    verdictFail.clear();
    verdictUnverifiable.clear();
    currentVerdicts = {};
    nodeStatus = {};
    stageWord = {};
    pendingCompletion = null;
    updateBadges();
    Array.from(nodeIds).forEach(function (n) { renderStageHeader(n); });
    renderGraph();
  }

  function renderGraph() {
    var container = el("mermaid-container");
    if (mermaidFailed || typeof mermaid === "undefined" || !mermaidSource) {
      container.innerHTML = "<p>Graph unavailable (mermaid CDN unreachable) "
        + "\u2014 the event log below is still live.</p>";
      return;
    }
    var classDefs = [
      "classDef running fill:#1976d2,stroke:#1976d2,color:#fff",
      "classDef pass fill:#2e7d32,stroke:#2e7d32,color:#fff",
      "classDef fail fill:#ffb300,stroke:#ffb300,color:#fff",
      "classDef fatal fill:#c62828,stroke:#c62828,color:#fff",
      "classDef unverifiable fill:#7e57c2,stroke:#7e57c2,color:#fff",
      "classDef pending fill:#f2f0ff,stroke:#555,color:#000"
    ];
    var classLines = Object.keys(nodeStatus).filter(function (n) {
      return nodeIds.has(n);
    }).map(function (n) {
      return "class " + n + " " + statusOf(n) + ";";
    });
    var src = mermaidSource + "\\n" + classDefs.join("\\n") + "\\n" + classLines.join("\\n");
    mermaid.render("graph-" + (renderCount++), src).then(function (res) {
      container.innerHTML = res.svg;
      if (res.bindFunctions) { res.bindFunctions(container); }
    }).catch(function (err) {
      container.innerHTML = "<p>Graph render failed.</p>";
      logLine("graph render failed: " + err);
    });
  }

  function addPlaceholder(pre) {
    var ph = document.createElement("div");
    ph.className = "stage-placeholder";
    ph.textContent = "No output";
    pre.appendChild(ph);
  }

  function ensureSection(node) {
    var existing = document.getElementById("stage-" + node);
    if (existing) { return existing; }
    var stages = el("stages");
    var section = document.createElement("section");
    section.className = "stage collapsed";
    section.id = "stage-" + node;

    var header = document.createElement("button");
    header.className = "stage-header";
    header.type = "button";
    header.addEventListener("click", function () {
      var willCollapse = !section.classList.contains("collapsed");
      section.classList.toggle("collapsed");
      renderStageHeader(node);
      if (!willCollapse) {
        requestAnimationFrame(function () { tailSection(section); });
      }
    });

    var glyph = document.createElement("span");
    glyph.className = "stage-glyph";
    var name = document.createElement("span");
    name.className = "stage-name";
    var status = document.createElement("span");
    status.className = "stage-status";

    header.appendChild(glyph);
    header.appendChild(name);
    header.appendChild(status);

    var console = document.createElement("pre");
    console.className = "stage-console";
    addPlaceholder(console);

    var wrap = document.createElement("div");
    wrap.className = "console-wrap";
    var copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn";
    copyBtn.type = "button";
    copyBtn.setAttribute("title", "Copy output");
    copyBtn.setAttribute("aria-label", "Copy " + node + " console output");
    copyBtn.innerHTML = COPY_ICON;
    copyBtn.addEventListener("click", function () {
      copyConsole(console, copyBtn);
    });
    wrap.appendChild(copyBtn);
    wrap.appendChild(console);

    section.appendChild(header);
    section.appendChild(wrap);
    stages.appendChild(section);
    renderStageHeader(node);
    return section;
  }

  function renderStageHeader(node) {
    var section = ensureSection(node);
    var header = section.querySelector(".stage-header");
    var glyph = header.querySelector(".stage-glyph");
    var name = header.querySelector(".stage-name");
    var status = header.querySelector(".stage-status");
    var collapsed = section.classList.contains("collapsed");
    glyph.textContent = collapsed ? "\u25B8" : "\u25BE";
    name.textContent = node;
    var word = stageWord[node] || "";
    var suffix = "";
    if (word === "completed") {
      var v = currentVerdicts[node];
      if (v === "pass") { suffix = " (verdict: PASS)"; }
      else if (v === "fail") { suffix = " (verdict: FAIL)"; }
      else if (v === "unverifiable") { suffix = " (verdict: UNVERIFIABLE)"; }
    }
    status.textContent = (word ? " " + word : "") + suffix;
    header.className = "stage-header status-" + statusOf(node);
  }

  function setStageStatus(node, word) {
    stageWord[node] = word;
    renderStageHeader(node);
  }

  function flushPending() {
    if (pendingCompletion === null) { return; }
    var p = pendingCompletion;
    pendingCompletion = null;
    setStageStatus(p.node, p.word);
  }

  function expandStage(node) {
    var section = ensureSection(node);
    section.classList.remove("collapsed");
    renderStageHeader(node);
    section.scrollIntoView({ block: "nearest" });
    requestAnimationFrame(function () { tailSection(section); });
  }

  function collapseStage(node) {
    ensureSection(node).classList.add("collapsed");
    renderStageHeader(node);
  }

  function collapseAll() {
    Array.from(nodeIds).forEach(function (n) {
      ensureSection(n).classList.add("collapsed");
      renderStageHeader(n);
    });
  }

  function clearConsoles() {
    stageWord = {};
    Array.from(nodeIds).forEach(function (n) {
      var section = ensureSection(n);
      var pre = consoleOf(section);
      pre.innerHTML = "";
      addPlaceholder(pre);
      section.classList.add("collapsed");
      renderStageHeader(n);
    });
    el("run-log").innerHTML = "";
  }

  function consoleOf(section) {
    return section.querySelector(".console-wrap")
      .querySelector(".stage-console");
  }

  function scrollPortOf(section) {
    return section.querySelector(".console-wrap");
  }

  function copyConsole(pre, btn) {
    var lines = Array.prototype.map.call(pre.querySelectorAll(".cline"),
      function (c) { return c.textContent; });
    if (lines.length === 0) {
      btn.setAttribute("title", "Nothing to copy");
      return;
    }
    var text = lines.join("\\n");
    function done(ok) {
      btn.innerHTML = ok ? CHECK_ICON : COPY_ICON;
      btn.setAttribute("title", ok ? "Copied!" : "Copy failed");
      if (ok) {
        setTimeout(function () {
          btn.innerHTML = COPY_ICON;
          btn.setAttribute("title", "Copy output");
        }, 1500);
      }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); },
        function () { done(false); });
    } else {
      // clipboard API unavailable (e.g. plain-http on a non-localhost host)
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.top = "-1000px";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
      document.body.removeChild(ta);
      done(ok);
    }
  }

  function nearBottom(pre) {
    return pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
  }

  function tailSection(section) {
    if (section.classList.contains("collapsed")) { return; }
    var sp = scrollPortOf(section);
    sp.scrollTop = sp.scrollHeight;
  }

  function appendConsole(section, html) {
    var pre = consoleOf(section);
    var sp = scrollPortOf(section);
    var stick = !section.classList.contains("collapsed") && nearBottom(sp);
    var ph = pre.querySelector(".stage-placeholder");
    if (ph) { pre.removeChild(ph); }
    if (pre.querySelectorAll(".cline").length >= MAX_CONSOLE) {
      var first = pre.querySelector(".cline");
      if (first) { pre.removeChild(first); }
      var omitted = pre.querySelector(".omitted");
      if (!omitted) {
        omitted = document.createElement("div");
        omitted.className = "omitted";
        pre.insertBefore(omitted, pre.firstChild);
      }
      var count = parseInt(omitted.getAttribute("data-n") || "0", 10) + 1;
      omitted.setAttribute("data-n", String(count));
      omitted.textContent = "\u2026" + count + " line"
        + (count === 1 ? "" : "s") + " omitted";
    }
    var line = document.createElement("span");
    line.className = "cline";
    line.innerHTML = html;
    pre.appendChild(line);
    if (stick) { sp.scrollTop = sp.scrollHeight; }
  }

  function handleConsole(ev) {
    var html = ansiToHtml(ev.text);
    if (ev.node && nodeIds.has(ev.node)) {
      appendConsole(ensureSection(ev.node), html);
    } else {
      logConsole(html);
    }
  }

  function ansiToHtml(text) {
    var out = "";
    var i = 0;
    var ESC = "\\x1b";
    var cur = { fg: null, bg: null, bold: false, italic: false, underline: false };
    var open = false;

    function currentStyle() {
      var parts = [];
      if (cur.bold) { parts.push("font-weight:bold"); }
      if (cur.italic) { parts.push("font-style:italic"); }
      if (cur.underline) { parts.push("text-decoration:underline"); }
      if (cur.fg !== null) { parts.push("color:" + cur.fg); }
      if (cur.bg !== null) { parts.push("background-color:" + cur.bg); }
      return parts.join(";");
    }

    function closeSpan() {
      if (open) { out += "</span>"; open = false; }
    }

    function pushSpan() {
      closeSpan();
      var s = currentStyle();
      if (s) { out += '<span style="' + s + '">'; open = true; }
    }

    var html = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    var n = html.length;
    while (i < n) {
      var idx = html.indexOf(ESC, i);
      if (idx === -1) {
        out += html.slice(i);
        break;
      }
      out += html.slice(i, idx);
      var j = idx + 1;
      if (j >= n) { i = idx + 1; continue; }
      var ch = html.charAt(j);
      if (ch === "[") {
        var k = j + 1;
        while (k < n) {
          var cc = html.charCodeAt(k);
          if (cc >= 0x40 && cc <= 0x7e) { break; }
          k++;
        }
        if (k >= n) { i = n; break; }
        var final = html.charAt(k);
        var params = html.slice(j + 1, k);
        if (final === "m") {
          var parts = params.split(";");
          var codes = [];
          for (var p = 0; p < parts.length; p++) {
            codes.push(parts[p] === "" ? 0 : parseInt(parts[p], 10));
          }
          var changed = false;
          for (var c = 0; c < codes.length; c++) {
            var code = codes[c];
            if (code === 0) {
              cur = { fg: null, bg: null, bold: false, italic: false, underline: false };
              changed = true;
            } else if (code === 1) { cur.bold = true; changed = true; }
            else if (code === 22) { cur.bold = false; changed = true; }
            else if (code === 3) { cur.italic = true; changed = true; }
            else if (code === 23) { cur.italic = false; changed = true; }
            else if (code === 4) { cur.underline = true; changed = true; }
            else if (code === 24) { cur.underline = false; changed = true; }
            else if (code >= 30 && code <= 37) { cur.fg = PALETTE[code - 30]; changed = true; }
            else if (code >= 90 && code <= 97) { cur.fg = BRIGHT[code - 90]; changed = true; }
            else if (code >= 40 && code <= 47) { cur.bg = PALETTE[code - 40]; changed = true; }
            else if (code >= 100 && code <= 107) { cur.bg = BRIGHT[code - 100]; changed = true; }
            else if (code === 39) { cur.fg = null; changed = true; }
            else if (code === 49) { cur.bg = null; changed = true; }
            else if (code === 38 || code === 48) {
              // extended colour: 38;5;n (256-colour) or 38;2;r;g;b (truecolor)
              if (c + 2 < codes.length && codes[c + 1] === 5) {
                var val = codes[c + 2];
                var col;
                if (val < 8) { col = PALETTE[val]; }
                else if (val < 16) { col = BRIGHT[val - 8]; }
                else if (val < 232) {
                  var cube = val - 16;
                  var levels = [0, 95, 135, 175, 215, 255];
                  col = "rgb(" + levels[Math.floor(cube / 36)] + ","
                      + levels[Math.floor((cube % 36) / 6)] + ","
                      + levels[cube % 6] + ")";
                } else {
                  var g = 8 + 10 * (val - 232);
                  col = "rgb(" + g + "," + g + "," + g + ")";
                }
                if (code === 38) { cur.fg = col; } else { cur.bg = col; }
                c += 2;
                changed = true;
              } else if (c + 4 < codes.length && codes[c + 1] === 2) {
                var rgb = "rgb(" + codes[c + 2] + "," + codes[c + 3] + "," + codes[c + 4] + ")";
                if (code === 38) { cur.fg = rgb; } else { cur.bg = rgb; }
                c += 4;
                changed = true;
              }
            }
          }
          if (changed) { pushSpan(); }
        }
        i = k + 1;
        continue;
      }
      if (ch === "]") {
        var bel = html.indexOf(String.fromCharCode(7), j);
        var st = html.indexOf(ESC + String.fromCharCode(92), j);
        var end = -1;
        if (bel !== -1) { end = bel + 1; }
        if (st !== -1 && (end === -1 || st + 2 < end)) { end = st + 2; }
        if (end === -1) { i = n; break; }
        i = end;
        continue;
      }
      i = idx + 1;
    }
    closeSpan();
    return out;
  }

  function handle(ev) {
    switch (ev.type) {
      case "run_started":
        flushPending();
        clearConsoles();
        resetVerdicts();
        if (ev.attempt !== undefined) {
          attempt = ev.attempt;
        }
        maxAttempts = ev.max_attempts;
        el("subject").textContent = ev.subject;
        if (ev.thread_id !== undefined) {
          el("thread-id").textContent = ev.thread_id;
        }
        logLine("run started: " + ev.subject + " (thread " + ev.thread_id + ")");
        updateAttempt();
        break;
      case "node_started":
        flushPending();
        if (!nodeIds.has(ev.node)) { break; }
        nodeStatus[ev.node] = "running";
        stageWord[ev.node] = "running";
        Array.from(nodeIds).forEach(function (n) {
          if (n === ev.node) { expandStage(n); } else { collapseStage(n); }
        });
        renderGraph();
        break;
      case "node_finished":
        clearElapsed();
        if (!nodeIds.has(ev.node)) { break; }
        if (!fatalNodes.has(ev.node)) {
          nodeStatus[ev.node] = ev.ok ? "pass" : "fail";
        }
        pendingCompletion = {
          node: ev.node,
          word: ev.ok ? "completed" : "errored"
        };
        setStageStatus(ev.node, ev.ok ? "completed" : "errored");
        collapseStage(ev.node);
        renderGraph();
        break;
      case "heartbeat":
        if (nodeStatus[ev.node] === "running") {
          el("elapsed").textContent = ev.node + " running \u2014 " + mmss(ev.elapsed_seconds) + " elapsed";
          el("elapsed").style.display = "block";
        }
        break;
      case "state":
        if (ev.attempt !== undefined) { attempt = ev.attempt; }
        if (ev.step_verdicts !== undefined) {
          Object.keys(ev.step_verdicts).forEach(function (k) {
            verdictSteps.add(k);
            var v = ev.step_verdicts[k];
            if (v === "pass") { verdictPass.add(k); verdictFail.delete(k); verdictUnverifiable.delete(k); }
            else if (v === "fail") { verdictFail.add(k); verdictPass.delete(k); verdictUnverifiable.delete(k); }
            else if (v === "unverifiable") { verdictUnverifiable.add(k); verdictPass.delete(k); verdictFail.delete(k); }
            else { verdictPass.delete(k); verdictFail.delete(k); verdictUnverifiable.delete(k); }
            currentVerdicts[k] = v;
          });
          updateBadges();
          if (pendingCompletion !== null
              && (pendingCompletion.node in ev.step_verdicts)) {
            flushPending();
          }
          renderGraph();
        }
        if (ev.failed) {
          Object.keys(nodeStatus).forEach(function (n) {
            if (nodeStatus[n] === "running") { fatalNodes.add(n); }
          });
          logLine("state: failed");
          renderGraph();
        }
        updateAttempt();
        break;
      case "run_finished":
        clearElapsed();
        flushPending();
        if (!ev.success) {
          Object.keys(nodeStatus).forEach(function (n) {
            var s = statusOf(n);
            if (s === "fail" || s === "running") { nodeStatus[n] = "fatal"; }
          });
          renderGraph();
        }
        collapseAll();
        logLine("run finished: " + (ev.success ? "success" : "failure"));
        var banner = el("run-banner");
        banner.textContent = ev.success ? "\u2713 SUCCESS" : "\u2718 FAILURE";
        banner.className = ev.success ? "banner-success" : "banner-failure";
        banner.style.display = "block";
        break;
      case "console":
        handleConsole(ev);
        break;
    }
  }

  fetch("/topology").then(function (r) { return r.json(); }).then(function (d) {
    el("subject").textContent = d.subject;
    mermaidSource = d.mermaid;
    (d.nodes || []).forEach(function (n) {
      nodeIds.add(n);
      ensureSection(n);
    });
    if (!mermaidFailed && typeof mermaid !== "undefined") {
      mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });
    }
    topologyReady = true;
    pendingEvents.forEach(handle);
    pendingEvents = [];
    renderGraph();
  }).catch(function () {
    logLine("failed to load topology");
    topologyReady = true;
    pendingEvents.forEach(handle);
    pendingEvents = [];
  });

  setTimeout(function () {
    if (!topologyReady) {
      topologyReady = true;
      logLine("topology load timed out \u2014 event log only");
      pendingEvents.forEach(handle);
      pendingEvents = [];
    }
  }, 5000);

  var es = new EventSource("/events");
  es.onmessage = function (e) {
    var ev;
    try { ev = JSON.parse(e.data); }
    catch (err) { logLine("bad event: " + e.data); return; }
    if (!topologyReady) { pendingEvents.push(ev); return; }
    try { handle(ev); }
    catch (err) { logLine("bad event: " + e.data); }
  };
  es.onerror = function () { logLine("event stream interrupted"); };

  // Grab-handle between the graph and the sidebar: drag to resize the
  // sidebar (and with it the console panels, which stretch with its width).
  (function initDivider() {
    var divider = el("divider");
    var sidebar = el("sidebar");
    var dragging = false;
    divider.addEventListener("mousedown", function (e) {
      dragging = true;
      divider.classList.add("dragging");
      document.body.style.userSelect = "none";
      e.preventDefault();
    });
    window.addEventListener("mousemove", function (e) {
      if (!dragging) { return; }
      var width = window.innerWidth - e.clientX - divider.offsetWidth / 2;
      width = Math.max(240, Math.min(width, window.innerWidth * 0.8));
      sidebar.style.flex = "0 0 " + width + "px";
    });
    window.addEventListener("mouseup", function () {
      dragging = false;
      divider.classList.remove("dragging");
      document.body.style.userSelect = "";
    });
  })();
})();
</script>
</body>
</html>
"""
