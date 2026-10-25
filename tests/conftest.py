"""Shared test doubles. FakeAgents replaces `stages.run_agent` - the SDK
network boundary (the ONLY permitted stub per repo mock policy); everything
else runs real: real files, real git, real checkpoint DB."""

import os
import sys
from pathlib import Path

# `viz_demo.py` lives at the repo root (a demo script, not part of the
# package), and plain `pytest` does not put the working directory on
# sys.path (unlike `python -m pytest`). Insert the repo root - idempotently -
# so the suite passes under any invocation, including CI.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# This repo dogfoods its own published wheel in .venv. The suite must test
# the WORKING TREE, not the installed wheel - insert src/ ahead of anything
# else (idempotently) so `import agentic_dev_loop` always resolves to the
# source being developed. Pinned by tests/test_source_resolution.py.
_SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import pytest

from agentic_dev_loop.engine.isolation import (Sandbox, SandboxExecutionRequest,
                                        SandboxExecutionResult, SandboxError,
                                        is_git_mutation)
from agentic_dev_loop.engine.proc import kill_process_tree
from agentic_dev_loop.engine.stages import AgentResult


class FakeAgents:
    """Scripted stand-in for stages.run_agent. Behaviors are per-agent lists
    consumed in call order (the last one repeats):

      ("ok",)                                - run finishes, no final response
      ("write", path, content)               - writes an artifact, finishes
                                               (final response = content)
      ("truncated",)                         - cut short, no final response
      ("write_truncated", path, content)     - writes, then is cut short
      ("raise",)                             - run_agent internally fails
                                               (its contract: never raise,
                                               return ok=False)
      ("crash",)                             - KeyboardInterrupt escapes the
                                               graph (process-death resume)

    Capture mirroring: for steps that capture (produces/artifact), ("ok",)
    and ("truncated",) - which produce no final response - return
    capture_error="empty_response", exactly as the real run_agent does.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch):
        self.calls: list[dict] = []
        self.script: dict[str, list[tuple]] = {}

        def _fake(runtime, agent, message, max_iterations=500, step=None):
            return self._invoke(agent, message, runtime, max_iterations, step)

        monkeypatch.setattr(
            "agentic_dev_loop.engine.stages.Runtime.run_agent", _fake)

    def set(self, agent: str, behaviors: list[tuple]) -> None:
        self.script[agent] = list(behaviors)

    def agents_called(self) -> list[str]:
        return [c["agent"] for c in self.calls]

    def messages_for(self, agent: str) -> list[str]:
        return [c["message"] for c in self.calls if c["agent"] == agent]

    def _invoke(self, agent, message, runtime, max_iterations, step):
        self.calls.append({"agent": agent, "message": message,
                           "max_iterations": max_iterations})
        n = sum(1 for c in self.calls if c["agent"] == agent)
        behaviors = self.script.get(agent) or [("ok",)]
        b = behaviors[min(n - 1, len(behaviors) - 1)]
        kind = b[0]
        if kind == "raise":
            return AgentResult(ok=False)
        if kind == "crash":
            raise KeyboardInterrupt()
        captures = step is not None and (step.artifact is not None or step.produces)
        response = None
        capture_error = None
        if kind in ("write", "write_truncated"):
            (runtime.state_dir / b[1]).write_text(b[2])
            response = b[2]
        elif kind == "respond":
            response = b[1]
        elif captures:
            capture_error = "empty_response"
        return AgentResult(ok=True, truncated=kind in ("truncated", "write_truncated"),
                           response=response, capture_error=capture_error)


@pytest.fixture
def fake_agents(monkeypatch):
    return FakeAgents(monkeypatch)


class FakeSandbox(Sandbox):
    """Interface-level fake (FD-5): the only permitted Sandbox stub. Executes
    commands host-side in-process (so command-step tests run real commands),
    records every request, and enforces the git-mutation guard exactly like the
    real DockerBackend. A per-test ``handler`` may override execution to script
    specific commands."""

    def __init__(self, *, git_mutations_blocked: bool = True):
        self.requests: list[SandboxExecutionRequest] = []
        self.closed = False
        self.git_mutations_blocked = git_mutations_blocked
        self.handler = None

    def script(self, handler) -> None:
        self.handler = handler

    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        self.requests.append(request)
        if self.git_mutations_blocked and is_git_mutation(request.argv):
            raise SandboxError(
                f"AE-SBX-008: git mutation {' '.join(request.argv[:2])!r} blocked")
        if self.handler is not None:
            code, output, timed_out = self.handler(request)
        else:
            code, output, timed_out = _exec_host(request)
        return SandboxExecutionResult(
            exit_code=code, stdout=output, stderr="",
            duration_seconds=0.0, backend="fake", timed_out=timed_out)

    def close(self) -> None:
        self.closed = True


def _exec_host(request: SandboxExecutionRequest) -> tuple[int | None, str, bool]:
    import os
    import subprocess

    # Host-side approximation of the container: keep the sandbox PATH first
    # but append the host PATH so host binaries (python) resolve - the real
    # sandbox image ships python on its own PATH. Env scrubbing is separately
    # covered by the resolve_sandbox_env tests.
    env = dict(request.env)
    env["PATH"] = (request.env.get("PATH", "") + os.pathsep
                   + os.environ.get("PATH", "")).rstrip(os.pathsep)
    # The container resolves '/bin/sh' inside the image; the FAKE executes
    # host-side, where Windows has no /bin/sh - map to the host's sh (git-bash).
    argv = list(request.argv)
    if os.name == "nt" and argv and argv[0] == "/bin/sh":
        import shutil
        host_sh = shutil.which("sh")
        argv[0] = host_sh if host_sh else "sh"
    try:
        proc = subprocess.Popen(
            argv, cwd=request.cwd, env=env, start_new_session=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = proc.communicate(timeout=request.timeout)
        return proc.returncode, out.decode("utf-8", "replace"), False
    except subprocess.TimeoutExpired:
        kill_process_tree(proc)
        out, _ = proc.communicate()
        return None, out.decode("utf-8", "replace"), True
    except FileNotFoundError:
        return 127, f"{request.argv[0]}: not found", False


@pytest.fixture
def fake_sandbox(monkeypatch):
    """Inject a FakeSandbox for every pipeline run: monkeypatches the eager
    startup seam so ``run_pipeline``/``main`` get a scriptable fake instead of
    a real Docker backend (FD-5)."""
    sandbox = FakeSandbox()
    monkeypatch.setattr("agentic_dev_loop.engine.graph.startup_sandbox",
                        lambda *a, **k: sandbox)
    return sandbox


@pytest.fixture(scope="session", autouse=True)
def _writable_home(tmp_path_factory):
    """Point HOME at a writable temp dir for the whole session.

    The commit action and the default state dir write under
    ``~/.agentic-dev-loop``, and the OpenHands SDK writes ``~/.openhands``.
    Some containers run the suite as a non-root uid with ``HOME=/root``
    (mode 700, unwritable), so those real paths fail with PermissionError.
    A writable HOME is what CI already provides; tests that pin a specific
    home pass an explicit parent mapping or assert the raw string, so none
    depend on the process's real home.
    """
    if os.name == "posix":
        os.environ["HOME"] = str(tmp_path_factory.mktemp("home"))
    yield
