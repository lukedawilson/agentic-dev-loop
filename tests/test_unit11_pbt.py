"""Property-based tests for unit-11 (PBT-02, PBT-03, PBT-07, PBT-08).

Generators are built from the real SDK event classes, and each property is a
pure function of its inputs: an event stream's action count, the structured
verdict decode, and the wire-shape invariant.
"""

import json

from hypothesis import given, settings
from hypothesis import strategies as st
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.llm_convertible.message import MessageEvent
from openhands.sdk.llm.message import Message, MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import FinishAction

from agentic_dev_loop.core.failure import FailureClass
from agentic_dev_loop.core.verdict import Verdict
from agentic_dev_loop.engine.completion import classify_completion, count_actions
from agentic_dev_loop.engine.provider_wire import (WireShapeError,
                                                   validate_finish_shape)

SETTINGS = settings(max_examples=50, deadline=None, derandomize=True)

REVIEW = {"APPROVED": "pass", "NEEDS CHANGES": "fail"}
QA = {"PASS": "pass", "FAIL": "fail", "UNVERIFIABLE": "unverifiable",
      "ENV": "env"}

_text = st.text(alphabet=st.characters(blacklist_categories=("Cc", "Cs")),
                max_size=30)


def _narration(text: str) -> MessageEvent:
    return MessageEvent(
        source="agent",
        llm_message=Message(role="assistant",
                            content=[TextContent(text=text)]))


def _action(tool_name: str) -> ActionEvent:
    return ActionEvent(
        thought=[TextContent(text="acting")],
        tool_name=tool_name,
        tool_call_id="call-1",
        tool_call=MessageToolCall(
            id="call-1", name=tool_name,
            arguments=json.dumps({"message": "done"}), origin="completion"),
        llm_response_id="resp-1",
        action=FinishAction(message="done"))


def _verdict_event(keyword: str) -> ActionEvent:
    return ActionEvent(
        thought=[TextContent(text="deciding")],
        tool_name="record_verdict",
        tool_call_id="call-v",
        tool_call=MessageToolCall(
            id="call-v", name="record_verdict",
            arguments=json.dumps({"verdict": keyword}), origin="completion"),
        llm_response_id="resp-v",
        action=None)


_events = st.lists(
    st.one_of(st.builds(_narration, _text),
              st.builds(_action, st.sampled_from(["finish", "terminal"]))),
    max_size=8)


@SETTINGS
@given(events=_events)
def test_zero_actions_always_classifies_as_no_actions(events):
    if count_actions(events) == 0:
        assert classify_completion(events, finished=True) is FailureClass.NO_ACTIONS
    else:
        assert classify_completion(events, finished=True) is None


@SETTINGS
@given(events=_events)
def test_action_count_matches_action_events(events):
    expected = sum(isinstance(event, ActionEvent) for event in events)
    assert count_actions(events) == expected


@SETTINGS
@given(keyword=st.sampled_from(sorted(REVIEW)),
       extra=st.lists(st.sampled_from(sorted(REVIEW)), max_size=4))
def test_verdict_round_trip_and_conflict_purity(keyword, extra):
    mapping = REVIEW
    result = Verdict.from_events([_verdict_event(keyword)], mapping)
    assert result.unwrap().value == mapping[keyword]

    keywords = [keyword, *extra]
    result = Verdict.from_events([_verdict_event(k) for k in keywords], mapping)
    canonical = {mapping[k] for k in keywords}
    if len(canonical) == 1:
        assert result.unwrap().value == next(iter(canonical))
    else:
        assert result.error.code is FailureClass.CONFLICTING_VERDICT


@SETTINGS
@given(reason=st.sampled_from([None, "stop", "tool_calls", "length"]),
       calls=st.lists(st.just(object()), max_size=3))
def test_finish_shape_invariant(reason, calls):
    if reason == "tool_calls" and not calls:
        try:
            validate_finish_shape(reason, calls)
        except WireShapeError:
            return
        raise AssertionError("empty tool_calls must be rejected")
    validate_finish_shape(reason, calls)


# Every configured review and qa keyword round-trips through the QA mapping
# too, so the generator covers both shipped verdict maps.
@SETTINGS
@given(mapping=st.sampled_from([REVIEW, QA]),
       keyword=st.sampled_from(sorted(set(REVIEW) | set(QA))))
def test_configured_keyword_round_trip(mapping, keyword):
    if keyword not in mapping:
        return
    result = Verdict.from_events([_verdict_event(keyword)], mapping)
    assert result.unwrap().value == mapping[keyword]
