"""Live graph visualization: StreamPart translation, a thread-safe event
bus, a config-driven topology renderer, and a stdlib-only HTTP/SSE server
serving the generated station-and-rail page.

The graph itself is untouched - every live update is derived from LangGraph's
``tasks``/``updates`` stream modes. The viz surface is served by default
(suppressed with ``--no-viz``); the server is stdlib-only (TS-4): the
HTTP/SSE server uses only the standard library, the topology DTO is assembled
directly from the pipeline config (no LangChain renderer), and the page is
the generated module in ``page.py``. ``mermaid`` remains in the DTO for
compatibility; the page consumes ``nodes``/``flow``/``edges`` and draws the
layout itself.
"""

from __future__ import annotations

import errno
import json
import os
import queue
import re
import sys
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from agentic_dev_loop.core.config import PipelineConfig, Route, route_target

from . import runs_view
from .page import PAGE, RUNS_PAGE

STATE_WHITELIST = ("attempt", "step_verdicts", "outcome", "failed")

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


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


def _topology_parts(cfg: PipelineConfig) -> tuple[list[_Edge], list[_Edge], list[str]]:
    """Compute the happy-path chain, the loop-back edges and the node order."""
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
    return chain, back_edges, node_ids


def topology_mermaid(cfg: PipelineConfig) -> tuple[str, list[str]]:
    """Build a single vertical flowchart for the configured steps.

    Returns ``(mermaid_source, node_ids)``. The happy path is one solid
    ``-->`` chain following each step's ``on_pass``; retries render as dotted
    ``-. label .->`` loop-backs, and a non-retry ``on_fail`` as an unlabeled
    dotted edge. Stdlib-only (TS-4): the source is assembled directly, with
    no node declarations and no terminal stadium shapes - byte-identical to
    the previous LangChain renderer's output.
    """
    chain, back_edges, node_ids = _topology_parts(cfg)
    return _render_mermaid(chain + back_edges), node_ids


def topology_graph(cfg: PipelineConfig) -> dict:
    """Return the topology DTO: mermaid plus structured flow/edges.

    ``mermaid`` stays byte-identical to :func:`topology_mermaid`; ``flow`` is
    the consecutive on_pass chain and ``edges`` maps loop-backs to
    ``{src, dst, label}`` (``label`` is ``None`` when unlabelled).
    """
    chain, back_edges, node_ids = _topology_parts(cfg)
    return {
        "mermaid": _render_mermaid(chain + back_edges),
        "nodes": node_ids,
        "flow": [{"src": edge.src, "dst": edge.dst} for edge in chain],
        "edges": [{"src": edge.src, "dst": edge.dst, "label": edge.label}
                  for edge in back_edges],
    }


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
                   subject: str, nodes: Iterable[str],
                   flow: Iterable[dict] | None = None,
                   edges: Iterable[dict] | None = None,
                   provider: str | None = None,
                   runs_page: str | None = RUNS_PAGE,
                   history: bool = False,
                   runs_root: str | None = None,
                   audit_dir: str | None = None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        viz_bus = bus
        viz_topology = topology_mermaid
        viz_subject = subject
        viz_provider = provider
        viz_nodes = list(nodes)
        viz_flow = list(flow or ())
        viz_edges = list(edges or ())
        viz_runs_page = runs_page
        viz_history = history
        viz_runs_root = runs_root
        viz_audit_dir = audit_dir

        def log_message(self, *_args) -> None:  # keep test output quiet
            pass

        def handle_one_request(self) -> None:
            try:
                super().handle_one_request()
            except (ConnectionResetError, BrokenPipeError):
                return

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/runs":
                self._runs_page()
            elif path == "/runs/data":
                self._runs_list()
            elif path.startswith("/runs/") and path.endswith("/data"):
                run_id = unquote(path[len("/runs/"):-len("/data")])
                if not _RUN_ID_RE.match(run_id):
                    self._json(404, {"error": f"unknown run: {run_id}"})
                else:
                    self._runs_detail(run_id)
            elif path.startswith("/runs/") and path != "/runs/":
                self._runs_page()
            elif path == "/" and self.viz_history:
                self._runs_page()
            elif path == "/" and not self.viz_history:
                self._send(200, "text/html; charset=utf-8", PAGE)
            elif path == "/topology" and not self.viz_history:
                body = json.dumps({"mermaid": self.viz_topology,
                                   "subject": self.viz_subject,
                                   "provider": self.viz_provider,
                                   "nodes": self.viz_nodes,
                                   "flow": self.viz_flow,
                                   "edges": self.viz_edges})
                self._send(200, "application/json", body)
            elif path == "/events" and not self.viz_history:
                self._events()
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            self._method_not_allowed()

        def do_PUT(self) -> None:
            self._method_not_allowed()

        def do_PATCH(self) -> None:
            self._method_not_allowed()

        def do_DELETE(self) -> None:
            self._method_not_allowed()

        def do_HEAD(self) -> None:
            self._method_not_allowed()

        def do_OPTIONS(self) -> None:
            self._method_not_allowed()

        def _runs_page(self) -> None:
            if self.viz_runs_page is None:
                self.send_error(404)
                return
            self._send(200, "text/html; charset=utf-8", self.viz_runs_page)

        def _runs_list(self) -> None:
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            since = (query.get("since") or [None])[0]
            until = (query.get("until") or [None])[0]
            status = query.get("status") or []
            filters, error = runs_view.parse_filters(since, until, status)
            if error is not None:
                self._json(400, {"error": error})
                return
            self._json(200, runs_view.list_payload(
                filters["since"], filters["until"], filters["statuses"],
                runs_root=self.viz_runs_root,
                audit_dir=self.viz_audit_dir))

        def _runs_detail(self, run_id: str) -> None:
            payload = runs_view.detail_payload(
                run_id, runs_root=self.viz_runs_root,
                audit_dir=self.viz_audit_dir)
            if payload is None:
                self._json(404, {"error": f"unknown run: {run_id}"})
                return
            self._json(200, payload)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, "application/json",
                       json.dumps(payload, ensure_ascii=True))

        def _method_not_allowed(self) -> None:
            self._send(405, "application/json",
                       json.dumps({"error": "method not allowed"}),
                       {"Allow": "GET"})

        def _send(self, status: int, content_type: str, body: str,
                  headers: dict | None = None) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
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


def _bind_server(handler: type[BaseHTTPRequestHandler],
                 port: int) -> tuple[_ReuseSafeServer, threading.Thread]:
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


def serve_viz(bus: VizBus, topology_mermaid: str, subject: str,
              port: int, nodes: Iterable[str] = (),
              flow: Iterable[dict] | None = None,
              edges: Iterable[dict] | None = None,
              provider: str | None = None) -> tuple[_ReuseSafeServer, threading.Thread]:
    """Bind a loopback HTTP server on ``port`` and serve it on a daemon
    thread. Returns ``(httpd, thread)`` - the caller reads the real bound
    port from ``httpd.server_address[1]``.

    ``port`` is preferred, not fixed: when it is already in use
    (``errno.EADDRINUSE``) the server rebinds port 0 and lets the OS pick a
    free port. Any other bind error (e.g. ``EACCES`` on a privileged port)
    still fails loud, naming ``--viz-port``."""
    handler = _handler_class(bus, topology_mermaid, subject, nodes,
                             flow, edges, provider)
    return _bind_server(handler, port)


def serve_history(port: int, runs_root: str | None = None,
                  audit_dir: str | None = None
                  ) -> tuple[_ReuseSafeServer, threading.Thread]:
    """Serve the historical runs view with no pipeline topology (BR-17).

    ``runs_root`` and ``audit_dir`` override the discovery defaults; both
    resolve through the same ``reporting`` helpers the markdown report uses.
    """
    handler = _handler_class(VizBus(), "", "", [], runs_page=RUNS_PAGE,
                             history=True, runs_root=runs_root,
                             audit_dir=audit_dir)
    return _bind_server(handler, port)
