"""Brief coverage gate (041): anchor extraction and the coverage check over
the captured brief, plus the brief node's bounded retry and failure path."""

from pathlib import Path

import pytest

from openhands.sdk import LLM

from agentic_dev_loop.core.config import PipelineConfig
from agentic_dev_loop.engine.brief_coverage import (
    extract_anchors,
    missing_anchors,
    required_sections,
)
from agentic_dev_loop.engine.registry import default_registry
from agentic_dev_loop.engine.stages import (
    Runtime,
    brief_inputs_hash,
    make_brief_node,
)

ROOT = Path(__file__).resolve().parents[1]
AGENTS_MD = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
STANDARDS_YAML = (ROOT / "coding-standards-ddd.yaml").read_text(encoding="utf-8")


def _brief_ok() -> str:
    return (
        "## Repo rules digest\n\n"
        "**Error handling**\nNever fall back silently.\n\n"
        "**Mock policy**\nNo mocks unless unavoidable.\n\n"
        "## Unit scope and lane digest\n\nunit scope\n\n"
        "## Environment facts\n\n- offline; never fetch\n\n"
        "## Open risks\n\nNone\n"
    )


def _brief_missing_mock() -> str:
    return _brief_ok().replace("**Mock policy**\nNo mocks unless unavoidable.\n\n", "")


def _merge_state(state, update):
    merged = dict(state)
    for key, value in update.items():
        if key in ("artifacts", "brief_hashes", "brief_retries"):
            merged[key] = {**state.get(key, {}), **value}
        else:
            merged[key] = value
    return merged


def _make_cfg(steps):
    return PipelineConfig.model_validate({
        "name": "t",
        "llm": {"model": "m", "api_key_env": "K"},
        "agents_dir": "agents",
        "lockfiles": ["uv.lock"],
        "steps": steps,
    })


def _make_state(**over):
    state = {"subject": "unit u", "docs": {"plan.md": "THE PLAN"},
             "artifacts": {}, "attempt": 1, "max_attempts": 10, "notes": [],
             "failed": False, "step_verdicts": {}, "retry_target": None,
             "thread_id": "t1", "brief_hashes": {}, "brief_retries": {}}
    state.update(over)
    return state


@pytest.fixture
def runtime(tmp_path, fake_sandbox):
    state_dir = tmp_path / ".pr"
    state_dir.mkdir()
    (tmp_path / "AGENTS.md").write_text(
        "## Error handling\n\nNever fall back silently.\n\n"
        "## Mock policy\n\nNo mocks unless unavoidable.\n")
    steps = [
        {"name": "brief", "agent": "brief", "produces": "attempt-brief.md",
         "max_iterations": 12,
         "brief_inputs": {"include": ["AGENTS.md"],
                          "env_facts": ["offline; never fetch"]},
         "on_pass": "dev"},
        {"name": "dev", "agent": "dev", "produces": "implementation-summary.md",
         "on_pass": "success"},
    ]
    return Runtime(cfg=_make_cfg(steps), registry=default_registry(),
                   llm=LLM(model="openai/test-model", api_key="sk-test"),
                   agents_dir=tmp_path / "agents", state_dir=state_dir,
                   workspace=tmp_path, sandbox=fake_sandbox)


class TestExtractAnchors:
    def test_required_sections_are_the_four_template_headings(self):
        assert required_sections() == [
            "Repo rules digest",
            "Unit scope and lane digest",
            "Environment facts",
            "Open risks",
        ]

    def test_markdown_headings_from_real_agents_md(self):
        anchors = extract_anchors(AGENTS_MD)
        assert anchors == [
            "default development mode - ask before coding",
            "slash commands",
            "github urls",
            "file search",
            "group session mode",
            "error handling",
            "em dash ban",
            "consumer name ban",
            "debugging",
            "reverse engineering undocumented systems",
            "development workflow",
            "backward compatibility",
            "mock policy",
            "sdk compat shims",
        ]

    def test_yaml_categories_and_enforcement_levels_from_real_file(self):
        anchors = extract_anchors(STANDARDS_YAML)
        assert anchors == [
            "enforcement_levels",
            "general_principles",
            "architecture_and_boundaries",
            "domain_and_persistence",
            "error_handling",
            "coding_style",
            "services_are_exceptions",
            "stack_conventions",
            "forbidden_patterns",
        ]

    def test_plain_file_has_no_anchors(self):
        assert extract_anchors("just prose\nno headings here\n") == []


class TestMissingAnchors:
    def test_all_present_passes(self):
        assert missing_anchors(_brief_ok(),
                               ["error handling", "mock policy"],
                               ["offline; never fetch"]) == []

    def test_missing_anchor_is_named(self):
        assert "mock policy" in missing_anchors(
            _brief_missing_mock(), ["error handling", "mock policy"],
            ["offline; never fetch"])

    def test_missing_section_heading_is_named(self):
        text = _brief_ok().replace("## Open risks\n\nNone\n", "")
        assert "Open risks" in missing_anchors(
            text, ["error handling", "mock policy"], ["offline; never fetch"])

    def test_altered_env_fact_is_named(self):
        text = _brief_ok().replace("offline; never fetch", "offline maybe")
        assert "offline; never fetch" in missing_anchors(
            text, ["error handling", "mock policy"], ["offline; never fetch"])

    def test_mid_line_mention_does_not_satisfy(self):
        text = _brief_ok().replace(
            "**Error handling**\nNever fall back silently.\n\n",
            "See error handling for the rules.\n\n")
        assert "error handling" in missing_anchors(
            text, ["error handling", "mock policy"], ["offline; never fetch"])

    def test_case_and_whitespace_variants_pass(self):
        text = _brief_ok().replace(
            "**Error handling**", "**  ERROR   HANDLING  **")
        assert missing_anchors(text, ["error handling", "mock policy"],
                               ["offline; never fetch"]) == []

    def test_bold_wrapped_heading_passes(self):
        assert missing_anchors(_brief_ok(), ["error handling", "mock policy"],
                               ["offline; never fetch"]) == []


class TestBriefNodeCoverage:
    def test_miss_returns_retry_note_and_reruns(self, runtime, fake_agents):
        good = _brief_ok()
        bad = _brief_missing_mock()
        fake_agents.set("brief", [
            ("write", "attempt-brief.md", bad),
            ("write", "attempt-brief.md", good),
        ])
        node = make_brief_node(runtime, runtime.cfg.steps[0])
        state = _make_state()

        first = node(state)
        assert first["step_verdicts"] == {"brief": None}
        assert first["retry_target"] == "brief"
        assert first["brief_retries"] == {"brief": 1}
        assert first["artifacts"] == {"brief": bad}
        assert first["brief_hashes"] == {
            "brief": brief_inputs_hash(runtime, runtime.cfg.steps[0],
                                       state["docs"])}

        second = node(_merge_state(state, first))
        assert second["step_verdicts"] == {"brief": "pass"}
        assert second["failed"] is False
        assert second["artifacts"] == {"brief": good}
        assert second["brief_retries"] == {"brief": 0}
        assert fake_agents.agents_called() == ["brief", "brief"]
        messages = fake_agents.messages_for("brief")
        assert "## Coverage retry" in messages[1]
        assert "mock policy" in messages[1]

    def test_cap_exhausted_writes_last_error_and_fails(self, runtime,
                                                       fake_agents):
        bad = _brief_missing_mock()
        fake_agents.set("brief", [("write", "attempt-brief.md", bad)])
        node = make_brief_node(runtime, runtime.cfg.steps[0])
        state = _make_state()

        for _ in range(3):
            update = node(state)
            assert update.get("failed") is not True
            state = _merge_state(state, update)

        update = node(state)
        assert update["failed"] is True
        assert update["failed_step"] == "brief"
        assert fake_agents.agents_called() == ["brief", "brief", "brief", "brief"]
        error = (runtime.state_dir / "last-error.md").read_text(encoding="utf-8")
        assert "Brief coverage failed" in error
        assert "mock policy" in error

    def test_reused_digest_failing_coverage_regenerates(self, runtime,
                                                        fake_agents):
        good = _brief_ok()
        bad = _brief_missing_mock()
        fake_agents.set("brief", [("write", "attempt-brief.md", good)])
        node = make_brief_node(runtime, runtime.cfg.steps[0])
        state = _make_state(artifacts={"brief": bad},
                            brief_hashes={"brief": brief_inputs_hash(
                                runtime, runtime.cfg.steps[0],
                                _make_state()["docs"])})
        update = node(state)
        assert update["artifacts"] == {"brief": good}
        assert fake_agents.agents_called() == ["brief"]
        (message,) = fake_agents.messages_for("brief")
        assert "## Coverage retry" in message

    def test_passing_reused_digest_skips_llm(self, runtime, fake_agents):
        good = _brief_ok()
        node = make_brief_node(runtime, runtime.cfg.steps[0])
        state = _make_state(artifacts={"brief": good},
                            brief_hashes={"brief": brief_inputs_hash(
                                runtime, runtime.cfg.steps[0],
                                _make_state()["docs"])})
        update = node(state)
        assert update == {"step_verdicts": {"brief": "pass"}, "failed": False}
        assert fake_agents.agents_called() == []
