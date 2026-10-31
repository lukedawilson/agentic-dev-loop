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

    def test_reasoning_effort_omitted_when_unset(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        with patch("agentic_dev_loop.engine.llm.LLM") as mock_cls:
            build_llm(_cfg())

        assert "reasoning_effort" not in mock_cls.call_args.kwargs

    def test_reasoning_effort_passed_through_when_set(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        with patch("agentic_dev_loop.engine.llm.LLM") as mock_cls:
            build_llm(_cfg(reasoning_effort="medium"))

        assert mock_cls.call_args.kwargs["reasoning_effort"] == "medium"


class TestReasoningEffortResolution:
    """D7: the step's value wins, else the llm block's value, else unset
    (the SDK default "high" applies)."""

    def test_step_override_wins_over_block(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        with patch("agentic_dev_loop.engine.llm.LLM") as mock_cls:
            build_llm(_cfg(reasoning_effort="low"), reasoning_effort="xhigh")

        assert mock_cls.call_args.kwargs["reasoning_effort"] == "xhigh"

    def test_block_value_applies_without_step_override(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        with patch("agentic_dev_loop.engine.llm.LLM") as mock_cls:
            build_llm(_cfg(reasoning_effort="medium"))

        assert mock_cls.call_args.kwargs["reasoning_effort"] == "medium"

    def test_unset_everywhere_omits_the_field(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        with patch("agentic_dev_loop.engine.llm.LLM") as mock_cls:
            build_llm(_cfg())

        assert "reasoning_effort" not in mock_cls.call_args.kwargs


class TestReasoningContentShim:
    """DeepSeek thinking mode 400s on multi-turn requests unless prior
    reasoning_content is echoed back - but OpenHands SDK gates that passback
    on a closed model-name list (SEND_REASONING_CONTENT_MODELS) that does not
    know deepseek-v4-pro. agentic_dev_loop.engine.llm must register it (import-time
    shim, removable once the SDK lists the model or adds an override)."""

    def test_deepseek_v4_pro_registered_for_reasoning_passback(self):
        assert get_features("openai/deepseek-v4-pro").send_reasoning_content is True

    def test_format_messages_preserves_reasoning_content(self, monkeypatch):
        monkeypatch.setenv(KEY, "sk-test")
        llm = build_llm(_cfg())
        msg = Message(role="assistant", content=[TextContent(text="391")],
                      reasoning_content="17*23 = 17*20 + 17*3")

        (out,) = llm.format_messages_for_llm([msg])

        assert out["reasoning_content"] == "17*23 = 17*20 + 17*3"

    def test_multi_tool_call_turn_repairs_reasoning_passback(self, monkeypatch):
        """OpenHands SDK splits a multi-tool-call turn into one assistant
        message per call and carries reasoning_content only on the first
        (agent.step: `... if i == 0 else None`). DeepSeek's thinking mode
        400s a request whose history mixes reasoned and unreasoned
        assistant tool-call messages. The engine must repair the serialized
        history: later messages in the turn borrow the first message's
        reasoning."""
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

        out = llm.format_messages_for_llm(messages)

        assert out[0]["reasoning_content"] == "need both files"
        assert out[2]["reasoning_content"] == "need both files"

    def test_borrowed_reasoning_resets_at_user_turn(self, monkeypatch):
        """The repair borrows within a turn only: after a user message, a
        tool-call message without its own reasoning gets none."""
        monkeypatch.setenv(KEY, "sk-test")
        llm = build_llm(_cfg())
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
