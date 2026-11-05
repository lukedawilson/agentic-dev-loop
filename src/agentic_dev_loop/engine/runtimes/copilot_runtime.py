"""CopilotRuntime - openai-codex subprocess driver (unit 10).

No project config load and no custom tools: the only tool channel is the
engine's MCP catalog, routed through the sandbox bridge. Auth is the engine
hosted GitHub device flow; the token reaches the harness through the SDK
auth surface only and joins the run's redaction set.
"""

from __future__ import annotations

from typing import Any

from .async_bridge import AsyncBridge
from .bridge import SandboxToolBridge
from .catalog import MCP_TOOL_NAMES
from .copilot_auth import ensure_token
from .errors import (HarnessProcessError, RuntimeSetupError,
                     is_process_death)
from .harness import engine_agent_context, minimal_env
from .mcp_server import InProcessMcpServer
from .protocol import NudgePolicy, RuntimeContext, RuntimeResult, RuntimeStatus

INSTALL_HINT = "pip " + "install agentic-dev-loop[copilot]"
_MAX_TURNS_DEFAULT = 500


class CopilotRuntime:
    """One openai-codex harness session, driven turn by turn."""

    def __init__(self, ctx: RuntimeContext) -> None:
        self._ctx = ctx
        try:
            import openai_codex as sdk
        except ImportError as exc:
            raise RuntimeSetupError(
                "provider 'copilot' requires the openai-codex extra; "
                f"install it with `{INSTALL_HINT}`") from exc
        self._sdk = sdk
        self._token = ensure_token()
        self._bridge = SandboxToolBridge(ctx)
        self._mcp_server = InProcessMcpServer(self._bridge)
        self.options = self._build_options()
        self._factory = self._resolve_factory()

    def _resolve_factory(self):
        factory = getattr(self._sdk, "CodexSession", None) or getattr(
            self._sdk, "Codex", None)
        if factory is None:
            raise RuntimeSetupError(
                "openai-codex does not expose a session factory; the "
                "installed version is unsupported - reinstall a supported "
                f"version with `{INSTALL_HINT}`")
        return factory

    # -- construction -----------------------------------------------------
    def _max_turns(self) -> int:
        return self._ctx.max_iterations or _MAX_TURNS_DEFAULT

    def _build_options(self) -> dict:
        block = self._ctx.cfg.copilot
        values: dict[str, Any] = {
            "cwd": str(self._ctx.workspace),
            "env": minimal_env(),
            "system_prompt": engine_agent_context(self._ctx),
            "mcp_servers": {"agentic-dev-loop": self._mcp_server},
            "allowed_tools": list(MCP_TOOL_NAMES),
            "max_turns": self._max_turns(),
            "auth": self._token,
        }
        if block is not None and block.model is not None:
            values["model"] = block.model
        return values

    def _session(self):
        return self._factory(**self.options)

    # -- run --------------------------------------------------------------
    def run(self, message: str, *,
            nudge_policy: NudgePolicy | None = None) -> RuntimeResult:
        bridge = AsyncBridge("copilot-runtime")
        try:
            final_text, turns = bridge.run(
                lambda: self._drive(message, nudge_policy))
        except HarnessProcessError:
            self._raise_if_sticky()
            return self._result(RuntimeStatus.STUCK)
        except Exception as exc:
            self._raise_if_sticky()
            status = (RuntimeStatus.STUCK if is_process_death(exc)
                      else RuntimeStatus.ERROR)
            return self._result(status)
        finally:
            bridge.close()
        self._raise_if_sticky()
        status = (RuntimeStatus.TRUNCATED
                  if turns >= self._max_turns() else RuntimeStatus.FINISHED)
        return self._result(status, final_text)

    async def _drive(self, message: str,
                     nudge_policy: NudgePolicy | None):
        session = self._session()
        max_turns = self._max_turns()
        nudge_at = (max_turns - nudge_policy.turns_before_end
                    if nudge_policy is not None else None)
        turns = 0
        final_text: str | None = None
        try:
            await session.start()
            prompt = message
            while turns < max_turns:
                result = await session.run(prompt)
                text = _session_text(result)
                if text is not None:
                    final_text = text
                turns += 1
                if nudge_at is not None and turns == nudge_at:
                    prompt = nudge_policy.message
                    nudge_at = None
                    continue
                break
            return final_text, turns
        except Exception as exc:
            if is_process_death(exc):
                raise HarnessProcessError(str(exc)) from exc
            raise
        finally:
            close = getattr(session, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    def _raise_if_sticky(self) -> None:
        """030: a dead sandbox fails the run with no retry, before any status
        mapping can turn it into a retryable outcome."""
        sandbox = self._ctx.sandbox
        if sandbox is not None and sandbox.infra_error is not None:
            raise sandbox.infra_error

    def _result(self, status: RuntimeStatus,
                final_text: str | None = None) -> RuntimeResult:
        artifact = self._ctx.artifact
        redactions = self._ctx.redactions + (self._token.access_token,)
        if artifact is None:
            return RuntimeResult(status=status, final_text=final_text)
        from ..outputs import CapturedOutput

        captured = CapturedOutput.from_runtime_result(
            final_text, artifact, self._ctx.state_dir, redactions)
        if captured.is_err():
            return RuntimeResult(status=status, final_text=final_text,
                                 capture_error=captured.error.value)
        write_result = captured.unwrap().write()
        if write_result.is_err():
            raise RuntimeError(
                f"captured output write failed: {write_result.error.value}")
        return RuntimeResult(status=status,
                             final_text=captured.unwrap().raw_text)

    @property
    def extra_redactions(self) -> tuple[str, ...]:
        return (self._token.access_token,)

    def close(self) -> None:
        self._bridge.close()


def _session_text(result) -> str | None:
    if isinstance(result, str):
        return result or None
    for attr in ("final_text", "text", "output", "message"):
        value = getattr(result, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return None
