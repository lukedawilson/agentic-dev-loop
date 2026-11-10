"""Unit-11: structural completion, structured verdicts, context split, wire
shape (FR-11.1..11.5).

The regression fixture reconstructs planning-docs/059: a finished turn with
zero ActionEvents (tool-less narration). It must fail fast with a named class
instead of reading as a clean pass.
"""

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.graph import END
from openhands.sdk import LLM
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.llm_convertible.message import MessageEvent
from openhands.sdk.llm.message import Message, MessageToolCall, TextContent
from openhands.sdk.tool import list_registered_tools
from openhands.sdk.tool.builtins.finish import FinishAction

from agentic_dev_loop.core.config import PipelineConfig
from agentic_dev_loop.core.failure import FailureClass
from agentic_dev_loop.core.verdict import RECORD_VERDICT_TOOL, Verdict
from agentic_dev_loop.engine.agents import (RecordVerdictAction,
                                            RecordVerdictTool, load_agent)
from agentic_dev_loop.engine.completion import (classify_completion,
                                                completion_evidence,
                                                count_actions)
from agentic_dev_loop.engine.dryrun import _validate_agents
from agentic_dev_loop.engine.graph import build_graph
from agentic_dev_loop.engine.onboarding.templates import load_template
from agentic_dev_loop.engine.provider_wire import (WireShapeError,
                                                   merge_choices,
                                                   validate_finish_shape,
                                                   validate_response_shape,
                                                   validate_string_tool_results,
                                                   validate_tool_call_pairing)
from agentic_dev_loop.engine.registry import default_registry
from agentic_dev_loop.engine.stages import (Runtime, build_context,
                                            make_agent_node,
                                            make_command_node, make_router)


def make_cfg(steps, **over):
    base = {"name": "t", "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "agents", "lockfiles": ["uv.lock"], "steps": steps}
    base.update(over)
    return PipelineConfig.model_validate(base)


def make_state(**over):
    state = {"subject": "plan plan.md", "docs": {}, "artifacts": {},
             "attempt": 1, "max_attempts": 10, "notes": [], "failed": False,
             "outcome": None, "commit_allowed": False, "step_verdicts": {},
             "retry_target": None, "thread_id": "t1"}
    state.update(over)
    return state


@pytest.fixture
def runtime(tmp_path, fake_sandbox):
    state_dir = tmp_path / ".pr"
    state_dir.mkdir()
    cfg = make_cfg([{"name": "review", "agent": "review",
                     "artifact": "review-findings.md",
                     "verdicts": {"pass": "APPROVED", "fail": "NEEDS CHANGES"},
                     "on_pass": "success", "on_fail": {"goto": "dev", "retry": True}},
                    {"name": "dev", "agent": "dev",
                     "produces": ["implementation-summary.md"]}])
    return Runtime(cfg=cfg, registry=default_registry(),
                   llm=LLM(model="openai/test-model", api_key="sk-test"),
                   agents_dir=tmp_path / "agents", state_dir=state_dir,
                   workspace=tmp_path, sandbox=fake_sandbox)


def _narration(text: str) -> MessageEvent:
    return MessageEvent(
        source="agent",
        llm_message=Message(role="assistant",
                            content=[TextContent(text=text)]))


def _action_event(tool_name: str = "finish", action=None) -> ActionEvent:
    return ActionEvent(
        thought=[TextContent(text="acting")],
        tool_name=tool_name,
        tool_call_id="call-1",
        tool_call=MessageToolCall(
            id="call-1", name=tool_name,
            arguments=json.dumps({"message": "done"}), origin="completion"),
        llm_response_id="resp-1",
        action=action if action is not None else FinishAction(message="done"))


def _verdict_event(value: str, *, parsed: bool = True) -> ActionEvent:
    action = RecordVerdictAction(verdict=value) if parsed else None
    return ActionEvent(
        thought=[TextContent(text="deciding")],
        tool_name=RECORD_VERDICT_TOOL,
        tool_call_id="call-v",
        tool_call=MessageToolCall(
            id="call-v", name=RECORD_VERDICT_TOOL,
            arguments=json.dumps({"verdict": value}), origin="completion"),
        llm_response_id="resp-v",
        action=action)


class TestCompletionFromActions:
    def test_zero_actions_finished_is_no_actions(self):
        events = [_narration("I reviewed the code. It looks fine.")]
        assert count_actions(events) == 0
        assert classify_completion(events, finished=True) is FailureClass.NO_ACTIONS

    def test_finish_action_counts_as_an_action(self):
        events = [_narration("working"), _action_event()]
        assert count_actions(events) == 1
        assert classify_completion(events, finished=True) is None

    def test_truncation_keeps_its_existing_path(self):
        events = [_narration("cut short")]
        assert classify_completion(events, finished=False) is None

    def test_evidence_names_the_counts_and_tools(self):
        evidence = completion_evidence([_narration("x"), _action_event("terminal")])
        assert "event_count=2" in evidence
        assert "action_events=1" in evidence
        assert "terminal" in evidence


class TestRunAgent059Regression:
    def test_finished_narration_with_zero_actions_is_not_a_pass(
            self, runtime, monkeypatch):
        runtime.agents_dir.mkdir()
        (runtime.agents_dir / "review.agent.md").write_text(
            "---\nname: review\ntools: [terminal, record_verdict]\n---\nReview.")
        events = [_narration("The change looks plausible, I think.")]

        class FakeConv:
            def __init__(self, **kwargs):
                self.state = SimpleNamespace(
                    execution_status=ConversationExecutionStatus.FINISHED,
                    events=events)

            def send_message(self, message):
                pass

            def run(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr("agentic_dev_loop.engine.stages.Conversation",
                            lambda **kw: FakeConv(**kw))
        result = runtime.run_agent("review", "review it",
                                   step=runtime.cfg.steps[0])
        assert result.ok
        assert result.failure_class is FailureClass.NO_ACTIONS
        assert result.verdict_error.code is FailureClass.NO_VERDICT_TOOL


class TestNodeStructuralFailure:
    def test_no_actions_halts_with_report_and_no_retry(self, runtime,
                                                       fake_agents):
        fake_agents.set("review", [("no_actions", "tool-less narration")])
        update = make_agent_node(runtime, runtime.cfg.steps[0])(make_state())
        assert update["failed"] is True
        assert update["failed_step"] == "review"
        assert update["failure_class"] == "no_actions"
        assert "retry_target" not in update
        report = (runtime.state_dir / "last-error.md").read_text()
        assert "no_actions" in report
        assert "step: review" in report
        assert "attempt: 1" in report
        assert "action_events=0" in report
        assert "tool-less narration" in report

    def test_verdict_channel_failure_halts_without_bump(self, runtime,
                                                        fake_agents):
        fake_agents.set("review", [(
            "verdict_error", "invalid_verdict_value",
            "value 'MAYBE' is not in the configured set", "findings")])
        update = make_agent_node(runtime, runtime.cfg.steps[0])(make_state())
        assert update["failed"] is True
        assert update["failure_class"] == "invalid_verdict_value"
        report = (runtime.state_dir / "last-error.md").read_text()
        assert "MAYBE" in report and "findings" in report

    def test_structural_failure_router_ends_even_with_retry_target(self,
                                                                   runtime):
        router = make_router(runtime, runtime.cfg.steps[0])
        state = make_state(failed=True, retry_target="dev",
                           failure_class="no_actions")
        assert router(state) == END


class TestVerdictFromEvents:
    REVIEW = {"APPROVED": "pass", "NEEDS CHANGES": "fail"}
    QA = {"PASS": "pass", "FAIL": "fail", "UNVERIFIABLE": "unverifiable",
          "ENV": "env"}

    def test_round_trip_every_configured_value(self):
        for mapping in (self.REVIEW, self.QA):
            for keyword, canonical in mapping.items():
                result = Verdict.from_events([_verdict_event(keyword)], mapping)
                assert result.is_ok()
                assert result.unwrap().value == canonical

    def test_value_matching_is_case_insensitive(self):
        result = Verdict.from_events([_verdict_event("approved")], self.REVIEW)
        assert result.unwrap().value == "pass"

    def test_no_tool_call_is_no_verdict_tool(self):
        result = Verdict.from_events([_narration("APPROVED")], self.REVIEW)
        assert result.error.code is FailureClass.NO_VERDICT_TOOL

    def test_out_of_set_value_is_invalid(self):
        result = Verdict.from_events([_verdict_event("MAYBE")], self.REVIEW)
        assert result.error.code is FailureClass.INVALID_VERDICT_VALUE
        assert "MAYBE" in result.error.detail
        assert "APPROVED" in result.error.detail

    def test_conflicting_values_fail_fast(self):
        result = Verdict.from_events(
            [_verdict_event("APPROVED"), _verdict_event("NEEDS CHANGES")],
            self.REVIEW)
        assert result.error.code is FailureClass.CONFLICTING_VERDICT
        assert "APPROVED" in result.error.detail

    def test_raw_arguments_are_read_when_action_was_not_parsed(self):
        result = Verdict.from_events(
            [_verdict_event("PASS", parsed=False)], self.QA)
        assert result.unwrap().value == "pass"


class TestRecordVerdictTool:
    def test_tool_is_registered_under_its_name(self):
        assert RECORD_VERDICT_TOOL in list_registered_tools()

    def test_tool_exposes_the_action_schema(self):
        tool = RecordVerdictTool.create()[0]
        assert tool.name == RECORD_VERDICT_TOOL
        assert tool.action_type is RecordVerdictAction
        assert RecordVerdictAction(verdict="PASS").verdict == "PASS"

    def test_agent_without_the_tool_fails_the_required_check(self, tmp_path):
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "review.agent.md").write_text(
            "---\nname: review\ntools: [terminal]\n---\nReview.")
        llm = LLM(model="openai/test-model", api_key="sk-test")
        with pytest.raises(ValueError, match="record_verdict"):
            load_agent(agents, "review", llm,
                       required_tools=(RECORD_VERDICT_TOOL,))

    def test_graph_build_rejects_verdict_step_without_the_tool(self, tmp_path):
        cfg = make_cfg([{"name": "review", "agent": "review",
                         "artifact": "r.md",
                         "verdicts": {"pass": "PASS", "fail": "FAIL"}}])
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "review.agent.md").write_text(
            "---\nname: review\ntools: [terminal]\n---\nReview.")
        runtime = Runtime(cfg=cfg, registry=default_registry(),
                          llm=LLM(model="openai/test-model", api_key="sk-test"),
                          agents_dir=agents, state_dir=tmp_path,
                          workspace=tmp_path, sandbox=None)
        with pytest.raises(ValueError, match="record_verdict"):
            build_graph(runtime)

    def test_dry_run_rejects_verdict_step_without_the_tool(self, tmp_path):
        cfg = make_cfg([{"name": "review", "agent": "review",
                         "artifact": "r.md",
                         "verdicts": {"pass": "PASS", "fail": "FAIL"}}])
        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "review.agent.md").write_text(
            "---\nname: review\ntools: [terminal]\n---\nReview.")
        with pytest.raises(ValueError, match="record_verdict"):
            _validate_agents(cfg, agents)


class TestContextSplit:
    def test_claims_and_verified_are_labelled_and_ordered(self, runtime):
        ctx = build_context(runtime, {}, "s", notes=["prior note"],
                            artifacts={"dev": "CLAIM-TEXT"},
                            verified={"test": "exit 1"})
        claims = "## Stage artifacts (each agent's own claims, not verified)"
        verified = "## Verified state (mechanically checked by the pipeline)"
        assert claims in ctx
        assert verified in ctx
        assert ctx.index(claims) < ctx.index(verified)
        assert "CLAIM-TEXT" in ctx
        assert "### Previous attempt feedback\n\nprior note" in ctx
        assert "- test: exit 1" in ctx

    def test_no_artifacts_still_renders_the_verified_section(self, runtime):
        ctx = build_context(runtime, {}, "s")
        assert "## Verified state (mechanically checked by the pipeline)" in ctx
        assert "- none yet" in ctx
        assert "## Stage artifacts (" not in ctx


class TestWireShape:
    def test_tool_calls_finish_reason_requires_tool_calls(self):
        with pytest.raises(WireShapeError, match="empty tool_calls"):
            validate_finish_shape("tool_calls", [])
        validate_finish_shape("tool_calls", [object()])
        validate_finish_shape("stop", [])

    def test_response_shape_validator_reads_the_surfaced_choice(self):
        bad = SimpleNamespace(raw_response={
            "choices": [{"finish_reason": "tool_calls", "message": {}}]})
        with pytest.raises(WireShapeError):
            validate_response_shape(bad)
        good = SimpleNamespace(raw_response={
            "choices": [{"finish_reason": "tool_calls",
                         "message": {"tool_calls": [{"id": "c1"}]}}]})
        validate_response_shape(good)

    def test_response_shape_validator_scans_all_choices(self):
        # The Copilot gateway splits parallel tool calls and narration across
        # choices; choices[0] can be narration with empty tool_calls while the
        # real tool calls sit in choices[1..n]. That must not be a violation.
        split = SimpleNamespace(raw_response={
            "choices": [
                {"finish_reason": "tool_calls",
                 "message": {"content": "I'll run two commands",
                             "tool_calls": []}},
                {"finish_reason": "tool_calls",
                 "message": {"tool_calls": [{"id": "c1"}]}},
                {"finish_reason": "tool_calls",
                 "message": {"tool_calls": [{"id": "c2"}]}},
            ]})
        validate_response_shape(split)

    def test_response_shape_validator_raises_when_no_choice_has_tool_calls(self):
        empty = SimpleNamespace(raw_response={
            "choices": [
                {"finish_reason": "tool_calls",
                 "message": {"content": "narration", "tool_calls": []}},
                {"finish_reason": "tool_calls", "message": {}},
            ]})
        with pytest.raises(WireShapeError, match="empty tool_calls"):
            validate_response_shape(empty)

    def test_merge_choices_collapses_narration_and_tool_calls(self):
        def _choice(content, tool_calls):
            msg = SimpleNamespace(content=content, tool_calls=tool_calls)
            return SimpleNamespace(message=msg, finish_reason="tool_calls")

        response = SimpleNamespace(choices=[
            _choice("I'll run two commands", []),
            _choice(None, [SimpleNamespace(id="c1")]),
            _choice(None, [SimpleNamespace(id="c2")]),
        ])
        merge_choices(response)
        assert len(response.choices) == 1
        assert response.choices[0].message.content == "I'll run two commands"
        assert [c.id for c in response.choices[0].message.tool_calls] == \
            ["c1", "c2"]

    def test_merge_choices_is_a_noop_for_a_single_choice(self):
        response = SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content="done",
                                                   tool_calls=[object()]),
                            finish_reason="stop"),
        ])
        merge_choices(response)
        assert len(response.choices) == 1

    def test_dangling_assistant_tool_call_is_rejected(self):
        dangling = [Message(
            role="assistant", content=[TextContent(text="")],
            tool_calls=[MessageToolCall(
                id="c1", name="terminal", arguments="{}",
                origin="completion")])]
        with pytest.raises(WireShapeError, match="without a tool result"):
            validate_tool_call_pairing(dangling)
        paired = dangling + [Message(
            role="tool", tool_call_id="c1", name="terminal",
            content=[TextContent(text="ok")])]
        validate_tool_call_pairing(paired)

    def test_forced_string_policy_must_surface_a_visible_result(self):
        tool_message = Message(role="tool", tool_call_id="c1",
                               name="terminal",
                               content=[TextContent(text="visible")])
        with pytest.raises(WireShapeError):
            validate_string_tool_results(
                [tool_message], [{"content": []}], forced=True)
        validate_string_tool_results(
            [tool_message], [{"content": "visible"}], forced=True)
        validate_string_tool_results(
            [tool_message], [{"content": []}], forced=False)

    def test_serialized_alignment_is_asserted_not_assumed(self):
        tool_message = Message(role="tool", tool_call_id="c1",
                               name="terminal",
                               content=[TextContent(text="visible")])
        with pytest.raises(WireShapeError, match="message count"):
            validate_string_tool_results(
                [tool_message], [], forced=True)
        with pytest.raises(WireShapeError, match="message count"):
            validate_string_tool_results(
                [tool_message],
                [{"content": "visible"}, {"content": "extra"}], forced=True)

    def test_request_wrapper_enforces_pairing_before_sending(self,
                                                              monkeypatch):
        llm = LLM(model="openai/test-model", api_key="sk-test")
        dangling = [Message(
            role="assistant", content=[TextContent(text="")],
            tool_calls=[MessageToolCall(
                id="missing", name="terminal", arguments="{}",
                origin="completion")])]
        monkeypatch.setattr(
            "agentic_dev_loop.engine.llm._ORIGINAL_COMPLETION",
            lambda self, messages, *a, **k: SimpleNamespace(raw_response=None))
        with pytest.raises(WireShapeError):
            llm.completion(dangling)


class TestMechanicalOutputChannel:
    """M2: command/action failure output is mechanically produced fact, not an
    agent claim - it never rides under the claims heading."""

    def _run_failing_command(self, runtime):
        cfg = make_cfg([{"name": "test",
                         "command": "echo gate-broke && false",
                         "on_pass": "success",
                         "on_fail": "success"}])
        rt = dataclasses.replace(runtime, cfg=cfg)
        update = make_command_node(rt, cfg.steps[0])(make_state())
        return rt, update

    def test_failing_command_records_mechanical_notes_not_agent_notes(
            self, runtime):
        _rt, update = self._run_failing_command(runtime)
        assert "notes" not in update
        assert "gate-broke" in update["mechanical_notes"][0]
        assert "## test output (attempt 1)" in update["mechanical_notes"][0]

    def test_command_output_never_renders_under_the_claims_heading(
            self, runtime):
        rt, update = self._run_failing_command(runtime)
        ctx = build_context(rt, {}, "s", notes=["agent prose note"],
                            mechanical_notes=update["mechanical_notes"],
                            verified=update["verified_state"])
        claims = "## Stage artifacts (each agent's own claims, not verified)"
        mechanical = ("## Mechanical gate output "
                      "(pipeline-captured process output, not agent claims; "
                      "each failure here is a required fix)")
        verified = "## Verified state (mechanically checked by the pipeline)"
        assert claims in ctx and mechanical in ctx
        claims_section = ctx[ctx.index(claims):ctx.index(mechanical)]
        assert "agent prose note" in claims_section
        assert "gate-broke" not in claims_section
        mechanical_section = ctx[ctx.index(mechanical):ctx.index(verified)]
        assert "gate-broke" in mechanical_section

    def test_context_without_mechanical_notes_omits_the_section(self, runtime):
        ctx = build_context(runtime, {}, "s", notes=["agent prose note"])
        assert "## Mechanical gate output" not in ctx

    def test_mechanical_heading_binds_failures_as_required_fixes(self, runtime):
        _rt, update = self._run_failing_command(runtime)
        ctx = build_context(runtime, {}, "s",
                            mechanical_notes=update["mechanical_notes"],
                            verified=update["verified_state"])
        heading = next(line for line in ctx.splitlines()
                       if line.startswith("## Mechanical gate output"))
        assert "required fix" in heading


class TestDevAuditContract:
    """Plan 065: the dev prompt binds a failing mechanical or audit gate to
    dev, and the two copies of the contract stay in sync."""

    @staticmethod
    def _texts():
        repo = Path(__file__).parent.parent
        return [
            (repo / "agentic-dev-loop" / "sdk_agents" / "dev.agent.md")
            .read_text(encoding="utf-8"),
            load_template("dev.agent.md"),
        ]

    def test_contract_names_both_required_fix_channels(self):
        for text in self._texts():
            assert "*Mechanical gate output*" in text
            assert "*Previous attempt feedback*" in text
            assert "failing output in your notes" not in text

    def test_contract_has_an_audit_gate_section(self):
        for text in self._texts():
            assert "## AUDIT GATE" in text
            assert "floor-pin" in text.lower()


class TestVerifiedStateAge:
    """m4: a verified fact carries the attempt that produced it, so attempt 1's
    exit code is never presented as concurrently verified in attempt 2."""

    def test_command_fact_is_tagged_with_its_attempt(self, runtime):
        cfg = make_cfg([{"name": "test", "command": "false",
                         "on_pass": "success",
                         "on_fail": "success"}])
        rt = dataclasses.replace(runtime, cfg=cfg)
        update = make_command_node(rt, cfg.steps[0])(make_state(attempt=2))
        assert update["verified_state"] == {"test": "attempt 2: exit 1"}

    def test_attempt_one_fact_does_not_read_as_current(self, runtime):
        cfg = make_cfg([{"name": "test", "command": "false",
                         "on_pass": "success",
                         "on_fail": "success"}])
        rt = dataclasses.replace(runtime, cfg=cfg)
        attempt_one = make_command_node(rt, cfg.steps[0])(make_state(attempt=1))
        ctx = build_context(rt, {}, "s", verified=attempt_one["verified_state"])
        assert "- test: attempt 1: exit 1" in ctx
        assert "- test: exit 1" not in ctx
