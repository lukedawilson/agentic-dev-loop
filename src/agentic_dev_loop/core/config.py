"""Pipeline YAML schema and loader. Strict: unknown keys fail at every level."""

from __future__ import annotations

import hashlib
import re
import warnings
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentic_dev_loop.core.paths import _has_wildcards, path_excluded, relative_parts

TERMINALS = frozenset({"success", "failure"})
KNOWN_SECRET_PROVIDERS = frozenset({"env"})
MOUNT_MODES = frozenset({"rw", "ro"})
# Host-only dirs a mount path may never name (022): they are masked in
# the sandbox with a read-only tmpfs, so declaring them would be a decoy.
HOST_ONLY_MOUNT_DIRS = frozenset({".venv"})
# The egress preset names the engine ships (BR-9). Config validates the
# name here; the target data lives in engine/presets.py (preset data,
# never mechanism - D-2).
KNOWN_EGRESS_PRESETS = frozenset({"package-registries"})
EGRESS_CLASSES = frozenset({"allowlist", "browser"})

# The neutral default section headings a brief step must emit (046). A
# consumer overrides them via `brief_inputs.sections`; the coverage gate
# and the embedded brief prompt both render from this list.
DEFAULT_BRIEF_SECTIONS = [
    "Repo rules digest",
    "Unit scope digest",
    "Environment facts",
    "Open risks",
]


def _validate_mount_path(path: str) -> str:
    """Validate one repo-relative mount path (BR-1.3).

    Rejects (loudly) an empty path, an absolute path, ``..`` traversal,
    any ``.git`` component, and any host-only dir (``.venv``). The path
    is normalised to forward-slash form and returned.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("mount path must be a non-empty string")
    p = path.strip()
    if p.startswith(("/", "\\")) or (len(p) > 1 and p[1] == ":"):
        raise ValueError(
            f"mount path {path!r} must be repo-relative (no absolute paths)")
    parts = [seg for seg in p.replace("\\", "/").split("/")
             if seg not in ("", ".")]
    for seg in parts:
        if seg == "..":
            raise ValueError(
                f"mount path {path!r} escapes the repo (no '..' traversal)")
        if seg == ".git":
            raise ValueError(
                f"mount path {path!r} must never name '.git'")
        if seg in HOST_ONLY_MOUNT_DIRS:
            raise ValueError(
                f"mount path {path!r} names the host-only dir {seg!r}")
    return "/".join(parts) if parts else "."


def _validate_declared_path(path: str, *, allow_wildcards: bool) -> str:
    """Validate one declared ``caches:``/``sync_excludes:`` entry (028).

    Same repo-relative contract as mount paths: non-empty, no absolute paths,
    no ``..`` traversal, no ``.git`` component, no host-only dir. Wildcard
    entries (``sync_excludes:`` only) skip wildcard segments in the literal
    checks; the literal segments still obey the same rules. The normalised
    forward-slash form is returned.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("declared path must be a non-empty string")
    p = path.strip()
    if p.startswith(("/", "\\")) or (len(p) > 1 and p[1] == ":"):
        raise ValueError(
            f"declared path {path!r} must be repo-relative (no absolute paths)")
    parts = [seg for seg in p.replace("\\", "/").split("/")
             if seg not in ("", ".")]
    for seg in parts:
        if allow_wildcards and any(ch in seg for ch in "*?["):
            continue
        if seg == "..":
            raise ValueError(
                f"declared path {path!r} escapes the repo (no '..' traversal)")
        if seg == ".git":
            raise ValueError(
                f"declared path {path!r} must never name '.git'")
        if seg in HOST_ONLY_MOUNT_DIRS:
            raise ValueError(
                f"declared path {path!r} names the host-only dir {seg!r}")
    return "/".join(parts) if parts else "."


def parse_egress_target(spec: str) -> tuple[str, int | None]:
    """Parse ``host``, ``host:port``, or ``scheme://host[:port]`` into
    ``(host, port)``. Malformed entries that can never match fail loud
    (BR-6.3)."""
    value = spec.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    if "/" in value or value == "":
        raise ValueError(
            f"malformed egress target {spec!r}: expected host or host:port")
    # IPv6 literal: [::1]:443
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            raise ValueError(f"malformed egress target {spec!r}: bad IPv6 literal")
        host = value[1:end]
        rest = value[end + 1:]
        if rest.startswith(":"):
            return host, parse_egress_port(spec, rest[1:])
        if rest:
            raise ValueError(f"malformed egress target {spec!r}")
        return host, None
    if value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        return host, parse_egress_port(spec, port)
    if value.count(":") > 1:
        raise ValueError(f"malformed egress target {spec!r}: ambiguous port")
    return value, None


def parse_egress_port(spec: str, port: str) -> int | None:
    """Validate a target port: numeric and within 1-65535 (BR-6.3)."""
    if port == "":
        raise ValueError(f"malformed egress target {spec!r}: empty port")
    if not port.isdigit():
        raise ValueError(f"malformed egress target {spec!r}: port {port!r} not numeric")
    n = int(port)
    if not 1 <= n <= 65535:
        raise ValueError(f"malformed egress target {spec!r}: port {n} out of range")
    return n


def _validate_egress_target(target: str) -> str:
    """Validate and canonicalise one egress target (BR-6.3/BR-8.4): non-empty,
    scheme stripped, host or host:port shape, port 1-65535. Malformed entries
    fail loud at load, so ``--dry-run`` is a true load-and-validate pass."""
    if not isinstance(target, str) or not target.strip():
        raise ValueError("egress target must be a non-empty string")
    host, port = parse_egress_target(target)
    if port is None:
        return host
    if ":" in host:  # IPv6 literal needs brackets before a port
        return f"[{host}]:{port}"
    return f"{host}:{port}"


class VerdictSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    pass_: str = Field(alias="pass")
    fail: str = Field(alias="fail")
    unverifiable: str | None = None
    env: str | None = None


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goto: str
    retry: bool = False  # True consumes an attempt; exhaustion routes to failure


RouteTarget = str | Route


def route_target(route: RouteTarget | None) -> str | None:
    if route is None:
        return None
    return route.goto if isinstance(route, Route) else route


class SecretSpec(BaseModel):
    """One ``secrets:`` ref - the locator only, never a value. ``from`` names
    the provider (``env`` first); ``key`` is that provider's locator (for
    ``env``, the host environment variable to read and the step env name to
    inject). Unknown keys fail at load (extra="forbid")."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    key: str

    @field_validator("from_")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        if v not in KNOWN_SECRET_PROVIDERS:
            raise ValueError(
                f"unknown secret provider {v!r} (known: "
                f"{sorted(KNOWN_SECRET_PROVIDERS)})")
        return v

    @field_validator("key")
    @classmethod
    def _nonempty_key(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("secret key must be a non-empty string")
        return v


class IsolationConfig(BaseModel):
    """Optional `isolation:` section - configures the always-on sandbox (BR-2).
    Absence means opinionated defaults, never unsandboxed behaviour. Unknown
    keys fail at load (BR-5). `state_dir` is HOST-ONLY (stamps + audit) and is
    never mounted into the sandbox (Q1)."""

    model_config = ConfigDict(extra="forbid")

    image: str | None = None            # per-pipeline override (KD-2 ref rules)
    refresh: bool = False               # force re-provision (NFR-5)
    memory: str | None = None           # override exec container memory
    cpus: float | None = None           # override exec container cpus
    allow_git_mutations: bool = False   # BR-6 knob
    state_dir: str = "~/.agentic-dev-loop/state"  # stamps + audit (host-only)
    # 022 relocation opt-in: an out-of-workspace host dir that is bind-mounted
    # at ``{ws}/.venv-sandbox`` (rw) in both the exec and provision containers,
    # keeping the workspace free of container venv artifacts. Unset keeps the
    # legacy workspace-local ``.venv-sandbox`` unchanged. Must resolve outside
    # the workspace - validated at startup, not load (the workspace is unknown
    # here).
    sandbox_venv_dir: str | None = None
    # 023/025: optional host path to a Docker seccomp profile JSON, or a
    # ``bundled:<name>`` sentinel resolved at load to a packaged profile
    # (e.g. ``bundled:chromium-userns``). When set, the exec container runs
    # with ``--security-opt seccomp=<path>`` in place of Docker's default
    # profile, plus ``--cap-add SYS_CHROOT`` (the profile gates chroot(2) on
    # exactly that capability). Used to allow Chromium's namespace sandbox
    # (userns syscalls) under --cap-drop ALL. An unknown sentinel name fails
    # at load naming the known bundled profiles; a raw path is validated by
    # Docker at container-create time, not at load.
    seccomp_profile: str | None = None

    @field_validator("memory")
    @classmethod
    def _check_memory(cls, v: str | None) -> str | None:
        if v is not None and re.fullmatch(r"\d+[kmg]", v) is None:
            raise ValueError(f"memory {v!r} must match ^\\d+[kmg]$")
        return v

    @field_validator("cpus")
    @classmethod
    def _check_cpus(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("cpus must be > 0")
        return v


class MountSpec(BaseModel):
    """One declared mount entry (unit-5): a repo-relative path plus its
    mode. ``ro`` is the default (BR-1.2: only declared outputs and caches
    are ``rw``); validation at construction rejects traversal, ``.git``,
    and host-only dirs (BR-1.3)."""
    model_config = ConfigDict(extra="forbid")

    path: str
    mode: str = "ro"

    @field_validator("path")
    @classmethod
    def _valid_path(cls, v: str) -> str:
        return _validate_mount_path(v)

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, v: str) -> str:
        if v not in MOUNT_MODES:
            raise ValueError(
                f"mount mode must be 'rw' or 'ro', got {v!r}")
        return v


class EgressSpec(BaseModel):
    """One declared egress policy (unit-5): an explicit class plus preset
    references and/or explicit targets. ``class: browser`` is the explicit
    broad-but-logged browser class (BR-7); the default is a strict
    allowlist (BR-8). Unknown preset names fail at load (BR-8.4)."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    class_: str | None = Field(default=None, alias="class")
    presets: list[str] = []
    targets: list[str] = []

    @field_validator("presets", "targets", mode="before")
    @classmethod
    def _coerce_list(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v

    @field_validator("class_")
    @classmethod
    def _known_class(cls, v: str | None) -> str | None:
        if v is not None and v not in EGRESS_CLASSES:
            raise ValueError(
                f"egress class must be 'allowlist' or 'browser', got {v!r}")
        return v

    @field_validator("presets")
    @classmethod
    def _known_presets(cls, v: list[str]) -> list[str]:
        for name in v:
            if name not in KNOWN_EGRESS_PRESETS:
                raise ValueError(
                    f"unknown egress preset {name!r} (known: "
                    f"{sorted(KNOWN_EGRESS_PRESETS)})")
        return v

    @field_validator("targets")
    @classmethod
    def _valid_targets(cls, v: list[str]) -> list[str]:
        return [_validate_egress_target(t) for t in v]


class BriefInputsSpec(BaseModel):
    """Optional ``brief_inputs:`` on an agent step (033): the closed input
    list a brief agent digests once per run. ``include`` is repo-relative
    paths/globs (wildcards allowed); ``env_facts`` are the stable environment
    facts handed to the agent verbatim so it never discovers them by running
    commands. Both accept a single string or a list; unknown keys fail at
    load."""

    model_config = ConfigDict(extra="forbid")

    include: list[str] = []
    env_facts: list[str] = []
    sections: list[str] = DEFAULT_BRIEF_SECTIONS

    @field_validator("include", "env_facts", "sections", mode="before")
    @classmethod
    def _coerce_list(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v

    @field_validator("include")
    @classmethod
    def _valid_include(cls, v: list[str]) -> list[str]:
        return [_validate_declared_path(p, allow_wildcards=True) for p in v]

    @field_validator("env_facts")
    @classmethod
    def _valid_env_facts(cls, v: list[str]) -> list[str]:
        for fact in v:
            if not isinstance(fact, str) or not fact.strip():
                raise ValueError(
                    "brief_inputs.env_facts entries must be non-empty strings")
        return v

    @field_validator("sections")
    @classmethod
    def _valid_sections(cls, v: list[str]) -> list[str]:
        for section in v:
            if not isinstance(section, str) or not section.strip():
                raise ValueError(
                    "brief_inputs.sections entries must be non-empty strings")
        if not v:
            raise ValueError(
                "brief_inputs.sections must be a non-empty list of headings")
        if len(set(v)) != len(v):
            raise ValueError(
                "brief_inputs.sections entries must be unique")
        return v


class StepConfig(BaseModel):
    """One pipeline step. Its type IS the key it carries: exactly one of
    `agent`, `action`, or `command` - empty values do not count, anything
    else fails to load. See the `kind` property (the single discriminator
    graph-building dispatches on)."""
    model_config = ConfigDict(extra="forbid")

    name: str
    agent: str | None = None
    action: str | None = None
    command: str | None = None  # mechanical step: exit code is the verdict
    params: dict[str, Any] = {}
    produces: list[str] = []  # hygiene-tracked, no verdict parsing
    artifact: str | None = None  # hygiene-tracked AND verdict-parsed
    verdicts: VerdictSpec | None = None
    max_iterations: int | None = None
    timeout: float | None = Field(default=None, gt=0)  # seconds, command steps only
    terminal_timeout: float | None = Field(default=None, gt=0)  # seconds, agent steps only (032)
    finalize_nudge_turns: int | None = Field(default=None, gt=0)  # agent steps only (035)
    brief_inputs: BriefInputsSpec | None = None  # agent steps only (033)
    custom_prompt: str | None = None  # agent steps only (040); file path resolved at load
    env: dict[str, str | None] | None = None  # NFR-1: null = inherit host value
    setup: str | None = None  # BR-13: command steps only
    setup_probe: str | None = None  # 045: consumer probe replacing the venv probe on a warm stamp; requires `setup`; probe text is NOT part of the stamp key (a probe-only change needs --refresh/--clean-stamps)
    forbidden_commands: list[str] = []  # banned-command regex deny-list (036 D1)
    network: bool = False  # per-stage egress (command/agent + sandbox actions; host-side actions reject at registry time)
    # unit-5: per-step mount granularity + egress policy. Empty mounts =
    # strict-by-default (BR-1.1); egress None = strict empty allowlist
    # (BR-8.3). Host-side built-in actions are refused these at registry
    # time (the schema cannot tell host-side from sandbox actions).
    mounts: list[MountSpec] = []
    egress: EgressSpec | None = None
    secrets: list[str] = []  # declared ref names; action steps reject
    on_pass: RouteTarget | None = None
    on_fail: RouteTarget | None = None
    on_unverifiable: RouteTarget | None = None
    on_env: RouteTarget | None = None

    @property
    def kind(self) -> str:
        """The step's type: 'agent', 'command', or 'action'. Which of the
        three keys is set is the ONLY signal; validation has already
        guaranteed exactly one is set and non-empty by the time any instance
        exists."""
        if self.agent is not None:
            return "agent"
        if self.command is not None:
            return "command"
        return "action"

    @field_validator("produces", mode="before")
    @classmethod
    def _coerce_produces(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v

    @field_validator("forbidden_commands", mode="before")
    @classmethod
    def _coerce_forbidden_commands(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v

    @field_validator("forbidden_commands")
    @classmethod
    def _valid_forbidden_commands(cls, v: list[str]) -> list[str]:
        for pattern in v:
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError(
                    "forbidden_commands entries must be non-empty strings")
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(
                    f"forbidden_commands entry {pattern!r} is not a valid "
                    f"regex: {e}") from e
        return v

    @field_validator("custom_prompt")
    @classmethod
    def _nonempty_custom_prompt(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("custom_prompt must be a non-empty string")
        return v

    @field_validator("setup_probe")
    @classmethod
    def _nonempty_setup_probe(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("setup_probe must be a non-empty string")
        return v

    @model_validator(mode="after")
    def _check_shape(self) -> "StepConfig":
        for key in ("agent", "action", "command"):
            value = getattr(self, key)
            if value is not None and not value.strip():
                raise ValueError(
                    f"step {self.name!r}: {key!r} must be a non-empty string"
                )
        if sum(k is not None for k in (self.agent, self.action, self.command)) != 1:
            raise ValueError(
                f"step {self.name!r}: exactly one of 'agent', 'action', or "
                "'command' is required"
            )
        if self.command is not None:
            for field, empty in (("artifact", None), ("verdicts", None),
                                 ("produces", []), ("params", {}),
                                 ("max_iterations", None)):
                if getattr(self, field) != empty:
                    raise ValueError(
                        f"step {self.name!r}: 'command' steps cannot set {field!r}"
                    )
        if (self.artifact is None) != (self.verdicts is None):
            raise ValueError(
                f"step {self.name!r}: 'artifact' and 'verdicts' must appear together"
            )
        if self.artifact is not None and self.produces:
            raise ValueError(
                f"step {self.name!r}: 'produces' and 'artifact' are mutually exclusive"
            )
        if self.timeout is not None and self.command is None:
            raise ValueError(
                f"step {self.name!r}: 'timeout' requires 'command'"
            )
        if self.terminal_timeout is not None and self.agent is None:
            raise ValueError(
                f"step {self.name!r}: 'terminal_timeout' requires 'agent'"
            )
        if self.finalize_nudge_turns is not None and self.agent is None:
            raise ValueError(
                f"step {self.name!r}: 'finalize_nudge_turns' requires 'agent'"
            )
        if self.finalize_nudge_turns is not None and self.max_iterations is None:
            raise ValueError(
                f"step {self.name!r}: 'finalize_nudge_turns' requires "
                "'max_iterations'"
            )
        if (self.finalize_nudge_turns is not None
                and self.finalize_nudge_turns >= self.max_iterations):
            raise ValueError(
                f"step {self.name!r}: 'finalize_nudge_turns' must be less "
                "than 'max_iterations'"
            )
        if self.brief_inputs is not None and self.agent is None:
            raise ValueError(
                f"step {self.name!r}: 'brief_inputs' requires 'agent'"
            )
        if self.custom_prompt is not None and self.agent is None:
            raise ValueError(
                f"step {self.name!r}: 'custom_prompt' requires 'agent'"
            )
        if (self.brief_inputs is not None
                and (self.artifact is not None or self.verdicts is not None)):
            raise ValueError(
                f"step {self.name!r}: 'brief_inputs' is only valid on a "
                "produces-only agent step"
            )
        if self.brief_inputs is not None and not self.produces:
            raise ValueError(
                f"step {self.name!r}: 'brief_inputs' requires 'produces'"
            )
        if self.setup is not None and self.command is None and self.agent is None:
            raise ValueError(
                f"step {self.name!r}: 'setup' is only allowed on 'command' or 'agent' steps"
            )
        if self.setup_probe is not None and self.setup is None:
            raise ValueError(
                f"step {self.name!r}: 'setup_probe' requires 'setup'"
            )
        # `network` is NOT rejected here: the schema can't tell a host-side
        # built-in action (e.g. `commit`, which must never take egress) from
        # a sandbox-executed consumer action (which may, like a command step).
        # That distinction is enforced once the registry is known - see
        # `validate_action_network` (020).
        if self.secrets and self.action is not None:
            raise ValueError(
                f"step {self.name!r}: 'secrets' is only allowed on 'agent' or "
                "'command' steps"
            )
        return self


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    base_url: str | None = None
    api_key_env: str
    timeout: float | None = None  # omitted → opinionated no-timeout default


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    extensions: list[str] = []
    additional_files: list[str] = []
    # Where consumer `extensions:` modules resolve (work item 020). Must live
    # OUTSIDE the workspace - the host refuses workspace-resident extension and
    # tool-handler modules, so agent-writable files are never imported by the
    # host process.
    extensions_dir: str = "~/.agentic-dev-loop/extensions"
    env_allowlist: list[str] = []
    llm: LlmConfig
    isolation: IsolationConfig | None = None  # None = always-on defaults
    state_dir: str = ".pr"  # default relocated to ~/.agentic-dev-loop/runs/<hash>/ at load
    secrets: dict[str, SecretSpec] = {}  # ref name -> locator; values never appear here
    max_attempts: int = 3
    agents_dir: str
    steps: list[StepConfig] = Field(min_length=1)
    # 021 supply-chain dependency dial: `locked` (default) = the human-owned
    # lockfile is the source of truth and the agent may not add dependencies;
    # `open` = the bootstrap protocol (the agent may add exact-pinned manifest
    # entries for the restore step to fetch). `lockfiles` names the pipeline's
    # declared lockfile(s) - injected into agent context and named by the
    # missing-dependency fail-fast.
    deps_policy: str = "locked"
    lockfiles: list[str] = []
    # 028: pipeline-declared toolchain caches (rw bind-mounts from the real
    # workspace) and run-wide sync-back excludes (scratch semantics: skipped
    # from both the export copy and sync-back). Both default to [] - absent
    # means "no toolchain caches beyond the engine's own sandbox venv" and
    # "no sync-back excludes".
    caches: list[str] = []
    sync_excludes: list[str] = []
    # 029: append-style logs that sync-back union-merges on a concurrent
    # edit instead of raising. Same declared-path vocabulary as
    # ``sync_excludes:``.
    merge_on_conflict: list[str] = []

    @field_validator("deps_policy")
    @classmethod
    def _known_deps_policy(cls, v: str) -> str:
        if v not in ("locked", "open"):
            raise ValueError(
                f"deps_policy must be 'locked' or 'open', got {v!r}")
        return v

    @field_validator("lockfiles", mode="before")
    @classmethod
    def _coerce_lockfiles(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v

    @field_validator("caches", "sync_excludes", "merge_on_conflict", mode="before")
    @classmethod
    def _coerce_declared_paths(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v

    @field_validator("caches")
    @classmethod
    def _valid_caches(cls, v: list[str]) -> list[str]:
        return [_validate_declared_path(c, allow_wildcards=False) for c in v]

    @field_validator("sync_excludes")
    @classmethod
    def _valid_sync_excludes(cls, v: list[str]) -> list[str]:
        return [_validate_declared_path(c, allow_wildcards=True) for c in v]

    @field_validator("merge_on_conflict")
    @classmethod
    def _valid_merge_on_conflict(cls, v: list[str]) -> list[str]:
        return [_validate_declared_path(m, allow_wildcards=True) for m in v]

    @field_validator("secrets", mode="before")
    @classmethod
    def _reject_value_shaped_refs(cls, v: Any) -> Any:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError(
                "'secrets' must be a mapping of ref name to "
                "{from: ..., key: ...}")
        for name, spec in v.items():
            if not isinstance(spec, dict):
                raise ValueError(
                    f"secret ref {name!r} must be a mapping "
                    "{from: ..., key: ...}, not a literal value - secret "
                    "values never belong in the pipeline yaml")
        return v

    @model_validator(mode="after")
    def _locked_requires_lockfiles(self) -> "PipelineConfig":
        if self.deps_policy == "locked" and not self.lockfiles:
            raise ValueError(
                "deps_policy 'locked' requires at least one entry in "
                "'lockfiles:' - name the lockfile(s) the locked boundary "
                "enforces, or set 'deps_policy: open'")
        return self

    @model_validator(mode="after")
    def _mounts_avoid_state_dir(self) -> "PipelineConfig":
        """BR-1.3: the host-only run-state dir is never a mount path.
        A relative ``state_dir`` (e.g. ``.pr``) lives inside the
        workspace and must be refused as a mount; the default relocated
        state_dir is absolute/outside the workspace, so relative mount
        paths can never collide with it.
        """
        sd = self.state_dir.strip().rstrip("/\\")
        if not sd or sd in ("/", "."):
            return self
        for step in self.steps:
            for m in step.mounts:
                mp = m.path.rstrip("/")
                if mp == sd or mp.startswith(sd + "/"):
                    raise ValueError(
                        f"step {step.name!r}: mount path {m.path!r} names "
                        f"the host-only state_dir {self.state_dir!r}")
        return self

    @model_validator(mode="after")
    def _caches_avoid_state_dir(self) -> "PipelineConfig":
        """028: a declared cache may never name (or sit under) the host-only
        run-state dir - it would bind-mount the engine's own state into the
        sandbox as a rw cache."""
        sd = self.state_dir.strip().rstrip("/\\")
        if not sd or sd in ("/", "."):
            return self
        sd_parts = relative_parts(sd)
        if not sd_parts:
            return self
        for cache in self.caches:
            cp = relative_parts(cache)
            if cp[:len(sd_parts)] == sd_parts:
                raise ValueError(
                    f"cache path {cache!r} names the host-only state_dir "
                    f"{self.state_dir!r}")
        return self

    @model_validator(mode="after")
    def _mounts_avoid_caches(self) -> "PipelineConfig":
        """028/BR-1.3: a step mount equal to or under a declared cache is
        rejected - the rw cache submount would silently defeat a declared
        ``ro`` mount there. A whole-tree ``.`` mount stays fine (the cache
        submount layers on top)."""
        cache_parts = [relative_parts(c) for c in self.caches if relative_parts(c)]
        if not cache_parts:
            return self
        for step in self.steps:
            for m in step.mounts:
                mp = relative_parts(m.path)
                for cp in cache_parts:
                    if mp[:len(cp)] == cp:
                        raise ValueError(
                            f"step {step.name!r}: mount path {m.path!r} names "
                            f"declared cache {('/'.join(cp))!r}")
        return self

    @model_validator(mode="after")
    def _excludes_avoid_caches(self) -> "PipelineConfig":
        """028: a literal sync-exclude equal to or under a declared cache is
        redundant (caches already behave as scratch) and fails loud. Wildcard
        excludes are skipped - their overlap is fuzzy, not a contradiction."""
        cache_parts = [relative_parts(c) for c in self.caches if relative_parts(c)]
        if not cache_parts:
            return self
        for exclude in self.sync_excludes:
            if _has_wildcards(exclude):
                continue
            ep = relative_parts(exclude)
            for cp in cache_parts:
                if ep[:len(cp)] == cp:
                    raise ValueError(
                        f"sync_excludes entry {exclude!r} is redundant: it is "
                        f"already excluded as declared cache "
                        f"{('/'.join(cp))!r}")
        return self

    @model_validator(mode="after")
    def _merge_avoid_excludes_and_caches(self) -> "PipelineConfig":
        """029: a literal merge_on_conflict entry equal to or under a literal
        sync_excludes entry or a literal caches entry is redundant - excluded
        and cached paths never sync, so they can never conflict. Wildcard
        merge entries are skipped (their overlap is fuzzy, 028 parity)."""
        cache_parts = [relative_parts(c) for c in self.caches if relative_parts(c)]
        exclude_parts = [relative_parts(e) for e in self.sync_excludes
                         if not _has_wildcards(e) and relative_parts(e)]
        for merge in self.merge_on_conflict:
            if _has_wildcards(merge):
                continue
            mp = relative_parts(merge)
            for cp in cache_parts:
                if mp[:len(cp)] == cp:
                    raise ValueError(
                        f"merge_on_conflict entry {merge!r} is redundant: it is "
                        f"already excluded as declared cache {('/'.join(cp))!r}")
            for ep in exclude_parts:
                if mp[:len(ep)] == ep:
                    raise ValueError(
                        f"merge_on_conflict entry {merge!r} is redundant: it is "
                        f"already excluded by sync_excludes entry "
                        f"{('/'.join(ep))!r}")
        return self

    @model_validator(mode="after")
    def _sandbox_venv_not_declared_cache(self) -> "PipelineConfig":
        """028: the sandbox venv is engine infrastructure, never author data.
        Declaring ``.venv-sandbox`` in ``caches:`` while the 022 relocation
        (``isolation.sandbox_venv_dir``) is set is a contradiction."""
        if self.isolation is None or self.isolation.sandbox_venv_dir is None:
            return self
        for cache in self.caches:
            if relative_parts(cache) == (".venv-sandbox",):
                raise ValueError(
                    "declaring '.venv-sandbox' in 'caches:' contradicts "
                    "'isolation.sandbox_venv_dir' - the sandbox venv is "
                    "relocated outside the workspace (022)")
        return self

    @model_validator(mode="after")
    def _warn_shadowed_artifacts(self) -> "PipelineConfig":
        """028: an artifact/produces path matching a sync-exclude would never
        sync back. Non-fatal warning (wildcard overlap is fuzzy, so a hard
        failure would false-positive)."""
        if not self.sync_excludes:
            return self
        for step in self.steps:
            for artifact in list(step.produces) + (
                    [step.artifact] if step.artifact is not None else []):
                if path_excluded(artifact, self.sync_excludes):
                    warnings.warn(
                        f"step {step.name!r}: {artifact!r} matches a "
                        "sync_excludes entry and will never sync back",
                        stacklevel=2)
        return self

    @model_validator(mode="after")
    def _check_graph(self) -> "PipelineConfig":
        names = [s.name for s in self.steps]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate step names: {dupes}")
        for n in names:
            if n in TERMINALS:
                raise ValueError(f"step name {n!r} collides with a reserved terminal")
        valid = set(names) | TERMINALS
        for step in self.steps:
            for route in (step.on_pass, step.on_fail, step.on_unverifiable,
                          step.on_env):
                target = route_target(route)
                if target is not None and target not in valid:
                    raise ValueError(
                        f"step {step.name!r}: unknown route target {target!r}"
                    )
        for step in self.steps:
            for ref in step.secrets:
                if ref not in self.secrets:
                    raise ValueError(
                        f"step {step.name!r}: secret ref {ref!r} is not "
                        "declared in the top-level 'secrets:' section")
        return self


def _custom_prompt_looks_like_path(value: str) -> bool:
    """040/D2: a value counts as path-shaped when it carries a path
    separator or a file extension (a dot). A path-shaped value that names no
    existing file is a hard error, never silent text."""
    return "/" in value or "\\" in value or Path(value).suffix != ""


def resolve_custom_prompt(value: str, pipeline_dir: Path, step_name: str) -> str:
    """Resolve a ``custom_prompt`` value (040/D2): an existing file
    (relative to the pipeline's directory, or absolute) is read as the
    prompt; any other value is literal text. A path-shaped value that names
    no file fails loud, naming the path."""
    candidate = Path(value)
    path = candidate if candidate.is_absolute() else pipeline_dir / candidate
    if path.is_file():
        return path.read_text(encoding="utf-8")
    if _custom_prompt_looks_like_path(value):
        raise ValueError(
            f"step {step_name!r}: 'custom_prompt' file not found: {value}")
    return value


def default_state_dir(pipeline_path: Path) -> str:
    """The new run-state default (Q1): checkpoint DB + last-error.md live OUTSIDE
    the workspace mount, under ~/.agentic-dev-loop/runs/<pipeline-hash>/ where
    pipeline-hash is the first 12 chars of the sha256 of the absolute
    pipeline-file path. Explicit `state_dir:` configs keep working unchanged."""
    digest = hashlib.sha256(
        str(Path(pipeline_path).resolve()).encode("utf-8")).hexdigest()[:12]
    return str(Path("~/.agentic-dev-loop/runs").expanduser() / digest)


def load_config(path: Path) -> PipelineConfig:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            f"pipeline config must be a mapping, got {type(data).__name__}: {path}"
        )
    if "state_dir" not in data:
        data["state_dir"] = default_state_dir(path)
    cfg = PipelineConfig.model_validate(data)
    pipeline_dir = path.resolve().parent
    for step in cfg.steps:
        if step.custom_prompt is not None:
            step.custom_prompt = resolve_custom_prompt(
                step.custom_prompt, pipeline_dir, step.name)
    return cfg
