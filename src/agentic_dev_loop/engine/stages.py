"""Stage nodes, routers, and the run_agent port.

The consumer loop's hardcoded stages become per-step closures built from the
pipeline config; the fixed policies stay here, not in config:

- per-attempt hygiene: every declared artifact/produces file + last-error.md
  is deleted at attempt start, so stale files can't be misread
- capture-first feedback: the pipeline captures each agent's final response
  and writes the artifact itself - agents no longer file their own reports
- verdict-first truncation: a clear verdict stands even if the run was cut
  short before the agent formally finished
- empty response / unclear verdict → implicit, attempt-bounded retry with the
  captured text surfaced into notes (never routable - policy, not config)
- notes accumulate across attempts into the next attempt's context
- command steps are mechanical: the exit code is the verdict (0 ⇒ pass,
  anything else ⇒ fail), a timeout kills the whole process group, and the
  output tail rides into the retry note - "unclear" is impossible
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from langgraph.graph import END
from openhands.sdk import Conversation

from .agent_error_guide import guide_section
from .agents import load_agent, register_sandbox_terminal
from agentic_dev_loop.core.config import TERMINALS, PipelineConfig, Route, StepConfig, route_target
from agentic_dev_loop.core.paths import _has_wildcards
from .deps import extension_message, parse_missing_dependencies, policy_injection
from .egress_policy import assemble_egress_policy
from .forbidden import resolve_forbidden_rules
from .isolation import (SandboxError, SandboxExecutionRequest, command_argv,
                        resolve_sandbox_env)
from .mounts import resolve_mount_set
from .outputs import CapturedOutput
from .secrets import redact_values, step_secret_env
from agentic_dev_loop.core.verdict import Verdict
from .proc import (child_env, host_shell_execution, kill_process_tree,
                   needs_shell, split_command)
from .registry import Registry

if TYPE_CHECKING:
    from openhands.sdk import LLM

    from .isolation import Sandbox

DEFAULT_MAX_ITERATIONS = 500
DEFAULT_COMMAND_TIMEOUT = 600.0  # seconds - a pytest-class suite budget
LAST_ERROR = "last-error.md"
ARTIFACT_CONTEXT_CAP = 24_000  # chars per artifact injected into a step's context


class _TurnPauseController:
    """Turn-level pause callback (032): pause the SDK run loop after every
    agent-sourced event so the caller can drive one turn at a time. The
    finalize nudge (035) reuses this hook without any stop-when state."""

    def __init__(self) -> None:
        self.conversation = None

    def attach(self, conversation) -> None:
        self.conversation = conversation

    def on_event(self, event) -> None:
        if self.conversation is None:
            return
        if getattr(event, "source", None) == "agent":
            from openhands.sdk.conversation.state import (
                ConversationExecutionStatus,
            )
            self.conversation.state.execution_status = (
                ConversationExecutionStatus.PAUSED
            )


class _FinalizeNudgeController(_TurnPauseController):
    """Finalize nudge (035): template text, never step-named. The turn loop
    asks for the message when the conversation reaches the configured index;
    ``nudged`` guards against re-injection on a same-turn retry."""

    _TEMPLATE = "{n} turns remain: produce your final response now"

    def __init__(self, turns: int) -> None:
        super().__init__()
        self.turns = turns
        self.nudged = False

    def message(self) -> str:
        return self._TEMPLATE.format(n=self.turns)


class _StopWhenController(_TurnPauseController):
    """Turn-level stop-when-green observation (032).

    Pure per-event state machine - no knowledge of how the conversation is
    driven. ``on_event`` is a SDK conversation callback: it records the most
    recent matching terminal observation and any subsequent file-editor
    write, and (via the base pause) pauses the conversation after every
    agent-sourced event so the caller can drive one turn at a time.
    """

    NUDGE = "Step complete: produce your final response now"
    _WRITE_COMMANDS = frozenset({"create", "str_replace", "insert", "undo_edit"})

    def __init__(self, spec) -> None:
        super().__init__()
        self._pattern = re.compile(spec.green_command)
        self.post_green_turns = spec.post_green_turns
        self.green_hit = False
        self.writes_since_green = False
        self.nudged = False
        self.post_nudge_turns = 0

    @property
    def green_signal(self) -> bool:
        return self.green_hit and not self.writes_since_green

    def on_event(self, event) -> None:
        super().on_event(event)
        if self.conversation is None:
            return
        from openhands.sdk.event import ObservationEvent

        if isinstance(event, ObservationEvent):
            self._record_observation(event)

    def on_terminal_result(self, command: str | None, exit_code: int | None,
                           wrote_workspace: bool) -> None:
        """Feed the sandbox terminal result channel (032): a matching green
        command is the signal; any other terminal write resets it."""
        if command is not None and self._pattern.search(command):
            self.green_hit = exit_code == 0
            self.writes_since_green = False
        elif wrote_workspace:
            self.writes_since_green = True

    def _record_observation(self, event) -> None:
        observation = event.observation
        tool_name = event.tool_name
        if tool_name == "terminal":
            command = getattr(observation, "command", None)
            if command is not None and self._pattern.search(command):
                self.green_hit = getattr(observation, "exit_code", None) == 0
                self.writes_since_green = False
        elif tool_name == "file_editor":
            command = getattr(observation, "command", None)
            if command in self._WRITE_COMMANDS and not observation.is_error:
                self.writes_since_green = True


@dataclass
class Runtime:
    """Everything stage nodes need that isn't in LoopState: pipeline config,
    extension registry, the shared LLM, and resolved (absolute) paths."""
    cfg: PipelineConfig
    registry: Registry
    llm: "LLM"
    agents_dir: Path
    state_dir: Path
    workspace: Path
    sandbox: Sandbox | None = None
    # unit-5: the .git-free export root sandboxed steps run in (BR-2); None =
    # the real workspace (no export materialised - fake/legacy paths).
    sandbox_cwd: Path | None = None
    secrets: dict[str, str] = field(default_factory=dict)
    # 036 lane boundary (v1): the active unit's engine-allowlist.yaml paths;
    # None = unrestricted (no allowlist file).
    engine_allowlist: list[str] | None = None

    def run_agent(self, agent_name: str, message: str,
                  max_iterations: int = DEFAULT_MAX_ITERATIONS,
                  step: StepConfig | None = None) -> AgentResult:
        """Run one agent stage. The agent file's body is delivered as a system
        message suffix (AgentContext); the user turn carries only the task.
        Any failure - setup or run - is reported to <state_dir>/last-error.md.

        For steps declaring outputs (produces/artifact), the final response is
        captured from conv.state.events (before conv.close()) and written by the
        pipeline to the declared artifact path - the agent never files its own
        report."""
        conv = None
        try:
            from openhands.sdk.conversation.state import ConversationExecutionStatus

            controller = None
            if step is not None and step.stop_when is not None:
                controller = _StopWhenController(step.stop_when)
            finalize_controller = None
            if step is not None and step.finalize_nudge_turns is not None:
                finalize_controller = _FinalizeNudgeController(
                    step.finalize_nudge_turns)

            if self.sandbox is not None:
                if step is not None:
                    cwd, mount_set, egress_policy = _step_sandbox(self, step)
                else:
                    cwd, mount_set, egress_policy = (self.workspace, None, None)
                forbidden_commands = (
                    resolve_forbidden_rules(step.forbidden_commands,
                                            step.network)
                    if step is not None else None)
                allowlist = (
                    tuple(self.engine_allowlist)
                    if self.engine_allowlist is not None else None)
                register_sandbox_terminal(
                    self.sandbox,
                    env=step.env if step is not None else None,
                    workspace=str(cwd),
                    pipeline=self.cfg.name,
                    step=step.name if step is not None else None,
                    network=step.network if step is not None else False,
                    mount_set=mount_set,
                    egress_policy=egress_policy,
                    terminal_timeout=(step.terminal_timeout
                                      if step is not None else None),
                    on_terminal_result=(controller.on_terminal_result
                                        if controller is not None else None),
                    secrets=(step_secret_env(self.cfg, step, self.secrets)
                             if step is not None else None),
                    forbidden_commands=forbidden_commands,
                    allowlist=allowlist)
            agent = load_agent(self.agents_dir, agent_name, self.llm)
            callbacks = []
            if controller is not None:
                callbacks.append(controller.on_event)
            if finalize_controller is not None:
                callbacks.append(finalize_controller.on_event)
            conv = Conversation(agent=agent, workspace=str(self.workspace),
                                max_iteration_per_run=max_iterations,
                                callbacks=callbacks or None)
            if controller is not None:
                controller.attach(conv)
            if finalize_controller is not None:
                finalize_controller.attach(conv)
            conv.send_message(message)
            if controller is not None or finalize_controller is not None:
                self._run_stop_when_loop(conv, controller, max_iterations,
                                         finalize_controller)
            else:
                conv.run()

            # 030, D1/D2: a sticky infra failure must fail the run with no
            # retry - check before any capture/verdict path would schedule an
            # unclear-retry. The existing except turns this into ok=False.
            if self.sandbox is not None and self.sandbox.infra_error is not None:
                raise self.sandbox.infra_error

            truncated = conv.state.execution_status != ConversationExecutionStatus.FINISHED

            if step is not None and (step.artifact is not None or step.produces):
                artifact = step.artifact if step.artifact is not None else step.produces[0]
                captured = CapturedOutput.from_agent_result(
                    conv, artifact, self.state_dir,
                    redactions=tuple(self.secrets.values()))
                if captured.is_err():
                    return AgentResult(ok=True, truncated=truncated,
                                       capture_error=captured.error.value)
                write_result = captured.unwrap().write()
                if write_result.is_err():
                    raise RuntimeError(
                        f"captured output write failed: {write_result.error.value}")
                return AgentResult(ok=True, truncated=truncated,
                                   response=captured.unwrap().raw_text)
            return AgentResult(ok=True, truncated=truncated)
        except Exception:
            err = redact_values(traceback.format_exc(), self.secrets.values())
            print(err[-4000:], file=sys.stderr, flush=True)
            self.state_dir.mkdir(parents=True, exist_ok=True)
            error_path = self.state_dir / LAST_ERROR
            error_path.write_text(
                f"# Agent run failed\n\n```\n{err[-4000:]}\n```",
                encoding="utf-8")
            print(f"Agent run failed - error details written to {error_path}", flush=True)
            return AgentResult(ok=False, truncated=False)
        finally:
            if conv is not None:
                conv.close()

    def _run_stop_when_loop(self, conv,
                            controller: _StopWhenController | None,
                            max_iterations: int,
                            finalize_controller: _FinalizeNudgeController | None = None) -> None:
        """Drive one turn at a time so turn-level hooks can inject their
        nudges and enforce budgets. A controller's callback pauses the SDK
        run loop after every agent-sourced event, so each ``conv.run()`` here
        advances exactly one agent turn."""
        from openhands.sdk.conversation.state import ConversationExecutionStatus

        turns = 0
        nudge_at = (max_iterations - finalize_controller.turns
                    if finalize_controller is not None else None)
        while True:
            conv.run()
            status = conv.state.execution_status
            if status in (ConversationExecutionStatus.FINISHED,
                          ConversationExecutionStatus.STUCK,
                          ConversationExecutionStatus.ERROR):
                break

            turns += 1
            if turns >= max_iterations:
                conv.state.execution_status = ConversationExecutionStatus.ERROR
                break

            if (finalize_controller is not None and turns == nudge_at
                    and not finalize_controller.nudged):
                conv.send_message(finalize_controller.message())
                finalize_controller.nudged = True
                continue

            if controller is not None:
                if not controller.nudged and controller.green_signal:
                    conv.send_message(controller.NUDGE)
                    controller.nudged = True
                    if controller.post_green_turns == 0:
                        break
                    continue

                if controller.nudged:
                    controller.post_nudge_turns += 1
                    if controller.post_nudge_turns >= controller.post_green_turns:
                        break


def prompt_dir(runtime: Runtime) -> str:
    """state_dir as agents must address it. Agents resolve prompt paths
    against their workspace, so a bare `state_dir.name` only reaches the real
    state_dir when the pipeline file sits at the workspace root - the consumer runs
    agentic-dev-loop/pipeline.yaml from the repo root, where `.pr` and
    `agentic-dev-loop/.pr` diverge. Workspace-relative when under the workspace,
    the absolute path when outside it."""
    try:
        return runtime.state_dir.relative_to(runtime.workspace).as_posix()
    except ValueError:
        return runtime.state_dir.as_posix()


def _step_sandbox(runtime: Runtime, step: StepConfig):
    """Resolve a step's sandbox execution context (unit-5): the cwd inside the
    export (or the workspace when no export exists), plus the resolved mount
    set (BR-1) and egress policy (BR-5/BR-8). Pure resolution - no I/O."""
    cwd = runtime.sandbox_cwd or runtime.workspace
    mount_set = resolve_mount_set(step.mounts)
    egress_policy = assemble_egress_policy(step.egress)
    return cwd, mount_set, egress_policy


@dataclass
class AgentResult:
    """Outcome of a single agent stage run.

    ok=False only on unhandled exceptions. truncated=True when the run ended
    without the agent reaching FINISHED - the turn cap (ERROR) or the SDK's
    stuck detector (STUCK) both land here. response carries the captured final
    agent message (capture-applying steps only); capture_error is set when the
    agent finished but produced no final response ("empty_response")."""
    ok: bool = False
    truncated: bool = False
    response: str | None = None
    capture_error: str | None = None


def _run_command(command: str, workspace: Path, timeout: float,
                 env: dict[str, str] | None = None,
                 sandbox: Sandbox | None = None,
                 pipeline: str | None = None,
                 step: str | None = None,
                 network: bool = False,
                 mount_set=None,
                 egress_policy=None,
                 cwd: Path | None = None,
                 allowlist: tuple[str, ...] | None = None) -> tuple[int | None, str, bool]:
    """Run a command step in the workspace, capturing combined stdout/stderr.

    With a ``Sandbox`` the command routes through the isolation boundary: git
    guard (inside ``Sandbox.run``) → ``SandboxExecutionRequest`` (carrying the
    resolved mount set + egress policy, unit-5) → exec. Without one the unit-2
    host-subprocess path runs unchanged (backward compatible): no shell
    metacharacters → argv-style (``shell=False``); metacharacters →
    ``shell=True``; both under the scrubbed ``child_env`` (BR-4).

    Returns ``(exit_code, output, timed_out)``. ``exit_code`` is None when the
    command exceeded ``timeout`` (SIGKILLed host-side, or the container's
    ``timeout(1)`` exit 124)."""
    if sandbox is not None:
        request = SandboxExecutionRequest(
            argv=command_argv(command), cwd=str(cwd or workspace), env=env or {},
            timeout=timeout, pipeline=pipeline, step=step, network=network,
            mount_set=mount_set, egress_policy=egress_policy,
            allowlist=allowlist)
        result = sandbox.run(request)
        return result.exit_code, result.stdout + result.stderr, result.timed_out
    if needs_shell(command):
        argv, shell = host_shell_execution(command)
    else:
        argv = split_command(command)
        shell = False
    proc = subprocess.Popen(
        argv, cwd=workspace, shell=shell, env=env, start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out.decode("utf-8", "replace"), False
    except subprocess.TimeoutExpired:
        kill_process_tree(proc)
        out, _ = proc.communicate()
        return None, out.decode("utf-8", "replace"), True


def _artifact_file(runtime: Runtime, step_name: str) -> str | None:
    """The declared artifact/produces filename a step writes, keyed by step
    name - the artifacts dict only carries step name -> captured text."""
    for step in runtime.cfg.steps:
        if step.name == step_name:
            if step.artifact is not None:
                return step.artifact
            if step.produces:
                return step.produces[0]
            return None
    return None


def build_context(runtime: Runtime, docs: dict[str, str], subject: str,
                  notes: list[str] | None = None,
                  artifacts: dict[str, str] | None = None) -> str:
    """Assemble the attempt's shared message: the always-on agent-facing error
    guide (034), subject, doc bundle, the canonical dependency-policy paragraph
    (021), the expected-output-paths section generated from step defs (a prompt
    merely naming files lets agents skip them), accumulated stage artifacts
    (027), and previous-attempt notes."""
    parts = [f"You are working on {subject}.\n", guide_section()]
    for name, content in docs.items():
        parts.append(f"## {name}\n\n{content}")
    if artifacts:
        parts.append("## Stage artifacts")
        for step_name, content in artifacts.items():
            capped = content[:ARTIFACT_CONTEXT_CAP]
            if len(content) > ARTIFACT_CONTEXT_CAP:
                filename = _artifact_file(runtime, step_name)
                path = (f"{prompt_dir(runtime)}/{filename}" if filename
                        else prompt_dir(runtime))
                capped += (f"\n\n[... content truncated - full text at "
                           f"`{path}`]")
            parts.append(f"### {step_name}\n\n{capped}")
    parts.append("## Dependency policy\n\n" + policy_injection(runtime.cfg))
    lines = []
    for step in runtime.cfg.steps:
        if step.agent is None:
            continue
        for produced in step.produces:
            lines.append(f"{step.name} agent writes: `{prompt_dir(runtime)}/{produced}`")
        if step.artifact is not None:
            lines.append(f"{step.name} agent writes: `{prompt_dir(runtime)}/{step.artifact}`")
    if lines:
        parts.append("## Expected output paths\n\n" + "\n".join(lines))
    if notes:
        parts.append("## Previous attempt feedback\n\n" + "\n\n".join(notes))
    return "\n\n".join(parts)


def declared_artifacts(cfg: PipelineConfig) -> list[str]:
    files = []
    for step in cfg.steps:
        files.extend(step.produces)
        if step.artifact is not None:
            files.append(step.artifact)
    return files


def run_scoped_artifacts(cfg: PipelineConfig) -> list[str]:
    """Artifacts that live for the whole run, not one attempt (033).

    A ``brief_inputs:`` step is the documented pattern: its digest is
    created once at run start and reused by every attempt, so its declared
    outputs are exempt from per-attempt hygiene."""
    files = []
    for step in cfg.steps:
        if step.brief_inputs is None:
            continue
        files.extend(step.produces)
        if step.artifact is not None:
            files.append(step.artifact)
    return files


def clear_attempt_artifacts(runtime: Runtime, *,
                            exempt_run_scoped: bool = True) -> None:
    """Per-attempt hygiene (fixed policy): delete every declared
    artifact/produces file + last-error.md so stale files from earlier
    attempts or runs can't be mistaken for this attempt's output.

    Run-scoped artifacts (033) survive attempt starts and are deleted at run
    start instead - pass ``exempt_run_scoped=False`` for that run-start sweep.
    """
    keep = set(run_scoped_artifacts(runtime.cfg)) if exempt_run_scoped else set()
    for name in [*declared_artifacts(runtime.cfg), LAST_ERROR]:
        if name in keep:
            continue
        (runtime.state_dir / name).unlink(missing_ok=True)


def _resolve_include_spec(workspace: Path, spec: str) -> list[Path]:
    """Resolve one ``brief_inputs.include`` entry to files, sorted by their
    workspace-relative posix path. Literal entries must name an existing file;
    wildcard entries must match at least one file (no silent fallback)."""
    if _has_wildcards(spec):
        matches = sorted(
            (p for p in workspace.glob(spec) if p.is_file()),
            key=lambda p: p.relative_to(workspace).as_posix())
    else:
        path = workspace / spec
        if not path.is_file():
            raise FileNotFoundError(
                f"brief_inputs include entry not found: {spec}")
        matches = [path]
    if not matches:
        raise FileNotFoundError(
            f"brief_inputs include entry matched no files: {spec}")
    return matches


def resolve_brief_inputs(runtime: Runtime, step: StepConfig
                         ) -> tuple[dict[str, str], list[str]]:
    """Resolve ``brief_inputs.include`` against the workspace into
    ``{workspace-relative-path: content}`` and return the env-facts list."""
    if step.brief_inputs is None:
        return {}, []
    included: dict[str, str] = {}
    for spec in step.brief_inputs.include:
        for path in _resolve_include_spec(runtime.workspace, spec):
            key = path.relative_to(runtime.workspace).as_posix()
            included.setdefault(key, path.read_text(encoding="utf-8"))
    return included, list(step.brief_inputs.env_facts)


def validate_brief_inputs(workspace: Path, step: StepConfig) -> None:
    """Fail fast on ``brief_inputs.include`` entries that name no files (033).

    The no-silent-fallback rule demands these surface at load/--dry-run time,
    not on the brief step's first run."""
    if step.brief_inputs is None:
        return
    for spec in step.brief_inputs.include:
        _resolve_include_spec(workspace, spec)


def _resolved_loader_docs(docs: dict[str, str] | None,
                          doc_sources: dict[str, str] | None) -> dict[str, str]:
    """The loader/`additional_files` docs for the brief hash and message.

    When source paths are known (033 D4), re-read them from disk so a
    mid-run edit to the unit plan or an additional file changes the hash -
    the run-start snapshot alone would miss it. Doc keys without a tracked
    source keep their snapshot content (third-party loaders)."""
    if not doc_sources:
        return dict(docs or {})
    resolved = dict(docs or {})
    for name, source in doc_sources.items():
        resolved[name] = Path(source).read_text(encoding="utf-8")
    return resolved


def _brief_sections(runtime: Runtime, step: StepConfig,
                    docs: dict[str, str] | None,
                    doc_sources: dict[str, str] | None = None
                    ) -> tuple[dict[str, str], list[str]]:
    """The brief's full resolved input set: the loader docs (unit plan,
    additional_files) re-read from disk where their sources are known, merged
    with the closed include-file set, plus the env-facts list. Duplicate
    names are de-duplicated; the loader doc already carries that input, so
    the include copy is not repeated."""
    sections: dict[str, str] = {}
    for name, content in _resolved_loader_docs(docs, doc_sources).items():
        sections.setdefault(name, content)
    included, env_facts = resolve_brief_inputs(runtime, step)
    for name, content in included.items():
        sections.setdefault(name, content)
    return sections, env_facts


def brief_inputs_hash(runtime: Runtime, step: StepConfig,
                      docs: dict[str, str] | None = None,
                      doc_sources: dict[str, str] | None = None) -> str:
    """Hash the resolved brief inputs (033): loader docs + include-file
    contents + env facts. Used by the regeneration gate - a change re-runs the
    brief step, otherwise the existing digest is reused without an LLM call."""
    sections, env_facts = _brief_sections(runtime, step, docs, doc_sources)
    digest = hashlib.sha256()
    for key in sorted(sections):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sections[key].encode("utf-8"))
        digest.update(b"\0")
    for fact in env_facts:
        digest.update(fact.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def build_brief_context(runtime: Runtime, step: StepConfig,
                        docs: dict[str, str],
                        doc_sources: dict[str, str] | None = None) -> str:
    """Assemble the brief agent's task message (033): the always-on
    agent-facing error guide (034), the resolved unit/plan docs (re-read from
    disk where sources are known), the closed ``brief_inputs.include`` file
    set, and the env-facts block. Prior verdicts and feedback are deliberately
    absent - the notes channel delivers them per attempt."""
    sections, env_facts = _brief_sections(runtime, step, docs, doc_sources)
    parts = [guide_section()]
    parts += [f"## {name}\n\n{content}" for name, content in sections.items()]
    if env_facts:
        parts.append("## Environment facts\n\n"
                     + "\n".join(f"- {fact}" for fact in env_facts))
    return "\n\n".join(parts)


def _unclear_target(runtime: Runtime, step: StepConfig) -> str:
    """Where the unclear-verdict retry loops to: on_fail's goto when that is
    a step, else the entry step (the consumer always looped back to dev)."""
    target = route_target(step.on_fail)
    if target is not None and target not in TERMINALS:
        return target
    return runtime.cfg.steps[0].name


def _unclear_note(runtime: Runtime, step: StepConfig, attempt: int,
                  text: str | None, truncated: bool, max_iterations: int,
                  error: str | None = None) -> str:
    """Explain an unclear verdict to the next attempt - and surface the
    captured response when it exists, so real findings are never swallowed."""
    expected = ""
    if step.verdicts is not None:
        if step.verdicts.unverifiable is not None:
            expected = (f" (expected `VERDICT: {step.verdicts.pass_}`, "
                        f"`VERDICT: {step.verdicts.fail}`, or "
                        f"`VERDICT: {step.verdicts.unverifiable}` on its own line)")
        else:
            expected = (f" (expected `VERDICT: {step.verdicts.pass_}` or "
                        f"`VERDICT: {step.verdicts.fail}` on its own line)")
    if truncated:
        lead = (f"{step.name} agent was cut short on attempt {attempt} "
                f"(hit the {max_iterations}-turn limit or was stopped "
                f"by stuck detection)")
        if text is not None:
            return (f"## {step.name} output (attempt {attempt} - truncated)\n\n"
                    f"{lead}. Its findings so far:\n\n{text}")
        return f"{lead} and produced no final response."
    if text is None:
        return f"{step.name} agent produced no final response on attempt {attempt}."
    if error == "ambiguous":
        return (f"{step.name} agent's final response on attempt {attempt} carries "
                f"conflicting verdicts{expected}. Its findings so far:\n\n{text}")
    return (f"{step.name} agent's final response on attempt {attempt} contains no "
            f"verdict{expected}. Its findings so far:\n\n{text}")


def make_agent_node(runtime: Runtime, step: StepConfig):
    """Node closure for an agent step. The pipeline captures the final
    response and writes the declared artifact itself; verdict steps parse the
    captured text (decoration-tolerant) and record pass/fail/None into
    step_verdicts. A finished agent with no final response, and a verdict the
    parser can't pin down, both fall into the attempt-bounded unclear retry."""
    verdicts_map = None
    if step.verdicts is not None:
        verdicts_map = {step.verdicts.pass_: "pass", step.verdicts.fail: "fail"}
        if step.verdicts.unverifiable is not None:
            verdicts_map[step.verdicts.unverifiable] = "unverifiable"
    captures = step.artifact is not None or bool(step.produces)

    def node(state: dict) -> dict:
        attempt = state["attempt"]
        subject = state["subject"]
        max_iterations = step.max_iterations or DEFAULT_MAX_ITERATIONS
        message = build_context(runtime, state["docs"], state["subject"],
                                state["notes"],
                                artifacts=state.get("artifacts", {}))
        print(f"[{subject}] Running {step.agent} agent...", flush=True)
        result = runtime.run_agent(step.agent, message,
                                    max_iterations=max_iterations, step=step)
        if not result.ok:
            print(f"[{subject}] {step.name} agent failed on attempt {attempt}.", flush=True)
            return {"failed": True, "failed_step": step.name}

        if result.capture_error is not None:
            print(f"[{subject}] {step.name} agent produced no final response on "
                  f"attempt {attempt}, retrying.", flush=True)
            merged = {**state["step_verdicts"], step.name: None}
            return {"step_verdicts": merged,
                    "notes": [_unclear_note(runtime, step, attempt, None,
                                            result.truncated, max_iterations)],
                    "retry_target": _unclear_target(runtime, step)}

        # 021: under `locked`, a declared missing dependency is a fail-fast -
        # one human lockfile edit, never a retry loop. The extension message
        # is written to last-error.md (the run-failure channel) and printed.
        if (runtime.cfg.deps_policy == "locked"
                and result.response is not None):
            pins = parse_missing_dependencies(result.response)
            if pins:
                msg = extension_message(runtime.cfg, pins)
                runtime.state_dir.mkdir(parents=True, exist_ok=True)
                (runtime.state_dir / LAST_ERROR).write_text(
                    f"# Missing dependencies\n\n```\n{msg}\n```\n",
                    encoding="utf-8")
                print(redact_values(msg, runtime.secrets.values()),
                      file=sys.stderr, flush=True)
                return {"failed": True, "failed_step": step.name}

        if verdicts_map is None:
            merged = {**state["step_verdicts"], step.name: "pass"} if captures else \
                state["step_verdicts"]
            update = {"step_verdicts": merged, "failed": False}
            if captures and result.response is not None:
                update["artifacts"] = {step.name: result.response}
            return update

        parsed = Verdict.from_text(result.response or "", verdicts_map)
        merged = {**state["step_verdicts"], step.name: None}
        if parsed.is_ok():
            verdict = parsed.unwrap().value
            merged[step.name] = verdict
            if verdict == "pass":
                print(f"[{subject}] {step.name} VERDICT: PASS on attempt {attempt}.",
                      flush=True)
                update = {"step_verdicts": merged, "failed": False}
                if captures and result.response is not None:
                    update["artifacts"] = {step.name: result.response}
                return update
            if verdict == "unverifiable":
                print(f"[{subject}] {step.name} verdict: UNVERIFIABLE "
                      f"(moves on).", flush=True)
                update = {"step_verdicts": merged, "failed": False}
                if captures and result.response is not None:
                    update["artifacts"] = {step.name: result.response}
                return update
            print(f"[{subject}] {step.name} verdict: fail on attempt {attempt}.",
                  flush=True)
            update = {"step_verdicts": merged,
                      "notes": [f"## {step.name} output (attempt {attempt})\n\n"
                                f"{result.response}"]}
            if captures and result.response is not None:
                update["artifacts"] = {step.name: result.response}
            if isinstance(step.on_fail, Route) and step.on_fail.retry:
                update["retry_target"] = step.on_fail.goto
            return update

        print(f"[{subject}] {step.name} verdict unclear on attempt {attempt}, retrying.",
              flush=True)
        return {"step_verdicts": merged,
                "notes": [_unclear_note(runtime, step, attempt, result.response,
                                        result.truncated, max_iterations,
                                        parsed.error.value)],
                "retry_target": _unclear_target(runtime, step)}

    return node


def make_brief_node(runtime: Runtime, step: StepConfig):
    """Node closure for a ``brief_inputs:`` agent step (033).

    The brief is an ordinary capture-applying agent step with one extra rule:
    it runs once per input set. The resolved inputs are hashed when the brief
    is written; on re-entry with an unchanged hash and an existing digest the
    LLM call is skipped entirely. A changed include file or env fact re-runs
    the step and refreshes the hash. Prior verdicts and feedback never enter
    the brief message - they already reach dev/review through the notes
    channel."""
    captures = step.artifact is not None or bool(step.produces)

    def node(state: dict) -> dict:
        attempt = state["attempt"]
        subject = state["subject"]
        max_iterations = step.max_iterations or DEFAULT_MAX_ITERATIONS

        try:
            current_hash = brief_inputs_hash(runtime, step, state["docs"],
                                             state.get("doc_sources"))
            if (state.get("artifacts", {}).get(step.name) is not None
                    and state.get("brief_hashes", {}).get(step.name) == current_hash):
                print(f"[{subject}] {step.name} brief inputs unchanged - reusing.",
                      flush=True)
                merged = {**state["step_verdicts"], step.name: "pass"}
                return {"step_verdicts": merged, "failed": False}
            message = build_brief_context(runtime, step, state["docs"],
                                          state.get("doc_sources"))
        except Exception:
            err = redact_values(traceback.format_exc(), runtime.secrets.values())
            print(err[-4000:], file=sys.stderr, flush=True)
            runtime.state_dir.mkdir(parents=True, exist_ok=True)
            (runtime.state_dir / LAST_ERROR).write_text(
                f"# Brief step failed\n\n```\n{err[-4000:]}\n```\n",
                encoding="utf-8")
            return {"failed": True, "failed_step": step.name}

        print(f"[{subject}] Running {step.agent} agent...", flush=True)
        result = runtime.run_agent(step.agent, message,
                                    max_iterations=max_iterations, step=step)
        if not result.ok:
            print(f"[{subject}] {step.name} agent failed on attempt {attempt}.",
                  flush=True)
            return {"failed": True, "failed_step": step.name}

        if result.capture_error is not None:
            print(f"[{subject}] {step.name} agent produced no final response on "
                  f"attempt {attempt}, retrying.", flush=True)
            merged = {**state["step_verdicts"], step.name: None}
            return {"step_verdicts": merged,
                    "notes": [_unclear_note(runtime, step, attempt, None,
                                            result.truncated, max_iterations)],
                    "retry_target": _unclear_target(runtime, step)}

        merged = {**state["step_verdicts"], step.name: "pass"}
        update = {"step_verdicts": merged, "failed": False}
        if captures and result.response is not None:
            update["artifacts"] = {step.name: result.response}
            update["brief_hashes"] = {step.name: current_hash}
        return update

    return node


def make_command_node(runtime: Runtime, step: StepConfig):
    """Node closure for a mechanical command step. The exit code is the sole
    verdict: 0 ⇒ pass, anything else (nonzero, signal-killed, not-found, or
    killed for exceeding timeout) ⇒ fail. Output is printed and its tail rides
    into the retry note - mirroring the agent FAIL branch, so a retried dev
    agent sees exactly what the gate emitted."""
    timeout = step.timeout if step.timeout is not None else DEFAULT_COMMAND_TIMEOUT
    sandbox = runtime.sandbox
    cwd, mount_set, egress_policy = _step_sandbox(runtime, step)
    allowlist = (tuple(runtime.engine_allowlist)
                 if runtime.engine_allowlist is not None else None)
    if sandbox is not None:
        env = resolve_sandbox_env(
            step.env, secrets=step_secret_env(runtime.cfg, step, runtime.secrets))
    else:
        env = child_env(runtime.cfg.env_allowlist, runtime.cfg.llm.api_key_env)

    def node(state: dict) -> dict:
        attempt = state["attempt"]
        subject = state["subject"]
        print(f"[{subject}] Running command `{step.command}`...", flush=True)
        try:
            exit_code, output, timed_out = _run_command(
                step.command, runtime.workspace, timeout, env,
                sandbox=sandbox, pipeline=runtime.cfg.name, step=step.name,
                network=step.network, mount_set=mount_set,
                egress_policy=egress_policy, cwd=cwd, allowlist=allowlist)
        except SandboxError:
            err = redact_values(traceback.format_exc(),
                                runtime.secrets.values())[-4000:]
            print(err, file=sys.stderr, flush=True)
            runtime.state_dir.mkdir(parents=True, exist_ok=True)
            (runtime.state_dir / LAST_ERROR).write_text(
                f"# Command step failed\n\n```\n{err}\n```\n",
                encoding="utf-8")
            return {"failed": True, "failed_step": step.name}
        except Exception:
            print(redact_values(traceback.format_exc(),
                                runtime.secrets.values())[-4000:],
                  file=sys.stderr, flush=True)
            return {"failed": True, "failed_step": step.name}
        if output:
            output = redact_values(output, runtime.secrets.values())
            print(output, end="" if output.endswith("\n") else "\n", flush=True)
        merged = {**state["step_verdicts"],
                  step.name: "pass" if exit_code == 0 else "fail"}
        if exit_code == 0:
            print(f"[{subject}] {step.name} command passed on attempt {attempt}.",
                  flush=True)
            return {"step_verdicts": merged, "failed": False}
        print(f"[{subject}] {step.name} command failed on attempt {attempt}.",
              flush=True)
        label = f"attempt {attempt} - timed out" if timed_out else f"attempt {attempt}"
        update = {"step_verdicts": merged,
                  "notes": [f"## {step.name} output ({label})\n\n{output[-4000:]}"]}
        if isinstance(step.on_fail, Route) and step.on_fail.retry:
            update["retry_target"] = step.on_fail.goto
        return update

    return node


def _render_action_script(script: str, params: dict, subject: str) -> str:
    """Render a sandbox-action script with the step's ``params:`` plus the
    run ``{subject}``. A placeholder missing from both fails loudly (no
    silent ``{key}`` passthrough)."""
    values = dict(params)
    values["subject"] = subject
    return script.format_map(values)


def make_action_node(runtime: Runtime, step: StepConfig):
    """Node closure for an action step.

    Host-side built-ins (``commit``) invoke their trusted ``fn`` in the host
    process; every other action is a sandbox-executed, command-shaped script
    (020) run through the same sandbox path as command steps - exit code is
    the verdict, and consumer action code never runs in the host. An action
    exception fails the run (same as an agent's)."""
    if step.action in runtime.registry.actions:
        fn = runtime.registry.actions[step.action]

        def node(state: dict) -> dict:
            try:
                return fn(state, step.params) or {}
            except Exception:
                print(redact_values(traceback.format_exc(),
                                    runtime.secrets.values())[-4000:],
                      file=sys.stderr, flush=True)
                return {"failed": True, "failed_step": step.name}

        return node

    script = runtime.registry.sandbox_actions[step.action]
    if runtime.sandbox is None:
        raise ValueError(
            f"sandbox action {step.action!r} requires a sandbox - consumer "
            "actions must execute inside the sandbox, never the host")
    timeout = DEFAULT_COMMAND_TIMEOUT
    env = resolve_sandbox_env(step.env)
    cwd, mount_set, egress_policy = _step_sandbox(runtime, step)
    allowlist = (tuple(runtime.engine_allowlist)
                 if runtime.engine_allowlist is not None else None)

    def node(state: dict) -> dict:
        attempt = state["attempt"]
        subject = state["subject"]
        try:
            command = _render_action_script(script, step.params, subject)
        except KeyError as e:
            print(redact_values(traceback.format_exc(),
                                runtime.secrets.values())[-4000:],
                  file=sys.stderr, flush=True)
            return {"failed": True, "failed_step": step.name}
        print(f"[{subject}] Running action `{step.action}`...", flush=True)
        try:
            exit_code, output, timed_out = _run_command(
                command, runtime.workspace, timeout, env, sandbox=runtime.sandbox,
                pipeline=runtime.cfg.name, step=step.name, network=step.network,
                mount_set=mount_set, egress_policy=egress_policy, cwd=cwd,
                allowlist=allowlist)
        except SandboxError:
            err = redact_values(traceback.format_exc(),
                                runtime.secrets.values())[-4000:]
            print(err, file=sys.stderr, flush=True)
            runtime.state_dir.mkdir(parents=True, exist_ok=True)
            (runtime.state_dir / LAST_ERROR).write_text(
                f"# Action step failed\n\n```\n{err}\n```\n",
                encoding="utf-8")
            return {"failed": True, "failed_step": step.name}
        except Exception:
            print(redact_values(traceback.format_exc(),
                                runtime.secrets.values())[-4000:],
                  file=sys.stderr, flush=True)
            return {"failed": True, "failed_step": step.name}
        if output:
            output = redact_values(output, runtime.secrets.values())
            print(output, end="" if output.endswith("\n") else "\n", flush=True)
        merged = {**state["step_verdicts"],
                  step.name: "pass" if exit_code == 0 else "fail"}
        if exit_code == 0:
            print(f"[{subject}] {step.name} action passed on attempt {attempt}.",
                  flush=True)
            return {"step_verdicts": merged, "failed": False}
        print(f"[{subject}] {step.name} action failed on attempt {attempt}.",
              flush=True)
        label = f"attempt {attempt} - timed out" if timed_out else f"attempt {attempt}"
        update = {"step_verdicts": merged,
                  "notes": [f"## {step.name} output ({label})\n\n{output[-4000:]}"]}
        if isinstance(step.on_fail, Route) and step.on_fail.retry:
            update["retry_target"] = step.on_fail.goto
        return update

    return node


def make_router(runtime: Runtime, step: StepConfig):
    """Router closure - a pure function of state, so control flow is
    deterministic by construction. Return values are node names (resolved
    dynamically by LangGraph), "bump", "success", or END (failure).

    Sandbox-executed actions (020) are verdict-routed like command steps
    (exit code ⇒ pass/fail); host-side built-in actions keep the plain
    fire-and-continue routing."""
    order = [s.name for s in runtime.cfg.steps]
    is_sandbox_action = (step.action is not None
                         and step.action in runtime.registry.sandbox_actions)

    def resolve(route) -> str:
        target = route_target(route)
        if target is None:  # omitted on_pass: next step, or success at the end
            idx = order.index(step.name)
            return order[idx + 1] if idx + 1 < len(order) else "success"
        if target == "failure":
            return END
        return target  # a step name, or the success node

    def router(state: dict) -> str:
        if state["failed"]:
            return END
        if step.verdicts is None and step.command is None and not is_sandbox_action:
            # Capture-applying steps without verdicts (produces-only) retry on
            # empty responses: the node signals it by recording None plus a
            # retry_target. Steps that don't capture never retry here.
            if ((step.artifact is not None or step.produces)
                    and state["step_verdicts"].get(step.name) is None
                    and state.get("retry_target")):
                return "bump" if state["attempt"] < state["max_attempts"] else END
            return resolve(step.on_pass)
        verdict = state["step_verdicts"].get(step.name)
        if verdict == "pass":
            return resolve(step.on_pass)
        if verdict == "unverifiable":
            if step.on_unverifiable is not None:
                return resolve(step.on_unverifiable)
            return resolve(step.on_pass)
        if verdict == "fail":
            if step.on_fail is None:
                return END
            if isinstance(step.on_fail, Route) and step.on_fail.retry:
                return "bump" if state["attempt"] < state["max_attempts"] else END
            return resolve(step.on_fail)
        # Unclear - implicit retry, attempt-bounded (fixed policy).
        return "bump" if state["attempt"] < state["max_attempts"] else END

    return router


def make_bump_node(runtime: Runtime):
    """Close the retry cycle: consume one attempt, clear per-attempt
    artifacts, and reset verdicts/failed for the next attempt. The retried
    agent's message is rebuilt per run by make_agent_node from the
    accumulated notes and artifacts (027)."""

    def node(state: dict) -> dict:
        clear_attempt_artifacts(runtime)
        update = {"attempt": state["attempt"] + 1,
                  "step_verdicts": {},
                  "failed": False}
        # 033 regeneration gate: if a brief step's resolved inputs changed
        # since it was written (a mid-run edit to AGENTS.md or the unit plan),
        # re-enter that brief step so it regenerates once and refreshes its
        # hash. Unchanged inputs keep the failing step's retry_target.
        for step in runtime.cfg.steps:
            if step.brief_inputs is None:
                continue
            try:
                current = brief_inputs_hash(runtime, step,
                                            state.get("docs", {}),
                                            state.get("doc_sources"))
            except Exception:
                update["retry_target"] = step.name
                break
            if state.get("brief_hashes", {}).get(step.name) != current:
                update["retry_target"] = step.name
                break
        return update

    return node


def success_node(_state: dict) -> dict:
    """The success terminal as a real node - any step may route here."""
    return {"outcome": "success"}
