"""Agent file loading + SDK factory mechanics.

The SDK's built-in tools are registered at import (its registry is global);
consumer tools are registered by extensions via `Registry.add_tool`. Unit-3
adds the sandbox terminal seam: `SandboxTerminalExecutor` delegates each
terminal action to a `Sandbox.run`, and `register_sandbox_terminal` re-registers
the `terminal` tool name per run (KD-4 / §6)."""

from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk import LLM
from openhands.sdk.tool import ToolExecutor
from openhands.tools.file_editor import FileEditorTool  # noqa: F401 - registers "file_editor"
from openhands.tools.task_tracker import TaskTrackerTool  # noqa: F401 - registers "task_tracker"
from openhands.tools.terminal import TerminalTool  # noqa: F401 - registers "terminal"

from .forbidden import ForbiddenRule, match_forbidden_command
from .isolation import (SandboxError, SandboxExecutionRequest, command_argv,
                        resolve_sandbox_env)

if TYPE_CHECKING:
    from openhands.sdk.agent.agent import Agent

    from .isolation import Sandbox


def load_agent(agents_dir: Path | str, name: str, llm: LLM) -> "Agent":
    """Load `<agents_dir>/<name>.agent.md` and build the Agent via the SDK
    factory (frontmatter tool names resolved against the registry, unknown →
    ValueError; the markdown body is delivered as the system prompt suffix).

    Tool surface (BR-1): the frontmatter `tools:` list is the exact
    capability allowlist - every name must resolve to a registered SDK tool.
    `FinishTool` (done signal) and `ThinkTool` (reasoning scaffold) are added
    by the SDK as default infra, never part of the allowlist and never
    removable via `tools:`. There is no regex or default-tools knob.

    Two pipeline policies on top of the SDK's native AgentDefinition loader:
    the file must exist (fail fast - the SDK's dir loader would silently
    skip it) and its frontmatter name must match its filename. A file with
    no frontmatter or no name gets the SDK's stem fallback ("<name>.agent"),
    which can never match the stem - so both degenerate cases fail the name
    check."""
    from openhands.sdk.subagent.registry import agent_definition_to_factory
    from openhands.sdk.subagent.schema import AgentDefinition

    path = Path(agents_dir) / f"{name}.agent.md"
    if not path.is_file():
        raise FileNotFoundError(f"Agent file not found: {path}")

    agent_def = AgentDefinition.load(path)

    stem = path.name.removesuffix(".agent.md")
    if agent_def.name != stem:
        raise ValueError(
            f"Agent file {path} name {agent_def.name!r} does not match "
            f"filename {stem!r}")
    return agent_definition_to_factory(agent_def)(llm)


class SandboxTerminalExecutor(ToolExecutor):
    """A stateless terminal executor: each action is a fresh ``Sandbox.run``
    (no persistent shell session). ``is_input`` degrades to an unsupported
    observation; ``reset`` is a no-op (there is no session to reset)."""

    def __init__(self, sandbox: "Sandbox", *, workspace: str = "",
                 env: dict[str, str] | None = None,
                 pipeline: str | None = None, step: str | None = None,
                 network: bool = False, mount_set=None, egress_policy=None,
                 terminal_timeout: float | None = None,
                 on_terminal_result=None,
                 forbidden_commands: tuple[ForbiddenRule, ...] | None = None,
                 allowlist: tuple[str, ...] | None = None):
        self._sandbox = sandbox
        self._workspace = workspace
        self._env = env or {}
        self._pipeline = pipeline
        self._step = step
        self._network = network
        self._mount_set = mount_set
        self._egress_policy = egress_policy
        self._terminal_timeout = terminal_timeout
        self._on_terminal_result = on_terminal_result
        self._forbidden_commands = forbidden_commands
        self._allowlist = allowlist

    def __call__(self, action, conversation=None):
        from openhands.tools.terminal import TerminalObservation
        from openhands.tools.terminal.metadata import CmdOutputMetadata

        if action.reset:
            return TerminalObservation.from_text(
                "[reset unsupported - stateless sandbox]",
                command=action.command, exit_code=None, timeout=False,
                metadata=CmdOutputMetadata(exit_code=-1))

        if action.is_input:
            return TerminalObservation.from_text(
                "[input unsupported - stateless sandbox]",
                command=action.command, exit_code=None, timeout=False,
                metadata=CmdOutputMetadata(exit_code=-1))

        timeout = action.timeout
        if timeout is None and self._terminal_timeout is not None:
            timeout = self._terminal_timeout
        argv = command_argv(action.command)
        forbidden = match_forbidden_command(argv, self._forbidden_commands)
        if forbidden is not None:
            return TerminalObservation.from_text(
                f"[forbidden: {forbidden}]", is_error=True,
                command=action.command, exit_code=None, timeout=False,
                metadata=CmdOutputMetadata(exit_code=-1))
        request = SandboxExecutionRequest(
            argv=argv, cwd=self._workspace,
            env=self._env, timeout=timeout,
            pipeline=self._pipeline, step=self._step, network=self._network,
            mount_set=self._mount_set, egress_policy=self._egress_policy,
            allowlist=self._allowlist)
        try:
            result = self._sandbox.run(request)
        except SandboxError as exc:
            # 030, D3: a sticky infra failure proves the exec path is dead, so
            # tell the SDK run loop to break this step instead of flapping to
            # max_iterations. See COMPAT-SHIMS.md for the reliance + canary.
            if (conversation is not None
                    and exc is self._sandbox.infra_error):
                from openhands.sdk.conversation.state import (
                    ConversationExecutionStatus,
                )
                conversation.state.execution_status = (
                    ConversationExecutionStatus.STUCK
                )
            raise
        if self._on_terminal_result is not None:
            self._on_terminal_result(
                action.command, result.exit_code, result.wrote_workspace)
        output = result.stdout + result.stderr
        return TerminalObservation.from_text(
            output, is_error=not result.succeeded and not result.is_timeout,
            command=action.command, exit_code=result.exit_code,
            timeout=result.timed_out,
            metadata=CmdOutputMetadata(
                exit_code=result.exit_code if result.exit_code is not None else -1,
                working_dir=self._workspace))

    def close(self) -> None:
        pass  # the sandbox is closed by Runtime; idempotent


def register_sandbox_terminal(sandbox: "Sandbox", *, env: dict[str, str | None] | None = None,
                              workspace: str = "", pipeline: str | None = None,
                              step: str | None = None,
                              network: bool = False,
                              mount_set=None, egress_policy=None,
                              terminal_timeout: float | None = None,
                              on_terminal_result=None,
                              secrets: dict[str, str] | None = None,
                              forbidden_commands: tuple[ForbiddenRule, ...] | None = None,
                              allowlist: tuple[str, ...] | None = None) -> None:
    """Per-run re-registration of the `terminal` name (KD-4): a callable
    factory that builds the SDK terminal with a `SandboxTerminalExecutor`.
    Pipelines are sequential, so re-registering per agent step is safe.

    ``secrets`` (019) is the per-step resolved secret env, injected into the
    terminal's exec env only for steps that declared the refs."""
    from openhands.sdk.tool import register_tool

    resolved_env = resolve_sandbox_env(env, secrets=secrets)

    def factory(conv_state, **params):
        return TerminalTool.create(
            conv_state,
            executor=SandboxTerminalExecutor(
                sandbox, workspace=workspace, env=resolved_env,
                pipeline=pipeline, step=step, network=network,
                mount_set=mount_set, egress_policy=egress_policy,
                terminal_timeout=terminal_timeout,
                on_terminal_result=on_terminal_result,
                forbidden_commands=forbidden_commands,
                allowlist=allowlist))

    register_tool("terminal", factory)
