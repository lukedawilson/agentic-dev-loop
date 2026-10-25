"""Unit tests for the sandbox domain (steps 2-6): value objects, pure
functions, the Docker backend with an injected command runner (the seam - no
monkeypatching), and the host-side audit/stamp helpers against real temp dirs.

No Docker daemon is required: every backend test drives the injected
`CommandRunner`; the real daemon is exercised only by the marked
test_isolation_integration.py."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentic_dev_loop.core.config import IsolationConfig
from agentic_dev_loop.core.config import MountSpec
from agentic_dev_loop.engine.egress_policy import EgressPolicy, TargetEntry
from agentic_dev_loop.engine.mounts import MountSet, resolve_mount_set
from agentic_dev_loop.engine.isolation import (
    AUDIT_MAX_BYTES, CmdResult, DEFAULT_DEV_IMAGE, DEFAULT_DEV_IMAGE_REF,
    DockerBackend, EffectiveProfile, ProbeResult, SandboxError,
    SandboxExecutionRequest, SandboxExecutionResult, append_audit_record,
    clean_stamps, container_profile_key, docker_create_payload,
    docker_provision_payload, exec_container_name, is_git_mutation,
    mount_set_docker_flags, probe_docker, read_stamp, remove_sandbox_venv,
    resolve_effective_profile, resolve_image_ref, resolve_sandbox_env,
    resolve_sandbox_venv_dir, select_backend, setup_stamp_key,
    translate_mount_path, venv_probe_argv, venv_probe_command,
    venv_probe_state, write_stamp,
)


class FakeRunner:
    """Scripted CommandRunner - the unit-test seam for the docker CLI."""

    def __init__(self, *script):
        self.calls: list[list[str]] = []
        self.script = list(script)

    def run(self, argv, *, timeout=None, check=False):
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
    def test_valid_request(self):
        r = SandboxExecutionRequest(argv=["echo", "hi"], cwd="/ws",
                                    env={"PATH": "/bin"}, timeout=1.0)
        assert r.argv == ["echo", "hi"]

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
        assert p.memory == "4g" and p.cpus == 2.0

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
        assert translate_mount_path(translate_mount_path(p)) == translate_mount_path(p)

    def test_expands_user(self):
        out = translate_mount_path("~/ws")
        assert not out.startswith("~")

    def test_relative_absolute_resolution(self):
        assert Path(translate_mount_path("ws")).is_absolute()


class TestSetupStampKey:
    def test_stable(self):
        a = setup_stamp_key("npm i", {"package-lock.json": "h1"})
        b = setup_stamp_key("npm i", {"package-lock.json": "h1"})
        assert a == b

    def test_diverges_on_cmd(self):
        assert setup_stamp_key("a", {}) != setup_stamp_key("b", {})

    def test_diverges_on_lockfile(self):
        assert setup_stamp_key("a", {"x": "1"}) != setup_stamp_key("a", {"x": "2"})

    def test_lockfile_order_insensitive(self):
        a = setup_stamp_key("c", {"b": "2", "a": "1"})
        b = setup_stamp_key("c", {"a": "1", "b": "2"})
        assert a == b


class TestVenvProbe:
    def test_command_classifies_broken_symlink_first(self):
        cmd = venv_probe_command()
        # the dangling-symlink (host-darwin) case is caught before -e, which
        # would otherwise report "missing"
        assert "echo broken" in cmd and "echo missing" in cmd and "echo valid" in cmd
        assert "-L .venv-sandbox/bin/python" in cmd
        assert "import sys" in cmd

    def test_argv_assembly(self):
        argv = venv_probe_argv("c1", "/ws")
        assert argv[:5] == ["docker", "exec", "-w", "/ws", "c1"]
        assert argv[5:] == ["/bin/sh", "-c", venv_probe_command()]

    @pytest.mark.parametrize("stdout,exit_code,expected", [
        ("valid\n", 0, "valid"),
        ("missing\n", 0, "missing"),
        ("broken\n", 0, "broken"),
        ("", 0, "unknown"),
        ("garbage", 0, "unknown"),
        ("valid", 1, "unknown"),
    ])
    def test_state_classification(self, stdout, exit_code, expected):
        assert venv_probe_state(stdout, exit_code) == expected


class TestRemoveSandboxVenv:
    def test_removes_broken_symlink_dir(self, tmp_path):
        ws = tmp_path / "ws"
        venv = ws / ".venv-sandbox"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").symlink_to("/opt/homebrew/opt/python/bin/python3.14")
        remove_sandbox_venv(ws)
        assert not venv.exists()

    def test_removes_regular_dir(self, tmp_path):
        ws = tmp_path / "ws"
        venv = ws / ".venv-sandbox"
        (venv / "lib").mkdir(parents=True)
        (venv / "lib" / "x.txt").write_text("x")
        remove_sandbox_venv(ws)
        assert not venv.exists()

    def test_missing_is_noop(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        remove_sandbox_venv(ws)  # must not raise

    def test_fails_loud_when_removal_impossible(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        (ws / ".venv-sandbox").mkdir(parents=True)

        def boom(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.rmtree", boom)
        with pytest.raises(SandboxError, match="AE-SBX-014"):
            remove_sandbox_venv(ws)

    def test_relocated_clears_contents_keeps_dir(self, tmp_path):
        """022: a relocated venv is the bind-mount source for the still-running
        exec container, so only its CONTENTS are removed - deleting the dir
        would leave the container pointed at a stale inode."""
        venv = tmp_path / "outside-venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").symlink_to("/opt/homebrew/opt/python/bin/python3.14")
        (venv / "lib").mkdir(parents=True)
        (venv / "lib" / "x.txt").write_text("x")
        remove_sandbox_venv(tmp_path / "ws", sandbox_venv_dir=str(venv))
        assert venv.is_dir()
        assert not list(venv.iterdir())  # contents gone, mount source intact

    def test_relocated_missing_is_noop(self, tmp_path):
        remove_sandbox_venv(tmp_path / "ws",
                            sandbox_venv_dir=str(tmp_path / "nope"))


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
        p = docker_provision_payload(run_id="r1", image="img", workspace=ws,
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
        p = docker_provision_payload(run_id="r1", image="img", workspace=ws,
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
                                             workspace=ws, setup_cmd="make")
        assert "--user" not in create
        assert "--user" not in provision

    def test_networked_create_payload(self, tmp_path):
        """014: `network: true` swaps the network mode AND the container name,
        but nothing else - the networked container is otherwise identical
        (read-only, cap-dropped, single mount)."""
        ws = str(tmp_path)
        p = docker_create_payload(run_id="r1", image="img",
                                  profile=self._profile(), workspace=ws,
                                  network=True)
        assert "--network" in p and "bridge" in p
        assert "none" not in p
        assert "--name" in p and f"{p[p.index('--name') + 1]}" == "r1-exec-net"
        assert "--read-only" in p
        assert "--cap-drop" in p and "ALL" in p
        mounts = [p[i + 1] for i, a in enumerate(p) if a == "-v"]
        assert mounts == [f"{ws}:{ws}"]

    def test_create_payload_masks_host_only_venv(self, tmp_path):
        """022: `.venv` is host-only. It cannot be excluded from the workspace
        bind, so it is shadowed by a read-only empty tmpfs submount - any
        container-side write fails loudly instead of mutating the host venv."""
        ws = str(tmp_path)
        p = docker_create_payload(run_id="r1", image="img",
                                  profile=self._profile(), workspace=ws)
        mask = f"{ws}/.venv:ro"
        assert mask in p
        assert p[p.index(mask) - 1] == "--tmpfs"
        # the mask comes after the workspace bind (submount shadows the bind)
        assert p.index(mask) > p.index(f"{ws}:{ws}")

    def test_provision_payload_masks_host_only_venv(self, tmp_path):
        ws = str(tmp_path)
        p = docker_provision_payload(run_id="r1", image="img", workspace=ws,
                                     setup_cmd="make")
        mask = f"{ws}/.venv:ro"
        assert mask in p
        assert p[p.index(mask) - 1] == "--tmpfs"
        assert p.index(mask) > p.index(f"{ws}:{ws}")

    def test_relocated_venv_mount_present_in_both_payloads(self, tmp_path):
        """022: when ``sandbox_venv_dir`` is set, the outside dir is mounted rw
        at the unchanged container path ``{ws}/.venv-sandbox`` in BOTH the exec
        and provision payloads (the provision container runs the setup hook that
        creates the venv)."""
        ws = str(tmp_path)
        venv = str(tmp_path / "outside-venv")
        target = f"{venv}:{ws}/.venv-sandbox"
        create = docker_create_payload(run_id="r1", image="img",
                                       profile=self._profile(), workspace=ws,
                                       sandbox_venv_dir=venv)
        provision = docker_provision_payload(run_id="r1", image="img",
                                             workspace=ws, setup_cmd="make",
                                             sandbox_venv_dir=venv)
        for payload in (create, provision):
            assert target in payload
            assert payload[payload.index(target) - 1] == "-v"
            # the venv mount is a submount: it must come after the workspace bind
            assert payload.index(target) > payload.index(f"{ws}:{ws}")


    def test_create_payload_emits_seccomp_profile(self, tmp_path):
        """023: a configured seccomp profile is emitted as a security-opt so
        Docker replaces its default profile (which blocks the userns syscalls
        Chromium's namespace sandbox needs)."""
        ws = str(tmp_path)
        p = docker_create_payload(run_id="r1", image="img",
                                  profile=self._profile(), workspace=ws,
                                  seccomp_profile="/host/chromium-userns.json")
        assert "--security-opt" in p
        assert p[p.index("--security-opt") + 1] == "seccomp=/host/chromium-userns.json"
        # 023 gate 2: chromium's zygote chroots /proc/self/fdinfo at startup;
        # under --cap-drop ALL that dies without CAP_SYS_CHROOT. The profile
        # gates chroot(2) on exactly that capability, so the payload adds it.
        assert "--cap-add" in p
        assert p[p.index("--cap-add") + 1] == "SYS_CHROOT"

    def test_create_payload_omits_seccomp_by_default(self, tmp_path):
        """023: without a profile, Docker's own default seccomp stays in force
        (no --security-opt flag is emitted)."""
        ws = str(tmp_path)
        p = docker_create_payload(run_id="r1", image="img",
                                  profile=self._profile(), workspace=ws)
        assert "--security-opt" not in p
        assert "--cap-add" not in p

    def test_networked_create_payload_emits_seccomp_profile(self, tmp_path):
        """023: the networked exec container (used by render_check, which
        fetches live assets) gets the same profile as the network-none one."""
        ws = str(tmp_path)
        p = docker_create_payload(run_id="r1", image="img",
                                  profile=self._profile(), workspace=ws,
                                  network=True,
                                  seccomp_profile="/host/chromium-userns.json")
        assert "--security-opt" in p
        assert p[p.index("--security-opt") + 1] == "seccomp=/host/chromium-userns.json"
        assert "--cap-add" in p
        assert p[p.index("--cap-add") + 1] == "SYS_CHROOT"


class TestResolveSandboxVenvDir:
    def test_none_returns_none(self, tmp_path):
        assert resolve_sandbox_venv_dir(None, tmp_path) is None

    def test_expanduser_and_absolute(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        out = resolve_sandbox_venv_dir("~/venvs/engine", tmp_path / "ws")
        assert out == str(tmp_path / "venvs" / "engine")
        assert os.path.isabs(out)

    def test_home_wins_over_userprofile(self, tmp_path, monkeypatch):
        # The engine is driven from bash, where HOME is the authoritative home
        # on every platform - CPython's Windows expanduser ignores HOME in
        # favour of USERPROFILE, which would resolve `~` inconsistently.
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "prof"))
        out = resolve_sandbox_venv_dir("~/venvs/engine", tmp_path / "ws")
        assert out == str(tmp_path / "home" / "venvs" / "engine")

    def test_userprofile_fallback_when_home_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "prof"))
        out = resolve_sandbox_venv_dir("~/venvs/engine", tmp_path / "ws")
        assert out == str(tmp_path / "prof" / "venvs" / "engine")

    def test_outside_workspace_accepted(self, tmp_path):
        ws = tmp_path / "ws"
        out = resolve_sandbox_venv_dir(str(tmp_path / "venvs"), ws)
        assert out == str(tmp_path / "venvs")

    def test_in_workspace_fails_loud(self, tmp_path):
        ws = tmp_path / "ws"
        with pytest.raises(SandboxError, match="AE-SBX-015"):
            resolve_sandbox_venv_dir(str(ws / "nested" / "venv"), ws)

    def test_equals_workspace_fails_loud(self, tmp_path):
        ws = tmp_path / "ws"
        with pytest.raises(SandboxError, match="AE-SBX-015"):
            resolve_sandbox_venv_dir(str(ws), ws)


class TestExecContainerName:
    def test_default_is_network_none(self):
        assert exec_container_name("abc") == "abc-exec"

    def test_networked_suffix(self):
        assert exec_container_name("abc", network=True) == "abc-exec-net"


class TestSelectBackend:
    def test_totality(self):
        assert select_backend("darwin", ProbeResult("1.43", "linux")) is DockerBackend
        assert select_backend("win32", ProbeResult("1.43", "linux")) is DockerBackend
        assert select_backend("linux", ProbeResult("1.43", "linux")) is DockerBackend


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
              network=False, sandbox_venv_dir=None, seccomp_profile=None):
    return DockerBackend(
        image=image, workspace=str(tmp_path / "ws"),
        iso_state_dir=str(tmp_path / "state"),
        profile=EffectiveProfile(True, True, True, True, git_blocked,
                                 image, "4g", 2.0),
        runner=runner, network=network,
        sandbox_venv_dir=sandbox_venv_dir,
        seccomp_profile=seccomp_profile)


class TestRelocatedVenvDirCreation:
    """022: the relocated venv dir is the bind-mount source. It must be
    pre-created host-side so Docker never auto-creates a missing source as
    root on native Linux (the --user uid:gid EACCES class, AE-SBX-009)."""

    def test_missing_dir_created_on_construction(self, tmp_path):
        venv_dir = tmp_path / "outside-venv"
        assert not venv_dir.exists()
        _backend(tmp_path, FakeRunner(), sandbox_venv_dir=str(venv_dir))
        assert venv_dir.is_dir()

    def test_nested_missing_dir_created(self, tmp_path):
        venv_dir = tmp_path / "a" / "b" / "venv"
        _backend(tmp_path, FakeRunner(), sandbox_venv_dir=str(venv_dir))
        assert venv_dir.is_dir()

    def test_unset_creates_nothing(self, tmp_path):
        _backend(tmp_path, FakeRunner())
        assert not (tmp_path / ".venv-sandbox").exists()


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
        """014: when any step declares network, startup creates BOTH the
        network-none container and a bridge-network container - zero infra
        work is deferred to the run phase."""
        runner = FakeRunner(ok("cid-none"), ok("cid-net"))
        backend = _backend(tmp_path, runner, network=True)
        backend.create_execution_container()
        assert len(runner.calls) == 2
        none_argv, net_argv = runner.calls
        assert "--network" in none_argv and "none" in none_argv
        assert none_argv[none_argv.index("--name") + 1].endswith("-exec")
        assert "--network" in net_argv and "bridge" in net_argv
        assert net_argv[net_argv.index("--name") + 1].endswith("-exec-net")

    def test_networked_container_failure_is_loud_and_reclaims_main(self, tmp_path):
        runner = FakeRunner(ok("cid-none"), fail(125, "network bridge unavailable"))
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
# Provision + stamps + audit (real temp dirs)
# ---------------------------------------------------------------------------


class TestProvisionStampsAudit:
    def test_provision_writes_stamp(self, tmp_path):
        runner = FakeRunner(ok(""))
        backend = _backend(tmp_path, runner)
        stamp_dir = tmp_path / "stamps"
        backend.provision("make build", "key1", str(stamp_dir), force=False)
        assert read_stamp(stamp_dir, "key1")
        assert runner.calls

    def test_provision_skips_on_stamp(self, tmp_path):
        """A warm stamp still skips, but only after the in-container venv
        probe reports ``valid`` (016 self-heal) - no blind skip."""
        runner = FakeRunner(ok("valid"))
        backend = _backend(tmp_path, runner)
        stamp_dir = tmp_path / "stamps"
        write_stamp(stamp_dir, "key1")
        backend.provision("make build", "key1", str(stamp_dir), force=False)
        # the probe ran (docker exec); no provision container ran
        assert [c for c in runner.calls if c[:2] == ["docker", "exec"]]
        assert not [c for c in runner.calls if c[:2] == ["docker", "run"]]

    def test_provision_self_heals_broken_venv_on_stamp(self, tmp_path):
        """016 repro (unit): stamp hit + broken venv → probe reports broken →
        the venv is removed and the setup re-runs instead of blind-skipping."""
        runner = FakeRunner(ok("broken"), ok(""))
        backend = _backend(tmp_path, runner)
        stamp_dir = tmp_path / "stamps"
        write_stamp(stamp_dir, "key1")
        ws = tmp_path / "ws"
        venv_bin = ws / ".venv-sandbox" / "bin"
        venv_bin.mkdir(parents=True)
        # host-darwin venv: a python symlink whose target doesn't exist in the
        # Linux container.
        (venv_bin / "python").symlink_to("/opt/homebrew/opt/python/bin/python3.14")
        backend.provision("python -m venv .venv-sandbox", "key1",
                          str(stamp_dir), force=False)
        # broken venv removed, then provision re-ran
        assert not (ws / ".venv-sandbox").exists()
        assert [c for c in runner.calls if c[:2] == ["docker", "run"]]
        assert read_stamp(stamp_dir, "key1")

    def test_provision_refresh_forces(self, tmp_path):
        runner = FakeRunner(ok(""))
        backend = _backend(tmp_path, runner)
        stamp_dir = tmp_path / "stamps"
        write_stamp(stamp_dir, "key1")
        backend.provision("make build", "key1", str(stamp_dir), force=True)
        assert runner.calls

    def test_provision_failure_no_stamp(self, tmp_path):
        runner = FakeRunner(CmdResult(2, "make: *** Error", ""))
        backend = _backend(tmp_path, runner)
        stamp_dir = tmp_path / "stamps"
        with pytest.raises(SandboxError, match="AE-SBX-009"):
            backend.provision("make", "key1", str(stamp_dir), force=False)
        assert not read_stamp(stamp_dir, "key1")

    def test_clean_stamps(self, tmp_path):
        state = tmp_path / "state"
        for k in ("a", "b", "c"):
            write_stamp(state / "stamps", k)
        assert clean_stamps(state) == 3
        assert clean_stamps(state) == 0

    def test_audit_rotation(self, tmp_path, monkeypatch):
        state = tmp_path / "state"
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.AUDIT_MAX_BYTES", 10)
        append_audit_record(state, {"n": 1, "pad": "x" * 8})
        append_audit_record(state, {"n": 2, "pad": "y" * 8})
        assert (state / "sandbox-audit.jsonl").exists()
        assert (state / "sandbox-audit.jsonl.1").exists()
        # the rolled file holds the earlier record, the fresh one the later
        rolled = (state / "sandbox-audit.jsonl.1").read_text()
        assert "x" * 8 in rolled


class TestStartupSandbox:
    """Eager startup sequence (Q2): probe → image → create/start → provision
    all declared setups - with zero infra operations left for the run phase."""

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

    def test_sequence_and_provision(self, tmp_path, monkeypatch):
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
        sandbox = startup_sandbox(cfg, workspace=ws, runner=runner)
        try:
            kinds = [c[1] for c in runner.calls]
            assert kinds == ["version", "info", "image", "run", "run"]
            # the provision payload carries the setup command
            run_argv = runner.calls[-1]
            assert "python -m venv .venv-sandbox" in run_argv
            # stamp written host-side
            stamps = tmp_path / "iso-state" / "stamps"
            assert list(stamps.iterdir())
        finally:
            sandbox.close()

    def test_network_declaration_starts_second_container(self, tmp_path, monkeypatch):
        """014: declaring `network: true` on any step makes eager startup
        create the bridge-network container in addition to the default
        network-none one - the default (no declaration) stays single-container."""
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
            ok(""),               # docker run -d --rm (network-none exec)
            ok(""),               # docker run -d --rm (networked exec)
        )
        sandbox = startup_sandbox(cfg, workspace=ws, runner=runner)
        try:
            run_argv = [c for c in runner.calls if c[1] == "run"]
            assert len(run_argv) == 2
            assert "--network" in run_argv[0] and "none" in run_argv[0]
            assert "--network" in run_argv[1] and "bridge" in run_argv[1]
        finally:
            sandbox.close()

    def test_refresh_forces_provision(self, tmp_path, monkeypatch):
        from agentic_dev_loop.engine.isolation import (setup_stamp_key, startup_sandbox,
                                                write_stamp)

        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = self._cfg(tmp_path, with_setup=True)
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        monkeypatch.setenv("AGENTIC_DEV_LOOP_DEV_IMAGE", "img:tag")

        # Pre-write the stamp as if a prior run already provisioned.
        stamps = tmp_path / "iso-state" / "stamps"
        write_stamp(stamps, setup_stamp_key("python -m venv .venv-sandbox", {}))

        # Without refresh the stamp is a hit: no provision container runs.
        runner = FakeRunner(ok("1.54"), ok("linux"), ok(""), ok(""))
        sandbox = startup_sandbox(cfg, workspace=ws, runner=runner)
        sandbox.close()
        assert not [c for c in runner.calls if "python -m venv .venv-sandbox" in c]

        # With refresh the stamp is ignored: provision re-runs.
        runner2 = FakeRunner(ok("1.54"), ok("linux"), ok(""), ok(""), ok(""))
        sandbox2 = startup_sandbox(cfg, workspace=ws, runner=runner2, refresh=True)
        sandbox2.close()
        assert [c for c in runner2.calls if "python -m venv .venv-sandbox" in c]

    def test_relocated_venv_dir_flows_into_payloads(self, tmp_path, monkeypatch):
        """022: a set `sandbox_venv_dir` is resolved and threaded into BOTH the
        exec and provision payloads; the workspace stays free of `.venv-sandbox`
        noise (the outside dir is mounted at the unchanged container path)."""
        from agentic_dev_loop.engine.isolation import startup_sandbox

        ws = tmp_path / "ws"
        ws.mkdir()
        venv_dir = tmp_path / "outside-venv"
        cfg = self._cfg(tmp_path, with_setup=True)
        cfg.isolation.sandbox_venv_dir = str(venv_dir)
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        monkeypatch.setenv("AGENTIC_DEV_LOOP_DEV_IMAGE", "img:tag")
        runner = FakeRunner(
            ok("1.54"),           # probe version
            ok("linux"),          # probe ostype
            ok(""),               # image inspect
            ok(""),               # docker run -d --rm (exec container)
            ok(""),               # docker run provision
        )
        sandbox = startup_sandbox(cfg, workspace=ws, runner=runner)
        try:
            target = f"{venv_dir}:{ws}/.venv-sandbox"
            for argv in runner.calls:
                if argv[:2] == ["docker", "run"]:
                    assert target in argv
                    assert argv[argv.index(target) - 1] == "-v"
                    assert f"{ws}/.venv:ro" in argv
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
        sandbox = startup_sandbox(cfg, workspace=ws, runner=runner)
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
            startup_sandbox(cfg, workspace=ws, runner=runner)
        assert runner.calls == []

    def test_in_workspace_sandbox_venv_dir_fails_loud(self, tmp_path, monkeypatch):
        from agentic_dev_loop.engine.isolation import startup_sandbox

        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = self._cfg(tmp_path, with_setup=False)
        cfg.isolation.sandbox_venv_dir = str(ws / "nested")
        monkeypatch.setattr("agentic_dev_loop.engine.isolation.shutil.which",
                            lambda *a: "/usr/bin/docker")
        monkeypatch.setenv("AGENTIC_DEV_LOOP_DEV_IMAGE", "img:tag")
        runner = FakeRunner(ok("1.54"), ok("linux"))
        with pytest.raises(SandboxError, match="AE-SBX-015"):
            startup_sandbox(cfg, workspace=ws, runner=runner)


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

    def test_networked_payload_adds_host_gateway(self, tmp_path):
        ws = str(tmp_path)
        p = docker_create_payload(run_id="r1", image="img",
                                  profile=self._profile(), workspace=ws,
                                  network=True)
        assert "--add-host" in p
        assert p[p.index("--add-host") + 1] == "host.docker.internal:host-gateway"

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
    def test_stable_and_ordered(self):
        ms = resolve_mount_set([MountSpec(path="src", mode="ro")])
        p = EgressPolicy(targets=(TargetEntry(host="a.com"),))
        assert container_profile_key(ms, p) == container_profile_key(ms, p)

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
        """D-1 + BR-5: a step's mount set + egress policy key a pooled
        container; the proxy env points at the host-gateway (never
        127.0.0.1), and the exec cwd is the export root."""
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        (export / "src" / "a.py").write_text("x")
        runner = FakeRunner(ok("cid"), ok(""))
        backend = _backend(tmp_path, runner)
        backend._export_root = str(export)
        ms = resolve_mount_set([MountSpec(path="src", mode="ro")])
        pol = EgressPolicy(targets=(TargetEntry(host="pypi.org"),))
        req = SandboxExecutionRequest(argv=["echo", "hi"], cwd=str(export),
                                      env={}, network=True, mount_set=ms,
                                      egress_policy=pol)
        try:
            backend.run(req)
            run_argv = runner.calls[0]
            assert run_argv[:2] == ["docker", "run"]
            assert f"{export}:{export}:ro" in run_argv
            assert f"{export}/src:{export}/src" not in run_argv
            proxy_envs = [a for a in run_argv
                          if a.startswith("HTTP_PROXY=")
                          or a.startswith("HTTPS_PROXY=")]
            assert proxy_envs and all("host.docker.internal" in a
                                      for a in proxy_envs)
            assert run_argv[run_argv.index("--workdir") + 1] == str(export)
            exec_argv = runner.calls[1]
            assert exec_argv[:2] == ["docker", "exec"]
            assert exec_argv[exec_argv.index("-w") + 1] == str(export)
            # the profile container is pooled: a second run reuses it
            backend.run(req)
            assert len([c for c in runner.calls if c[:2] == ["docker", "run"]]) == 1
        finally:
            backend.close()

    def test_proxy_death_fails_closed(self, tmp_path):
        """BR-11.3: a networked profile whose egress proxy has died fails the
        request loudly - the enforcement point being down is never a silent
        degradation."""
        export = tmp_path / "export"
        (export / "src").mkdir(parents=True)
        runner = FakeRunner(ok("cid"), ok(""))
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
            assert prof.proxy is not None
            prof.proxy.stop()
            with pytest.raises(SandboxError, match="proxy"):
                backend.run(req)
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
