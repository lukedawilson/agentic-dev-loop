"""Viz module tests - translation, the pub/sub bus, and the HTTP/SSE server.

Everything here runs real: translation is exercised against a real tiny
StateGraph + MemorySaver, and HTTP/SSE over a real socket on port 0. Only the
two CLI-lifecycle seams (viz.serve_viz / webbrowser.open) are stubbed, and
only in test_cli.py's main() lifecycle tests.
"""

import http.client
import io
import json
import operator
import os
import queue
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Annotated, TypedDict

import pytest

from agentic_dev_loop import viz
from agentic_dev_loop.core.config import PipelineConfig, load_config
from agentic_dev_loop.viz import (MAX_LINE, PAGE, ConsoleCapture, HeartbeatThread,
                              NodeWatch, VizBus, _ReuseSafeServer, _Tee,
                              _handler_class, serve_viz, topology_graph,
                              topology_mermaid, translate)

MERMAID = "graph TD;\n\tdev(dev)\n\ttest(test)\n"
SELF_PIPELINE = Path(__file__).parent.parent / "agentic-dev-loop" / "pipeline.yaml"


def _privileged_ports_enforced() -> bool:
    """True when the kernel reserves privileged ports for root. Some
    containers set ``net.ipv4.ip_unprivileged_port_start=0``, which lets a
    non-root uid bind port 1 - the loud-privileged-port test can't run there."""
    if sys.platform != "linux":
        return True
    try:
        start = int(Path("/proc/sys/net/ipv4/ip_unprivileged_port_start")
                    .read_text().strip())
    except (OSError, ValueError):
        return True
    return start > 0


def tasks_start(name):
    return {"type": "tasks", "ns": (), "data": {"id": "id-1", "name": name,
                                                 "triggers": ("branch:to:x",)}}


def tasks_finish(name, error=None):
    return {"type": "tasks", "ns": (), "data": {"id": "id-1", "name": name,
                                                 "error": error}}


def updates(delta):
    return {"type": "updates", "ns": (), "data": {"n": delta}}


class TestTranslate:
    def test_start_part(self):
        assert translate(tasks_start("test")) == \
            [{"type": "node_started", "node": "test"}]

    def test_finish_ok(self):
        assert translate(tasks_finish("test")) == \
            [{"type": "node_finished", "node": "test", "ok": True}]

    def test_finish_error(self):
        assert translate(tasks_finish("test", error="boom")) == \
            [{"type": "node_finished", "node": "test", "ok": False}]

    def test_updates_drops_non_whitelisted_keys(self):
        delta = {"docs": {"a": "x"}, "context": "long", "notes": ["n"],
                 "commit_allowed": True}
        assert translate(updates(delta)) == [{"type": "state"}]

    def test_updates_keeps_whitelisted_keys(self):
        delta = {"attempt": 2, "step_verdicts": {"test": "fail"},
                 "outcome": None, "failed": False, "docs": {"a": "x"}}
        assert translate(updates(delta)) == [
            {"type": "state", "attempt": 2,
             "step_verdicts": {"test": "fail"}, "outcome": None,
             "failed": False}]

    def test_unknown_parts_are_empty(self):
        assert translate({"type": "custom", "ns": (), "data": {"x": 1}}) == []
        assert translate({"type": "tasks", "ns": (), "data": {"id": "id-1"}}) == []
        assert translate({"type": "updates", "ns": (), "data": {}}) == \
            [{"type": "state"}]

    def test_end_to_end_stream_sequence(self):
        class TinyState(TypedDict):
            vals: Annotated[list[str], operator.add]
            attempt: int

        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.graph import END, START, StateGraph

        def node_a(state):
            return {"vals": ["a"], "attempt": state.get("attempt", 0) + 1}

        def node_b(_state):
            return {"vals": ["b"]}

        graph = StateGraph(TinyState)
        graph.add_node("a", node_a)
        graph.add_node("b", node_b)
        graph.add_edge(START, "a")
        graph.add_edge("a", "b")
        graph.add_edge("b", END)
        compiled = graph.compile(checkpointer=MemorySaver())

        events = []
        for part in compiled.stream({"vals": [], "attempt": 1},
                                    {"configurable": {"thread_id": "viz-e2e"}},
                                    stream_mode=["tasks", "updates"],
                                    version="v2"):
            events.extend(translate(part))

        assert events == [
            {"type": "node_started", "node": "a"},
            {"type": "state", "attempt": 2},
            {"type": "node_finished", "node": "a", "ok": True},
            {"type": "node_started", "node": "b"},
            {"type": "state"},
            {"type": "node_finished", "node": "b", "ok": True},
        ]


class TestTopologyMermaid:
    def _pipeline(self, steps: list[dict]) -> PipelineConfig:
        return PipelineConfig.model_validate({
            "name": "viz-topology",
            "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "sdk_agents",
            "lockfiles": ["uv.lock"],
            "steps": steps,
        })

    def test_shipped_self_loop_topology(self):
        cfg = load_config(SELF_PIPELINE)
        source, nodes = topology_mermaid(cfg)

        chain = ["brief", "deps", "dev", "restore", "test", "review", "qa", "commit", "success"]
        for src, dst in zip(chain, chain[1:]):
            assert f"{src} --> {dst};" in source

        assert "restore -. &nbsp;fail&nbsp; .-> dev;" in source
        assert "test -. &nbsp;fail&nbsp; .-> dev;" in source
        assert "review -. &nbsp;NEEDS CHANGES&nbsp; .-> dev;" in source
        assert "qa -. &nbsp;FAIL&nbsp; .-> dev;" in source

        assert "__start__" not in source
        assert "__end__" not in source
        assert "bump" not in source
        assert "(dev)" not in source

        assert nodes == chain

    def test_route_on_pass_and_nonretry_fail(self):
        cfg = self._pipeline([{
            "name": "dev",
            "agent": "dev",
            "on_pass": {"goto": "success"},
            "on_fail": "failure",
        }])
        source, nodes = topology_mermaid(cfg)

        assert "dev --> success;" in source
        assert "dev -.-> failure;" in source
        assert nodes == ["dev", "success", "failure"]

    def test_retry_without_verdicts_falls_back_to_retry(self):
        cfg = self._pipeline([{
            "name": "dev",
            "agent": "dev",
            "on_pass": "success",
            "on_fail": {"goto": "dev", "retry": True},
        }])
        source, _nodes = topology_mermaid(cfg)

        assert "dev -. &nbsp;retry&nbsp; .-> dev;" in source

    def test_command_retry_edge_label_is_fail(self):
        cfg = self._pipeline([
            {"name": "dev", "agent": "dev", "on_pass": "success"},
            {"name": "test", "command": "true", "on_pass": "success",
             "on_fail": {"goto": "dev", "retry": True}},
        ])
        source, _nodes = topology_mermaid(cfg)

        assert "test -. &nbsp;fail&nbsp; .-> dev;" in source

    def test_topology_graph_structured_flow_and_edges(self):
        cfg = load_config(SELF_PIPELINE)
        dto = topology_graph(cfg)
        chain = ["brief", "deps", "dev", "restore", "test", "review", "qa",
                 "commit", "success"]
        assert dto["nodes"] == chain
        assert dto["flow"] == [{"src": src, "dst": dst}
                               for src, dst in zip(chain, chain[1:])]
        assert dto["edges"] == [
            {"src": "restore", "dst": "dev", "label": "fail"},
            {"src": "test", "dst": "dev", "label": "fail"},
            {"src": "review", "dst": "dev", "label": "NEEDS CHANGES"},
            {"src": "qa", "dst": "dev", "label": "FAIL"},
        ]
        assert dto["mermaid"] == topology_mermaid(cfg)[0]

    def test_topology_graph_unlabelled_nonretry_edge(self):
        cfg = self._pipeline([{
            "name": "dev",
            "agent": "dev",
            "on_pass": {"goto": "success"},
            "on_fail": "failure",
        }])
        dto = topology_graph(cfg)
        assert dto["flow"] == [{"src": "dev", "dst": "success"}]
        assert dto["edges"] == [{"src": "dev", "dst": "failure",
                                 "label": None}]


class TestVizBus:
    def test_fanout_to_two_subscribers(self):
        bus = VizBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.publish({"x": 1})
        assert q1.get(timeout=1) == {"x": 1}
        assert q2.get(timeout=1) == {"x": 1}

    def test_late_subscriber_gets_backlog_in_order(self):
        bus = VizBus(maxlen=10)
        for i in range(3):
            bus.publish({"x": i})
        q = bus.subscribe()
        assert [q.get(timeout=1) for _ in range(3)] == \
            [{"x": 0}, {"x": 1}, {"x": 2}]
        assert q.empty()

    def test_unsubscribe_stops_delivery(self):
        bus = VizBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.publish({"x": 1})
        assert q.empty()

    def test_backlog_evicts_oldest_beyond_maxlen(self):
        bus = VizBus(maxlen=3)
        for i in range(5):
            bus.publish({"x": i})
        q = bus.subscribe()
        received = []
        while not q.empty():
            received.append(q.get_nowait())
        assert [e["x"] for e in received] == [2, 3, 4]

    def test_concurrent_publish_loses_no_events(self):
        bus = VizBus()
        q = bus.subscribe()
        threads = 10
        per_thread = 20

        def worker(offset):
            for i in range(per_thread):
                bus.publish({"i": offset * per_thread + i})

        workers = [threading.Thread(target=worker, args=(t,))
                   for t in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()

        received = []
        while not q.empty():
            received.append(q.get_nowait())
        assert len(received) == threads * per_thread
        assert {e["i"] for e in received} == set(range(threads * per_thread))

    def test_console_spam_evicts_only_console(self):
        bus = VizBus()
        bus.publish({"type": "run_started"})
        for i in range(600):
            bus.publish({"type": "console", "text": i})
        bus.publish({"type": "node_started", "node": "dev"})
        q = bus.subscribe()
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert [e["type"] for e in events] == \
            ["run_started"] + ["console"] * 500 + ["node_started"]
        console_texts = [e["text"] for e in events if e["type"] == "console"]
        assert console_texts == list(range(100, 600))

    def test_console_maxlen_is_configurable(self):
        bus = VizBus(console_maxlen=2)
        for i in range(5):
            bus.publish({"type": "console", "text": i})
        q = bus.subscribe()
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert [e["text"] for e in events] == [3, 4]

    def test_console_replay_retains_each_node_separately(self):
        bus = VizBus()
        bus.publish({"type": "run_started"})
        for i in range(600):
            bus.publish({"type": "console", "node": "dev", "text": i})
        for i in range(600):
            bus.publish({"type": "console", "node": "qa", "text": i})
        bus.publish({"type": "node_started", "node": "qa"})
        q = bus.subscribe()
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert [e["type"] for e in events] == \
            ["run_started"] + ["console"] * 1000 + ["node_started"]
        dev_texts = [e["text"] for e in events
                     if e["type"] == "console" and e["node"] == "dev"]
        qa_texts = [e["text"] for e in events
                    if e["type"] == "console" and e["node"] == "qa"]
        assert dev_texts == list(range(100, 600))
        assert qa_texts == list(range(100, 600))

    def test_console_replay_merges_nodes_in_publish_order(self):
        bus = VizBus()
        published = [
            {"type": "console", "node": "dev", "text": "a0"},
            {"type": "node_started", "node": "dev"},
            {"type": "console", "node": "qa", "text": "b0"},
            {"type": "console", "node": "dev", "text": "a1"},
            {"type": "console", "node": "qa", "text": "b1"},
            {"type": "node_finished", "node": "dev"},
            {"type": "console", "node": "dev", "text": "a2"},
        ]
        for event in published:
            bus.publish(event)
        q = bus.subscribe()
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert events == published

    def test_heartbeat_live_only_not_replayed(self):
        bus = VizBus()
        bus.publish({"type": "heartbeat", "node": "dev", "elapsed_seconds": 1})
        q = bus.subscribe()
        assert q.empty()
        bus.publish({"type": "heartbeat", "node": "dev", "elapsed_seconds": 2})
        assert q.get(timeout=1) == \
            {"type": "heartbeat", "node": "dev", "elapsed_seconds": 2}


class StubWatch:
    def __init__(self, node=None):
        self.node = node

    def current(self):
        return self.node


class TestConsoleCapture:
    def _attach(self, bus, watch, monkeypatch):
        out = io.StringIO()
        err = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)
        cap = ConsoleCapture(bus, watch)
        cap.attach()
        return cap, out, err

    def _events(self, bus):
        q = bus.subscribe()
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        return events

    def test_complete_lines_split_and_attributed(self, monkeypatch):
        bus = VizBus()
        watch = StubWatch("dev")
        cap, out, _err = self._attach(bus, watch, monkeypatch)
        sys.stdout.write("hello\nworld\n")
        cap.detach()
        assert out.getvalue() == "hello\nworld\n"
        assert self._events(bus) == [
            {"type": "console", "node": "dev", "stream": "stdout",
             "text": "hello"},
            {"type": "console", "node": "dev", "stream": "stdout",
             "text": "world"},
        ]

    def test_ansi_preserved_verbatim(self, monkeypatch):
        bus = VizBus()
        cap, _out, _err = self._attach(bus, StubWatch(None), monkeypatch)
        sys.stdout.write("\x1b[32mhi\x1b[0m\n")
        cap.detach()
        assert self._events(bus)[0]["text"] == "\x1b[32mhi\x1b[0m"

    def test_write_through_to_target(self, monkeypatch):
        bus = VizBus()
        cap, out, err = self._attach(bus, StubWatch("dev"), monkeypatch)
        sys.stdout.write("out\n")
        sys.stderr.write("err\n")
        cap.detach()
        assert out.getvalue() == "out\n"
        assert err.getvalue() == "err\n"
        events = self._events(bus)
        assert {e["stream"] for e in events} == {"stdout", "stderr"}

    def test_partial_writes_join_into_one_line(self, monkeypatch):
        bus = VizBus()
        cap, _out, _err = self._attach(bus, StubWatch("dev"), monkeypatch)
        sys.stdout.write("hel")
        sys.stdout.write("lo\n")
        cap.detach()
        assert self._events(bus) == [
            {"type": "console", "node": "dev", "stream": "stdout",
             "text": "hello"},
        ]

    def test_crlf_stripped(self, monkeypatch):
        bus = VizBus()
        cap, _out, _err = self._attach(bus, StubWatch("dev"), monkeypatch)
        sys.stdout.write("hi\r\n")
        cap.detach()
        assert self._events(bus)[0]["text"] == "hi"

    def test_node_attribution_none_is_null(self, monkeypatch):
        bus = VizBus()
        cap, _out, _err = self._attach(bus, StubWatch(None), monkeypatch)
        sys.stdout.write("idle\n")
        cap.detach()
        assert self._events(bus)[0]["node"] is None

    def test_attach_swap_and_restore(self, monkeypatch):
        bus = VizBus()
        out = io.StringIO()
        err = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)
        cap = ConsoleCapture(bus, StubWatch(None))
        cap.attach()
        assert sys.stdout is not out
        assert sys.stderr is not err
        cap.detach()
        assert sys.stdout is out
        assert sys.stderr is err

    def test_double_attach_raises(self, monkeypatch):
        bus = VizBus()
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        monkeypatch.setattr(sys, "stderr", io.StringIO())
        cap = ConsoleCapture(bus, StubWatch(None))
        cap.attach()
        with pytest.raises(RuntimeError):
            cap.attach()
        cap.detach()

    def test_long_line_truncated(self, monkeypatch):
        bus = VizBus()
        cap, _out, _err = self._attach(bus, StubWatch(None), monkeypatch)
        sys.stdout.write("x" * (MAX_LINE + 1) + "\n")
        cap.detach()
        assert self._events(bus)[0]["text"] == "x" * MAX_LINE + "…"

    def test_isatty_delegates_to_target(self):
        class TtyTarget:
            def isatty(self):
                return True

            def write(self, _s):
                return 0

            def flush(self):
                pass

        cap = ConsoleCapture(VizBus(), StubWatch(None))
        tee = _Tee(cap, "stdout", TtyTarget())
        assert tee.isatty() is True

    def test_columns_env_wide_during_capture(self, monkeypatch):
        monkeypatch.delenv("COLUMNS", raising=False)
        cap, _out, _err = self._attach(VizBus(), StubWatch(None), monkeypatch)
        assert os.environ["COLUMNS"] == str(MAX_LINE)
        cap.detach()
        assert "COLUMNS" not in os.environ

    def test_columns_env_restored_after_capture(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "120")
        cap, _out, _err = self._attach(VizBus(), StubWatch(None), monkeypatch)
        assert os.environ["COLUMNS"] == str(MAX_LINE)
        cap.detach()
        assert os.environ["COLUMNS"] == "120"


class TestHandleOneRequest:
    def _handler(self):
        return _handler_class(VizBus(), MERMAID, "subject", [])

    def test_swallows_connection_reset(self):
        cls = self._handler()
        handler = cls.__new__(cls)

        class FakeRfile:
            def readline(self, _limit=-1):
                raise ConnectionResetError

        handler.rfile = FakeRfile()
        assert handler.handle_one_request() is None

    def test_propagates_other_errors(self):
        cls = self._handler()
        handler = cls.__new__(cls)

        class FakeRfile:
            def readline(self, _limit=-1):
                raise ValueError("boom")

        handler.rfile = FakeRfile()
        with pytest.raises(ValueError):
            handler.handle_one_request()


class TestNodeWatch:
    def test_elapsed_grows(self, monkeypatch):
        clock = [0.0]
        monkeypatch.setattr(viz.time, "monotonic", lambda: clock[0])
        watch = NodeWatch()
        watch.start("dev")
        assert watch.elapsed_seconds() == 0
        clock[0] = 26.0
        assert watch.elapsed_seconds() == 26

    def test_finish_only_clears_matching_node(self):
        watch = NodeWatch()
        watch.start("dev")
        watch.finish("test")
        assert watch.current() == "dev"
        watch.finish("dev")
        assert watch.current() is None


class TestHeartbeatThread:
    class StubWatch:
        def __init__(self, node="dev", elapsed=7):
            self._node = node
            self._elapsed = elapsed

        def current(self):
            return self._node

        def elapsed_seconds(self):
            return self._elapsed

    def test_publishes_while_running_and_stops(self):
        bus = VizBus()
        thread = HeartbeatThread(self.StubWatch(), bus, interval=0.01)
        q = bus.subscribe()
        thread.start()
        deadline = time.monotonic() + 0.2
        events = []
        while time.monotonic() < deadline:
            try:
                events.append(q.get(timeout=0.05))
            except queue.Empty:
                pass
        thread.stop()
        thread.join(timeout=1)
        assert not thread.is_alive()

        heartbeats = [e for e in events if e["type"] == "heartbeat"]
        assert len(heartbeats) >= 2
        assert heartbeats[0] == {"type": "heartbeat", "node": "dev",
                                 "elapsed_seconds": 7}
        assert all(e == heartbeats[0] for e in heartbeats)

        while not q.empty():
            q.get_nowait()
        time.sleep(0.05)
        assert q.empty()


@pytest.fixture
def server():
    bus = VizBus()
    httpd, thread = serve_viz(bus, MERMAID, "plan plan.md", 0)
    port = httpd.server_address[1]
    yield bus, httpd, thread, port
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    return resp.status, body


def connect_sse(port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(b"GET /events HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                 b"Accept: text/event-stream\r\n\r\n")
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        assert chunk, "connection closed before response headers"
        data += chunk
    sock.settimeout(5)
    _headers, _sep, leftover = data.partition(b"\r\n\r\n")
    return sock, leftover


def read_frame(sock, leftover=b""):
    data = leftover
    while b"\n\n" not in data:
        chunk = sock.recv(4096)
        assert chunk, "connection closed before SSE frame"
        data += chunk
    return data


class TestServer:
    def test_index_serves_page(self, server):
        _bus, _httpd, _thread, port = server
        status, body = http_get(port, "/")
        assert status == 200
        assert 'id="graph"' in body
        assert 'role="img"' in body
        for marker in ('id="subject"', 'id="attempt"', 'id="thread-id"',
                       'id="total"', 'id="verdicts"', 'id="stages"',
                       'id="run-log"', 'id="stream-banner"', 'id="run-banner"',
                       'data-testid="theme-toggle"'):
            assert marker in body
        assert "agentic-dev-loop viz" in body

    def test_topology_json_round_trip(self, server):
        _bus, _httpd, _thread, port = server
        status, body = http_get(port, "/topology")
        assert status == 200
        assert json.loads(body) == {"mermaid": MERMAID, "subject": "plan plan.md", "provider": None,
                                    "nodes": [], "flow": [], "edges": []}

    def test_topology_round_trip_includes_nodes(self):
        bus = VizBus()
        httpd, thread = serve_viz(bus, MERMAID, "subject", 0,
                                  ["dev", "success"])
        port = httpd.server_address[1]
        try:
            status, body = http_get(port, "/topology")
            assert status == 200
            assert json.loads(body) == {"mermaid": MERMAID, "subject": "subject", "provider": None,
                                        "nodes": ["dev", "success"],
                                        "flow": [], "edges": []}
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_sse_streams_published_events(self, server):
        bus, _httpd, _thread, port = server
        sock, leftover = connect_sse(port)
        bus.publish({"type": "node_started", "node": "dev"})
        frame = read_frame(sock, leftover)
        sock.close()
        assert json.loads(frame.decode().split("data: ", 1)[1].strip()) == \
            {"type": "node_started", "node": "dev"}

    def test_sse_replays_backlog(self, server):
        bus, _httpd, _thread, port = server
        bus.publish({"type": "node_started", "node": "test"})
        sock, leftover = connect_sse(port)
        frame = read_frame(sock, leftover)
        sock.close()
        assert json.loads(frame.decode().split("data: ", 1)[1].strip()) == \
            {"type": "node_started", "node": "test"}

    def test_preferred_port_honored_when_free(self):
        port = free_port()
        bus = VizBus()
        httpd, thread = serve_viz(bus, MERMAID, "subject", port)
        try:
            assert httpd.server_address[1] == port
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_busy_port_falls_back_to_os_assigned(self, server):
        _bus, _httpd, _thread, port = server
        bus = VizBus()
        httpd, thread = serve_viz(bus, MERMAID, "subject", port)
        try:
            assert httpd.server_address[1] != port
            status, _body = http_get(httpd.server_address[1], "/")
            assert status == 200
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_privileged_port_still_loud(self):
        if sys.platform == "win32":
            pytest.skip("no os.geteuid on Windows")
        if os.geteuid() == 0:
            pytest.skip("privileged port is not EACCES when running as root")
        if not _privileged_ports_enforced():
            pytest.skip("kernel permits unprivileged privileged-port binding")
        with pytest.raises(OSError, match="--viz-port"):
            serve_viz(VizBus(), MERMAID, "subject", 1)

    @pytest.mark.skipif(sys.platform != "win32",
                        reason="Windows socket semantics only")
    def test_windows_disables_so_reuseaddr(self):
        """CI-red root cause: on Windows SO_REUSEADDR permits binding an
        in-use port, so the busy-port fallback reassigned the SAME port."""
        assert _ReuseSafeServer.allow_reuse_address is False

    def test_connection_reset_on_request_path_is_silent(self, server, monkeypatch):
        _bus, _httpd, _thread, port = server
        captured = io.StringIO()
        monkeypatch.setattr("sys.stderr", captured)
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                        struct.pack("ii", 1, 0))
        sock.close()
        for _ in range(20):
            if "Exception occurred during processing" in captured.getvalue():
                break
            time.sleep(0.05)
        status, _body = http_get(port, "/topology")
        assert status == 200
        assert "Exception occurred during processing" not in captured.getvalue()



# The page script is real browser JS embedded in the PAGE constant. Run it
# under node (skipped when node is absent) against a minimal DOM shim and feed
# it real event JSON, so the verdict-badge behaviour is tested by execution
# rather than by substring assertions.
class TestPageScriptBehavior:
    INLINE_SCRIPT = re.compile(r"<script>\n\(function \(\) \{.*?\}\)\(\);\n</script>",
                               re.S)

    HARNESS = r"""
var registry = {};
function El(tag) {
  this.tag = tag;
  this.children = [];
  this._class = "";
  this.style = {};
  this.textContent = "";
  this._html = "";
  this._attrs = {};
  this.listeners = {};
  var self = this;
  this.classList = {
    add: function (c) {
      var cs = _cs();
      if (cs.indexOf(c) === -1) { cs.push(c); }
      self._class = cs.join(" ");
    },
    remove: function (c) {
      self._class = _cs().filter(function (x) { return x !== c; }).join(" ");
    },
    toggle: function (c) {
      var cs = _cs();
      if (cs.indexOf(c) === -1) { cs.push(c); }
      else { cs = cs.filter(function (x) { return x !== c; }); }
      self._class = cs.join(" ");
    },
    contains: function (c) { return _cs().indexOf(c) !== -1; }
  };
  function _cs() { return self._class.split(/\s+/).filter(Boolean); }
}
Object.defineProperties(El.prototype, {
  className: {
    get: function () { return this._class; },
    set: function (v) { this._class = String(v); }
  },
  innerHTML: {
    get: function () { return this._html; },
    set: function (v) {
      this._html = String(v);
      this.children = [];
      // approximate innerText for copy tests: strip tags, unescape entities
      this.textContent = String(v).replace(/<[^>]*>/g, "")
        .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
        .replace(/&amp;/g, "&");
    }
  }
});
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.removeChild = function (c) {
  var i = this.children.indexOf(c);
  if (i >= 0) { this.children.splice(i, 1); }
  return c;
};
El.prototype.insertBefore = function (c, ref) {
  var i = ref ? this.children.indexOf(ref) : -1;
  if (i < 0) { this.children.push(c); } else { this.children.splice(i, 0, c); }
  return c;
};
El.prototype.addEventListener = function (t, fn) { this.listeners[t] = fn; };
El.prototype.setAttribute = function (k, v) { this._attrs[k] = String(v); };
El.prototype.getAttribute = function (k) {
  return Object.prototype.hasOwnProperty.call(this._attrs, k)
    ? this._attrs[k] : null;
};
El.prototype.querySelector = function (sel) {
  var cls = sel.slice(1);
  for (var i = 0; i < this.children.length; i++) {
    var c = this.children[i];
    if ((" " + c._class + " ").indexOf(" " + cls + " ") !== -1) { return c; }
  }
  return null;
};
El.prototype.querySelectorAll = function (sel) {
  var cls = sel.slice(1);
  var out = [];
  this.children.forEach(function (c) {
    if ((" " + c._class + " ").indexOf(" " + cls + " ") !== -1) { out.push(c); }
  });
  return out;
};
El.prototype.scrollIntoView = function () {};

var STATIC_IDS = ["subject", "attempt", "thread-id", "total", "verdicts",
                  "stages", "run-log", "graph", "run-banner",
                  "stream-banner", "pipeline-panel", "meter",
                  "stream", "stream-label", "theme-toggle", "theme-icon"];
STATIC_IDS.forEach(function (id) { registry[id] = new El(id); });
var elCounter = 0;
var document = {
  _listeners: {},
  addEventListener: function (t, fn) { document._listeners[t] = fn; },
  getElementById: function (id) {
    if (registry[id]) { return registry[id]; }
    for (var k in registry) {
      if (registry[k].id === id) { return registry[k]; }
    }
    return null;
  },
  createElement: function (tag) {
    var e = new El(tag);
    registry["el-" + (++elCounter)] = e;
    return e;
  },
  body: new El("body")
};
var closeCalls = 0;
var reloadCalls = 0;
var esInstances = [];
var window = { innerWidth: 1000, _listeners: {} };
window.addEventListener = function (t, fn) { window._listeners[t] = fn; };
window.location = { reload: function () { reloadCalls++; } };
var esInstance = null;

globalThis.document = document;
globalThis.window = window;
globalThis.location = window.location;
var copiedText = null;
var intervalFn = null;
// node 21+ ships a read-only `navigator` global, so defineProperty is needed
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    clipboard: {
      writeText: function (t) { copiedText = t; return Promise.resolve(); }
    }
  }
});
var rootTheme = null;
globalThis.document.documentElement = {
  setAttribute: function (k, v) {
    if (k === "data-theme") { rootTheme = v; }
  }
};
globalThis.fetch = function () {
  return Promise.resolve({
    json: function () {
      return Promise.resolve({
        subject: "test subject",
        mermaid: "graph TD;\\ndev(dev)\\ntest(test)\\nreview(review)\\n",
        nodes: ["dev", "test", "review"],
        flow: [{ src: "dev", dst: "test" }, { src: "test", dst: "review" }],
        edges: [{ src: "review", dst: "dev", label: "NEEDS CHANGES" }]
      });
    }
  });
};
globalThis.requestAnimationFrame = function (fn) { fn(); };
globalThis.setTimeout = function () { return 0; };
globalThis.setInterval = function (fn) { intervalFn = fn; return 1; };
globalThis.clearInterval = function () {};
var fakeNow = 0;
Date.now = function () { return fakeNow; };
globalThis.EventSource = function (url) {
  this.url = url;
  this.onmessage = null;
  this.onerror = null;
  this.close = function () { closeCalls++; };
  esInstances.push(this);
  esInstance = this;
};

(0, eval)(PAGE_SCRIPT);

function badges() {
  var box = document.getElementById("verdicts");
  return box.children.map(function (c) { return c.textContent; });
}
function nodeClasses() {
  var out = {};
  for (var k in registry) {
    var e = registry[k];
    if (e.id && e.id.indexOf("station-") === 0) {
      var m = /st-(\S+)/.exec(e._class);
      if (m) { out[e.id.slice(8)] = m[1]; }
    }
  }
  return out;
}
function fire(ev) { esInstance.onmessage({ data: JSON.stringify(ev) }); }

function stageTimes() {
  var out = {};
  ["dev", "test", "review"].forEach(function (n) {
    var section = document.getElementById("stage-" + n);
    var header = section.querySelector(".stage-header");
    var t = header.querySelector(".stage-time");
    out[n] = t ? t.textContent : null;
  });
  return out;
}
function totalText() {
  var total = document.getElementById("total");
  return { text: total.textContent, display: total.style.display };
}

function watchdogState() {
  var banner = document.getElementById("stream-banner");
  return {
    instances: esInstances.length,
    closeCalls: closeCalls,
    reloadCalls: reloadCalls,
    bannerDisplay: banner ? banner.style.display : null
  };
}

function graphLines() {
  var box = document.getElementById("graph");
  var out = [];
  box.children.forEach(function (c) {
    if ((" " + c._class + " ").indexOf(" ln-") !== -1) {
      out.push({ cls: c._class, dir: c.getAttribute("data-dir"),
                 w: c.style.width, h: c.style.height });
    }
  });
  return out;
}

function graphArrows() {
  var box = document.getElementById("graph");
  var out = [];
  box.children.forEach(function (c) {
    if ((" " + c._class + " ").indexOf(" arr-") !== -1) {
      out.push({ cls: c._class });
    }
  });
  return out;
}

var snapshots = [];
async function main() {
  // let the topology fetch microtask chain resolve so events are handled
  // live (synchronously inside es.onmessage) instead of buffered
  await new Promise(function (res) { setImmediate(res); });
  // Events arrive serially over the stream; process them the same way so
  // each __copy snapshot reflects exactly one click (an async forEach would
  // let later clicks run before earlier snapshots are taken).
  for (var i = 0; i < EVENTS.length; i++) {
    var ev = EVENTS[i];
    if (ev.type === "__tick") {
      fakeNow = ev.now;
      if (intervalFn) { intervalFn(); }
      continue;
    }
    if (ev.type === "__snapshot") {
      snapshots.push({ watchdog: watchdogState() });
      continue;
    }
    if (ev.type === "__visibility") {
      fakeNow = ev.now;
      var visFn = document._listeners["visibilitychange"];
      if (visFn) { visFn(); }
      continue;
    }
    if (ev.type === "__graph") {
      snapshots.push({ lines: graphLines(), arrows: graphArrows() });
      continue;
    }
    if (ev.type === "__theme") {
      var tbtn = document.getElementById("theme-toggle");
      snapshots.push({ theme: rootTheme, label: tbtn.getAttribute("aria-label") });
      tbtn.listeners["click"]();
      snapshots.push({ theme: rootTheme, label: tbtn.getAttribute("aria-label") });
      continue;
    }
    if (ev.type === "__copy") {
      var tid = document.getElementById("thread-id");
      if (tid.listeners["click"]) { tid.listeners["click"](); }
      await new Promise(function (res) { setImmediate(res); });
      snapshots.push({ copy: { text: tid.textContent, cls: tid._class,
                               copied: copiedText } });
      continue;
    }
    fire(ev);
    if (ev.type === "state") {
      snapshots.push({ ev: ev, badges: badges(), classes: nodeClasses(),
                       times: stageTimes(), total: totalText(),
                       timer: intervalFn !== null });
    }
  }
  // exercise the copy button on the dev console
  var dev = document.getElementById("stage-dev");
  var copyBtn = dev.querySelector(".console-wrap").querySelector(".copy-btn");
  copyBtn.listeners["click"]();
  await new Promise(function (res) { setImmediate(res); });
  snapshots.push({ copy: { text: copiedText,
                           title: copyBtn.getAttribute("title") },
                   times: stageTimes(), total: totalText(),
                   timer: intervalFn !== null });
  console.log(JSON.stringify(snapshots));
}
main();
"""

    @pytest.fixture
    def node(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")
        return node

    def _run_page(self, node, events, tmp_path):
        match = self.INLINE_SCRIPT.search(PAGE)
        assert match, "inline page script not found in PAGE"
        script = match.group(0)[len("<script>\n"):-len("</script>")]
        layout_js = (Path(__file__).parent.parent / "src" / "agentic_dev_loop"
                     / "viz" / "assets" / "pipeline-layout.js").read_text()
        js = ("var PAGE_SCRIPT = " + json.dumps(layout_js + "\n" + script)
              + ";\n" + "var EVENTS = " + json.dumps(events) + ";\n"
              + self.HARNESS)
        script_file = tmp_path / "page_script_test.js"
        script_file.write_text(js)
        proc = subprocess.run([node, str(script_file)], capture_output=True,
                              text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    def test_attempt_change_keeps_latest_results(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t"},
            {"type": "node_finished", "node": "test", "ok": True},
            {"type": "state", "attempt": 1,
             "step_verdicts": {"test": "pass"}, "failed": False},
            {"type": "node_finished", "node": "review", "ok": True},
            {"type": "state", "attempt": 1,
             "step_verdicts": {"test": "pass", "review": "fail"},
             "failed": False},
            # the bump node: review failed, so the engine loops back to dev
            {"type": "state", "attempt": 2, "step_verdicts": {},
             "failed": False},
            {"type": "node_finished", "node": "test", "ok": True},
            {"type": "state", "attempt": 2,
             "step_verdicts": {"test": "pass"}, "failed": False},
        ]
        snaps = self._run_page(node, events, tmp_path)

        # within attempt 1 both verdicts show
        assert snaps[0]["badges"] == ["test: pass"]
        assert snaps[0]["classes"]["test"] == "pass"
        assert snaps[1]["badges"] == ["test: pass", "review: fail"]
        assert snaps[1]["classes"]["review"] == "fail"
        # the loopback must NOT wipe the latest results - the stage pills
        # and node colours survive the attempt bump
        assert snaps[2]["badges"] == ["test: pass", "review: fail"]
        assert snaps[2]["classes"]["test"] == "pass"
        assert snaps[2]["classes"]["review"] == "fail"
        # re-running the gate on attempt 2 updates its own results only
        assert snaps[3]["badges"] == ["test: pass", "review: fail"]
        assert snaps[3]["classes"]["test"] == "pass"
        assert snaps[3]["classes"]["review"] == "fail"

    def test_same_attempt_accumulates_badges_without_reset(self, node,
                                                           tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t"},
            {"type": "node_finished", "node": "test", "ok": True},
            {"type": "state", "attempt": 1,
             "step_verdicts": {"test": "pass"}, "failed": False},
            {"type": "node_finished", "node": "review", "ok": True},
            {"type": "state", "attempt": 1,
             "step_verdicts": {"test": "pass", "review": "fail"},
             "failed": False},
        ]
        snaps = self._run_page(node, events, tmp_path)

        assert snaps[0]["badges"] == ["test: pass"]
        # a same-attempt state event must NOT reset - the test pass badge
        # survives and the review fail badge joins it
        assert snaps[1]["badges"] == ["test: pass", "review: fail"]
        assert snaps[1]["classes"]["test"] == "pass"
        assert snaps[1]["classes"]["review"] == "fail"

    def test_unverifiable_verdict_badge_and_class(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t"},
            {"type": "node_finished", "node": "review", "ok": True},
            {"type": "state", "attempt": 1,
             "step_verdicts": {"review": "unverifiable"}, "failed": False},
        ]
        snaps = self._run_page(node, events, tmp_path)

        assert snaps[0]["badges"] == ["review: unverifiable"]
        assert snaps[0]["classes"]["review"] == "unverifiable"

    def test_env_verdict_badge_and_fatal_class(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t"},
            {"type": "node_finished", "node": "review", "ok": True},
            {"type": "state", "attempt": 1,
             "step_verdicts": {"review": "env"}, "failed": False},
        ]
        snaps = self._run_page(node, events, tmp_path)

        assert snaps[0]["badges"] == ["review: env"]
        assert snaps[0]["classes"]["review"] == "fatal"

    def test_copy_button_copies_console_text(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t"},
            {"type": "console", "node": "dev", "stream": "stdout",
             "text": "first line"},
            {"type": "console", "node": "dev", "stream": "stderr",
             "text": "\x1b[31mred\x1b[0m line"},
        ]
        snaps = self._run_page(node, events, tmp_path)

        # the copied text is the console lines with ANSI markup stripped
        assert snaps[-1]["copy"]["text"] == "first line\nred line"
        assert snaps[-1]["copy"]["title"] == "Copied!"

    def test_copy_button_nothing_to_copy_on_empty_console(self, node,
                                                          tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t"},
        ]
        snaps = self._run_page(node, events, tmp_path)

        # no console lines: no clipboard write, informative title instead
        assert snaps[-1]["copy"]["text"] is None
        assert snaps[-1]["copy"]["title"] == "Nothing to copy"


    def test_step_header_shows_accumulated_time(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t", "started_at": 1000},
            {"type": "node_started", "node": "dev"},
            {"type": "node_finished", "node": "dev", "ok": True,
             "elapsed_seconds": 135},
            {"type": "state", "attempt": 1, "failed": False},
            {"type": "node_finished", "node": "dev", "ok": True,
             "elapsed_seconds": 45},
            {"type": "state", "attempt": 1, "failed": False},
        ]
        snaps = self._run_page(node, events, tmp_path)

        assert snaps[-1]["times"]["dev"] == "3:00"
        assert snaps[-1]["times"]["test"] == ""
        assert snaps[-1]["times"]["review"] == ""

    def test_running_stage_shows_live_heartbeat_time(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t", "started_at": 1000},
            {"type": "node_started", "node": "dev"},
            {"type": "heartbeat", "node": "dev", "elapsed_seconds": 27},
            {"type": "state", "attempt": 1, "failed": False},
            {"type": "node_finished", "node": "dev", "ok": True,
             "elapsed_seconds": 28},
            {"type": "state", "attempt": 1, "failed": False},
        ]
        snaps = self._run_page(node, events, tmp_path)

        assert snaps[0]["times"]["dev"] == "0:27"
        assert snaps[1]["times"]["dev"] == "0:28"

    def test_loop_total_ticks_then_freezes(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t", "started_at": 1000},
            {"type": "__tick", "now": 1065000},
            {"type": "state", "attempt": 1, "failed": False},
            {"type": "run_finished", "success": True, "attempts": 1,
             "elapsed_seconds": 200},
        ]
        snaps = self._run_page(node, events, tmp_path)

        assert snaps[0]["total"]["text"] == "total 1:05"
        assert snaps[0]["total"]["display"] == "block"
        assert snaps[-1]["total"]["text"] == "total 3:20"

    def test_loop_total_fallback_without_started_at(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t"},
            {"type": "state", "attempt": 1, "failed": False},
            {"type": "run_finished", "success": True, "attempts": 1,
             "elapsed_seconds": 95},
        ]
        snaps = self._run_page(node, events, tmp_path)

        assert snaps[0]["timer"] is True
        assert snaps[0]["total"] == {"text": "", "display": "none"}
        assert snaps[-1]["total"]["text"] == "total 1:35"
        assert snaps[-1]["total"]["display"] == "block"

    def test_silence_while_run_active_recreates_stream(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t", "started_at": 1000},
            {"type": "node_started", "node": "dev"},
            {"type": "heartbeat", "node": "dev", "elapsed_seconds": 5},
            {"type": "__tick", "now": 15000},
            {"type": "__snapshot"},
        ]
        snaps = self._run_page(node, events, tmp_path)

        wd = snaps[0]["watchdog"]
        assert wd["instances"] >= 2
        assert wd["closeCalls"] == 1
        assert wd["bannerDisplay"] == "block"
        assert wd["reloadCalls"] == 0

    def test_silence_when_idle_does_not_recreate(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t"},
            {"type": "run_finished", "success": True, "attempts": 1,
             "elapsed_seconds": 60},
            {"type": "__tick", "now": 120000},
            {"type": "__snapshot"},
        ]
        snaps = self._run_page(node, events, tmp_path)

        wd = snaps[0]["watchdog"]
        assert wd["instances"] == 1
        assert wd["bannerDisplay"] == "none"
        assert wd["reloadCalls"] == 0

    def test_recovery_rearms_after_replay(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t", "started_at": 1000},
            {"type": "node_started", "node": "dev"},
            {"type": "heartbeat", "node": "dev", "elapsed_seconds": 5},
            {"type": "__tick", "now": 15000},
            {"type": "__snapshot"},
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t", "started_at": 1000},
            {"type": "node_started", "node": "dev"},
            {"type": "__tick", "now": 24000},
            {"type": "__snapshot"},
            {"type": "__tick", "now": 26000},
            {"type": "__snapshot"},
        ]
        snaps = self._run_page(node, events, tmp_path)

        assert snaps[0]["watchdog"]["instances"] >= 2
        assert snaps[1]["watchdog"]["instances"] == 2
        assert snaps[1]["watchdog"]["bannerDisplay"] == "none"
        assert snaps[2]["watchdog"]["instances"] >= 3
        assert snaps[2]["watchdog"]["closeCalls"] >= 2

    def test_repeated_failures_reload(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t", "started_at": 1000},
            {"type": "node_started", "node": "dev"},
            {"type": "__tick", "now": 11000},
            {"type": "__tick", "now": 22000},
            {"type": "__tick", "now": 33000},
            {"type": "__tick", "now": 44000},
            {"type": "__snapshot"},
        ]
        snaps = self._run_page(node, events, tmp_path)

        wd = snaps[0]["watchdog"]
        assert wd["reloadCalls"] == 1
        assert wd["instances"] == 4
        assert wd["closeCalls"] == 3

    def test_visibility_return_triggers_recovery(self, node, tmp_path):
        events = [
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t", "started_at": 1000},
            {"type": "node_started", "node": "dev"},
            {"type": "heartbeat", "node": "dev", "elapsed_seconds": 5},
            {"type": "__visibility", "now": 15000},
            {"type": "__snapshot"},
            {"type": "heartbeat", "node": "dev", "elapsed_seconds": 16},
            {"type": "__visibility", "now": 15000},
            {"type": "__snapshot"},
        ]
        snaps = self._run_page(node, events, tmp_path)

        assert snaps[0]["watchdog"]["instances"] >= 2
        assert snaps[0]["watchdog"]["closeCalls"] == 1
        assert snaps[0]["watchdog"]["reloadCalls"] == 0
        assert snaps[1]["watchdog"]["instances"] == 2
        assert snaps[1]["watchdog"]["bannerDisplay"] == "none"

    def test_graph_flow_lines_have_visible_thickness(self, node, tmp_path):
        # makeLine used to zero the thickness dimension (height for horizontal
        # lines, width for vertical), so the background-painted on_pass rails
        # were invisible in the browser while the dashed on_fail edges
        # (painted with borders) still showed. Every line must keep a 2px
        # thickness on its cross axis.
        snaps = self._run_page(node, [{"type": "__graph"}], tmp_path)
        lines = snaps[0]["lines"]
        assert lines, "no graph lines rendered"
        flow = [l for l in lines if "ln-flow" in l["cls"]]
        assert flow, "no on_pass flow lines rendered"
        for l in lines:
            if l["dir"] == "h":
                assert l["h"] == "2px", f"horizontal line has height {l['h']}"
                assert l["w"] != "0px", f"horizontal line has width {l['w']}"
            else:
                assert l["w"] == "2px", f"vertical line has width {l['w']}"
                assert l["h"] != "0px", f"vertical line has height {l['h']}"

    def test_theme_toggle_flips_theme_and_label(self, node, tmp_path):
        # The SVG glyphs are shown/hidden by CSS off the data-theme attribute,
        # so the toggle must keep setting it (and the aria-label) on click.
        snaps = self._run_page(node, [{"type": "__theme"}], tmp_path)
        assert snaps[0]["theme"] == "dark"
        assert snaps[0]["label"] == "Switch to light theme"
        assert snaps[1]["theme"] == "light"
        assert snaps[1]["label"] == "Switch to dark theme"

    def test_thread_id_click_copies_and_gives_feedback(self, node, tmp_path):
        # Clicking the thread id must copy it to the clipboard and show
        # visible feedback; the "--" placeholder and the feedback state are
        # not copyable.
        snaps = self._run_page(node, [
            {"type": "__copy"},
            {"type": "run_started", "subject": "s", "max_attempts": 5,
             "attempt": 1, "thread_id": "t-123"},
            {"type": "__copy"},
            {"type": "__copy"},
        ], tmp_path)
        # placeholder: click must do nothing (harness starts with an empty
        # code element; the real page starts with "--" - the guard ignores
        # both, no clipboard write, no feedback class)
        assert snaps[0]["copy"]["text"] == ""
        assert snaps[0]["copy"]["copied"] is None
        assert "copied" not in snaps[0]["copy"]["cls"].split()
        # live id: click copies and flips to the "Copied" feedback state
        assert snaps[1]["copy"]["copied"] == "t-123"
        assert snaps[1]["copy"]["text"] == "Copied"
        assert "copied" in snaps[1]["copy"]["cls"].split()
        # feedback state: a further click must not re-copy or re-trigger
        assert snaps[2]["copy"]["text"] == "Copied"
        assert "copied" in snaps[2]["copy"]["cls"].split()

    def test_fail_edge_lights_amber_class_when_node_fails(self, node, tmp_path):
        # A dashed on_fail edge must carry the "on" class while its source is
        # in a failed state (and drop it again when the retry passes), so CSS
        # can paint it amber like the green lit flow rails.
        snaps = self._run_page(node, [
            {"type": "node_finished", "node": "review", "ok": False},
            {"type": "__graph"},
            {"type": "node_finished", "node": "review", "ok": True},
            {"type": "__graph"},
        ], tmp_path)
        failed = snaps[0]
        back_lines = [l for l in failed["lines"] if "ln-back" in l["cls"]]
        back_arrows = [a for a in failed["arrows"] if "arr-back" in a["cls"]]
        assert back_lines, "no dashed back lines rendered"
        assert back_arrows, "no back arrowheads rendered"
        assert all(" on" in (" " + l["cls"] + " ") for l in back_lines)
        assert all(" on" in (" " + a["cls"] + " ") for a in back_arrows)
        passed = snaps[1]
        back_lines = [l for l in passed["lines"] if "ln-back" in l["cls"]]
        assert back_lines, "no dashed back lines rendered after retry pass"
        assert all(" on" not in (" " + l["cls"] + " ") for l in back_lines)


class TestVizPageAssets:
    ROOT = Path(__file__).parent.parent

    def test_generated_page_matches_committed_assets(self):
        proc = subprocess.run(
            [sys.executable, str(self.ROOT / "tools" / "build_viz_page.py"),
             "--check"], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr

    def test_page_is_ascii_and_mermaid_free(self):
        PAGE.encode("ascii")
        assert "mermaid" not in PAGE.lower()

    def test_no_external_script_dependencies(self):
        # The Carbon import never resolved in browsers: the jsdelivr /es/
        # chain pulls bare specifiers ("tslib", "lit") that no browser can
        # resolve without an import map, so it threw on every page load. The
        # page is fully styled by its own hand-written token skin, so the
        # dead import is removed - no external scripts may come back.
        assert "@carbon/web-components" not in PAGE
        assert "cdn.jsdelivr" not in PAGE
        assert '<script type="module">' not in PAGE

    def test_graph_is_an_accessible_image(self):
        assert 'id="graph"' in PAGE
        assert 'role="img"' in PAGE
        assert 'id="pipeline-panel"' in PAGE

    def test_no_resize_divider(self):
        # The pipeline panel is not user-resizable; sections scroll instead.
        assert "divider" not in PAGE.lower()

    def _rule(self, selector):
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", PAGE)
        assert match, f"{selector} rule missing from PAGE"
        return match.group(1)

    def test_lower_sections_scroll_instead_of_clipping(self):
        # Regression: `.lower` used `flex-wrap: wrap`, making it a multi-line
        # flex container, so the stages/log panels kept their content height,
        # `#stages`/`#run-log` never scrolled, and body overflow:hidden
        # chopped the overflow off with no scrollbar.
        assert "flex-wrap" not in self._rule(".lower")
        assert "min-width: 0" in self._rule(".panel-stages")
        assert "min-width: 0" in self._rule(".panel-log")
        assert "overflow-y: auto" in self._rule("#stages")
        assert "overflow-y: auto" in self._rule("#run-log")

    def test_pipeline_panel_keeps_graph_height(self):
        # The panel never shrinks below the graph: it is not a flex shock
        # absorber for the lower section. A viewport-relative cap keeps a
        # pathologically tall graph scrolling instead of starving the lower
        # section entirely.
        rule = self._rule("#pipeline-panel")
        assert "flex: 0 0 auto" in rule
        assert "max-height: 60vh" in rule
        assert "overflow: auto" in rule

    def test_no_auto_expand_collapse(self):
        # Stages only open/close when the user clicks them; the run no longer
        # expands the current step or collapses stages on its own.
        for fn in ("expandStage", "collapseStage", "collapseAll"):
            assert fn not in PAGE
        assert 'classList.toggle("collapsed")' in PAGE

    def test_theme_glyphs_are_svg_icons(self):
        # The CSS-drawn glyphs were ambiguous: the sun was a plain circle (its
        # box-shadow "rays" shrank to nothing) and the moon a circle with a
        # slightly offset overlay. Crisp inline SVGs replace them, shown/hidden
        # by CSS off the data-theme attribute: dark theme shows the sun (click
        # to switch to light), light theme shows the moon.
        assert '<svg class="ic-sun"' in PAGE
        assert '<svg class="ic-moon"' in PAGE
        assert "box-shadow: 0 -6px" not in PAGE  # broken ray trick removed
        assert "position: absolute" in self._rule(".ic-sun, .ic-moon")
        assert "display: none" in self._rule(':root[data-theme="light"] .ic-sun')
        assert "display: none" in self._rule(':root[data-theme="dark"] .ic-moon')

    def test_graph_flow_lines_have_visible_thickness(self):
        # Regression: makeLine zeroed the thickness dimension (height for
        # horizontal lines, width for vertical), so the background-painted
        # on_pass lines were invisible while the dashed on_fail borders still
        # rendered. The zeroing statements must be gone from the page script.
        assert 'if (l.dir === "v") { d.style.width = "0"; }' not in PAGE
        assert 'if (l.dir === "h") { d.style.height = "0"; }' not in PAGE
        assert 'd.style.width = (l.dir === "h" ? l.w : 2) + "px"' in PAGE

    def test_back_edges_light_amber_when_used(self):
        # Used fail routes (dashed) paint with the warning (amber) token,
        # mirroring the success-green lit flow rails. The arrows are up
        # triangles painted with border-bottom.
        assert "var(--cds-support-warning)" in self._rule(".ln-back.on")
        assert "var(--cds-support-warning)" in self._rule(".arr-back.on.arr-up")

    def test_arrow_classes_match_kind_for_status_skins(self):
        # makeArrow must emit "arr-" + kind so the status skins (.arr-flow.on,
        # .arr-back.on) can match; it used to emit the bare kind ("flow" /
        # "back"), which no CSS selector ever selected.
        assert '"arr arr-" + a.dir + " arr-" + a.kind' in PAGE

    def test_summary_tiles_wrap_at_natural_breaks(self):
        # Regression: `.big` used `word-break: break-all`, which slices words
        # at any character ("plan dem/o.md", "tota/l 0:27"). Natural break
        # points must win; long unbroken tokens still wrap as a fallback via
        # overflow-wrap. (Console output keeps word-break: break-all on
        # purpose - it must wrap dense log lines.)
        rule = self._rule(".big")
        assert "overflow-wrap: break-word" in rule
        assert "word-break" not in rule

    def test_summary_tiles_stack_two_per_row_on_mobile(self):
        # Regression: on the <=760px layout the tiles kept `flex: 1 1 0`, so
        # flex-basis 0 meant all four always fit one line, `flex-wrap: wrap`
        # never engaged, and each tile crushed to ~85px. A percentage basis
        # forces a 2x2 stack on narrow screens.
        m = re.search(r"@media \(max-width: 760px\) \{(.*?)\}\s*</style>",
                      PAGE, re.S)
        assert m, "mobile media query missing from PAGE"
        block = m.group(1)
        rule = re.search(re.escape(".tiles .tile") + r"\s*\{([^}]*)\}", block)
        assert rule, "mobile .tiles .tile override missing"
        assert "flex: 1 1 45%" in rule.group(1)

    def test_thread_id_click_copies_to_clipboard_with_feedback(self):
        # Clicking the thread id copies it and shows transient success
        # feedback. The element must be focusable/announced as a button, and
        # the old selection-only handler (createRange/getSelection) is gone.
        assert ('<code id="thread-id" tabindex="0" role="button" '
                'title="click to copy">--</code>') in PAGE
        assert "navigator.clipboard" in PAGE
        assert "writeText" in PAGE
        assert 'textContent = "Copied"' in PAGE
        assert "initThreadSelect" not in PAGE  # selection-only handler replaced
        assert "selectFallback" in PAGE  # clipboard-less fallback kept
        assert "var(--cds-support-success)" in self._rule("#thread-id.copied")

    def test_viz_asset_register_lint(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")
        proc = subprocess.run(
            [node, str(self.ROOT / "frontend" / "scripts"
                       / "lint-figma-primitives.mjs"),
             "--assets", "src/agentic_dev_loop/viz/assets",
             "--register", "frontend/viz-register.md"],
            capture_output=True, text=True, cwd=self.ROOT, timeout=60)
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestProvider:
    def test_provider_name_mapping(self):
        from agentic_dev_loop.engine.provider_wire import provider_name
        assert provider_name("https://api.githubcopilot.com/") == "Copilot"
        assert provider_name("https://api.deepseek.com") == "OpenAI"
        assert provider_name(None) == "OpenAI"
        assert provider_name("") == "OpenAI"

    def test_provider_in_topology(self):
        import urllib.request
        bus = VizBus()
        httpd, _t = serve_viz(bus, MERMAID, "subject", 0, provider="Copilot")
        try:
            url = f"http://127.0.0.1:{httpd.server_address[1]}/topology"
            body = json.loads(urllib.request.urlopen(url).read())
        finally:
            httpd.shutdown()
            httpd.server_close()
        assert body["provider"] == "Copilot"

    def test_provider_handler_class_forwards(self):
        cls = _handler_class(VizBus(), MERMAID, "s", [], provider="Copilot")
        assert cls.viz_provider == "Copilot"
