"""Sandbox execution domain (unit-3).

The two agent-influence channels - the ``terminal`` tool and command steps -
run inside a Docker-based, kernel-level sandbox that is always on (FD-4).
``Sandbox`` is the retained extension seam; ``DockerBackend`` is the single
concrete implementation (user directive 2026-10-19). Everything else here is
either pure translation logic (the PBT-03 targets) or host-side audit/stamp
helpers.

Host-only vs sandboxed: the sandbox mount set is {workspace} plus the
host-only mask mounts (022): a read-only empty tmpfs at ``{ws}/.venv`` so the
host's throwaway venv can never be written from inside the sandbox. When
``isolation.sandbox_venv_dir`` relocates the sandbox venv, one extra rw bind
mount at ``{ws}/.venv-sandbox`` is added (opt-in). The isolation state dir
(stamps + audit JSONL) and the pipeline run-state dir (checkpoint DB +
last-error.md) are never mounted - sandboxed code cannot reach them.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol

from agentic_dev_loop.core.config import IsolationConfig, PipelineConfig
from .proc import needs_shell, split_command
from .seccomp import resolve_seccomp_profile

# ---------------------------------------------------------------------------
# Constants (KD-2 / ID-4)
# ---------------------------------------------------------------------------

DEFAULT_DEV_IMAGE = "ghcr.io/lukedawilson/agentic-dev-loop/dev"
# Digest-pinned; filled after first publish (KD-2). An empty ref fails fast at
# runtime unless AGENTIC_DEV_LOOP_DEV_IMAGE overrides it - never a silent
# `latest` fallback.
DEFAULT_DEV_IMAGE_REF = "ghcr.io/lukedawilson/agentic-dev-loop/dev@sha256:890105209c127446caf20ce3dc05f0c0f5527e9d679e18cc0075e6b0ee917434"
IMAGE_ENV_OVERRIDE = "AGENTIC_DEV_LOOP_DEV_IMAGE"

DEFAULT_EXEC_MEMORY = "4g"
DEFAULT_EXEC_CPUS = 2.0
DEFAULT_PROVISION_MEMORY = "8g"
DEFAULT_PROVISION_CPUS = 4.0

ENGINE_API_FLOOR = "1.43"

SANDBOX_ENV_BASE = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TERM": "xterm",
}

TIMEOUT_EXIT_CODE = 124  # timeout(1) convention inside the container
AUDIT_MAX_BYTES = 5 * 1024 * 1024
# Provision is a trusted install/build - give it a generous hard bound so a
# hung `setup:` hook can never wedge startup (BR-8 fail fast, not a hang).
PROVISION_TIMEOUT_SECONDS = 3600

# The sandbox venv the shipped pipeline relies on (016 sandbox-venv
# hardening). The engine probes this path INSIDE the exec container
# before trusting a warm provision stamp - a host-built (darwin) venv
# symlinks to a host interpreter that does not exist in the Linux
# container, so it must be detected and re-provisioned there.
SANDBOX_VENV_DIR = ".venv-sandbox"
SANDBOX_VENV_PYTHON = ".venv-sandbox/bin/python"
# Host-only dirs masked inside the sandbox (022): each gets a read-only empty
# tmpfs submount at ``{ws}/<dir>`` so agents fail loudly on any write instead
# of silently mutating a host-owned throwaway venv.
HOST_ONLY_DIRS = (".venv",)


class SandboxError(RuntimeError):
    """A loud fail-fast sandbox failure; the message names the problem and
    points at docs/setup.md."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SandboxExecutionRequest:
    """One command to execute inside the boundary (SECURITY-05 invariants)."""

    argv: list[str]  # full command, argv[0] = program
    cwd: str  # absolute container-side path (== host mount path)
    env: dict[str, str]  # resolved sandbox env (already scrubbed)
    timeout: float | None = None  # seconds; None = no limit
    network: bool = False  # route to the bridge-network exec container when True
    # Host-side audit metadata (BR-11) - never sent into the container.
    pipeline: str | None = None
    step: str | None = None

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("SandboxExecutionRequest.argv must be non-empty")
        # Coerce Path-like cwd to str so the invariant "cwd is always a str"
        # holds at the boundary (audit digest etc. serialize it).
        object.__setattr__(self, "cwd", os.fspath(self.cwd))
        if not (Path(self.cwd).is_absolute()
                or PurePosixPath(self.cwd).is_absolute()):
            raise ValueError(f"SandboxExecutionRequest.cwd must be absolute: {self.cwd!r}")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError(
                f"SandboxExecutionRequest.timeout must be > 0, got {self.timeout!r}")


@dataclass(frozen=True)
class SandboxExecutionResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    backend: str  # "docker"
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0

    @property
    def is_timeout(self) -> bool:
        return self.timed_out


@dataclass(frozen=True)
class EffectiveProfile:
    """The resolved isolation profile (BR-5): opinionated defaults merged with
    author overrides. Pure data - no environment access."""

    network_off: bool
    read_only_rootfs: bool
    writes_only_on_mounts: bool
    drop_all_capabilities: bool
    git_mutations_blocked: bool
    image: str
    memory: str
    cpus: float


@dataclass(frozen=True)
class ProbeResult:
    api_version: str
    ostype: str


# ---------------------------------------------------------------------------
# Pure functions (no I/O, no env access - PBT-03 targets)
# ---------------------------------------------------------------------------


def resolve_effective_profile(isolation: IsolationConfig | None) -> EffectiveProfile:
    """BR-5 defaults: network off, rootfs ro, writes only on mounts, all caps
    dropped, git mutations blocked unless the profile re-allows them. The
    image and resource caps come from the author's overrides or the harness
    defaults."""
    if isolation is None:
        image, memory, cpus = DEFAULT_DEV_IMAGE_REF, DEFAULT_EXEC_MEMORY, DEFAULT_EXEC_CPUS
        allow_git = False
    else:
        image = isolation.image or DEFAULT_DEV_IMAGE_REF
        memory = isolation.memory or DEFAULT_EXEC_MEMORY
        cpus = isolation.cpus if isolation.cpus is not None else DEFAULT_EXEC_CPUS
        allow_git = isolation.allow_git_mutations
    return EffectiveProfile(
        network_off=True,
        read_only_rootfs=True,
        writes_only_on_mounts=True,
        drop_all_capabilities=True,
        git_mutations_blocked=not allow_git,
        image=image,
        memory=memory,
        cpus=cpus,
    )


def resolve_sandbox_env(step_env: dict[str, str | None] | None, *,
                        host_env: Mapping[str, str] | None = None,
                        secrets: Mapping[str, str] | None = None) -> dict[str, str]:
    """Fixed safe base (PATH/HOME/LANG/LC_ALL/TERM) + the step's declared
    ``env:`` mapping only. A declared value of ``None`` inherits the host
    value; the global ``env_allowlist`` is never consulted and the LLM key is
    never auto-added (NFR-3.1 / BR-9).

    ``secrets`` (019) carries the per-step resolved secret env - injected last,
    so a declared ref is authoritative over any same-named ``env:`` entry. It
    arrives only from ``step_secret_env``, which maps only the refs the step
    declared, so no secret ever reaches an undeclaring step."""
    source = os.environ if host_env is None else host_env
    env = dict(SANDBOX_ENV_BASE)
    for name, value in (step_env or {}).items():
        if value is None:
            if name in source:
                env[name] = source[name]
        else:
            env[name] = value
    for name, value in (secrets or {}).items():
        env[name] = value
    return env


_KNOWN_READ_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "ls-files", "rev-parse", "blame",
    "describe", "for-each-ref", "--help", "help",
})
_BRANCH_TAG_MUTATING = frozenset({
    "create", "delete", "-d", "-D", "-m", "-M", "--delete", "--move",
    "-c", "-C", "--copy", "-u", "--set-upstream-to",
})


def _branch_tag_mutation(rest: list[str]) -> bool:
    """A branch/tag invocation is read-only only when it lists (no positional
    name, no mutating flag). A bare name or a mutating flag → mutation."""
    for arg in rest:
        if arg in _BRANCH_TAG_MUTATING:
            return True
        if not arg.startswith("-"):
            return True
    return False


def is_git_mutation(argv: list[str]) -> bool:
    """Deny-by-default git classifier (BR-6). Only classifies git invocations
    (argv[0] basename in {"git", "git.exe"}); non-git programs return False.
    Known-read subcommands are always allowed; everything else - including
    unknown subcommands - is a mutation. Policy convenience only, NOT the
    security boundary (the container is)."""
    if not argv:
        return False
    if Path(argv[0]).name not in ("git", "git.exe"):
        return False
    args = argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-C" or a == "-c":
            i += 2
            continue
        if a.startswith("--git-dir=") or a.startswith("--work-tree="):
            i += 1
            continue
        if a in ("--no-pager", "--paginate"):
            i += 1
            continue
        if a == "--version":
            return False
        break
    if i >= len(args):
        return False  # bare `git` - no subcommand; not a mutation
    subcommand = args[i]
    rest = args[i + 1:]
    if subcommand in _KNOWN_READ_SUBCOMMANDS:
        return False
    if subcommand in ("branch", "tag"):
        return _branch_tag_mutation(rest)
    if subcommand == "remote":
        return not (not rest or rest[0] in ("-v", "--verbose", "show"))
    return True


def _expand_home(path: str) -> str:
    """Expand a leading ``~`` against ``$HOME``, falling back to
    ``USERPROFILE`` (Windows).

    The engine is driven from bash on every platform (Windows via Git Bash),
    where HOME is the authoritative home. CPython's Windows expanduser ignores
    HOME in favour of USERPROFILE, so a literal ``~`` in a config file would
    resolve inconsistently on Windows. Only a leading tilde (``~``, ``~/``,
    ``~\\``) is handled here; ``~user`` forms and the no-HOME/no-USERPROFILE
    case fall through to the stdlib below.
    """
    if path != "~" and not path.startswith(("~/", "~\\")):
        return path
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if home:
        return home + path[1:]
    return path


def translate_mount_path(path: str) -> str:
    """expanduser + absolute resolution without symlink chasing. Identity on
    macOS/Windows/WSL2-with-Docker-Desktop (verbatim passthrough - Docker
    Desktop translates). Idempotent: translate(translate(p)) == translate(p).
    A leading ``~`` resolves against $HOME first (USERPROFILE fallback), then
    the stdlib handles anything left (pwd on POSIX)."""
    p = Path(_expand_home(path)).expanduser()
    if not p.is_absolute():
        p = p.absolute()
    return str(p)


def resolve_sandbox_venv_dir(sandbox_venv_dir: str | None,
                             workspace: str | Path) -> str | None:
    """Resolve the opt-in relocated sandbox venv (022).

    ``None`` keeps the legacy workspace-local ``.venv-sandbox`` (bit-compatible
    default - payloads are unchanged). A set value is expanduser + absolute and
    must resolve OUTSIDE the workspace: an in-workspace value defeats the whole
    relocation and fails loud (AE-SBX-015).
    """
    if sandbox_venv_dir is None:
        return None
    resolved = Path(translate_mount_path(sandbox_venv_dir))
    ws = Path(translate_mount_path(os.fspath(workspace)))
    if resolved == ws or ws in resolved.parents:
        raise SandboxError(
            f"AE-SBX-015: isolation.sandbox_venv_dir {resolved!r} must resolve "
            "outside the workspace - an in-workspace value defeats the 022 "
            "relocation (docs/setup.md §3)")
    return str(resolved)


def setup_stamp_key(setup_cmd: str, lockfile_hashes: dict[str, str]) -> str:
    """sha256 hex of canonical JSON {setup_cmd, lockfiles: sorted hashes}."""
    canonical = {
        "setup_cmd": setup_cmd,
        "lockfiles": {k: lockfile_hashes[k] for k in sorted(lockfile_hashes)},
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


def venv_probe_command() -> str:
    """In-container probe classifying the sandbox venv (016 self-heal).

    Returns a shell command that prints exactly one of ``valid`` /
    ``missing`` / ``broken`` and exits 0. ``broken`` is the host-tampered
    class: the python entry exists as a path (including a dangling symlink -
    the host-darwin venv case) but is not executable/importable in the
    container. ``missing`` means no such entry at all (the setup hook may
    legitimately not create a venv), so it is treated as a stamp hit, never a
    self-heal trigger.
    """
    py = shlex.quote(SANDBOX_VENV_PYTHON)
    return (
        f"if [ -L {py} ] && [ ! -x {py} ]; then echo broken; "
        f"elif [ ! -e {py} ]; then echo missing; "
        f"elif {py} -c 'import sys' >/dev/null 2>&1; then echo valid; "
        f"else echo broken; fi"
    )


def venv_probe_argv(container: str, workspace: str) -> list[str]:
    """``docker exec`` argv that runs the venv probe inside the (already
    running) execution container against the bind-mounted workspace."""
    return ["docker", "exec", "-w", workspace, container,
            "/bin/sh", "-c", venv_probe_command()]


def venv_probe_state(stdout: str, exit_code: int | None) -> str:
    """Classify a probe result. A nonzero exit (docker exec failed) is
    ``unknown`` - the caller leaves the stamp alone rather than guessing."""
    if exit_code != 0:
        return "unknown"
    line = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    return line if line in ("valid", "missing", "broken") else "unknown"


def _clear_dir_contents(path: Path) -> None:
    """Delete every entry under ``path``, keeping ``path`` itself. The 022
    relocated venv is the bind-mount source for the still-running execution
    container, so the source dir must survive removal (deleting it would leave
    the container pointed at a stale inode)."""
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)


def remove_sandbox_venv(workspace: str | Path, *,
                        sandbox_venv_dir: str | None = None) -> None:
    """Remove the sandbox venv before re-provision (016 self-heal). Fails loud
    when the venv exists but cannot be removed - a silent skip would re-create
    the ENOENT failure the next run.

    With the 022 relocation opt-in (``sandbox_venv_dir``), the venv lives at
    that host path and is the source of a live bind mount in the execution
    container - so only its CONTENTS are removed (the mount source dir stays),
    leaving the container's ``{ws}/.venv-sandbox`` mount valid for re-provision.
    The default (unset) path removes the whole workspace-local dir as before.
    """
    if sandbox_venv_dir is not None:
        venv = Path(sandbox_venv_dir)
        try:
            if venv.is_symlink() or venv.is_file():
                venv.unlink()
            elif venv.is_dir():
                _clear_dir_contents(venv)
        except OSError as e:
            raise SandboxError(
                f"AE-SBX-014: cannot remove invalid sandbox venv at {venv}: {e} "
                f"- remove it manually and re-run (docs/setup.md §5)") from e
        return
    venv = Path(workspace) / SANDBOX_VENV_DIR
    try:
        if venv.is_symlink() or venv.is_file():
            venv.unlink()
        elif venv.is_dir():
            shutil.rmtree(venv)
    except OSError as e:
        raise SandboxError(
            f"AE-SBX-014: cannot remove invalid sandbox venv at {venv}: {e} "
            f"- remove it manually and re-run (docs/setup.md §5)") from e


def host_user_flag() -> str | None:
    """The ``docker --user`` value binding the sandbox to the host user.

    Container root with all capabilities dropped cannot write a host-owned
    bind mount on native Linux - the CI-red AE-SBX-009/writable-probe
    failures (Docker Desktop's virtiofs masks it on mac/Windows hosts).
    Running as ``uid:gid`` fixes ownership end-to-end. On Windows hosts
    (no os.getuid) the flag is omitted entirely.
    """
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        return f"{os.getuid()}:{os.getgid()}"
    return None


def exec_container_name(run_id: str, network: bool = False) -> str:
    """The execution container name for a network mode. Networked requests
    land in a second, bridge-network container; the default is the always-on
    network-none container (014 per-stage networking)."""
    return f"{run_id}-exec-net" if network else f"{run_id}-exec"


def _sandbox_mounts(workspace: str, sandbox_venv_dir: str | None) -> list[str]:
    """The mount segment shared by the exec and provision payloads (022).

    ``{workspace}`` rw is always first; each host-only dir is then shadowed by
    a read-only empty tmpfs submount (Docker cannot exclude a subdirectory of a
    bind - a submount is the only way to hide it, and ``ro`` makes any write
    attempt fail loudly instead of silently succeeding against a decoy). When
    ``sandbox_venv_dir`` relocates the sandbox venv, one rw bind mount maps it
    to the unchanged container path ``{ws}/.venv-sandbox``.
    """
    mounts = ["-v", f"{workspace}:{workspace}"]
    for host_only in HOST_ONLY_DIRS:
        mounts += ["--tmpfs", f"{workspace}/{host_only}:ro"]
    if sandbox_venv_dir is not None:
        mounts += ["-v", f"{sandbox_venv_dir}:{workspace}/{SANDBOX_VENV_DIR}"]
    return mounts


def docker_create_payload(*, run_id: str, image: str, profile: EffectiveProfile,
                          workspace: str, network: bool = False,
                          sandbox_venv_dir: str | None = None,
                          seccomp_profile: str | None = None) -> list[str]:
    """Exact ``docker run -d --rm`` argv for the execution container (§3.3, KD-8).
    Mount set {workspace} + host-only masks (022); ``--network none`` (bridge
    when ``network`` is true - 014 per-stage networking), --read-only,
    --cap-drop ALL; --user host uid:gid (host-owned bind mounts, AE-SBX-009
    root cause) with HOME=/tmp (the rootfs is read-only; /tmp is a 1777 tmpfs).
    ``--rm`` lets the daemon auto-remove the container the instant it exits,
    however it exits (D1) - a SIGKILLed CLI never reaches close().

    ``seccomp_profile`` (023) is a host path to a seccomp profile JSON; when
    set it replaces Docker's default profile (which blocks the userns syscalls
    Chromium's namespace sandbox needs under --cap-drop ALL). Chromium's
    zygote also chroots /proc/self/fdinfo at startup, which needs
    CAP_SYS_CHROOT; the profile gates chroot(2) on exactly that capability,
    so the payload adds it back (the bounding set is otherwise empty under
    --cap-drop ALL). Without the cap the zygote dies even with the profile
    (023 host-side gate 2). The ``--cap-add SYS_CHROOT`` applies to any
    seccomp profile by design: the bundled profile requires it, and custom
    profiles are expected to follow the same cap-gated pattern."""
    payload = [
        "docker", "run", "--detach", "--rm", "--name",
        exec_container_name(run_id, network),
        "--network", "bridge" if network else "none",
        "--read-only", "--cap-drop", "ALL",
    ]
    if seccomp_profile is not None:
        payload.extend([
            "--security-opt", f"seccomp={seccomp_profile}",
            "--cap-add", "SYS_CHROOT",
        ])
    payload.extend([
        "--memory", profile.memory, "--cpus", str(profile.cpus),
        "--tmpfs", "/tmp", "--tmpfs", "/root",
    ])
    user = host_user_flag()
    if user is not None:
        payload.extend(["--user", user, "--env", "HOME=/tmp"])
    payload.extend(_sandbox_mounts(workspace, sandbox_venv_dir))
    payload.extend([
        "--workdir", workspace,
        image, "sleep", "infinity",
    ])
    return payload


def docker_provision_payload(*, run_id: str, image: str, workspace: str,
                             setup_cmd: str,
                             sandbox_venv_dir: str | None = None) -> list[str]:
    """Exact ``docker run --rm`` argv for the provision container (§3.4).
    Network ON (default bridge), rw rootfs, --cap-drop ALL, mount set
    {workspace} + host-only masks (022); --user host uid:gid (AE-SBX-009) with
    HOME=/tmp."""
    payload = [
        "docker", "run", "--rm", "--name", f"{run_id}-provision",
        "--network", "bridge", "--cap-drop", "ALL",
        "--memory", DEFAULT_PROVISION_MEMORY, "--cpus", str(DEFAULT_PROVISION_CPUS),
    ]
    user = host_user_flag()
    if user is not None:
        payload.extend(["--user", user, "--env", "HOME=/tmp"])
    payload.extend(_sandbox_mounts(workspace, sandbox_venv_dir))
    payload.extend([
        "--workdir", workspace,
        image, "sh", "-c", setup_cmd,
    ])
    return payload


def select_backend(platform: str, probe_result: ProbeResult) -> type["Sandbox"]:
    """Totality: exactly one backend for every input - DockerBackend. Retained
    as the seam where a future native/gVisor backend plugs in (BR-3.3)."""
    return DockerBackend


def command_argv(command: str) -> list[str]:
    """Translate a command string into a sandbox argv. Simple commands are
    shlex-split and run argv-style; shell-metacharacter commands run via the
    container's shell (backward compatible with unit-2's classification)."""
    if needs_shell(command):
        return ["/bin/sh", "-c", command]
    return split_command(command)


# ---------------------------------------------------------------------------
# Command runner seam (unit tests inject this - NOT monkeypatching)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CmdResult:
    exit_code: int | None
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(self, argv: list[str], *, timeout: float | None,
            check: bool = False) -> CmdResult: ...


def _run_subprocess(argv: list[str], *, timeout: float | None = None,
                    check: bool = False) -> CmdResult:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, check=check)
        return CmdResult(exit_code=proc.returncode, stdout=proc.stdout,
                         stderr=proc.stderr)
    except subprocess.TimeoutExpired as e:
        return CmdResult(exit_code=None, stdout=e.stdout or "", stderr=e.stderr or "")
    except FileNotFoundError as e:
        return CmdResult(exit_code=127, stdout="", stderr=str(e))


class SubprocessRunner:
    """The real subprocess-based docker CLI runner (the default)."""

    def run(self, argv: list[str], *, timeout: float | None = None,
            check: bool = False) -> CmdResult:
        return _run_subprocess(argv, timeout=timeout, check=check)


_default_runner = SubprocessRunner()


# ---------------------------------------------------------------------------
# Sandbox surface
# ---------------------------------------------------------------------------


class Sandbox(ABC):
    @abstractmethod
    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult: ...

    @abstractmethod
    def close(self) -> None: ...


def _guard_git(request: SandboxExecutionRequest,
               git_mutations_blocked: bool) -> None:
    if git_mutations_blocked and is_git_mutation(request.argv):
        name = " ".join(request.argv[:2])
        raise SandboxError(
            f"AE-SBX-008: git mutation {name!r} blocked - set "
            "isolation.allow_git_mutations: true to allow (docs/setup.md §3)")


def _request_digest(argv: list[str], cwd: str, env: dict[str, str]) -> str:
    payload = [argv, cwd, sorted(env.keys())]
    return hashlib.sha256(json.dumps(payload).encode("utf-8")).hexdigest()


class DockerBackend(Sandbox):
    """Docker-based sandbox: a persistent network-none execution container per
    run, plus - when any step declares ``network: true`` - a second
    bridge-network container (eagerly created + started, Q2). ``docker exec``
    per request routes to the container matching its network mode; both are
    destroyed on close(). The injected ``runner`` callable is the unit-test
    seam."""

    def __init__(self, *, image: str, workspace: str, iso_state_dir: str,
                 profile: EffectiveProfile, runner: CommandRunner | None = None,
                 network: bool = False, sandbox_venv_dir: str | None = None,
                 seccomp_profile: str | None = None):
        self._image = image
        self._workspace = workspace
        self._iso_state_dir = Path(iso_state_dir)  # host-only (audit)
        self._profile = profile
        self._runner = runner or _default_runner
        self._run_id = uuid.uuid4().hex
        self._container = exec_container_name(self._run_id, network=False)
        self._network_container = exec_container_name(self._run_id, network=True)
        self._network_enabled = network
        self._sandbox_venv_dir = sandbox_venv_dir  # 022 relocated venv (opt-in)
        self._seccomp_profile = seccomp_profile  # 023 userns allowlist (opt-in)
        if sandbox_venv_dir is not None:
            # 022: the relocated venv dir is a bind-mount source. On native
            # Linux, Docker auto-creates a missing source as root, which the
            # --user uid:gid containers then cannot write (the AE-SBX-009
            # ownership class). Pre-create it host-side so it is uid-owned.
            Path(sandbox_venv_dir).mkdir(parents=True, exist_ok=True)
        self._started = False
        self._network_started = False

    # -- infrastructure (startup) -------------------------------------------

    def ensure_image(self) -> None:
        """Bounded-retry pull + digest verify (BR-10, SECURITY-07). Retry only
        on connection/timeout errors, never auth/not-found; never retry exec."""
        inspect = self._runner.run(
            ["docker", "image", "inspect", self._image, "--format", "{{.Id}}"],
            timeout=30)
        if inspect.exit_code == 0:
            return
        last = None
        for attempt in range(3):
            pull = self._runner.run(["docker", "pull", self._image], timeout=1800)
            if pull.exit_code == 0:
                last = None
                break
            last = pull
            if not _is_transient_pull_error(pull):
                raise SandboxError(
                    f"AE-SBX-006: image pull failed: {pull.stderr.strip() or pull.stdout.strip()} "
                    f"- docs/setup.md §5")
            time.sleep(2 ** (attempt + 1))
        if last is not None:
            raise SandboxError(
                f"AE-SBX-006: image pull failed after retries: "
                f"{last.stderr.strip() or last.stdout.strip()} - docs/setup.md §5")
        self._verify_digest()

    def create_execution_container(self) -> None:
        """Eager create + start via ``docker run -d --rm`` (Q2, D1) - never
        lazy; ``run -d`` is create + start in one call, so there is no
        separate ``docker start``. When any step declares ``network: true`` a
        second, otherwise-identical bridge-network container is started so no
        infrastructure work happens mid-run (014 per-stage networking)."""
        payload = docker_create_payload(
            run_id=self._run_id, image=self._image, profile=self._profile,
            workspace=self._workspace, network=False,
            sandbox_venv_dir=self._sandbox_venv_dir,
            seccomp_profile=self._seccomp_profile)
        res = self._runner.run(payload, timeout=60)
        if res.exit_code != 0:
            raise SandboxError(
                f"AE-SBX-010: container run failed: "
                f"{res.stderr.strip() or res.stdout.strip()}")
        self._started = True
        if not self._network_enabled:
            return
        net_payload = docker_create_payload(
            run_id=self._run_id, image=self._image, profile=self._profile,
            workspace=self._workspace, network=True,
            sandbox_venv_dir=self._sandbox_venv_dir,
            seccomp_profile=self._seccomp_profile)
        net_res = self._runner.run(net_payload, timeout=60)
        if net_res.exit_code != 0:
            # reclaim the already-started network-none container - never leak it.
            self._runner.run(["docker", "rm", "-f", self._container], timeout=60)
            self._started = False
            raise SandboxError(
                f"AE-SBX-013: networked container run failed: "
                f"{net_res.stderr.strip() or net_res.stdout.strip()}")
        self._network_started = True

    def provision(self, setup_cmd: str, stamp_key: str, stamp_dir: str,
                  force: bool) -> None:
        """Run one ``setup:`` hook in the provision container (network ON),
        stamp-cached. Nonzero exit → AE-SBX-009, no stamp written.

        016 self-heal: a warm stamp no longer blind-skips. Before trusting it,
        probe the sandbox venv inside the exec container - a host-built venv
        (darwin symlinks to a host interpreter) looks valid on the host but is
        broken in the Linux container. A ``broken`` probe removes the venv
        (fail loud if removal is impossible) and falls through to re-provision;
        ``valid``/``missing``/``unknown`` keep the stamp skip.
        """
        if not force and read_stamp(Path(stamp_dir), stamp_key):
            if self._probe_venv() == "broken":
                remove_sandbox_venv(self._workspace,
                                    sandbox_venv_dir=self._sandbox_venv_dir)
            else:
                return
        payload = docker_provision_payload(
            run_id=self._run_id, image=self._image, workspace=self._workspace,
            setup_cmd=setup_cmd, sandbox_venv_dir=self._sandbox_venv_dir)
        res = self._runner.run(payload, timeout=PROVISION_TIMEOUT_SECONDS)
        if res.exit_code != 0:
            tail = (res.stdout or res.stderr or "")[-4000:]
            raise SandboxError(
                f"AE-SBX-009: setup command failed (exit {res.exit_code}): {tail} "
                f"- stamp not written (docs/setup.md §5)")
        write_stamp(Path(stamp_dir), stamp_key)

    def _probe_venv(self) -> str:
        """Run the in-container venv probe and classify it (016 self-heal)."""
        res = self._runner.run(
            venv_probe_argv(self._container, self._workspace), timeout=30)
        return venv_probe_state(res.stdout, res.exit_code)

    # -- execution ----------------------------------------------------------

    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        _guard_git(request, self._profile.git_mutations_blocked)
        container = self._network_container if request.network else self._container
        if request.network and not self._network_started:
            raise SandboxError(
                "AE-SBX-013: networked execution requested but no networked "
                "container is running - declare `network: true` on the step "
                "(docs/setup.md §3)")
        start = time.monotonic()
        exec_argv = ["docker", "exec"]
        for key, value in request.env.items():
            exec_argv += ["-e", f"{key}={value}"]
        exec_argv += ["-w", self._workspace, container]
        runner_timeout = None
        if request.timeout is not None:
            exec_argv += ["/usr/bin/timeout", str(math.ceil(request.timeout))]
            runner_timeout = request.timeout + 30.0
        exec_argv += list(request.argv)
        res = self._runner.run(exec_argv, timeout=runner_timeout)
        duration = time.monotonic() - start
        timed_out = res.exit_code == TIMEOUT_EXIT_CODE
        result = SandboxExecutionResult(
            exit_code=res.exit_code, stdout=res.stdout, stderr=res.stderr,
            duration_seconds=duration, backend="docker", timed_out=timed_out)
        self._audit(request, result, container)
        if res.exit_code is None:
            raise SandboxError(
                f"AE-SBX-012: Docker daemon lost mid-run: "
                f"{res.stderr.strip() or res.stdout.strip()}")
        return result

    def close(self) -> None:
        """Best-effort stop + rm; idempotent (BR-8.3). With ``--rm`` the
        daemon may already have auto-removed the container by the time close()
        runs, so a nonzero stop/rm exit code ("no such container") is treated
        as success, never raised (D2)."""
        if self._started:
            self._runner.run(["docker", "stop", "-t", "5", self._container],
                             timeout=60)
            # rm -f returns rc != 0 when --rm already removed it - ignore, never raise.
            self._runner.run(["docker", "rm", "-f", self._container], timeout=60)
            self._started = False
        if self._network_started:
            self._runner.run(["docker", "stop", "-t", "5", self._network_container],
                             timeout=60)
            self._runner.run(["docker", "rm", "-f", self._network_container],
                             timeout=60)
            self._network_started = False

    # -- internal -----------------------------------------------------------

    def _verify_digest(self) -> None:
        digest = _pinned_digest(self._image)
        if digest is None:
            return
        inspect = self._runner.run(
            ["docker", "image", "inspect", self._image, "--format", "{{.RepoDigests}}"],
            timeout=30)
        if inspect.exit_code != 0 or digest not in inspect.stdout:
            raise SandboxError(
                "AE-SBX-007: post-pull digest mismatch - supply-chain check "
                "failed; refusing to continue")

    def _audit(self, request: SandboxExecutionRequest,
               result: SandboxExecutionResult, container: str) -> None:
        append_audit_record(self._iso_state_dir, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline": request.pipeline or "",
            "step": request.step or "",
            "request_digest": _request_digest(request.argv, request.cwd, request.env),
            "backend": "docker",
            "container_id": container,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_seconds": result.duration_seconds,
        })


# ---------------------------------------------------------------------------
# Probe (IO adapter - faked in tests, FD-5)
# ---------------------------------------------------------------------------


def probe_docker(runner: CommandRunner | None = None) -> ProbeResult:
    runner = runner or _default_runner
    if shutil.which("docker") is None:
        raise SandboxError(
            "AE-SBX-001: docker is not on PATH - install a Docker engine and "
            "add it to your PATH (docs/setup.md §1)")
    version = runner.run(
        ["docker", "version", "--format", "{{.Server.APIVersion}}"], timeout=30)
    if version.exit_code != 0:
        raise SandboxError(
            "AE-SBX-002: Docker daemon unreachable - start your Docker engine "
            "(docs/setup.md §1)")
    api = version.stdout.strip()
    if _version_lt(api, ENGINE_API_FLOOR):
        raise SandboxError(
            f"AE-SBX-003: Docker Engine API {api} found, require >= "
            f"{ENGINE_API_FLOOR} (Engine 24) - upgrade Docker (docs/setup.md §1)")
    info = runner.run(["docker", "info", "--format", "{{.OSType}}"], timeout=30)
    if info.exit_code != 0 or info.stdout.strip() != "linux":
        raise SandboxError(
            f"AE-SBX-004: Docker OSType must be linux, found "
            f"{info.stdout.strip() or 'unknown'} - switch Docker Desktop to "
            "Linux containers (docs/setup.md §1)")
    return ProbeResult(api_version=api, ostype="linux")


def _version_lt(a: str, b: str) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in re.split(r"[^0-9]+", v) if x.isdigit())
    return parts(a) < parts(b)


def _pinned_digest(image: str) -> str | None:
    return image.split("@", 1)[1] if "@" in image else None


def _is_transient_pull_error(res: CmdResult) -> bool:
    text = f"{res.stderr} {res.stdout}".lower()
    markers = (
        "cannot connect", "connection refused", "connection reset",
        "tls handshake", "timeout", "no such host", "temporary failure",
        "network is unreachable", "i/o timeout",
    )
    return any(m in text for m in markers)


# ---------------------------------------------------------------------------
# Audit + stamps (host-side only)
# ---------------------------------------------------------------------------


def append_audit_record(state_dir: Path, record: dict) -> None:
    """One JSON record per sandboxed execution, appended to
    sandbox-audit.jsonl. Rotates to sandbox-audit.jsonl.1 (overwrite) at 5 MB.
    Host-side only - never called from inside the sandbox (BR-11)."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "sandbox-audit.jsonl"
    if path.exists() and path.stat().st_size > AUDIT_MAX_BYTES:
        os.replace(path, state_dir / "sandbox-audit.jsonl.1")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def read_stamp(stamp_dir: Path, stamp_key: str) -> bool:
    return (Path(stamp_dir) / stamp_key).exists()


def write_stamp(stamp_dir: Path, stamp_key: str) -> None:
    d = Path(stamp_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / stamp_key).touch()


def clean_stamps(state_dir: Path) -> int:
    stamps = Path(state_dir) / "stamps"
    if not stamps.is_dir():
        return 0
    files = list(stamps.iterdir())
    for f in files:
        f.unlink()
    return len(files)


# ---------------------------------------------------------------------------
# Eager startup (Q2) - probe → image → run -d --rm → provision all setups
# ---------------------------------------------------------------------------

_LOCKFILE_NAMES = ("package-lock.json", "requirements.txt")
_SKIP_DIRS = {".git", ".venv", ".venv-sandbox", "node_modules", "__pycache__",
              ".pytest_cache", ".hypothesis"}


def _lockfile_hashes(workspace: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    root = Path(workspace)
    for name in _LOCKFILE_NAMES:
        for p in root.rglob(name):
            if _skip_scan_path(p, root):
                continue
            result[str(p.relative_to(root))] = _file_digest(p)
    for p in root.rglob("*.csproj"):
        if _skip_scan_path(p, root):
            continue
        result[str(p.relative_to(root))] = _file_digest(p)
    return result


def _skip_scan_path(p: Path, root: Path) -> bool:
    return any(part in _SKIP_DIRS for part in p.relative_to(root).parts)


def _file_digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _declared_setups(cfg: PipelineConfig, workspace: Path):
    hashes = _lockfile_hashes(workspace)
    for step in cfg.steps:
        if step.command is not None and step.setup is not None:
            yield step.setup, setup_stamp_key(step.setup, hashes)


def isolation_state_dir(cfg: PipelineConfig) -> Path:
    """The resolved host-only isolation state dir (stamps + audit)."""
    iso = cfg.isolation or IsolationConfig()
    return Path(iso.state_dir).expanduser()


def resolved_seccomp_profile(cfg: PipelineConfig) -> str | None:
    """The pipeline's seccomp profile with any ``bundled:<name>`` sentinel
    expanded to a real host path (025). A raw path passes through unchanged
    and is still validated by Docker at container-create time."""
    return resolve_seccomp_profile(
        cfg.isolation.seccomp_profile if cfg.isolation else None)


def resolve_image_ref(image: str, environ: Mapping[str, str] | None = None) -> str:
    """AGENTIC_DEV_LOOP_DEV_IMAGE override (tag OR digest) → else the profile
    image (digest-pinned) → if empty, fail fast AE-SBX-005."""
    src = os.environ if environ is None else environ
    override = src.get(IMAGE_ENV_OVERRIDE)
    if override:
        return override
    if not image:
        raise SandboxError(
            f"AE-SBX-005: no pinned digest published and no "
            f"{IMAGE_ENV_OVERRIDE} override - set {IMAGE_ENV_OVERRIDE} to a "
            "dev image ref (docs/setup.md §2)")
    return image


def startup_sandbox(cfg: PipelineConfig, *, workspace: Path,
                    runner: CommandRunner | None = None,
                    refresh: bool | None = None) -> Sandbox:
    """Eager startup sequence (Q2): probe → image → backend → ensure_image →
    create+start → pre-provision every declared setup (stamp-cached)."""
    seccomp_profile = resolved_seccomp_profile(cfg)
    probe_docker(runner)
    profile = resolve_effective_profile(cfg.isolation)
    image = resolve_image_ref(profile.image)
    iso_state_dir = isolation_state_dir(cfg)
    needs_network = any(step.network for step in cfg.steps)
    sandbox_venv_dir = resolve_sandbox_venv_dir(
        cfg.isolation.sandbox_venv_dir if cfg.isolation else None, workspace)
    backend = DockerBackend(
        image=image, workspace=str(workspace), iso_state_dir=str(iso_state_dir),
        profile=profile, runner=runner, network=needs_network,
        sandbox_venv_dir=sandbox_venv_dir, seccomp_profile=seccomp_profile)
    backend.ensure_image()
    backend.create_execution_container()
    if refresh is None:
        refresh = bool(cfg.isolation and cfg.isolation.refresh)
    for setup_cmd, stamp_key in _declared_setups(cfg, workspace):
        backend.provision(setup_cmd, stamp_key, str(iso_state_dir / "stamps"),
                          force=refresh)
    return backend
