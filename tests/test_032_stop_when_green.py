"""Stop-when-green (032): config surface, the per-event signal state machine,
the one-turn run-loop budget, and the terminal timeout override."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from openhands.sdk import LLM
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.llm.message import Message, MessageToolCall, TextContent
from openhands.tools.file_editor import FileEditorObservation
from openhands.tools.terminal import TerminalAction, TerminalObservation
from openhands.tools.terminal.metadata import CmdOutputMetadata

from agentic_dev_loop.core.config import PipelineConfig, StopWhenSpec, load_config
from agentic_dev_loop.engine.agents import SandboxTerminalExecutor
from agentic_dev_loop.engine.registry import default_registry
from agentic_dev_loop.engine.stages import Runtime, _StopWhenController


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


def _terminal_obs(command, exit_code):
    return TerminalObservation.from_text(
        "output", command=command, exit_code=exit_code,
        metadata=CmdOutputMetadata(exit_code=exit_code))


def _terminal_event(command, exit_code, idx=0):
    return ObservationEvent(
        tool_name="terminal",
        tool_call_id=f"tc-{idx}",
        action_id=f"a-{idx}",
        observation=_terminal_obs(command, exit_code))


def _file_write_event(command="create", idx=0, is_error=False):
    obs = FileEditorObservation(
        command=command, path="src/x.py", prev_exist=False,
        old_content=None, new_content="x")
    obs = obs.model_copy(update={"is_error": is_error})
    return ObservationEvent(
        tool_name="file_editor",
        tool_call_id=f"fc-{idx}",
        action_id=f"fa-{idx}",
        observation=obs)


def _agent_event(idx=0):
    return ActionEvent(
        thought=[TextContent(type="text", text="working")],
        tool_name="terminal",
        tool_call_id=f"ag-{idx}",
        tool_call=MessageToolCall(
            id=f"ag-{idx}", name="terminal", arguments="{}",
            origin="completion"),
        llm_response_id=f"r-{idx}",
        action=TerminalAction(command="echo hi"))


def _controller(green_command="pytest"):
    return _StopWhenController(StopWhenSpec(green_command=green_command))


class TestStopWhenConfig:
    def test_valid_agent_step_round_trip(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev", "on_pass": "success",
                          "stop_when": {"green_command": "pytest tests/"},
                          "terminal_timeout": 900}]
        step = _load(tmp_path, data).steps[0]
        assert step.stop_when == StopWhenSpec(green_command="pytest tests/")
        assert step.stop_when.post_green_turns == 2
        assert step.terminal_timeout == 900

    def test_post_green_turns_custom(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev", "on_pass": "success",
                          "stop_when": {"green_command": "pytest",
                                        "post_green_turns": 0}}]
        assert _load(tmp_path, data).steps[0].stop_when.post_green_turns == 0

    @pytest.mark.parametrize("bad", ["[", ""])
    def test_invalid_regex_rejected(self, tmp_path, bad):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "stop_when": {"green_command": bad}}]
        with pytest.raises(ValidationError, match="green_command"):
            _load(tmp_path, data)

    def test_negative_post_green_turns_rejected(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "stop_when": {"green_command": "pytest",
                                        "post_green_turns": -1}}]
        with pytest.raises(ValidationError):
            _load(tmp_path, data)

    @pytest.mark.parametrize("step", [
        {"name": "test", "command": "true", "stop_when": {"green_command": "pytest"}},
        {"name": "commit", "action": "commit", "stop_when": {"green_command": "pytest"}},
        {"name": "test", "command": "true", "terminal_timeout": 900},
        {"name": "commit", "action": "commit", "terminal_timeout": 900},
    ])
    def test_agent_only_fields_rejected_on_non_agent(self, tmp_path, step):
        data = _base()
        data["steps"] = [step]
        with pytest.raises(ValidationError, match="requires 'agent'"):
            _load(tmp_path, data)

    def test_negative_terminal_timeout_rejected(self, tmp_path):
        data = _base()
        data["steps"] = [{"name": "dev", "agent": "dev",
                          "terminal_timeout": -1}]
        with pytest.raises(ValidationError):
            _load(tmp_path, data)


class TestStopWhenController:
    def test_matching_green_terminal_sets_signal(self):
        c = _controller()
        c.attach(SimpleNamespace(state=SimpleNamespace(execution_status=None)))
        c.on_event(_agent_event(0))
        c.on_event(_terminal_event("pytest tests/ -q", 0))
        assert c.green_signal is True

    def test_matching_failure_clears_signal(self):
        c = _controller()
        c.attach(SimpleNamespace(state=SimpleNamespace(execution_status=None)))
        c.on_event(_agent_event(0))
        c.on_event(_terminal_event("pytest tests/ -q", 1))
        assert c.green_signal is False

    def test_file_write_after_green_resets_signal(self):
        c = _controller()
        c.attach(SimpleNamespace(state=SimpleNamespace(execution_status=None)))
        c.on_event(_agent_event(0))
        c.on_event(_terminal_event("pytest tests/ -q", 0))
        c.on_event(_file_write_event("create"))
        assert c.green_signal is False

    def test_file_view_does_not_reset_signal(self):
        c = _controller()
        c.attach(SimpleNamespace(state=SimpleNamespace(execution_status=None)))
        c.on_event(_agent_event(0))
        c.on_event(_terminal_event("pytest tests/ -q", 0))
        c.on_event(_file_write_event("view"))
        assert c.green_signal is True

    def test_later_nonmatching_terminal_keeps_signal(self):
        c = _controller()
        c.attach(SimpleNamespace(state=SimpleNamespace(execution_status=None)))
        c.on_event(_agent_event(0))
        c.on_event(_terminal_event("pytest tests/ -q", 0))
        c.on_event(_terminal_event("git status", 0))
        assert c.green_signal is True

    def test_later_matching_failure_replaces_signal(self):
        c = _controller()
        c.attach(SimpleNamespace(state=SimpleNamespace(execution_status=None)))
        c.on_event(_agent_event(0))
        c.on_event(_terminal_event("pytest tests/ -q", 0))
        c.on_event(_terminal_event("pytest tests/ -q", 2))
        assert c.green_signal is False

    def test_agent_event_pauses_conversation(self):
        c = _controller()
        conv = SimpleNamespace(state=SimpleNamespace(execution_status=None))
        c.attach(conv)
        c.on_event(_agent_event(0))
        assert conv.state.execution_status is not None
        assert conv.state.execution_status.name == "PAUSED"


class TestTerminalWriteChannel:
    def test_terminal_result_green_then_write_resets(self):
        c = _controller()
        c.on_terminal_result("pytest tests/ -q", 0, False)
        assert c.green_signal is True
        c.on_terminal_result("git diff", 0, True)
        assert c.green_signal is False

    def test_terminal_result_matching_failure_clears(self):
        c = _controller()
        c.on_terminal_result("pytest tests/ -q", 0, False)
        c.on_terminal_result("pytest tests/ -q", 1, False)
        assert c.green_signal is False

    def test_executor_reports_writes(self, fake_sandbox, tmp_path):
        seen = []
        ex = SandboxTerminalExecutor(
            fake_sandbox, workspace=str(tmp_path), env={"PATH": "/bin"},
            on_terminal_result=lambda command, exit_code, wrote: seen.append(
                (command, exit_code, wrote)))
        fake_sandbox.script(lambda req: (0, "ok", False, True))
        ex(TerminalAction(command="echo hi"), None)
        assert seen == [("echo hi", 0, True)]


class TestStopWhenRunLoop:
    def _conv(self, controller):
        class Fake:
            def __init__(self):
                self.state = SimpleNamespace(execution_status=None)
                self.messages = []
                self.run_calls = 0

            def run(self):
                self.run_calls += 1
                # The controller callback normally pauses after the turn.
                self.state.execution_status = SimpleNamespace(name="PAUSED")

            def send_message(self, message):
                self.messages.append(message)

        return Fake()

    def test_nudge_then_budget_close(self):
        controller = _controller()
        controller.green_hit = True
        controller.writes_since_green = False
        conv = self._conv(controller)
        Runtime._run_stop_when_loop(None, conv, controller, max_iterations=10)
        assert controller.nudged is True
        assert conv.messages == [controller.NUDGE]
        assert conv.run_calls == 1 + controller.post_green_turns
        assert controller.post_nudge_turns == controller.post_green_turns

    def test_post_green_turns_zero_closes_after_nudge(self):
        controller = _StopWhenController(
            StopWhenSpec(green_command="pytest", post_green_turns=0))
        controller.green_hit = True
        conv = self._conv(controller)
        Runtime._run_stop_when_loop(None, conv, controller, max_iterations=10)
        assert controller.nudged is True
        assert conv.messages == [controller.NUDGE]
        assert conv.run_calls == 1


class TestRunAgentStopWhen:
    def _runtime(self, tmp_path):
        cfg = PipelineConfig.model_validate({
            "name": "t",
            "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "agents",
            "lockfiles": ["uv.lock"],
            "steps": [{
                "name": "dev",
                "agent": "dev",
                "produces": ["implementation-summary.md"],
                "stop_when": {"green_command": "pytest", "post_green_turns": 2},
                "on_pass": "success",
            }],
        })
        state_dir = tmp_path / ".pr"
        state_dir.mkdir()
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "dev.agent.md").write_text(
            "---\nname: dev\ntools: [terminal]\n---\nDo the work.")
        return Runtime(
            cfg=cfg, registry=default_registry(),
            llm=LLM(model="openai/test-model", api_key="sk-test"),
            agents_dir=agents_dir, state_dir=state_dir,
            workspace=tmp_path, sandbox=None)

    def _fake_conv(self, script):
        class Fake:
            def __init__(self, script):
                self.state = SimpleNamespace(events=[], execution_status=None)
                self.callbacks = []
                self.messages = []
                self.closed = False
                self.script = list(script)
                self.run_calls = 0

            def send_message(self, message):
                self.messages.append(message)

            def run(self):
                self.run_calls += 1
                events, status = self.script.pop(0)
                for event in events:
                    self.state.events.append(event)
                    for cb in self.callbacks:
                        cb(event)
                self.state.execution_status = status

            def close(self):
                self.closed = True

        return Fake(script)

    def test_green_signal_nudges_closes_and_captures(self, tmp_path, monkeypatch):
        runtime = self._runtime(tmp_path)
        final = MessageEvent(
            source="agent",
            llm_message=Message(
                role="assistant",
                content=[TextContent(type="text", text="final summary")]))
        script = [
            ([_agent_event(0), _terminal_event("pytest tests/ -q", 0)],
             ConversationExecutionStatus.PAUSED),
            ([final], ConversationExecutionStatus.FINISHED),
        ]
        conv = self._fake_conv(script)

        def factory(**kw):
            conv.callbacks = kw.get("callbacks") or []
            return conv

        monkeypatch.setattr(
            "agentic_dev_loop.engine.stages.Conversation", factory)

        result = runtime.run_agent("dev", "the task", max_iterations=10,
                                   step=runtime.cfg.steps[0])

        assert result.ok is True
        assert result.response == "final summary"
        assert result.truncated is False
        assert conv.messages == ["the task", _StopWhenController.NUDGE]
        assert conv.run_calls == 2
        assert conv.closed is True


class TestTerminalTimeoutOverride:
    def test_defaults_to_terminal_timeout(self, fake_sandbox, tmp_path):
        ex = SandboxTerminalExecutor(
            fake_sandbox, workspace=str(tmp_path), env={"PATH": "/bin"},
            terminal_timeout=900)
        ex(TerminalAction(command="echo hi"), None)
        assert fake_sandbox.requests[0].timeout == 900

    def test_action_timeout_wins(self, fake_sandbox, tmp_path):
        ex = SandboxTerminalExecutor(
            fake_sandbox, workspace=str(tmp_path), env={"PATH": "/bin"},
            terminal_timeout=900)
        ex(TerminalAction(command="echo hi", timeout=30), None)
        assert fake_sandbox.requests[0].timeout == 30

    def test_unset_keeps_none(self, fake_sandbox, tmp_path):
        ex = SandboxTerminalExecutor(
            fake_sandbox, workspace=str(tmp_path), env={"PATH": "/bin"})
        ex(TerminalAction(command="echo hi"), None)
        assert fake_sandbox.requests[0].timeout is None
