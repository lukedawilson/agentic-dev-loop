"""LLM construction from pipeline config. Fail fast on a missing key -
never fall back to another env var (no silent fallbacks)."""

import os

from openhands.sdk import LLM
from openhands.sdk.llm.utils import model_features as _model_features

from agentic_dev_loop.core.config import LlmConfig

# Compat shim: OpenHands SDK 1.17 gates reasoning_content passback on a closed
# model-name list (SEND_REASONING_CONTENT_MODELS) that does not know
# deepseek-v4-pro. DeepSeek's thinking mode 400s on any multi-turn request
# whose history omits prior reasoning_content. Unlike force_string_serializer,
# the SDK exposes no instance-level override, so register the model here.
# Remove once the SDK lists the model or adds an override.
_DEEPSEEK_MODEL = "deepseek-v4-pro"
if _DEEPSEEK_MODEL not in _model_features.SEND_REASONING_CONTENT_MODELS:
    _model_features.SEND_REASONING_CONTENT_MODELS.append(_DEEPSEEK_MODEL)

# Compat shim 2: the SDK splits a multi-tool-call turn into one assistant
# message per call (Agent.step) and carries reasoning_content only on the
# first; Message.to_chat_dict also drops empty-string reasoning. DeepSeek's
# thinking mode 400s any request whose history contains reasoning_content
# but omits it from an assistant tool-call message ("The reasoning_content
# in the thinking mode must be passed back to the API"). Repair the
# serialized history: an assistant tool-call message without its own
# reasoning borrows the turn's last reasoning (reset on user turns), and a
# present-but-empty reasoning is still written (field presence matters).
# Remove once the SDK distributes reasoning_content to every message.
_ORIGINAL_FORMAT_MESSAGES = LLM.format_messages_for_llm
if not getattr(_ORIGINAL_FORMAT_MESSAGES, "_agentic_dev_loop_reasoning_repair", False):
    def _repair_reasoning_passback(self, messages: list) -> list[dict]:
        out = _ORIGINAL_FORMAT_MESSAGES(self, messages)
        last = None
        for msg, d in zip(messages, out):
            if msg.role == "user":
                last = None
            if msg.role == "assistant" and msg.tool_calls:
                rc = getattr(msg, "reasoning_content", None)
                if rc is not None:
                    last = rc
                if last is not None:
                    d["reasoning_content"] = last
        return out

    _repair_reasoning_passback._agentic_dev_loop_reasoning_repair = True
    LLM.format_messages_for_llm = _repair_reasoning_passback


def build_llm(cfg: LlmConfig, reasoning_effort: str | None = None) -> LLM:
    api_key = os.environ.get(cfg.api_key_env)
    if not api_key:
        raise RuntimeError(f"{cfg.api_key_env} environment variable is required")
    # 047 (D2/D7): an explicit per-step override wins, else the llm block's
    # value applies, else the field is omitted entirely and the SDK default
    # ("high") is in force. The reasoning_content shims above stay untouched.
    effort = reasoning_effort if reasoning_effort is not None else cfg.reasoning_effort
    kwargs = {
        "model": cfg.model,
        "api_key": api_key,
        "base_url": cfg.base_url,
        "timeout": cfg.timeout,  # policy: no LLM timeout unless explicitly set
    }
    if effort is not None:
        kwargs["reasoning_effort"] = effort
    return LLM(**kwargs)
