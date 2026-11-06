"""Provider wire-format policy tests.

The Copilot gateway silently drops ARRAY-encoded tool-result content
(probe-proven 2026-11-06, see aidlc-docs/audit.md). The engine fixes this
with the SDK's own documented ``force_string_serializer`` field: the policy
registry in provider_wire.py maps a gateway base URL to the wire shape it
requires, and build_llm applies that policy to every LLM instance it
builds. No monkeypatch is involved.
"""

from openhands.sdk import LLM as SDK_LLM
from openhands.sdk.llm.message import Message, TextContent

from agentic_dev_loop.core.config import LlmConfig
from agentic_dev_loop.engine import llm as engine_llm
from agentic_dev_loop.engine.copilot_auth import COPILOT_BASE_URL
from agentic_dev_loop.engine.provider_wire import (
    WIRE_ADAPTERS, adapter_for, copilot_strings)

KEY = "AGENT_ENGINE_TEST_KEY"


def _cfg(**over) -> LlmConfig:
    base = {"model": "openai/deepseek-v4-pro", "api_key_env": KEY}
    base.update(over)
    return LlmConfig.model_validate(base)


def _tool_message(text: str) -> Message:
    return Message(
        role="tool",
        name="terminal",
        tool_call_id="call-1",
        content=[TextContent(text=text)],
    )


class TestAdapterRegistry:
    def test_copilot_url_maps_to_string_policy(self):
        adapter = adapter_for(COPILOT_BASE_URL)
        assert adapter is copilot_strings
        assert adapter.force_string_serializer is True

    def test_trailing_slash_variant_maps_to_same_adapter(self):
        assert adapter_for(COPILOT_BASE_URL + "/") is copilot_strings

    def test_other_urls_have_no_adapter(self):
        assert adapter_for("https://api.deepseek.com") is None

    def test_none_url_has_no_adapter(self):
        assert adapter_for(None) is None

    def test_registry_holds_exactly_the_copilot_url(self):
        assert WIRE_ADAPTERS == {COPILOT_BASE_URL: copilot_strings}


class TestBuildLlmPolicy:
    def test_copilot_config_forces_string_serializer(self, monkeypatch):
        monkeypatch.setenv(KEY, "test-key")
        llm = engine_llm.build_llm(_cfg(
            model="openai/kimi-k3",
            base_url=COPILOT_BASE_URL,
        ))
        assert llm.force_string_serializer is True

    def test_non_copilot_config_leaves_serializer_auto(self, monkeypatch):
        monkeypatch.setenv(KEY, "test-key")
        llm = engine_llm.build_llm(_cfg(
            model="openai/deepseek-v4-pro",
            base_url="https://api.deepseek.com",
        ))
        assert llm.force_string_serializer is None

    def test_missing_base_url_leaves_serializer_auto(self, monkeypatch):
        monkeypatch.setenv(KEY, "test-key")
        llm = engine_llm.build_llm(_cfg())
        assert llm.force_string_serializer is None


class TestStringWirePolicy:
    """The end-to-end serialization shape each config produces.

    These are the pinning tests for the SDK seam: if the SDK ever stops
    honoring force_string_serializer, the copilot case below flips back to
    a list and this test fails loudly.
    """

    def test_copilot_kimi_tool_content_serializes_to_string(self):
        llm = SDK_LLM(
            model="openai/kimi-k3",
            api_key="test-key",
            base_url=COPILOT_BASE_URL,
            force_string_serializer=True,
        )
        out = llm.format_messages_for_llm([_tool_message("HELLO")])
        assert out[0]["content"] == "HELLO"
        assert isinstance(out[0]["content"], str)

    def test_non_copilot_gpt4o_tool_content_serializes_to_list(self):
        llm = SDK_LLM(
            model="openai/gpt-4o",
            api_key="test-key",
            base_url="https://api.openai.com",
            force_string_serializer=None,
        )
        out = llm.format_messages_for_llm([_tool_message("HELLO")])
        assert isinstance(out[0]["content"], list)

    def test_deepseek_defaults_to_string_via_registry(self):
        llm = SDK_LLM(
            model="openai/deepseek-v4-pro",
            api_key="test-key",
            base_url="https://api.deepseek.com",
            force_string_serializer=None,
        )
        out = llm.format_messages_for_llm([_tool_message("HELLO")])
        assert out[0]["content"] == "HELLO"
        assert isinstance(out[0]["content"], str)
