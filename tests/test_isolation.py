"""Unit tests for the sandbox domain (steps 2-6): value objects, pure
functions, the Docker backend with an injected command runner (the seam - no
monkeypatching), and the host-side audit/stamp helpers against real temp dirs.

No Docker daemon is required: every backend test drives the injected
`CommandRunner`; the real daemon is exercised only by the marked
test_isolation_integration.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentic_dev_loop.core.config import IsolationConfig
from agentic_dev_loop.core.config import MountSpec
from agentic_dev_loop.engine.egress_policy import EgressPolicy, TargetEntry
from agentic_dev_loop.engine.mounts import MountSet, resolve_mount_set
from agentic_dev_loop.engine.isolation import (
    AUDIT_MAX_BYTES, CONTAINER_REGISTRY, CmdResult, DEFAULT_DEV_IMAGE,
    PROXY_CPUS, PROXY_MEMORY, PROXY_READY_ATTEMPTS, PROXY_USER,
    PROXY_BIND_TIMEOUT,
    DEFAULT_DEV_IMAGE_REF, DockerBackend, EffectiveProfile, ProbeResult,
    SandboxError, SandboxExecutionRequest, SandboxExecutionResult,
    append_audit_record, classify_infra_failure,
    container_profile_key, docker_create_payload, docker_provision_payload,
    docker_proxy_payload, egress_events_path, exec_container_name,
    is_git_mutation, merge_egress_events, mount_set_docker_flags,
    proxy_container_name, proxy_url, run_network_name,
    probe_docker, process_start, reap_orphaned_containers,
    resolve_effective_profile, resolve_ignored_paths,
    resolve_image_ref,
    resolve_sandbox_env,
    translate_mount_path,
    )


class FakeRunner:
    """Scripted CommandRunner - the unit-test seam for the docker CLI."""

    def __init__(self, *script):
        self.calls: list[list[str]] = []
        self.script = list(script)

    def run(self, argv, *, timeout=None, check=False, input=None):
        self.calls.append(argv)
        if not self.script:
            return CmdResult(exit_code=0, stdout="", stderr="")
        item = self.script[0]
        if len(self.script) > 1:
            self.script.pop(0)
        return item(argv) if callable(item) else item


def ok(stdout=""):
    return CmdResult(exit_code=0, stdout=stdout, stderr="")


def fail(code, stderr=""):
    return CmdResult(exit_code=code, stdout="", stderr=stderr)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class TestRequest:
    def test_empty_argv_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            SandboxExecutionRequest(argv=[], cwd="/ws", env={})

    def test_relative_cwd_rejected(self):
        with pytest.raises(ValueError, match="absolute"):
            SandboxExecutionRequest(argv=["true"], cwd="ws", env={})

    def test_posix_absolute_cwd_accepted_on_any_host(self):
        """The cwd is a CONTAINER-side path (the container is always Linux),
        so POSIX-absolute values like '/ws' must validate on every host OS -
        Windows included (the CI-red ValueError on test_valid_request)."""
        r = SandboxExecutionRequest(argv=["echo", "hi"], cwd="/ws",
                                    env={}, timeout=1.0)
        assert r.cwd == "/ws"

    def test_nonpositive_timeout_rejected(self):
        with pytest.raises(ValueError, match="> 0"):
            SandboxExecutionRequest(argv=["true"], cwd="/ws", env={}, timeout=0)


class TestResult:
    def test_succeeded_and_timeout(self):
        assert SandboxExecutionResult(0, "", "", 1.0, "docker").succeeded
        assert not SandboxExecutionResult(1, "", "", 1.0, "docker").succeeded
        r = SandboxExecutionResult(None, "", "", 1.0, "docker", timed_out=True)
        assert r.is_timeout and not r.succeeded


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


class TestEffectiveProfile:
    def test_defaults(self):
        p = resolve_effective_profile(None)
        assert p.network_off and p.read_only_rootfs and p.writes_only_on_mounts
        assert p.drop_all_capabilities and p.git_mutations_blocked
        assert p.image == DEFAULT_DEV_IMAGE_REF

    def test_default_ref_is_digest_pinned(self):
        """KD-2: the shipped default must name the published dev image by
        digest - AE-SBX-005 fails fast while the constant is empty, and an
        unpinned tag would silently drift to `latest`."""
        assert DEFAULT_DEV_IMAGE_REF.startswith(
            DEFAULT_DEV_IMAGE + "@sha256:"
        ), "DEFAULT_DEV_IMAGE_REF must hold the published dev-image digest"

    def test_overrides(self):
        iso = IsolationConfig(image="img@sha256:abc", memory="8g", cpus=4.0,
                              allow_git_mutations=True)
        p = resolve_effective_profile(iso)
        assert p.image == "img@sha256:abc"
        assert p.memory == "8g" and p.cpus == 4.0
        assert not p.git_mutations_blocked

    def test_git_blocked_by_default(self):
        assert resolve_effective_profile(IsolationConfig()).git_mutations_blocked


class TestResolveSandboxEnv:
    def test_base_only(self):
        env = resolve_sandbox_env(None, host_env={})
        assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"
        assert env["HOME"] == "/tmp"
        assert "OPENAI_API_KEY" not in env

    def test_declared_null_inherits(self):
        env = resolve_sandbox_env({"MY_VAR": None}, host_env={"MY_VAR": "hostval"})
        assert env["MY_VAR"] == "hostval"

    def test_declared_value_used(self):
        env = resolve_sandbox_env({"MY_VAR": "declared"}, host_env={"MY_VAR": "hostval"})
        assert env["MY_VAR"] == "declared"

    def test_host_env_not_leaked(self):
        env = resolve_sandbox_env(None, host_env={"SECRET": "s3cr3t", "PATH": "/x"})
        assert "SECRET" not in env


class TestIsGitMutation:
    @pytest.mark.parametrize("argv", [
        ["git", "status"],
        ["git", "log", "--oneline"],
        ["git", "diff"],
        ["git", "show", "HEAD"],
        ["git", "ls-files"],
        ["git", "rev-parse", "HEAD"],
        ["git", "blame", "file.py"],
        ["git", "describe"],
        ["git", "for-each-ref"],
        ["git", "branch"],
        ["git", "tag"],
        ["git", "remote", "-v"],
        ["git", "remote", "show", "origin"],
        ["git", "--help"],
        ["git", "help"],
        ["git", "-C", "/repo", "status"],
        ["git", "-c", "color.ui=false", "log"],
        ["git", "--no-pager", "diff"],
    ])
    def test_read_operations_allowed(self, argv):
        assert not is_git_mutation(argv)

    @pytest.mark.parametrize("argv", [
        ["git", "commit", "-m", "x"],
        ["git", "push"],
        ["git", "reset", "--hard"],
        ["git", "branch", "newbranch"],
        ["git", "branch", "-d", "old"],
        ["git", "tag", "-d", "v1"],
        ["git", "stash"],
        ["git", "merge", "main"],
        ["git", "rebase"],
        ["git", "cherry-pick", "abc"],
        ["git", "revert", "HEAD"],
        ["git", "worktree", "add"],
        ["git", "clean", "-fd"],
        ["git", "gc"],
        ["git", "fetch"],
        ["git", "pull"],
        ["git", "am"],
        ["git", "apply"],
        ["git", "checkout", "main"],
        ["git", "switch", "main"],
        ["git", "rm", "f"],
        ["git", "mv", "a", "b"],
        ["git", "init"],
        ["git", "config", "--local", "x", "y"],
        ["git", "remote", "add", "origin", "url"],
        ["git", "unknown-subcommand"],
    ])
    def test_mutations_blocked(self, argv):
        assert is_git_mutation(argv)

    @pytest.mark.parametrize("argv", [
        ["ls", "-la"],
        ["python", "-m", "pytest"],
        ["git-status", "x"],
        [],
    ])
    def test_non_git_not_classified(self, argv):
        assert not is_git_mutation(argv)

    def test_deny_by_default_unknown_subcommand(self):
        assert is_git_mutation(["git", "frobnicate"])


class TestTranslateMountPath:
    def test_identity_and_idempotence(self, tmp_path):
        p = str(tmp_path / "ws")
        assert translate_mount_path(p) == p

    def test_expands_user(self):
        out = translate_mount_path("~/ws")
        assert not out.startswith("~")

    def test_relative_absolute_resolution(self):
        assert Path(translate_mount_path("ws")).is_absolute()
class TestPayloads:
    def _profile(self):
        return EffectiveProfile(True, True, True, True, True, "img", "4g", 2.0)

    def test_create_payload_invariants(self, tmp_path):
        ws = str(tmp_path)
        p = docker_create_payload(run_id="r1", image="img", profile=self._profile(),
                                  workspace=ws)
        assert p[0] == "docker" and "run" in p
        assert "--detach" in p and "--rm" in p
        assert "create" not in p
        assert "--network" in p and "none" in p
        assert "--read-only" in p
        assert "--cap-drop" in p and "ALL" in p
        mounts = [p[i + 1] for i, a in enumerate(p) if a == "-v"]
        assert mounts == [f"{ws}:{ws}"]  # single mount, exactly {workspace}
        assert p[-2:] == ["sleep", "infinity"]

    def test_provision_payload_invariants(self, tmp_path):
        ws = str(tmp_path)
        p = docker_provision_payload(run_id="r1", image="img", export_root=ws,
                                     setup_cmd="make")
        assert "--network" in p and "bridge" in p
        assert "--read-only" not in p  # rw rootfs
        assert "--cap-drop" in p and "ALL" in p
        assert "sh" in p and "-c" in p and "make" in p
        mounts = [p[i + 1] for i, a in enumerate(p) if a == "-v"]
        assert mounts == [f"{ws}:{ws}"]

    @pytest.mark.skipif(not hasattr(os, "getuid"),
                        reason="POSIX-only: Windows hosts have no uid:gid to bind")
    def test_create_payload_binds_host_user(self, tmp_path):
        """CI-red root cause: container root with all caps dropped cannot
        write a host-owned bind mount on native Linux (Docker Desktop's
        virtiofs masked it on mac). The execution container must run as the
        host user, with HOME somewhere a non-root user can write."""
        ws = str(tmp_path)
        p = docker_create_payload(run_id="r1", image="img", profile=self._profile(),
                                  workspace=ws)
        assert "--user" in p
        assert p[p.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
        assert "--env" in p
        assert p[p.index("--env") + 1] == "HOME=/tmp"

    @pytest.mark.skipif(not hasattr(os, "getuid"),
                        reason="POSIX-only: Windows hosts have no uid:gid to bind")
    def test_provision_payload_binds_host_user(self, tmp_path):
        ws = str(tmp_path)
        p = docker_provision_payload(run_id="r1", image="img", export_root=ws,
                                     setup_cmd="make")
        assert "--user" in p
        assert p[p.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
        assert "--env" in p
        assert p[p.index("--env") + 1] == "HOME=/tmp"

    def test_payloads_omit_user_flag_without_posix_ids(self, tmp_path, monkeypatch):
        """Windows hosts have no os.getuid/getgid - the --user flag must be
        omitted rather than fabricating an identity."""
        monkeypatch.delattr(os, "getuid", raising=False)
        monkeypatch.delattr(os, "getgid", raising=False)
        ws = str(tmp_path)
        create = docker_create_payload(run_id="r1", image="img",
                                       profile=self._profile(), workspace=ws)
        provision = docker_provision_payload(run_id="r1", image="img",
                                             export_root=ws, setup_cmd="make")
        assert "--user" not in create
        assert "--user" not in provision

    def test_networked_create_payload(self, tmp_path):
        """038: `network: true` attaches to the per-run internal network and
        never adds a host-gateway mapping; everything else stays identical
        (read-only, cap-dropped, single mount)."""
        ws = str(tmp_path)
        p = docker_create_payload(run_id="r1", image="img",
                                  profile=self._profile(), workspace=ws,
                                  network=True, network_name="r1-net")
        assert "--network" in p and "r1-net" in p
        assert "none" not in p
        assert "--add-host" not in p
        assert "--name" in p and f"{p[p.index('--name') + 1]}" == "r1-exec-net"
        assert "--read-only" in p
        assert "--cap-drop" in p and "ALL" in p
        mounts = [p[i + 1] for i, a in enumerate(p) if a == "-v"]
        assert mounts == [f"{ws}:{ws}"]

class TestExecContainerName:
    def test_default_is_network_none(self):
        assert exec_container_name("abc") == "abc-exec"

    def test_networked_suffix(self):
        assert exec_container_name("abc", network=True) == "abc-exec-net"


# ---------------------------------------------------------------------------
# Probe + image ref
# ---------------------------------------------------------------------------


class TestProbeDocker:
    def test_success(self, monkeypatch):
        runner = FakeRunner(ok("1.54"), ok("linux"))
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        r = probe_docker(runner)
        assert r == ProbeResult("1.54", "linux")

    def test_missing_docker(self, monkeypatch):
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: None)
        with pytest.raises(SandboxError, match="AE-SBX-001"):
            probe_docker(FakeRunner())

    def test_daemon_unreachable(self, monkeypatch):
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        with pytest.raises(SandboxError, match="AE-SBX-002"):
            probe_docker(FakeRunner(fail(1, "Cannot connect")))

    def test_api_too_old(self, monkeypatch):
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        with pytest.raises(SandboxError, match="AE-SBX-003"):
            probe_docker(FakeRunner(ok("1.24"), ok("linux")))

    def test_windows_containers(self, monkeypatch):
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        with pytest.raises(SandboxError, match="AE-SBX-004"):
            probe_docker(FakeRunner(ok("1.54"), ok("windows")))


class TestResolveImageRef:
    def test_env_override_wins(self):
        assert resolve_image_ref("pinned", environ={"AGENTIC_DEV_LOOP_DEV_IMAGE": "tag"}) == "tag"

    def test_profile_image_used(self):
        assert resolve_image_ref("img@sha256:x", environ={}) == "img@sha256:x"

    def test_empty_without_override_fails(self):
        with pytest.raises(SandboxError, match="AE-SBX-005"):
            resolve_image_ref("", environ={})


# ---------------------------------------------------------------------------
# DockerBackend with fake runner
# ---------------------------------------------------------------------------


def _backend(tmp_path, runner, *, image="img@sha256:abc", git_blocked=True,
              network=False, seccomp_profile=None,
              dial_rewrites=None):
    return DockerBackend(
        image=image, workspace=str(tmp_path / "ws"),
        iso_state_dir=str(tmp_path / "state"),
        profile=EffectiveProfile(True, True, True, True, git_blocked,
                                 image, "4g", 2.0),
        runner=runner, network=network,
        seccomp_profile=seccomp_profile,
        dial_rewrites=dial_rewrites)
class TestEnsureImage:
    def test_present_skips_pull(self, tmp_path):
        runner = FakeRunner(ok("sha256:abc"))
        backend = _backend(tmp_path, runner)
        backend.ensure_image()
        assert len(runner.calls) == 1
        assert "inspect" in runner.calls[0]

    def test_pull_retries_on_transient_then_verifies(self, tmp_path):
        runner = FakeRunner(
            fail(1, "id-not-found"),
            fail(1, "Cannot connect to the Docker daemon"),
            fail(1, "connection refused"),
            ok(""),
            ok("img@sha256:abc repo@sha256:other"),
        )
        backend = _backend(tmp_path, runner)
        backend.ensure_image()
        pulls = [c for c in runner.calls if "pull" in c]
        assert len(pulls) == 3  # bounded retry succeeded on the 3rd attempt

    def test_pull_auth_error_is_not_retried(self, tmp_path):
        runner = FakeRunner(fail(1, "id-not-found"), fail(1, "denied: unauthorized"))
        backend = _backend(tmp_path, runner)
        with pytest.raises(SandboxError, match="AE-SBX-006"):
            backend.ensure_image()
        assert len([c for c in runner.calls if "pull" in c]) == 1

    def test_digest_mismatch_fails(self, tmp_path):
        runner = FakeRunner(
            fail(1, "id-not-found"), ok(""), ok("img@sha256:other"),
        )
        backend = _backend(tmp_path, runner)
        with pytest.raises(SandboxError, match="AE-SBX-007"):
            backend.ensure_image()


class TestCreateExecutionContainer:
    def test_eager_create_and_start(self, tmp_path):
        """``docker run -d --rm`` collapses create + start into one call (D1):
        exactly one runner invocation, no separate ``docker start``."""
        runner = FakeRunner(ok("cid"))
        backend = _backend(tmp_path, runner)
        backend.create_execution_container()
        assert runner.calls[0][:2] == ["docker", "run"]
        assert "--detach" in runner.calls[0] and "--rm" in runner.calls[0]
        assert "--network" in runner.calls[0] and "none" in runner.calls[0]
        assert len(runner.calls) == 1

    def test_networked_second_container_eagerly_created(self, tmp_path):
        """038: a network-declaring startup creates the per-run internal
        network, the network-none container, and the internal-network
        container - zero infra work is deferred to the run phase."""
        runner = FakeRunner(ok("net"), ok("cid-none"), ok("cid-net"))
        backend = _backend(tmp_path, runner, network=True)
        backend.create_execution_container()
        assert len(runner.calls) == 3
        net_create, none_argv, net_argv = runner.calls
        assert net_create[:3] == ["docker", "network", "create"]
        assert "--internal" in net_create and backend._run_net in net_create
        assert "--network" in none_argv and "none" in none_argv
        assert none_argv[none_argv.index("--name") + 1].endswith("-exec")
        assert "--network" in net_argv and backend._run_net in net_argv
        assert "--add-host" not in net_argv
        assert net_argv[net_argv.index("--name") + 1].endswith("-exec-net")

    def test_networked_container_failure_is_loud_and_reclaims_main(self, tmp_path):
        runner = FakeRunner(ok("net"), ok("cid-none"),
                            fail(125, "network unavailable"))
        backend = _backend(tmp_path, runner, network=True)
        with pytest.raises(SandboxError, match="AE-SBX-013"):
            backend.create_execution_container()
        assert backend._started is False
        # the already-started network-none container is reclaimed, never leaked
        rm = [c for c in runner.calls if c[1] == "rm"]
        assert rm and backend._container in rm[-1]


    def test_seccomp_profile_plumbed_through_backend(self, tmp_path):
        """023: the isolation config's seccomp_profile reaches the exec
        container argv (both network modes use the same payload builder)."""
        runner = FakeRunner(ok("cid"))
        backend = _backend(tmp_path, runner,
                           seccomp_profile="/host/chromium-userns.json")
        backend.create_execution_container()
        argv = runner.calls[0]
        assert "--security-opt" in argv
        assert argv[argv.index("--security-opt") + 1] == "seccomp=/host/chromium-userns.json"
        assert "--cap-add" in argv
        assert argv[argv.index("--cap-add") + 1] == "SYS_CHROOT"


class TestRun:
    def test_exec_argv_assembly(self, tmp_path):
        runner = FakeRunner(ok(""))  # ensure_image not called here
        backend = _backend(tmp_path, runner)
        backend._container = "c1"
        backend._started = True
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(tmp_path / "ws"),
                                      env={"PATH": "/bin", "K": "v"}, timeout=10.0)
        backend.run(req)
        exec_argv = runner.calls[-1]
        assert exec_argv[0] == "docker" and exec_argv[1] == "exec"
        assert exec_argv[-4:] == ["/usr/bin/timeout", "10", "echo", "hi"]

    def test_no_timeout_argv_verbatim(self, tmp_path):
        runner = FakeRunner(ok(""))
        backend = _backend(tmp_path, runner)
        backend._container = "c1"
        backend._started = True
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(tmp_path / "ws"),
                                      env={}, timeout=None)
        backend.run(req)
        assert backend._container in runner.calls[-1]
        assert "/usr/bin/timeout" not in runner.calls[-1]

    def test_networked_request_routes_to_network_container(self, tmp_path):
        """014: an exec routes to the bridge-network container iff the request
        declares network - the default stays network-none."""
        runner = FakeRunner(ok(""), ok(""))
        backend = _backend(tmp_path, runner, network=True)
        backend._container = "c1"
        backend._network_container = "c1-net"
        backend._started = True
        backend._network_started = True
        req = SandboxExecutionRequest(argv=["curl", "https://example.com"],
                                      cwd=str(tmp_path / "ws"), env={},
                                      network=True)
        backend.run(req)
        assert "c1-net" in runner.calls[-1]
        assert "c1" not in runner.calls[-1]

    def test_networked_request_without_network_container_is_loud(self, tmp_path):
        runner = FakeRunner()
        backend = _backend(tmp_path, runner, network=True)
        backend._container = "c1"
        backend._started = True  # network container never started
        req = SandboxExecutionRequest(argv=["curl", "https://example.com"],
                                      cwd=str(tmp_path / "ws"), env={},
                                      network=True)
        with pytest.raises(SandboxError, match="AE-SBX-013"):
            backend.run(req)
        assert not runner.calls  # never reached docker exec

    def test_exit_124_is_timeout(self, tmp_path):
        runner = FakeRunner(CmdResult(124, "out", ""))
        backend = _backend(tmp_path, runner)
        backend._container = "c1"
        backend._started = True
        req = SandboxExecutionRequest(argv=["sleep", "5"], cwd=str(tmp_path / "ws"),
                                      env={}, timeout=1.0)
        result = backend.run(req)
        assert result.timed_out and result.is_timeout
        assert result.exit_code == 124

    def test_git_mutation_blocked(self, tmp_path):
        runner = FakeRunner()
        backend = _backend(tmp_path, runner, git_blocked=True)
        backend._container = "c1"
        backend._started = True
        req = SandboxExecutionRequest(argv=["git", "commit", "-m", "x"],
                                      cwd=str(tmp_path / "ws"), env={})
        with pytest.raises(SandboxError, match="AE-SBX-008"):
            backend.run(req)
        assert not runner.calls  # never reached docker

    def test_git_mutation_allowed_when_knob_set(self, tmp_path):
        runner = FakeRunner(ok(""))
        backend = _backend(tmp_path, runner, git_blocked=False)
        backend._container = "c1"
        backend._started = True
        req = SandboxExecutionRequest(argv=["git", "commit", "-m", "x"],
                                      cwd=str(tmp_path / "ws"), env={})
        backend.run(req)
        assert runner.calls

    def test_audit_record_written(self, tmp_path):
        runner = FakeRunner(CmdResult(1, "", "boom"))
        backend = _backend(tmp_path, runner)
        backend._container = "c1"
        backend._started = True
        req = SandboxExecutionRequest(argv=["false"], cwd=str(tmp_path / "ws"),
                                      env={"K": "secret"}, pipeline="p", step="s")
        backend.run(req)
        audit = (tmp_path / "state" / "sandbox-audit.jsonl").read_text().strip()
        rec = json.loads(audit)
        assert rec["pipeline"] == "p" and rec["step"] == "s"
        assert rec["exit_code"] == 1
        # env values never logged; env keys are hashed into the digest only
        assert "secret" not in audit
        assert "secret" not in rec["request_digest"]

    def test_daemon_lost_raises_after_audit(self, tmp_path):
        runner = FakeRunner(CmdResult(None, "", "lost"))
        backend = _backend(tmp_path, runner)
        backend._container = "c1"
        backend._started = True
        req = SandboxExecutionRequest(argv=["true"], cwd=str(tmp_path / "ws"), env={})
        with pytest.raises(SandboxError, match="AE-SBX-012"):
            backend.run(req)
        assert (tmp_path / "state" / "sandbox-audit.jsonl").exists()


class TestClose:
    def test_idempotent(self, tmp_path):
        runner = FakeRunner(ok(""), ok(""))
        backend = _backend(tmp_path, runner)
        backend._started = True
        backend._container = "c1"
        backend.close()
        backend.close()
        stops = [c for c in runner.calls if c[0] == "docker" and c[1] == "stop"]
        assert len(stops) == 1

    def test_noop_when_not_started(self, tmp_path):
        runner = FakeRunner()
        backend = _backend(tmp_path, runner)
        backend.close()
        assert runner.calls == []

    def test_tolerates_auto_removed_container(self, tmp_path):
        """D2: with --rm the daemon may have auto-removed the container by the
        time close() runs, so stop/rm return "no such container" (rc != 0).
        close() must treat that as success and never raise."""
        runner = FakeRunner(fail(1, "No such container"),
                            fail(1, "No such container"))
        backend = _backend(tmp_path, runner)
        backend._started = True
        backend._container = "c1"
        backend.close()  # must not raise
        assert not backend._started

    def test_closes_network_container_too(self, tmp_path):
        runner = FakeRunner(ok(""), ok(""), ok(""), ok(""))
        backend = _backend(tmp_path, runner, network=True)
        backend._container = "c1"
        backend._network_container = "c1-net"
        backend._started = True
        backend._network_started = True
        backend.close()
        stopped = [c[-1] for c in runner.calls if c[1] == "stop"]
        assert stopped == ["c1", "c1-net"]
        assert not backend._started and not backend._network_started


# ---------------------------------------------------------------------------
# Startup + audit (real temp dirs)
# ---------------------------------------------------------------------------
class TestStartupSandbox:
    """Startup sequence: probe → image → create/start the exec container →
    materialise the export + host-side caches. Setup runs at the step boundary
    (052), never at startup."""

    def _cfg(self, tmp_path, with_setup=False):
        from agentic_dev_loop.core.config import PipelineConfig
        steps = [{"name": "dev", "agent": "dev", "on_pass": "test"},
                 {"name": "test", "command": "make", "on_pass": "success"}]
        if with_setup:
            steps[1]["setup"] = "python -m venv .venv-sandbox"
        return PipelineConfig.model_validate({
            "name": "p", "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "agents", "steps": steps,
            "lockfiles": ["uv.lock"],
            "isolation": {"state_dir": str(tmp_path / "iso-state")},
        })

    def test_startup_reaps_orphaned_containers(self, tmp_path, monkeypatch):
        """030: the reaper runs at startup, before any container creation, and
        removes a leaked exec container whose owning pid is dead."""
        from agentic_dev_loop.engine.isolation import (CONTAINER_REGISTRY,
                                                       startup_sandbox)

        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = self._cfg(tmp_path)
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        name = f"{'a' * 32}-exec"
        iso = tmp_path / "iso-state"
        iso.mkdir(parents=True, exist_ok=True)
        (iso / CONTAINER_REGISTRY).write_text(
            json.dumps({"run_id": "dead", "name": name, "pid": proc.pid,
                        "process_start": process_start(proc.pid),
                        "created_at": "now"}) + "\n")
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        monkeypatch.setenv("AGENTIC_DEV_LOOP_DEV_IMAGE", "img:tag")
        runner = FakeRunner(
            ok("1.54"),  # probe version
            ok("linux"),  # probe ostype
            ok(""),  # reaper: docker rm -f
            ok(""),  # image inspect
            ok(""),  # docker run (exec container)
        )
        sandbox = startup_sandbox(cfg, workspace=ws,
                                  state_dir=tmp_path / "run-state",
                                  runner=runner)
        try:
            rm_calls = [c for c in runner.calls if c[:2] == ["docker", "rm"]]
            assert ["docker", "rm", "-f", name] in rm_calls
            remaining = [
                json.loads(line)
                for line in (iso / CONTAINER_REGISTRY).read_text().splitlines()
                if line.strip()
            ]
            assert all(entry["name"] != name for entry in remaining)
        finally:
            sandbox.close()

    def test_state_dir_required(self, tmp_path, monkeypatch):
        """The run state_dir is mandatory: the runtime always resolves a
        mount set for steps, so enforcement needs the materialised export
        (AE-SBX-017). Omitting state_dir fails at the call - never silently
        degrades to the legacy workspace model."""
        from agentic_dev_loop.engine.isolation import startup_sandbox

        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = self._cfg(tmp_path)
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        monkeypatch.setenv("AGENTIC_DEV_LOOP_DEV_IMAGE", "img:tag")
        runner = FakeRunner()
        with pytest.raises(TypeError, match="state_dir"):
            startup_sandbox(cfg, workspace=ws, runner=runner)  # type: ignore[call-arg]
        assert runner.calls == []

    def test_sequence_no_provision_at_startup(self, tmp_path, monkeypatch):
        from agentic_dev_loop.engine import isolation
        from agentic_dev_loop.engine.isolation import startup_sandbox

        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = self._cfg(tmp_path, with_setup=True)
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        monkeypatch.setenv("AGENTIC_DEV_LOOP_DEV_IMAGE", "img:tag")
        runner = FakeRunner(
            ok("1.54"),           # probe version
            ok("linux"),          # probe ostype
            ok(""),               # image inspect (pull)
            ok(""),               # docker run -d --rm (exec container)
            ok(""),               # docker run provision
        )
        sandbox = startup_sandbox(cfg, workspace=ws, state_dir=tmp_path / "run-state", runner=runner)
        try:
            kinds = [c[1] for c in runner.calls]
            assert kinds == ["version", "info", "image", "run"]
            # 052: setup runs at the step boundary, never at startup.
            assert not [c for c in runner.calls
                        if "python -m venv .venv-sandbox" in c]
        finally:
            sandbox.close()

    def test_network_declaration_starts_second_container(self, tmp_path, monkeypatch):
        """014/unit-5: startup creates only the base network-none export
        container. The bridge-network container for a network-declaring step
        starts lazily in the profile pool on the first networked request
        (covered by test_isolation_integration), never eagerly at startup."""
        from agentic_dev_loop.core.config import PipelineConfig
        from agentic_dev_loop.engine.isolation import startup_sandbox

        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = PipelineConfig.model_validate({
            "name": "p", "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "agents",
            "lockfiles": ["uv.lock"],
            "steps": [{"name": "dev", "agent": "dev", "on_pass": "test"},
                      {"name": "test", "command": "make", "network": True,
                       "on_pass": "success"}],
            "isolation": {"state_dir": str(tmp_path / "iso-state")},
        })
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        monkeypatch.setenv("AGENTIC_DEV_LOOP_DEV_IMAGE", "img:tag")
        runner = FakeRunner(
            ok("1.54"),           # probe version
            ok("linux"),          # probe ostype
            ok(""),               # image inspect
            ok(""),               # docker run -d --rm (base export container)
        )
        sandbox = startup_sandbox(cfg, workspace=ws, state_dir=tmp_path / "run-state", runner=runner)
        try:
            run_argv = [c for c in runner.calls if c[1] == "run"]
            assert len(run_argv) == 1
            assert "--network" in run_argv[0] and "none" in run_argv[0]
        finally:
            sandbox.close()

    def test_bundled_seccomp_sentinel_resolved_and_plumbed(self, tmp_path, monkeypatch):
        """025: a ``bundled:chromium-userns`` sentinel is expanded to the real
        packaged path before the exec payload is built, so the payload sees a
        plain path and emits the profile plus the cap-gated SYS_CHROOT add."""
        from agentic_dev_loop.engine.isolation import startup_sandbox
        from agentic_dev_loop.engine.seccomp import chromium_userns_profile_path

        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = self._cfg(tmp_path, with_setup=False)
        cfg.isolation.seccomp_profile = "bundled:chromium-userns"
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        monkeypatch.setenv("AGENTIC_DEV_LOOP_DEV_IMAGE", "img:tag")
        runner = FakeRunner(
            ok("1.54"),   # probe version
            ok("linux"),  # probe ostype
            ok(""),       # image inspect
            ok(""),       # docker run -d --rm (exec container)
        )
        sandbox = startup_sandbox(cfg, workspace=ws, state_dir=tmp_path / "run-state", runner=runner)
        try:
            run_argv = [c for c in runner.calls if c[:2] == ["docker", "run"]][0]
            resolved = chromium_userns_profile_path()
            assert "--security-opt" in run_argv
            assert run_argv[run_argv.index("--security-opt") + 1] == f"seccomp={resolved}"
            assert "--cap-add" in run_argv
            assert run_argv[run_argv.index("--cap-add") + 1] == "SYS_CHROOT"
        finally:
            sandbox.close()

    def test_unknown_bundled_seccomp_name_fails_before_docker_probe(self, tmp_path, monkeypatch):
        """025: an unknown bundled:<name> fails loud at startup, before any
        Docker probe runs."""
        from agentic_dev_loop.engine.isolation import startup_sandbox

        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = self._cfg(tmp_path, with_setup=False)
        cfg.isolation.seccomp_profile = "bundled:unknown-name"
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        monkeypatch.setenv("AGENTIC_DEV_LOOP_DEV_IMAGE", "img:tag")
        runner = FakeRunner()
        with pytest.raises(ValueError, match="bundled:chromium-userns"):
            startup_sandbox(cfg, workspace=ws, state_dir=tmp_path / "run-state", runner=runner)
        assert runner.calls == []

class TestBundledSeccompProfile:
    """023: the bundled chromium-userns.json must be valid JSON, deny-by-
    default (hardening preserved), allow the userns syscalls Chrome's
    namespace sandbox needs, and keep the privileged mount/admin syscalls
    cap-gated (never unconditionally allowed)."""

    @staticmethod
    def _doc():
        from importlib.resources import files
        traversable = files("agentic_dev_loop.engine.seccomp").joinpath(
            "chromium-userns.json")
        return json.loads(traversable.read_text(encoding="utf-8"))

    def _unconditional_allow(self):
        doc = self._doc()
        names = set()
        for entry in doc["syscalls"]:
            if (entry.get("action") == "SCMP_ACT_ALLOW"
                    and not entry.get("args")
                    and not entry.get("includes")):
                names.update(entry["names"])
        return names

    def test_deny_by_default(self):
        assert self._doc()["defaultAction"] == "SCMP_ACT_ERRNO"

    def test_userns_syscalls_allowed(self):
        assert {"clone", "clone3", "unshare"} <= self._unconditional_allow()

    def test_privileged_syscalls_stay_gated(self):
        privileged = {"mount", "umount", "umount2", "setns", "reboot",
                      "kexec_load", "bpf", "ptrace", "chroot"}
        assert privileged.isdisjoint(self._unconditional_allow())

    def test_profile_path_helper_resolves_to_bundled_json(self):
        """023: the public helper returns a real, readable host path to the
        bundled profile (what a consumer passes as
        ``isolation.seccomp_profile``)."""
        from agentic_dev_loop.engine.seccomp import chromium_userns_profile_path

        path = Path(chromium_userns_profile_path())
        assert path.is_file()
        assert json.loads(path.read_text(encoding="utf-8"))["defaultAction"]             == "SCMP_ACT_ERRNO"


class TestResolveSeccompProfile:
    """025: the ``bundled:<name>`` sentinel resolves to a packaged profile
    path; raw host paths pass through unchanged; unknown names fail loud with
    every known bundled name."""

    def test_bundled_sentinel_resolves_to_real_path(self):
        from agentic_dev_loop.engine.seccomp import (chromium_userns_profile_path,
                                                     resolve_seccomp_profile)

        resolved = resolve_seccomp_profile("bundled:chromium-userns")
        assert resolved == chromium_userns_profile_path()
        assert Path(resolved).is_file()

    def test_unknown_bundled_name_lists_known(self):
        from agentic_dev_loop.engine.seccomp import resolve_seccomp_profile

        with pytest.raises(ValueError, match="bundled:chromium-userns"):
            resolve_seccomp_profile("bundled:unknown-name")

    def test_empty_bundled_name_lists_known(self):
        from agentic_dev_loop.engine.seccomp import resolve_seccomp_profile

        with pytest.raises(ValueError, match="bundled:chromium-userns"):
            resolve_seccomp_profile("bundled:")

    def test_none_passes_through(self):
        from agentic_dev_loop.engine.seccomp import resolve_seccomp_profile

        assert resolve_seccomp_profile(None) is None

    def test_raw_path_passes_through(self):
        from agentic_dev_loop.engine.seccomp import resolve_seccomp_profile

        assert resolve_seccomp_profile(
            "/host/chromium-userns.json") == "/host/chromium-userns.json"

    def test_bundled_profile_path_unknown_name(self):
        from agentic_dev_loop.engine.seccomp import bundled_profile_path

        with pytest.raises(ValueError, match="bundled:chromium-userns"):
            bundled_profile_path("nope")

    def test_sentinel_resolves_into_payload(self):
        """025: the resolved sentinel path reaches ``docker_create_payload``
        unchanged, producing the profile security-opt and the cap-add."""
        from agentic_dev_loop.engine.seccomp import resolve_seccomp_profile

        resolved = resolve_seccomp_profile("bundled:chromium-userns")
        payload = docker_create_payload(
            run_id="r1", image="img",
            profile=EffectiveProfile(True, True, True, True, True,
                                     "img", "4g", 2.0),
            workspace="/ws", seccomp_profile=resolved)
        assert payload[payload.index("--security-opt") + 1] == f"seccomp={resolved}"
        assert payload[payload.index("--cap-add") + 1] == "SYS_CHROOT"


class TestUnit5PayloadAdditions:
    """Unit-5 (step 9): host-gateway reachability, proxy env injection, and
    mount-set/export-cwd assembly - the profile-keyed payload building blocks."""

    def _profile(self):
        return EffectiveProfile(True, True, True, True, True, "img", "4g", 2.0)

    def test_network_none_payload_omits_host_gateway(self, tmp_path):
        ws = str(tmp_path)
        p = docker_create_payload(run_id="r1", image="img",
                                  profile=self._profile(), workspace=ws)
        assert "--add-host" not in p

    def test_egress_proxy_env_injected(self, tmp_path):
        ws = str(tmp_path)
        p = docker_create_payload(run_id="r1", image="img",
                                  profile=self._profile(), workspace=ws,
                                  egress_proxy="http://127.0.0.1:4321")
        assert "--env" in p and "HTTP_PROXY=http://127.0.0.1:4321" in p
        assert "HTTPS_PROXY=http://127.0.0.1:4321" in p

    def test_mount_set_overrides_workspace_mount(self, tmp_path):
        """Declared-writes model (BR-1.2): the export root is mounted ro and
        only declared-rw subpaths get a writable submount; declared-ro paths
        are covered by the ro root. The cwd becomes the export root."""
        ws = str(tmp_path)
        export = str(tmp_path / "export")
        ms = resolve_mount_set([MountSpec(path="src", mode="ro"),
                                MountSpec(path="target", mode="rw")])
        p = docker_create_payload(run_id="r1", image="img",
                                  profile=self._profile(), workspace=ws,
                                  mount_set=ms, export_root=export)
        mounts = [p[i + 1] for i, a in enumerate(p) if a == "-v"]
        assert mounts == [
            f"{export}:{export}:ro",
            f"{export}/target:{export}/target",
        ]
        assert p[p.index("--workdir") + 1] == export

    def test_mount_set_whole_tree_mode(self, tmp_path):
        """A `.` entry mounts the whole export with its declared mode (the
        dogfood's dev/restore/test use rw, review/qa use ro)."""
        ws = str(tmp_path)
        export = str(tmp_path / "export")
        rw = docker_create_payload(run_id="r1", image="img",
                                   profile=self._profile(), workspace=ws,
                                   mount_set=resolve_mount_set(
                                       [MountSpec(path=".", mode="rw")]),
                                   export_root=export)
        assert f"{export}:{export}" in rw
        assert f"{export}:{export}:ro" not in rw
        ro = docker_create_payload(run_id="r1", image="img",
                                   profile=self._profile(), workspace=ws,
                                   mount_set=resolve_mount_set(
                                       [MountSpec(path=".", mode="ro")]),
                                   export_root=export)
        assert f"{export}:{export}:ro" in ro


class TestContainerProfileKey:
    def test_mount_flags_empty_mounts_nothing(self, tmp_path):
        """Strict-by-default (BR-1.1): an empty mount set mounts nothing."""
        export = str(tmp_path / "export")
        flags = mount_set_docker_flags(export, resolve_mount_set([]))
        assert flags == []


class TestUnit5RuntimeEnforcement:
    """The unit-5 enforcement path: export materialisation, the profile-keyed
    pool, proxy env injection, and sync-back - all through the fake runner."""

    def _cfg(self, tmp_path, steps=None):
        from agentic_dev_loop.core.config import PipelineConfig
        steps = steps or [{"name": "dev", "agent": "dev", "on_pass": "test"},
                          {"name": "test", "command": "make", "on_pass": "success"}]
        return PipelineConfig.model_validate({
            "name": "p", "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "agents", "steps": steps,
            "lockfiles": ["uv.lock"],
            "isolation": {"state_dir": str(tmp_path / "iso-state")},
        })

    def test_startup_materialises_gitfree_export(self, tmp_path, monkeypatch):
        """BR-2: startup materialises the export under state_dir/exports and
        the .git dir is absent by construction."""
        from agentic_dev_loop.engine.isolation import startup_sandbox

        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "a.py").write_text("x")
        (ws / ".git").mkdir()
        (ws / ".git" / "HEAD").write_text("ref")
        state_dir = tmp_path / "run-state"
        cfg = self._cfg(tmp_path)
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        monkeypatch.setenv("AGENTIC_DEV_LOOP_DEV_IMAGE", "img:tag")
        runner = FakeRunner(ok("1.54"), ok("linux"), ok(""), ok(""))
        sandbox = startup_sandbox(cfg, workspace=ws, runner=runner,
                                  state_dir=state_dir)
        try:
            assert sandbox.export_root is not None
            export = Path(sandbox.export_root)
            assert export.is_relative_to(state_dir / "exports")
            assert (export / "src" / "a.py").read_text() == "x"
            assert not (export / ".git").exists()
        finally:
            sandbox.close()

    def test_profile_container_mounts_and_proxy_env(self, tmp_path):
        """038: a step's mount set + egress policy key a pooled container;
        the proxy env points at the proxy container's internal IP
        (never 127.0.0.1 or the host-gateway), the proxy binds a pinned
        internal IP after a readiness probe, and the exec cwd is the export
        root."""
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        (export / "src" / "a.py").write_text("x")
        runner = FakeRunner(
            ok("net"), ok("172.18.0.0/16 "), ok("proxy"), ok(""), ok(""),
            ok("cid"), ok("true"), ok(""), ok("true"), ok(""))
        backend = _backend(tmp_path, runner)
        backend._export_root = str(export)
        ms = resolve_mount_set([MountSpec(path="src", mode="ro")])
        pol = EgressPolicy(targets=(TargetEntry(host="pypi.org"),))
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=True, mount_set=ms,
                                      egress_policy=pol)
        try:
            backend.run(req)
            prof = next(iter(backend._pool.values()))
            proxy_name = prof.proxy_name
            assert proxy_name is not None
            net_create = runner.calls[0]
            assert net_create[:3] == ["docker", "network", "create"]
            assert "--internal" in net_create and backend._run_net in net_create
            subnet_inspect = runner.calls[1]
            assert subnet_inspect[:3] == ["docker", "network", "inspect"]
            assert backend._run_net in subnet_inspect
            proxy_run = runner.calls[2]
            assert proxy_run[:2] == ["docker", "run"]
            assert "--network" in proxy_run and "bridge" in proxy_run
            assert "--add-host" in proxy_run
            assert "host.docker.internal:host-gateway" in proxy_run
            assert "--read-only" in proxy_run
            assert proxy_run[proxy_run.index("--host") + 1] == "172.18.0.2"
            connect = runner.calls[3]
            assert connect == ["docker", "network", "connect", "--ip",
                               "172.18.0.2", backend._run_net, proxy_name]
            probe = runner.calls[4]
            assert probe[:2] == ["docker", "exec"]
            assert probe[-2] == "172.18.0.2"
            assert probe[-1] == "3128"
            run_argv = runner.calls[5]
            assert run_argv[:2] == ["docker", "run"]
            assert "--network" in run_argv and backend._run_net in run_argv
            assert "--add-host" not in run_argv
            assert f"{export}:{export}:ro" in run_argv
            assert f"{export}/src:{export}/src" not in run_argv
            proxy_envs = [a for a in run_argv
                          if a.startswith("HTTP_PROXY=")
                          or a.startswith("HTTPS_PROXY=")]
            assert proxy_envs and all(
                "http://172.18.0.2:3128" in a for a in proxy_envs)
            assert run_argv[run_argv.index("--workdir") + 1] == str(export)
            inspect = runner.calls[6]
            assert inspect == ["docker", "inspect", "-f",
                               "{{.State.Running}}", proxy_name]
            exec_argv = runner.calls[7]
            assert exec_argv[:2] == ["docker", "exec"]
            assert exec_argv[exec_argv.index("-w") + 1] == str(export)
            # the profile container is pooled: a second run reuses it
            backend.run(req)
            runs = [c for c in runner.calls if c[:2] == ["docker", "run"]]
            assert len(runs) == 2  # proxy container + sandbox container
        finally:
            backend.close()

    def test_proxy_death_fails_closed(self, tmp_path):
        """BR-11.3 / AE-SBX-018: a networked profile whose proxy container
        is reported dead fails the request loudly - the enforcement point
        being down is never a silent degradation."""
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        runner = FakeRunner(
            ok("net"), ok("172.18.0.0/16 "), ok("proxy"), ok(""), ok(""),
            ok("cid"), ok("true"), ok(""), ok("false"))
        backend = _backend(tmp_path, runner)
        backend._export_root = str(export)
        ms = resolve_mount_set([MountSpec(path="src", mode="ro")])
        pol = EgressPolicy(targets=(TargetEntry(host="pypi.org"),))
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=True, mount_set=ms,
                                      egress_policy=pol)
        try:
            backend.run(req)
            prof = next(iter(backend._pool.values()))
            assert prof.proxy_name is not None
            with pytest.raises(SandboxError, match="proxy"):
                backend.run(req)
        finally:
            backend.close()

    def test_proxy_readiness_exhaustion_raises_proxy_error(self, tmp_path):
        """039 D2: the bounded readiness probe runs between container start and
        sandbox payload creation; exhaustion raises the internal proxy error."""
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        runner = FakeRunner(
            ok("net"), ok("172.18.0.0/16 "), ok("proxy"), ok(""),
            fail(1, ""), fail(1, ""), fail(1, ""), fail(1, ""), fail(1, ""),
            ok(""), ok(""))
        backend = _backend(tmp_path, runner)
        backend._export_root = str(export)
        ms = resolve_mount_set([MountSpec(path="src", mode="ro")])
        pol = EgressPolicy(targets=(TargetEntry(host="pypi.org"),))
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=True, mount_set=ms,
                                      egress_policy=pol)
        try:
            with pytest.raises(SandboxError, match="AE-SBX-021"):
                backend.run(req)
            probes = [c for c in runner.calls
                      if c[:2] == ["docker", "exec"] and "python3" in c]
            assert len(probes) == PROXY_READY_ATTEMPTS
        finally:
            backend.close()

    def test_proxy_dial_rewrites_reach_the_payload(self, tmp_path):
        """039 D3: the backend's dial_rewrites field reaches the proxy argv
        instead of the hardcoded empty map."""
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        rewrites = {"host.docker.internal": "127.0.0.1"}
        runner = FakeRunner(
            ok("net"), ok("172.18.0.0/16 "), ok("proxy"), ok(""), ok(""),
            ok("cid"), ok("true"), ok(""))
        backend = _backend(tmp_path, runner, dial_rewrites=rewrites)
        backend._export_root = str(export)
        ms = resolve_mount_set([MountSpec(path="src", mode="ro")])
        pol = EgressPolicy(targets=(TargetEntry(host="pypi.org"),))
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=True, mount_set=ms,
                                      egress_policy=pol)
        try:
            backend.run(req)
            proxy_run = runner.calls[2]
            idx = proxy_run.index("--dial-rewrites")
            assert proxy_run[idx + 1] == json.dumps(rewrites)
        finally:
            backend.close()

    def test_rw_step_syncs_back(self, tmp_path):
        """BR-4: after a declared-rw step, the export changes sync back to the
        real repo and a sync-back audit record is written host-side."""
        import hashlib

        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "a.py").write_text("orig")
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        (export / "src" / "a.py").write_text("orig")
        runner = FakeRunner(ok("cid"), ok(""))
        backend = _backend(tmp_path, runner)
        backend._export_root = str(export)
        backend._baseline = {"src/a.py": hashlib.sha256(b"orig").hexdigest()}
        (export / "src" / "a.py").write_text("changed")
        ms = resolve_mount_set([MountSpec(path="src", mode="rw")])
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=False, mount_set=ms,
                                      egress_policy=EgressPolicy())
        try:
            backend.run(req)
            assert (ws / "src" / "a.py").read_text() == "changed"
            audit = (tmp_path / "state" / "sandbox-audit.jsonl").read_text()
            assert "sync_back" in audit
            assert "src/a.py" in audit
        finally:
            backend.close()

    def test_sync_back_host_added_skipped_and_audited(self, tmp_path):
        """037: a real file absent from both the export and the baseline is a
        host-side addition, not an agent deletion - it is left untouched and
        the sync-back audit record names the skipped path."""
        import hashlib

        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "a.py").write_text("orig")
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        (export / "src" / "a.py").write_text("orig")
        (ws / "src" / "host-added.py").write_text("host-side addition")

        runner = FakeRunner(ok("cid"), ok(""))
        backend = _backend(tmp_path, runner)
        backend._export_root = str(export)
        backend._baseline = {"src/a.py": hashlib.sha256(b"orig").hexdigest()}
        ms = resolve_mount_set([MountSpec(path="src", mode="rw")])
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=False, mount_set=ms,
                                      egress_policy=EgressPolicy())
        try:
            backend.run(req)
            assert (ws / "src" / "host-added.py").read_text() == "host-side addition"
            audit = (tmp_path / "state" / "sandbox-audit.jsonl").read_text()
            assert "sync_back" in audit
            assert "host_added_paths" in audit
            assert "src/host-added.py" in audit
        finally:
            backend.close()

    def test_whole_tree_rw_syncs_back_and_cache_untouched(self, tmp_path):
        """BR-4 + BR-3/F2: a ``.``-rw step (the shipped pipeline's shape)
        syncs its edits back, while the bind-mounted cache is never classified
        as a deletion - the real workspace cache survives."""
        import hashlib

        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "a.py").write_text("orig")
        (ws / "node_modules").mkdir()
        (ws / "node_modules" / "dep.js").write_text("cache")

        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        (export / "src" / "a.py").write_text("orig")
        (export / "node_modules").mkdir()  # empty cache mount target

        runner = FakeRunner(ok("cid"), ok(""))
        backend = DockerBackend(
            image="img@sha256:abc", workspace=str(ws),
            iso_state_dir=str(tmp_path / "state"),
            profile=EffectiveProfile(True, True, True, True, True,
                                     "img@sha256:abc", "4g", 2.0),
            runner=runner, network=False,
            export_root=str(export),
            cache_mounts=[(str(ws / "node_modules"),
                           f"{export}/node_modules", "rw")])
        backend._baseline = {"src/a.py": hashlib.sha256(b"orig").hexdigest()}
        (export / "src" / "a.py").write_text("changed")
        ms = resolve_mount_set([MountSpec(path=".", mode="rw")])
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=False, mount_set=ms,
                                      egress_policy=EgressPolicy())
        try:
            backend.run(req)
            assert (ws / "src" / "a.py").read_text() == "changed"
            assert (ws / "node_modules" / "dep.js").read_text() == "cache"
            audit = (tmp_path / "state" / "sandbox-audit.jsonl").read_text()
            assert "src/a.py" in audit
            assert "node_modules" not in audit
        finally:
            backend.close()

    def test_sync_back_conflict_is_loud(self, tmp_path):
        """BR-4.4: a declared-rw path changed in both the export and the real
        repo since materialisation is a loud failure, never an overwrite."""
        import hashlib

        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "a.py").write_text("external")
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        (export / "src" / "a.py").write_text("agent")
        runner = FakeRunner(ok("cid"), ok(""))
        backend = _backend(tmp_path, runner)
        backend._export_root = str(export)
        backend._baseline = {"src/a.py": hashlib.sha256(b"orig").hexdigest()}
        ms = resolve_mount_set([MountSpec(path="src", mode="rw")])
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=False, mount_set=ms,
                                      egress_policy=EgressPolicy())
        try:
            with pytest.raises(SandboxError, match="AE-SBX-016"):
                backend.run(req)
            assert (ws / "src" / "a.py").read_text() == "external"
        finally:
            backend.close()


    def test_sync_back_merge_path_converges_and_no_raise(self, tmp_path, capsys):
        """029: a conflict on a declared merge path is union-merged: host and
        export converge, the baseline is re-snapshotted, and no raise occurs."""
        import hashlib

        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "audit.md").write_text("base\nhost-line\n")
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        (export / "src" / "audit.md").write_text("base\nexport-line\n")
        runner = FakeRunner(ok("cid"), ok(""))
        backend = DockerBackend(
            image="img@sha256:abc", workspace=str(ws),
            iso_state_dir=str(tmp_path / "state"),
            profile=EffectiveProfile(True, True, True, True, True,
                                     "img@sha256:abc", "4g", 2.0),
            runner=runner, network=False, export_root=str(export),
            merge_on_conflict=["src/audit.md"])
        backend._baseline = {"src/audit.md": hashlib.sha256(b"base\n").hexdigest()}
        ms = resolve_mount_set([MountSpec(path="src", mode="rw")])
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=False, mount_set=ms,
                                      egress_policy=EgressPolicy())
        try:
            backend.run(req)
            merged = "base\nhost-line\nexport-line\n"
            assert (ws / "src" / "audit.md").read_text() == merged
            assert (export / "src" / "audit.md").read_text() == merged
            out = capsys.readouterr().out
            assert ("[isolation] merged concurrent edits: src/audit.md "
                    "(1 sandbox lines appended)") in out
            audit = (tmp_path / "state" / "sandbox-audit.jsonl").read_text()
            assert "sync_back_merge" in audit
            assert "src/audit.md" in audit
            # the baseline was re-snapshotted: a subsequent sync is clean
            backend.run(req)
        finally:
            backend.close()

    def test_sync_back_conflict_absorbs_baseline_for_next_sync(self, tmp_path,
                                                              capsys):
        """029 fixes 1+2: a hard conflict announces once, discards the
        conflicted exec's edit (host wins), re-snapshots the baseline, and the
        next sync-back with no new changes runs clean."""
        import hashlib

        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "a.py").write_text("external")
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        (export / "src" / "a.py").write_text("agent")
        runner = FakeRunner(ok("cid"), ok(""))
        backend = DockerBackend(
            image="img@sha256:abc", workspace=str(ws),
            iso_state_dir=str(tmp_path / "state"),
            profile=EffectiveProfile(True, True, True, True, True,
                                     "img@sha256:abc", "4g", 2.0),
            runner=runner, network=False, export_root=str(export))
        backend._baseline = {"src/a.py": hashlib.sha256(b"orig").hexdigest()}
        ms = resolve_mount_set([MountSpec(path="src", mode="rw")])
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=False, mount_set=ms,
                                      egress_policy=EgressPolicy())
        try:
            with pytest.raises(SandboxError, match="AE-SBX-016"):
                backend.run(req)
            assert (ws / "src" / "a.py").read_text() == "external"
            assert (export / "src" / "a.py").read_text() == "external"
            out = capsys.readouterr().out
            assert ("[isolation] sync-back conflict: src/a.py - the workspace "
                    "changed underneath; edits to these paths will not sync") in out
            backend.run(req)  # absorbed: no new changes, no raise
        finally:
            backend.close()

    def test_conflict_raise_lists_only_hard_paths(self, tmp_path, capsys):
        """029: a mixed merge+hard conflict merges the append-style log first
        and then raises AE-SBX-016 scoped to the hard paths only."""
        import hashlib

        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "audit.md").write_text("base\nhost-line\n")
        (ws / "src" / "b.py").write_text("external-b")
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        (export / "src" / "audit.md").write_text("base\nexport-line\n")
        (export / "src" / "b.py").write_text("agent-b")
        runner = FakeRunner(ok("cid"), ok(""))
        backend = DockerBackend(
            image="img@sha256:abc", workspace=str(ws),
            iso_state_dir=str(tmp_path / "state"),
            profile=EffectiveProfile(True, True, True, True, True,
                                     "img@sha256:abc", "4g", 2.0),
            runner=runner, network=False, export_root=str(export),
            merge_on_conflict=["src/audit.md"])
        backend._baseline = {
            "src/audit.md": hashlib.sha256(b"base\n").hexdigest(),
            "src/b.py": hashlib.sha256(b"orig-b").hexdigest(),
        }
        ms = resolve_mount_set([MountSpec(path="src", mode="rw")])
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=False, mount_set=ms,
                                      egress_policy=EgressPolicy())
        try:
            with pytest.raises(SandboxError) as exc:
                backend.run(req)
            assert "src/b.py" in str(exc.value)
            assert "audit.md" not in str(exc.value)
            merged = "base\nhost-line\nexport-line\n"
            assert (ws / "src" / "audit.md").read_text() == merged
            assert (export / "src" / "audit.md").read_text() == merged
            out = capsys.readouterr().out
            assert ("[isolation] merged concurrent edits: src/audit.md "
                    "(1 sandbox lines appended)") in out
            assert ("[isolation] sync-back conflict: src/b.py - the workspace "
                    "changed underneath; edits to these paths will not sync") in out
        finally:
            backend.close()


    def test_merge_path_host_deleted_raises_and_absorb_removes_export(
            self, tmp_path, capsys):
        """029: a host-deleted declared merge path is a hard conflict - the
        host wins, the export copy is unlinked, and AE-SBX-016 raises once."""
        import hashlib

        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        (export / "src" / "audit.md").write_text("export\n")
        runner = FakeRunner(ok("cid"), ok(""))
        backend = DockerBackend(
            image="img@sha256:abc", workspace=str(ws),
            iso_state_dir=str(tmp_path / "state"),
            profile=EffectiveProfile(True, True, True, True, True,
                                     "img@sha256:abc", "4g", 2.0),
            runner=runner, network=False, export_root=str(export),
            merge_on_conflict=["src/audit.md"])
        backend._baseline = {"src/audit.md": hashlib.sha256(b"base\n").hexdigest()}
        ms = resolve_mount_set([MountSpec(path="src", mode="rw")])
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=False, mount_set=ms,
                                      egress_policy=EgressPolicy())
        try:
            with pytest.raises(SandboxError, match="AE-SBX-016"):
                backend.run(req)
            assert not (ws / "src" / "audit.md").exists()
            assert not (export / "src" / "audit.md").exists()
            out = capsys.readouterr().out
            assert ("[isolation] sync-back conflict: src/audit.md - the "
                    "workspace changed underneath") in out
            backend.run(req)  # absorbed: no new changes, no raise
        finally:
            backend.close()

    def test_merge_path_export_deleted_raises_and_absorb_restores_export(
            self, tmp_path, capsys):
        """029: an export-deleted declared merge path is a hard conflict - the
        host wins, the export copy is restored, and AE-SBX-016 raises once."""
        import hashlib

        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "audit.md").write_text("host\n")
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        runner = FakeRunner(ok("cid"), ok(""))
        backend = DockerBackend(
            image="img@sha256:abc", workspace=str(ws),
            iso_state_dir=str(tmp_path / "state"),
            profile=EffectiveProfile(True, True, True, True, True,
                                     "img@sha256:abc", "4g", 2.0),
            runner=runner, network=False, export_root=str(export),
            merge_on_conflict=["src/audit.md"])
        backend._baseline = {"src/audit.md": hashlib.sha256(b"base\n").hexdigest()}
        ms = resolve_mount_set([MountSpec(path="src", mode="rw")])
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=False, mount_set=ms,
                                      egress_policy=EgressPolicy())
        try:
            with pytest.raises(SandboxError, match="AE-SBX-016"):
                backend.run(req)
            assert (ws / "src" / "audit.md").read_text() == "host\n"
            assert (export / "src" / "audit.md").read_text() == "host\n"
            out = capsys.readouterr().out
            assert ("[isolation] sync-back conflict: src/audit.md - the "
                    "workspace changed underneath") in out
            backend.run(req)  # absorbed: no new changes, no raise
        finally:
            backend.close()


class TestDeclaredCachesAndSyncExcludes:
    """028: ``caches:``/``sync_excludes:`` wiring into startup and the
    engine's removal of the hardcoded cache whitelist."""

    def _cfg(self, tmp_path, **extra):
        from agentic_dev_loop.core.config import PipelineConfig
        steps = [{"name": "dev", "agent": "dev", "on_pass": "success"}]
        data = {"name": "p", "llm": {"model": "m", "api_key_env": "K"},
                "agents_dir": "agents", "steps": steps,
                "lockfiles": ["uv.lock"],
                "isolation": {"state_dir": str(tmp_path / "iso-state")}}
        data.update(extra)
        return PipelineConfig.model_validate(data)

    def _startup(self, tmp_path, monkeypatch, cfg):
        from agentic_dev_loop.engine.isolation import startup_sandbox
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        monkeypatch.setenv("AGENTIC_DEV_LOOP_DEV_IMAGE", "img:tag")
        runner = FakeRunner(ok("1.54"), ok("linux"), ok(""), ok(""))
        sandbox = startup_sandbox(cfg, workspace=ws,
                                  state_dir=tmp_path / "run-state",
                                  runner=runner)
        return ws, runner, sandbox

    def test_declared_missing_cache_auto_created(self, tmp_path, monkeypatch):
        """052: declared caches are host-side. The host cache dir is created
        under {isolation.state_dir}/caches/<name>; the export gets only the
        mount target; the workspace path is never auto-created."""
        from agentic_dev_loop.core.config import PipelineConfig
        iso_state = tmp_path / "iso-state"
        cfg = PipelineConfig.model_validate({
            "name": "p", "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "agents", "lockfiles": ["uv.lock"],
            "caches": ["node_modules"],
            "steps": [{"name": "dev", "agent": "dev", "on_pass": "success"}],
            "isolation": {"state_dir": str(iso_state)},
        })
        ws, runner, sandbox = self._startup(tmp_path, monkeypatch, cfg)
        try:
            assert not (ws / "node_modules").exists()
            assert (iso_state / "caches" / "node_modules").is_dir()
            root = Path(sandbox.export_root)
            assert (root / "node_modules").is_dir()
            run_argv = [c for c in runner.calls
                        if c[:2] == ["docker", "run"] and "--detach" in c][0]
            assert f"{iso_state / 'caches' / 'node_modules'}:{root}/node_modules" in run_argv
        finally:
            sandbox.close()

    def test_sync_excludes_flow_into_backend(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, sync_excludes=["**/bin/**", "**/obj/**"])
        _ws, _runner, sandbox = self._startup(tmp_path, monkeypatch, cfg)
        try:
            assert sandbox._sync_excludes == ["**/bin/**", "**/obj/**"]
        finally:
            sandbox.close()

    def test_no_engine_cache_whitelist_remains(self):
        import agentic_dev_loop.engine.isolation as isolation
        import agentic_dev_loop.engine.presets as presets
        assert not hasattr(presets, "CACHE_DIRS")
        assert not hasattr(isolation, "discover_cache_paths")

# ---------------------------------------------------------------------------
# 030: infra-failure classifier + orphan-container reaper
# ---------------------------------------------------------------------------


class TestClassifier:
    @pytest.mark.parametrize("exit_code,stderr,stdout", [
        (126, "openat etc/group: input/output error", ""),
        (126, "I/O error", "some stdout"),
        (125, "", ""),
        (1, "Error response from daemon: conflict", ""),
        (126, "Error response from daemon: exec failed", ""),
    ])
    def test_infra_failures_classified(self, exit_code, stderr, stdout):
        assert classify_infra_failure(exit_code, stderr, stdout) is not None

    @pytest.mark.parametrize("exit_code,stderr,stdout", [
        (127, "sh: missing: not found", ""),
        (1, "input/output error", ""),
        (126, "cannot execute binary", ""),
        (1, "read-only file system", ""),
        (1, "permission denied", ""),
        (137, "", ""),
        (124, "", ""),
        (0, "input/output error", ""),
        (1, "", ""),
        (2, "", ""),
        (2, "Error response from daemon: conflict", ""),
    ])
    def test_routine_failures_not_classified(self, exit_code, stderr, stdout):
        assert classify_infra_failure(exit_code, stderr, stdout) is None


class TestExec:
    def test_infra_eio_raises_and_is_sticky(self, tmp_path):
        runner = FakeRunner(CmdResult(126, "", "openat etc/group: input/output error"))
        backend = _backend(tmp_path, runner)
        backend._container = "c1"
        backend._started = True
        req = SandboxExecutionRequest(argv=["echo", "hi"],
                                      cwd=str(tmp_path / "ws"), env={})
        with pytest.raises(SandboxError, match="AE-SBX-019"):
            backend.run(req)
        assert len(runner.calls) == 1
        audit = (tmp_path / "state" / "sandbox-audit.jsonl").read_text().strip()
        rec = json.loads(audit)
        assert rec["exit_code"] == 126
        assert rec["infra_reason"]
        # sticky: the next run raises instantly, never spawning docker again
        with pytest.raises(SandboxError, match="AE-SBX-019"):
            backend.run(req)
        assert len(runner.calls) == 1

    def test_routine_exit_1_returned_and_not_sticky(self, tmp_path):
        runner = FakeRunner(CmdResult(1, "", "boom"))
        backend = _backend(tmp_path, runner)
        backend._container = "c1"
        backend._started = True
        req = SandboxExecutionRequest(argv=["false"], cwd=str(tmp_path / "ws"),
                                      env={})
        result = backend.run(req)
        assert result.exit_code == 1
        assert backend.infra_error is None
        backend.run(req)
        assert len(runner.calls) == 2

    def test_daemon_lost_is_sticky(self, tmp_path):
        runner = FakeRunner(CmdResult(None, "", "lost"))
        backend = _backend(tmp_path, runner)
        backend._container = "c1"
        backend._started = True
        req = SandboxExecutionRequest(argv=["true"], cwd=str(tmp_path / "ws"),
                                      env={})
        with pytest.raises(SandboxError, match="AE-SBX-012"):
            backend.run(req)
        assert len(runner.calls) == 1
        with pytest.raises(SandboxError, match="AE-SBX-012"):
            backend.run(req)
        assert len(runner.calls) == 1


class TestCreatePayloadLabels:
    def _profile(self):
        return EffectiveProfile(True, True, True, True, True, "img", "4g", 2.0)

    def test_create_payload_carries_identification_labels(self, tmp_path):
        p = docker_create_payload(run_id="r1", image="img",
                                  profile=self._profile(),
                                  workspace=str(tmp_path))
        assert "--label" in p
        labels = [p[i + 1] for i, a in enumerate(p) if a == "--label"]
        assert "agentic-dev-loop=true" in labels
        assert "agentic-dev-loop.run=r1" in labels


class TestReaper:
    def _entry(self, name, pid, process_start=None):
        return {"run_id": "r", "name": name, "pid": pid,
                "process_start": process_start, "created_at": "now"}

    def _write(self, state_dir, entries):
        path = Path(state_dir) / CONTAINER_REGISTRY
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry) + "\n")

    def _read(self, state_dir):
        path = Path(state_dir) / CONTAINER_REGISTRY
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_dead_pid_reaped_and_pruned(self, tmp_path):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        name = f"{'a' * 32}-exec"
        self._write(tmp_path / "state", [self._entry(name, proc.pid)])
        runner = FakeRunner()
        reaped = reap_orphaned_containers(tmp_path / "state", runner)
        assert reaped == [name]
        assert runner.calls == [
            ["docker", "rm", "-f", name],
            ["docker", "ps", "-a", "--filter", f"name={name}",
             "--format", "{{.Names}}"],
        ]
        assert self._read(tmp_path / "state") == []

    def test_live_pid_left_alone(self, tmp_path):
        name = f"{'b' * 32}-exec"
        entry = self._entry(name, os.getpid(), process_start(os.getpid()))
        self._write(tmp_path / "state", [entry])
        runner = FakeRunner()
        reap_orphaned_containers(tmp_path / "state", runner)
        assert runner.calls == []
        assert self._read(tmp_path / "state") == [entry]

    def test_concurrent_run_entry_left_alone(self, tmp_path):
        name = f"{'c' * 32}-exec-{'d' * 16}-net"
        entry = self._entry(name, os.getpid(), process_start(os.getpid()))
        self._write(tmp_path / "state", [entry])
        runner = FakeRunner()
        reap_orphaned_containers(tmp_path / "state", runner)
        assert runner.calls == []
        assert self._read(tmp_path / "state") == [entry]

    def test_dead_network_reaped_and_pruned(self, tmp_path):
        """038: the per-run internal network is reaped by kind; its name
        matches the network pattern, never the container pattern."""
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        name = f"{'a' * 32}-net"
        self._write(tmp_path / "state", [{
            "run_id": "r", "name": name, "kind": "network",
            "pid": proc.pid, "process_start": None, "created_at": "now",
        }])
        runner = FakeRunner(ok(""), fail(1, "No such network"))
        reaped = reap_orphaned_containers(tmp_path / "state", runner)
        assert reaped == [name]
        assert runner.calls == [
            ["docker", "network", "rm", name],
            ["docker", "network", "inspect", name],
        ]
        assert self._read(tmp_path / "state") == []

    def test_malformed_name_skipped(self, tmp_path):
        entry = self._entry("not-an-engine-name", os.getpid(), None)
        self._write(tmp_path / "state", [entry])
        runner = FakeRunner()
        reap_orphaned_containers(tmp_path / "state", runner)
        assert runner.calls == []
        assert self._read(tmp_path / "state") == [entry]

    def test_missing_container_tolerated(self, tmp_path):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        name = f"{'e' * 32}-exec"
        self._write(tmp_path / "state", [self._entry(name, proc.pid)])
        runner = FakeRunner(fail(1, "No such container"), ok(""))
        reaped = reap_orphaned_containers(tmp_path / "state", runner)
        assert reaped == [name]
        assert runner.calls == [
            ["docker", "rm", "-f", name],
            ["docker", "ps", "-a", "--filter", f"name={name}",
             "--format", "{{.Names}}"],
        ]
        assert self._read(tmp_path / "state") == []

    def test_rm_failure_container_survives_retained(self, tmp_path):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        name = f"{'a' * 32}-exec"
        entry = self._entry(name, proc.pid)
        self._write(tmp_path / "state", [entry])
        runner = FakeRunner(fail(1, "daemon busy"), ok(name + "\n"))
        reaped = reap_orphaned_containers(tmp_path / "state", runner)
        assert reaped == []
        assert self._read(tmp_path / "state") == [entry]
        assert runner.calls == [
            ["docker", "rm", "-f", name],
            ["docker", "ps", "-a", "--filter", f"name={name}",
             "--format", "{{.Names}}"],
        ]

    def test_rm_failure_network_survives_retained(self, tmp_path):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        name = f"{'a' * 32}-net"
        entry = {"run_id": "r", "name": name, "kind": "network",
                 "pid": proc.pid, "process_start": None, "created_at": "now"}
        self._write(tmp_path / "state", [entry])
        runner = FakeRunner(fail(1, "daemon busy"), ok(name))
        reaped = reap_orphaned_containers(tmp_path / "state", runner)
        assert reaped == []
        assert self._read(tmp_path / "state") == [entry]
        assert runner.calls == [
            ["docker", "network", "rm", name],
            ["docker", "network", "inspect", name],
        ]

    def test_missing_network_tolerated(self, tmp_path):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        name = f"{'a' * 32}-net"
        entry = {"run_id": "r", "name": name, "kind": "network",
                 "pid": proc.pid, "process_start": None, "created_at": "now"}
        self._write(tmp_path / "state", [entry])
        runner = FakeRunner(fail(1, "No such network"),
                            fail(1, "No such network"))
        reaped = reap_orphaned_containers(tmp_path / "state", runner)
        assert reaped == [name]
        assert self._read(tmp_path / "state") == []
        assert runner.calls == [
            ["docker", "network", "rm", name],
            ["docker", "network", "inspect", name],
        ]

    def test_no_registry_is_a_noop(self, tmp_path):
        runner = FakeRunner()
        reap_orphaned_containers(tmp_path / "state", runner)
        assert runner.calls == []
        assert not (tmp_path / "state" / CONTAINER_REGISTRY).exists()

    def test_close_prunes_own_registry_entries(self, tmp_path):
        runner = FakeRunner(ok(""), ok(""))
        backend = _backend(tmp_path, runner)
        backend._container = "c1"
        backend._started = True
        own = {"run_id": backend._run_id, "name": f"{'f' * 32}-exec",
               "pid": os.getpid(), "process_start": None, "created_at": "now"}
        other = {"run_id": "other-run", "name": f"{'a' * 32}-exec",
                 "pid": os.getpid(), "process_start": None, "created_at": "now"}
        self._write(tmp_path / "state", [own, other])
        backend.close()
        assert self._read(tmp_path / "state") == [other]


def test_provision_payload_mounts_declared_caches():
    payload = docker_provision_payload(
        run_id="run1",
        image="img",
        export_root="/ws",
        setup_cmd="cd frontend-editor && npm ci",
        cache_mounts=[
            ("/host/fe/node_modules", "/ws/frontend-editor/node_modules", "rw"),
        ],
    )
    assert "-v" in payload
    assert "/host/fe/node_modules:/ws/frontend-editor/node_modules" in payload


class TestNetworkEgressEnforcement:
    """038: internal-network topology, containerized proxy payload, teardown,
    and enforcement-event merge into the audit log."""

    def test_proxy_payload_carries_host_access_hardening_and_bind(self, tmp_path):
        rewrites = {"host.docker.internal": "127.0.0.1"}
        payload = docker_proxy_payload(
            run_id="r1", image="img", name="r1-proxy-abc",
            policy_json=json.dumps({"class": "allowlist", "targets": []}),
            dial_rewrites_json=json.dumps(rewrites),
            events_host_path="/host/events.jsonl",
            egress_proxy_host_path="/host/egress_proxy.py",
            egress_policy_host_path="/host/egress_policy.py",
            bind_host="172.18.0.2")
        assert payload[:2] == ["docker", "run"]
        assert "--network" in payload and "bridge" in payload
        assert "--add-host" in payload
        assert payload[payload.index("--add-host") + 1] == "host.docker.internal:host-gateway"
        assert "--read-only" in payload
        assert payload[payload.index("--cap-drop") + 1] == "ALL"
        assert payload[payload.index("--user") + 1] == PROXY_USER
        assert payload[payload.index("--memory") + 1] == PROXY_MEMORY
        assert payload[payload.index("--cpus") + 1] == str(PROXY_CPUS)
        assert "/host/egress_proxy.py:/egress/egress_proxy.py:ro" in payload
        assert "/host/egress_policy.py:/egress/egress_policy.py:ro" in payload
        assert "/host/events.jsonl:/egress/events.jsonl" in payload
        assert "python3" in payload and "/egress/egress_proxy.py" in payload
        assert payload[payload.index("--host") + 1] == "172.18.0.2"
        assert payload[payload.index("--dial-rewrites") + 1] == json.dumps(rewrites)
        assert payload[payload.index("--bind-timeout") + 1] == str(PROXY_BIND_TIMEOUT)
        assert "--port" in payload and "3128" in payload

    def test_close_removes_network_and_proxy_and_merges_events(self, tmp_path):
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        runner = FakeRunner(
            ok("net"), ok("172.18.0.0/16 "), ok("proxy"), ok(""), ok(""),
            ok("cid"), ok("true"), ok(""))
        backend = _backend(tmp_path, runner)
        backend._export_root = str(export)
        ms = resolve_mount_set([MountSpec(path="src", mode="ro")])
        pol = EgressPolicy(targets=(TargetEntry(host="pypi.org"),))
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=True, mount_set=ms,
                                      egress_policy=pol)
        backend.run(req)
        prof = next(iter(backend._pool.values()))
        proxy_name = prof.proxy_name
        assert proxy_name is not None
        backend._egress_events_path.parent.mkdir(parents=True, exist_ok=True)
        backend._egress_events_path.write_text(
            json.dumps({"hostname": "pypi.org", "allowed": True,
                        "timestamp": "t"}) + "\n")
        backend.close()
        assert ["docker", "stop", "-t", "5", proxy_name] in runner.calls
        assert ["docker", "rm", "-f", proxy_name] in runner.calls
        assert ["docker", "network", "rm", backend._run_net] in runner.calls
        audit = (tmp_path / "state" / "sandbox-audit.jsonl").read_text().splitlines()
        merged = json.loads(audit[-1])
        assert merged["event"] == "egress_enforcement"
        assert merged["hostname"] == "pypi.org"
        assert not backend._egress_events_path.exists()



class TestProxyIpAllocation:
    """048: per-proxy internal IP allocation - distinct ``--ip`` per proxy,
    the first proxy keeps ``hosts[1]``, subnet exhaustion fails fast, and a
    failed attach surfaces the daemon stderr."""

    def test_two_proxies_get_distinct_ips(self, tmp_path):
        runner = FakeRunner(
            ok(""), ok("172.18.0.0/16 "), ok("proxy"), ok(""), ok(""),
            ok("172.18.0.0/16 "), ok("proxy"), ok(""), ok(""))
        backend = _backend(tmp_path, runner, network=True)
        backend._start_proxy_container("r-proxy-aaa", EgressPolicy())
        backend._start_proxy_container("r-proxy-bbb", EgressPolicy())
        connects = [c for c in backend._runner.calls if "--ip" in c]
        assert [c[c.index("--ip") + 1] for c in connects] == \
            ["172.18.0.2", "172.18.0.3"]
        runs = [c for c in backend._runner.calls if "--host" in c]
        assert [c[c.index("--host") + 1] for c in runs] == \
            ["172.18.0.2", "172.18.0.3"]

    def test_allocation_reused_for_same_name(self, tmp_path):
        backend = _backend(tmp_path, FakeRunner())
        first = backend._allocate_proxy_ip("172.18.0.0/16 ", "p1")
        again = backend._allocate_proxy_ip("172.18.0.0/16 ", "p1")
        assert first == "172.18.0.2"
        assert again == first

    def test_exhaustion_raises_naming_subnet_and_count(self, tmp_path):
        backend = _backend(tmp_path, FakeRunner())
        backend._allocate_proxy_ip("172.18.0.0/30 ", "p1")
        with pytest.raises(SandboxError,
                           match=r"1 usable proxy address.*2 egress proxies"):
            backend._allocate_proxy_ip("172.18.0.0/30 ", "p2")

    def test_in_use_addresses_are_skipped(self, tmp_path):
        """048 fix: addresses held by other containers on the network (the
        profile containers auto-join it) must be skipped, not re-pinned."""
        backend = _backend(tmp_path, FakeRunner())
        first = backend._allocate_proxy_ip("172.18.0.0/16 ", "p1")
        second = backend._allocate_proxy_ip(
            "172.18.0.0/16 ", "p2", in_use=["172.18.0.3/16"])
        assert first == "172.18.0.2"
        assert second == "172.18.0.4"

    def test_in_use_parsing_ignores_malformed_and_ipv6_tokens(self, tmp_path):
        backend = _backend(tmp_path, FakeRunner())
        backend._allocate_proxy_ip("172.18.0.0/16 ", "p1")
        second = backend._allocate_proxy_ip(
            "172.18.0.0/16 ", "p2",
            in_use=["172.18.0.2/16", "172.18.0.3/16", "fd00::2/64",
                    "garbage"])
        assert second == "172.18.0.4"

    def test_second_proxy_skips_container_held_ips(self, tmp_path):
        """048 fix: the inspect output reports in-use container addresses;
        the second proxy skips the first proxy's IP AND the auto-assigned
        profile-container IP (the CI AE-SBX-020 collision)."""
        runner = FakeRunner(
            ok(""), ok("172.18.0.0/16 |"), ok("proxy"), ok(""), ok(""),
            ok("172.18.0.0/16 |172.18.0.2/16 172.18.0.3/16 "),
            ok("proxy"), ok(""), ok(""))
        backend = _backend(tmp_path, runner, network=True)
        backend._start_proxy_container("r-proxy-aaa", EgressPolicy())
        backend._start_proxy_container("r-proxy-bbb", EgressPolicy())
        connects = [c for c in backend._runner.calls if "--ip" in c]
        assert [c[c.index("--ip") + 1] for c in connects] == \
            ["172.18.0.2", "172.18.0.4"]

    def test_connect_failure_message_carries_stderr_tail(self, tmp_path):
        runner = FakeRunner(
            ok(""), ok("172.18.0.0/16 "), ok("proxy"),
            fail(1, "Error response from daemon: Address already in use"))
        backend = _backend(tmp_path, runner, network=True)
        with pytest.raises(SandboxError,
                           match="Address already in use") as exc:
            backend._start_proxy_container("r-proxy-abc", EgressPolicy())
        assert "AE-SBX-020" in str(exc.value)


class TestRegisterBeforeCreate:
    """042 D1: every create site appends its registry record before the
    create command, so a failed create still leaves a stale entry the close()
    verification and startup reaper can later prune."""

    def _entries(self, state_dir):
        path = Path(state_dir) / CONTAINER_REGISTRY
        if not path.is_file():
            return []
        return [json.loads(line) for line in
                path.read_text().splitlines() if line.strip()]

    def test_legacy_exec_entry_exists_when_create_fails(self, tmp_path):
        runner = FakeRunner(fail(125, "boom"))
        backend = _backend(tmp_path, runner)
        with pytest.raises(SandboxError, match="AE-SBX-010"):
            backend.create_execution_container()
        assert any(e["name"] == backend._container
                   for e in self._entries(tmp_path / "state"))

    def test_legacy_network_entry_exists_when_create_fails(self, tmp_path):
        runner = FakeRunner(ok(""), ok("cid"), fail(125, "boom"))
        backend = _backend(tmp_path, runner, network=True)
        with pytest.raises(SandboxError, match="AE-SBX-013"):
            backend.create_execution_container()
        assert any(e["name"] == backend._network_container
                   for e in self._entries(tmp_path / "state"))

    def test_network_entry_exists_when_network_create_fails(self, tmp_path):
        runner = FakeRunner(fail(125, "boom"))
        backend = _backend(tmp_path, runner, network=True)
        with pytest.raises(SandboxError, match="AE-SBX-021"):
            backend._ensure_run_network()
        assert any(e.get("name") == backend._run_net
                   and e.get("kind") == "network"
                   for e in self._entries(tmp_path / "state"))

    def test_base_export_entry_exists_when_create_fails(self, tmp_path):
        runner = FakeRunner(fail(125, "boom"))
        backend = _backend(tmp_path, runner)
        backend._export_root = str(tmp_path / "export")
        with pytest.raises(SandboxError, match="AE-SBX-010"):
            backend._create_base_export_container()
        assert any(e["name"] == backend._container
                   for e in self._entries(tmp_path / "state"))

    def test_profile_entry_exists_when_create_fails(self, tmp_path):
        runner = FakeRunner(fail(125, "boom"))
        backend = _backend(tmp_path, runner)
        backend._export_root = str(tmp_path / "export")
        key = container_profile_key(MountSet(), EgressPolicy())
        with pytest.raises(SandboxError, match="AE-SBX-010"):
            backend._start_profile((key, False), MountSet(), EgressPolicy(),
                                   False)
        expected = f"{backend._run_id}-exec-{key[:16]}"
        assert any(e["name"] == expected
                   for e in self._entries(tmp_path / "state"))

    def test_proxy_entry_exists_when_create_fails(self, tmp_path):
        runner = FakeRunner(ok(""), ok("172.18.0.0/16 "), fail(125, "boom"))
        backend = _backend(tmp_path, runner, network=True)
        with pytest.raises(SandboxError, match="AE-SBX-020"):
            backend._start_proxy_container("r-proxy-abc", EgressPolicy())
        assert any(e["name"] == "r-proxy-abc"
                   for e in self._entries(tmp_path / "state"))


class TestCloseVerification:
    """042 D2: close() prunes an owned registry entry only when the daemon
    confirms the object is gone; a surviving object keeps its entry."""

    def _write(self, state_dir, entry):
        path = Path(state_dir) / CONTAINER_REGISTRY
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def _read(self, state_dir):
        path = Path(state_dir) / CONTAINER_REGISTRY
        if not path.is_file():
            return []
        return [json.loads(line) for line in
                path.read_text().splitlines() if line.strip()]

    def test_close_keeps_entry_when_rm_fails_and_container_survives(self, tmp_path):
        name = f"{'a' * 32}-exec"
        entry = {"run_id": "r", "name": name, "pid": os.getpid(),
                 "process_start": None, "created_at": "now"}
        self._write(tmp_path / "state", entry)
        runner = FakeRunner(ok(""), fail(1, "boom"), ok(name + "\n"))
        backend = _backend(tmp_path, runner)
        backend._run_id = "r"
        backend._container = name
        backend._started = True
        backend.close()
        assert self._read(tmp_path / "state") == [entry]

    def test_close_prunes_entry_after_verified_removal(self, tmp_path):
        name = f"{'b' * 32}-exec"
        entry = {"run_id": "r", "name": name, "pid": os.getpid(),
                 "process_start": None, "created_at": "now"}
        self._write(tmp_path / "state", entry)
        runner = FakeRunner(ok(""), ok(""), ok(""))
        backend = _backend(tmp_path, runner)
        backend._run_id = "r"
        backend._container = name
        backend._started = True
        backend.close()
        assert self._read(tmp_path / "state") == []

    def test_second_close_is_a_noop(self, tmp_path):
        name = f"{'c' * 32}-exec"
        entry = {"run_id": "r", "name": name, "pid": os.getpid(),
                 "process_start": None, "created_at": "now"}
        self._write(tmp_path / "state", entry)
        runner = FakeRunner(ok(""), ok(""), ok(""))
        backend = _backend(tmp_path, runner)
        backend._run_id = "r"
        backend._container = name
        backend._started = True
        backend.close()
        calls_after_first = len(runner.calls)
        backend.close()
        assert len(runner.calls) == calls_after_first


class TestResolveIgnoredPaths:
    """052: git check-ignore drives the ignored-path set. The pure plan
    functions receive it as input; the host resolves it once per
    materialisation and once per sync-back (``walk_root`` = the export, so a
    gitignored dir created mid-run is still caught)."""

    @staticmethod
    def _git(cwd, *argv):
        return subprocess.run(["git", *argv], cwd=str(cwd), check=True,
                              capture_output=True, text=True)

    def _init_repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t")
        self._git(repo, "config", "user.name", "t")
        return repo

    def test_reports_gitignored_paths(self, tmp_path):
        repo = self._init_repo(tmp_path)
        (repo / ".gitignore").write_text("__pycache__/\n.hypothesis/\n")
        (repo / "src" / "__pycache__").mkdir(parents=True)
        (repo / "src" / "__pycache__" / "mod.cpython-312.pyc").write_text("x")
        (repo / "src" / "mod.py").write_text("x")
        (repo / ".hypothesis").mkdir()
        (repo / ".hypothesis" / "x.json").write_text("x")
        ignored = resolve_ignored_paths(repo)
        assert "src/__pycache__/mod.cpython-312.pyc" in ignored
        assert ".hypothesis/x.json" in ignored
        assert "src/mod.py" not in ignored

    def test_walk_root_checks_export_not_repo(self, tmp_path):
        repo = self._init_repo(tmp_path)
        (repo / ".gitignore").write_text("__pycache__/\n")
        (repo / "tracked.py").write_text("x")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-qm", "init")
        walk = tmp_path / "export"
        (walk / "src" / "__pycache__").mkdir(parents=True)
        (walk / "src" / "__pycache__" / "mod.cpython-312.pyc").write_text("x")
        (walk / "src" / "mod.py").write_text("x")
        ignored = resolve_ignored_paths(repo, walk_root=walk)
        assert "src/__pycache__/mod.cpython-312.pyc" in ignored
        assert "src/mod.py" not in ignored

    def test_tracked_file_matching_pattern_not_ignored(self, tmp_path):
        repo = self._init_repo(tmp_path)
        (repo / ".gitignore").write_text("*.pyc\n")
        (repo / "mod.pyc").write_text("x")
        self._git(repo, "add", "-f", "mod.pyc")
        self._git(repo, "commit", "-qm", "init")
        ignored = resolve_ignored_paths(repo)
        assert "mod.pyc" not in ignored

    def test_non_repo_fails_loud(self, tmp_path):
        repo = tmp_path / "not-a-repo"
        repo.mkdir()
        (repo / "f.py").write_text("x")
        runner = FakeRunner(fail(128, "not a git repository"))
        with pytest.raises(SandboxError, match="git check-ignore failed"):
            resolve_ignored_paths(repo, runner=runner)

    def test_single_batched_git_subprocess(self, tmp_path):
        repo = self._init_repo(tmp_path)
        (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        (repo / "a.pyc").write_text("x")
        (repo / "b").mkdir()
        (repo / "b" / "c.pyc").write_text("x")
        (repo / "mod.py").write_text("x")

        class RecordingRunner:
            def __init__(self):
                self.calls = []

            def run(self, argv, *, timeout=None, check=False, input=None):
                self.calls.append((argv, input))
                return ok("a.pyc\0b/c.pyc\0")

        runner = RecordingRunner()
        resolve_ignored_paths(repo, runner=runner)
        assert len(runner.calls) == 1
        argv, payload = runner.calls[0]
        assert argv == ["git", "-C", str(repo), "check-ignore", "--stdin", "-z"]
        # one subprocess, all candidates batched over a single stdin payload
        assert "a.pyc" in payload and "mod.py" in payload



class TestRemovedApparatusGuards:
    """052: the stamp/probe/venv apparatus is gone from src/. These guards pin
    the removal so the engine cannot silently re-accumulate the special cases
    the design deleted. The only allowed survivors are the migration-rejection
    strings in ``core/config.py`` and the explanatory comments."""

    SRC = Path(__file__).resolve().parents[1] / "src"

    @classmethod
    def _py_files(cls):
        return [p for p in cls.SRC.rglob("*.py")
                if "__pycache__" not in p.parts]

    def test_stamp_functions_and_cli_flags_absent(self):
        absent = ("read_stamp", "write_stamp", "clean_stamps",
                  "setup_stamp_key", "--refresh", "--clean-stamps")
        offenders = []
        for py in self._py_files():
            text = py.read_text(encoding="utf-8")
            for token in absent:
                if token in text:
                    offenders.append(f"{py}: {token}")
        assert offenders == []

    def test_setup_probe_only_as_migration_message(self):
        offenders = []
        for py in self._py_files():
            for i, line in enumerate(
                    py.read_text(encoding="utf-8").splitlines(), 1):
                if "setup_probe" not in line:
                    continue
                if py.name == "config.py" and (
                        '"setup_probe" in data' in line or "removed" in line):
                    continue
                offenders.append(f"{py}:{i}: {line.strip()}")
        assert offenders == []

    def test_venv_only_in_comments_and_migration(self):
        offenders = []
        for py in self._py_files():
            for i, line in enumerate(
                    py.read_text(encoding="utf-8").splitlines(), 1):
                if "venv" not in line:
                    continue
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if ('"sandbox_venv_dir" in data' in line
                        or "sandbox_venv_dir was removed" in line):
                    continue
                offenders.append(f"{py}:{i}: {stripped}")
        assert offenders == []
