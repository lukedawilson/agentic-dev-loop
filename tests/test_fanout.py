"""Fanout command tests: pure planner, status table, stream matchers, and the
CLI usage surface. Child pipelines are never run here - the stream matchers
are exercised over a real pipe, and worktree creation is a real `git worktree
add` against a temp repo."""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentic_dev_loop.engine.extensions.aidlc import AidlcDocumentLoader
from agentic_dev_loop.engine.fanout import (
    FanoutEntry, FanoutStatus, FanoutUsageError, aggregate_exit_code,
    build_fanout_parser, classify_outcome, collect_fanout_entries,
    consume_stream, create_worktree, default_slug, extract_viz_url,
    is_failed_line, is_finished_line, plan_fanout, render_plan, render_table,
    resolve_repo_root, run_fanout, validate_entries,
)
from agentic_dev_loop.engine.loaders import PlanDocumentLoader
from agentic_dev_loop.engine.registry import Registry

MINIMAL_YAML = """\
name: fanout-test
llm:
  model: openai/deepseek-v4-pro
  base_url: https://api.deepseek.com
  api_key_env: TEST_LLM_KEY
state_dir: .pr
agents_dir: sdk_agents
deps_policy: open
steps:
  - name: dev
    agent: dev
    produces: implementation-summary.md
    on_pass: success
"""


def _git(repo, *args, check=True):
    result = subprocess.run(
        ["git", "-c", "user.email=test@example.com",
         "-c", "user.name=Fanout Test", *args],
        cwd=repo, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(result.stderr)
    return result


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "agentic-dev-loop").mkdir()
    (repo / "agentic-dev-loop" / "pipeline.yaml").write_text(MINIMAL_YAML)
    (repo / "plan-a.md").write_text("# Plan A")
    (repo / "plan-b.md").write_text("# Plan B")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _registry_with(*loaders):
    registry = Registry()
    for loader in loaders:
        registry.add_document_loader(loader)
    return registry


def _two_entries(repo, *shared):
    return [
        FanoutEntry("a", "plan plan-a.md",
                    ("--plan", str((repo / "plan-a.md").resolve()))),
        FanoutEntry("b", "plan plan-b.md",
                    ("--plan", str((repo / "plan-b.md").resolve()))),
    ]


class TestDefaultSlug:
    def test_sanitizes_runs_to_single_hyphen(self):
        assert default_slug("My Plan (v2)!") == "My-Plan-v2"

    def test_strips_leading_and_trailing_dot_dash(self):
        assert default_slug("..--unit 8--..") == "unit-8"

    def test_collapses_dotdot_for_branch_validity(self):
        assert default_slug("a..b") == "a.b"

    def test_empty_result_is_loud(self):
        with pytest.raises(ValueError, match="slug"):
            default_slug("...")


class TestPlanLoaderFanout:
    def test_multi_registers_append(self):
        parser = build_fanout_parser(_registry_with(PlanDocumentLoader()), [])
        args = parser.parse_args(["--plan", "a.md", "--plan", "b.md"])
        assert args.plan == [Path("a.md"), Path("b.md")]

    def test_single_run_registration_is_single_valued(self):
        import argparse
        parser = argparse.ArgumentParser()
        PlanDocumentLoader().add_cli_args(parser)
        assert parser.parse_args(["--plan", "a.md"]).plan == Path("a.md")

    def test_fanout_entries_build_invocations(self, tmp_path):
        plan_a = tmp_path / "foo.md"
        plan_b = tmp_path / "bar baz.md"
        plan_a.write_text("# A")
        plan_b.write_text("# B")
        import argparse
        args = argparse.Namespace(plan=[plan_a, plan_b])

        entries = PlanDocumentLoader().fanout_entries(args)

        assert [e.slug for e in entries] == ["foo", "bar-baz"]
        assert [e.label for e in entries] == ["plan foo.md", "plan bar baz.md"]
        assert entries[0].invocation == ("--plan", str(plan_a.resolve()))

    def test_no_flags_yields_none(self):
        import argparse
        assert PlanDocumentLoader().fanout_entries(
            argparse.Namespace(plan=None)) is None


class TestAidlcLoaderFanout:
    def test_multi_registers_append(self):
        parser = build_fanout_parser(
            _registry_with(AidlcDocumentLoader()), [])
        args = parser.parse_args(["--aidlc-unit", "unit-8",
                                  "--aidlc-unit", "unit-9"])
        assert args.aidlc_unit == ["unit-8", "unit-9"]

    def test_fanout_entries_keep_unit_slug_verbatim(self):
        import argparse
        args = argparse.Namespace(aidlc_unit=["unit-8", "support for windows"])
        entries = AidlcDocumentLoader().fanout_entries(args)
        assert [e.slug for e in entries] == ["unit-8", "support-for-windows"]
        assert [e.label for e in entries] == [
            "unit unit-8", "unit support for windows"]
        assert entries[0].invocation == ("--aidlc-unit", "unit-8")


class TestCollectEntries:
    def test_mixed_loaders_are_registry_ordered(self):
        registry = _registry_with(PlanDocumentLoader(), AidlcDocumentLoader())
        argv = ["--aidlc-unit", "unit-8", "--plan", "a.md"]
        args = build_fanout_parser(registry, argv).parse_args(argv)

        entries = collect_fanout_entries(registry, args)

        assert [e.slug for e in entries] == ["a", "unit-8"]


class TestValidateEntries:
    def test_zero_entries_is_usage_error(self):
        with pytest.raises(FanoutUsageError, match="at least two"):
            validate_entries([])

    def test_one_entry_points_at_single_run(self, repo):
        with pytest.raises(FanoutUsageError, match="single run") as excinfo:
            validate_entries(_two_entries(repo)[:1])
        assert "--plan" in str(excinfo.value)

    def test_duplicate_slug_names_both_entries(self):
        entries = [
            FanoutEntry("same", "plan one/foo.md", ("--plan", "one/foo.md")),
            FanoutEntry("same", "unit same", ("--aidlc-unit", "same")),
        ]
        with pytest.raises(FanoutUsageError, match="duplicate") as excinfo:
            validate_entries(entries)
        assert "plan one/foo.md" in str(excinfo.value)
        assert "unit same" in str(excinfo.value)


class TestNonCapableLoaders:
    class _OldLoader:
        name = "old"
        defaultable = False

        def add_cli_args(self, parser):
            parser.add_argument("--old-doc", default=None)

        def resolve(self, args):  # pragma: no cover - never invoked
            raise NotImplementedError

    class _HalfLoader:
        name = "half"
        defaultable = False

        def add_cli_args(self, parser, *, multi=False):
            parser.add_argument("--half-doc", default=None)

        def resolve(self, args):  # pragma: no cover - never invoked
            raise NotImplementedError

    def test_loader_without_multi_kwarg_is_refused_loudly(self):
        registry = _registry_with(self._OldLoader())
        with pytest.raises(FanoutUsageError, match="does not support fanout"):
            build_fanout_parser(registry, ["--old-doc", "x"])

    def test_loader_without_fanout_entries_is_refused_loudly(self):
        registry = _registry_with(self._HalfLoader())
        with pytest.raises(FanoutUsageError, match="does not support fanout"):
            build_fanout_parser(registry, ["--half-doc", "x"])

    def test_absent_flag_from_non_capable_loader_is_harmless(self):
        registry = _registry_with(self._OldLoader())
        parser = build_fanout_parser(registry, [])
        assert parser.parse_args([]).pipeline is None


class TestPlanner:
    def test_resolves_repo_root(self, repo):
        assert resolve_repo_root(repo).resolve() == repo.resolve()

    def test_non_repo_cwd_is_loud(self, tmp_path):
        with pytest.raises(FanoutUsageError, match="not a git repository"):
            resolve_repo_root(tmp_path)

    def test_plan_re_roots_pipeline_and_argv(self, repo, tmp_path):
        pipeline = repo / "agentic-dev-loop" / "pipeline.yaml"
        plan = plan_fanout(
            _two_entries(repo), pipeline=pipeline, repo_root=repo,
            base_ref="HEAD", worktree_root=tmp_path / "wt",
            shared_flags=("--no-viz",))

        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        assert plan.base_ref == "HEAD"
        assert plan.base_commit == head
        first = plan.entries[0]
        assert first.branch == "loop/fanout/a"
        assert first.worktree == tmp_path / "wt" / "fanout-a"
        assert first.pipeline == (tmp_path / "wt" / "fanout-a"
                                  / "agentic-dev-loop" / "pipeline.yaml")
        assert first.argv[:3] == (sys.executable, "-m", "agentic_dev_loop")
        assert first.argv[3] == str(first.pipeline)
        assert first.argv[4:6] == ("--plan",
                                   str((repo / "plan-a.md").resolve()))
        assert first.argv[-1] == "--no-viz"

    def test_pipeline_outside_repo_is_usage_error(self, repo, tmp_path):
        outside = tmp_path / "elsewhere.yaml"
        outside.write_text(MINIMAL_YAML)
        with pytest.raises(FanoutUsageError, match="inside the git repository"):
            plan_fanout(_two_entries(repo), pipeline=outside, repo_root=repo,
                        base_ref="HEAD", worktree_root=tmp_path / "wt",
                        shared_flags=())

    def test_untracked_pipeline_is_usage_error(self, repo, tmp_path):
        pipeline = repo / "agentic-dev-loop" / "extra.yaml"
        pipeline.write_text(MINIMAL_YAML)
        with pytest.raises(FanoutUsageError, match="not tracked"):
            plan_fanout(_two_entries(repo), pipeline=pipeline, repo_root=repo,
                        base_ref="HEAD", worktree_root=tmp_path / "wt",
                        shared_flags=())

    def test_unknown_base_ref_is_usage_error(self, repo, tmp_path):
        pipeline = repo / "agentic-dev-loop" / "pipeline.yaml"
        with pytest.raises(FanoutUsageError, match="base ref"):
            plan_fanout(_two_entries(repo), pipeline=pipeline, repo_root=repo,
                        base_ref="no-such-ref", worktree_root=tmp_path / "wt",
                        shared_flags=())

    def test_render_plan_lists_entries(self, repo, tmp_path):
        pipeline = repo / "agentic-dev-loop" / "pipeline.yaml"
        plan = plan_fanout(_two_entries(repo), pipeline=pipeline,
                           repo_root=repo, base_ref="HEAD",
                           worktree_root=tmp_path / "wt", shared_flags=())
        text = render_plan(plan)
        assert "fanout plan: 2 entries" in text
        assert "loop/fanout/a" in text
        assert "loop/fanout/b" in text
        assert str(pipeline) in text or "agentic-dev-loop/pipeline.yaml" in text


class TestWorktrees:
    def test_creates_branch_and_worktree(self, repo, tmp_path):
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()
        worktree = tmp_path / "wt" / "a"

        error = create_worktree(repo, worktree, "loop/fanout/a", base)

        assert error is None
        assert (worktree / "agentic-dev-loop" / "pipeline.yaml").is_file()
        branches = _git(repo, "branch", "--list", "loop/fanout/a").stdout
        assert "loop/fanout/a" in branches

    def test_existing_branch_fails_loudly(self, repo, tmp_path):
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()
        assert create_worktree(repo, tmp_path / "wt" / "a",
                               "loop/fanout/a", base) is None

        error = create_worktree(repo, tmp_path / "wt" / "a2",
                                "loop/fanout/a", base)

        assert error is not None
        assert "loop/fanout/a" in error


class TestTable:
    def _rows(self):
        return [
            FanoutStatus("unit-8", "loop/fanout/unit-8",
                         Path("/repo/../fanout-unit-8"),
                         "http://127.0.0.1:9000", "PASSED"),
            FanoutStatus("foo", "loop/fanout/foo",
                         Path("/repo/../fanout-foo"), None, "FAILED"),
        ]

    def test_renders_every_column_and_outcome(self):
        text = render_table(self._rows(), Path("/repo"))
        lines = text.splitlines()
        assert lines[0].split() == ["slug", "branch", "worktree", "viz",
                                    "URL", "outcome"] or \
            lines[0].split() == ["slug", "branch", "worktree", "viz URL",
                                 "outcome"]
        assert "../fanout-unit-8" in text
        assert "http://127.0.0.1:9000" in text
        assert "PASSED" in text and "FAILED" in text
        assert all(len(line) for line in lines)

    def test_missing_url_renders_a_dash(self):
        assert " - " in render_table(self._rows(), Path("/repo"))

    def test_aggregate_zero_only_when_all_passed(self):
        rows = self._rows()
        assert aggregate_exit_code(rows[:1]) == 0
        assert aggregate_exit_code(rows) == 1

    def test_skipped_and_aborted_are_failures(self):
        for outcome in ("SKIPPED", "ABORTED", "FAILED"):
            row = FanoutStatus("x", "loop/fanout/x", Path("/x"), None, outcome)
            assert aggregate_exit_code([row]) == 1


class TestStreamMatchers:
    def test_viz_url_extraction(self):
        assert extract_viz_url(
            "Live graph viz: http://127.0.0.1:8321") == "http://127.0.0.1:8321"
        assert extract_viz_url("nothing here") is None

    def test_finished_marker_is_exact(self):
        assert is_finished_line("Pipeline finished: press ctrl+c to exit")
        assert not is_finished_line("Pipeline finished early")

    def test_failed_marker_is_exact(self):
        assert is_failed_line(
            "agentic-dev-loop: pipeline failed - exiting with code 1")
        assert not is_failed_line("pipeline failed")

    def test_classify_outcome_matrix(self):
        assert classify_outcome(finished=True, failed=False,
                                returncode=None) == "PASSED"
        assert classify_outcome(finished=True, failed=True,
                                returncode=None) == "FAILED"
        assert classify_outcome(finished=False, failed=True,
                                returncode=1) == "FAILED"
        assert classify_outcome(finished=False, failed=False,
                                returncode=0) == "PASSED"
        assert classify_outcome(finished=False, failed=False,
                                returncode=3) == "ABORTED"

    def _child(self, code):
        return subprocess.Popen(
            [sys.executable, "-c", code], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace")

    def test_consume_stream_prefixes_and_captures_url(self):
        child = self._child(
            "print('Live graph viz: http://127.0.0.1:9001')\n"
            "print('Pipeline finished: press ctrl+c to exit')\n"
            "import time; time.sleep(5)\n")
        out = io.StringIO()
        try:
            result = consume_stream(child.stdout, "slug1", out)
        finally:
            child.kill()
            child.wait()
        assert result.viz_url == "http://127.0.0.1:9001"
        assert result.finished is True
        assert result.failed is False
        assert out.getvalue().splitlines()[0].startswith("[slug1] ")

    def test_consume_stream_merges_failure_marker(self):
        child = self._child(
            "import sys\n"
            "print('agentic-dev-loop: pipeline failed - exiting with code 1',"
            " file=sys.stderr)\n"
            "print('Pipeline finished: press ctrl+c to exit')\n"
            "import time; time.sleep(5)\n")
        out = io.StringIO()
        try:
            result = consume_stream(child.stdout, "slug2", out)
        finally:
            child.kill()
            child.wait()
        assert result.finished is True
        assert result.failed is True
        assert classify_outcome(finished=result.finished,
                                failed=result.failed,
                                returncode=None) == "FAILED"

    def test_dead_process_fallback_without_markers(self):
        child = self._child("pass")
        out = io.StringIO()
        result = consume_stream(child.stdout, "slug3", out)
        code = child.wait()
        assert result.finished is False
        assert classify_outcome(finished=False, failed=False,
                                returncode=code) == "PASSED"


class TestRunFanoutUsage:
    def _write(self, root):
        (root / "agentic-dev-loop").mkdir(parents=True, exist_ok=True)
        (root / "agentic-dev-loop" / "pipeline.yaml").write_text(MINIMAL_YAML)
        (root / "a.md").write_text("# A")
        (root / "b.md").write_text("# B")

    def test_missing_pipeline_is_exit_2(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert run_fanout(["--plan", "a.md", "--plan", "b.md"]) == 2
        assert "no pipeline" in capsys.readouterr().err

    def test_single_entry_is_exit_2(self, repo, monkeypatch, capsys):
        monkeypatch.chdir(repo)
        code = run_fanout(["agentic-dev-loop/pipeline.yaml",
                           "--plan", "plan-a.md"])
        assert code == 2
        assert "single run" in capsys.readouterr().err

    def test_no_entries_is_exit_2(self, repo, monkeypatch, capsys):
        monkeypatch.chdir(repo)
        code = run_fanout(["agentic-dev-loop/pipeline.yaml"])
        assert code == 2
        assert "at least two" in capsys.readouterr().err

    def test_duplicate_slug_is_exit_2(self, repo, monkeypatch, capsys):
        (repo / "nested").mkdir()
        (repo / "nested" / "plan-a.md").write_text("# A2")
        monkeypatch.chdir(repo)
        code = run_fanout(["agentic-dev-loop/pipeline.yaml",
                           "--plan", "plan-a.md",
                           "--plan", "nested/plan-a.md"])
        assert code == 2
        assert "duplicate" in capsys.readouterr().err

    def test_non_repo_cwd_is_exit_2(self, tmp_path, monkeypatch, capsys):
        self._write(tmp_path)
        monkeypatch.chdir(tmp_path)
        code = run_fanout(["agentic-dev-loop/pipeline.yaml",
                           "--plan", "a.md", "--plan", "b.md"])
        assert code == 2
        assert "not a git repository" in capsys.readouterr().err

    def test_unknown_flag_is_argparse_exit_2(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        with pytest.raises(SystemExit) as excinfo:
            run_fanout(["agentic-dev-loop/pipeline.yaml", "--bogus"])
        assert excinfo.value.code == 2

    def test_dry_run_prints_plan_without_creating_worktrees(
            self, repo, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(repo)
        root = tmp_path / "wt"
        code = run_fanout([
            "agentic-dev-loop/pipeline.yaml",
            "--plan", "plan-a.md", "--plan", "plan-b.md",
            "--worktree-root", str(root), "--dry-run"])

        assert code == 0
        out = capsys.readouterr().out
        assert "fanout plan: 2 entries" in out
        assert "loop/fanout/plan-a" in out
        assert "loop/fanout/plan-b" in out
        assert not root.exists()

    def test_pipeline_must_precede_options(self, repo, monkeypatch, capsys):
        monkeypatch.chdir(repo)
        code = run_fanout(["--plan", "plan-a.md", "--plan", "plan-b.md",
                           "agentic-dev-loop/pipeline.yaml", "--dry-run"])
        assert code == 2
        assert "before fanout options" in capsys.readouterr().err

    def test_dry_run_default_worktree_root_is_sibling_fanout_slug(
            self, repo, monkeypatch, capsys):
        monkeypatch.chdir(repo)
        code = run_fanout([
            "agentic-dev-loop/pipeline.yaml",
            "--plan", "plan-a.md", "--plan", "plan-b.md", "--dry-run"])

        assert code == 0
        out = capsys.readouterr().out
        assert "fanout plan: 2 entries" in out
        assert "../fanout-plan-a" in out
        assert "../fanout-plan-b" in out
        assert str(repo.parent) in out
        assert not (repo.parent / "fanout-plan-a").exists()
        assert not (repo.parent / "fanout-plan-b").exists()


class TestMainEntryPoint:
    def test_python_dash_m_passes_exit_code_through(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(root / "src"), env.get("PYTHONPATH", "")]))
        result = subprocess.run(
            [sys.executable, "-m", "agentic_dev_loop", "help"],
            cwd=root, env=env, capture_output=True, text=True)
        assert result.returncode == 0
        assert "usage: agentic-dev-loop" in result.stdout
