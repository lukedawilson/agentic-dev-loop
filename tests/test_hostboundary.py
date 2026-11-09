"""Host-boundary guard tests (020): the orchestrator install self-check, the
tooling-path set, and the run-start tooling integrity check. Real git repos
and real files throughout (git is a local utility, not a network boundary)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentic_dev_loop.core.config import PipelineConfig
from agentic_dev_loop.engine.hostboundary import (
    HostBoundaryError, check_orchestrator_install, check_tooling_integrity,
    tooling_rel_paths,
)


def _cfg(agents_dir="sdk_agents", additional_files=()):
    return PipelineConfig.model_validate({
        "name": "t",
        "llm": {"model": "m", "api_key_env": "K"},
        "agents_dir": agents_dir,
        "additional_files": list(additional_files),
        "lockfiles": ["uv.lock"],
        "steps": [{"name": "dev", "agent": "dev", "on_pass": "success"}],
    })


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout.strip()


def init_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    (repo / "pipeline.yaml").write_text("steps: []\n")
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "init")
    return repo


class TestOrchestratorInstall:
    def test_outside_workspace_is_allowed(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        check_orchestrator_install(ws, engine_file=tmp_path / "engine"
                                   / "agentic_dev_loop" / "__init__.py")

    def test_inside_workspace_is_refused_naming_path(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        engine = ws / ".venv" / "lib" / "agentic_dev_loop" / "__init__.py"
        with pytest.raises(HostBoundaryError) as excinfo:
            check_orchestrator_install(ws, engine_file=engine)
        message = str(excinfo.value)
        assert str(engine.resolve()) in message
        assert "pipx" in message

    def test_editable_src_install_refused(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        engine = ws / "src" / "agentic_dev_loop" / "__init__.py"
        with pytest.raises(HostBoundaryError):
            check_orchestrator_install(ws, engine_file=engine)


class TestToolingRelPaths:
    def test_collects_pipeline_agents_additional_and_fixed_dirs(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = _cfg(agents_dir="agents", additional_files=["shared/spec.yaml"])
        base = ws / "agentic-dev-loop"
        pipeline = base / "pipeline.yaml"
        rels = tooling_rel_paths(cfg, pipeline, base, ws)
        assert rels == [
            "agentic-dev-loop/.engine-version",
            "agentic-dev-loop/agents",
            "agentic-dev-loop/pipeline.yaml",
            "agentic-dev-loop/shared/spec.yaml",
            "aidlc-docs",
            "tools",
        ]

    def test_engine_pin_is_tooling(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = _cfg()
        rels = tooling_rel_paths(cfg, ws / "pipeline.yaml", ws, ws)
        assert "agentic-dev-loop/.engine-version" in rels
        assert "trigger-agentic-dev-loop.sh" not in rels

    def test_outside_workspace_paths_are_excluded(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = _cfg(additional_files=["../README.md"])
        rels = tooling_rel_paths(cfg, ws / "pipeline.yaml", ws, ws)
        assert "../README.md" not in rels
        assert "pipeline.yaml" in rels


class TestToolingIntegrity:
    def test_clean_tooling_passes(self, tmp_path):
        repo = init_repo(tmp_path)
        cfg = _cfg()
        check_tooling_integrity(cfg, repo / "pipeline.yaml", repo, repo)

    def test_modified_pipeline_refused_naming_file(self, tmp_path):
        repo = init_repo(tmp_path)
        (repo / "pipeline.yaml").write_text("name: changed\n")
        cfg = _cfg()
        with pytest.raises(HostBoundaryError) as excinfo:
            check_tooling_integrity(cfg, repo / "pipeline.yaml", repo, repo)
        assert "pipeline.yaml" in str(excinfo.value)

    def test_modified_tools_script_refused(self, tmp_path):
        repo = init_repo(tmp_path)
        tools = repo / "tools"
        tools.mkdir()
        (tools / "run-conformance.sh").write_text("#!/bin/sh\ntrue\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "add tools")
        (tools / "run-conformance.sh").write_text("#!/bin/sh\nfalse\n")
        cfg = _cfg()
        with pytest.raises(HostBoundaryError) as excinfo:
            check_tooling_integrity(cfg, repo / "pipeline.yaml", repo, repo)
        assert "tools/run-conformance.sh" in str(excinfo.value)

    def test_untracked_tooling_refused(self, tmp_path):
        repo = init_repo(tmp_path)
        (repo / "tools").mkdir()
        (repo / "tools" / "new-gate.sh").write_text("#!/bin/sh\ntrue\n")
        cfg = _cfg()
        with pytest.raises(HostBoundaryError) as excinfo:
            check_tooling_integrity(cfg, repo / "pipeline.yaml", repo, repo)
        assert "tools/new-gate.sh" in str(excinfo.value)

    def test_non_git_workspace_is_skipped(self, tmp_path):
        ws = tmp_path / "plain"
        ws.mkdir()
        (ws / "pipeline.yaml").write_text("steps: []\n")
        cfg = _cfg()
        check_tooling_integrity(cfg, ws / "pipeline.yaml", ws, ws)  # must not raise
