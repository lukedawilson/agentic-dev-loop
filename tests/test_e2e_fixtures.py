"""Historical-fixture e2e: the failures that shaped the dev loop's policies,
replayed against the SHIPPED example pipeline (agentic-dev-loop/) -
a byte-identical copy per test, so state_dir, agents_dir and additional_files
resolve exactly as they would in a consumer's repo.

run_agent is stubbed (the SDK network boundary, via the fake_agents fixture);
files, git, checkpoints, verdict parsing and routing are all real. The
example no longer has a port_sweep step (the startup viz sweep is the
remaining kill path), so the `_listening_pids` → `[]` patch is
defense-in-depth - a contributor's real process may be listening on the
default viz port. Kill behavior itself is covered in test_actions.py.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from agentic_dev_loop.engine.agents import load_agent
from agentic_dev_loop.engine.cli import main
from agentic_dev_loop.core.config import load_config
from agentic_dev_loop.engine.llm import build_llm
from agentic_dev_loop.engine.registry import default_registry

EXAMPLE = Path(__file__).resolve().parent.parent / "agentic-dev-loop"


# Scripted stand-in for the shipped example's mechanical test command. The
# example runs `.venv-sandbox/bin/python -m pytest tests/ -q -m "not docker"`,
# which cannot run inside the consumer-repo copy (no .venv-sandbox, no tests/)
# - so this fixture scripts the seam locally. The real subprocess path stays
# covered by test_stages.py and test_graph.py. Behaviors: ("pass",) →
# (0, "", False); ("fail", output) →
# (1, output, False); ("timeout", output) → (None, output, True); the last
# behavior repeats; empty queue → default pass.
_COMMAND_QUEUE: list[tuple] = []


def _fake_sandbox_run(request):
    """Script the example's mechanical test command through the FakeSandbox
    (the sandbox boundary) - the consumer-repo copy has no `.venv-sandbox`.
    Only the pytest gate is scripted; the network-on `restore` step (added
    later, 016/018) always passes so it never consumes the scripted queue."""
    if "pytest" not in " ".join(request.argv):
        return 0, "", False
    if not _COMMAND_QUEUE:
        return 0, "", False
    behavior = _COMMAND_QUEUE[0]
    if len(_COMMAND_QUEUE) > 1:
        _COMMAND_QUEUE.pop(0)
    kind = behavior[0]
    if kind == "fail":
        return 1, behavior[1], False
    if kind == "timeout":
        return None, behavior[1], True
    return 0, "", False


def _script_test(*behaviors):
    _COMMAND_QUEUE[:] = list(behaviors)


def _passing_scripts(fake_agents, **overrides):
    scripts = {
        "brief": [("write", "attempt-brief.md", "Brief digest.\n")],
        "dev": [("write", "implementation-summary.md", "Implemented the fix.\n")],
        "review": [("write", "review-findings.md",
                    "Looks good.\n\nVERDICT: APPROVED\n")],
        "qa": [("write", "qa-report.md", "All good.\n\nVERDICT: PASS\n")],
    }
    scripts.update(overrides)
    for agent, behaviors in scripts.items():
        fake_agents.set(agent, behaviors)


@pytest.fixture
def consumer_repo(tmp_path, monkeypatch, fake_agents, fake_sandbox):
    """Byte-identical copy of the shipped example as a clean git repo,
    laid out like the real repo (README at the root, pipeline under
    agentic-dev-loop/) so additional_files (../README.md), state_dir and
    agents_dir all resolve exactly as they would in a consumer's repo."""
    repo = tmp_path / "repo"
    (repo / "agentic-dev-loop").mkdir(parents=True)
    # .pr is the pipeline's gitignored build-artifact dir. It must NOT ride
    # into the "shipped example" copy - a prior real run can leave stale
    # artifacts there, and committing them would flip commit_allowed to False
    # once clear_attempt_artifacts deletes the produced files.
    shutil.copytree(EXAMPLE, repo / "agentic-dev-loop", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".pr"))
    # The shipped example drops `state_dir` (the relocated default lives in the
    # user home, out of the workspace). The e2e fixture replays historical
    # artifact flows against it, so it declares an explicit in-repo state_dir
    # under tmp_path - never the real home (Q1 / §7.1).
    example_pipeline = repo / "agentic-dev-loop" / "pipeline.yaml"
    example_pipeline.write_text(
        example_pipeline.read_text().replace(
            "agents_dir: sdk_agents", "agents_dir: sdk_agents\nstate_dir: .pr", 1))
    (repo / "README.md").write_text("# README\n")
    # 033 brief_inputs.include resolves against the workspace root - the
    # shipped example lists these repo-rule files, so the consumer copy must
    # carry them (the real repo does).
    (repo / "AGENTS.md").write_text("# Repo rules\n")
    (repo / "coding-standards-ddd.yaml").write_text("standards: true\n")
    monkeypatch.chdir(repo)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "config", "user.name", "T"], check=True)
    (repo / "plan.md").write_text("# Plan\n\nBuild the thing.\n")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "init"], check=True)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    # Never kill a contributor's real process on that port (see module
    # docstring).
    monkeypatch.setattr("agentic_dev_loop.engine.actions._listening_pids",
                        lambda *a, **k: [])
    # Script the example's mechanical test command (see _fake_sandbox_run).
    _script_test()
    fake_sandbox.script(_fake_sandbox_run)
    return repo


def _log(repo: Path) -> str:
    return subprocess.run(["git", "log", "--oneline"], cwd=repo,
                          capture_output=True, text=True, check=True).stdout


class TestHistoricalFixtures:
    def test_run004_decorated_verdict_still_passes(self, consumer_repo,
                                                   fake_agents):
        """QA run 004 burned 3 attempts on `**VERDICT: PASS**` - markdown
        decoration around the verdict line. The tolerant parser (LAST
        VERDICT line wins) must route this as a pass, not an unclear retry."""
        _passing_scripts(fake_agents, qa=[
            ("write", "qa-report.md",
             "## CI analysis\n\nEverything is fine.\n\n**VERDICT: PASS**\n")])
        rc = main([str(consumer_repo / "agentic-dev-loop" / "pipeline.yaml"), "--plan", "plan.md", "--no-viz"])
        assert rc == 0
        assert fake_agents.agents_called() == ["brief", "dev", "review", "qa"]
        assert "feat: construct plan plan.md (agent dev loop)" in _log(
            consumer_repo)

    def test_stale_artifact_is_deleted_between_attempts(self, consumer_repo,
                                                        fake_agents):
        """A stale artifact from attempt N must not leak into attempt N+1's
        verdict - capture is per-attempt (read from the attempt's own final
        response), so attempt 2's empty response must produce the
        empty-response note, proving the stale FAIL was not re-read."""
        _passing_scripts(fake_agents, qa=[
            ("write", "qa-report.md", "STALE findings\n\nVERDICT: FAIL\n"),
            ("ok",),  # attempt 2 produces no final response
            ("write", "qa-report.md", "Fixed.\n\nVERDICT: PASS\n"),
        ])
        rc = main([str(consumer_repo / "agentic-dev-loop" / "pipeline.yaml"), "--plan", "plan.md", "--no-viz"])
        assert rc == 0
        attempt3_qa = fake_agents.messages_for("qa")[-1]
        assert "qa agent produced no final response on attempt 2." in attempt3_qa
        assert "qa output (attempt 2)" not in attempt3_qa

    def test_verdict_written_before_turn_cap_still_passes(self, consumer_repo,
                                                          fake_agents):
        """Verdict-first truncation: a clear verdict stands even when the run
        was cut short (turn cap / stuck detection) before the agent formally
        finished - the artifact on disk is the source of truth."""
        _passing_scripts(fake_agents, qa=[
            ("write_truncated", "qa-report.md",
             "Partial QA findings, cut short.\n\nVERDICT: PASS\n")])
        rc = main([str(consumer_repo / "agentic-dev-loop" / "pipeline.yaml"), "--plan", "plan.md", "--no-viz"])
        assert rc == 0
        assert fake_agents.agents_called().count("qa") == 1
        assert "(agent dev loop)" in _log(consumer_repo)

    def test_unclear_verdict_retries_with_content_in_notes(self, consumer_repo,
                                                           fake_agents):
        """An unparseable verdict forces an attempt-bounded retry, and the
        artifact's content rides into the next attempt's notes - findings
        are never swallowed."""
        _passing_scripts(fake_agents, qa=[
            ("write", "qa-report.md",
             "The build looks mostly fine but I cannot decide.\n"
             "Line two of findings.\n"),
            ("write", "qa-report.md", "Decided.\n\nVERDICT: PASS\n"),
        ])
        rc = main([str(consumer_repo / "agentic-dev-loop" / "pipeline.yaml"), "--plan", "plan.md", "--no-viz"])
        assert rc == 0
        attempt2_dev = fake_agents.messages_for("dev")[1]
        assert "contains no verdict" in attempt2_dev
        assert "I cannot decide" in attempt2_dev

    def test_dirty_worktree_at_start_skips_commit_but_run_succeeds(
            self, consumer_repo, fake_agents):
        """Run 004: the dev agent's commit swept up an unrelated edit. Now the
        single commit point only fires when the worktree was clean at start -
        a dirty-at-start run still succeeds, it just doesn't commit."""
        (consumer_repo / "plan.md").write_text("# Plan\n\nEdited by a human.\n")
        _passing_scripts(fake_agents)
        rc = main([str(consumer_repo / "agentic-dev-loop" / "pipeline.yaml"), "--plan", "plan.md", "--no-viz"])
        assert rc == 0
        assert "(agent dev loop)" not in _log(consumer_repo)

    def test_empty_unit_dir_fails_fast(self, consumer_repo, fake_agents):
        """U3 (2026-07-19): an empty unit dir silently degraded to
        inception-only context the agents couldn't act on. Fail fast."""
        (consumer_repo / "aidlc-docs" / "construction"
         / "unit-99").mkdir(parents=True)
        with pytest.raises(RuntimeError, match="yielded no docs"):
            main([str(consumer_repo / "agentic-dev-loop" / "pipeline.yaml"),
                  "--aidlc-unit", "unit-99", "--no-viz"])
        assert fake_agents.calls == []


class TestCommandGate:
    def _run(self, consumer_repo) -> int:
        return main([str(consumer_repo / "agentic-dev-loop" / "pipeline.yaml"),
                     "--plan", "plan.md", "--no-viz"])

    def test_gate_passes_through(self, consumer_repo, fake_agents):
        _passing_scripts(fake_agents)
        assert self._run(consumer_repo) == 0
        assert fake_agents.agents_called() == ["brief", "dev", "review", "qa"]
        assert "feat: construct plan plan.md (agent dev loop)" in _log(
            consumer_repo)

    def test_gate_fail_rewinds_to_dev_with_output(self, consumer_repo,
                                                  fake_agents):
        _passing_scripts(fake_agents)
        _script_test(("fail", "2 failed, 1 passed in 0.05s\n"), ("pass",))
        assert self._run(consumer_repo) == 0
        assert fake_agents.agents_called() == ["brief", "dev", "dev", "review", "qa"]
        second_dev = fake_agents.messages_for("dev")[1]
        assert "2 failed, 1 passed" in second_dev
        assert "test output (attempt 1)" in second_dev

    def test_gate_timeout_rewinds_with_tail(self, consumer_repo, fake_agents):
        _passing_scripts(fake_agents)
        _script_test(("timeout", "collected 250 items\n"), ("pass",))
        assert self._run(consumer_repo) == 0
        second_dev = fake_agents.messages_for("dev")[1]
        assert "timed out" in second_dev
        assert "collected 250 items" in second_dev

    def test_gate_fail_forever_exhausts(self, consumer_repo, fake_agents):
        _passing_scripts(fake_agents)
        _script_test(("fail", "boom\n"))
        assert self._run(consumer_repo) != 0
        assert fake_agents.agents_called() == ["brief"] + ["dev"] * 10
        assert "(agent dev loop)" not in _log(consumer_repo)


class TestExampleExtensionless:
    def test_example_dev_agent_builds_with_bundled_registry(self, monkeypatch):
        """The example's agents name only bundled tools - the dev agent
        builds against the default registry with no consumer extensions.
        (Real SDK factory, fake-key LLM - no network.)"""
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        registry = default_registry()
        cfg = load_config(EXAMPLE / "pipeline.yaml")
        agent = load_agent(EXAMPLE / "sdk_agents", "dev", build_llm(cfg.llm))
        assert agent is not None
