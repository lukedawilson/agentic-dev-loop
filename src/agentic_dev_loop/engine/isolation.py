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
import sys
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol

from agentic_dev_loop.core.config import IsolationConfig, PipelineConfig
from .egress_policy import EgressPolicy
from .egress_proxy import EgressProxy
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

# 030 infra-failure classifier + orphan-container reaper (D1/D4).
_EIO_PATTERN = re.compile(r"input/output error|I/O error", re.IGNORECASE)
CONTAINER_REGISTRY = "container-registry.jsonl"
_CONTAINER_NAME_RE = re.compile(r"^[0-9a-f]{32}-exec(-[0-9a-f]{16})?(-net)?$")


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
    proxy: EgressProxy | None = None
    started: bool = False


def _proxy_url(port: int) -> str:
    """The container-reachable URL for a host-loopback proxy (ID-2): the
    host-gateway mapping, never 127.0.0.1 (which is the container itself)."""
    return f"http://host.docker.internal:{port}"


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


def repair_venv_shebangs(venv_dir: str | Path, *,
                           state_dir: str | Path,
                           export_root: str | Path) -> None:
    """Repair stale export-path shebangs in a sandbox venv (028).

    Each run materialises a fresh export under ``state_dir/exports/<uuid>/``,
    so a venv built by an earlier run carries console-script shebangs that bake
    that (now dead) export path. Rewrite every regular file under
    ``<venv_dir>/bin/`` whose first line starts with ``#!`` and points at a
    dead ``state_dir/exports/`` path to the current run's venv python
    (``{export_root}/.venv-sandbox/bin/python``). Symlinks and files without a
    shebang are left alone; idempotent; host-side, before any exec.
    """
    bin_dir = Path(venv_dir) / "bin"
    if not bin_dir.is_dir():
        return
    target = str(Path(export_root) / SANDBOX_VENV_DIR / "bin" / "python")
    stale_prefix = (Path(state_dir) / "exports").as_posix() + "/"
    for entry in sorted(bin_dir.iterdir()):
        if entry.is_symlink() or not entry.is_file():
            continue
        try:
            with entry.open("rb") as fh:
                first = fh.readline(256)
        except OSError:
            continue
        if not first.startswith(b"#!"):
            continue
        line = first.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
        interpreter = line[2:].strip()
        if not interpreter or interpreter == target:
            continue
        if not interpreter.replace("\\", "/").startswith(stale_prefix):
            continue
        _rewrite_shebang(entry, target)


def _rewrite_shebang(path: Path, target: str) -> None:
    """Replace only the first line of a regular file, preserving the rest."""
    data = path.read_bytes()
    nl = data.find(b"\n")
    if nl == -1:
        return
    body = data[nl + 1:]
    path.write_bytes(f"#!{target}\n".encode("utf-8") + body)


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
                          seccomp_profile: str | None = None,
                          mount_set: MountSet | None = None,
                          export_root: str | None = None,
                          cache_mounts: list[tuple[str, str, str]] | None = None,
                          egress_proxy: str | None = None,
                          name: str | None = None) -> list[str]:
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
        name or exec_container_name(run_id, network),
        "--label", "agentic-dev-loop=true",
        "--label", f"agentic-dev-loop.run={run_id}",
        "--network", "bridge" if network else "none",
    ]
    if network:
        # unit-5 (ID-2): network-on containers reach the engine-run egress
        # proxy via the host-gateway mapping - one mechanism on every platform.
        payload += ["--add-host", "host.docker.internal:host-gateway"]
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
        # Declared cache paths bind-mount rw from the real workspace at the
        # same relative paths inside the export (BR-3/F2); the relocated
        # sandbox venv (022) maps into the export's `.venv-sandbox`.
        for source, target, _mode in (cache_mounts or []):
            payload += ["-v", f"{source}:{target}"]
        if sandbox_venv_dir is not None:
            payload += ["-v", f"{sandbox_venv_dir}:{root}/{SANDBOX_VENV_DIR}"]
        workdir = root
    else:
        root = workspace
        payload.extend(_sandbox_mounts(workspace, sandbox_venv_dir))
        workdir = workspace
    payload.extend([
        "--workdir", workdir,
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
    """Docker-based sandbox: a persistent network-none execution container per
    run, plus - when any step declares ``network: true`` - a second
    bridge-network container (eagerly created + started, Q2). ``docker exec``
    per request routes to the container matching its network mode; both are
    destroyed on close(). The injected ``runner`` callable is the unit-test
    seam."""

    def __init__(self, *, image: str, workspace: str, iso_state_dir: str,
                 profile: EffectiveProfile, runner: CommandRunner | None = None,
                 network: bool = False, sandbox_venv_dir: str | None = None,
                 seccomp_profile: str | None = None,
                 export_root: str | None = None,
                 cache_mounts: list[tuple[str, str, str]] | None = None,
                 sync_excludes: list[str] | None = None,
                 merge_on_conflict: list[str] | None = None):
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
        self._baseline: dict[str, str] = {}
        # The profile-keyed pool (D-1/BR-10): profile key -> running container.
        self._pool: dict[tuple[str, bool], _ProfileContainer] = {}
        if sandbox_venv_dir is not None:
            # 022: the relocated venv dir is a bind-mount source. On native
            # Linux, Docker auto-creates a missing source as root, which the
            # --user uid:gid containers then cannot write (the AE-SBX-009
            # ownership class). Pre-create it host-side so it is uid-owned.
            Path(sandbox_venv_dir).mkdir(parents=True, exist_ok=True)
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
        container (the .git-free export, whole-tree rw) that serves the venv
        probe (016) and the legacy default path; step execution then uses the
        profile-keyed pool (D-1). Without an export the legacy two-container
        model is retained (integration tests, no enforcement)."""
        if self._export_root is not None:
            self._create_base_export_container()
            return
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
        self._register_container(self._container)
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
        self._register_container(self._network_container)
        self._network_started = True

    def _create_base_export_container(self) -> None:
        """Create the base export container (network-none, whole-tree rw) and
        snapshot the export baseline for sync-back conflict detection (BR-4)."""
        base = MountSet(mounts=(Mount(path=".", mode="rw"),))
        payload = docker_create_payload(
            run_id=self._run_id, image=self._image, profile=self._profile,
            workspace=self._workspace, network=False,
            sandbox_venv_dir=self._sandbox_venv_dir,
            seccomp_profile=self._seccomp_profile,
            mount_set=base, export_root=self._export_root,
            cache_mounts=self._cache_mounts)
        res = self._runner.run(payload, timeout=60)
        if res.exit_code != 0:
            raise SandboxError(
                f"AE-SBX-010: container run failed: "
                f"{res.stderr.strip() or res.stdout.strip()}")
        self._register_container(self._container)
        self._started = True
        if not self._baseline:
            self._baseline = _snapshot(Path(self._export_root),
                                       self._cache_paths,
                                       self._sync_excludes)

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
        """Run the in-container venv probe and classify it (016 self-heal).
        With an export the venv is mounted at ``{export}/.venv-sandbox``, so
        the probe workdir follows the export root."""
        workdir = self._export_root or self._workspace
        res = self._runner.run(
            venv_probe_argv(self._container, workdir), timeout=30)
        return venv_probe_state(res.stdout, res.exit_code)

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
        if request.network and not prof.started:
            raise SandboxError(
                "AE-SBX-013: networked execution requested but no networked "
                "container is running - declare `network: true` on the step "
                "(docs/setup.md §3)")
        if request.network and prof.proxy is not None and not prof.proxy.is_alive:
            raise SandboxError(
                "AE-SBX-018: the egress proxy for this profile has stopped - "
                "failing closed (BR-11.3): no networked execution runs "
                "without egress enforcement")
        result = self._exec(request, prof.name)
        if mount_set.rw_paths() and self._export_root is not None:
            self._sync_back(mount_set)
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
        its per-policy egress proxy and is pointed at it via the host-gateway
        mapping (ID-2); the proxy enforces the allowlist (BR-5/BR-6)."""
        name = f"{self._run_id}-exec-{key[0][:16]}"
        if network:
            name += "-net"
        proxy: EgressProxy | None = None
        proxy_url: str | None = None
        if network and self._export_root is not None:
            proxy = EgressProxy(policy=policy)
            proxy_url = _proxy_url(proxy.start())
        payload = docker_create_payload(
            run_id=self._run_id, image=self._image, profile=self._profile,
            workspace=self._workspace, network=network,
            sandbox_venv_dir=self._sandbox_venv_dir,
            seccomp_profile=self._seccomp_profile,
            mount_set=mount_set, export_root=self._export_root,
            cache_mounts=self._cache_mounts, egress_proxy=proxy_url, name=name)
        res = self._runner.run(payload, timeout=60)
        if res.exit_code != 0:
            if proxy is not None:
                proxy.stop()
            raise SandboxError(
                f"AE-SBX-010: container run failed: "
                f"{res.stderr.strip() or res.stdout.strip()}")
        self._register_container(name)
        return _ProfileContainer(name=name, mount_set=mount_set,
                                 egress_policy=policy, proxy=proxy,
                                 started=True)

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

    def _sync_back(self, mount_set: MountSet) -> None:
        """Sync declared-rw export changes back to the real repo (BR-4/F1),
        git-aware, host-side, with the sandbox step already complete.

        029: a conflict on a declared ``merge_on_conflict:`` path is
        union-merged (host wins, export-only lines appended) and does not
        raise. A conflict on any other path - including a declared merge path
        one side deleted - is announced, absorbed (the host wins and the
        export is reset to it), and then still raises AE-SBX-016 scoped to
        those hard paths, so the next sync-back runs clean. Merge paths are
        union-merged first, before any hard-conflict raise."""
        report: SyncBackReport = sync_back(
            self._export_root, self._workspace, list(mount_set.rw_paths()),
            baseline=self._baseline, step_id=None,
            cache_paths=self._cache_paths, excludes=self._sync_excludes,
            merge_on_conflict=self._merge_on_conflict)

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
        })

    def close(self) -> None:
        """Best-effort stop + rm; idempotent (BR-8.3). With ``--rm`` the
        daemon may already have auto-removed the container by the time close()
        runs, so a nonzero stop/rm exit code ("no such container") is treated
        as success, never raised (D2). Every pooled profile container and its
        egress proxy are destroyed too (BR-10.2/BR-11.3)."""
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
            if prof.proxy is not None:
                prof.proxy.stop()
        self._pool.clear()
        self._prune_registry()

    def _register_container(self, name: str) -> None:
        """Record one successfully created exec container in the host registry
        (030, D4). Best-effort: a broken registry only disables reaping, it
        never fails the run."""
        _append_registry_record(self._iso_state_dir, {
            "run_id": self._run_id,
            "name": name,
            "pid": os.getpid(),
            "process_start": process_start(os.getpid()),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    def _prune_registry(self) -> None:
        """Drop this run's registry entries after close() removed the
        containers (030, D4). Tolerates a missing registry file."""
        entries = _read_registry(self._iso_state_dir)
        if not entries:
            return
        remaining = [e for e in entries if e.get("run_id") != self._run_id]
        _write_registry(self._iso_state_dir, remaining)

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


def reap_orphaned_containers(state_dir: str | Path,
                             runner: CommandRunner | None = None) -> None:
    """Startup orphan reaper (030, D4): remove exec containers whose owning
    CLI process is gone (crashed before close()). Exact by construction - only
    engine registry names are touched, and a live owner (pid alive with the
    same start time) is left alone. Best-effort: never raises."""
    runner = runner or _default_runner
    entries = _read_registry(Path(state_dir))
    if not entries:
        return
    remaining: list[dict] = []
    for entry in entries:
        name = entry.get("name")
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
        _announce(f"reaped orphaned sandbox container {name}")
    _write_registry(Path(state_dir), remaining)


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


_MODULE_TAG = __name__.rsplit(".", 1)[-1]


def _announce(msg: str) -> None:
    """One-line console progress note for the eager startup phases. The tag is
    derived from the emitting module's own name (what a logging library's
    name field would record), never hardcoded."""
    print(f"[{_MODULE_TAG}] {msg}", flush=True)


def startup_sandbox(cfg: PipelineConfig, *, workspace: Path,
                    state_dir: str | Path,
                    runner: CommandRunner | None = None,
                    refresh: bool | None = None) -> Sandbox:
    """Eager startup sequence (Q2): probe → image → materialise export →
    backend → ensure_image → create+start → pre-provision every declared setup
    (stamp-cached).

    ``state_dir`` (the run state dir) hosts the .git-free export under
    ``exports/<run-id>/``, materialised here (BR-2/BR-3). It is required:
    the runtime always resolves a mount set for steps, and enforcement needs
    the export (AE-SBX-017), so there is no legacy fallback through this
    entry point. The legacy workspace model is reachable only through the
    direct Sandbox API (DockerBackend without an export + requests carrying
    ``mount_set=None``), which is integration-tests-only."""
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
    sandbox_venv_dir = resolve_sandbox_venv_dir(
        cfg.isolation.sandbox_venv_dir if cfg.isolation else None, workspace)

    export_root = str(Path(state_dir) / "exports" / uuid.uuid4().hex)
    # 028: cache paths are pipeline-author data (``caches:``), plus the engine's
    # own sandbox venv when it is not relocated (016/022 behaviour - the venv
    # is a cache only once it exists). Declared-but-missing caches are
    # auto-created host-side (uid-owned, empty) before export planning, so the
    # missing-cache guard in plan_step_workspace stays a race guard only.
    cache_paths = list(cfg.caches)
    if sandbox_venv_dir is None and (workspace / SANDBOX_VENV_DIR).is_dir():
        if SANDBOX_VENV_DIR not in cache_paths:
            cache_paths.append(SANDBOX_VENV_DIR)
    for cache in cfg.caches:
        (workspace / cache).mkdir(parents=True, exist_ok=True)
    _announce("materialising workspace export...")
    ws = StepWorkspace.materialise(
        workspace, cache_paths, sys.platform, export_root,
        state_dir=str(state_dir), excludes=list(cfg.sync_excludes))
    export_root = str(ws.export_root)
    cache_mounts = ws.mount_points()[1:]  # skip the export root itself
    _announce(f"export ready at {export_root}")
    # Repair console-script shebangs that bake a previous run's dead export
    # path (028): a venv built by export A must execute its ``bin/*`` scripts
    # when mounted into export B.
    venv_dir = Path(sandbox_venv_dir) if sandbox_venv_dir else workspace / SANDBOX_VENV_DIR
    _announce("checking venv shebangs")
    repair_venv_shebangs(venv_dir, state_dir=state_dir, export_root=export_root)

    backend = DockerBackend(
        image=image, workspace=str(workspace), iso_state_dir=str(iso_state_dir),
        profile=profile, runner=runner, network=needs_network,
        sandbox_venv_dir=sandbox_venv_dir, seccomp_profile=seccomp_profile,
        export_root=export_root, cache_mounts=cache_mounts,
        sync_excludes=list(cfg.sync_excludes),
        merge_on_conflict=list(cfg.merge_on_conflict))
    _announce(f"preparing sandbox image {image} (first pull can take a while)")
    backend.ensure_image()
    _announce("sandbox image ready")
    _announce("starting execution container...")
    backend.create_execution_container()
    _announce("execution container started")
    if refresh is None:
        refresh = bool(cfg.isolation and cfg.isolation.refresh)
    for setup_cmd, stamp_key in _declared_setups(cfg, workspace):
        _announce(f"provisioning setup (stamp {stamp_key[:8]})")
        backend.provision(setup_cmd, stamp_key, str(iso_state_dir / "stamps"),
                          force=refresh)
    _announce("sandbox ready")
    return backend
