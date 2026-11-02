"""Marked real-Docker integration tests (BR-12.3). Skip with an explicit
reason when no daemon or integration image is present; never silently skipped
in CI (the workflows pre-install Docker and the image)."""
from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading

import pytest

from agentic_dev_loop.core.config import IsolationConfig, MountSpec
from agentic_dev_loop.engine.egress_policy import EgressPolicy, TargetEntry
from agentic_dev_loop.engine.isolation import (
    CONTAINER_REGISTRY, DockerBackend, SandboxExecutionRequest, SandboxError,
    SubprocessRunner, probe_docker, process_start,
    reap_orphaned_containers, resolve_effective_profile,
)
from agentic_dev_loop.engine.mounts import resolve_mount_set
from agentic_dev_loop.engine.step_workspace import StepWorkspace

pytestmark = pytest.mark.docker

TEST_IMAGE = "python:3.12"


def _docker_available() -> str | None:
    if shutil.which("docker") is None:
        return "docker CLI not on PATH"
    try:
        probe_docker()
    except SandboxError as e:
        return f"docker daemon unavailable: {e}"
    return None


@pytest.fixture
def backend(tmp_path):
    reason = _docker_available()
    if reason is not None:
        pytest.skip(reason)
    inspect = SubprocessRunner().run(
        ["docker", "image", "inspect", TEST_IMAGE, "--format", "{{.Id}}"],
        timeout=30)
    if inspect.exit_code != 0:
        pytest.skip(f"integration image {TEST_IMAGE!r} not present locally")
    ws = tmp_path / "ws"
    ws.mkdir()
    profile = resolve_effective_profile(IsolationConfig(image=TEST_IMAGE))
    b = DockerBackend(image=TEST_IMAGE, workspace=str(ws),
                      iso_state_dir=str(tmp_path / "state"), profile=profile)
    b.ensure_image()
    b.create_execution_container()
    yield b, ws
    b.close()


def _run(backend, argv, **kw):
    ws = backend._workspace
    req = SandboxExecutionRequest(
        argv=argv, cwd=ws,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"},
        timeout=kw.get("timeout"))
    return backend.run(req)


class TestProbe:
    def test_probe_passes(self):
        reason = _docker_available()
        if reason is not None:
            pytest.skip(reason)
        r = probe_docker()
        assert r.ostype == "linux"
        assert tuple(int(x) for x in r.api_version.split(".")) >= (1, 43)


class TestEcho:
    def test_echo_round_trip(self, backend):
        b, _ws = backend
        result = _run(b, ["echo", "hello-sandbox"])
        assert result.exit_code == 0
        assert "hello-sandbox" in result.stdout


class TestNetworkOff:
    def test_no_external_reachability(self, backend):
        b, _ws = backend
        result = _run(b, ["python", "-c",
                          "import socket; s=socket.socket(); s.settimeout(2); "
                          "s.connect(('1.1.1.1', 80))"])
        assert result.exit_code != 0  # --network none: no route


class TestReadOnlyRootfs:
    def test_rootfs_read_only_mount_writable(self, backend):
        b, ws = backend
        # rootfs is read-only: writing to / fails
        ro = _run(b, ["python", "-c", "open('/readonly-probe','w').write('x')"])
        assert ro.exit_code != 0
        # the workspace mount is writable
        rw = _run(b, ["sh", "-c", "printf x > writable-probe"])
        assert rw.exit_code == 0
        assert (ws / "writable-probe").read_text() == "x"


class TestGitGuardE2E:
    def test_mutation_blocked_before_exec(self, backend):
        b, _ws = backend
        with pytest.raises(SandboxError, match="AE-SBX-008"):
            _run(b, ["git", "commit", "-m", "x"])

    def test_read_runs(self, backend):
        b, _ws = backend
        result = _run(b, ["git", "status"])
        assert not isinstance(result, SandboxError)


class TestNetworkedContainerE2E:
    """014: `network: true` steps run in a container on the per-run internal
    network. The distinguishing observable is the interface set - network-none
    has only loopback; the networked container additionally gets a routable
    eth0. This is deterministic (no external egress required)."""

    @pytest.fixture
    def networked_backend(self, tmp_path):
        reason = _docker_available()
        if reason is not None:
            pytest.skip(reason)
        inspect = SubprocessRunner().run(
            ["docker", "image", "inspect", TEST_IMAGE, "--format", "{{.Id}}"],
            timeout=30)
        if inspect.exit_code != 0:
            pytest.skip(f"integration image {TEST_IMAGE!r} not present locally")
        ws = tmp_path / "ws"
        ws.mkdir()
        profile = resolve_effective_profile(IsolationConfig(image=TEST_IMAGE))
        b = DockerBackend(image=TEST_IMAGE, workspace=str(ws),
                          iso_state_dir=str(tmp_path / "state"), profile=profile,
                          network=True)
        b.ensure_image()
        b.create_execution_container()
        yield b, ws
        b.close()

    def test_networked_container_has_bridge_interface(self, networked_backend):
        b, ws = networked_backend
        req = SandboxExecutionRequest(
            argv=["python", "-c",
                  "import socket; print(' '.join(sorted(n for _, n in socket.if_nameindex())))"],
            cwd=ws,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"},
            network=True)
        result = b.run(req)
        assert result.exit_code == 0, result.stderr
        names = result.stdout.split()
        assert "lo" in names
        assert any(name != "lo" for name in names)

    def test_default_container_still_network_none(self, networked_backend):
        b, ws = networked_backend
        req = SandboxExecutionRequest(
            argv=["python", "-c",
                  "print(len(open('/proc/net/route').read().strip().splitlines()[1:]))"],
            cwd=ws,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"},
            network=False)
        result = b.run(req)
        assert result.exit_code == 0, result.stderr
        assert result.stdout.strip() == "0"  # no routes at all - no egress


class TestExportAndMountsE2E:
    """unit-5: the profile-keyed pool over a real export. A step mounts only
    its declared `mounts:` (ro paths reject writes; rw paths land and sync
    back to the real repo)."""

    def _export_backend(self, tmp_path):
        reason = _docker_available()
        if reason is not None:
            pytest.skip(reason)
        inspect = SubprocessRunner().run(
            ["docker", "image", "inspect", TEST_IMAGE, "--format", "{{.Id}}"],
            timeout=30)
        if inspect.exit_code != 0:
            pytest.skip(f"integration image {TEST_IMAGE!r} not present locally")
        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "a.py").write_text("orig")
        (ws / "ro").mkdir()
        (ws / "ro" / "f").write_text("read-only")
        # 052: sync-back resolves ignored paths via `git check-ignore` against
        # the workspace, which must be a git repo (production workspaces
        # always are). The synthetic fixture workspace needs the same shape.
        init = SubprocessRunner().run(["git", "init", "-q", str(ws)], timeout=30)
        if init.exit_code != 0:
            pytest.fail(f"git init failed: {init.stderr}")
        export = tmp_path / "state" / "exports" / "r1"
        ws_obj = StepWorkspace.materialise(ws, [], "linux", export)
        profile = resolve_effective_profile(IsolationConfig(image=TEST_IMAGE))
        b = DockerBackend(image=TEST_IMAGE, workspace=str(ws),
                          iso_state_dir=str(tmp_path / "state"), profile=profile,
                          export_root=str(export),
                          cache_mounts=ws_obj.mount_points()[1:])
        b.ensure_image()
        b.create_execution_container()
        return b, ws, export

    def _req(self, export, argv, mount_specs):
        return SandboxExecutionRequest(
            argv=argv, cwd=str(export),
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"},
            mount_set=resolve_mount_set(mount_specs),
            egress_policy=EgressPolicy())

    def test_ro_mount_rejects_write(self, tmp_path):
        b, _ws, export = self._export_backend(tmp_path)
        try:
            req = self._req(export, ["sh", "-c", "printf x > ro/f"],
                            [MountSpec(path="ro", mode="ro")])
            result = b.run(req)
            assert result.exit_code != 0  # read-only mount: EROFS
        finally:
            b.close()

    def test_rw_mount_syncs_back(self, tmp_path):
        b, ws, export = self._export_backend(tmp_path)
        try:
            req = self._req(export, ["sh", "-c", "printf changed > src/a.py"],
                            [MountSpec(path="src", mode="rw")])
            result = b.run(req)
            assert result.exit_code == 0, result.stderr
            assert (ws / "src" / "a.py").read_text() == "changed"
        finally:
            b.close()


class TestReaperE2E:
    """030: the startup reaper removes a leaked exec container whose owning
    pid is dead, and leaves the registry pruned (missing containers are
    tolerated via the rm -f exit-code-ignoring contract)."""

    def test_reaper_removes_orphaned_exec_container(self, backend, tmp_path):
        b, _ws = backend
        name = b._container
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        registry = b._iso_state_dir / CONTAINER_REGISTRY
        registry.parent.mkdir(parents=True, exist_ok=True)
        with registry.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "run_id": b._run_id,
                "name": name,
                "pid": proc.pid,
                "process_start": process_start(proc.pid),
                "created_at": "now",
            }) + "\n")

        reaped = reap_orphaned_containers(b._iso_state_dir, SubprocessRunner())
        assert reaped == [name]

        inspect = SubprocessRunner().run(
            ["docker", "ps", "-a", "--filter", f"name={name}",
             "--format", "{{.Names}}"],
            timeout=30)
        assert name not in inspect.stdout



class TestTwoProxyDistinctIpsE2E:
    """048: two distinct egress policies on one run network each get their
    own proxy container (distinct internal IP) and both relay an allowed
    loopback dial through the host gateway."""

    def _target(self, body):
        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                payload = body.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def _backend_with_export(self, tmp_path):
        reason = _docker_available()
        if reason is not None:
            pytest.skip(reason)
        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "a.py").write_text("orig")
        export = tmp_path / "state" / "exports" / "r1"
        StepWorkspace.materialise(ws, [], "linux", export)
        profile = resolve_effective_profile(IsolationConfig(image=TEST_IMAGE))
        b = DockerBackend(image=TEST_IMAGE, workspace=str(ws),
                          iso_state_dir=str(tmp_path / "state"), profile=profile,
                          export_root=str(export))
        b.ensure_image()
        b.create_execution_container()
        return b, export

    def test_two_proxies_attach_and_relay(self, tmp_path):
        server_a = self._target("hello-from-a")
        server_b = self._target("hello-from-b")
        b, export = self._backend_with_export(tmp_path)
        try:
            cases = [
                (EgressPolicy(targets=(TargetEntry(
                    host="host.docker.internal",
                    port=server_a.server_address[1]),)),
                 server_a.server_address[1], "hello-from-a"),
                (EgressPolicy(targets=(TargetEntry(
                    host="host.docker.internal",
                    port=server_b.server_address[1]),)),
                 server_b.server_address[1], "hello-from-b"),
            ]
            ms = resolve_mount_set([MountSpec(path=".", mode="ro")])
            for policy, port, body in cases:
                code = (
                    "import urllib.request;"
                    f"print(urllib.request.urlopen('http://host.docker.internal:{port}/').read().decode())"
                )
                req = SandboxExecutionRequest(
                    argv=["python", "-c", code],
                    cwd=str(export),
                    env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"},
                    network=True, mount_set=ms, egress_policy=policy)
                result = b.run(req)
                assert result.exit_code == 0, result.stderr
                assert body in result.stdout
            ips = sorted(b._proxy_ips.values())
            assert len(ips) == 2 and ips[0] != ips[1]
        finally:
            b.close()
            server_a.shutdown()
            server_b.shutdown()
