"""Brief prompt embedding (046): the OUTPUT contract renders from the
configured section headings, so the prompt and the coverage gate share one
list and cannot drift apart (the 043 divergence class)."""

from agentic_dev_loop.engine.brief_prompt import (
    BRIEF_PROMPT_VERSION,
    load_template,
    render_brief_prompt,
)

SECTIONS = ["Repo rules digest", "Unit scope digest",
            "Environment facts", "Open risks"]


class TestRenderBriefPrompt:
    def test_each_configured_section_is_its_own_heading(self):
        prompt = render_brief_prompt(SECTIONS)
        lines = prompt.splitlines()
        for section in SECTIONS:
            assert f"## {section}" in lines

    def test_custom_sections_rendered(self):
        prompt = render_brief_prompt(["Rules", "Scope", "Risks"])
        lines = prompt.splitlines()
        assert "## Rules" in lines
        assert "## Scope" in lines
        assert "## Risks" in lines
        assert "## Repo rules digest" not in lines

    def test_warning_lists_configured_names(self):
        prompt = render_brief_prompt(["Rules", "Scope", "Risks"])
        assert "`## Rules`" in prompt
        assert "`## Scope`" in prompt
        assert "`## Risks`" in prompt
        assert "Do not shorten or rename them" in prompt

    def test_static_template_loads(self):
        template = load_template()
        assert "## WORKFLOW" in template
        assert "## RULES" in template
        assert "Do not use em dash characters" in template

    def test_version_is_stable_string(self):
        assert isinstance(BRIEF_PROMPT_VERSION, str)
        assert BRIEF_PROMPT_VERSION
