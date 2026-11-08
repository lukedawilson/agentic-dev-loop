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

import pytest

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
    """Canary: passes while the SDK bug the reasoning passback serializer
    works around is still present; fails once the SDK fixes it - the
    serializer is then dead code and must be removed per COMPAT-SHIMS.md."""
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
        "message - the reasoning passback serializer in "
        "src/agentic_dev_loop/engine/llm.py is dead code. Remove it and update "
        "COMPAT-SHIMS.md."
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


def test_canary_sdk_turn_level_pause_seam_still_present():
    """Canary for the 035 finalize-nudge turn loop seam (COMPAT-SHIMS.md):
    passes while the SDK still accepts ``callbacks`` on LocalConversation,
    passes agent-sourced events to them via ``agent.step(on_event=...)``, and
    breaks its run loop on PAUSED; fails once any of those disappear - the
    _TurnPauseController pause loop is then dead and must be replaced with the
    SDK's supported turn-level pause mechanism."""
    import inspect

    from openhands.sdk.conversation.impl.local_conversation import LocalConversation

    assert "callbacks" in inspect.signature(LocalConversation.__init__).parameters, (
        "The SDK's LocalConversation no longer accepts `callbacks=` - the 035 "
        "finalize-nudge turn loop seam in src/agentic_dev_loop/engine/stages.py "
        "is dead. Adopt the SDK's supported pause mechanism and update "
        "COMPAT-SHIMS.md.")
    run_src = inspect.getsource(LocalConversation.run)
    assert "on_event=self._on_event" in run_src, (
        "The SDK's LocalConversation.run no longer passes its composed callback "
        "to agent.step - the 035 callback seam no longer receives agent-sourced "
        "events. Update COMPAT-SHIMS.md.")
    assert "ConversationExecutionStatus.PAUSED" in run_src, (
        "The SDK's LocalConversation.run no longer breaks on PAUSED - the 035 "
        "finalize-nudge turn loop in src/agentic_dev_loop/engine/stages.py is dead. "
        "Adopt the SDK's supported pause mechanism and update COMPAT-SHIMS.md.")


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


def test_canary_sdk_wire_checks_still_wrap_completion_and_responses():
    """Canary for the unit-11 wire-shape shims (COMPAT-SHIMS.md): passes while
    ``LLM.completion`` and ``LLM.responses`` remain plain class attributes the
    engine can wrap, and both engine wrappers are installed. Fails once the SDK
    stops routing requests through those attributes (a rename, an instance-level
    attribute, or a built-in validator that makes the wrappers dead) - replace
    the shim and update COMPAT-SHIMS.md."""
    from openhands.sdk import LLM
    from agentic_dev_loop.engine import llm as engine_llm

    assert getattr(LLM.completion, "_agentic_dev_loop_wire_checked", False), (
        "LLM.completion is no longer the engine's wire-checked wrapper - the "
        "D-1/D-2 shim in src/agentic_dev_loop/engine/llm.py is dead. Replace "
        "it and update COMPAT-SHIMS.md.")
    assert getattr(LLM.responses, "_agentic_dev_loop_wire_checked", False), (
        "LLM.responses is no longer the engine's wire-checked wrapper - the "
        "D-1/D-2 shim in src/agentic_dev_loop/engine/llm.py is dead. Replace "
        "it and update COMPAT-SHIMS.md.")
    assert engine_llm._ORIGINAL_COMPLETION is not LLM.completion, (
        "The engine did not capture the SDK's original LLM.completion before "
        "wrapping - the shim would recurse. Update COMPAT-SHIMS.md.")


def test_canary_responses_wrapper_still_validates_the_surfaced_shape(
        monkeypatch):
    """The Responses-path wrapper must enforce D-1 on the surfaced response,
    same as the chat path. A failing assertion here means the wrapper no longer
    wraps the SDK entry point (COMPAT-SHIMS.md)."""
    from types import SimpleNamespace

    from openhands.sdk import LLM

    from agentic_dev_loop.engine.provider_wire import WireShapeError

    llm = LLM(model="openai/test-model", api_key="sk-test")
    malformed = SimpleNamespace(raw_response={
        "choices": [{"finish_reason": "tool_calls", "message": {}}]})
    monkeypatch.setattr(
        "agentic_dev_loop.engine.llm._ORIGINAL_RESPONSES",
        lambda self, messages, *a, **k: malformed)
    with pytest.raises(WireShapeError):
        llm.responses([])
