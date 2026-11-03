"""055 Task 4: engine-resident per-toolchain recipes."""

from __future__ import annotations

import pytest

from agentic_dev_loop.engine.onboarding.recipes import (
    RecipeError,
    bundled_recipe_names,
    load_recipe,
    undeclared_tokens,
)


def test_bundled_recipe_names():
    assert bundled_recipe_names() == ["dotnet-node", "python"]


def test_both_recipes_load_and_have_steps():
    for name in bundled_recipe_names():
        doc = load_recipe(name)
        assert doc["name"] == name
        assert isinstance(doc["steps"], list) and doc["steps"]
        assert isinstance(doc["placeholders"], dict)
        assert doc["steps"][-1]["kind"] == "action"


def test_every_token_is_declared():
    for name in bundled_recipe_names():
        assert undeclared_tokens(load_recipe(name)) == set()


def test_python_recipe_gate_set_and_commands():
    doc = load_recipe("python")
    steps = {s["name"]: s for s in doc["steps"]}
    assert list(steps) == ["brief", "dev", "restore", "test", "review", "qa",
                           "commit"]
    assert steps["dev"]["setup"] == (
        'python -m venv __VENV__ && __VENV__/bin/pip install -e '
        '"__DEV_EXTRAS__"')
    assert steps["test"]["command"] == (
        '__VENV__/bin/python -m pytest __TEST_PATH__ -q -m "__TEST_FILTER__"')
    assert steps["restore"]["network"] is True
    assert steps["restore"]["egress"] == {"presets": ["package-registries"]}
    assert steps["review"]["mounts"] == [{"path": ".", "mode": "ro"}]
    assert steps["qa"]["max_iterations"] == 150
    assert steps["qa"]["finalize_nudge_turns"] == 10
    assert "env" in steps["qa"]["verdicts"]
    assert "on_env" not in steps["qa"]
    for name in ("brief", "dev", "review"):
        assert steps[name]["forbidden_commands"] == ['pip install', 'uv add', 'pip-compile']
    assert "forbidden_commands" not in steps["qa"]


def test_dotnet_node_recipe_gate_set_and_commands():
    doc = load_recipe("dotnet-node")
    steps = {s["name"]: s for s in doc["steps"]}
    assert list(steps) == ["brief", "dev", "restore", "test", "editor-tests",
                           "conformance", "review", "qa", "commit"]
    assert doc["placeholders"]["__SOLUTION__"] == "DETECTED"
    assert doc["placeholders"]["__FRONTEND_DIR__"] == "DETECTED"
    assert doc["placeholders"]["__QA_FRONTEND_DIR__"] == "__FRONTEND_DIR__"
    assert steps["dev"]["setup"] == (
        'cd __FRONTEND_DIR__ && npm ci && cd .. && dotnet restore __SOLUTION__')
    assert steps["test"]["command"] == (
        'dotnet test __SOLUTION__ --filter "__FILTER__"')
    assert steps["editor-tests"]["command"] == (
        'cd __FRONTEND_DIR__ && npm ci && npm test')
    assert steps["conformance"]["optional"] == {
        "detect": "tools/run-conformance.sh"}
    assert steps["qa"]["egress"] == {"class": "browser"}
    assert steps["qa"]["env"] == {"PLAYWRIGHT_BROWSERS_PATH": "qa/.browsers"}
    assert steps["qa"]["mounts"] == [{"path": ".", "mode": "rw"}]
    assert len(doc["dev_impossible_set"]) == 4
    for name in ("brief", "dev", "review"):
        assert steps[name]["forbidden_commands"] == ['npm install', 'npm ci', 'dotnet add package', 'dotnet restore', 'dotnet add', 'npx playwright', 'playwright']
    assert "forbidden_commands" not in steps["qa"]


def test_unknown_recipe_raises():
    with pytest.raises(RecipeError):
        load_recipe("nope")


def test_undeclared_tokens_finds_nested_tokens():
    doc = {
        "placeholders": {"__A__": "x"},
        "steps": [{"command": "__A__ and __B__", "env": {"K": ["__C__"]}}],
    }
    assert undeclared_tokens(doc) == {"__B__", "__C__"}
