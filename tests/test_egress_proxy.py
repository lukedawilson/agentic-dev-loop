"""Unit-5 egress proxy (code-gen steps 7-8): a portable loopback test server +
the hand-rolled asyncio forward proxy. Allowed hostnames tunnel/forward; an
unmatched hostname is denied and logged as an enforcement event (BR-6)."""

import http.server
import socket
import threading
import time

import pytest

from agentic_dev_loop.engine.egress_policy import EgressPolicy, TargetEntry
from agentic_dev_loop.engine.egress_proxy import EgressProxy


class _Target(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"hello-from-target"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def target_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Target)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    thread.join(timeout=5.0)


@pytest.fixture
def proxy(target_server):
    policy = EgressPolicy(targets=(TargetEntry(host="127.0.0.1"),))
    p = EgressProxy(policy=policy)
    p.start()
    yield p
    p.stop()


def _send_raw(port, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5.0) as s:
        s.sendall(data)
        chunks = []
        while True:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            chunks.append(chunk)
            if b"hello-from-target" in b"".join(chunks) or b"403" in b"".join(chunks):
                break
        return b"".join(chunks)


def test_absolute_uri_forward_allowed(proxy, target_server):
    req = (f"GET http://127.0.0.1:{target_server}/ HTTP/1.1\r\n"
           "Host: 127.0.0.1\r\n\r\n").encode()
    resp = _send_raw(proxy._port, req)
    assert b"200" in resp and b"hello-from-target" in resp
    assert any(e.hostname == "127.0.0.1" and e.allowed for e in proxy.events)


def test_connect_tunnel_allowed(proxy, target_server):
    req = f"CONNECT 127.0.0.1:{target_server} HTTP/1.1\r\n\r\n".encode()
    resp = _send_raw(proxy._port, req)
    assert b"200 Connection Established" in resp


def test_unmatched_hostname_denied_and_logged(proxy):
    req = ("GET http://denied.example/ HTTP/1.1\r\n"
           "Host: denied.example\r\n\r\n").encode()
    resp = _send_raw(proxy._port, req)
    assert b"403" in resp
    assert any(e.hostname == "denied.example" and not e.allowed
               for e in proxy.events)


def test_unmatched_connect_denied_and_logged(proxy):
    req = "CONNECT denied.example:443 HTTP/1.1\r\n\r\n".encode()
    resp = _send_raw(proxy._port, req)
    assert b"403" in resp
    assert any(e.hostname == "denied.example" and not e.allowed
               for e in proxy.events)


def test_connect_port_constraint_matches_exact_port():
    """BR-6: a port-constrained entry must match the exact connection it
    allows. The proxy passes the full host:port through to match_target."""
    import http.server

    class _PortTarget(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _PortTarget)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        policy = EgressPolicy(targets=(TargetEntry(host="127.0.0.1", port=port),))
        p = EgressProxy(policy=policy)
        p.start()
        try:
            # the exact allowed port tunnels
            resp = _send_raw(p._port, f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n\r\n".encode())
            assert b"200 Connection Established" in resp
            # a different port is denied (and logged)
            wrong = port + 1 if port < 65535 else port - 1
            resp = _send_raw(p._port, f"CONNECT 127.0.0.1:{wrong} HTTP/1.1\r\n\r\n".encode())
            assert b"403" in resp
            assert any(e.hostname == "127.0.0.1" and not e.allowed
                       for e in p.events)
        finally:
            p.stop()
    finally:
        server.shutdown()


class _RawTcpServer:
    """A bare TCP listener that records every byte it receives."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received = bytearray()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5.0)
            while True:
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                self.received.extend(chunk)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def test_connect_headers_drained_not_tunnelled():
    """The CONNECT request's header block must be consumed by the proxy, not
    relayed into the tunnel. Clients send the request line, headers and the
    first TLS bytes in one write; leaking the headers corrupts the handshake
    (the upstream answers with a TLS decode alert)."""
    upstream = _RawTcpServer()
    try:
        policy = EgressPolicy(targets=(TargetEntry(host="127.0.0.1"),))
        p = EgressProxy(policy=policy)
        p.start()
        try:
            tls_bytes = b"\x16\x03\x01" + b"post-200-bytes"
            payload = (
                f"CONNECT 127.0.0.1:{upstream.port} HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "User-Agent: pip\r\n"
                "Proxy-Connection: keep-alive\r\n"
                "\r\n"
            ).encode("latin-1") + tls_bytes
            with socket.create_connection(("127.0.0.1", p._port), timeout=5.0) as s:
                s.sendall(payload)
                s.settimeout(5.0)
                buf = b""
                while b"\r\n\r\n" not in buf:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            assert b"200 Connection Established" in buf
            deadline = time.monotonic() + 5.0
            while (time.monotonic() < deadline
                   and len(upstream.received) < len(tls_bytes)):
                time.sleep(0.05)
            assert bytes(upstream.received) == tls_bytes
        finally:
            p.stop()
    finally:
        upstream.close()


class _HostRecordingHandler(http.server.BaseHTTPRequestHandler):
    received_hosts: list[str] = []

    def do_GET(self):
        _HostRecordingHandler.received_hosts.append(self.headers.get("Host"))
        body = b"host-header-recorded"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_dial_rewrite_forward_preserves_host_header():
    """D8: a configured dial rewrite applies only at the upstream dial site -
    the policy input and the Host header keep the original hostname."""
    import http.server

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                             _HostRecordingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        policy = EgressPolicy(
            targets=(TargetEntry(host="host.docker.internal"),))
        p = EgressProxy(
            policy=policy,
            dial_rewrites={"host.docker.internal": "127.0.0.1"})
        p.start()
        try:
            req = (f"GET http://host.docker.internal:{port}/ HTTP/1.1\r\n"
                   "Host: host.docker.internal\r\n\r\n").encode()
            resp = _send_raw(p._port, req)
            assert b"200" in resp and b"host-header-recorded" in resp
            assert _HostRecordingHandler.received_hosts == [
                "host.docker.internal"]
            assert any(e.hostname == "host.docker.internal" and e.allowed
                       for e in p.events)
        finally:
            p.stop()
    finally:
        server.shutdown()


def test_dial_rewrite_connect_reaches_local_upstream():
    """D8: a CONNECT for the host alias with an explicit rewrite to
    127.0.0.1 reaches a local upstream; the enforcement event is recorded."""
    upstream = _RawTcpServer()
    try:
        policy = EgressPolicy(
            targets=(TargetEntry(host="host.docker.internal"),))
        p = EgressProxy(
            policy=policy,
            dial_rewrites={"host.docker.internal": "127.0.0.1"})
        p.start()
        try:
            tunnel = b"tunnel-bytes-after-200"
            payload = (
                f"CONNECT host.docker.internal:{upstream.port} HTTP/1.1\r\n"
                "\r\n"
            ).encode() + tunnel
            with socket.create_connection(("127.0.0.1", p._port),
                                          timeout=5.0) as s:
                s.sendall(payload)
                s.settimeout(5.0)
                buf = b""
                while b"\r\n\r\n" not in buf:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            assert b"200 Connection Established" in buf
            deadline = time.monotonic() + 5.0
            while (time.monotonic() < deadline
                   and len(upstream.received) < len(tunnel)):
                time.sleep(0.05)
            assert bytes(upstream.received) == tunnel
            assert any(e.hostname == "host.docker.internal" and e.allowed
                       for e in p.events)
        finally:
            p.stop()
    finally:
        upstream.close()


def test_event_file_writer(tmp_path):
    """The event-file writer used by the container-mode entrypoint appends one
    JSON enforcement record per connection decision."""
    import json

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Target)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        events = tmp_path / "events.jsonl"
        policy = EgressPolicy(targets=(TargetEntry(host="127.0.0.1"),))
        p = EgressProxy(policy=policy, event_file=events)
        p.start()
        try:
            req = (f"GET http://127.0.0.1:{port}/ HTTP/1.1\r\n"
                   "Host: 127.0.0.1\r\n\r\n").encode()
            _send_raw(p._port, req)
        finally:
            p.stop()
        lines = events.read_text().splitlines()
        assert lines
        rec = json.loads(lines[0])
        assert rec["hostname"] == "127.0.0.1"
        assert rec["allowed"] is True
    finally:
        server.shutdown()


def test_container_entrypoint_writes_events_file(tmp_path):
    """The standalone container-mode entrypoint serves the policy and writes
    enforcement events to the mounted file (exercised out-of-package)."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "agentic_dev_loop" / "engine"
    events = tmp_path / "events.jsonl"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    policy_json = json.dumps({"class": "allowlist", "targets": []})
    proc = subprocess.Popen(
        [sys.executable, str(src / "egress_proxy.py"),
         "--policy", policy_json, "--events", str(events),
         "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5.0
        connected = False
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    connected = True
                    break
            except OSError:
                time.sleep(0.05)
        assert connected, "container-mode entrypoint did not bind"
        resp = _send_raw(
            port,
            b"GET http://denied.example/ HTTP/1.1\r\n"
            b"Host: denied.example\r\n\r\n")
        assert b"403" in resp
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    lines = events.read_text().splitlines()
    assert lines
    rec = json.loads(lines[0])
    assert rec["hostname"] == "denied.example"
    assert rec["allowed"] is False

def test_container_entrypoint_relays_allowed_dial(tmp_path):
    """039 D6: the container-mode entrypoint binds, relays an allowed proxied
    dial end-to-end to a real local upstream, and emits an allowed enforcement
    event line."""
    import http.server
    import json
    import subprocess
    import sys
    from pathlib import Path

    class _Ok(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"relayed"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    target = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=target.serve_forever, daemon=True).start()
    try:
        target_port = target.server_address[1]
        src = Path(__file__).resolve().parents[1] / "src" / "agentic_dev_loop" / "engine"
        events = tmp_path / "events.jsonl"
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        policy_json = json.dumps({
            "class": "allowlist",
            "targets": [{"host": "127.0.0.1"}],
        })
        proc = subprocess.Popen(
            [sys.executable, str(src / "egress_proxy.py"),
             "--policy", policy_json, "--events", str(events),
             "--host", "127.0.0.1", "--port", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5.0
            connected = False
            while time.monotonic() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        connected = True
                        break
                except OSError:
                    time.sleep(0.05)
            assert connected, "container-mode entrypoint did not bind"
            req = (f"GET http://127.0.0.1:{target_port}/ HTTP/1.1\r\n"
                   "Host: 127.0.0.1\r\n\r\n").encode()
            resp = _send_raw(port, req)
            assert b"200" in resp and b"relayed" in resp
        finally:
            proc.terminate()
            proc.wait(timeout=5)
        recs = [json.loads(line) for line in events.read_text().splitlines()]
        assert any(r["hostname"] == "127.0.0.1" and r["allowed"] is True
                   for r in recs)
    finally:
        target.shutdown()
