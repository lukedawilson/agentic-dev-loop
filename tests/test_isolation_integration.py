"""Marked real-Docker integration tests (BR-12.3). Skip with an explicit
reason when no daemon or integration image is present; never silently skipped
in CI (the workflows pre-install Docker and the image)."""
from __future__ import annotations

import os
import shutil

import pytest

from agentic_dev_loop.core.config import IsolationConfig, MountSpec
from agentic_dev_loop.engine.egress_policy import EgressPolicy
from agentic_dev_loop.engine.isolation import (
    DockerBackend, SandboxExecutionRequest, SandboxError,
    SubprocessRunner, probe_docker, read_stamp, resolve_effective_profile,
    write_stamp,
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
    """014: `network: true` steps run in a second, bridge-network container.
    The distinguishing observable is the interface set - network-none has only
    loopback; the bridge container additionally gets a routable eth0. This is
    deterministic (no external egress required)."""

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


class TestProvisionE2E:
    def test_provision_stamp_skip_refresh(self, backend, tmp_path):
        b, ws = backend
        stamp_dir = tmp_path / "stamps"
        b.provision("printf provisioned > stamp-probe", "k1", str(stamp_dir),
                    force=False)
        assert (ws / "stamp-probe").read_text() == "provisioned"
        assert read_stamp(stamp_dir, "k1")
        # stamp hit: a second provision does not re-run (file would change)
        b.provision("printf other > stamp-probe", "k1", str(stamp_dir),
                    force=False)
        assert (ws / "stamp-probe").read_text() == "provisioned"
        # force re-runs
        b.provision("printf refreshed > stamp-probe", "k1", str(stamp_dir),
                    force=True)
        assert (ws / "stamp-probe").read_text() == "refreshed"


class TestVenvSelfHealE2E:
    """016 exact repro: a warm stamp plus a host-built (darwin) venv that is
    broken inside the Linux container must self-heal - remove + re-provision -
    instead of blind-skipping into a later ENOENT."""

    def test_stamp_hit_with_host_venv_reprovisions(self, backend, tmp_path):
        b, ws = backend
        # Simulate a host-rebuilt venv: `bin/python` is a symlink to a host
        # interpreter path that does not exist inside the Linux container.
        venv_bin = ws / ".venv-sandbox" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").symlink_to(
            "/opt/homebrew/opt/python@3.14/bin/python3.14")
        (venv_bin / "pip").symlink_to(
            "/opt/homebrew/opt/python@3.14/bin/pip3.14")

        stamp_dir = tmp_path / "stamps"
        key = "warm-stamp"
        write_stamp(stamp_dir, key)

        b.provision("python -m venv .venv-sandbox", key, str(stamp_dir),
                    force=False)

        # The broken venv was removed and re-provisioned: the sandbox python
        # is now executable and importable, and the warm stamp still stands.
        req = SandboxExecutionRequest(
            argv=[".venv-sandbox/bin/python", "-c", "import sys; print('ok')"],
            cwd=ws,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"})
        result = b.run(req)
        assert result.exit_code == 0, result.stderr
        assert "ok" in result.stdout
        assert read_stamp(stamp_dir, key)


class TestRelocatedVenvE2E:
    """022: `sandbox_venv_dir` relocates the sandbox venv outside the workspace.
    The outside dir is bind-mounted at `{ws}/.venv-sandbox` in BOTH containers,
    so the venv materializes there and stays absent from the repo dir - and the
    probe/remove_sandbox_venv self-heal keeps operating at the unchanged
    container path (the mount)."""

    def _relocated_backend(self, tmp_path):
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
        venv_dir = tmp_path / "outside-venv"
        profile = resolve_effective_profile(IsolationConfig(image=TEST_IMAGE))
        b = DockerBackend(image=TEST_IMAGE, workspace=str(ws),
                          iso_state_dir=str(tmp_path / "state"), profile=profile,
                          sandbox_venv_dir=str(venv_dir))
        b.ensure_image()
        b.create_execution_container()
        return b, ws, venv_dir

    def test_relocated_venv_materializes_outside_workspace(self, tmp_path):
        b, ws, venv_dir = self._relocated_backend(tmp_path)
        try:
            b.provision("python -m venv .venv-sandbox", "k1",
                        str(tmp_path / "stamps"), force=False)
            # 022: the venv materialized OUTSIDE the workspace. bin/python is
            # a symlink to the CONTAINER's interpreter, which dangles on the
            # host - existence must be checked without following links.
            assert os.path.lexists(venv_dir / "bin" / "python")
            assert (venv_dir / "pyvenv.cfg").exists()
            # No venv content leaked into the workspace. Docker auto-creates
            # the empty mountpoint dir, so its mere existence is not a leak.
            assert not (ws / ".venv-sandbox" / "pyvenv.cfg").exists()
            req = SandboxExecutionRequest(
                argv=[".venv-sandbox/bin/python", "-c", "import sys; print('ok')"],
                cwd=ws,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"})
            result = b.run(req)
            assert result.exit_code == 0, result.stderr
            assert "ok" in result.stdout
        finally:
            b.close()

    def test_relocated_self_heal_reprovisions_broken_venv(self, tmp_path):
        b, ws, venv_dir = self._relocated_backend(tmp_path)
        try:
            # Simulate a host-built venv broken inside the Linux container.
            (venv_dir / "bin").mkdir(parents=True)
            (venv_dir / "bin" / "python").symlink_to(
                "/opt/homebrew/opt/python@3.14/bin/python3.14")
            stamp_dir = tmp_path / "stamps"
            key = "warm-stamp"
            write_stamp(stamp_dir, key)

            b.provision("python -m venv .venv-sandbox", key, str(stamp_dir),
                        force=False)

            # contents cleared (mount source dir kept) and re-provisioned.
            assert venv_dir.is_dir()
            req = SandboxExecutionRequest(
                argv=[".venv-sandbox/bin/python", "-c", "import sys; print('ok')"],
                cwd=ws,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"})
            result = b.run(req)
            assert result.exit_code == 0, result.stderr
            assert "ok" in result.stdout
            assert read_stamp(stamp_dir, key)
        finally:
            b.close()


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
