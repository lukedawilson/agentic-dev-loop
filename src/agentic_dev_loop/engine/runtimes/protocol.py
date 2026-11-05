"""Runtime protocol and the provider-independent result shape (unit 10).

Everything the engine consumes from a provider is declared here: one terminal
status, one result value object, the nudge policy, and the resolved context a
driver is constructed with. Drivers contain no resolution logic.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentic_dev_loop.core.config import PipelineConfig, StepConfig

    from ..isolation import Sandbox

# The 035 finalize-nudge text, unchanged across providers. {n} is the number
# of turns remaining when the nudge fires.
NUDGE_TEMPLATE = "{n} turns remain: produce your final response now"


class RuntimeStatus(enum.Enum):
    """A run's provider-independent terminal status."""

    FINISHED = "finished"
    ERROR = "error"
    STUCK = "stuck"
    TRUNCATED = "truncated"


@dataclass(frozen=True)
class RuntimeResult:
    """The only data the step runner consumes from a runtime.

    ``truncated`` is derived, never stored: the engine-wide rule is
    ``status != FINISHED``. ``capture_error`` carries today's
    "empty_response" channel when a step that captures produced no text.
    """

    status: RuntimeStatus
    final_text: str | None = None
    capture_error: str | None = None

    @property
    def truncated(self) -> bool:
        return self.status is not RuntimeStatus.FINISHED


@dataclass(frozen=True)
class NudgePolicy:
    """Turn-synchronous finalize nudge (035).

    ``message`` is the template rendered with ``turns_before_end`` - the same
    text the OpenHands turn loop injects.
    """

    turns_before_end: int
    message: str

    @classmethod
    def for_turns(cls, turns: int) -> "NudgePolicy":
        return cls(turns_before_end=turns, message=NUDGE_TEMPLATE.format(n=turns))


@dataclass
class RuntimeContext:
    """The engine-side bag a driver is constructed with.

    Every field is resolved by the step runner before construction, so a
    driver never resolves config, mounts, secrets or policy itself.
    """

    cfg: "PipelineConfig"
    workspace: Path
    state_dir: Path
    agents_dir: Path
    agent_name: str
    max_iterations: int
    step: "StepConfig | None" = None
    sandbox: "Sandbox | None" = None
    sandbox_cwd: Path | None = None
    secrets: dict[str, str] = field(default_factory=dict)
    engine_allowlist: list[str] | None = None
    llm: Any | None = None
    llms: dict[str, Any] = field(default_factory=dict)
    terminal_timeout: float | None = None
    network: bool = False
    mount_set: Any | None = None
    egress_policy: Any | None = None
    step_env: dict[str, str] | None = None
    secret_env: dict[str, str] | None = None
    forbidden_commands: tuple[Any, ...] | None = None
    allowlist: tuple[str, ...] | None = None
    viz_bus: Any | None = None
    agent_context: str | None = None

    @property
    def pipeline_name(self) -> str:
        return self.cfg.name

    @property
    def step_name(self) -> str | None:
        return self.step.name if self.step is not None else None

    @property
    def terminal_cwd(self) -> Path:
        return self.sandbox_cwd or self.workspace

    @property
    def artifact(self) -> str | None:
        step = self.step
        if step is None:
            return None
        if step.artifact is not None:
            return step.artifact
        if step.produces:
            return step.produces[0]
        return None

    @property
    def redactions(self) -> tuple[str, ...]:
        return tuple(self.secrets.values())


@runtime_checkable
class AgentRuntime(Protocol):
    """One provider harness, driven synchronously by the step runner."""

    def run(self, message: str, *,
            nudge_policy: NudgePolicy | None = None) -> RuntimeResult: ...

    def close(self) -> None: ...
