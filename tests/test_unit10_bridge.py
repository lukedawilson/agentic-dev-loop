"""Unit 10 SandboxToolBridge against the real Sandbox request path."""

import pytest

from agentic_dev_loop.core.config import PipelineConfig
from agentic_dev_loop.engine.forbidden import resolve_forbidden_rules
from agentic_dev_loop.engine.isolation import SandboxError
from agentic_dev_loop.engine.runtimes.bridge import (SandboxToolBridge,
                                                     WorkspacePathError)
from agentic_dev_loop.engine.runtimes.protocol import RuntimeContext


def make_ctx(tmp_path, sandbox, **over):
    cfg = PipelineConfig.model_validate({
        "name": "pipe", "provider": "copilot", "copilot": {"auth": "device"},
        "agents_dir": "agents", "lockfiles": ["uv.lock"],
        "steps": [{"name": "dev", "agent": "dev", "on_pass": "success"}],
    })
    values = dict(
        cfg=cfg, workspace=tmp_path, state_dir=tmp_path / ".pr",
        agents_dir=tmp_path / "agents", agent_name="dev", max_iterations=10,
        sandbox=sandbox, sandbox_cwd=tmp_path,
        secrets={"ref": "supersecret"},
        step_env={"BASE": "1"}, secret_env={"TOKEN": "supersecret"},
        forbidden_commands=resolve_forbidden_rules(["blocked_tool"]),
        terminal_timeout=42.0, network=True,
    )
    values.update(over)
    return RuntimeContext(**values)


def test_terminal_routes_through_sandbox(tmp_path, fake_sandbox):
    bridge = SandboxToolBridge(make_ctx(tmp_path, fake_sandbox))
    outcome = bridge.run_terminal("echo hi")
    assert outcome.exit_code == 0
    assert "hi" in outcome.stdout
    request = fake_sandbox.requests[-1]
    assert request.argv == ["echo", "hi"]
    assert request.cwd == str(tmp_path)
    assert request.timeout == 42.0
    assert request.network is True
    assert request.env["TOKEN"] == "supersecret"


def test_terminal_timeout_arg_overrides_step_timeout(tmp_path, fake_sandbox):
    bridge = SandboxToolBridge(make_ctx(tmp_path, fake_sandbox))
    bridge.run_terminal("echo hi", timeout=3)
    assert fake_sandbox.requests[-1].timeout == 3


def test_forbidden_command_never_reaches_the_sandbox(tmp_path, fake_sandbox):
    bridge = SandboxToolBridge(make_ctx(tmp_path, fake_sandbox))
    outcome = bridge.run_terminal("blocked_tool --now")
    assert outcome.exit_code is None
    assert "forbidden" in outcome.render()
    assert fake_sandbox.requests == []


def test_infra_error_is_reraised(tmp_path, fake_sandbox):
    error = SandboxError("AE-SBX-019: daemon lost")
    fake_sandbox.infra_error = error

    def boom(request):
        raise error

    fake_sandbox.script(boom)
    bridge = SandboxToolBridge(make_ctx(tmp_path, fake_sandbox))
    with pytest.raises(SandboxError, match="AE-SBX-019"):
        bridge.run_terminal("echo hi")


def test_non_sticky_sandbox_error_becomes_an_observation(tmp_path,
                                                         fake_sandbox):
    def boom(request):
        raise SandboxError("AE-SBX-099: other")

    fake_sandbox.script(boom)
    bridge = SandboxToolBridge(make_ctx(tmp_path, fake_sandbox))
    outcome = bridge.run_terminal("echo hi")
    assert outcome.exit_code is None
    assert "AE-SBX-099" in outcome.render()


def test_file_lane_round_trip(tmp_path, fake_sandbox):
    bridge = SandboxToolBridge(make_ctx(tmp_path, fake_sandbox))
    bridge.write_file("notes/a.txt", "one")
    assert (tmp_path / "notes/a.txt").read_text() == "one"
    assert bridge.view_file("notes/a.txt") == "one"
    bridge.edit_file("notes/a.txt", "one", "two")
    assert bridge.view_file("notes/a.txt") == "two"
    bridge.insert_file("notes/a.txt", 0, "zero\n")
    assert bridge.view_file("notes/a.txt").startswith("zero")


def test_file_lane_refuses_escape(tmp_path, fake_sandbox):
    bridge = SandboxToolBridge(make_ctx(tmp_path, fake_sandbox))
    with pytest.raises(WorkspacePathError):
        bridge.write_file("../outside.txt", "x")
    with pytest.raises(WorkspacePathError):
        bridge.view_file(str(tmp_path.parent / "x"))


def test_task_store_round_trip(tmp_path, fake_sandbox):
    bridge = SandboxToolBridge(make_ctx(tmp_path, fake_sandbox))
    assert bridge.task_list() == []
    tasks = [{"title": "one", "status": "todo"}]
    assert bridge.task_set(tasks) == tasks
    assert bridge.task_list() == tasks


def test_terminal_result_publishes_to_the_viz_bus(tmp_path, fake_sandbox):
    events = []

    class Bus:
        def publish(self, event):
            events.append(event)

    bridge = SandboxToolBridge(make_ctx(tmp_path, fake_sandbox, viz_bus=Bus()))
    bridge.run_terminal("echo hi")
    assert events and events[0]["type"] == "terminal"
    assert events[0]["exit_code"] == 0
