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
                              _handler_class, serve_viz, topology_mermaid,
                              translate)

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

        chain = ["brief", "dev", "restore", "test", "review", "qa", "commit", "success"]
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

    def test_detach_idempotent(self, monkeypatch):
        bus = VizBus()
        cap, _out, _err = self._attach(bus, StubWatch(None), monkeypatch)
        cap.detach()
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
    def test_idle_before_start(self):
        watch = NodeWatch()
        assert watch.current() is None
        assert watch.elapsed_seconds() is None

    def test_start_then_finish(self):
        watch = NodeWatch()
        watch.start("dev")
        assert watch.current() == "dev"
        assert isinstance(watch.elapsed_seconds(), int)
        watch.finish("dev")
        assert watch.current() is None
        assert watch.elapsed_seconds() is None

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
        assert 'id="mermaid-container"' in body
        assert 'id="subject"' in body
        assert "agentic-dev-loop pipeline" in body  # topic placeholder

    def test_topology_json_round_trip(self, server):
        _bus, _httpd, _thread, port = server
        status, body = http_get(port, "/topology")
        assert status == 200
        assert json.loads(body) == {"mermaid": MERMAID, "subject": "plan plan.md",
                                    "nodes": []}

    def test_topology_round_trip_includes_nodes(self):
        bus = VizBus()
        httpd, thread = serve_viz(bus, MERMAID, "subject", 0,
                                  ["dev", "success"])
        port = httpd.server_address[1]
        try:
            status, body = http_get(port, "/topology")
            assert status == 200
            assert json.loads(body) == {"mermaid": MERMAID, "subject": "subject",
                                        "nodes": ["dev", "success"]}
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

    def test_reuse_safe_server_keeps_so_reuseaddr_on_posix(self):
        if sys.platform == "win32":
            pytest.skip("POSIX socket semantics only")
        assert _ReuseSafeServer.allow_reuse_address is True

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

    def test_page_constant_has_fallback_hook(self):
        assert "onerror" in PAGE
        assert "mermaid" in PAGE

    def test_page_constant_has_nodes_filter_hook(self):
        assert "nodeIds" in PAGE
        assert "nodeIds.has(n)" in PAGE

    def test_page_constant_has_mobile_media_query(self):
        assert "@media" in PAGE
        assert "max-width: 768px" in PAGE
        assert "flex-direction: column" in PAGE

    def test_page_constant_attempt_fallback_has_no_em_dash(self):
        assert "attempt ?/" in PAGE
        assert "\u2013/\u2013" not in PAGE

    def test_page_constant_logs_only_drawn_nodes(self):
        assert "nodeIds.has(ev.node)" in PAGE

    def test_page_constant_completion_wording(self):
        assert "completed" in PAGE
        assert "errored" in PAGE
        assert "verdict" in PAGE
        assert " passed" not in PAGE

    def test_page_constant_verdict_status_precedence(self):
        assert "statusOf" in PAGE
        assert "verdictFail" in PAGE
        assert "verdictPass" in PAGE

    def test_page_constant_has_unverifiable_verdict_support(self):
        assert "classDef unverifiable" in PAGE
        assert ".badge-unverifiable" in PAGE
        assert ".status-unverifiable" in PAGE
        assert "verdictUnverifiable" in PAGE
        # the new value maps to badge-unverifiable, not the unclear fallthrough
        assert 'else if (v === "unverifiable")' in PAGE
        assert " (verdict: UNVERIFIABLE)" in PAGE

    def test_page_constant_has_env_verdict_support(self):
        assert "verdictEnv" in PAGE
        # the new value maps to the fatal badge, never the unclear fallthrough
        assert 'else if (v === "env")' in PAGE
        assert " (verdict: ENV)" in PAGE
        assert 'verdictEnv.has(n)' in PAGE

    def test_page_constant_color_palette(self):
        assert 'classDef running fill:#1976d2' in PAGE
        assert 'classDef fail fill:#ffb300' in PAGE
        assert 'classDef fatal fill:#c62828' in PAGE

    def test_page_constant_terminal_failure_marking(self):
        assert "fatalNodes" in PAGE
        assert "ev.failed" in PAGE

    def test_page_constant_has_heartbeat(self):
        assert "heartbeat" in PAGE
        assert "elapsed_seconds" in PAGE
        assert "elapsed" in PAGE

    def test_page_constant_clears_elapsed(self):
        assert "clearElapsed" in PAGE
        assert 'id="elapsed"' in PAGE

    def test_page_constant_buffers_sse_until_topology_ready(self):
        assert "topologyReady" in PAGE
        assert "pendingEvents" in PAGE
        assert "pendingEvents.push(ev)" in PAGE
        assert "pendingEvents.forEach(handle)" in PAGE

    def test_page_constant_flushes_on_topology_failure_and_timeout(self):
        assert "failed to load topology" in PAGE
        assert "topology load timed out" in PAGE
        assert "5000" in PAGE

    def test_page_constant_has_stage_sections(self):
        assert "stage-header" in PAGE
        assert "stage-console" in PAGE
        assert "collapsed" in PAGE

    def test_page_constant_has_copy_button(self):
        assert "copy-btn" in PAGE
        assert "console-wrap" in PAGE
        assert "COPY_ICON" in PAGE
        assert "CHECK_ICON" in PAGE
        assert "navigator.clipboard" in PAGE
        assert "writeText" in PAGE
        # float+sticky, not absolute: the button lives in the wrap's content
        # box so the scrollbar (outside the content box) can't cover it
        assert "position: sticky" in PAGE
        assert "float: right" in PAGE
        assert "position: absolute" not in PAGE
        assert "has-scrollbar" not in PAGE

    def test_page_constant_has_console_live_tail(self):
        assert "scrollHeight" in PAGE
        assert "scrollTop" in PAGE
        assert "MAX_CONSOLE" in PAGE
        assert '"console"' in PAGE

    def test_page_constant_routes_console_through_node_guard(self):
        assert "nodeIds.has(ev.node)" in PAGE

    def test_page_constant_has_ansi_renderer(self):
        assert "ansiToHtml" in PAGE
        assert "\\x1b" in PAGE
        assert "38;5" in PAGE
        assert "38;2" in PAGE

    def test_page_constant_restores_running_pulse(self):
        assert ".running > *" in PAGE
        assert "animation: pulse" in PAGE
        assert "@keyframes pulse" in PAGE

    def test_page_constant_stage_placeholder(self):
        assert "stage-placeholder" in PAGE
        assert "No output" in PAGE

    def test_page_constant_console_tail_robust(self):
        assert "nearBottom" in PAGE
        assert "requestAnimationFrame" in PAGE

    def test_page_constant_has_resizable_divider(self):
        assert '"divider"' in PAGE
        assert "col-resize" in PAGE
        assert "mousedown" in PAGE
        assert "mousemove" in PAGE
        assert "mouseup" in PAGE
        assert "dragging" in PAGE

    def test_page_constant_resets_verdicts_on_run_start(self):
        assert "resetVerdicts" in PAGE
        # no attempt-bump reset: verdicts must survive a retry loopback
        assert "verdictAttempt" not in PAGE
        assert "ev.attempt !== verdictAttempt" not in PAGE
        assert "currentVerdicts[k] = v" in PAGE


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

var STATIC_IDS = ["subject", "attempt", "thread-id", "elapsed", "verdicts",
                  "stages", "run-log", "mermaid-container", "run-banner",
                  "divider", "sidebar"];
STATIC_IDS.forEach(function (id) { registry[id] = new El(id); });
var elCounter = 0;
var document = {
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
var window = { __mermaidFailed: false, addEventListener: function () {},
               innerWidth: 1000 };
var mermaidCalls = [];
var esInstance = null;

globalThis.document = document;
globalThis.window = window;
var copiedText = null;
// node 21+ ships a read-only `navigator` global, so defineProperty is needed
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    clipboard: {
      writeText: function (t) { copiedText = t; return Promise.resolve(); }
    }
  }
});
globalThis.mermaid = {
  initialize: function () {},
  render: function (_id, src) {
    mermaidCalls.push(src);
    return Promise.resolve({ svg: "<svg/>", bindFunctions: null });
  }
};
globalThis.fetch = function () {
  return Promise.resolve({
    json: function () {
      return Promise.resolve({
        subject: "test subject",
        mermaid: "graph TD;\\ndev(dev)\\ntest(test)\\nreview(review)\\n",
        nodes: ["dev", "test", "review"]
      });
    }
  });
};
globalThis.requestAnimationFrame = function (fn) { fn(); };
globalThis.setTimeout = function () { return 0; };
globalThis.EventSource = function (url) {
  this.url = url;
  this.onmessage = null;
  this.onerror = null;
  esInstance = this;
};

(0, eval)(PAGE_SCRIPT);

function badges() {
  var box = document.getElementById("verdicts");
  return box.children.map(function (c) { return c.textContent; });
}
function nodeClasses() {
  var src = mermaidCalls[mermaidCalls.length - 1];
  var out = {};
  var re = /class (\S+) (\S+);/g, m;
  while ((m = re.exec(src))) { out[m[1]] = m[2]; }
  return out;
}
function fire(ev) { esInstance.onmessage({ data: JSON.stringify(ev) }); }

var snapshots = [];
async function main() {
  // let the topology fetch microtask chain resolve so events are handled
  // live (synchronously inside es.onmessage) instead of buffered
  await new Promise(function (res) { setImmediate(res); });
  EVENTS.forEach(function (ev) {
    fire(ev);
    if (ev.type === "state") {
      snapshots.push({ ev: ev, badges: badges(), classes: nodeClasses() });
    }
  });
  // exercise the copy button on the dev console
  var dev = document.getElementById("stage-dev");
  var copyBtn = dev.querySelector(".console-wrap").querySelector(".copy-btn");
  copyBtn.listeners["click"]();
  await new Promise(function (res) { setImmediate(res); });
  snapshots.push({ copy: { text: copiedText,
                           title: copyBtn.getAttribute("title") } });
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
        js = ("var PAGE_SCRIPT = " + json.dumps(script) + ";\n"
              + "var EVENTS = " + json.dumps(events) + ";\n"
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


class TestDemo:
    def test_run_started_carries_attempt(self, monkeypatch):
        import viz_demo

        monkeypatch.setattr(viz_demo.time, "sleep", lambda *_a: None)
        bus = VizBus()
        viz_demo.run_scenario(bus, "success")
        q = bus.subscribe()
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        run_started = next(e for e in events if e["type"] == "run_started")
        assert run_started["attempt"] == 1

    def test_demo_emits_heartbeats(self, monkeypatch):
        import viz_demo

        monkeypatch.setattr(viz_demo.time, "sleep", lambda *_a: None)
        bus = VizBus()
        q = bus.subscribe()  # heartbeats are live-only, never replayed
        viz_demo.run_scenario(bus, "success")
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        heartbeats = [e for e in events if e["type"] == "heartbeat"]
        assert heartbeats
        assert all("node" in e and "elapsed_seconds" in e for e in heartbeats)

    def test_demo_emits_node_console(self, monkeypatch):
        import viz_demo

        monkeypatch.setattr(viz_demo.time, "sleep", lambda *_a: None)
        bus = VizBus()
        q = bus.subscribe()
        viz_demo.run_scenario(bus, "success")
        events = []
        while not q.empty():
            events.append(q.get_nowait())

        consoles = [e for e in events if e["type"] == "console"]
        assert consoles
        assert all(e["stream"] in ("stdout", "stderr") for e in consoles)
        dev_stdout = [e for e in consoles if e["node"] == "dev"
                      and e["stream"] == "stdout"]
        assert dev_stdout
        assert any("Running dev step..." in e["text"] for e in dev_stdout)
        ansi = [e for e in consoles if e["stream"] == "stderr"]
        assert ansi
        assert any("\x1b[33m" in e["text"] for e in ansi)
        assert any(e["node"] is None for e in consoles)

    def test_demo_runs_mechanical_test_step(self, monkeypatch):
        import viz_demo

        monkeypatch.setattr(viz_demo.time, "sleep", lambda *_a: None)
        bus = VizBus()
        viz_demo.run_scenario(bus, "success")
        q = bus.subscribe()
        events = []
        while not q.empty():
            events.append(q.get_nowait())

        started = [e["node"] for e in events if e["type"] == "node_started"]
        assert started.count("test") == 2  # the gate runs on both attempts
        assert "test" in started
        consoles = [e["text"] for e in events if e["type"] == "console"]
        assert any("pytest" in t for t in consoles)

    def test_demo_failure_scenario_runs_test_step(self, monkeypatch):
        import viz_demo

        monkeypatch.setattr(viz_demo.time, "sleep", lambda *_a: None)
        bus = VizBus()
        viz_demo.run_scenario(bus, "failure")
        q = bus.subscribe()
        events = []
        while not q.empty():
            events.append(q.get_nowait())

        started = [e["node"] for e in events if e["type"] == "node_started"]
        assert started == ["dev", "test", "review", "qa"]
        run_finished = next(e for e in events
                            if e["type"] == "run_finished")
        assert run_finished["success"] is False

    def test_demo_step_verdicts_are_full_snapshots(self, monkeypatch):
        import viz_demo

        monkeypatch.setattr(viz_demo.time, "sleep", lambda *_a: None)
        bus = VizBus()
        viz_demo.run_scenario(bus, "success")
        q = bus.subscribe()
        events = []
        while not q.empty():
            events.append(q.get_nowait())

        verdict_states = [e["step_verdicts"] for e in events
                          if e["type"] == "state"
                          and "step_verdicts" in e]
        assert verdict_states == [
            {"test": "pass"},
            {"test": "pass", "review": "pass"},
            {"test": "pass", "review": "pass", "qa": "fail"},
            {},  # the bump clears the snapshot for attempt 2
            {"test": "pass"},
            {"test": "pass", "review": "pass"},
            {"test": "pass", "review": "pass", "qa": "pass"},
        ]

