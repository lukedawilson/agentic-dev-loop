"""CLI tests - two-phase argparse: the pipeline YAML determines which
extension flags exist (a loader's flag appears only when its extension is
listed in the pipeline's `extensions:`). Pipeline runs are real; FakeAgents
stubs only the SDK network boundary."""

import importlib.metadata
import os
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import pytest

from agentic_dev_loop.engine.cli import build_parser, main

PLAIN_YAML = """\
name: tiny
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

RESUME_YAML = """\
name: tiny-resume
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
    on_pass: check
  - name: check
    agent: check
    artifact: ci-fix.md
    verdicts: { pass: PASS, fail: FAIL }
    on_pass: success
    on_fail: failure
"""

RETRY_YAML = """\
name: tiny-retry
llm:
  model: openai/deepseek-v4-pro
  base_url: https://api.deepseek.com
  api_key_env: TEST_LLM_KEY
max_attempts: 1
agents_dir: sdk_agents
deps_policy: open
steps:
  - name: dev
    agent: dev
    produces: implementation-summary.md
    on_pass: check
  - name: check
    agent: check
    artifact: ci-fix.md
    verdicts: { pass: PASS, fail: FAIL }
    on_pass: success
    on_fail: { goto: dev, retry: true }
"""


def with_extensions(yaml_text: str, *refs: str) -> str:
    block = "extensions:\n" + "".join(f"  - {r}\n" for r in refs)
    return block + yaml_text


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch, fake_sandbox):
    """A real git repo with the plain pipeline + a plan doc."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pipeline.yaml").write_text(PLAIN_YAML)
    (repo / "plan.md").write_text("THE PLAN")
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "init")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test")
    return repo


def write_pipeline(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", f"add {name}")
    return name


class TestHelp:
    def test_core_and_builtin_loader_flags(self, repo, capsys):
        with pytest.raises(SystemExit) as e:
            main(["pipeline.yaml", "--help"])
        assert e.value.code == 0
        out = capsys.readouterr().out
        assert "--resume" in out
        assert "--max-attempts" in out
        assert "--no-viz" in out
        assert "--viz-port" in out
        assert "--plan" in out  # built-in plan loader is always present

    def test_extension_flags_only_when_configured(self, repo, capsys):
        write_pipeline(repo, "aidlc.yaml", with_extensions(
            PLAIN_YAML, "agentic_dev_loop.engine.extensions.aidlc:AidlcExtension"))
        with pytest.raises(SystemExit) as e:
            main(["aidlc.yaml", "--help"])
        assert e.value.code == 0
        assert "--aidlc-unit" in capsys.readouterr().out

        with pytest.raises(SystemExit):
            main(["pipeline.yaml", "--help"])
        assert "--aidlc-unit" not in capsys.readouterr().out

    def test_no_pipeline_shows_core_surface(self, repo, capsys):
        with pytest.raises(SystemExit) as e:
            main(["--help"])
        assert e.value.code == 0
        assert "--resume" in capsys.readouterr().out


class TestVersion:
    def test_version_flag_without_pipeline(self, capsys):
        with pytest.raises(SystemExit) as e:
            main(["--version"])
        assert e.value.code == 0
        assert capsys.readouterr().out.strip() == (
            f"agentic-dev-loop {importlib.metadata.version('agentic-dev-loop')}"
        )

    def test_version_flag_with_pipeline(self, repo, capsys):
        with pytest.raises(SystemExit) as e:
            main(["pipeline.yaml", "--version"])
        assert e.value.code == 0
        assert capsys.readouterr().out.strip() == (
            f"agentic-dev-loop {importlib.metadata.version('agentic-dev-loop')}"
        )

    def test_version_flag_in_core_help(self, repo, capsys):
        with pytest.raises(SystemExit) as e:
            main(["--help"])
        assert e.value.code == 0
        assert "--version" in capsys.readouterr().out


class TestRun:
    def test_success_returns_zero(self, repo, fake_agents):
        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
        assert main(["pipeline.yaml", "--plan", "plan.md", "--no-viz"]) == 0

    def test_failure_returns_one(self, repo, fake_agents):
        write_pipeline(repo, "retry.yaml", RETRY_YAML)
        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
        fake_agents.set("check", [("write", "ci-fix.md", "VERDICT: FAIL")])
        assert main(["retry.yaml", "--plan", "plan.md", "--no-viz"]) == 1

    def test_max_attempts_overrides_config(self, repo, fake_agents):
        write_pipeline(repo, "retry.yaml", RETRY_YAML)  # YAML says max_attempts: 1
        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
        fake_agents.set("check", [("write", "ci-fix.md", "VERDICT: FAIL")])
        assert main(["retry.yaml", "--plan", "plan.md",
                     "--max-attempts", "2", "--no-viz"]) == 1
        assert fake_agents.agents_called().count("dev") == 2

    def test_resume_threads_through(self, repo, fake_agents):
        write_pipeline(repo, "resume.yaml", RESUME_YAML)
        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
        fake_agents.set("check", [
            ("crash",),  # process death mid-run
            ("write", "ci-fix.md", "VERDICT: PASS"),
        ])
        with pytest.raises(KeyboardInterrupt):
            main(["resume.yaml", "--plan", "plan.md", "--no-viz"])

        db = repo / ".pr" / "loop-checkpoints.sqlite"
        with sqlite3.connect(db) as conn:
            (tid,) = conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints").fetchone()

        assert main(["resume.yaml", "--resume", tid, "--no-viz"]) == 0
        # dev completed before the crash - only test re-ran on resume
        assert fake_agents.agents_called() == ["dev", "check", "check"]

    def test_success_stamps_plan_completed(self, repo, fake_agents):
        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])

        assert main(["pipeline.yaml", "--plan", "plan.md", "--no-viz"]) == 0

        assert (repo / "plan.md").read_text() == (
            "> **Status: COMPLETED**\n\nTHE PLAN")

    def test_success_replaces_existing_status(self, repo, fake_agents):
        (repo / "plan.md").write_text(
            "# Plan\n\n> **Status: DONE**\n\nTHE PLAN\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "record status")
        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])

        assert main(["pipeline.yaml", "--plan", "plan.md", "--no-viz"]) == 0

        assert (repo / "plan.md").read_text() == (
            "# Plan\n\n> **Status: COMPLETED**\n\nTHE PLAN\n")

    def test_failed_run_does_not_stamp(self, repo, fake_agents):
        write_pipeline(repo, "retry.yaml", RETRY_YAML)
        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
        fake_agents.set("check", [("write", "ci-fix.md", "VERDICT: FAIL")])

        assert main(["retry.yaml", "--plan", "plan.md", "--no-viz"]) == 1

        assert (repo / "plan.md").read_text() == "THE PLAN"

    def test_aidlc_run_leaves_docs_untouched(self, repo, fake_agents):
        write_pipeline(repo, "aidlc.yaml", with_extensions(
            PLAIN_YAML, "agentic_dev_loop.engine.extensions.aidlc:AidlcExtension"))
        unit_dir = repo / "aidlc-docs" / "construction" / "unit-3"
        unit_dir.mkdir(parents=True)
        (unit_dir / "design.md").write_text("# Design")
        (repo / "aidlc-docs" / "aidlc-state.md").write_text(
            "# State\n\n- **Current Stage**: CONSTRUCTION - Unit unit-3 (SCAN).\n",
            encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "aidlc fixture")
        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
        state_before = (repo / "aidlc-docs" / "aidlc-state.md").read_text(encoding="utf-8")

        assert main(["aidlc.yaml", "--aidlc-unit", "unit-3", "--no-viz"]) == 0

        assert (repo / "aidlc-docs" / "aidlc-state.md").read_text(encoding="utf-8") == state_before
        assert (repo / "aidlc-docs" / "construction" / "unit-3" / "design.md").read_text() == "# Design"
        assert (repo / "plan.md").read_text() == "THE PLAN"


class TestViz:
    def test_no_viz_flags_parse(self, repo):
        args = build_parser("pipeline.yaml").parse_args(
            ["pipeline.yaml", "--no-viz", "--viz-port", "9"])
        assert args.no_viz is True
        assert args.viz_port == 9

    def test_viz_on_by_default(self, repo):
        args = build_parser("pipeline.yaml").parse_args(["pipeline.yaml"])
        assert args.no_viz is False

    def test_lifecycle_serves_and_exits(self, repo, fake_agents, monkeypatch):
        import queue

        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
        captured = {}
        order = []
        sweep_calls = []

        def fake_sweep(port, match=None, term_timeout=3.0, cwd=None):
            sweep_calls.append((port, match, cwd))
            order.append("sweep")

        def fake_serve_viz(bus, topology, subject, port, nodes=()):
            captured["bus"] = bus
            captured["topology"] = topology
            captured["subject"] = subject
            captured["port"] = port
            captured["nodes"] = nodes
            order.append("serve")
            return types.SimpleNamespace(server_address=("127.0.0.1", port)), \
                object()

        monkeypatch.setattr("agentic_dev_loop.engine.graph.kill_listeners_on_port",
                            fake_sweep)
        monkeypatch.setattr("agentic_dev_loop.viz.serve_viz", fake_serve_viz)
        opened = []
        monkeypatch.setattr("webbrowser.open", opened.append)

        stdout_before = sys.stdout
        stderr_before = sys.stderr
        code = main(["pipeline.yaml", "--plan", "plan.md"])  # served by default
        assert code == 0  # main returns only when the loop completes - no keep-alive
        assert sys.stdout is stdout_before
        assert sys.stderr is stderr_before
        assert "dev" in captured["topology"]
        assert "success" in captured["topology"]
        assert "dev" in captured["nodes"]
        assert "success" in captured["nodes"]
        assert "bump" not in captured["nodes"]
        assert captured["subject"] == "plan plan.md"
        assert captured["port"] == 8321
        assert opened == ["http://127.0.0.1:8321"]
        assert sweep_calls == [(8321, "agentic-dev-loop", repo.resolve())]
        assert order == ["sweep", "serve"]

        events = []
        q = captured["bus"].subscribe()
        while True:
            try:
                events.append(q.get_nowait())
            except queue.Empty:
                break
        assert any(e["type"] == "run_started" for e in events)
        assert any(e["type"] == "run_finished" for e in events)
        run_started = next(e for e in events if e["type"] == "run_started")
        assert run_started["attempt"] == 1

    def test_busy_preferred_port_falls_back_and_notices(self, repo, fake_agents,
                                                        monkeypatch, capsys):
        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
        monkeypatch.setattr("agentic_dev_loop.engine.graph.kill_listeners_on_port",
                            lambda *a, **k: None)
        monkeypatch.setattr(
            "agentic_dev_loop.viz.serve_viz",
            lambda *a, **k: (types.SimpleNamespace(
                server_address=("127.0.0.1", 45123)), object()))
        opened = []
        monkeypatch.setattr("webbrowser.open", opened.append)

        code = main(["pipeline.yaml", "--plan", "plan.md"])
        assert code == 0
        assert opened == ["http://127.0.0.1:45123"]
        assert ("viz port 8321 busy - serving on http://127.0.0.1:45123"
                in capsys.readouterr().out)

    def test_viz_port_zero_means_any_port_no_notice(self, repo, fake_agents,
                                                    monkeypatch, capsys):
        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
        swept = []
        monkeypatch.setattr("agentic_dev_loop.engine.graph.kill_listeners_on_port",
                            lambda *a, **k: swept.append(a))
        monkeypatch.setattr(
            "agentic_dev_loop.viz.serve_viz",
            lambda *a, **k: (types.SimpleNamespace(
                server_address=("127.0.0.1", 45123)), object()))
        opened = []
        monkeypatch.setattr("webbrowser.open", opened.append)

        code = main(["pipeline.yaml", "--plan", "plan.md", "--viz-port", "0"])
        assert code == 0
        assert opened == ["http://127.0.0.1:45123"]
        assert swept == []
        assert "busy" not in capsys.readouterr().out

    def test_no_viz_suppresses_server(self, repo, fake_agents, monkeypatch):
        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
        called = []
        swept = []
        monkeypatch.setattr(
            "agentic_dev_loop.viz.serve_viz",
            lambda *a, **k: called.append("serve") or (object(), object()))
        monkeypatch.setattr("agentic_dev_loop.engine.graph.kill_listeners_on_port",
                            lambda *a, **k: swept.append(a))

        code = main(["pipeline.yaml", "--plan", "plan.md", "--no-viz"])
        assert code == 0
        assert called == []
        assert swept == []


CLEAN_YAML = """\
name: clean-test
llm:
  model: openai/deepseek-v4-pro
  base_url: https://api.deepseek.com
  api_key_env: TEST_LLM_KEY
state_dir: .pr
agents_dir: sdk_agents
deps_policy: open
isolation:
  state_dir: {iso_state_dir}
steps:
  - name: dev
    agent: dev
    produces: implementation-summary.md
    on_pass: success
"""


class TestRefreshAndCleanStamps:
    def test_flags_parse(self, repo):
        args = build_parser("pipeline.yaml").parse_args(
            ["pipeline.yaml", "--refresh", "--clean-stamps", "--no-viz"])
        assert args.refresh is True
        assert args.clean_stamps is True

    def test_flags_default_false(self, repo):
        args = build_parser("pipeline.yaml").parse_args(["pipeline.yaml"])
        assert args.refresh is False
        assert args.clean_stamps is False

    def test_clean_stamps_removes_and_exits_zero(self, repo, tmp_path):
        from agentic_dev_loop.engine.isolation import write_stamp

        state = tmp_path / "iso-state"
        write_stamp(state / "stamps", "k1")
        write_stamp(state / "stamps", "k2")
        yaml = CLEAN_YAML.format(iso_state_dir=str(state))
        write_pipeline(repo, "clean.yaml", yaml)

        assert main(["clean.yaml", "--clean-stamps"]) == 0
        assert not (state / "stamps" / "k1").exists()
        assert not (state / "stamps" / "k2").exists()

    def test_clean_stamps_loads_config_once(self, repo, tmp_path, monkeypatch):
        # --clean-stamps must reuse the config _registry_for already loaded;
        # loading it twice is the pre-fix behaviour this guards against.
        import agentic_dev_loop.engine.cli as cli_mod

        state = tmp_path / "iso-state"
        yaml = CLEAN_YAML.format(iso_state_dir=str(state))
        write_pipeline(repo, "clean.yaml", yaml)

        real = cli_mod.load_config
        calls = []

        def counting(cfg_path):
            calls.append(cfg_path)
            return real(cfg_path)

        monkeypatch.setattr(cli_mod, "load_config", counting)

        assert cli_mod.main(["clean.yaml", "--clean-stamps"]) == 0
        assert len(calls) == 1



class TestContainersSubcommand:
    def test_help_without_pipeline(self, capsys):
        with pytest.raises(SystemExit) as e:
            main(["containers", "--help"])
        assert e.value.code == 0
        out = capsys.readouterr().out
        assert "--reap" in out
        assert "--json" in out
        assert "--state-dir" in out


class TestCliLightweightImports:
    def test_containers_path_does_not_import_sdk(self):
        # Change 5: `containers`, `--help`, and `--version` must load no
        # pipeline, extension, or SDK imports. Importing cli in a clean
        # interpreter must not pull in dryrun -> stages -> openhands.sdk /
        # langgraph (the pre-fix top-level `from .dryrun import dry_run` did).
        root = Path(__file__).resolve().parents[1]
        src = root / "src"
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(src), str(root), env.get("PYTHONPATH", "")]))
        code = (
            "import sys, agentic_dev_loop.engine.cli;"
            "print('openhands.sdk' in sys.modules);"
            "print('langgraph' in sys.modules);"
            "print('agentic_dev_loop.engine.dryrun' in sys.modules);"
            "print('agentic_dev_loop.engine.stages' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=root, env=env,
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().splitlines() == [
            "False", "False", "False", "False"]


class TestSigtermHandler:
    def test_first_sigterm_raises_system_exit_143(self, monkeypatch):
        import agentic_dev_loop.engine.cli as cli_mod

        cli_mod._sigterm_in_progress = False
        with pytest.raises(SystemExit) as e:
            cli_mod._handle_sigterm(15, None)
        assert e.value.code == 143
        assert cli_mod._sigterm_in_progress is True

    def test_second_sigterm_hard_exits(self, monkeypatch):
        import agentic_dev_loop.engine.cli as cli_mod

        cli_mod._sigterm_in_progress = True
        exited = []

        def fake_exit(code):
            exited.append(code)
            raise SystemExit(code)

        monkeypatch.setattr(cli_mod.os, "_exit", fake_exit)
        with pytest.raises(SystemExit) as e:
            cli_mod._handle_sigterm(15, None)
        assert exited == [143]
        assert e.value.code == 143

    def test_run_path_installs_and_restores_handler(self, repo, fake_agents,
                                                    monkeypatch):
        import agentic_dev_loop.engine.cli as cli_mod

        fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
        installed = []
        restored = []
        monkeypatch.setattr(cli_mod, "_install_sigterm_handler",
                            lambda: (installed.append(True) or object()))
        monkeypatch.setattr(cli_mod, "_restore_sigterm_handler",
                            lambda prev: restored.append(prev))

        assert cli_mod.main(["pipeline.yaml", "--plan", "plan.md",
                             "--no-viz"]) == 0
        assert installed == [True]
        assert len(restored) == 1
