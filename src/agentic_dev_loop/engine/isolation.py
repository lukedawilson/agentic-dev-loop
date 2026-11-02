"""Sandbox execution domain (unit-3).

The two agent-influence channels - the ``terminal`` tool and command steps -
run inside a Docker-based, kernel-level sandbox that is always on (FD-4).
``Sandbox`` is the retained extension seam; ``DockerBackend`` is the single
concrete implementation (user directive 2026-10-19). Everything else here is
either pure translation logic (the PBT-03 targets) or host-side helpers.

The sandbox mounts the per-run export (a .git-free copy of the repo's product
files) plus host-side cache bind-mounts (052); the real repo is never mounted.
The isolation state dir (host-side caches + audit JSONL) and the pipeline
run-state dir (checkpoint DB + last-error.md) are never mounted - sandboxed
code cannot reach them. Setup hooks run fresh, per step entry, into the export.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol

from agentic_dev_loop.core.config import IsolationConfig, PipelineConfig
from . import egress_policy as egress_policy_module
from . import egress_proxy as egress_proxy_module
from .egress_policy import EgressPolicy
from .mounts import Mount, MountSet
from .proc import needs_shell, split_command
from .seccomp import resolve_seccomp_profile
from .step_workspace import StepWorkspace
from .sync_back import SyncBackReport, _snapshot, merge_append_only, sync_back

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

# 030 infra-failure classifier + orphan-container reaper (D1/D4).
_EIO_PATTERN = re.compile(r"input/output error|I/O error", re.IGNORECASE)
CONTAINER_REGISTRY = "container-registry.jsonl"
# 038: engine containers now include the per-policy egress proxy containers;
# the per-run internal network is reaped as its own kind.
_CONTAINER_NAME_RE = re.compile(
    r"^[0-9a-f]{32}-(exec(-[0-9a-f]{16})?(-net)?|proxy(-[0-9a-f]{16})?)$")
_NETWORK_NAME_RE = re.compile(r"^[0-9a-f]{32}-net$")

EGRESS_PROXY_PORT = 3128
HOST_ACCESS_NETWORK = "bridge"
EGRESS_EVENTS_PREFIX = "egress-events"

# 039: hardened proxy-container runtime posture and readiness bounds.
PROXY_USER = "65534:65534"
PROXY_MEMORY = "64m"
PROXY_CPUS = 0.5
PROXY_BIND_TIMEOUT = 15.0
PROXY_READY_ATTEMPTS = 5
PROXY_READY_DELAY = 0.2


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
    network: bool = False  # route to a network-enabled container when True
    # unit-5: the resolved per-step mount set + egress policy (BR-1/BR-5).
    # None keeps the legacy two-container model (workspace mount, no proxy).
    mount_set: MountSet | None = None
    egress_policy: EgressPolicy | None = None
    # Host-side audit metadata (BR-11) - never sent into the container.
    pipeline: str | None = None
    step: str | None = None
    # 036 lane boundary (v1): the unit allowlist every synced path must match.
    # None = unrestricted (no engine-allowlist.yaml); an empty tuple = present
    # but empty file (deny all synced paths).
    allowlist: tuple[str, ...] | None = None

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
    wrote_workspace: bool = False  # declared-rw paths changed (032)

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


def container_profile_key(mount_set: MountSet, policy: EgressPolicy) -> str:
    """Stable pool key (D-1/BR-10): equal (mount set, egress policy) pairs
    hash to the same key, every time. Pure (PBT-03)."""
    canonical = {
        "mounts": mount_set.to_dict(),
        "egress": {
            "class": policy.policy_class,
            "targets": [{"host": t.host, "port": t.port}
                         for t in policy.targets],
        },
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


def classify_infra_failure(exit_code: int | None, stderr: str, stdout: str) -> str | None:
    """Classify a daemon-side exec failure (030, D1). Returns a short reason
    string when the failure is Docker/VM infrastructure and None for a routine
    command failure. Pure - no I/O.

    Only the Docker CLI contract is trusted here: exit 125 means the docker
    client itself failed; exit 126 means the contained command could not be
    invoked, and combined with an EIO read error it proves the guest image was
    unreadable; a daemon error prefix in stderr cannot come from the
    container's command (the CLI prints it before the command runs)."""
    if exit_code == 125:
        return "docker CLI exit 125 (daemon-side error)"
    if exit_code == 126 and _EIO_PATTERN.search(f"{stderr} {stdout}"):
        return "exit 126 with an I/O error: the guest could not load the command"
    if exit_code in (1, 125, 126) and "Error response from daemon:" in stderr:
        return "docker daemon error response"
    return None


@dataclass(frozen=True)
class ContainerProfile:
    """The profile-keyed pool identity (D-1/BR-10): resolved mount set +
    resolved egress policy + seccomp/cap policy. Equal profiles share one
    container."""

    mount_set: MountSet
    egress_policy: EgressPolicy
    seccomp_caps: str | None = None

    def key(self) -> str:
        return container_profile_key(self.mount_set, self.egress_policy)


@dataclass
class _ProfileContainer:
    """One pooled execution container (D-1/BR-10): its resolved profile plus
    the running state. ``proxy`` is the per-policy egress proxy (network-on
    profiles only); ``started`` is True once ``docker run`` returned 0."""

    name: str
    mount_set: MountSet
    egress_policy: EgressPolicy
    proxy_name: str | None = None
    started: bool = False


def run_network_name(run_id: str) -> str:
    """The per-run internal network name (038, D1)."""
    return f"{run_id}-net"


def proxy_container_name(run_id: str, profile_key: str) -> str:
    """The container name (also its internal DNS name) for a per-policy proxy."""
    return f"{run_id}-proxy-{profile_key}"


def proxy_url(proxy_host: str, port: int = EGRESS_PROXY_PORT) -> str:
    """The sandbox-reachable proxy URL: the proxy's internal address on the
    internal network (038, D3). Never 127.0.0.1 or the host-gateway."""
    return f"http://{proxy_host}:{port}"


def egress_events_path(iso_state_dir: str | Path, run_id: str) -> Path:
    """The mounted enforcement-events file for one run (038, D6)."""
    return Path(iso_state_dir) / f"{EGRESS_EVENTS_PREFIX}-{run_id}.jsonl"


def proxy_usable_ips(subnets: str) -> list[str]:
    """Usable non-gateway IPv4 host addresses from the first viable subnet
    (048 D1): ``hosts[1:]``, skipping the gateway at ``hosts[0]``. Empty when
    no subnet offers a non-gateway host."""
    for token in subnets.split():
        try:
            network = ipaddress.ip_network(token, strict=False)
        except ValueError:
            continue
        if network.version != 4:
            continue
        hosts = list(network.hosts())
        if len(hosts) < 2:
            continue
        return [str(host) for host in hosts[1:]]
    return []



def proxy_readiness_argv(name: str, host: str,
                         port: int = EGRESS_PROXY_PORT) -> list[str]:
    """The in-container TCP-connect probe for proxy readiness (039, D2)."""
    probe = (
        "import socket,sys;"
        "s=socket.socket();"
        "s.settimeout(2);"
        "s.connect((sys.argv[1], int(sys.argv[2])));"
        "s.close()"
    )
    return ["docker", "exec", name, "python3", "-c", probe, host, str(port)]



def mount_set_docker_flags(export_root: str, mount_set: MountSet) -> list[str]:
    """Translate a resolved MountSet into Docker ``-v`` flags over the
    export (ID-4/BR-1). Strict-by-default: an empty mount set mounts nothing
    (BR-1.1). A ``.`` entry mounts the whole export with its mode; a set of
    subpath entries (or a ``ro`` whole-tree entry plus ``rw`` subpaths) mounts
    the export root read-only and adds a writable submount for each
    declared-rw path (declared-writes, BR-1.2)."""
    if not mount_set.mounts:
        return []
    base_mode = next((m.mode for m in mount_set.mounts if m.path in (".", "")),
                     None)
    if base_mode is not None:
        flags = ["-v", f"{export_root}:{export_root}"
                 f"{':ro' if base_mode == 'ro' else ''}"]
        if base_mode == "ro":
            for m in mount_set.mounts:
                if m.path not in (".", "") and m.mode == "rw":
                    flags += ["-v", f"{export_root}/{m.path}:{export_root}/{m.path}"]
        return flags
    flags = ["-v", f"{export_root}:{export_root}:ro"]
    for m in mount_set.mounts:
        if m.mode == "rw":
            flags += ["-v", f"{export_root}/{m.path}:{export_root}/{m.path}"]
    return flags


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
    land in a container on the per-run internal network (038); the default is the always-on
    network-none container (014 per-stage networking)."""
    return f"{run_id}-exec-net" if network else f"{run_id}-exec"


def docker_create_payload(*, run_id: str, image: str, profile: EffectiveProfile,
                          workspace: str, network: bool = False,
                          network_name: str | None = None,
                          seccomp_profile: str | None = None,
                          mount_set: MountSet | None = None,
                          export_root: str | None = None,
                          cache_mounts: list[tuple[str, str, str]] | None = None,
                          egress_proxy: str | None = None,
                          name: str | None = None) -> list[str]:
    """Exact ``docker run -d --rm`` argv for the execution container (§3.3, KD-8).
    Mount set {export root} + declared caches (or {workspace} in the legacy
    model); ``--network none`` (the per-run internal network when ``network``
    is true - 038), --read-only, --cap-drop ALL; --user host uid:gid
    (host-owned bind mounts, AE-SBX-009 root cause) with HOME=/tmp (the rootfs
    is read-only; /tmp is a 1777 tmpfs). ``--rm`` lets the daemon auto-remove
    the container the instant it exits, however it exits (D1) - a SIGKILLed
    CLI never reaches close().

    ``seccomp_profile`` (023) is a host path to a seccomp profile JSON; when
    set it replaces docker's default profile (which blocks the userns
    syscalls Chromium's namespace sandbox needs under --cap-drop ALL).
    Chromium's zygote also chroots /proc/self/fdinfo at startup, which needs
    CAP_SYS_CHROOT; the profile gates chroot(2) on exactly that capability,
    so the payload adds it back (the bounding set is otherwise empty under
    --cap-drop ALL). Without the cap the zygote dies even with the profile
    (023 host-side gate 2). The ``--cap-add SYS_CHROOT`` applies to any
    seccomp profile by design: the bundled profile requires it, and custom
    profiles are expected to follow the same cap-gated pattern."""
    payload = [
        "docker", "run", "--detach", "--rm", "--name",
        name or exec_container_name(run_id, network),
        "--label", "agentic-dev-loop=true",
        "--label", f"agentic-dev-loop.run={run_id}",
        "--network", (network_name or run_network_name(run_id)) if network else "none",
    ]
    payload += ["--read-only", "--cap-drop", "ALL"]
    if seccomp_profile is not None:
        payload.extend([
            "--security-opt", f"seccomp={seccomp_profile}",
            "--cap-add", "SYS_CHROOT",
        ])
    payload.extend([
        "--memory", profile.memory, "--cpus", str(profile.cpus),
        "--tmpfs", "/tmp", "--tmpfs", "/root",
    ])
    if egress_proxy is not None:
        # unit-5 (BR-5): force container HTTP(S) through the per-policy proxy.
        payload += [
            "--env", f"HTTP_PROXY={egress_proxy}",
            "--env", f"HTTPS_PROXY={egress_proxy}",
            "--env", "NO_PROXY=localhost,127.0.0.1",
        ]
    user = host_user_flag()
    if user is not None:
        payload.extend(["--user", user, "--env", "HOME=/tmp"])
    if export_root is not None:
        root = export_root
        payload.extend(mount_set_docker_flags(root, mount_set or MountSet()))
        # Declared cache paths bind-mount rw from the host-side cache dir at
        # the same relative paths inside the export (BR-3/F2/052).
        for source, target, _mode in (cache_mounts or []):
            payload += ["-v", f"{source}:{target}"]
        workdir = root
    else:
        root = workspace
        payload += ["-v", f"{workspace}:{workspace}"]
        workdir = workspace
    payload.extend([
        "--workdir", workdir,
        image, "sleep", "infinity",
    ])
    return payload



def docker_proxy_payload(*, run_id: str, image: str, name: str,
                         policy_json: str,
                         dial_rewrites_json: str,
                         events_host_path: str,
                         egress_proxy_host_path: str,
                         egress_policy_host_path: str,
                         bind_host: str,
                         port: int = EGRESS_PROXY_PORT,
                         host_network: str = HOST_ACCESS_NETWORK) -> list[str]:
    """Exact argv for the containerized egress proxy (038 D2/D5, 039 D1/D4).

    Created on the host-access network with a host-gateway mapping (D8), then
    attached to the internal network with the pinned ``bind_host``; the proxy
    binds that internal address only, so the host-access attachment carries no
    listener. Hardened with a read-only rootfs, dropped capabilities, a non-root
    user, and memory/CPU limits. The policy is passed as JSON; the two engine
    modules are bind-mounted read-only so the image does not need the engine
    package installed."""
    payload = [
        "docker", "run", "--detach", "--rm", "--name", name,
        "--label", "agentic-dev-loop=true",
        "--label", f"agentic-dev-loop.run={run_id}",
        "--network", host_network,
        "--add-host", "host.docker.internal:host-gateway",
        "--read-only",
        "--cap-drop", "ALL",
        "--user", PROXY_USER,
        "--memory", PROXY_MEMORY, "--cpus", str(PROXY_CPUS),
        "-v", f"{egress_proxy_host_path}:/egress/egress_proxy.py:ro",
        "-v", f"{egress_policy_host_path}:/egress/egress_policy.py:ro",
        "-v", f"{events_host_path}:/egress/events.jsonl",
        "--workdir", "/egress",
        image,
        "python3", "/egress/egress_proxy.py",
        "--policy", policy_json,
        "--events", "/egress/events.jsonl",
        "--dial-rewrites", dial_rewrites_json,
        "--host", bind_host,
        "--bind-timeout", str(PROXY_BIND_TIMEOUT),
        "--port", str(port),
    ]
    return payload


def docker_provision_payload(*, run_id: str, image: str, export_root: str,
                             setup_cmd: str,
                             cache_mounts: list[tuple[str, str, str]]
                             | None = None,
                             env: dict[str, str] | None = None) -> list[str]:
    """Exact ``docker run --rm`` argv for the one-shot provision container
    (052). Network ON (default bridge), rw rootfs, --cap-drop ALL, mount the
    export root rw + the host-side cache bind-mounts; workdir = export root;
    the declaring step's env block is injected so cache/registry env vars
    resolve during setup; --user host uid:gid (AE-SBX-009) with HOME=/tmp."""
    payload = [
        "docker", "run", "--rm", "--name", f"{run_id}-provision",
        "--network", "bridge", "--cap-drop", "ALL",
        "--memory", DEFAULT_PROVISION_MEMORY, "--cpus", str(DEFAULT_PROVISION_CPUS),
    ]
    user = host_user_flag()
    if user is not None:
        payload.extend(["--user", user, "--env", "HOME=/tmp"])
    for key, value in (env or {}).items():
        payload += ["--env", f"{key}={value}"]
    payload += ["-v", f"{export_root}:{export_root}"]
    for source, target, _mode in (cache_mounts or []):
        payload += ["-v", f"{source}:{target}"]
    payload.extend([
        "--workdir", export_root,
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
            check: bool = False, input: str | None = None) -> CmdResult: ...


def _run_subprocess(argv: list[str], *, timeout: float | None = None,
                    check: bool = False, input: str | None = None) -> CmdResult:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, check=check, input=input)
        return CmdResult(exit_code=proc.returncode, stdout=proc.stdout,
                         stderr=proc.stderr)
    except subprocess.TimeoutExpired as e:
        return CmdResult(exit_code=None, stdout=e.stdout or "", stderr=e.stderr or "")
    except FileNotFoundError as e:
        return CmdResult(exit_code=127, stdout="", stderr=str(e))


class SubprocessRunner:
    """The real subprocess-based docker CLI runner (the default)."""

    def run(self, argv: list[str], *, timeout: float | None = None,
            check: bool = False, input: str | None = None) -> CmdResult:
        return _run_subprocess(argv, timeout=timeout, check=check, input=input)


_default_runner = SubprocessRunner()


# ---------------------------------------------------------------------------
# Sandbox surface
# ---------------------------------------------------------------------------


class Sandbox(ABC):
    @abstractmethod
    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult: ...

    @abstractmethod
    def close(self) -> None: ...

    def provision(self, setup_cmd: str, *,
                  env: dict[str, str] | None = None) -> None:
        """Run a setup hook at the step boundary (052). The default is a no-op
        so fakes and legacy backends without setup stay compatible."""
        return None

    @property
    def infra_error(self) -> SandboxError | None:
        """The sticky sandbox infra failure (030, D1/D2), or None. Backends
        that classify daemon-side failures override this; the default keeps
        every other Sandbox implementation healthy-sandbox compatible."""
        return None


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
    """DockerBackend sandbox: a persistent network-none execution container per
    run plus, when any step declares ``network: true``, a per-run internal
    network (038, D1) and containerized per-policy egress proxies (D2).
    Networked profile containers attach to the internal network with no host
    route and reach the host only through their proxy; everything is destroyed
    on close(). The injected ``runner`` callable is the unit-test seam."""

    def __init__(self, *, image: str, workspace: str, iso_state_dir: str,
                 profile: EffectiveProfile, runner: CommandRunner | None = None,
                 network: bool = False,
                 seccomp_profile: str | None = None,
                 export_root: str | None = None,
                 cache_mounts: list[tuple[str, str, str]] | None = None,
                 sync_excludes: list[str] | None = None,
                 merge_on_conflict: list[str] | None = None,
                 ignored: frozenset[str] | set[str] = frozenset(),
                 dial_rewrites: dict[str, str] | None = None):
        self._image = image
        self._workspace = workspace
        self._iso_state_dir = Path(iso_state_dir)  # host-only (audit)
        self._profile = profile
        self._runner = runner or _default_runner
        self._run_id = uuid.uuid4().hex
        self._container = exec_container_name(self._run_id, network=False)
        self._network_container = exec_container_name(self._run_id, network=True)
        self._network_enabled = network
        # 038: per-run internal network + containerized per-policy proxies.
        self._run_net = run_network_name(self._run_id)
        self._network_created = False
        self._proxy_containers: list[str] = []
        self._proxy_ips: dict[str, str] = {}
        self._egress_events_path = egress_events_path(self._iso_state_dir,
                                                      self._run_id)
        self._seccomp_profile = seccomp_profile  # 023 userns allowlist (opt-in)
        # unit-5: the .git-free export + its cache bind-mounts (BR-2/BR-3).
        # None keeps the legacy workspace-mount model (integration tests).
        self._export_root = export_root
        self._cache_mounts = cache_mounts or []
        # The repo-relative cache paths (BR-3.1), derived from the mount
        # targets for sync-back exclusion: caches are bind-mounted, never
        # copied, so their contents are never a sync-back change.
        self._cache_paths: list[str] = []
        if export_root is not None:
            for _source, target, _mode in self._cache_mounts:
                self._cache_paths.append(
                    os.path.relpath(target, export_root).replace("\\", "/"))
        # 028: run-wide sync-back excludes (scratch paths - skipped from both
        # the export copy and every snapshot).
        self._sync_excludes = list(sync_excludes or ())
        # 029: append-style logs union-merged on a concurrent-edit conflict
        # instead of raising (host wins, export-only lines appended).
        self._merge_on_conflict = list(merge_on_conflict or ())
        self._dial_rewrites = dict(dial_rewrites or {})
        self._baseline: dict[str, str] = {}
        # The profile-keyed pool (D-1/BR-10): profile key -> running container.
        self._pool: dict[tuple[str, bool], _ProfileContainer] = {}
        self._ignored = frozenset(ignored)  # gitignored paths (052)
        self._started = False
        self._network_started = False
        # 030: the first classified infra failure, sticky for the rest of the
        # run (D1/D2). None until the exec path proves itself broken.
        self._infra_error: SandboxError | None = None

    @property
    def export_root(self) -> str | None:
        """The materialised .git-free export root (BR-2); None when no export
        was materialised (the legacy workspace model)."""
        return self._export_root

    @property
    def infra_error(self) -> SandboxError | None:
        """The sticky infra failure raised by the last classified exec (030)."""
        return self._infra_error

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
        separate ``docker start``.

        With an export (unit-5) this creates only the base network-none
        container (the .git-free export, whole-tree rw); step execution then
        uses the profile-keyed pool (D-1). Without an export the legacy
        two-container model is retained (integration tests, no enforcement)."""
        if self._network_enabled:
            self._ensure_run_network()
        if self._export_root is not None:
            self._create_base_export_container()
            return
        payload = docker_create_payload(
            run_id=self._run_id, image=self._image, profile=self._profile,
            workspace=self._workspace, network=False,
                        seccomp_profile=self._seccomp_profile)
        self._register_container(self._container)
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
            network_name=self._run_net,
                        seccomp_profile=self._seccomp_profile)
        self._register_container(self._network_container)
        net_res = self._runner.run(net_payload, timeout=60)
        if net_res.exit_code != 0:
            # reclaim the already-started network-none container - never leak it.
            self._runner.run(["docker", "rm", "-f", self._container], timeout=60)
            self._started = False
            raise SandboxError(
                f"AE-SBX-013: networked container run failed: "
                f"{net_res.stderr.strip() or net_res.stdout.strip()}")
        self._network_started = True

    def _ensure_run_network(self) -> None:
        """Create the per-run internal network once (038, D1/D6)."""
        if self._network_created:
            return
        self._register_network(self._run_net)
        res = self._runner.run(
            ["docker", "network", "create", "--internal",
             "--label", "agentic-dev-loop=true",
             "--label", f"agentic-dev-loop.run={self._run_id}",
             self._run_net], timeout=60)
        if res.exit_code != 0:
            raise SandboxError(
                f"AE-SBX-021: internal network create failed: "
                f"{res.stderr.strip() or res.stdout.strip()}")
        self._network_created = True

    def _register_network(self, name: str) -> None:
        """Record the per-run network for the orphan reaper (038 D6, 042 D1).
        Appended BEFORE the create command so any object that exists is
        registered; the reaper tolerates a stale entry (a dead owner whose
        create never happened gets a no-op rm and a pruned entry)."""
        _append_registry_record(self._iso_state_dir, {
            "run_id": self._run_id,
            "name": name,
            "kind": "network",
            "pid": os.getpid(),
            "process_start": process_start(os.getpid()),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    def _create_base_export_container(self) -> None:
        """Create the base export container (network-none, whole-tree rw) and
        snapshot the export baseline for sync-back conflict detection (BR-4)."""
        base = MountSet(mounts=(Mount(path=".", mode="rw"),))
        payload = docker_create_payload(
            run_id=self._run_id, image=self._image, profile=self._profile,
            workspace=self._workspace, network=False,
                        seccomp_profile=self._seccomp_profile,
            mount_set=base, export_root=self._export_root,
            cache_mounts=self._cache_mounts)
        self._register_container(self._container)
        res = self._runner.run(payload, timeout=60)
        if res.exit_code != 0:
            raise SandboxError(
                f"AE-SBX-010: container run failed: "
                f"{res.stderr.strip() or res.stdout.strip()}")
        self._started = True
        if not self._baseline:
            self._baseline = _snapshot(Path(self._export_root),
                                       self._cache_paths,
                                       self._sync_excludes)

    def provision(self, setup_cmd: str, *,
                  env: dict[str, str] | None = None) -> None:
        """Run one ``setup:`` hook in the one-shot provision container (052),
        targeting the export root. Fresh every time - no stamping, no probes.
        Nonzero exit -> AE-SBX-009 (loud halt)."""
        if self._export_root is None:
            raise SandboxError(
                "AE-SBX-017: provision needs the .git-free export, which was "
                "not materialised - the runtime must materialise an export "
                "before setup (BR-2)")
        payload = docker_provision_payload(
            run_id=self._run_id, image=self._image, export_root=self._export_root,
            setup_cmd=setup_cmd, cache_mounts=self._cache_mounts, env=env)
        res = self._runner.run(payload, timeout=PROVISION_TIMEOUT_SECONDS)
        if res.exit_code != 0:
            tail = (res.stdout or res.stderr or "")[-4000:]
            raise SandboxError(
                f"AE-SBX-009: setup command failed (exit {res.exit_code}): {tail} "
                f"(docs/setup.md §5)")


    # -- execution ----------------------------------------------------------

    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        if self._infra_error is not None:
            raise self._infra_error
        _guard_git(request, self._profile.git_mutations_blocked)
        if request.mount_set is None:
            return self._run_legacy(request)
        return self._run_profile(request)

    def _run_legacy(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        """The pre-unit-5 path: a request without a resolved mount set routes
        to the legacy two-container model (integration tests only - the
        runtime always resolves a mount set)."""
        container = self._network_container if request.network else self._container
        if request.network and not self._network_started:
            raise SandboxError(
                "AE-SBX-013: networked execution requested but no networked "
                "container is running - declare `network: true` on the step "
                "(docs/setup.md §3)")
        return self._exec(request, container)

    def _run_profile(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        """The unit-5 path (D-1): resolve the step's mount set + egress policy
        into a pool key, create the profile container lazily, exec into it,
        then sync declared-rw changes back to the real repo (BR-4)."""
        mount_set = request.mount_set or MountSet()
        policy = request.egress_policy or EgressPolicy()
        if self._export_root is None:
            raise SandboxError(
                "AE-SBX-017: a resolved mount set / egress policy needs the "
                ".git-free export, which was not materialised - the runtime "
                "must materialise an export before enforcement (BR-2)")
        key = (container_profile_key(mount_set, policy), bool(request.network))
        prof = self._pool.get(key)
        if prof is None:
            prof = self._start_profile(key, mount_set, policy, request.network)
            self._pool[key] = prof
        if request.network:
            if prof.proxy_name is None:
                raise SandboxError(
                    "AE-SBX-018: the egress proxy for this profile is missing - "
                    "failing closed (BR-11.3): no networked execution runs "
                    "without egress enforcement")
            if not self._proxy_alive(prof.proxy_name):
                raise SandboxError(
                    "AE-SBX-018: the egress proxy for this profile has stopped - "
                    "failing closed (BR-11.3): no networked execution runs "
                    "without egress enforcement")
        result = self._exec(request, prof.name)
        report: SyncBackReport | None = None
        if mount_set.rw_paths() and self._export_root is not None:
            report = self._sync_back(mount_set, request.allowlist)
        if report is not None and (report.synced_paths or report.merged_paths):
            result = SandboxExecutionResult(
                exit_code=result.exit_code, stdout=result.stdout,
                stderr=result.stderr, duration_seconds=result.duration_seconds,
                backend=result.backend, timed_out=result.timed_out,
                wrote_workspace=True)
        return result

    def _exec(self, request: SandboxExecutionRequest,
              container: str) -> SandboxExecutionResult:
        if self._infra_error is not None:
            raise self._infra_error
        start = time.monotonic()
        exec_argv = ["docker", "exec"]
        for key, value in request.env.items():
            exec_argv += ["-e", f"{key}={value}"]
        exec_argv += ["-w", request.cwd, container]
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
        if res.exit_code is None:
            self._audit(request, result, container)
            err = SandboxError(
                f"AE-SBX-012: Docker daemon lost mid-run: "
                f"{res.stderr.strip() or res.stdout.strip()}")
            self._infra_error = err
            raise err
        reason = classify_infra_failure(res.exit_code, res.stderr, res.stdout)
        self._audit(request, result, container, infra_reason=reason)
        if reason is not None:
            tail = (res.stderr or res.stdout or "").strip()[-2000:]
            message = (
                f"AE-SBX-019: sandbox infrastructure failure: {reason}. The "
                "exec container cannot run commands; this is a Docker "
                "engine/VM problem, not a command failure. Check the engine "
                "health (docker info, docker system df) and restart Docker "
                "Desktop if the VM reports I/O errors (docs/setup.md)."
            )
            if tail:
                message += f" {tail}"
            err = SandboxError(message)
            self._infra_error = err
            raise err
        return result

    def _start_profile(self, key: tuple[str, bool], mount_set: MountSet,
                       policy: EgressPolicy, network: bool) -> _ProfileContainer:
        """Create one profile container (D-1). A network-enabled profile gets
        its containerized per-policy egress proxy on both networks (038, D2)."""
        name = f"{self._run_id}-exec-{key[0][:16]}"
        if network:
            name += "-net"
        proxy_name: str | None = None
        proxy_url_value: str | None = None
        if network and self._export_root is not None:
            proxy_name = proxy_container_name(self._run_id, key[0][:16])
            proxy_ip = self._start_proxy_container(proxy_name, policy,
                                                    self._dial_rewrites)
            proxy_url_value = proxy_url(proxy_ip)
        payload = docker_create_payload(
            run_id=self._run_id, image=self._image, profile=self._profile,
            workspace=self._workspace, network=network,
            network_name=self._run_net if network else None,
                        seccomp_profile=self._seccomp_profile,
            mount_set=mount_set, export_root=self._export_root,
            cache_mounts=self._cache_mounts, egress_proxy=proxy_url_value,
            name=name)
        self._register_container(name)
        res = self._runner.run(payload, timeout=60)
        if res.exit_code != 0:
            if proxy_name is not None:
                self._rm_container(proxy_name)
            raise SandboxError(
                f"AE-SBX-010: container run failed: "
                f"{res.stderr.strip() or res.stdout.strip()}")
        return _ProfileContainer(name=name, mount_set=mount_set,
                                 egress_policy=policy, proxy_name=proxy_name,
                                 started=True)

    def _start_proxy_container(self, name: str, policy: EgressPolicy,
                               dial_rewrites: dict[str, str] | None = None) -> str:
        """Create one containerized proxy (038 D2/D5, 039 D1-D4): host-access
        network at create time with a pinned internal IP, then attach the internal
        network; the proxy binds that address only and is readiness probed from
        inside the container before the first sandbox exec (D2)."""
        self._ensure_run_network()
        events_host = self._egress_events_path
        events_host.parent.mkdir(parents=True, exist_ok=True)
        events_host.touch(exist_ok=True)
        try:
            os.chmod(events_host, 0o666)
        except OSError:
            pass
        policy_json = egress_proxy_module.policy_to_json(policy)
        dial_rewrites = dict(dial_rewrites or {})
        dial_rewrites_json = json.dumps(dial_rewrites, sort_keys=True)
        bind_host = self._proxy_internal_ip(name)
        payload = docker_proxy_payload(
            run_id=self._run_id, image=self._image, name=name,
            policy_json=policy_json, dial_rewrites_json=dial_rewrites_json,
            events_host_path=str(events_host),
            egress_proxy_host_path=str(Path(egress_proxy_module.__file__)),
            egress_policy_host_path=str(Path(egress_policy_module.__file__)),
            bind_host=bind_host, port=EGRESS_PROXY_PORT)
        self._register_container(name)
        res = self._runner.run(payload, timeout=60)
        if res.exit_code != 0:
            raise SandboxError(
                f"AE-SBX-020: egress proxy container run failed: "
                f"{res.stderr.strip() or res.stdout.strip()}")
        connect = self._runner.run(
            ["docker", "network", "connect", "--ip", bind_host,
             self._run_net, name], timeout=60)
        if connect.exit_code != 0:
            self._rm_container(name)
            raise SandboxError(
                f"AE-SBX-020: egress proxy network attach failed: "
                f"{connect.stderr.strip() or connect.stdout.strip()}")
        if not self._wait_proxy_ready(name, bind_host):
            self._rm_container(name)
            raise SandboxError(
                f"AE-SBX-021: egress proxy failed to bind "
                f"{bind_host}:{EGRESS_PROXY_PORT} after "
                f"{PROXY_READY_ATTEMPTS} readiness probes")
        self._proxy_containers.append(name)
        return bind_host

    def _proxy_internal_ip(self, proxy_name: str) -> str:
        """Return the per-proxy pinned IPv4 address on the internal network
        (048 D1): inspect the subnet and the addresses containers already hold
        on it, then allocate the next free host (profile containers join the
        run network with a daemon auto-assigned IP, so those must be skipped
        or the later ``network connect --ip`` collides)."""
        inspect = self._runner.run(
            ["docker", "network", "inspect", "-f",
             "{{range .IPAM.Config}}{{.Subnet}} {{end}}|"
             "{{range .Containers}}{{.IPv4Address}} {{end}}",
             self._run_net],
            timeout=30)
        if inspect.exit_code != 0:
            raise SandboxError(
                f"AE-SBX-021: internal network inspect failed: "
                f"{inspect.stderr.strip() or inspect.stdout.strip()}")
        subnets, sep, in_use_part = inspect.stdout.partition("|")
        in_use = in_use_part.split() if sep else ()
        return self._allocate_proxy_ip(subnets, proxy_name, in_use)

    def _allocate_proxy_ip(self, subnets: str, proxy_name: str,
                           in_use: Iterable[str] = ()) -> str:
        """Allocate one internal IPv4 address per proxy (048 D1/D2). An
        existing allocation for ``proxy_name`` is reused; otherwise the first
        free non-gateway host wins (the first proxy keeps ``hosts[1]``).
        Addresses in ``in_use`` (held by other containers on the network) are
        skipped alongside the already-allocated proxy IPs. Raises when the
        subnet has no free host instead of letting the daemon emit a
        confusing collision error."""
        existing = self._proxy_ips.get(proxy_name)
        if existing is not None:
            return existing
        hosts = proxy_usable_ips(subnets)
        if not hosts:
            raise SandboxError(
                f"AE-SBX-021: internal network has no usable IPv4 subnet: "
                f"{subnets.strip()!r}")
        taken = set(self._proxy_ips.values())
        for token in in_use:
            addr = token.split("/", 1)[0].strip()
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if isinstance(ip, ipaddress.IPv4Address):
                taken.add(str(ip))
        free = [host for host in hosts if host not in taken]
        if not free:
            raise SandboxError(
                f"AE-SBX-021: internal network {subnets.strip()!r} has "
                f"{len(hosts)} usable proxy address(es) but "
                f"{len(self._proxy_ips) + 1} egress proxies were requested - "
                f"no free address to assign")
        ip = free[0]
        self._proxy_ips[proxy_name] = ip
        return ip

    def _wait_proxy_ready(self, name: str, host: str) -> bool:
        """Bounded in-container TCP-connect readiness probe (039 D2)."""
        for _ in range(PROXY_READY_ATTEMPTS):
            res = self._runner.run(proxy_readiness_argv(name, host),
                                   timeout=15)
            if res.exit_code == 0:
                return True
            time.sleep(PROXY_READY_DELAY)
        return False


    def _proxy_alive(self, name: str) -> bool:
        """Query the daemon for proxy liveness (038 AE-SBX-018)."""
        res = self._runner.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            timeout=30)
        return res.exit_code == 0 and res.stdout.strip() == "true"

    def _rm_container(self, name: str) -> None:
        """Best-effort stop + rm for a proxy container; never raises."""
        self._runner.run(["docker", "stop", "-t", "5", name], timeout=60)
        self._runner.run(["docker", "rm", "-f", name], timeout=60)

    def _reset_export_to_host(self, rel: str) -> None:
        """Absorb one hard-conflict path: the host wins, so the export is
        reset to match it. A host-side deletion removes the export file; any
        other host state is copied back (preserving symlinks)."""
        host_path = Path(self._workspace) / rel
        export_path = Path(self._export_root) / rel
        if not host_path.exists() and not host_path.is_symlink():
            export_path.unlink(missing_ok=True)
            return
        export_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(host_path, export_path, follow_symlinks=False)

    def _sync_back(self, mount_set: MountSet,
                   allowlist: tuple[str, ...] | None = None) -> SyncBackReport | None:
        """Sync declared-rw export changes back to the real repo (BR-4/F1),
        git-aware, host-side, with the sandbox step already complete.

        029: a conflict on a declared ``merge_on_conflict:`` path is
        union-merged (host wins, export-only lines appended) and does not
        raise. A conflict on any other path - including a declared merge path
        one side deleted - is announced, absorbed (the host wins and the
        export is reset to it), and then still raises AE-SBX-016 scoped to
        those hard paths, so the next sync-back runs clean. Merge paths are
        union-merged first, before any hard-conflict raise."""
        ignored = resolve_ignored_paths(self._workspace, runner=self._runner,
                                        walk_root=self._export_root)
        report: SyncBackReport = sync_back(
            self._export_root, self._workspace, list(mount_set.rw_paths()),
            baseline=self._baseline, step_id=None,
            cache_paths=self._cache_paths, excludes=self._sync_excludes,
            merge_on_conflict=self._merge_on_conflict, allowlist=allowlist,
            ignored=ignored)

        # 036 lane boundary (v1): fail the step before any copy/delete/merge
        # so an out-of-lane write is never promoted into the real repo.
        if report.lane_violations:
            _announce(
                "lane violation: "
                + ", ".join(report.lane_violations)
                + " outside engine-allowlist.yaml")
            raise SandboxError(
                "engine lane violation: synced/merged paths outside the unit "
                "engine-allowlist.yaml: " + ", ".join(report.lane_violations))

        # 029 step 2: union-merge declared merge paths first, so a mixed
        # merge+hard conflict still converges the append-style logs before
        # the hard paths raise.
        for rel in report.merged_paths:
            host_path = Path(self._workspace) / rel
            export_path = Path(self._export_root) / rel
            host_text = host_path.read_text(encoding="utf-8")
            export_text = export_path.read_text(encoding="utf-8")
            merged = merge_append_only(host_text, export_text)
            appended = len(merged.splitlines()) - len(host_text.splitlines())
            host_path.write_text(merged, encoding="utf-8")
            export_path.write_text(merged, encoding="utf-8")
            _announce(
                f"merged concurrent edits: {rel} "
                f"({appended} sandbox lines appended)")
            append_audit_record(self._iso_state_dir, {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "sync_back_merge",
                "path": rel,
                "appended_lines": appended,
            })

        if report.conflicts:
            # fix 1 + fix 2: absorb the divergence once (host wins on every
            # hard path, reset the export to match) and announce before the
            # raise, so the next exec - even `echo hello` - runs clean.
            for rel in report.conflicts:
                self._reset_export_to_host(rel)
            _announce(
                f"sync-back conflict: {', '.join(report.conflicts)} - the "
                "workspace changed underneath; edits to these paths will "
                "not sync")
            self._baseline = _snapshot(Path(self._export_root),
                                       self._cache_paths,
                                       self._sync_excludes)
            raise SandboxError(
                f"AE-SBX-016: sync-back conflict in "
                f"{', '.join(report.conflicts)} - the real repo changed "
                f"underneath; edits to these paths will not sync (BR-4.4)")

        self._baseline = _snapshot(Path(self._export_root),
                                   self._cache_paths,
                                   self._sync_excludes)
        append_audit_record(self._iso_state_dir, {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "sync_back",
            "synced_paths": list(report.synced_paths),
            "merged_paths": list(report.merged_paths),
            "host_added_paths": list(report.host_added_paths),
        })
        return report

    def close(self) -> None:
        """Best-effort stop + rm; idempotent (BR-8.3). With ``--rm`` the
        daemon may already have auto-removed the container by the time close()
        runs, so a nonzero stop/rm exit code ("no such container") is treated
        as success, never raised (D2). Every pooled profile container and its
        proxy container are destroyed too (BR-10.2/BR-11.3), then the internal
        network is removed and enforcement events are merged into the audit log."""
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
        for prof in self._pool.values():
            if prof.started:
                self._runner.run(["docker", "stop", "-t", "5", prof.name],
                                 timeout=60)
                self._runner.run(["docker", "rm", "-f", prof.name], timeout=60)
        self._pool.clear()
        for name in self._proxy_containers:
            self._rm_container(name)
        self._proxy_containers.clear()
        self._proxy_ips.clear()
        if self._network_created:
            self._runner.run(["docker", "network", "rm", self._run_net], timeout=60)
            self._network_created = False
        merge_egress_events(self._iso_state_dir, self._egress_events_path)
        self._verify_and_prune_registry()

    def _register_container(self, name: str) -> None:
        """Record one exec/proxy container in the host registry (030 D4, 042
        D1). Appended BEFORE the create command so any object that exists is
        registered. Best-effort: a broken registry only disables reaping, it
        never fails the run; the reaper tolerates a stale entry (a dead owner
        whose create never happened gets a no-op rm and a pruned entry)."""
        _append_registry_record(self._iso_state_dir, {
            "run_id": self._run_id,
            "name": name,
            "pid": os.getpid(),
            "process_start": process_start(os.getpid()),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    def _verify_and_prune_registry(self) -> None:
        """Prune this run's registry entries only after the daemon confirms
        the object is gone (042 D2). A surviving object (daemon down, rm
        failure) keeps its entry so the next startup reaper retries. Never
        raises."""
        entries = _read_registry(self._iso_state_dir)
        if not entries:
            return
        remaining: list[dict] = []
        for entry in entries:
            if entry.get("run_id") != self._run_id:
                remaining.append(entry)
                continue
            name = entry.get("name")
            if not isinstance(name, str):
                remaining.append(entry)
                continue
            if entry.get("kind") == "network":
                gone = self._network_gone(name)
            else:
                gone = self._container_gone(name)
            if not gone:
                remaining.append(entry)
        _write_registry(self._iso_state_dir, remaining)

    def _container_gone(self, name: str) -> bool:
        """Thin delegation to the module-level confirm helper (050 D2)."""
        return _container_gone(self._runner, name)

    def _network_gone(self, name: str) -> bool:
        """Thin delegation to the module-level confirm helper (050 D2)."""
        return _network_gone(self._runner, name)

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
               result: SandboxExecutionResult, container: str,
               *, infra_reason: str | None = None) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline": request.pipeline or "",
            "step": request.step or "",
            "request_digest": _request_digest(request.argv, request.cwd, request.env),
            "backend": "docker",
            "container_id": container,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_seconds": result.duration_seconds,
        }
        if infra_reason is not None:
            record["infra_reason"] = infra_reason
        append_audit_record(self._iso_state_dir, record)


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
# Audit (host-side only)
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


def merge_egress_events(state_dir: Path, events_path: Path) -> int:
    """Merge a run's containerized-proxy enforcement events into the audit
    log (038, D6), then remove the events file. Returns the merged count."""
    if not events_path.is_file():
        return 0
    count = 0
    with events_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record["event"] = "egress_enforcement"
            append_audit_record(state_dir, record)
            count += 1
    try:
        events_path.unlink()
    except OSError:
        pass
    return count


# ---------------------------------------------------------------------------
# Orphan-container registry + reaper (030, D4) - host-side only
# ---------------------------------------------------------------------------


def process_start(pid: int) -> str | None:
    """``ps -o lstart= -p <pid>`` - the owning CLI process's start time. None
    when ``ps`` is unavailable or the pid is gone. Recorded at create time and
    re-read at reap time to defeat pid recycling."""
    try:
        res = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return res.stdout.strip()


def _read_registry(state_dir: Path) -> list[dict]:
    path = Path(state_dir) / CONTAINER_REGISTRY
    if not path.is_file():
        return []
    entries: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    except OSError:
        return []
    return entries


def _write_registry(state_dir: Path, entries: list[dict]) -> None:
    path = Path(state_dir) / CONTAINER_REGISTRY
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except OSError:
        pass


def _append_registry_record(state_dir: Path, record: dict) -> None:
    path = Path(state_dir) / CONTAINER_REGISTRY
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pid_alive_matches(pid: int, recorded_start: str | None) -> bool:
    """True when the recorded owning CLI process still owns the container: the
    pid exists and, when a start time was recorded, the process at that pid
    started at the same time (defeats pid recycling)."""
    if not _pid_alive(pid):
        return False
    if recorded_start is None:
        return True
    current = process_start(pid)
    if current is None:
        return True  # cannot verify the start time - never reap a live pid
    return current == recorded_start


def _container_gone(runner: CommandRunner, name: str) -> bool:
    """True only when the daemon confirms the container no longer exists.
    A nonzero daemon exit keeps the entry."""
    res = runner.run(
        ["docker", "ps", "-a", "--filter", f"name={name}",
         "--format", "{{.Names}}"], timeout=30)
    if res.exit_code != 0:
        return False
    return name not in res.stdout.splitlines()


def _network_gone(runner: CommandRunner, name: str) -> bool:
    """True only when the daemon reports the network is missing. Any
    other inspect failure (daemon unreachable) keeps the entry."""
    res = runner.run(
        ["docker", "network", "inspect", name], timeout=30)
    if res.exit_code == 0:
        return False
    return "No such network" in f"{res.stderr} {res.stdout}"



def reap_orphaned_containers(state_dir: str | Path,
                             runner: CommandRunner | None = None,
                             *, announce: bool = True) -> list[str]:
    """Startup orphan reaper (030/038, D4/D6): remove exec/proxy containers
    and the per-run internal network whose owning CLI process is gone (crashed
    before close()). Exact by construction - only engine registry names are
    touched, and a live owner (pid alive with the same start time) is left
    alone. A surviving object (rm failure) keeps its entry so the next
    startup reaper retries; only a daemon-confirmed absence prunes it (050
    D1). Best-effort: never raises."""
    runner = runner or _default_runner
    entries = _read_registry(Path(state_dir))
    if not entries:
        return []
    remaining: list[dict] = []
    reaped: list[str] = []
    for entry in entries:
        name = entry.get("name")
        kind = entry.get("kind", "container")
        if kind == "network":
            if not isinstance(name, str) or not _NETWORK_NAME_RE.match(name):
                remaining.append(entry)
                continue
            pid = entry.get("pid")
            if isinstance(pid, int) and _pid_alive_matches(
                    pid, entry.get("process_start")):
                remaining.append(entry)
                continue
            runner.run(["docker", "network", "rm", name], timeout=60)
            if not _network_gone(runner, name):
                remaining.append(entry)
                continue
            if announce:
                _announce(f"reaped orphaned sandbox network {name}")
            reaped.append(name)
            continue
        if not isinstance(name, str) or not _CONTAINER_NAME_RE.match(name):
            # Defense in depth: a corrupted registry entry is never an engine
            # container, so leave it (and its entry) untouched.
            remaining.append(entry)
            continue
        pid = entry.get("pid")
        if isinstance(pid, int) and _pid_alive_matches(
                pid, entry.get("process_start")):
            remaining.append(entry)
            continue
        runner.run(["docker", "rm", "-f", name], timeout=60)
        if not _container_gone(runner, name):
            remaining.append(entry)
            continue
        if announce:
            _announce(f"reaped orphaned sandbox container {name}")
        reaped.append(name)
    _write_registry(Path(state_dir), remaining)
    return reaped


# ---------------------------------------------------------------------------
# Eager startup (Q2) - probe → image → run -d --rm → provision all setups
# ---------------------------------------------------------------------------

def resolve_ignored_paths(repo: str | Path,
                         runner: CommandRunner | None = None,
                         walk_root: str | Path | None = None) -> frozenset[str]:
    """Resolve the repo's ignored-path set host-side (052) via
    ``git check-ignore --stdin -z`` - covers ``.gitignore``,
    ``.git/info/exclude``, and global excludes, with default index-aware
    semantics (a tracked file is never ignored even if a pattern matches).
    One git subprocess, all candidate paths batched over stdin. ``walk_root``
    is the tree whose paths are checked (the export at sync-back time, so
    gitignored dirs created mid-run are caught); ``repo`` is always the git
    repository the ignore rules come from. A missing git or a non-repo
    workspace is a loud failure (never silently degraded)."""
    repo = Path(repo)
    walk = Path(walk_root) if walk_root is not None else repo
    runner = runner or _default_runner
    candidates: list[str] = []
    for dirpath, dirnames, filenames in os.walk(walk):
        dirnames[:] = [x for x in dirnames if x != ".git"]
        rel_dir = Path(dirpath).relative_to(walk)
        prefix = rel_dir.as_posix()
        if prefix == ".":
            prefix = ""
        for x in dirnames:
            candidates.append(f"{prefix}/{x}" if prefix else x)
        for x in filenames:
            candidates.append(f"{prefix}/{x}" if prefix else x)
    if not candidates:
        return frozenset()
    payload = "\0".join(candidates) + "\0"
    res = runner.run(
        ["git", "-C", str(repo), "check-ignore", "--stdin", "-z"],
        input=payload, timeout=60)
    if res.exit_code not in (0, 1):
        raise SandboxError(
            "git check-ignore failed - the workspace must be a git repository "
            "with git on PATH (git said: "
            f"{res.stderr.strip() or res.stdout.strip()})")
    ignored: set[str] = set()
    for token in res.stdout.split("\0"):
        token = token.replace("\\", "/").rstrip("/")
        if token:
            ignored.add(token)
    return frozenset(ignored)


def isolation_state_dir(cfg: PipelineConfig) -> Path:
    """The resolved host-only isolation state dir (host-side caches + audit)."""
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


_MODULE_TAG = __name__.rsplit(".", 1)[-1]


def _announce(msg: str) -> None:
    """One-line console progress note for the eager startup phases. The tag is
    derived from the emitting module's own name (what a logging library's
    name field would record), never hardcoded."""
    print(f"[{_MODULE_TAG}] {msg}", flush=True)


def startup_sandbox(cfg: PipelineConfig, *, workspace: Path,
                    state_dir: str | Path,
                    runner: CommandRunner | None = None,
                    dial_rewrites: dict[str, str] | None = None) -> Sandbox:
    """Startup sequence (052): probe -> image -> reap -> resolve gitignored
    paths -> materialise export -> create host-side caches -> backend ->
    ensure_image -> create+start exec container. No setup runs here: each step
    entry provisions its own setup hook, fresh, into the export (052).

    ``state_dir`` (the run state dir) hosts the .git-free export under
    ``exports/<run-id>/``, materialised here (BR-2/BR-3). Host-side caches live
    under ``{isolation.state_dir}/caches/<name>``. The legacy workspace model is
    reachable only through the direct Sandbox API (DockerBackend without an
    export + requests carrying ``mount_set=None``), which is
    integration-tests-only."""
    seccomp_profile = resolved_seccomp_profile(cfg)
    _announce("probing docker daemon...")
    probe_docker(runner)
    profile = resolve_effective_profile(cfg.isolation)
    image = resolve_image_ref(profile.image)
    iso_state_dir = isolation_state_dir(cfg)
    # 030, D4: reap exec containers leaked by a previous run's crash before
    # creating anything new (exact registry + pid liveness, never heuristics).
    reap_orphaned_containers(iso_state_dir, runner)
    needs_network = any(step.network for step in cfg.steps)

    # 052: gitignored paths are the scratch definition - resolved host-side,
    # once, batched over stdin, and passed into the pure planners.
    ignored = resolve_ignored_paths(workspace, runner)

    # 052: host-side caches. Each declared cache resolves to
    # {isolation.state_dir}/caches/<name>, mkdir -p'd host-side (idempotent).
    cache_host_root = iso_state_dir / "caches"
    for cache in cfg.caches:
        (cache_host_root / cache).mkdir(parents=True, exist_ok=True)

    export_root = str(Path(state_dir) / "exports" / uuid.uuid4().hex)
    _announce("materialising workspace export...")
    ws = StepWorkspace.materialise(
        workspace, list(cfg.caches), sys.platform, export_root,
        state_dir=str(state_dir), excludes=list(cfg.sync_excludes),
        ignored=ignored, cache_host_root=str(cache_host_root))
    export_root = str(ws.export_root)
    cache_mounts = ws.mount_points()[1:]  # skip the export root itself
    _announce(f"export ready at {export_root}")

    backend = DockerBackend(
        image=image, workspace=str(workspace), iso_state_dir=str(iso_state_dir),
        profile=profile, runner=runner, network=needs_network,
        seccomp_profile=seccomp_profile,
        export_root=export_root, cache_mounts=cache_mounts,
        sync_excludes=list(cfg.sync_excludes),
        merge_on_conflict=list(cfg.merge_on_conflict),
        ignored=ignored,
        dial_rewrites=dial_rewrites)
    try:
        _announce(f"preparing sandbox image {image} (first pull can take a while)")
        backend.ensure_image()
        _announce("sandbox image ready")
        _announce("starting execution container...")
        backend.create_execution_container()
        _announce("execution container started")
        _announce("sandbox ready")
        return backend
    except BaseException:
        # 042 D3: a post-create startup failure must still reap the exec
        # container it created, never leak it to the reaper.
        backend.close()
        raise
