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
    merge_choices: bool = False


copilot_strings = WireAdapter(name="copilot-strings", force_string_serializer=True,
                              merge_choices=True)

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


def _choice_message_tool_calls(choice) -> list:
    """The tool_calls carried by one choice's message, or [] (dict or attr)."""
    message = choice.get("message") if isinstance(choice, dict) else getattr(choice, "message", None)
    if message is None:
        return []
    tool_calls = (message.get("tool_calls") if isinstance(message, dict)
                  else getattr(message, "tool_calls", None))
    return list(tool_calls) if tool_calls else []


def _choice_finish_reason(choice):
    if isinstance(choice, dict):
        return choice.get("finish_reason")
    return getattr(choice, "finish_reason", None)


def validate_response_shape(response) -> None:
    """D-1 at the adapter boundary: inspect the surfaced response shape.

    Chat path: any choice's ``finish_reason`` plus its message's tool_calls.
    The Copilot gateway returns MULTIPLE choices for Anthropic models (a
    narration-only choice with ``finish_reason='tool_calls'`` and empty
    tool_calls, plus one choice per real tool call), so the check scans ALL
    choices and only fails when NO choice carries tool calls. Responses-path
    shapes that do not expose these fields are left to the Code Generation
    parity probe (validated-model.md, D-5)."""
    raw = getattr(response, "raw_response", None)
    if raw is None:
        return
    choices = raw.get("choices") if isinstance(raw, dict) else getattr(raw, "choices", None)
    if choices:
        finish_reason = _choice_finish_reason(choices[0])
        tool_calls = []
        for choice in choices:
            tool_calls.extend(_choice_message_tool_calls(choice))
    else:
        finish_reason = getattr(raw, "finish_reason", None)
        tool_calls = None
    if finish_reason is None:
        finish_reason = getattr(getattr(response, "message", None),
                                "finish_reason", None)
    if finish_reason is None:
        return
    if tool_calls is None:
        tool_calls = getattr(getattr(response, "message", None),
                             "tool_calls", None)
    validate_finish_shape(finish_reason, tool_calls)


def merge_choices(response) -> None:
    """Collapse a multi-choice chat response into one merged choice.

    The Copilot gateway splits an Anthropic model's turn into multiple
    choices: a narration-only choice (prose, ``finish_reason='tool_calls'``
    but empty tool_calls) plus one choice per tool call. The SDK reads only
    ``choices[0]``, so this function (applied at the ``_transport_call``
    boundary, before the SDK reads the response) merges the narration content
    and every tool call onto ``choices[0]`` and drops the rest. A no-op when
    there is at most one choice or no tool calls anywhere."""
    choices = getattr(response, "choices", None)
    if not choices or len(choices) < 2:
        return
    content = None
    tool_calls = []
    for choice in choices:
        message = getattr(choice, "message", None)
        if message is None:
            continue
        if content is None:
            c = getattr(message, "content", None)
            if isinstance(c, str) and c.strip():
                content = c
        tool_calls.extend(_choice_message_tool_calls(choice))
    if not tool_calls:
        return
    first = choices[0]
    first_message = getattr(first, "message", None)
    if first_message is not None:
        if content is not None:
            setattr(first_message, "content", content)
        setattr(first_message, "tool_calls", tool_calls)
    response.choices = [first]
