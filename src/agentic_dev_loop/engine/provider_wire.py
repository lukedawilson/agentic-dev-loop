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
