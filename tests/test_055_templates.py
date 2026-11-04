"""055 Task 5: onboarding templates."""

from __future__ import annotations

import subprocess

import pytest

from agentic_dev_loop.engine.onboarding.templates import (
    TemplateError,
    load_template,
    render_dev_template,
)

NAMES = [
    "dev.agent.md",
    "qa.agent.md",
    "review.agent.md",
    "trigger-agentic-dev-loop.sh",
    "env.example",
]


def test_all_templates_load():
    for name in NAMES:
        assert load_template(name).strip()


def test_unknown_template_raises():
    with pytest.raises(TemplateError):
        load_template("nope.md")


def test_dev_template_has_both_tokens_and_headings():
    text = load_template("dev.agent.md")
    assert "{{IMPOSSIBLE_SET}}" in text
    assert "{{SANDBOX_NOTES}}" in text
    assert "SANDBOX NOTES" in text.upper()
    assert "IMPOSSIBLE SET" in text.upper()


def test_dev_token_lines_replace_section_content_without_duplication():
    rendered = render_dev_template(
        load_template("dev.agent.md"),
        impossible_set=["package fetches outside restore", "database tests"],
        sandbox_notes=["your mount is read-write", "network is off"],
    )
    assert "{{IMPOSSIBLE_SET}}" not in rendered
    assert "{{SANDBOX_NOTES}}" not in rendered
    assert "- package fetches outside restore" in rendered
    assert "- network is off" in rendered
    assert rendered.count("IMPOSSIBLE SET") == 1
    assert rendered.count("SANDBOX NOTES") == 1


def test_dev_template_missing_heading_reports_actual_headings():
    with pytest.raises(TemplateError) as exc:
        render_dev_template("# Dev\n## WORKFLOW\n{{SANDBOX_NOTES}}\n",
                            impossible_set=[], sandbox_notes=[])
    message = str(exc.value)
    assert "WORKFLOW" in message


def test_trigger_template_contract():
    text = load_template("trigger-agentic-dev-loop.sh")
    assert "doctor --fast" in text
    assert "agentic-dev-loop upgrade" in text
    assert "pipx install --force" not in text
    assert "pipx upgrade" not in text
    assert "AGENTIC_DEV_LOOP_ENV_FILE" in text
    assert "exec agentic-dev-loop" in text


def test_trigger_template_is_valid_bash(tmp_path):
    path = tmp_path / "trigger.sh"
    path.write_text(load_template("trigger-agentic-dev-loop.sh"),
                    encoding="utf-8")
    result = subprocess.run(["bash", "-n", str(path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
