"""SDK version guard + shim canary - see COMPAT-SHIMS.md before touching.

The engine papers over OpenHands SDK reasoning-content bugs with import-time
shims (src/agentic_dev_loop/llm.py). Those shims go stale silently when the SDK
changes. These tests make the change loud in both directions:

- SDK upgraded outside the tested range → the version guard fails; re-verify
  every shim against COMPAT-SHIMS.md, then update the pin.
- SDK bug fixed in-place (shim now dead code) → the canary fails; remove the
  shim per its removal criterion in COMPAT-SHIMS.md.
"""

import importlib.metadata

from openhands.sdk.llm.message import Message, MessageToolCall, TextContent

from agentic_dev_loop.core.config import LlmConfig
from agentic_dev_loop.engine.llm import _ORIGINAL_FORMAT_MESSAGES, build_llm

KEY = "AGENT_ENGINE_TEST_KEY"
TESTED_OPENHANDS_SDK = "1.17"


def _cfg(**kw):
    return LlmConfig(**({"model": "openai/deepseek-v4-pro", "api_key_env": KEY} | kw))


def test_openhands_sdk_within_tested_range():
    version = importlib.metadata.version("openhands-sdk")
    assert version.startswith(f"{TESTED_OPENHANDS_SDK}."), (
        f"openhands-sdk {version} is outside the tested range "
        f"~={TESTED_OPENHANDS_SDK}.0 - the reasoning-content shims in "
        "src/agentic_dev_loop/llm.py target SDK 1.17.x bugs. Read "
        "COMPAT-SHIMS.md, re-verify each shim against the new SDK, remove "
        "any that are no longer needed, then update this pin."
    )


def test_canary_sdk_still_drops_reasoning_on_split_tool_call_turn(monkeypatch):
    """Canary: passes while the SDK bug shim 2 works around is still present;
    fails once the SDK fixes it - shim 2 is then dead code and must be
    removed per COMPAT-SHIMS.md."""
    monkeypatch.setenv(KEY, "sk-test")
    llm = build_llm(_cfg())
    messages = [
        Message(role="assistant", content=[TextContent(text="reading")],
                tool_calls=[MessageToolCall(
                    id="call_1", name="read_file",
                    arguments='{"path":"a.md"}', origin="completion")],
                reasoning_content="need both files"),
        Message(role="tool", tool_call_id="call_1", name="read_file",
                content=[TextContent(text="a contents")]),
        Message(role="assistant", content=[TextContent(text="")],
                tool_calls=[MessageToolCall(
                    id="call_2", name="read_file",
                    arguments='{"path":"b.md"}', origin="completion")],
                reasoning_content=None),
        Message(role="tool", tool_call_id="call_2", name="read_file",
                content=[TextContent(text="b contents")]),
    ]

    out = _ORIGINAL_FORMAT_MESSAGES(llm, messages)

    assert "reasoning_content" not in out[2], (
        "The SDK now carries reasoning_content on every assistant tool-call "
        "message - shim 2 in src/agentic_dev_loop/llm.py is dead code. Remove it "
        "and update COMPAT-SHIMS.md."
    )


def test_canary_sdk_terminal_seam_still_present():
    """Canary for the unit-3 terminal seam (COMPAT-SHIMS.md): passes while the
    SDK still exposes `TerminalTool.create(executor=...)` plus a callable
    registry factory; fails once the SDK drops/changes either - the
    SandboxTerminalExecutor seam is then dead and must be replaced with an
    agentic-dev-loop-owned terminal tool."""
    import inspect

    from openhands.sdk.tool import register_tool
    from openhands.tools.terminal import TerminalTool

    assert "executor" in inspect.signature(TerminalTool.create).parameters, (
        "The SDK no longer accepts `executor=` in TerminalTool.create - the "
        "SandboxTerminalExecutor seam in src/agentic_dev_loop/agents.py is dead. "
        "Replace it with an agentic-dev-loop-owned terminal tool and update "
        "COMPAT-SHIMS.md.")
    assert callable(register_tool), (
        "The SDK's register_tool no longer accepts a callable factory - the "
        "per-run terminal re-registration in agents.py is dead. Replace it and "
        "update COMPAT-SHIMS.md.")


def test_canary_sdk_run_loop_still_breaks_on_stuck():
    """Canary for the D3 fast-stop reliance (COMPAT-SHIMS.md): passes while
    the SDK's LocalConversation.run breaks on STUCK; fails once the run loop
    stops checking STUCK - SandboxTerminalExecutor's STUCK write (agents.py)
    is then dead and must be replaced with a supported stop mechanism."""
    import inspect

    from openhands.sdk.conversation.impl.local_conversation import LocalConversation

    src = inspect.getsource(LocalConversation.run)
    assert "ConversationExecutionStatus.STUCK" in src, (
        "The SDK's LocalConversation.run no longer checks STUCK in its break "
        "condition - the D3 fast-stop reliance in SandboxTerminalExecutor "
        "(agents.py) is dead. Adopt the SDK's supported stop mechanism and "
        "update COMPAT-SHIMS.md."
    )
