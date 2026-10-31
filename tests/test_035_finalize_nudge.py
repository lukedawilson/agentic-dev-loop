"""Finalize nudge (035): config surface, the turn-index injection, and the
exactly-once guard on the shared turn loop."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from openhands.sdk import LLM
from openhands.sdk.conversation.state import ConversationExecutionStatus

from agentic_dev_loop.core.config import PipelineConfig, load_config
from agentic_dev_loop.engine.registry import default_registry
from agentic_dev_loop.engine.stages import Runtime, _FinalizeNudgeController

NUDGE = "3 turns remain: produce your final response now"


def _base() -> dict:
    return {
        "name": "t",
        "llm": {"model": "m", "api_key_env": "K"},
        "agents_dir": "agents",
        "lockfiles": ["uv.lock"],
        "steps": [{"name": "dev", "agent": "dev", "on_pass": "success"}],
    }


def _load(tmp_path, data):
    p = tmp_path / "pipeline.yaml"
    p.write_text(yaml.safe_dump(data))
    return load_config(p)


def _verdict_step(**extra) -> dict:
    step = {
        "name": "qa",
        "agent": "qa",
        "artifact": "qa-report.md",
        "verdicts": {"pass": "PASS", "fail": "FAIL"},
        "max_iterations": 100,
        "finalize_nudge_turns": 10,
        "on_pass": "success",
    }
    step.update(extra)
    return step


class TestFinalizeNudgeConfig:
    def test_valid_agent_step_round_trip(self, tmp_path):
        data = _base()
        data["steps"] = [_verdict_step()]
        step = _load(tmp_path, data).steps[0]
        assert step.finalize_nudge_turns == 10
        assert step.max_iterations == 100

    def test_absent_keeps_none(self, tmp_path):
        data = _base()
        assert _load(tmp_path, data).steps[0].finalize_nudge_turns is None

    def test_requires_max_iterations(self, tmp_path):
        data = _base()
        data["steps"] = [_verdict_step(max_iterations=None)]
        with pytest.raises(ValidationError, match="finalize_nudge_turns"):
            _load(tmp_path, data)

    def test_must_be_less_than_max_iterations(self, tmp_path):
        data = _base()
        data["steps"] = [_verdict_step(max_iterations=10,
                                       finalize_nudge_turns=10)]
        with pytest.raises(ValidationError, match="finalize_nudge_turns"):
            _load(tmp_path, data)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_nonpositive_rejected(self, tmp_path, bad):
        data = _base()
        data["steps"] = [_verdict_step(finalize_nudge_turns=bad)]
        with pytest.raises(ValidationError, match="finalize_nudge_turns"):
            _load(tmp_path, data)

    @pytest.mark.parametrize("step", [
        {"name": "test", "command": "true", "finalize_nudge_turns": 2},
        {"name": "commit", "action": "commit", "finalize_nudge_turns": 2},
    ])
    def test_agent_only_rejected_on_non_agent(self, tmp_path, step):
        data = _base()
        data["steps"] = [step]
        with pytest.raises(ValidationError, match="requires 'agent'"):
            _load(tmp_path, data)

    def test_stop_when_key_rejected(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "stop_when": {"green_command": "pytest"}}]
        with pytest.raises(ValidationError, match="stop_when"):
            _load(tmp_path, data)


class TestFinalizeNudgeLoop:
    def _conv(self, finish_after):
        class Fake:
            def __init__(self):
                self.state = SimpleNamespace(execution_status=None)
                self.run_calls = 0
                self.sent = []

            def run(self):
                self.run_calls += 1
                self.state.execution_status = (
                    ConversationExecutionStatus.FINISHED
                    if self.run_calls >= finish_after
                    else ConversationExecutionStatus.PAUSED)

            def send_message(self, message):
                self.sent.append((self.run_calls, message))

        return Fake()

    def test_nudge_at_configured_turn_and_only_once(self):
        conv = self._conv(finish_after=9)
        controller = _FinalizeNudgeController(3)
        Runtime._run_finalize_loop(None, conv, max_iterations=10,
                                    finalize_controller=controller)
        assert controller.nudged is True
        assert conv.sent == [(7, NUDGE)]

    def test_no_nudge_when_finished_before_threshold(self):
        conv = self._conv(finish_after=3)
        controller = _FinalizeNudgeController(3)
        Runtime._run_finalize_loop(None, conv, max_iterations=10,
                                    finalize_controller=controller)
        assert controller.nudged is False
        assert conv.sent == []

    def test_turn_cap_still_applies_when_nudge_ignored(self):
        conv = self._conv(finish_after=999)
        controller = _FinalizeNudgeController(3)
        Runtime._run_finalize_loop(None, conv, max_iterations=5,
                                    finalize_controller=controller)
        assert controller.nudged is True
        assert conv.state.execution_status == ConversationExecutionStatus.ERROR
        assert conv.sent == [(2, NUDGE)]


class TestRunAgentFinalizeNudge:
    def _runtime(self, tmp_path):
        cfg = PipelineConfig.model_validate({
            "name": "t",
            "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "agents",
            "lockfiles": ["uv.lock"],
            "steps": [{
                "name": "qa",
                "agent": "qa",
                "max_iterations": 10,
                "finalize_nudge_turns": 3,
                "on_pass": "success",
            }],
        })
        state_dir = tmp_path / ".pr"
        state_dir.mkdir()
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "qa.agent.md").write_text(
            "---\nname: qa\ntools: [terminal]\n---\nProbe.")
        return Runtime(
            cfg=cfg, registry=default_registry(),
            llm=LLM(model="openai/test-model", api_key="sk-test"),
            agents_dir=agents_dir, state_dir=state_dir,
            workspace=tmp_path, sandbox=None)

    def _fake_conv(self, finish_after):
        class Fake:
            def __init__(self):
                self.state = SimpleNamespace(events=[], execution_status=None)
                self.callbacks = []
                self.messages = []
                self.closed = False
                self.run_calls = 0
                self.finish_after = finish_after

            def send_message(self, message):
                self.messages.append(message)

            def run(self):
                self.run_calls += 1
                self.state.execution_status = (
                    ConversationExecutionStatus.FINISHED
                    if self.run_calls >= self.finish_after
                    else ConversationExecutionStatus.PAUSED)

            def close(self):
                self.closed = True

        return Fake()

    def test_run_agent_attaches_pause_and_sends_nudge(self, tmp_path, monkeypatch):
        runtime = self._runtime(tmp_path)
        conv = self._fake_conv(finish_after=9)

        def factory(**kw):
            conv.callbacks = kw.get("callbacks") or []
            return conv

        monkeypatch.setattr(
            "agentic_dev_loop.engine.stages.Conversation", factory)

        result = runtime.run_agent("qa", "the task", max_iterations=10,
                                   step=runtime.cfg.steps[0])

        assert result.ok is True
        assert result.truncated is False
        assert conv.callbacks
        assert conv.messages == ["the task", NUDGE]
        assert conv.run_calls == 9
        assert conv.closed is True
