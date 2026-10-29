"""Agent-facing error guide (034).

The guide is an engine-owned bundled resource injected into every agent
step's task context. These tests pin the injection point, the one-line-per-code
table shape, the consumer-agnostic ASCII content, and the pairing between the
guide and the agent-visible ``SandboxError`` codes emitted by the engine.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from openhands.sdk import LLM

from agentic_dev_loop.core.config import PipelineConfig
from agentic_dev_loop.engine.agent_error_guide import (
    GUIDE_HEADING,
    GUIDE_RESOURCE,
    guide_section,
    load_guide,
)
from agentic_dev_loop.engine.registry import default_registry
from agentic_dev_loop.engine.stages import (
    Runtime,
    build_brief_context,
    build_context,
    make_agent_node,
    make_command_node,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ISOLATION = REPO_ROOT / "src" / "agentic_dev_loop" / "engine" / "isolation.py"

# The source-of-truth agent-visible codes. AE-SBX-012/013/017/019 are emitted
# from the same Sandbox.run surface but are internal: sticky infra failures
# (012/019) stop the run before the agent can respond, and 013/017 are
# engine-invariant/config errors that never describe a command the agent can
# correct.
AGENT_VISIBLE_CODES = {"AE-SBX-008", "AE-SBX-010", "AE-SBX-016",
                        "AE-SBX-018", "AE-SBX-020"}
INTERNAL_SANDBOX_CODES = {"AE-SBX-012", "AE-SBX-013", "AE-SBX-017", "AE-SBX-019",
                            "AE-SBX-021"}

_CODE_RE = re.compile(r"AE-SBX-\d{3}")


def make_cfg(steps, **over):
    base = {"name": "t", "llm": {"model": "m", "api_key_env": "K"},
            "agents_dir": "agents", "lockfiles": ["uv.lock"], "steps": steps}
    base.update(over)
    return PipelineConfig.model_validate(base)


def make_state(**over):
    state = {"subject": "plan plan.md", "docs": {"plan.md": "THE PLAN"},
             "artifacts": {}, "attempt": 1, "max_attempts": 3, "notes": [],
             "failed": False, "outcome": None, "commit_allowed": False,
             "step_verdicts": {}, "retry_target": None, "thread_id": "t1"}
    state.update(over)
    return state


@pytest.fixture
def runtime(tmp_path, fake_sandbox):
    state_dir = tmp_path / ".pr"
    state_dir.mkdir()
    cfg = make_cfg([{"name": "dev", "agent": "dev",
                     "produces": ["implementation-summary.md"],
                     "on_pass": "success"}])
    return Runtime(cfg=cfg, registry=default_registry(),
                   llm=LLM(model="openai/test-model", api_key="sk-test"),
                   agents_dir=tmp_path / "agents", state_dir=state_dir,
                   workspace=tmp_path, sandbox=fake_sandbox)


def _guide_codes(text: str) -> list[str]:
    codes = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if cells and _CODE_RE.fullmatch(cells[0]):
            codes.append(cells[0])
    return codes


def _agent_path_error_codes() -> set[str]:
    """Every AE-SBX code raised on the Sandbox.run surface an agent terminal
    action can reach, parsed from the engine source."""
    tree = ast.parse(ISOLATION.read_text(encoding="utf-8"), filename=str(ISOLATION))
    target_nodes: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "_guard_git":
                target_nodes.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "DockerBackend":
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if child.name in {"run", "_run_legacy", "_run_profile",
                                      "_exec", "_start_profile",
                                      "_start_proxy_container", "_sync_back"}:
                        target_nodes.append(child)

    found: set[str] = set()
    for node in target_nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Raise) and isinstance(child.exc, ast.Call):
                fn = child.exc.func
                if isinstance(fn, ast.Name) and fn.id == "SandboxError":
                    for part in ast.walk(child.exc):
                        if isinstance(part, ast.Constant) and isinstance(part.value, str):
                            found.update(_CODE_RE.findall(part.value))
    return found


class TestBundledGuide:
    def test_loads_from_package_resource(self):
        from importlib.resources import files

        expected = (files("agentic_dev_loop.engine")
                    .joinpath(GUIDE_RESOURCE)
                    .read_text(encoding="utf-8"))
        assert load_guide() == expected
        assert "AE-SBX-016" in load_guide()

    def test_section_wraps_table(self):
        section = guide_section()
        assert section.startswith(f"## {GUIDE_HEADING}\n\n")
        assert "AE-SBX-016" in section

    def test_ascii_and_consumer_agnostic(self):
        text = load_guide()
        assert text.isascii()
        assert "consumer" not in text.lower()

    def test_every_visible_code_has_exactly_one_line(self):
        codes = _guide_codes(load_guide())
        assert Counter(codes) == Counter(AGENT_VISIBLE_CODES)

    def test_engine_agent_path_codes_pair_with_guide(self):
        visible = _agent_path_error_codes() - INTERNAL_SANDBOX_CODES
        assert visible == AGENT_VISIBLE_CODES


class TestInjection:
    def test_agent_context_contains_guide_before_docs(self, runtime):
        ctx = build_context(runtime, {"plan.md": "THE PLAN"}, "plan plan.md")
        assert f"## {GUIDE_HEADING}" in ctx
        assert "AE-SBX-016" in ctx
        assert ctx.index(f"## {GUIDE_HEADING}") < ctx.index("## plan.md")

    def test_brief_context_contains_guide_before_docs(self, runtime):
        (runtime.workspace / "AGENTS.md").write_text("REPO RULES")
        step = make_cfg([{
            "name": "brief", "agent": "brief", "produces": "attempt-brief.md",
            "brief_inputs": {"include": ["AGENTS.md"],
                             "env_facts": ["offline"]},
            "on_pass": "success",
        }]).steps[0]
        ctx = build_brief_context(runtime, step, {"plan.md": "THE PLAN"})
        assert f"## {GUIDE_HEADING}" in ctx
        assert "AE-SBX-016" in ctx
        assert ctx.index(f"## {GUIDE_HEADING}") < ctx.index("## plan.md")

    def test_agent_node_message_contains_guide(self, runtime, fake_agents):
        fake_agents.set("dev", [("respond", "done")])
        make_agent_node(runtime, runtime.cfg.steps[0])(make_state())
        (message,) = fake_agents.messages_for("dev")
        assert f"## {GUIDE_HEADING}" in message
        assert "AE-SBX-016" in message

    def test_command_step_notes_do_not_gain_guide(self, runtime):
        cfg = make_cfg([
            {"name": "dev", "agent": "dev", "on_pass": "test"},
            {"name": "test", "command": "false", "on_pass": "success",
             "on_fail": {"goto": "dev", "retry": True}},
        ])
        rt = replace(runtime, cfg=cfg)
        update = make_command_node(rt, cfg.steps[1])(make_state())
        assert "notes" in update
        assert all(f"## {GUIDE_HEADING}" not in note for note in update["notes"])
