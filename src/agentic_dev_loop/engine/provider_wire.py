"""Provider wire-format policy.

The openhands SDK serializes message content as a list of content blocks
whenever a model supports native tool calling. Most gateways accept that
shape; the Copilot gateway accepts the request but silently drops
ARRAY-encoded tool-result content (probe-proven 2026-11-06: string tool
content is answered, array tool content is invisible to the model - see
aidlc-docs/audit.md). The SDK's own remedy is its documented
``force_string_serializer`` field on LLM, so this module is POLICY ONLY:
which gateway base URL requires which wire shape. ``build_llm`` applies the
policy to every instance it builds. Nothing is monkeypatched.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_dev_loop.engine.copilot_auth import COPILOT_BASE_URL


@dataclass(frozen=True)
class WireAdapter:
    """The wire shape a gateway requires for message content."""

    name: str
    force_string_serializer: bool


copilot_strings = WireAdapter(name="copilot-strings", force_string_serializer=True)

WIRE_ADAPTERS: dict[str, WireAdapter] = {COPILOT_BASE_URL: copilot_strings}


def adapter_for(base_url: str | None) -> WireAdapter | None:
    """The adapter for a gateway base URL, or None for the SDK default shape."""
    if base_url is None:
        return None
    return WIRE_ADAPTERS.get(base_url.rstrip("/"))


class WireShapeError(RuntimeError):
    """A surfaced or outgoing wire shape that cannot carry a tool call.

    Structural, not retryable model behaviour: the request would fail the same
    way again, so the run fails fast naming the offending shape (FR-11.4)."""


def validate_finish_shape(finish_reason: str | None, tool_calls,
                          *, path: str = "chat") -> None:
    """D-1: ``finish_reason == 'tool_calls'`` implies a non-empty tool-call
    list (the opus-4.8 mis-shape)."""
    if finish_reason == "tool_calls" and not tool_calls:
        raise WireShapeError(
            f"wire shape violation ({path}): finish_reason='tool_calls' with "
            "an empty tool_calls list")


def validate_tool_call_pairing(messages) -> None:
    """D-2: every assistant tool call in the outgoing history has a matching
    tool result (the kimi array-drop blindness)."""
    results = {
        getattr(message, "tool_call_id", None)
        for message in messages
        if getattr(message, "role", None) == "tool"
    }
    dangling: list[str] = []
    for message in messages:
        if getattr(message, "role", None) != "assistant":
            continue
        for call in getattr(message, "tool_calls", None) or []:
            call_id = getattr(call, "id", None)
            if call_id is not None and call_id not in results:
                dangling.append(str(call_id))
    if dangling:
        raise WireShapeError(
            "wire shape violation (chat): assistant tool calls without a "
            f"tool result in the outgoing history: {sorted(dangling)}")


def validate_string_tool_results(messages, serialized,
                                 *, forced: bool) -> None:
    """D-3: when the string serializer is forced, a non-empty tool result must
    surface as a non-empty string, not vanish from the request."""
    if not forced:
        return
    if len(messages) != len(serialized):
        raise WireShapeError(
            "wire shape violation (chat): the serializer changed the message "
            f"count ({len(messages)} in, {len(serialized)} out); the forced "
            "string policy cannot be aligned positionally")
    for message, out in zip(messages, serialized):
        if getattr(message, "role", None) != "tool":
            continue
        parts = getattr(message, "content", None) or []
        text = "".join(
            part.text for part in parts
            if isinstance(getattr(part, "text", None), str))
        if not text.strip():
            continue
        content = out.get("content") if isinstance(out, dict) else None
        if not isinstance(content, str) or not content:
            raise WireShapeError(
                "wire shape violation (chat): forced string serialization "
                "produced no visible tool result content")


def validate_response_shape(response) -> None:
    """D-1 at the adapter boundary: inspect the surfaced response shape.

    Chat path: ``choices[0].finish_reason`` plus its message's tool_calls.
    Responses-path shapes that do not expose the same fields are left to the
    Code Generation parity probe (validated-model.md, D-5)."""
    raw = getattr(response, "raw_response", None)
    if raw is None:
        return
    choices = raw.get("choices") if isinstance(raw, dict) else getattr(raw, "choices", None)
    finish_reason = None
    tool_calls = None
    if choices:
        first = choices[0]
        if isinstance(first, dict):
            finish_reason = first.get("finish_reason")
            message = first.get("message") or {}
        else:
            finish_reason = getattr(first, "finish_reason", None)
            message = getattr(first, "message", None) or {}
        if isinstance(message, dict):
            tool_calls = message.get("tool_calls")
        else:
            tool_calls = getattr(message, "tool_calls", None)
    if finish_reason is None:
        finish_reason = getattr(raw, "finish_reason", None)
    if finish_reason is None:
        finish_reason = getattr(getattr(response, "message", None),
                                "finish_reason", None)
    if finish_reason is None:
        return
    if tool_calls is None:
        tool_calls = getattr(getattr(response, "message", None),
                             "tool_calls", None)
    validate_finish_shape(finish_reason, tool_calls)
