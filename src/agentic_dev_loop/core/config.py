"""Pipeline YAML schema and loader. Strict: unknown keys fail at every level."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TERMINALS = frozenset({"success", "failure"})
KNOWN_SECRET_PROVIDERS = frozenset({"env"})


class VerdictSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    pass_: str = Field(alias="pass")
    fail: str = Field(alias="fail")
    unverifiable: str | None = None


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
    env: dict[str, str | None] | None = None  # NFR-1: null = inherit host value
    setup: str | None = None  # BR-13: command steps only
    network: bool = False  # per-stage egress (command/agent + sandbox actions; host-side actions reject at registry time)
    secrets: list[str] = []  # declared ref names; action steps reject
    on_pass: RouteTarget | None = None
    on_fail: RouteTarget | None = None
    on_unverifiable: RouteTarget | None = None

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
        if self.setup is not None and self.command is None:
            raise ValueError(
                f"step {self.name!r}: 'setup' is only allowed on 'command' steps"
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
            for route in (step.on_pass, step.on_fail, step.on_unverifiable):
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
    return PipelineConfig.model_validate(data)
