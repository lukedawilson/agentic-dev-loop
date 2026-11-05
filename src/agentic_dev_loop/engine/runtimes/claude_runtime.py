"""ClaudeAgentRuntime - claude-agent-sdk subprocess driver (unit 10).

Hardening is non-negotiable: no project/user settings, skills, memory or
hooks (``setting_sources=[]``), native tools off via a closed ``allowed_tools``
list and ``permission_mode="deny"``, and exactly one MCP server - the engine's
catalog. Subscription login is detected, never read or replayed; key mode
reads ANTHROPIC_API_KEY from the engine environment. Every tool call routes
through the ``SandboxToolBridge``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .async_bridge import AsyncBridge
from .bridge import SandboxToolBridge
from .catalog import MCP_TOOL_NAMES
from .errors import (HarnessProcessError, RuntimeSetupError,
                     is_process_death)
from .harness import engine_agent_context, minimal_env
from .mcp_server import build_sdk_mcp_server
from .protocol import NudgePolicy, RuntimeContext, RuntimeResult, RuntimeStatus

INSTALL_HINT = "pip " + "install agentic-dev-loop[claude]"
CREDENTIAL_STORE = Path(".claude") / ".credentials.json"
_MAX_TURNS_DEFAULT = 500


class ClaudeAgentRuntime:
    """One claude-agent-sdk harness session."""

    def __init__(self, ctx: RuntimeContext) -> None:
        self._ctx = ctx
        try:
            import claude_agent_sdk as sdk
        except ImportError as exc:
            raise RuntimeSetupError(
                "provider 'claude' requires the claude-agent-sdk extra; "
                f"install it with `{INSTALL_HINT}`") from exc
        self._sdk = sdk
        self._credential_env = self._resolve_auth()
        self._bridge = SandboxToolBridge(ctx)
        self._mcp_server = build_sdk_mcp_server(self._bridge)
        self.options = self._build_options()
        self._verify_hardening(self.options)

    @staticmethod
    def _verify_hardening(options) -> None:
        """Never a best-effort downgrade: a pinned SDK that cannot express the
        hardening fails construction."""
        checks = {
            "setting_sources": [],
            "permission_mode": "deny",
            "allowed_tools": list(MCP_TOOL_NAMES),
        }
        for attr, expected in checks.items():
            actual = getattr(options, attr, None)
            if actual != expected:
                raise RuntimeSetupError(
                    f"claude-agent-sdk cannot enforce harness hardening: "
                    f"{attr} is {actual!r}, expected {expected!r}. Install a "
                    f"supported version with `{INSTALL_HINT}`")

    # -- construction -----------------------------------------------------
    def _resolve_auth(self) -> dict[str, str]:
        block = self._ctx.cfg.claude
        if block is None:  # config validation guarantees this
            raise RuntimeSetupError("provider 'claude' requires a claude: block")
        if block.login == "subscription":
            store = Path.home() / CREDENTIAL_STORE
            if not store.is_file():
                raise RuntimeSetupError(
                    "claude login not found: run `claude login` in your "
                    "terminal, then re-run")
            return {}
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeSetupError(
                "claude login mode 'key' requires ANTHROPIC_API_KEY in the "
                "engine environment")
        return {"ANTHROPIC_API_KEY": key}

    def _max_turns(self) -> int:
        block = self._ctx.cfg.claude
        if block is not None and block.max_turns is not None:
            return block.max_turns
        return self._ctx.max_iterations or _MAX_TURNS_DEFAULT

    def _build_options(self) -> Any:
        block = self._ctx.cfg.claude
        values = {
            "setting_sources": [],
            "allowed_tools": list(MCP_TOOL_NAMES),
            "permission_mode": "deny",
            "mcp_servers": {"agentic-dev-loop": self._mcp_server},
            "system_prompt": engine_agent_context(self._ctx),
            "cwd": str(self._ctx.workspace),
            "env": minimal_env(self._credential_env),
            "max_turns": self._max_turns(),
        }
        if block is not None and block.model is not None:
            values["model"] = block.model
        options_cls = getattr(self._sdk, "ClaudeAgentOptions", None)
        if options_cls is None:
            raise RuntimeSetupError(
                "claude-agent-sdk does not expose ClaudeAgentOptions; the "
                "installed version is unsupported - reinstall a supported "
                f"version with `{INSTALL_HINT}`")
        return options_cls(**values)

    # -- run --------------------------------------------------------------
    def run(self, message: str, *,
            nudge_policy: NudgePolicy | None = None) -> RuntimeResult:
        bridge = AsyncBridge("claude-runtime")
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
        client = self._sdk.ClaudeSDKClient(options=self.options)
        max_turns = self._max_turns()
        nudge_at = (max_turns - nudge_policy.turns_before_end
                    if nudge_policy is not None else None)
        turns = 0
        final_text: str | None = None
        nudge_fired = False
        try:
            await client.connect()
            await client.query(message)
            while True:
                async for response in client.receive_response():
                    name = type(response).__name__
                    text = _assistant_text(response)
                    if name == "ResultMessage":
                        if text is not None:
                            final_text = text
                        continue
                    if text is None:
                        continue
                    final_text = text
                    turns += 1
                    if nudge_at is not None and turns == nudge_at:
                        await client.query(nudge_policy.message)
                        nudge_fired = True
                        break
                if nudge_at is None or not nudge_fired:
                    break
                nudge_at = None
                nudge_fired = False
        finally:
            close = getattr(client, "disconnect", None)
            if close is not None:
                await close()
        return final_text, turns

    def _raise_if_sticky(self) -> None:
        """030: a dead sandbox fails the run with no retry, before any status
        mapping can turn it into a retryable outcome."""
        sandbox = self._ctx.sandbox
        if sandbox is not None and sandbox.infra_error is not None:
            raise sandbox.infra_error

    def _result(self, status: RuntimeStatus,
                final_text: str | None = None) -> RuntimeResult:
        artifact = self._ctx.artifact
        if artifact is None:
            return RuntimeResult(status=status, final_text=final_text)
        from ..outputs import CapturedOutput

        captured = CapturedOutput.from_runtime_result(
            final_text, artifact, self._ctx.state_dir, self._ctx.redactions)
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
        return tuple(self._credential_env.values())

    def close(self) -> None:
        self._bridge.close()


def _assistant_text(response) -> str | None:
    """The assistant text carried by an SDK response message, if any."""
    name = type(response).__name__
    if name == "ResultMessage":
        text = getattr(response, "result", None)
        return text if isinstance(text, str) and text.strip() else None
    if name != "AssistantMessage":
        return None
    blocks = getattr(response, "content", None) or []
    texts = [getattr(block, "text", None) for block in blocks
             if getattr(block, "type", None) in (None, "text")]
    texts = [text for text in texts if isinstance(text, str)]
    return "\n".join(texts) if texts else None
