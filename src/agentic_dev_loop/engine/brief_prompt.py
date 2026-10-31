"""Bundled brief-agent prompt (046).

The brief prompt is an engine-owned resource, versioned with the engine so the
prompt contract and the coverage gate cannot drift (the 043 divergence class).
The static prose is packaged and read at runtime like the 034 error guide; the
section-dependent OUTPUT block is rendered here from the configured
``brief_inputs.sections`` list, so one list feeds the prompt and the gate.

``BRIEF_PROMPT_VERSION`` joins the brief digest hash: an engine upgrade (or a
sections edit) busts a cached digest and forces a fresh capture.
"""

from __future__ import annotations

from importlib.resources import files

BRIEF_PROMPT_RESOURCE = "brief_prompt.md"
BRIEF_PROMPT_VERSION = "1"


def load_template() -> str:
    """The static brief-prompt prose (header, workflow, rules)."""
    return (files("agentic_dev_loop.engine")
            .joinpath(BRIEF_PROMPT_RESOURCE)
            .read_text(encoding="utf-8"))


def _output_block(sections: list[str]) -> str:
    """Render the OUTPUT contract from the configured section headings."""
    headings = "\n\n".join(f"## {section}" for section in sections)
    names = ", ".join(f"`## {section}`" for section in sections)
    return (
        "## OUTPUT\n\n"
        "Start your final response with exactly this structure. Each configured "
        "section heading appears as its own `## ` line, followed by the "
        "compressed content for that section (write `None` when a section has "
        "nothing):\n\n"
        f"{headings}\n\n"
        f"The section headings must appear exactly as written above: {names}. "
        "Do not shorten or rename them, and do not merge a section with "
        "another section."
    )


def render_brief_prompt(sections: list[str]) -> str:
    """The full brief prompt: static prose plus the section-rendered OUTPUT."""
    return load_template() + "\n\n" + _output_block(sections)
