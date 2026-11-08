"""LLM construction from pipeline config. Fail fast on a missing key -
never fall back to another env var (no silent fallbacks)."""

import os

from openhands.sdk import LLM

from agentic_dev_loop.core.config import LlmConfig
from agentic_dev_loop.engine.provider_wire import (adapter_for,
                                                   validate_response_shape,
                                                   validate_string_tool_results,
                                                   validate_tool_call_pairing)
from agentic_dev_loop.engine.secrets import (
    SecretResolutionError, resolve_secret_spec)

# 054 (D8): reasoning passback is config-gated, never name-list-gated. The
# SDK gates reasoning_content passback on a closed model-name list
# (SEND_REASONING_CONTENT_MODELS) the engine never consults; instead each
# LLM instance carries the engine-resolved effort on
# ``_agentic_dev_loop_reasoning_effort`` and the serializer below gates
# passback on it. Gate on = the effort is set and not "none"; gate off =
# reasoning is stripped and, when history still carries reasoning_content,
# a clear engine error fires before the doomed request is sent (D12).
_ORIGINAL_FORMAT_MESSAGES = LLM.format_messages_for_llm


def _reasoning_gate_on(llm: LLM) -> bool:
    """The engine-configured effort for this instance, if any. The SDK's own
    ``reasoning_effort`` field defaults to "high", so a direct (non-engine)
    instance would read as gate-on; the dedicated attribute is only set by
    ``build_llm``, keeping absent effort = reasoning off."""
    effort = getattr(llm, "_agentic_dev_loop_reasoning_effort", None)
    return effort is not None and effort != "none"


def _reasoning_stripped_error(llm: LLM) -> RuntimeError:
    """054 (D12): gate off while the conversation still carries
    reasoning_content - a request that strips it would 400 at the provider."""
    effort = getattr(llm, "_agentic_dev_loop_reasoning_effort", None)
    return RuntimeError(
        f"model {llm.model!r} is configured with reasoning off "
        f"(reasoning_effort={effort!r}) but the conversation history carries "
        "reasoning_content that would be stripped from the request. Set "
        "'reasoning_effort' (low/medium/high/xhigh) on the step or its llm "
        "profile, use 'none' only where the provider honors it, or use a "
        "non-thinking model."
    )


if not getattr(_ORIGINAL_FORMAT_MESSAGES, "_agentic_dev_loop_reasoning_repair", False):
    def _config_gated_reasoning_serializer(self, messages: list) -> list[dict]:
        """The engine-owned reasoning passback serializer (054 D8).

        Gate on: every assistant message that carries reasoning_content
        writes it back (the SDK only does so for listed models and only on
        the first message of a split tool-call turn), and a tool-call
        message without its own reasoning borrows the turn's last reasoning
        (reset on user turns). Gate off: reasoning is stripped from the
        outgoing request and, if history still carries reasoning_content,
        the D12 error fires instead of sending a doomed request.
        """
        out = _ORIGINAL_FORMAT_MESSAGES(self, messages)
        if not _reasoning_gate_on(self):
            for d in out:
                d.pop("reasoning_content", None)
            if any(getattr(m, "reasoning_content", None) for m in messages):
                raise _reasoning_stripped_error(self)
            validate_string_tool_results(
                messages, out, forced=bool(self.force_string_serializer))
            return out

        last = None
        for msg, d in zip(messages, out):
            if msg.role == "user":
                last = None
                continue
            if msg.role != "assistant":
                continue
            rc = getattr(msg, "reasoning_content", None)
            if msg.tool_calls:
                if rc is not None:
                    last = rc
                if last is not None:
                    d["reasoning_content"] = last
            elif rc:
                d["reasoning_content"] = rc
        validate_string_tool_results(
            messages, out, forced=bool(self.force_string_serializer))
        return out

    _config_gated_reasoning_serializer._agentic_dev_loop_reasoning_repair = True
    LLM.format_messages_for_llm = _config_gated_reasoning_serializer


_ORIGINAL_COMPLETION = LLM.completion
_ORIGINAL_RESPONSES = LLM.responses


def _wire_checked_completion(self, messages, *args, **kwargs):
    """D-2 before the request, D-1 on the surfaced response (chat path)."""
    validate_tool_call_pairing(messages)
    response = _ORIGINAL_COMPLETION(self, messages, *args, **kwargs)
    validate_response_shape(response)
    return response


def _wire_checked_responses(self, messages, *args, **kwargs):
    """D-2 before the request; D-1 where the Responses shape exposes it."""
    validate_tool_call_pairing(messages)
    response = _ORIGINAL_RESPONSES(self, messages, *args, **kwargs)
    validate_response_shape(response)
    return response


if not getattr(LLM.completion, "_agentic_dev_loop_wire_checked", False):
    _wire_checked_completion._agentic_dev_loop_wire_checked = True
    LLM.completion = _wire_checked_completion

if not getattr(LLM.responses, "_agentic_dev_loop_wire_checked", False):
    _wire_checked_responses._agentic_dev_loop_wire_checked = True
    LLM.responses = _wire_checked_responses


def resolve_api_key(cfg: LlmConfig, environ=None) -> str:
    """Resolve the configured host-side key: the existing env mechanism, or
    the file-backed secret spec. Missing values fail fast naming the fix."""
    if cfg.api_key_secret is not None:
        try:
            return resolve_secret_spec(cfg.api_key_secret, environ)
        except SecretResolutionError as e:
            raise RuntimeError(f"llm api_key_secret: {e}") from e
    api_key = os.environ.get(cfg.api_key_env)
    if not api_key:
        raise RuntimeError(f"{cfg.api_key_env} environment variable is required")
    return api_key


def build_llm(cfg: LlmConfig, reasoning_effort: str | None = None) -> LLM:
    api_key = resolve_api_key(cfg)
    # 054 (D6): a step's value wins, else the effective profile's value, else
    # off. reasoning_effort is always passed (None when unset) so the SDK
    # "high" default cannot engage; absent effort = reasoning off.
    effort = reasoning_effort if reasoning_effort is not None else cfg.reasoning_effort
    # Copilot gateway policy (provider_wire.py): the gateway silently drops
    # ARRAY-encoded tool-result content, so Copilot-configured instances
    # force the SDK's documented string content serializer. Every other
    # gateway keeps the SDK default (None = auto-detect by model registry).
    adapter = adapter_for(cfg.base_url)
    llm = LLM(
        model=cfg.model,
        api_key=api_key,
        base_url=cfg.base_url,
        timeout=cfg.timeout,  # policy: no LLM timeout unless explicitly set
        reasoning_effort=effort,
        extra_headers=dict(cfg.headers),
        force_string_serializer=adapter.force_string_serializer if adapter else None,
    )
    llm._agentic_dev_loop_reasoning_effort = effort
    return llm
