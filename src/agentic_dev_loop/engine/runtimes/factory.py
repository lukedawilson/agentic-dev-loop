"""Runtime factory (unit 10).

`openhands` needs no optional dependency and imports no driver package.
`claude` / `copilot` import their extras lazily and fail fast with the
install command when the extra is absent - never a degraded path.
"""

from __future__ import annotations

from agentic_dev_loop.core.config import PipelineConfig

from .errors import RuntimeSetupError
from .protocol import AgentRuntime, RuntimeContext

_INSTALL = {
    "claude": "pip " + "install agentic-dev-loop[claude]",
    "copilot": "pip " + "install agentic-dev-loop[copilot]",
}


def build_runtime(cfg: PipelineConfig, ctx: RuntimeContext) -> AgentRuntime:
    provider = cfg.provider
    if provider == "openhands":
        from .openhands_runtime import OpenHandsRuntime
        return OpenHandsRuntime(ctx)
    if provider == "claude":
        from .claude_runtime import ClaudeAgentRuntime
        return ClaudeAgentRuntime(ctx)
    if provider == "copilot":
        from .copilot_runtime import CopilotRuntime
        return CopilotRuntime(ctx)
    raise RuntimeSetupError(
        f"unknown provider {provider!r} (valid: openhands, claude, copilot)")
