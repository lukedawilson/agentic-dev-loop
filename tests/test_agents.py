"""load_agent tests - ported from the consumer's TestLoadAgent, retargeted at the
library signature which also builds the Agent via the SDK factory. Real
agent files on disk (fail-fast file policy); real LLM/Agent SDK objects
(constructed locally, no network)."""

from pathlib import Path

import pytest
from openhands.sdk import LLM
from openhands.sdk.agent.agent import Agent

from agentic_dev_loop.engine.agents import load_agent


@pytest.fixture
def llm():
    return LLM(model="openai/test-model", api_key="sk-test")


@pytest.fixture
def agents_dir(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    return d


def make_agent(dir: Path, name: str, frontmatter: str, body: str) -> Path:
    path = dir / f"{name}.agent.md"
    path.write_text(f"---\n{frontmatter}\n---\n{body}")
    return path


class TestLoadAgent:
    """load_agent parses agent files with the SDK's native AgentDefinition
    loader, keeping two pipeline policies on top: the file must exist (fail
    fast - the SDK's dir loader would silently skip it) and its frontmatter
    name must match its filename."""

    def test_builds_agent_from_file(self, agents_dir, llm):
        make_agent(agents_dir, "dev",
                   "name: dev\ntools: [terminal, file_editor]\n",
                   "# System prompt\nDo the work.")

        agent = load_agent(agents_dir, "dev", llm)

        assert isinstance(agent, Agent)

    def test_raises_if_file_missing(self, agents_dir, llm):
        with pytest.raises(FileNotFoundError):
            load_agent(agents_dir, "nonexistent", llm)

    def test_raises_if_no_frontmatter(self, agents_dir, llm):
        (agents_dir / "bad.agent.md").write_text("no frontmatter here\n")
        with pytest.raises(ValueError):
            load_agent(agents_dir, "bad", llm)

    def test_raises_if_missing_name_field(self, agents_dir, llm):
        make_agent(agents_dir, "bad", "tools: []\n", "# Body")
        with pytest.raises(ValueError):
            load_agent(agents_dir, "bad", llm)

    def test_raises_if_name_mismatches_filename(self, agents_dir, llm):
        make_agent(agents_dir, "dev", "name: other\ntools: []\n", "# Body")
        with pytest.raises(ValueError, match="does not match"):
            load_agent(agents_dir, "dev", llm)

    def test_frontmatter_with_extra_fields_ok(self, agents_dir, llm):
        make_agent(agents_dir, "extra",
                   "name: extra\ntools: [terminal]\nextra: ignored\n", "# Body")

        assert isinstance(load_agent(agents_dir, "extra", llm), Agent)

    def test_unknown_tool_name_fails(self, agents_dir, llm):
        """The SDK factory resolves frontmatter tool names against the
        registry - an unknown name fails loudly."""
        make_agent(agents_dir, "bad", "name: bad\ntools: [no_such_tool]\n", "# Body")
        with pytest.raises(ValueError):
            load_agent(agents_dir, "bad", llm)

    def test_empty_tools_builds_infra_only(self, agents_dir, llm):
        """`tools: []` yields no capability tools - only the SDK-default
        infra (FinishTool + ThinkTool) and no regex filtering (BR-1.3,
        BR-1.4, FD-2b)."""
        make_agent(agents_dir, "bare", "name: bare\ntools: []\n", "# Body")
        agent = load_agent(agents_dir, "bare", llm)
        assert agent.tools == []
        assert agent.include_default_tools == ["FinishTool", "ThinkTool"]
        assert agent.filter_tools_regex is None


from agentic_dev_loop.engine.isolation import SandboxError


class TestSandboxTerminalExecutor:
    """The terminal seam (KD-4): each action is a fresh Sandbox.run through a
    SandboxTerminalExecutor; is_input degrades; git mutations refuse loudly."""

    def test_executes_through_sandbox(self, fake_sandbox, tmp_path):
        from openhands.tools.terminal import TerminalAction

        from agentic_dev_loop.engine.agents import SandboxTerminalExecutor
        ex = SandboxTerminalExecutor(fake_sandbox, workspace=str(tmp_path),
                                     env={"PATH": "/bin"})
        obs = ex(TerminalAction(command="echo hi"), None)
        assert obs.exit_code == 0
        assert "hi" in obs.text
        assert len(fake_sandbox.requests) == 1
        assert fake_sandbox.requests[0].argv == ["echo", "hi"]

    def test_is_input_degrades(self, fake_sandbox, tmp_path):
        from openhands.tools.terminal import TerminalAction

        from agentic_dev_loop.engine.agents import SandboxTerminalExecutor
        ex = SandboxTerminalExecutor(fake_sandbox, workspace=str(tmp_path), env={})
        obs = ex(TerminalAction(command="x", is_input=True), None)
        assert obs.exit_code is None
        assert "input unsupported" in obs.text
        assert fake_sandbox.requests == []

    def test_reset_degrades(self, fake_sandbox, tmp_path):
        from openhands.tools.terminal import TerminalAction

        from agentic_dev_loop.engine.agents import SandboxTerminalExecutor
        ex = SandboxTerminalExecutor(fake_sandbox, workspace=str(tmp_path), env={})
        obs = ex(TerminalAction(command="", reset=True), None)
        assert obs.exit_code is None
        assert "reset unsupported" in obs.text
        assert fake_sandbox.requests == []

    def test_git_mutation_refuses(self, fake_sandbox, tmp_path):
        from openhands.tools.terminal import TerminalAction

        from agentic_dev_loop.engine.agents import SandboxTerminalExecutor
        ex = SandboxTerminalExecutor(fake_sandbox, workspace=str(tmp_path), env={})
        with pytest.raises(SandboxError, match="AE-SBX-008"):
            ex(TerminalAction(command="git commit -m x"), None)

    def test_infra_error_sets_stuck_and_reraised(self, fake_sandbox, tmp_path):
        from openhands.sdk.conversation.state import ConversationExecutionStatus
        from openhands.tools.terminal import TerminalAction

        from agentic_dev_loop.engine.agents import SandboxTerminalExecutor

        err = SandboxError("AE-SBX-019: sandbox infrastructure failure")
        fake_sandbox.infra_error = err

        def boom(request):
            raise err

        fake_sandbox.script(boom)

        class State:
            execution_status = None

        class Conv:
            state = State()

        conv = Conv()
        ex = SandboxTerminalExecutor(fake_sandbox, workspace=str(tmp_path), env={})
        with pytest.raises(SandboxError, match="AE-SBX-019"):
            ex(TerminalAction(command="echo hi"), conv)
        assert conv.state.execution_status == ConversationExecutionStatus.STUCK

    def test_timeout_propagates(self, fake_sandbox, tmp_path):
        from openhands.tools.terminal import TerminalAction

        from agentic_dev_loop.engine.agents import SandboxTerminalExecutor
        ex = SandboxTerminalExecutor(fake_sandbox, workspace=str(tmp_path), env={})
        fake_sandbox.script(lambda req: (None, "out", True))
        obs = ex(TerminalAction(command="sleep 5", timeout=1), None)
        assert obs.timeout is True

    def test_network_reaches_request(self, fake_sandbox, tmp_path):
        from openhands.tools.terminal import TerminalAction

        from agentic_dev_loop.engine.agents import SandboxTerminalExecutor
        ex = SandboxTerminalExecutor(fake_sandbox, workspace=str(tmp_path),
                                     env={}, network=True)
        ex(TerminalAction(command="curl https://example.com"), None)
        assert fake_sandbox.requests[0].network is True

    def test_network_defaults_off(self, fake_sandbox, tmp_path):
        from openhands.tools.terminal import TerminalAction

        from agentic_dev_loop.engine.agents import SandboxTerminalExecutor
        ex = SandboxTerminalExecutor(fake_sandbox, workspace=str(tmp_path), env={})
        ex(TerminalAction(command="echo hi"), None)
        assert fake_sandbox.requests[0].network is False


class TestRegisterSandboxTerminal:
    def test_resolves_to_sandbox_executor(self, fake_sandbox, tmp_path):
        from openhands.sdk.tool.registry import resolve_tool
        from openhands.sdk.tool.spec import Tool

        from agentic_dev_loop.engine.agents import (SandboxTerminalExecutor,
                                             register_sandbox_terminal)
        register_sandbox_terminal(fake_sandbox, workspace=str(tmp_path),
                                  env={"X": None})

        class Workspace:
            working_dir = str(tmp_path)

        class ConvState:
            workspace = Workspace()
            env_observation_persistence_dir = None

        tools = resolve_tool(Tool(name="terminal"), ConvState())
        assert len(tools) == 1
        assert isinstance(tools[0].executor, SandboxTerminalExecutor)
