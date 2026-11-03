"""build_llm tests. LLM is an SDK network class - mocking it is permitted."""

from unittest.mock import patch

import pytest
from openhands.sdk.llm.message import Message, MessageToolCall, TextContent
from openhands.sdk.llm.utils.model_features import get_features

from agentic_dev_loop.core.config import LlmConfig
from agentic_dev_loop.engine.llm import build_llm

KEY = "AGENT_ENGINE_TEST_KEY"


def _cfg(**kw):
    return LlmConfig(**({"model": "openai/deepseek-v4-pro", "api_key_env": KEY} | kw))


class TestBuildLlm:
    def test_missing_api_key_env_raises_naming_the_var(self, monkeypatch):
        """Fail fast naming the missing var - never fall back to another key."""
        monkeypatch.delenv(KEY, raising=False)
        with pytest.raises(RuntimeError, match=KEY):
            build_llm(_cfg())

    def test_builds_llm_from_config(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        with patch("agentic_dev_loop.engine.llm.LLM") as mock_cls:
            result = build_llm(_cfg(base_url="https://api.deepseek.com"))

        assert result is mock_cls.return_value
        kw = mock_cls.call_args.kwargs
        assert kw["model"] == "openai/deepseek-v4-pro"
        assert kw["api_key"] == "sk-test"
        assert kw["base_url"] == "https://api.deepseek.com"
        assert "extra_body" not in kw  # dead config - the SDK silently drops it
        assert kw["timeout"] is None  # opinionated no-timeout default

    def test_timeout_passed_through_when_set(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        with patch("agentic_dev_loop.engine.llm.LLM") as mock_cls:
            build_llm(_cfg(timeout=30))

        assert mock_cls.call_args.kwargs["timeout"] == 30

    def test_builds_llm_from_profile_cfg(self, monkeypatch):
        """054 (D6): a named profile is a full LlmConfig - model, endpoint,
        key, effort all come from the profile, never the default block."""
        monkeypatch.setenv("REVIEW_KEY", "sk-review")
        with patch("agentic_dev_loop.engine.llm.LLM") as mock_cls:
            build_llm(LlmConfig(
                model="openai/deepseek-v4-pro",
                base_url="https://api.deepseek.com",
                api_key_env="REVIEW_KEY",
                reasoning_effort="xhigh"))

        kw = mock_cls.call_args.kwargs
        assert kw["model"] == "openai/deepseek-v4-pro"
        assert kw["api_key"] == "sk-review"
        assert kw["base_url"] == "https://api.deepseek.com"
        assert kw["reasoning_effort"] == "xhigh"


class TestReasoningEffortResolution:
    """054 (D6): step value wins, else the effective profile's value, else
    off. reasoning_effort is always passed (None when unset), so the SDK
    "high" default can never engage."""

    def test_step_override_wins_over_profile(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        with patch("agentic_dev_loop.engine.llm.LLM") as mock_cls:
            build_llm(_cfg(reasoning_effort="low"), reasoning_effort="xhigh")

        assert mock_cls.call_args.kwargs["reasoning_effort"] == "xhigh"

    def test_profile_value_applies_without_step_override(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        with patch("agentic_dev_loop.engine.llm.LLM") as mock_cls:
            build_llm(_cfg(reasoning_effort="medium"))

        assert mock_cls.call_args.kwargs["reasoning_effort"] == "medium"

    def test_unset_everywhere_passes_none(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        with patch("agentic_dev_loop.engine.llm.LLM") as mock_cls:
            build_llm(_cfg())

        assert mock_cls.call_args.kwargs["reasoning_effort"] is None

    def test_none_profile_value_is_passed_through(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        with patch("agentic_dev_loop.engine.llm.LLM") as mock_cls:
            build_llm(_cfg(reasoning_effort="none"))

        assert mock_cls.call_args.kwargs["reasoning_effort"] == "none"


class TestReasoningPassback:
    """054 (D8): reasoning passback is config-gated, never model-name-gated.
    The engine does not consult SEND_REASONING_CONTENT_MODELS; the gate is
    the LLM instance's engine-configured reasoning_effort."""

    def test_deepseek_v4_pro_no_longer_name_list_registered(self):
        """Shim 1 is removed: the engine no longer mutates the SDK name
        list, so deepseek-v4-pro is not in it."""
        assert get_features("openai/deepseek-v4-pro").send_reasoning_content is False

    def test_gate_on_plain_assistant_message_keeps_reasoning(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        llm = build_llm(_cfg(reasoning_effort="medium"))
        msg = Message(role="assistant", content=[TextContent(text="391")],
                      reasoning_content="17*23 = 17*20 + 17*3")

        (out,) = llm.format_messages_for_llm([msg])

        assert out["reasoning_content"] == "17*23 = 17*20 + 17*3"

    def test_gate_on_multi_tool_call_turn_repairs_passback(self, monkeypatch):
        """OpenHands SDK splits a multi-tool-call turn into one assistant
        message per call and carries reasoning_content only on the first
        (agent.step: `... if i == 0 else None`). The engine repairs the
        serialized history: later messages in the turn borrow the first
        message's reasoning."""
        monkeypatch.setenv(KEY, "sk-test")
        llm = build_llm(_cfg(reasoning_effort="medium"))
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

        out = llm.format_messages_for_llm(messages)

        assert out[0]["reasoning_content"] == "need both files"
        assert out[2]["reasoning_content"] == "need both files"

    def test_gate_on_borrowed_reasoning_resets_at_user_turn(self, monkeypatch):
        """The repair borrows within a turn only: after a user message, a
        tool-call message without its own reasoning gets none."""
        monkeypatch.setenv(KEY, "sk-test")
        llm = build_llm(_cfg(reasoning_effort="medium"))
        messages = [
            Message(role="assistant", content=[TextContent(text="")],
                    tool_calls=[MessageToolCall(
                        id="call_1", name="read_file",
                        arguments='{"path":"a.md"}', origin="completion")],
                    reasoning_content="turn one reasoning"),
            Message(role="tool", tool_call_id="call_1", name="read_file",
                    content=[TextContent(text="a contents")]),
            Message(role="user", content=[TextContent(text="next question")]),
            Message(role="assistant", content=[TextContent(text="")],
                    tool_calls=[MessageToolCall(
                        id="call_2", name="read_file",
                        arguments='{"path":"b.md"}', origin="completion")],
                    reasoning_content=None),
        ]

        out = llm.format_messages_for_llm(messages)

        assert out[0]["reasoning_content"] == "turn one reasoning"
        assert "reasoning_content" not in out[3]

    def test_gate_off_with_reasoning_in_history_raises(self, monkeypatch):
        """054 (D12): absent effort = reasoning off; a history that still
        carries reasoning_content fails fast instead of sending a doomed
        request."""
        monkeypatch.setenv(KEY, "sk-test")
        llm = build_llm(_cfg())
        msg = Message(role="assistant", content=[TextContent(text="x")],
                      reasoning_content="some reasoning")

        with pytest.raises(RuntimeError, match="reasoning off"):
            llm.format_messages_for_llm([msg])

    def test_gate_off_none_effort_with_reasoning_raises(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        llm = build_llm(_cfg(reasoning_effort="none"))
        msg = Message(role="assistant", content=[TextContent(text="x")],
                      reasoning_content="some reasoning")

        with pytest.raises(RuntimeError, match="reasoning off"):
            llm.format_messages_for_llm([msg])

    def test_gate_off_error_names_model_and_fix(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        llm = build_llm(_cfg())
        msg = Message(role="assistant", content=[TextContent(text="x")],
                      reasoning_content="some reasoning")

        with pytest.raises(RuntimeError) as excinfo:
            llm.format_messages_for_llm([msg])

        text = str(excinfo.value)
        assert "openai/deepseek-v4-pro" in text
        assert "reasoning_effort" in text
        assert "non-thinking model" in text

    def test_gate_off_without_reasoning_sends_clean_request(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        llm = build_llm(_cfg())
        msg = Message(role="assistant", content=[TextContent(text="x")])

        (out,) = llm.format_messages_for_llm([msg])

        assert "reasoning_content" not in out
