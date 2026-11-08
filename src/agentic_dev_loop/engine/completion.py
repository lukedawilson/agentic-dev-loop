"""Completion classification from the conversation event stream (unit-11).

Completion is a structural fact: an agent step completes only when it took at
least one verifiable action. The OpenHands SDK marks a content-bearing turn
with zero tool calls as FINISHED, which is how tool-less narration used to
read as a clean pass (planning-docs/059). This module reclassifies that shape
as ``FailureClass.NO_ACTIONS``; truncation semantics stay with ``stages.py``.
"""

from __future__ import annotations

from typing import Iterable

from openhands.sdk.event.llm_convertible.action import ActionEvent

from agentic_dev_loop.core.failure import FailureClass


def count_actions(events: Iterable[object]) -> int:
    """The number of tool calls in the event stream.

    Each tool call emits exactly one ``ActionEvent`` (a ``FinishAction``
    included: the SDK's done signal is itself a tool call)."""
    return sum(1 for event in events if isinstance(event, ActionEvent))


def classify_completion(events: Iterable[object], *,
                        finished: bool) -> FailureClass | None:
    """Classify a completed conversation, or return None when it took actions.

    A FINISHED conversation with zero ActionEvents is NO_ACTIONS. A truncated
    conversation keeps the existing truncation path: ``finished`` is False and
    classification is left to the caller."""
    events = list(events)
    if count_actions(events) > 0:
        return None
    if not finished:
        return None
    return FailureClass.NO_ACTIONS


def completion_evidence(events: Iterable[object]) -> str:
    """The classifier evidence recorded in a structural failure report."""
    events = list(events)
    actions = [e for e in events if isinstance(e, ActionEvent)]
    tools = ", ".join(sorted({getattr(e, "tool_name", "?") for e in actions}))
    return (f"event_count={len(events)} action_events={len(actions)} "
            f"tool_calls=[{tools}]")
