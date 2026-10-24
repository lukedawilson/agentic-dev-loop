"""Stage nodes, routers, context assembly, and the run_agent port.

The yosk hardcoded stages (dev/test/review/qa + bump) become per-step node
and router closures built from the pipeline config; the fixed policies
(hygiene, verdict-first truncation, unclear-verdict retry, notes) stay here."""

import dataclasses
import os
import sys
from types import SimpleNamespace

import pytest
from langgraph.graph import END
from openhands.sdk import LLM
from openhands.sdk.conversation.state import ConversationExecutionStatus

from agentic_dev_loop.core.config import PipelineConfig
from agentic_dev_loop.engine.registry import Registry, default_registry
from agentic_dev_loop.engine.stages import (
    Runtime, _run_command, build_context, make_action_node, make_agent_node,
    make_bump_node, make_command_node, make_router, run_agent,
)


def make_cfg(steps, **over):
    base = {"name": "t", "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "agents", "lockfiles": ["uv.lock"], "steps": steps}
    base.update(over)
    return PipelineConfig.model_validate(base)


STEPS = [
    {"name": "dev", "agent": "dev", "produces": ["implementation-summary.md"],
     "on_pass": "check"},
    {"name": "check", "agent": "check", "artifact": "ci-fix.md",
     "verdicts": {"pass": "PASS", "fail": "FAIL"},
     "on_pass": "review", "on_fail": {"goto": "dev", "retry": True}},
    {"name": "review", "agent": "review", "artifact": "review-findings.md",
     "verdicts": {"pass": "APPROVED", "fail": "NEEDS CHANGES"},
     "on_pass": "success"},  # no on_fail - fail is fatal, unclear retries entry
]


@pytest.fixture
def runtime(tmp_path, fake_sandbox):
    state_dir = tmp_path / ".pr"
    state_dir.mkdir()
    return Runtime(cfg=make_cfg(STEPS), registry=default_registry(),
                   llm=LLM(model="openai/test-model", api_key="sk-test"),
                   agents_dir=tmp_path / "agents", state_dir=state_dir,
                   workspace=tmp_path, sandbox=fake_sandbox)


def make_state(**over):
    state = {"subject": "plan plan.md", "docs": {"plan.md": "THE PLAN"},
             "context": "CTX", "attempt": 1, "max_attempts": 3, "notes": [],
             "failed": False, "outcome": None, "commit_allowed": False,
             "step_verdicts": {}, "retry_target": None, "thread_id": "t1"}
    state.update(over)
    return state


class TestBuildContext:
    def test_subject_docs_and_generated_output_paths(self, runtime):
        ctx = build_context(runtime, {"plan.md": "THE PLAN", "std.yaml": "STD"},
                            "plan plan.md", [])
        assert "You are working on plan plan.md." in ctx
        assert "## plan.md\n\nTHE PLAN" in ctx
        assert "## std.yaml\n\nSTD" in ctx
        assert "## Expected output paths" in ctx
        assert "dev agent writes: `.pr/implementation-summary.md`" in ctx
        assert "check agent writes: `.pr/ci-fix.md`" in ctx
        assert "review agent writes: `.pr/review-findings.md`" in ctx
        assert "Previous attempt feedback" not in ctx

    def test_notes_section_only_when_notes(self, runtime):
        ctx = build_context(runtime, {}, "s", ["note one", "note two"])
        assert "## Previous attempt feedback\n\nnote one\n\nnote two" in ctx

    def test_output_paths_workspace_relative_for_subdirectory_pipeline(self, tmp_path):
        """yosk layout: the pipeline YAML lives in a subdirectory while the
        workspace is the repo root - the prompt path must reach the real
        state_dir; state_dir.name alone sends agents to the wrong directory
        (U18 burned 3 attempts on an unfindable ci-fix.md)."""
        state_dir = tmp_path / "agentic-dev-loop" / ".pr"
        state_dir.mkdir(parents=True)
        rt = Runtime(cfg=make_cfg(STEPS), registry=default_registry(),
                     llm=LLM(model="openai/test-model", api_key="sk-test"),
                     agents_dir=tmp_path / "agents", state_dir=state_dir,
                     workspace=tmp_path)
        ctx = build_context(rt, {}, "s", [])
        assert "check agent writes: `agentic-dev-loop/.pr/ci-fix.md`" in ctx
        assert "\\" not in ctx  # prompt paths are container-addressed → POSIX

    def test_output_paths_absolute_when_state_dir_outside_workspace(self, tmp_path):
        state_dir = tmp_path / "state" / ".pr"
        state_dir.mkdir(parents=True)
        workspace = tmp_path / "ws"
        workspace.mkdir()
        rt = Runtime(cfg=make_cfg(STEPS), registry=default_registry(),
                     llm=LLM(model="openai/test-model", api_key="sk-test"),
                     agents_dir=tmp_path / "agents", state_dir=state_dir,
                     workspace=workspace)
        ctx = build_context(rt, {}, "s", [])
        assert f"check agent writes: `{state_dir.as_posix()}/ci-fix.md`" in ctx
        assert "\\" not in ctx  # prompt paths are container-addressed → POSIX

    def test_command_step_contributes_no_output_paths(self, runtime):
        cfg = make_cfg([{"name": "dev", "agent": "dev", "produces": ["summary.md"],
                         "on_pass": "test"},
                        {"name": "test", "command": "true", "on_pass": "success"}])
        rt = dataclasses.replace(runtime, cfg=cfg)
        ctx = build_context(rt, {}, "s", [])
        assert "dev agent writes:" in ctx
        assert "test agent writes:" not in ctx


class TestDepsPolicyContext:
    def test_locked_policy_injected(self, runtime):
        cfg = make_cfg(STEPS, deps_policy="locked", lockfiles=["uv.lock"])
        rt = dataclasses.replace(runtime, cfg=cfg)
        ctx = build_context(rt, {}, "s", [])
        assert "## Dependency policy" in ctx
        assert "`uv.lock`" in ctx
        assert "## Missing dependencies" in ctx
        assert "Do not add it to the manifest." in ctx

    def test_open_policy_injected(self, runtime):
        cfg = make_cfg(STEPS, deps_policy="open")
        rt = dataclasses.replace(runtime, cfg=cfg)
        ctx = build_context(rt, {}, "s", [])
        assert "## Dependency policy" in ctx
        assert "add the exact-pinned entry to the dependency manifest" in ctx
        assert "## Missing dependencies" not in ctx


class TestMissingDependencyFailFast:
    def _rt(self, runtime, **over):
        cfg = make_cfg(STEPS, **over)
        return dataclasses.replace(runtime, cfg=cfg)

    def test_locked_dev_declaring_missing_dep_fails_fast(self, runtime,
                                                         fake_agents):
        rt = self._rt(runtime, deps_policy="locked", lockfiles=["uv.lock"])
        fake_agents.set("dev", [("write", "implementation-summary.md",
            "Built most of it.\n\n## Missing dependencies\n\n"
            "requests==2.32.0\n")])
        update = make_agent_node(rt, rt.cfg.steps[0])(make_state())
        assert update == {"failed": True, "failed_step": "dev"}
        err = (rt.state_dir / "last-error.md").read_text()
        assert "Extend `uv.lock`" in err
        assert "requests==2.32.0" in err

    def test_no_header_no_fail(self, runtime, fake_agents):
        rt = self._rt(runtime, deps_policy="locked", lockfiles=["uv.lock"])
        fake_agents.set("dev", [("write", "implementation-summary.md",
                                 "no new deps")])
        update = make_agent_node(rt, rt.cfg.steps[0])(make_state())
        assert update["failed"] is False

    def test_open_policy_does_not_fail_on_header(self, runtime, fake_agents):
        rt = self._rt(runtime, deps_policy="open")
        fake_agents.set("dev", [("write", "implementation-summary.md",
            "## Missing dependencies\n\nrequests==2.32.0\n")])
        update = make_agent_node(rt, rt.cfg.steps[0])(make_state())
        assert update["failed"] is False


class TestAgentNode:
    def test_pass_verdict(self, runtime, fake_agents):
        fake_agents.set("check", [("write", "ci-fix.md", "all good\nVERDICT: PASS\n")])
        update = make_agent_node(runtime, runtime.cfg.steps[1])(make_state())
        assert update["step_verdicts"] == {"check": "pass"}
        assert update["failed"] is False
        assert "notes" not in update

    def test_fail_verdict_surfaces_artifact_and_retry_target(self, runtime, fake_agents):
        fake_agents.set("check", [("write", "ci-fix.md", "broken build\nVERDICT: FAIL\n")])
        update = make_agent_node(runtime, runtime.cfg.steps[1])(make_state(attempt=2))
        assert update["step_verdicts"] == {"check": "fail"}
        assert update["retry_target"] == "dev"
        (note,) = update["notes"]
        assert "broken build" in note and "attempt 2" in note

    def test_verdicts_merge_with_prior_steps(self, runtime, fake_agents):
        fake_agents.set("check", [("write", "ci-fix.md", "VERDICT: PASS")])
        state = make_state(step_verdicts={"dev": "pass"})
        update = make_agent_node(runtime, runtime.cfg.steps[1])(state)
        assert update["step_verdicts"] == {"dev": "pass", "check": "pass"}

    def test_empty_response_is_unclear(self, runtime, fake_agents):
        update = make_agent_node(runtime, runtime.cfg.steps[1])(make_state())
        assert update["step_verdicts"] == {"check": None}
        (note,) = update["notes"]
        assert "check agent produced no final response on attempt 1" in note
        assert update["retry_target"] == "dev"  # on_fail's goto

    def test_empty_response_note_names_the_step(self, tmp_path, fake_agents):
        state_dir = tmp_path / "agentic-dev-loop" / ".pr"
        state_dir.mkdir(parents=True)
        rt = Runtime(cfg=make_cfg(STEPS), registry=default_registry(),
                     llm=LLM(model="openai/test-model", api_key="sk-test"),
                     agents_dir=tmp_path / "agents", state_dir=state_dir,
                     workspace=tmp_path)
        update = make_agent_node(rt, rt.cfg.steps[1])(make_state())
        (note,) = update["notes"]
        assert "check agent produced no final response on attempt 1" in note

    def test_no_verdict_surfaces_content(self, runtime, fake_agents):
        fake_agents.set("check", [("write", "ci-fix.md", "I am undecided")])
        update = make_agent_node(runtime, runtime.cfg.steps[1])(make_state())
        (note,) = update["notes"]
        assert "contains no verdict" in note
        assert "VERDICT: PASS" in note and "VERDICT: FAIL" in note
        assert "I am undecided" in note

    def test_unclear_retries_entry_step_when_no_fail_route(self, runtime, fake_agents):
        # review has no on_fail - the unclear-retry policy targets the entry step
        update = make_agent_node(runtime, runtime.cfg.steps[2])(make_state())
        assert update["retry_target"] == "dev"

    def test_verdict_first_truncation_passes(self, runtime, fake_agents):
        fake_agents.set("check", [("write_truncated", "ci-fix.md", "VERDICT: PASS")])
        update = make_agent_node(runtime, runtime.cfg.steps[1])(make_state())
        assert update["step_verdicts"] == {"check": "pass"}
        assert "notes" not in update

    def test_truncated_without_verdict_notes_cut_short(self, runtime, fake_agents):
        fake_agents.set("check", [("write_truncated", "ci-fix.md", "partial findings")])
        update = make_agent_node(runtime, runtime.cfg.steps[1])(make_state())
        assert update["step_verdicts"] == {"check": None}
        (note,) = update["notes"]
        assert "cut short" in note and "partial findings" in note

    def test_agent_failure_sets_failed(self, runtime, fake_agents):
        fake_agents.set("check", [("raise",)])
        update = make_agent_node(runtime, runtime.cfg.steps[1])(make_state())
        assert update == {"failed": True, "failed_step": "check"}

    def test_plain_agent_step_runs_and_continues(self, runtime, fake_agents):
        fake_agents.set("dev", [("write", "implementation-summary.md", "did the work")])
        update = make_agent_node(runtime, runtime.cfg.steps[0])(make_state())
        assert update["failed"] is False
        assert update["step_verdicts"] == {"dev": "pass"}
        assert fake_agents.calls[0]["max_iterations"] == 500  # default budget

    def test_max_iterations_from_step(self, runtime, fake_agents):
        step = make_cfg([{"name": "qa", "agent": "qa", "artifact": "qa.md",
                          "verdicts": {"pass": "PASS", "fail": "FAIL"},
                          "max_iterations": 200, "on_pass": "success"}]).steps[0]
        make_agent_node(runtime, step)(make_state())
        assert fake_agents.calls[0]["max_iterations"] == 200

    def test_unverifiable_verdict_records_and_moves_on(self, runtime,
                                                       fake_agents, capsys):
        step = make_cfg([
            {"name": "dev", "agent": "dev", "on_pass": "qa"},
            {"name": "qa", "agent": "qa", "artifact": "qa-report.md",
             "verdicts": {"pass": "PASS", "fail": "FAIL",
                          "unverifiable": "UNVERIFIABLE"},
             "on_pass": "commit", "on_fail": {"goto": "dev", "retry": True},
             "on_unverifiable": "commit"},
            {"name": "commit", "action": "commit", "on_pass": "success"}]).steps[1]
        fake_agents.set("qa", [("write", "qa-report.md", "VERDICT: UNVERIFIABLE")])
        update = make_agent_node(runtime, step)(make_state())
        assert update["step_verdicts"] == {"qa": "unverifiable"}
        assert update["failed"] is False
        assert "notes" not in update
        assert "UNVERIFIABLE (moves on)" in capsys.readouterr().out

    def test_unclear_note_mentions_third_keyword(self, runtime, fake_agents):
        step = make_cfg([
            {"name": "qa", "agent": "qa", "artifact": "qa-report.md",
             "verdicts": {"pass": "PASS", "fail": "FAIL",
                          "unverifiable": "UNVERIFIABLE"},
             "on_pass": "success"}]).steps[0]
        fake_agents.set("qa", [("write", "qa-report.md", "undecided")])
        update = make_agent_node(runtime, step)(make_state())
        (note,) = update["notes"]
        assert "VERDICT: PASS" in note
        assert "VERDICT: FAIL" in note
        assert "VERDICT: UNVERIFIABLE" in note


class TestCommandNode:
    def _node(self, runtime, steps, idx=0):
        cfg = make_cfg(steps)
        rt = dataclasses.replace(runtime, cfg=cfg)
        return make_command_node(rt, cfg.steps[idx])

    def test_pass_verdict(self, runtime):
        node = self._node(runtime, [{"name": "test", "command": "true",
                                     "on_pass": "success"}])
        update = node(make_state())
        assert update == {"step_verdicts": {"test": "pass"}, "failed": False}

    def test_fail_verdict_surfaces_output_and_retry_target(self, runtime):
        node = self._node(runtime, [
            {"name": "dev", "agent": "dev", "on_pass": "test"},
            {"name": "test", "command": "printf 'FAILURE OUTPUT'; exit 1",
             "on_pass": "success", "on_fail": {"goto": "dev", "retry": True}}],
            idx=1)
        update = node(make_state())
        assert update["step_verdicts"] == {"test": "fail"}
        assert update["retry_target"] == "dev"
        (note,) = update["notes"]
        assert "FAILURE OUTPUT" in note and "attempt 1" in note

    def test_output_printed_on_pass_and_fail(self, runtime, capsys):
        node = self._node(runtime, [{"name": "test", "command": "echo PASS_OUTPUT",
                                     "on_pass": "success"}])
        node(make_state())
        assert "PASS_OUTPUT" in capsys.readouterr().out

        node = self._node(runtime, [
            {"name": "dev", "agent": "dev", "on_pass": "test"},
            {"name": "test", "command": "echo FAIL_OUTPUT; exit 1",
             "on_pass": "success", "on_fail": "dev"}], idx=1)
        node(make_state())
        assert "FAIL_OUTPUT" in capsys.readouterr().out

    def test_timeout_fails(self, runtime):
        node = self._node(runtime, [
            {"name": "dev", "agent": "dev", "on_pass": "test"},
            {"name": "test",
             "command": f'"{sys.executable}" -c "import time; time.sleep(5)"',
             "timeout": 0.2,
             "on_pass": "success", "on_fail": {"goto": "dev", "retry": True}}],
            idx=1)
        update = node(make_state())
        assert update["step_verdicts"] == {"test": "fail"}
        (note,) = update["notes"]
        assert "timed out" in note

    def test_timeout_reaps_the_process_tree(self, tmp_path):
        """The host-path timeout must cut the WHOLE tree short, not just the
        direct child - a shell wrapper's grandchild that survives keeps the
        pipes open and the follow-up communicate() blocks out the full sleep
        (CI-red 9.5s). kill_process_tree's per-OS branches are unit-pinned
        in tests/test_proc.py::TestKillProcessTree."""
        code, out, timed_out = _run_command(
            f'"{sys.executable}" -c "import time; time.sleep(30)"',
            tmp_path, 0.2)
        assert timed_out is True and code is None

    def test_workspace_is_cwd(self, runtime, capsys):
        node = self._node(runtime, [
            {"name": "test",
             "command": f'"{sys.executable}" -c "import os; print(os.getcwd())"',
             "on_pass": "success"}])
        node(make_state())
        assert os.path.realpath(runtime.workspace) in capsys.readouterr().out

    def test_run_command_exception_hard_fails(self, runtime, monkeypatch):
        node = self._node(runtime, [{"name": "test", "command": "true",
                                     "on_pass": "success"}])

        def boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr("agentic_dev_loop.engine.stages._run_command", boom)
        assert node(make_state()) == {"failed": True, "failed_step": "test"}

    def test_plain_fail_route_has_no_retry_target(self, runtime):
        node = self._node(runtime, [
            {"name": "dev", "agent": "dev", "on_pass": "test"},
            {"name": "test", "command": "false",
             "on_pass": "success", "on_fail": "dev"}], idx=1)
        update = node(make_state())
        assert update["step_verdicts"] == {"test": "fail"}
        assert "retry_target" not in update
        (note,) = update["notes"]
        assert "attempt 1" in note


class TestRunCommandHybrid:
    """Command steps classify their command string: no shell metacharacters
    → shlex-split argv (shell=False); metacharacters → shell=True (BR-4)."""

    def test_simple_command_runs_argv_style(self, runtime):
        exit_code, output, timed_out = _run_command(
            "printf '%s' 'hello world'", runtime.workspace, 10)
        assert exit_code == 0 and not timed_out
        assert output == "hello world"

    def test_shell_metachar_command_runs_via_shell(self, runtime):
        exit_code, output, timed_out = _run_command(
            "printf '%s\n' 'A B' | tr a-z A-Z", runtime.workspace, 10)
        assert exit_code == 0 and not timed_out
        assert "A B" in output

    def test_shell_variable_expansion(self, runtime):
        exit_code, output, timed_out = _run_command(
            "printf '%s' $STAGE_TEST_MARKER", runtime.workspace, 10,
            env={"PATH": os.environ.get("PATH", ""),
                 "STAGE_TEST_MARKER": "expanded"})
        assert exit_code == 0 and not timed_out
        assert output == "expanded"


class TestCommandRouter:
    def _router(self, runtime, steps, idx=0):
        cfg = make_cfg(steps)
        rt = dataclasses.replace(runtime, cfg=cfg)
        return make_router(rt, cfg.steps[idx])

    def test_pass_routes_on_pass(self, runtime):
        router = self._router(runtime, [
            {"name": "dev", "agent": "dev", "on_pass": "test"},
            {"name": "test", "command": "true", "on_pass": "success",
             "on_fail": {"goto": "dev", "retry": True}}], idx=1)
        assert router(make_state(step_verdicts={"test": "pass"})) == "success"

    def test_fail_retry_while_attempts_remain(self, runtime):
        router = self._router(runtime, [
            {"name": "dev", "agent": "dev", "on_pass": "test"},
            {"name": "test", "command": "true", "on_pass": "success",
             "on_fail": {"goto": "dev", "retry": True}}], idx=1)
        assert router(make_state(attempt=1, step_verdicts={"test": "fail"})) == "bump"

    def test_fail_retry_exhausted_ends(self, runtime):
        router = self._router(runtime, [
            {"name": "dev", "agent": "dev", "on_pass": "test"},
            {"name": "test", "command": "true", "on_pass": "success",
             "on_fail": {"goto": "dev", "retry": True}}], idx=1)
        assert router(make_state(attempt=3, step_verdicts={"test": "fail"})) == END

    def test_fail_plain_on_fail_routes_directly(self, runtime):
        router = self._router(runtime, [
            {"name": "dev", "agent": "dev", "on_pass": "test"},
            {"name": "test", "command": "true", "on_pass": "success",
             "on_fail": "dev"}], idx=1)
        assert router(make_state(step_verdicts={"test": "fail"})) == "dev"

    def test_fail_without_on_fail_ends(self, runtime):
        router = self._router(runtime, [{"name": "test", "command": "true",
                                         "on_pass": "success"}], idx=0)
        assert router(make_state(step_verdicts={"test": "fail"})) == END


class TestRouter:
    def test_failed_goes_to_end(self, runtime):
        router = make_router(runtime, runtime.cfg.steps[0])
        assert router(make_state(failed=True)) == END

    def test_plain_step_routes_on_pass(self, runtime):
        router = make_router(runtime, runtime.cfg.steps[0])
        assert router(make_state()) == "check"

    def test_pass_routes_on_pass(self, runtime):
        router = make_router(runtime, runtime.cfg.steps[1])
        assert router(make_state(step_verdicts={"check": "pass"})) == "review"

    def test_fail_retry_while_attempts_remain(self, runtime):
        router = make_router(runtime, runtime.cfg.steps[1])
        assert router(make_state(attempt=1, step_verdicts={"check": "fail"})) == "bump"

    def test_fail_retry_exhausted_ends(self, runtime):
        router = make_router(runtime, runtime.cfg.steps[1])
        assert router(make_state(attempt=3, step_verdicts={"check": "fail"})) == END

    def test_unclear_retries_then_exhausts(self, runtime):
        router = make_router(runtime, runtime.cfg.steps[1])
        assert router(make_state(attempt=1, step_verdicts={"check": None})) == "bump"
        assert router(make_state(attempt=3, step_verdicts={"check": None})) == END

    def test_fail_without_route_ends(self, runtime):
        router = make_router(runtime, runtime.cfg.steps[2])  # review: no on_fail
        assert router(make_state(step_verdicts={"review": "fail"})) == END

    def test_pass_to_success_terminal(self, runtime):
        router = make_router(runtime, runtime.cfg.steps[2])
        assert router(make_state(step_verdicts={"review": "pass"})) == "success"

    def test_default_on_pass_is_next_step(self, runtime):
        cfg = make_cfg([{"name": "a", "agent": "a"}, {"name": "b", "agent": "b"}])
        runtime = dataclasses.replace(runtime, cfg=cfg)
        router = make_router(runtime, cfg.steps[0])
        assert router(make_state()) == "b"

    def test_last_step_default_on_pass_is_success(self, runtime):
        cfg = make_cfg([{"name": "a", "agent": "a"}, {"name": "b", "agent": "b"}])
        runtime = dataclasses.replace(runtime, cfg=cfg)
        router = make_router(runtime, cfg.steps[1])
        assert router(make_state()) == "success"

    def test_plain_goto_on_fail_routes_directly(self, runtime):
        cfg = make_cfg([
            {"name": "a", "agent": "a"},
            {"name": "c", "agent": "c", "artifact": "c.md",
             "verdicts": {"pass": "PASS", "fail": "FAIL"}, "on_fail": "a"},
        ])
        runtime = dataclasses.replace(runtime, cfg=cfg)
        router = make_router(runtime, cfg.steps[1])
        assert router(make_state(step_verdicts={"c": "fail"})) == "a"

    def test_unverifiable_routes_on_unverifiable(self, runtime):
        cfg = make_cfg([
            {"name": "dev", "agent": "dev", "on_pass": "qa"},
            {"name": "qa", "agent": "qa", "artifact": "qa-report.md",
             "verdicts": {"pass": "PASS", "fail": "FAIL",
                          "unverifiable": "UNVERIFIABLE"},
             "on_pass": "commit", "on_fail": {"goto": "dev", "retry": True},
             "on_unverifiable": "commit"},
            {"name": "commit", "action": "commit", "on_pass": "success"},
        ])
        runtime = dataclasses.replace(runtime, cfg=cfg)
        router = make_router(runtime, cfg.steps[1])
        assert router(make_state(step_verdicts={"qa": "unverifiable"})) == "commit"

    def test_unverifiable_falls_back_to_on_pass_and_never_retries(self, runtime):
        cfg = make_cfg([
            {"name": "dev", "agent": "dev", "on_pass": "qa"},
            {"name": "qa", "agent": "qa", "artifact": "qa-report.md",
             "verdicts": {"pass": "PASS", "fail": "FAIL",
                          "unverifiable": "UNVERIFIABLE"},
             "on_pass": "commit", "on_fail": {"goto": "dev", "retry": True}},
            {"name": "commit", "action": "commit", "on_pass": "success"},
        ])
        runtime = dataclasses.replace(runtime, cfg=cfg)
        router = make_router(runtime, cfg.steps[1])
        # unset on_unverifiable -> on_pass; never on_fail's bump retry
        assert router(make_state(attempt=1,
                                 step_verdicts={"qa": "unverifiable"})) == "commit"


class TestBumpNode:
    def test_consumes_attempt_resets_verdicts_rebuilds_cleans(self, runtime):
        for stale in ("ci-fix.md", "implementation-summary.md", "last-error.md"):
            (runtime.state_dir / stale).write_text("stale")
        state = make_state(attempt=1, notes=["N1"],
                           step_verdicts={"check": "fail"})
        update = make_bump_node(runtime)(state)
        assert update["attempt"] == 2
        assert update["step_verdicts"] == {}
        assert update["failed"] is False
        assert "## Previous attempt feedback\n\nN1" in update["context"]
        assert "## plan.md\n\nTHE PLAN" in update["context"]
        for stale in ("ci-fix.md", "implementation-summary.md", "last-error.md"):
            assert not (runtime.state_dir / stale).exists()


class TestSandboxActionNode:
    """020: consumer actions are sandbox-executed command-shaped scripts -
    the exit code is the verdict, and no consumer code runs host-side."""

    def _rt(self, runtime, script, **extra):
        reg = Registry()
        reg.add_sandbox_action("consumer", script)
        cfg = make_cfg([{"name": "consumer", "action": "consumer",
                         "on_pass": "success", **extra}])
        return dataclasses.replace(runtime, registry=reg, cfg=cfg), cfg

    def test_runs_via_sandbox_and_records_pass(self, runtime, fake_sandbox):
        rt, cfg = self._rt(runtime, "echo sandboxed")
        update = make_action_node(rt, cfg.steps[0])(make_state())
        assert update["step_verdicts"] == {"consumer": "pass"}
        assert update["failed"] is False
        assert fake_sandbox.requests
        assert fake_sandbox.requests[0].argv == ["echo", "sandboxed"]

    def test_renders_subject_and_params(self, runtime, fake_sandbox):
        rt, cfg = self._rt(runtime, "echo {subject} {greeting}",
                           params={"greeting": "hi"})
        make_action_node(rt, cfg.steps[0])(make_state())
        assert fake_sandbox.requests[0].argv == ["echo", "plan", "plan.md", "hi"]

    def test_nonzero_exit_records_fail(self, runtime, fake_sandbox):
        rt, cfg = self._rt(runtime, 'python -c "import sys; sys.exit(1)"',
                           on_fail={"goto": "success", "retry": False})
        update = make_action_node(rt, cfg.steps[0])(make_state())
        assert update["step_verdicts"] == {"consumer": "fail"}
        assert "failed" not in update
        assert "notes" in update

    def test_missing_param_fails_loud_not_silent(self, runtime, fake_sandbox):
        rt, cfg = self._rt(runtime, "echo {missing}")
        update = make_action_node(rt, cfg.steps[0])(make_state())
        assert update == {"failed": True, "failed_step": "consumer"}
        assert fake_sandbox.requests == []

    def test_requires_a_sandbox(self, runtime):
        reg = Registry()
        reg.add_sandbox_action("consumer", "echo hi")
        cfg = make_cfg([{"name": "consumer", "action": "consumer",
                         "on_pass": "success"}])
        rt = dataclasses.replace(runtime, registry=reg, cfg=cfg, sandbox=None)
        with pytest.raises(ValueError, match="requires a sandbox"):
            make_action_node(rt, cfg.steps[0])


class FakeConversation:
    """Boundary stub for the SDK's network-driving Conversation."""

    def __init__(self, status, **kwargs):
        self.state = SimpleNamespace(execution_status=status)
        self.kwargs = kwargs
        self.messages = []
        self.closed = False

    def send_message(self, message):
        self.messages.append(message)

    def run(self):
        pass

    def close(self):
        self.closed = True


class TestRunAgent:
    def test_missing_agent_file_reports_last_error(self, runtime):
        result = run_agent("ghost", "msg", runtime)
        assert result.ok is False
        err = (runtime.state_dir / "last-error.md").read_text()
        assert "Agent run failed" in err and "ghost.agent.md" in err

    def _write_agent(self, runtime):
        runtime.agents_dir.mkdir()
        (runtime.agents_dir / "dev.agent.md").write_text(
            "---\nname: dev\ntools: [terminal]\n---\nDo the work.")

    def test_finished_run_is_not_truncated(self, runtime, monkeypatch):
        self._write_agent(runtime)
        convs = []
        monkeypatch.setattr(
            "agentic_dev_loop.engine.stages.Conversation",
            lambda **kw: convs.append(FakeConversation(ConversationExecutionStatus.FINISHED, **kw)) or convs[-1])
        result = run_agent("dev", "the task", runtime, max_iterations=200)
        assert result.ok and not result.truncated
        (conv,) = convs
        assert conv.messages == ["the task"]
        assert conv.kwargs["max_iteration_per_run"] == 200
        assert conv.kwargs["workspace"] == str(runtime.workspace)
        assert conv.closed

    def test_unfinished_run_is_truncated(self, runtime, monkeypatch):
        self._write_agent(runtime)
        monkeypatch.setattr(
            "agentic_dev_loop.engine.stages.Conversation",
            lambda **kw: FakeConversation(ConversationExecutionStatus.ERROR, **kw))
        result = run_agent("dev", "msg", runtime)
        assert result.ok and result.truncated


class TestSandboxedCommandNode:
    """Command steps route through the Sandbox (BR-1.1); git mutations are
    refused loudly (hard fail, retry loop never consulted); action steps never
    touch the sandbox (FR-2.6)."""

    def _node(self, runtime, command, **extra):
        cfg = make_cfg([{"name": "test", "command": command,
                         "on_pass": "success", **extra}])
        rt = dataclasses.replace(runtime, cfg=cfg)
        return make_command_node(rt, cfg.steps[0])

    def test_routes_through_sandbox(self, runtime, fake_sandbox):
        node = self._node(runtime, "echo routed")
        update = node(make_state())
        assert update == {"step_verdicts": {"test": "pass"}, "failed": False}
        assert fake_sandbox.requests
        assert fake_sandbox.requests[0].argv == ["echo", "routed"]

    def test_sandbox_env_is_resolved(self, runtime, fake_sandbox):
        cfg = make_cfg([{"name": "test", "command": "echo hi",
                         "env": {"MY_VAR": "declared"}, "on_pass": "success"}])
        rt = dataclasses.replace(runtime, cfg=cfg)
        node = make_command_node(rt, cfg.steps[0])
        node(make_state())
        assert fake_sandbox.requests[0].env["MY_VAR"] == "declared"
        assert fake_sandbox.requests[0].env["PATH"].startswith("/usr/local/bin")

    def test_network_defaults_off(self, runtime, fake_sandbox):
        node = self._node(runtime, "echo routed")
        node(make_state())
        assert fake_sandbox.requests[0].network is False

    def test_network_true_reaches_request(self, runtime, fake_sandbox):
        node = self._node(runtime, "curl https://example.com", network=True)
        node(make_state())
        assert fake_sandbox.requests[0].network is True

    def test_git_mutation_hard_fails_not_retried(self, runtime, fake_sandbox):
        cfg = make_cfg([
            {"name": "dev", "agent": "dev", "on_pass": "test"},
            {"name": "test", "command": "git commit -m x",
             "on_pass": "success", "on_fail": {"goto": "dev", "retry": True}},
        ])
        rt = dataclasses.replace(runtime, cfg=cfg)
        node = make_command_node(rt, cfg.steps[1])
        update = node(make_state())
        # blocked is blocked - a hard failure, never a retry cycle
        assert update == {"failed": True, "failed_step": "test"}
        assert "notes" not in update

    def test_commit_action_never_routed(self, runtime, fake_sandbox):
        cfg = make_cfg([{"name": "commit", "action": "commit",
                         "params": {"message": "m"}, "on_pass": "success"}])
        rt = dataclasses.replace(runtime, cfg=cfg)
        node = make_action_node(rt, cfg.steps[0])
        node(make_state(commit_allowed=False))
        assert fake_sandbox.requests == []
