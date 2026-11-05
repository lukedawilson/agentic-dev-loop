"""OpenHandsRuntime - the in-process OpenHands driver (unit 10).

A pure move of the step runner's OpenHands body: per-step LLM selection
(054), agent loading, per-run sandbox terminal registration (KD-4), the
Conversation run, the 035 finalize nudge loop, the 030 sticky-infra raise,
capture, and the truncated rule. No behavior is added here.
"""

from __future__ import annotations

from typing import Any

from openhands.sdk import Conversation

from ..agents import (load_agent, load_brief_agent, register_sandbox_terminal)
from ..llm import build_llm
from ..outputs import CapturedOutput
from ..secrets import step_secret_env
from .protocol import NudgePolicy, RuntimeContext, RuntimeResult, RuntimeStatus


class _TurnPauseController:
    """Pause the SDK run loop after every agent-sourced event so the caller
    can drive one turn at a time (035)."""

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
    """Finalize nudge (035): template text, never step-named."""

    def __init__(self, policy: int | NudgePolicy) -> None:
        super().__init__()
        self.policy = (policy if isinstance(policy, NudgePolicy)
                       else NudgePolicy.for_turns(policy))
        self.nudged = False

    @property
    def turns(self) -> int:
        return self.policy.turns_before_end

    def message(self) -> str:
        return self.policy.message


def _run_finalize_loop(conv, max_iterations: int,
                       finalize_controller: _FinalizeNudgeController) -> bool:
    """Drive one turn at a time so nudges and the budget are enforced."""
    from openhands.sdk.conversation.state import ConversationExecutionStatus

    turns = 0
    nudge_at = max_iterations - finalize_controller.turns
    while True:
        conv.run()
        status = conv.state.execution_status
        if status in (ConversationExecutionStatus.FINISHED,
                      ConversationExecutionStatus.STUCK,
                      ConversationExecutionStatus.ERROR):
            return False

        turns += 1
        if turns >= max_iterations:
            conv.state.execution_status = ConversationExecutionStatus.ERROR
            return True

        if turns == nudge_at and not finalize_controller.nudged:
            conv.send_message(finalize_controller.message())
            finalize_controller.nudged = True
            continue


class OpenHandsRuntime:
    """Drives one OpenHands SDK conversation through a Sandbox."""

    def __init__(self, ctx: RuntimeContext) -> None:
        self._ctx = ctx
        self._conv: Any | None = None

    def run(self, message: str, *,
            nudge_policy: NudgePolicy | None = None) -> RuntimeResult:
        from openhands.sdk.conversation.state import (
            ConversationExecutionStatus,
        )

        ctx = self._ctx
        step = ctx.step
        finalize_controller = (
            _FinalizeNudgeController(nudge_policy)
            if nudge_policy is not None else None)

        if ctx.sandbox is not None:
            register_sandbox_terminal(
                ctx.sandbox,
                env=step.env if step is not None else None,
                workspace=str(ctx.terminal_cwd),
                pipeline=ctx.cfg.name,
                step=ctx.step_name,
                network=step.network if step is not None else False,
                mount_set=ctx.mount_set,
                egress_policy=ctx.egress_policy,
                terminal_timeout=ctx.terminal_timeout,
                secrets=(step_secret_env(ctx.cfg, step, ctx.secrets)
                         if step is not None else None),
                forbidden_commands=ctx.forbidden_commands,
                allowlist=ctx.allowlist,
            )

        llm = self._select_llm()
        if step is not None and step.kind == "brief":
            agent = load_brief_agent(ctx.agent_name,
                                     step.brief_inputs.sections, llm)
        else:
            agent = load_agent(ctx.agents_dir, ctx.agent_name, llm)

        callbacks = []
        if finalize_controller is not None:
            callbacks.append(finalize_controller.on_event)
        conv = Conversation(agent=agent, workspace=str(ctx.workspace),
                            max_iteration_per_run=ctx.max_iterations,
                            callbacks=callbacks or None)
        self._conv = conv
        if finalize_controller is not None:
            finalize_controller.attach(conv)
        conv.send_message(message)
        budget_exhausted = False
        if finalize_controller is not None:
            budget_exhausted = self._run_finalize_loop(
                conv, ctx.max_iterations, finalize_controller)
        else:
            conv.run()

        # 030, D1/D2: a sticky infra failure must fail the run with no retry.
        if ctx.sandbox is not None and ctx.sandbox.infra_error is not None:
            raise ctx.sandbox.infra_error

        finished = (conv.state.execution_status
                    == ConversationExecutionStatus.FINISHED)
        status = self._map_status(conv, finished, budget_exhausted)
        final_text, capture_error = self._capture(conv)
        return RuntimeResult(status=status, final_text=final_text,
                             capture_error=capture_error)

    def close(self) -> None:
        conv, self._conv = self._conv, None
        if conv is not None:
            conv.close()

    # -- internals --------------------------------------------------------
    def _select_llm(self):
        ctx = self._ctx
        step = ctx.step
        llm = ctx.llm
        if step is None:
            return llm
        effective_cfg = (ctx.cfg.llms[step.llm]
                         if step.llm is not None else ctx.cfg.llm)
        if step.llm is not None:
            llm = ctx.llms.get(step.llm, ctx.llm)
        if step.reasoning_effort is not None:
            llm = build_llm(effective_cfg,
                            reasoning_effort=step.reasoning_effort)
        return llm

    def _run_finalize_loop(self, conv, max_iterations: int,
                           finalize_controller: _FinalizeNudgeController) -> bool:
        return _run_finalize_loop(conv, max_iterations, finalize_controller)

    @staticmethod
    def _map_status(conv, finished: bool, budget_exhausted: bool) -> RuntimeStatus:
        from openhands.sdk.conversation.state import (
            ConversationExecutionStatus,
        )

        if finished:
            return RuntimeStatus.FINISHED
        if budget_exhausted:
            return RuntimeStatus.TRUNCATED
        status = conv.state.execution_status
        if status == ConversationExecutionStatus.STUCK:
            return RuntimeStatus.STUCK
        if status == ConversationExecutionStatus.ERROR:
            return RuntimeStatus.ERROR
        return RuntimeStatus.TRUNCATED

    def _capture(self, conv) -> tuple[str | None, str | None]:
        ctx = self._ctx
        artifact = ctx.artifact
        if artifact is None:
            return None, None
        captured = CapturedOutput.from_agent_result(
            conv, artifact, ctx.state_dir, redactions=ctx.redactions)
        if captured.is_err():
            return None, captured.error.value
        write_result = captured.unwrap().write()
        if write_result.is_err():
            raise RuntimeError(
                f"captured output write failed: {write_result.error.value}")
        return captured.unwrap().raw_text, None
