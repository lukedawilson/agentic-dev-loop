"""SandboxToolBridge - the single execution choke point (unit 10).

Every tool call a subprocess harness makes lands here, and every terminal
request goes through ``Sandbox.run`` with the same execution policy the
OpenHands path uses: forbidden-command matching, per-step secret env, the
step's terminal timeout, network lane, mount set and egress policy. File I/O
is host-side but confined to the step workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..forbidden import match_forbidden_command
from ..isolation import SandboxError, SandboxExecutionRequest, command_argv
from .protocol import RuntimeContext


@dataclass(frozen=True)
class TerminalOutcome:
    """One normalized terminal result, rendered per harness by the driver."""

    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    infra_error: SandboxError | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def render(self) -> str:
        if self.infra_error is not None:
            return f"[infra] {self.infra_error}"
        return self.stdout + self.stderr


class WorkspacePathError(ValueError):
    """A file-lane path escaped the step workspace."""


class SandboxToolBridge:
    """Owns terminal execution, the host-side file lane and the task store."""

    def __init__(self, ctx: RuntimeContext) -> None:
        self._ctx = ctx
        self._tasks: list[dict] = []
        self._closed = False

    # -- terminal ---------------------------------------------------------
    def run_terminal(self, command: str,
                     timeout: float | None = None) -> TerminalOutcome:
        argv = command_argv(command)
        forbidden = match_forbidden_command(argv, self._ctx.forbidden_commands)
        if forbidden is not None:
            return TerminalOutcome(
                command=command, exit_code=None,
                stdout=f"[forbidden: {forbidden}]", stderr="")

        if self._ctx.sandbox is None:
            raise RuntimeError(
                "the sandbox tool bridge requires a Sandbox; none was resolved "
                "for this run")

        effective_timeout = timeout if timeout is not None else self._ctx.terminal_timeout
        env = dict(self._ctx.step_env or {})
        if self._ctx.secret_env:
            env.update(self._ctx.secret_env)
        request = SandboxExecutionRequest(
            argv=argv,
            cwd=str(self._ctx.terminal_cwd),
            env=env,
            timeout=effective_timeout,
            pipeline=self._ctx.pipeline_name,
            step=self._ctx.step_name,
            network=self._ctx.network,
            mount_set=self._ctx.mount_set,
            egress_policy=self._ctx.egress_policy,
            allowlist=self._ctx.allowlist,
        )
        try:
            result = self._ctx.sandbox.run(request)
        except SandboxError as exc:
            if exc is self._ctx.sandbox.infra_error:
                raise
            return TerminalOutcome(
                command=command, exit_code=None, stdout="", stderr=str(exc))
        outcome = TerminalOutcome(
            command=command, exit_code=result.exit_code,
            stdout=result.stdout, stderr=result.stderr,
            timed_out=result.timed_out)
        if self._ctx.viz_bus is not None:
            self._ctx.viz_bus.publish({
                "type": "terminal",
                "pipeline": self._ctx.pipeline_name,
                "step": self._ctx.step_name,
                "command": command,
                "exit_code": result.exit_code,
                "wrote_workspace": getattr(result, "wrote_workspace", False),
            })
        return outcome

    def is_sticky_error(self, exc: BaseException) -> bool:
        """True when ``exc`` is the run's sticky infra failure (030).

        A dead sandbox cannot recover by asking the harness to try again, so
        the MCP layer re-raises it instead of rendering a retryable tool
        error (BR-10.8).
        """
        sandbox = self._ctx.sandbox
        return sandbox is not None and exc is sandbox.infra_error

    # -- file lane --------------------------------------------------------
    def view_file(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"file not found: {path}")
        return target.read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def edit_file(self, path: str, old: str, new: str) -> None:
        target = self._resolve(path)
        text = target.read_text(encoding="utf-8")
        if old not in text:
            raise ValueError(f"old_str not found in {path}")
        target.write_text(text.replace(old, new, 1), encoding="utf-8")

    def insert_file(self, path: str, line: int, content: str) -> None:
        target = self._resolve(path)
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        index = max(0, min(line, len(lines)))
        lines.insert(index, content)
        target.write_text("".join(lines), encoding="utf-8")

    def _resolve(self, path: str) -> Path:
        root = Path(self._ctx.workspace).resolve()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            raise WorkspacePathError(
                f"path {path!r} escapes the step workspace {root}")
        return resolved

    # -- task tracker -----------------------------------------------------
    def task_list(self) -> list[dict]:
        return list(self._tasks)

    def task_set(self, tasks: list[dict]) -> list[dict]:
        self._tasks = list(tasks)
        return self.task_list()

    def close(self) -> None:
        self._closed = True
