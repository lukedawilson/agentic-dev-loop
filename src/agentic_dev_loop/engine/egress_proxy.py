"""Hand-rolled asyncio egress proxy (unit-5, ID-1).

The enforcement point on all platforms (BR-5): a per-policy HTTP(S) forward
proxy, loopback-bound on an ephemeral port, stdlib-only. It matches the
request hostname (CONNECT target) per connection against the expanded policy
(BR-6); an unmatched hostname is denied and logged as an enforcement event.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from .egress_policy import EgressPolicy, TargetEntry, match_target
else:  # standalone container-mode entrypoint (run as a script)
    from egress_policy import EgressPolicy, TargetEntry, match_target


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
    dial_rewrites: dict[str, str] = field(default_factory=dict)
    event_file: Path | None = None

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

    def start(self, host: str = "127.0.0.1", port: int = 0,
              bind_timeout: float = 5.0) -> int:
        """Bind the proxy on ``host:port`` (ephemeral when port=0) and serve
        in a background thread. Returns the bound port.

        ``bind_timeout`` bounds how long the bind may wait for the requested
        address to appear (the container-mode entrypoint binds an
        internal-network IP that is attached after the container starts)."""
        self._loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(self._loop)
            deadline = time.monotonic() + bind_timeout
            while True:
                try:
                    self._server = self._loop.run_until_complete(
                        asyncio.start_server(self._handle, host, port))
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            self._port = self._server.sockets[0].getsockname()[1]
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        deadline = datetime.now().timestamp() + bind_timeout + 1.0
        while self._port is None:
            if not self._thread.is_alive():
                raise RuntimeError("egress proxy failed to bind")
            if datetime.now().timestamp() > deadline:
                raise RuntimeError(
                    f"egress proxy failed to bind within {bind_timeout}s")
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
        # Drain the rest of the CONNECT request (the header block after the
        # request line). Clients send the whole request in one write, so the
        # headers are already buffered here; relaying them to the upstream as
        # if they were the first tunnel bytes corrupts the TLS handshake (the
        # upstream answers with a decode alert - the restore step's SSL
        # TLSV1_ALERT_DECODE_ERROR).
        await _drain_headers(reader)
        conn = await self._open(host, port)
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
        conn = await self._open(host, port)
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
        event = EnforcementEvent(
            hostname=hostname, allowed=allowed,
            timestamp=datetime.now(timezone.utc).isoformat())
        self.events.append(event)
        if self.event_file is not None:
            _append_event_file(self.event_file, event)

    async def _open(self, host: str, port: int):
        """Dial the upstream, applying the configured dial rewrite (D8).
        ``match_target`` already ran against the original hostname, and the
        request/Host header keep that hostname; only the upstream dial address
        is rewritten."""
        dial_host = self.dial_rewrites.get(host, host)
        try:
            return await asyncio.open_connection(dial_host, port)
        except OSError:
            return None


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


async def _drain_headers(reader) -> None:
    """Consume a request's header block (the lines after the request line,
    terminated by a blank line). The CONNECT path must not forward these to
    the upstream tunnel - only the post-200 bytes belong there."""
    while True:
        line = await reader.readline()
        if not line or line in (b"\r\n", b"\n"):
            break


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



def _append_event_file(path: Path, event: EnforcementEvent) -> None:
    """Append one enforcement event to the mounted JSONL file (best-effort:
    a broken events file must never break enforcement)."""
    record = {
        "hostname": event.hostname,
        "allowed": event.allowed,
        "timestamp": event.timestamp,
    }
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def policy_to_json(policy: EgressPolicy) -> str:
    """Serialize a resolved policy for the container-mode entrypoint."""
    return json.dumps({
        "class": policy.policy_class,
        "targets": [{"host": t.host, "port": t.port} for t in policy.targets],
    }, sort_keys=True)


def policy_from_json(text: str) -> EgressPolicy:
    """Rebuild a resolved policy from the entrypoint's ``--policy`` JSON."""
    data = json.loads(text)
    return EgressPolicy(
        targets=tuple(
            TargetEntry(host=t["host"], port=t.get("port"))
            for t in data.get("targets", [])),
        policy_class=data.get("class", "allowlist"),
    )


def main(argv: list[str] | None = None) -> int:
    """Container-mode entrypoint: serve the policy on the internal network
    and append enforcement events to a mounted file until SIGTERM."""
    parser = argparse.ArgumentParser(description="agentic-dev-loop egress proxy")
    parser.add_argument("--policy", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--dial-rewrites", default="{}")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--bind-timeout", type=float, default=5.0)
    args = parser.parse_args(argv)

    proxy = EgressProxy(
        policy=policy_from_json(args.policy),
        dial_rewrites=json.loads(args.dial_rewrites),
        event_file=Path(args.events),
    )
    proxy.start(host=args.host, port=args.port, bind_timeout=args.bind_timeout)
    threading.Event().wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
