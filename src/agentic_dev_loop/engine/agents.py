"""Agent file loading + SDK factory mechanics.

The SDK's built-in tools are registered at import (its registry is global);
consumer tools are registered by extensions via `Registry.add_tool`. Unit-3
adds the sandbox terminal seam: `SandboxTerminalExecutor` delegates each
terminal action to a `Sandbox.run`, and `register_sandbox_terminal` re-registers
the `terminal` tool name per run (KD-4 / §6)."""

from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk import LLM
from openhands.sdk.tool import (Action, Observation, ToolAnnotations,
                                ToolDefinition, ToolExecutor, register_tool)
from pydantic import Field
from openhands.tools.file_editor import FileEditorTool  # noqa: F401 - registers "file_editor"
from openhands.tools.task_tracker import TaskTrackerTool  # noqa: F401 - registers "task_tracker"
from openhands.tools.terminal import TerminalTool  # noqa: F401 - registers "terminal"

from .forbidden import ForbiddenRule, match_forbidden_command
from .brief_prompt import render_brief_prompt
from agentic_dev_loop.core.verdict import RECORD_VERDICT_TOOL

from .isolation import (SandboxError, SandboxExecutionRequest, command_argv,
                        resolve_sandbox_env)

if TYPE_CHECKING:
    from openhands.sdk.agent.agent import Agent

    from .isolation import Sandbox

# 049, D3: the internal brief agent name is a fixed UUID4 string, never the
# literal "brief", so a consumer vendoring a `brief.agent.md` cannot mint a
# second AgentDefinition under the same name. The vendored-file warning still
# keys on the literal filename `brief.agent.md` (that is the file to delete).
BRIEF_AGENT_NAME = "4f1c9e2b-7a3d-4e5f-8b6c-0d1a2f3c4d5e"


def check_required_tools(tools: list[str], required: tuple[str, ...],
                         *, agent_name: str) -> None:
    """Fail loudly when a step's contract needs a tool the agent file does not
    grant (BR-1: the frontmatter list is the exact allowlist)."""
    missing = [name for name in required if name not in tools]
    if missing:
        raise ValueError(
            f"agent {agent_name!r} does not grant required tools "
            f"{missing}; add them to its frontmatter 'tools:' allowlist")


def validate_agent_contracts(cfg, agents_dir: Path | str) -> None:
    """Load-time contract check (unit-11, B-2): a step configured with
    ``verdicts:`` requires ``record_verdict`` in its agent's tool allowlist.

    A missing agent file is left to ``load_agent`` (which fails fast naming
    it); this check only inspects files that exist so graph construction stays
    a pure config pass for fake-agent test harnesses."""
    from openhands.sdk.subagent.schema import AgentDefinition

    for step in cfg.steps:
        if step.agent is None or step.verdicts is None:
            continue
        path = Path(agents_dir) / f"{step.agent}.agent.md"
        if not path.is_file():
            continue
        agent_def = AgentDefinition.load(path)
        check_required_tools(agent_def.tools, (RECORD_VERDICT_TOOL,),
                             agent_name=step.agent)


def load_agent(agents_dir: Path | str, name: str, llm: LLM,
               required_tools: tuple[str, ...] = ()) -> "Agent":
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
    check_required_tools(agent_def.tools, required_tools, agent_name=name)
    return agent_definition_to_factory(agent_def)(llm)


def load_brief_agent(name: str, sections: list[str], llm: LLM) -> "Agent":
    """Build the brief agent from the engine-embedded prompt (046).

    The brief prompt is engine-owned and versioned; it is never loaded from
    ``agents_dir``. The OUTPUT contract's headings are rendered from the
    configured ``brief_inputs.sections``, so the prompt and the coverage gate
    share one list and cannot diverge (043)."""
    from openhands.sdk.subagent.registry import agent_definition_to_factory
    from openhands.sdk.subagent.schema import AgentDefinition

    agent_def = AgentDefinition(
        name=name,
        tools=["terminal"],
        system_prompt=render_brief_prompt(sections),
    )
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


class RecordVerdictAction(Action):
    """The structured verdict channel's input (unit-11, FR-11.2)."""

    verdict: str = Field(
        description=(
            "The verdict keyword for this step, exactly as configured in the "
            "pipeline's `verdicts:` map (for example APPROVED or NEEDS "
            "CHANGES)."))


class RecordVerdictObservation(Observation):
    """A visible acknowledgement: the wire invariant (D-3) requires the tool
    result to reach the model, so this is never empty."""


class RecordVerdictExecutor(ToolExecutor):
    def __call__(self, action: RecordVerdictAction,
                 conversation=None) -> RecordVerdictObservation:
        return RecordVerdictObservation.from_text(
            f"recorded verdict: {action.verdict}")


class RecordVerdictTool(
        ToolDefinition[RecordVerdictAction, RecordVerdictObservation]):
    """Registers `record_verdict`; validation against the step's configured
    verdict set happens in `Verdict.from_events`, never in the tool schema."""

    @classmethod
    def create(cls, conv_state=None, **params):
        if params:
            raise ValueError("RecordVerdictTool does not accept parameters")
        return [
            cls(
                action_type=RecordVerdictAction,
                observation_type=RecordVerdictObservation,
                description=(
                    "Record this step's verdict. Call exactly once when your "
                    "work is complete, passing the configured verdict keyword. "
                    "Never state the verdict only in prose: the pipeline reads "
                    "this tool call, not your final message."),
                executor=RecordVerdictExecutor(),
                annotations=ToolAnnotations(
                    title=RECORD_VERDICT_TOOL,
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(RECORD_VERDICT_TOOL, RecordVerdictTool)
