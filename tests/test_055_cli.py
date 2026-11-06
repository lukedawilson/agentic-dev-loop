"""055 Task 8: CLI dispatch for init, doctor, and upgrade, plus the 057
post-run viz keep-alive."""

from __future__ import annotations

import subprocess
import types
import webbrowser
from pathlib import Path

import pytest

from agentic_dev_loop.engine import cli
from agentic_dev_loop.engine.cli import main

KEEPALIVE_LINE = "Pipeline finished: press ctrl+c to exit"

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
    on_fail: failure
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch, fake_sandbox):
    """A real git repo with the plain pipeline, a plan doc, and agent defs."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pipeline.yaml").write_text(PLAIN_YAML)
    (repo / "plan.md").write_text("THE PLAN")
    agents = repo / "sdk_agents"
    agents.mkdir()
    (agents / "dev.agent.md").write_text(
        "---\nname: dev\ntools: [terminal]\n---\nYou are the dev agent.\n")
    (agents / "check.agent.md").write_text(
        "---\nname: check\ntools: [terminal]\n---\nYou are the check agent.\n")
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test")
    return repo


@pytest.fixture
def quiet_viz(monkeypatch):
    """Keep the real server and browser out of the CLI tests."""
    monkeypatch.setattr("agentic_dev_loop.engine.graph.kill_listeners_on_port",
                        lambda *a, **k: None)
    monkeypatch.setattr(
        "agentic_dev_loop.viz.serve_viz",
        lambda *a, **k: (types.SimpleNamespace(
            server_address=("127.0.0.1", 45123)), object()))
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: None)


def _write_pipeline(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"add {name}")


def _interrupt(waits):
    def wait():
        waits.append(True)
        raise KeyboardInterrupt
    return wait


def test_keepalive_prints_exact_line_and_returns_zero(
        repo, fake_agents, quiet_viz, monkeypatch, capsys):
    fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
    waits = []
    monkeypatch.setattr(cli, "_wait_for_interrupt", _interrupt(waits))

    code = main(["pipeline.yaml", "--plan", "plan.md"])

    assert code == 0
    assert waits == [True]
    assert KEEPALIVE_LINE + "\n" in capsys.readouterr().out


def test_keepalive_failed_run_prints_line_and_returns_one(
        repo, fake_agents, quiet_viz, monkeypatch, capsys):
    _write_pipeline(repo, "retry.yaml", RETRY_YAML)
    fake_agents.set("dev", [("write", "implementation-summary.md", "done")])
    fake_agents.set("check", [("write", "ci-fix.md", "VERDICT: FAIL")])
    waits = []
    monkeypatch.setattr(cli, "_wait_for_interrupt", _interrupt(waits))

    code = main(["retry.yaml", "--plan", "plan.md"])

    assert code == 1
    assert waits == [True]
    assert KEEPALIVE_LINE + "\n" in capsys.readouterr().out


def test_keepalive_false_neither_prints_nor_blocks(
        repo, fake_agents, quiet_viz, monkeypatch, capsys):
    fake_agents.set("dev", [("write", "implementation-summary.md", "done")])

    def blocked():
        raise AssertionError("keep-alive wait called with keep_alive=False")

    monkeypatch.setattr(cli, "_wait_for_interrupt", blocked)

    code = main(["pipeline.yaml", "--plan", "plan.md"], keep_alive=False)

    assert code == 0
    assert KEEPALIVE_LINE not in capsys.readouterr().out


def test_keepalive_skipped_with_no_viz(
        repo, fake_agents, monkeypatch, capsys):
    fake_agents.set("dev", [("write", "implementation-summary.md", "done")])

    def blocked():
        raise AssertionError("keep-alive wait called with --no-viz")

    monkeypatch.setattr(cli, "_wait_for_interrupt", blocked)

    code = main(["pipeline.yaml", "--plan", "plan.md", "--no-viz"])

    assert code == 0
    assert KEEPALIVE_LINE not in capsys.readouterr().out


def test_keepalive_skipped_on_dry_run(repo, monkeypatch, capsys):
    def blocked():
        raise AssertionError("keep-alive wait called on --dry-run")

    monkeypatch.setattr(cli, "_wait_for_interrupt", blocked)

    code = main(["pipeline.yaml", "--dry-run", "--plan", "plan.md"])

    assert code == 0
    assert KEEPALIVE_LINE not in capsys.readouterr().out


def test_unknown_subcommand_falls_through_to_probe(monkeypatch):
    seen = {}

    def fake_registry(path):
        seen["path"] = path
        raise RuntimeError("probe reached")

    monkeypatch.setattr(cli, "_registry_for", fake_registry)
    with pytest.raises(RuntimeError, match="probe reached"):
        main(["doctorx"])
    assert seen["path"] == Path("doctorx")
