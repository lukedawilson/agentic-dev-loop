"""055 Task 6: init renders and writes a consumer pipeline."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import yaml

from agentic_dev_loop.core.config import load_config
from agentic_dev_loop.engine.onboarding.init import detect, run_init
from agentic_dev_loop.engine.stages import validate_brief_inputs


def _python_repo(root: Path) -> Path:
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'fixture'\nversion = '0.1.0'\n", encoding="utf-8")
    (root / "README.md").write_text("# Fixture\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
    return root


def _dotnet_repo(root: Path, conformance: bool = True) -> Path:
    (root / "src").mkdir()
    (root / "src" / "App.sln").write_text("\n", encoding="utf-8")
    (root / "frontend").mkdir()
    (root / "frontend" / "package.json").write_text(
        '{"name": "frontend"}\n', encoding="utf-8")
    if conformance:
        (root / "tools").mkdir()
        (root / "tools" / "run-conformance.sh").write_text(
            "#!/bin/sh\nexit 0\n", encoding="utf-8")
    return root


def test_detect_python_repo(tmp_path: Path):
    _python_repo(tmp_path)
    result = detect(tmp_path)
    assert result.solutions == []
    assert result.frontend_dirs == []
    assert result.readme == "README.md"
    assert result.agents_md is True
    assert result.conformance is False


def test_detect_dotnet_repo_ignores_node_modules(tmp_path: Path):
    _dotnet_repo(tmp_path)
    nested = tmp_path / "frontend" / "node_modules" / "pkg"
    nested.mkdir(parents=True)
    (nested / "package.json").write_text("{}", encoding="utf-8")
    result = detect(tmp_path)
    assert result.solutions == ["src/App.sln"]
    assert result.frontend_dirs == ["frontend"]
    assert result.conformance is True


def test_python_init_renders_loadable_pipeline(tmp_path: Path):
    _python_repo(tmp_path)
    assert run_init(["--profile", "python", "--yes", "--dir", str(tmp_path)]) == 0
    path = tmp_path / "agentic-dev-loop" / "pipeline.yaml"
    cfg = load_config(path)
    steps = {s.name: s for s in cfg.steps}
    assert steps["dev"].setup == (
        'python -m venv .venv-sandbox && .venv-sandbox/bin/pip install -e '
        '".[dev]"')
    assert steps["test"].command == (
        '.venv-sandbox/bin/python -m pytest tests/ -q -m "not docker"')
    assert cfg.profile == "python"
    installed = importlib.metadata.version("agentic-dev-loop")
    assert (tmp_path / "agentic-dev-loop" / ".engine-version").read_text().strip() == installed
    assert not (tmp_path / "trigger-agentic-dev-loop.sh").exists()
    assert (tmp_path / "agentic-dev-loop" / "sdk_agents" / "dev.agent.md").is_file()


def test_dotnet_init_renders_nine_steps_and_facts(tmp_path: Path):
    _dotnet_repo(tmp_path)
    assert run_init(["--profile", "dotnet-node", "--yes",
                     "--dir", str(tmp_path)]) == 0
    path = tmp_path / "agentic-dev-loop" / "pipeline.yaml"
    raw = yaml.safe_load(path.read_text())
    names = [s["name"] for s in raw["steps"]]
    assert names == ["brief", "dev", "restore", "test", "editor-tests",
                     "conformance", "review", "qa", "commit"]
    steps = {s["name"]: s for s in raw["steps"]}
    assert steps["dev"]["setup"] == (
        'cd frontend && npm ci && cd .. && dotnet restore src/App.sln')
    assert steps["test"]["command"] == (
        'dotnet test src/App.sln --filter "Category!=Database"')
    assert steps["editor-tests"]["command"] == (
        'cd frontend && npm ci && npm test')
    assert raw["caches"] == ["qa/.browsers"]
    cfg = load_config(path)
    assert cfg.isolation.seccomp_profile == "bundled:chromium-userns"
    assert (tmp_path / "NuGet.Config").is_file()
    gitignore = (tmp_path / ".gitignore").read_text()
    for entry in ("bin/", "obj/", ".nuget-packages/", "qa/.browsers/"):
        assert entry in gitignore.splitlines()


def test_optional_gate_absent_reconnects_chain(tmp_path: Path):
    _dotnet_repo(tmp_path, conformance=False)
    assert run_init(["--profile", "dotnet-node", "--yes",
                     "--dir", str(tmp_path)]) == 0
    raw = yaml.safe_load(
        (tmp_path / "agentic-dev-loop" / "pipeline.yaml").read_text())
    steps = {s["name"]: s for s in raw["steps"]}
    assert "conformance" not in steps
    assert steps["editor-tests"]["on_pass"] == "review"


def test_unknown_profile_exits_one(tmp_path: Path):
    assert run_init(["--profile", "nope", "--yes", "--dir", str(tmp_path)]) == 1


def test_existing_pipeline_refuses(tmp_path: Path):
    (tmp_path / "agentic-dev-loop").mkdir()
    (tmp_path / "agentic-dev-loop" / "pipeline.yaml").write_text(
        "name: old\n", encoding="utf-8")
    assert run_init(["--profile", "python", "--yes", "--dir", str(tmp_path)]) == 1


def test_fresh_repo_without_agents_md_renders_valid_brief_inputs(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'fixture'\nversion = '0.1.0'\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "uv.lock").write_text("\n", encoding="utf-8")
    assert run_init(["--profile", "python", "--yes",
                     "--dir", str(tmp_path)]) == 0
    path = tmp_path / "agentic-dev-loop" / "pipeline.yaml"
    cfg = load_config(path)
    brief = next(step for step in cfg.steps if step.name == "brief")
    assert brief.brief_inputs is not None
    assert "AGENTS.md" not in brief.brief_inputs.include
    for step in cfg.steps:
        validate_brief_inputs(tmp_path, step)
