"""--dry-run tests: the full load-and-validate pass with zero execution.

All tests run real files in tmp dirs (no mocks - the SDK network boundary is
never touched, because a dry run builds no agent and makes no LLM call). The
valid path therefore also works with OPENAI_API_KEY stripped from the
subprocess environment.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentic_dev_loop.engine.cli import main
from agentic_dev_loop.engine.isolation import DEFAULT_DEV_IMAGE_REF

SRC = str(Path(__file__).resolve().parents[1] / "src")

DRY_RUN_YAML = """\
name: dry-run-construction
llm:
  model: openai/deepseek-v4-pro
  base_url: https://api.deepseek.com
  api_key_env: OPENAI_API_KEY
state_dir: .pr
agents_dir: sdk_agents
deps_policy: open
steps:
  - name: dev
    agent: dev
    produces: implementation-summary.md
    on_pass: test
  - name: test
    command: .venv-sandbox/bin/python -m pytest tests/ -q
    timeout: 600
    on_pass: review
  - name: review
    agent: review
    artifact: review-findings.md
    verdicts: { pass: PASS, fail: FAIL }
    on_pass: success
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pipeline.yaml").write_text(DRY_RUN_YAML)
    (repo / "plan.md").write_text("THE PLAN")
    agents = repo / "sdk_agents"
    agents.mkdir()
    (agents / "dev.agent.md").write_text(
        "---\nname: dev\ntools: [terminal]\n---\nYou are the dev agent.\n")
    (agents / "review.agent.md").write_text(
        "---\nname: review\ntools: [terminal]\n---\nYou are the review agent.\n")
    monkeypatch.chdir(repo)
    monkeypatch.delenv("AGENTIC_DEV_LOOP_DEV_IMAGE", raising=False)
    return repo


def run_cli(repo: Path, *args: str, strip=("AGENTIC_DEV_LOOP_DEV_IMAGE",)):
    env = {k: v for k, v in os.environ.items() if k not in strip}
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c",
         "import sys; from agentic_dev_loop.engine.cli import main; "
         "sys.exit(main(sys.argv[1:]))",
         *args],
        cwd=repo, capture_output=True, text=True, env=env)


class TestDryRun:
    def test_valid_pipeline_prints_summary_and_exits_zero(self, repo, capsys):
        assert main(["pipeline.yaml", "--dry-run", "--plan", "plan.md"]) == 0
        out = capsys.readouterr().out
        assert "Subject: plan plan.md" in out
        assert "Document loader: plan" in out
        assert "dev [agent]" in out
        assert "test [command]" in out
        assert "review [agent]" in out
        assert "backend: docker" in out
        assert DEFAULT_DEV_IMAGE_REF in out
        assert "dry run complete" in out

    def test_no_state_files_created(self, repo):
        assert main(["pipeline.yaml", "--dry-run", "--plan", "plan.md"]) == 0
        assert not (repo / ".pr").exists()
        assert not list(repo.glob("**/*.sqlite*"))

    def test_works_with_openai_key_stripped(self, repo):
        result = run_cli(repo, "pipeline.yaml", "--dry-run", "--plan", "plan.md",
                         strip=("AGENTIC_DEV_LOOP_DEV_IMAGE", "OPENAI_API_KEY"))
        assert result.returncode == 0, result.stderr
        assert "dry run complete" in result.stdout

    def test_composes_with_irrelevant_flags(self, repo):
        assert main(["pipeline.yaml", "--dry-run", "--plan", "plan.md",
                     "--no-viz", "--resume", "some-thread-id",
                     "--max-attempts", "5"]) == 0

    def test_network_step_surfaced_in_summary(self, repo, capsys):
        (repo / "pipeline.yaml").write_text(
            DRY_RUN_YAML.replace(
                "    command: .venv-sandbox/bin/python -m pytest tests/ -q\n",
                "    command: .venv-sandbox/bin/python -m pytest tests/ -q\n"
                "    network: true\n"))
        assert main(["pipeline.yaml", "--dry-run", "--plan", "plan.md"]) == 0
        out = capsys.readouterr().out
        assert "test [command]" in out
        assert "network=True" in out

    def test_sandbox_action_passes_dry_run(self, repo, capsys):
        """020: a consumer (sandbox) action is a registered action for the
        dry-run validation - `unknown action` must not be raised for it."""
        (repo / "action.yaml").write_text(
            "extensions:\n  - sample_ext:SampleExtension\n"
            + DRY_RUN_YAML.replace(
                "    on_pass: success\n",
                "    on_pass: success\n")
            .replace(
                "steps:\n",
                "steps:\n"
                "  - name: lint\n"
                "    action: sample_action\n"
                "    network: true\n"
                "    on_pass: dev\n")
        )
        assert main(["action.yaml", "--dry-run", "--plan", "plan.md"]) == 0
        out = capsys.readouterr().out
        assert "lint [action]" in out
        assert "network=True" in out

    def test_host_side_action_rejects_network_in_dry_run(self, repo):
        (repo / "hostnet.yaml").write_text(
            DRY_RUN_YAML.replace(
                "    on_pass: success\n",
                "    on_pass: success\n")
            .replace(
                "steps:\n",
                "steps:\n"
                "  - name: commit\n"
                "    action: commit\n"
                "    network: true\n"
                "    params: { message: \"x {subject}\" }\n"
                "    on_pass: success\n")
        )
        result = run_cli(repo, "hostnet.yaml", "--dry-run")
        assert result.returncode == 1
        assert "host-side" in result.stderr

    def test_tool_extension_loads_once(self, repo, capsys):
        """A tool-registering extension must survive the CLI's two-phase load:
        build_parser loads extensions once for loader-contributed flags, and
        the dry run reuses that registry instead of loading again (a second
        load collides with the SDK's process-global tool registry and BR-3
        rejects it - the pre-fix double-load crash)."""
        (repo / "tool.yaml").write_text(
            "extensions:\n  - sample_ext:DryRunProbeExtension\n" + DRY_RUN_YAML)
        assert main(["tool.yaml", "--dry-run", "--plan", "plan.md"]) == 0
        assert "dry run complete" in capsys.readouterr().out


class TestDryRunFailures:
    def test_invalid_yaml(self, repo):
        (repo / "bad.yaml").write_text("steps: [unclosed\n")
        result = run_cli(repo, "bad.yaml", "--dry-run")
        assert result.returncode == 1
        assert result.stderr.strip()

    def test_missing_agent_file(self, repo):
        (repo / "sdk_agents" / "dev.agent.md").unlink()
        result = run_cli(repo, "pipeline.yaml", "--dry-run")
        assert result.returncode == 1
        assert "Agent file not found" in result.stderr

    def test_unknown_route_target(self, repo):
        (repo / "route.yaml").write_text(
            DRY_RUN_YAML.replace("on_pass: test", "on_pass: nope"))
        result = run_cli(repo, "route.yaml", "--dry-run")
        assert result.returncode == 1
        assert "unknown route target 'nope'" in result.stderr

    def test_empty_isolation_image_fails_fast(self, repo):
        (repo / "image.yaml").write_text(
            DRY_RUN_YAML + "\nisolation:\n  image: \"\"\n")
        result = run_cli(repo, "image.yaml", "--dry-run")
        assert result.returncode == 1
        assert "AE-SBX-005" in result.stderr
