"""Hand-rolled asyncio egress proxy (unit-5, ID-1).

The enforcement point on all platforms (BR-5): a per-policy HTTP(S) forward
proxy, loopback-bound on an ephemeral port, stdlib-only. It matches the
request hostname (CONNECT target) per connection against the expanded policy
(BR-6); an unmatched hostname is denied and logged as an enforcement event.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .egress_policy import EgressPolicy, match_target


@dataclass(frozen=True)
class EnforcementEvent:
    """One connection decision (BR-6.2, SECURITY-02): allowed or denied."""

    hostname: str
    allowed: bool
    timestamp: str = ""


@dataclass
class EgressProxy:
    """A per-policy egress proxy. ``policy`` is the resolved EgressPolicy;
    ``events`` collects enforcement events (host-side, no secrets)."""

    policy: EgressPolicy
    events: list[EnforcementEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._server: asyncio.AbstractServer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._port: int | None = None
        self._writers: set = set()
        self._closing = False

    @property
    def is_alive(self) -> bool:
        """True while the proxy's server thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        """Bind the proxy on ``host:port`` (ephemeral when port=0) and serve
        in a background thread. Returns the bound port."""
        self._loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(self._loop)
            self._server = self._loop.run_until_complete(
                asyncio.start_server(self._handle, host, port))
            self._port = self._server.sockets[0].getsockname()[1]
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        deadline = datetime.now().timestamp() + 5.0
        while self._port is None:
            if datetime.now().timestamp() > deadline:
                raise RuntimeError("egress proxy failed to bind within 5s")
            threading.Event().wait(0.01)
        return self._port

    def stop(self) -> None:
        """Stop the proxy and join its thread. Idempotent.

        ``Server.close`` and the writer teardown are not thread-safe, so they
        are scheduled onto the loop's own thread before it stops."""
        if self._loop is None:
            return
        self._closing = True
        loop = self._loop

        def _shutdown() -> None:
            if self._server is not None:
                self._server.close()
            for writer in list(self._writers):
                try:
                    writer.close()
                except Exception:
                    pass

        loop.call_soon_threadsafe(_shutdown)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass
        self._loop = None
        self._server = None
        self._port = None

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            line = await reader.readline()
            if not line:
                return
            method, target, _version = _parse_request_line(line)
            if method.upper() == "CONNECT":
                await self._handle_connect(reader, writer, target)
            else:
                await self._handle_forward(reader, writer, target, line)
        except Exception:
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    async def _handle_connect(self, reader, writer, target: str) -> None:
        host, port = _split_host_port(target)
        # Pass the full host:port so a port-constrained entry can match the
        # exact connection it allows (BR-6); the bare host is still what we log.
        if not match_target(target, self.policy):
            self._record(host, False)
            _write(writer, b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        self._record(host, True)
        conn = await _open(host, port)
        if conn is None:
            _write(writer, b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        _write(writer, b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        upstream_reader, upstream_writer = conn
        await _relay_both(reader, writer, upstream_reader, upstream_writer)

    async def _handle_forward(self, reader, writer, target: str,
                              first_line: bytes) -> None:
        url = urlsplit(target)
        host = url.hostname or ""
        port = url.port or (443 if url.scheme == "https" else 80)
        # Match the host:port (not the bare host) so port-constrained entries
        # apply to absolute-URI requests too (BR-6).
        if not match_target(f"{host}:{port}", self.policy):
            self._record(host, False)
            _write(writer, b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return
        self._record(host, True)
        conn = await _open(host, port)
        if conn is None:
            _write(writer, b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        upstream_reader, upstream_writer = conn
        path = url.path or "/"
        if url.query:
            path += "?" + url.query
        method = first_line.decode("latin-1").split(" ", 1)[0]
        upstream_writer.write(
            f"{method} {path} HTTP/1.1\r\n".encode("latin-1"))
        await _forward_headers(reader, upstream_writer, host)
        await _relay_both(reader, writer, upstream_reader, upstream_writer)

    def _record(self, hostname: str, allowed: bool) -> None:
        self.events.append(EnforcementEvent(
            hostname=hostname, allowed=allowed,
            timestamp=datetime.now(timezone.utc).isoformat()))


def _parse_request_line(line: bytes) -> tuple[str, str, str]:
    parts = line.decode("latin-1").strip().split()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    return parts[0], parts[1], parts[2] if len(parts) > 2 else ""


def _split_host_port(target: str) -> tuple[str, int]:
    value = target.strip()
    if value.startswith("["):
        end = value.find("]")
        host = value[1:end]
        rest = value[end + 1:]
        port = int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else 443
        return host, port
    if value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        if port.isdigit():
            return host, int(port)
    return value, 443


def _write(writer: asyncio.StreamWriter, data: bytes) -> None:
    writer.write(data)


async def _open(host: str, port: int):
    try:
        return await asyncio.open_connection(host, port)
    except OSError:
        return None


async def _forward_headers(reader, upstream, host: str) -> None:
    saw_host = False
    while True:
        line = await reader.readline()
        if not line or line in (b"\r\n", b"\n"):
            break
        if line.lower().startswith(b"host:"):
            saw_host = True
        upstream.write(line)
    if not saw_host:
        upstream.write(f"Host: {host}\r\n".encode("latin-1"))
    upstream.write(b"\r\n")
    await upstream.drain()


async def _relay_both(client_reader, client_writer, upstream_reader,
                      upstream_writer) -> None:
    await asyncio.gather(
        _relay(client_reader, upstream_writer),
        _relay(upstream_reader, client_writer),
    )


async def _relay(src, dst) -> None:
    try:
        while True:
            data = await src.read(65536)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except Exception:
        pass
    finally:
        try:
            dst.write_eof()
        except Exception:
            pass
