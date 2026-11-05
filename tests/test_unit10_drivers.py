"""Unit 10 driver contracts and status mapping (SDK boundary faked)."""

import asyncio
import sys
import types

import pytest

from agentic_dev_loop.core.config import PipelineConfig
from agentic_dev_loop.engine.isolation import SandboxError
from agentic_dev_loop.engine.runtimes.errors import (HarnessProcessError,
                                                     RuntimeSetupError)
from agentic_dev_loop.engine.runtimes.protocol import (NudgePolicy,
                                                       RuntimeContext,
                                                       RuntimeStatus)


def claude_cfg(**block):
    values = {"login": "subscription"}
    values.update(block)
    return PipelineConfig.model_validate({
        "name": "pipe", "provider": "claude", "claude": values,
        "agents_dir": "agents", "lockfiles": ["uv.lock"],
        "steps": [{"name": "dev", "agent": "dev",
                   "produces": ["out.md"], "max_iterations": 10,
                   "on_pass": "success"}],
    })


def copilot_cfg(**block):
    values = {"auth": "device"}
    values.update(block)
    return PipelineConfig.model_validate({
        "name": "pipe", "provider": "copilot", "copilot": values,
        "agents_dir": "agents", "lockfiles": ["uv.lock"],
        "steps": [{"name": "dev", "agent": "dev", "produces": ["out.md"],
                   "max_iterations": 10, "on_pass": "success"}],
    })


def make_ctx(tmp_path, cfg, sandbox=None):
    return RuntimeContext(
        cfg=cfg, workspace=tmp_path, state_dir=tmp_path / "state",
        agents_dir=tmp_path / "agents", agent_name="dev", max_iterations=10,
        step=cfg.steps[0], sandbox=sandbox, sandbox_cwd=tmp_path,
        secrets={"ref": "supersecret"})


def write_agent(tmp_path):
    (tmp_path / "agents").mkdir(exist_ok=True)
    (tmp_path / "agents" / "dev.agent.md").write_text(
        "---\nname: dev\ntools: [terminal]\n---\nDo the work.")


class Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class AssistantMessage:
    def __init__(self, text):
        self.content = [Block(text)]


class FakeOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeClient:
    scripted = [["done"]]
    queries = []
    connects = 0

    def __init__(self, options=None):
        self.options = options
        self._run = 0
        FakeClient.queries = []

    async def connect(self):
        FakeClient.connects += 1

    async def query(self, message):
        FakeClient.queries.append(message)

    async def receive_response(self):
        texts = FakeClient.scripted[min(self._run, len(FakeClient.scripted) - 1)]
        self._run += 1
        for text in texts:
            yield AssistantMessage(text)

    async def disconnect(self):
        pass


class ProcessError(Exception):
    pass


def _fake_claude_sdk(client_cls=FakeClient):
    module = types.ModuleType("claude_agent_sdk")

    def tool(name, description, schema):
        def wrap(fn):
            fn.tool_name = name
            return fn
        return wrap

    module.ClaudeAgentOptions = FakeOptions
    module.ClaudeSDKClient = client_cls
    module.tool = tool
    module.create_sdk_mcp_server = lambda **kwargs: {"server": kwargs}
    return module


@pytest.fixture
def claude_sdk(monkeypatch):
    FakeClient.scripted = [["done"]]
    module = _fake_claude_sdk()
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    return module


def _home(tmp_path, monkeypatch, logged_in=True):
    monkeypatch.setenv("HOME", str(tmp_path))
    if logged_in:
        store = tmp_path / ".claude"
        store.mkdir()
        (store / ".credentials.json").write_text("{}")


def test_claude_options_are_hardened(tmp_path, monkeypatch, claude_sdk):
    _home(tmp_path, monkeypatch)
    write_agent(tmp_path)
    from agentic_dev_loop.engine.runtimes.claude_runtime import ClaudeAgentRuntime

    runtime = ClaudeAgentRuntime(make_ctx(tmp_path, claude_cfg()))
    options = runtime.options.kwargs
    assert options["setting_sources"] == []
    assert options["allowed_tools"] == ["terminal", "file_editor",
                                        "task_tracker"]
    assert options["permission_mode"] == "deny"
    assert list(options["mcp_servers"]) == ["agentic-dev-loop"]
    assert options["cwd"] == str(tmp_path)
    assert options["max_turns"] == 10
    assert "supersecret" not in options["env"].values()
    assert "inside the agentic-dev-loop harness" in options["system_prompt"]


def test_claude_config_max_turns_wins(tmp_path, monkeypatch, claude_sdk):
    _home(tmp_path, monkeypatch)
    write_agent(tmp_path)
    from agentic_dev_loop.engine.runtimes.claude_runtime import ClaudeAgentRuntime

    cfg = claude_cfg(max_turns=3, model="sonnet")
    runtime = ClaudeAgentRuntime(make_ctx(tmp_path, cfg))
    assert runtime.options.kwargs["max_turns"] == 3
    assert runtime.options.kwargs["model"] == "sonnet"


def test_claude_subscription_without_login_fails_fast(tmp_path, monkeypatch,
                                                      claude_sdk):
    _home(tmp_path, monkeypatch, logged_in=False)
    write_agent(tmp_path)
    from agentic_dev_loop.engine.runtimes.claude_runtime import ClaudeAgentRuntime

    with pytest.raises(RuntimeSetupError, match="claude login"):
        ClaudeAgentRuntime(make_ctx(tmp_path, claude_cfg()))


def test_claude_key_mode_requires_env(tmp_path, monkeypatch, claude_sdk):
    _home(tmp_path, monkeypatch, logged_in=False)
    write_agent(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from agentic_dev_loop.engine.runtimes.claude_runtime import ClaudeAgentRuntime

    with pytest.raises(RuntimeSetupError, match="ANTHROPIC_API_KEY"):
        ClaudeAgentRuntime(make_ctx(tmp_path, claude_cfg(login="key")))


def test_claude_key_mode_passes_only_its_credential(tmp_path, monkeypatch,
                                                    claude_sdk):
    _home(tmp_path, monkeypatch, logged_in=False)
    write_agent(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    from agentic_dev_loop.engine.runtimes.claude_runtime import ClaudeAgentRuntime

    runtime = ClaudeAgentRuntime(make_ctx(tmp_path, claude_cfg(login="key")))
    env = runtime.options.kwargs["env"]
    assert env["ANTHROPIC_API_KEY"] == "sk-ant"
    assert "supersecret" not in env.values()


def test_claude_finished_run_captures(tmp_path, monkeypatch, claude_sdk):
    _home(tmp_path, monkeypatch)
    write_agent(tmp_path)
    from agentic_dev_loop.engine.runtimes.claude_runtime import ClaudeAgentRuntime

    runtime = ClaudeAgentRuntime(make_ctx(tmp_path, claude_cfg()))
    result = runtime.run("task")
    assert result.status is RuntimeStatus.FINISHED
    assert result.final_text == "done"
    assert (tmp_path / "state" / "out.md").read_text() == "done"


def test_claude_nudge_is_a_session_continuation(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    write_agent(tmp_path)
    FakeClient.scripted = [["a1", "a2", "a3", "a4", "a5", "a6", "a7"],
                          ["final"]]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk",
                        _fake_claude_sdk())
    from agentic_dev_loop.engine.runtimes.claude_runtime import ClaudeAgentRuntime

    runtime = ClaudeAgentRuntime(make_ctx(tmp_path, claude_cfg()))
    result = runtime.run("task", nudge_policy=NudgePolicy.for_turns(3))
    assert FakeClient.queries[0] == "task"
    assert FakeClient.queries[1] == "3 turns remain: produce your final response now"
    assert result.status is RuntimeStatus.FINISHED
    assert result.final_text == "final"


def test_claude_process_death_is_stuck(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    write_agent(tmp_path)

    class DeadClient(FakeClient):
        async def connect(self):
            raise ProcessError("harness exited")

    monkeypatch.setitem(sys.modules, "claude_agent_sdk",
                        _fake_claude_sdk(DeadClient))
    from agentic_dev_loop.engine.runtimes.claude_runtime import ClaudeAgentRuntime

    result = ClaudeAgentRuntime(make_ctx(tmp_path, claude_cfg())).run("task")
    assert result.status is RuntimeStatus.STUCK
    assert result.truncated is True


def test_claude_sdk_error_is_error(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    write_agent(tmp_path)

    class BrokenClient(FakeClient):
        async def connect(self):
            raise ValueError("api error")

    monkeypatch.setitem(sys.modules, "claude_agent_sdk",
                        _fake_claude_sdk(BrokenClient))
    from agentic_dev_loop.engine.runtimes.claude_runtime import ClaudeAgentRuntime

    result = ClaudeAgentRuntime(make_ctx(tmp_path, claude_cfg())).run("task")
    assert result.status is RuntimeStatus.ERROR


def test_claude_sticky_infra_error_fails_the_run_without_retry(
        tmp_path, monkeypatch, fake_sandbox):
    _home(tmp_path, monkeypatch)
    write_agent(tmp_path)
    error = SandboxError("AE-SBX-019: daemon lost")
    fake_sandbox.infra_error = error

    class ExplodingClient(FakeClient):
        async def receive_response(self):
            raise error
            yield

    monkeypatch.setitem(sys.modules, "claude_agent_sdk",
                        _fake_claude_sdk(ExplodingClient))
    from agentic_dev_loop.engine.runtimes.claude_runtime import ClaudeAgentRuntime

    runtime = ClaudeAgentRuntime(
        make_ctx(tmp_path, claude_cfg(), sandbox=fake_sandbox))
    with pytest.raises(SandboxError) as excinfo:
        runtime.run("task")
    assert excinfo.value is error


# -- copilot ---------------------------------------------------------------
class FakeCodexSession:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        FakeCodexSession.instances.append(self)

    async def start(self):
        pass

    async def run(self, prompt):
        return "copilot done"

    async def close(self):
        self.closed = True


def test_copilot_options_are_mcp_only(tmp_path, monkeypatch):
    FakeCodexSession.instances = []
    module = types.ModuleType("openai_codex")
    module.CodexSession = FakeCodexSession
    monkeypatch.setitem(sys.modules, "openai_codex", module)
    from agentic_dev_loop.engine.runtimes import copilot_runtime
    from agentic_dev_loop.engine.runtimes.copilot_auth import CopilotToken

    token = CopilotToken("copilot-secret")
    monkeypatch.setattr(copilot_runtime, "ensure_token", lambda: token)
    write_agent(tmp_path)
    runtime = copilot_runtime.CopilotRuntime(make_ctx(tmp_path, copilot_cfg()))
    options = runtime.options
    assert options["mcp_servers"].keys() == {"agentic-dev-loop"}
    assert options["allowed_tools"] == ["terminal", "file_editor",
                                        "task_tracker"]
    assert options["auth"] is token
    assert options["cwd"] == str(tmp_path)
    assert "supersecret" not in options["env"].values()

    result = runtime.run("task")
    assert result.status is RuntimeStatus.FINISHED
    assert (tmp_path / "state" / "out.md").read_text() == "copilot done"
    assert FakeCodexSession.instances[0].closed


def test_copilot_token_is_redacted_from_capture(tmp_path, monkeypatch):
    module = types.ModuleType("openai_codex")

    class LeakySession(FakeCodexSession):
        async def run(self, prompt):
            return "my token is copilot-secret"

    module.CodexSession = LeakySession
    monkeypatch.setitem(sys.modules, "openai_codex", module)
    from agentic_dev_loop.engine.runtimes import copilot_runtime
    from agentic_dev_loop.engine.runtimes.copilot_auth import CopilotToken

    monkeypatch.setattr(copilot_runtime, "ensure_token",
                        lambda: CopilotToken("copilot-secret"))
    write_agent(tmp_path)
    runtime = copilot_runtime.CopilotRuntime(make_ctx(tmp_path, copilot_cfg()))
    runtime.run("task")
    assert "copilot-secret" not in (tmp_path / "state" / "out.md").read_text()


def test_copilot_sticky_infra_error_fails_the_run_without_retry(
        tmp_path, monkeypatch, fake_sandbox):
    write_agent(tmp_path)
    error = SandboxError("AE-SBX-019: daemon lost")
    fake_sandbox.infra_error = error

    module = types.ModuleType("openai_codex")

    class ExplodingSession(FakeCodexSession):
        async def run(self, prompt):
            raise error

    module.CodexSession = ExplodingSession
    monkeypatch.setitem(sys.modules, "openai_codex", module)
    from agentic_dev_loop.engine.runtimes import copilot_runtime
    from agentic_dev_loop.engine.runtimes.copilot_auth import CopilotToken

    monkeypatch.setattr(copilot_runtime, "ensure_token",
                        lambda: CopilotToken("copilot-secret"))
    runtime = copilot_runtime.CopilotRuntime(
        make_ctx(tmp_path, copilot_cfg(), sandbox=fake_sandbox))
    with pytest.raises(SandboxError) as excinfo:
        runtime.run("task")
    assert excinfo.value is error


def test_missing_extras_fail_fast_naming_the_install(tmp_path, monkeypatch):
    from agentic_dev_loop.engine.runtimes.factory import build_runtime

    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    with pytest.raises(RuntimeSetupError, match=r"agentic-dev-loop\[claude\]"):
        build_runtime(claude_cfg(), make_ctx(tmp_path, claude_cfg()))
    monkeypatch.delitem(sys.modules, "openai_codex", raising=False)
    with pytest.raises(RuntimeSetupError, match=r"agentic-dev-loop\[copilot\]"):
        build_runtime(copilot_cfg(), make_ctx(tmp_path, copilot_cfg()))


def test_openhands_provider_builds_without_optional_drivers(tmp_path):
    from agentic_dev_loop.engine.runtimes.factory import build_runtime
    from agentic_dev_loop.engine.runtimes.openhands_runtime import OpenHandsRuntime

    cfg = PipelineConfig.model_validate({
        "name": "pipe", "llm": {"model": "m", "api_key_env": "K"},
        "agents_dir": "agents", "lockfiles": ["uv.lock"],
        "steps": [{"name": "dev", "agent": "dev", "on_pass": "success"}],
    })
    runtime = build_runtime(cfg, make_ctx(tmp_path, cfg))
    assert isinstance(runtime, OpenHandsRuntime)
    assert "claude_agent_sdk" not in sys.modules
    assert "openai_codex" not in sys.modules


def test_claude_sdk_that_cannot_disable_native_tools_fails_construction(
        tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    write_agent(tmp_path)
    module = _fake_claude_sdk()

    class WeakOptions(FakeOptions):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.setting_sources = ["project"]

    module.ClaudeAgentOptions = WeakOptions
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    from agentic_dev_loop.engine.runtimes.claude_runtime import ClaudeAgentRuntime

    with pytest.raises(RuntimeSetupError, match="cannot enforce harness hardening"):
        ClaudeAgentRuntime(make_ctx(tmp_path, claude_cfg()))
