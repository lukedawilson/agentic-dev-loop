"""Onboarding templates: agent prompts, the trigger script, env template."""

from __future__ import annotations

import re
from importlib import resources

__all__ = [
    "TemplateError",
    "load_template",
    "render_dev_template",
]

_HEADING_RE = re.compile(r"(?m)^#{1,6}[ \t]+(.+?)[ \t]*$")
_REQUIRED_HEADINGS = ("sandbox notes", "impossible set")


class TemplateError(ValueError):
    """Any template load or anchor failure."""


def _templates_dir():
    return resources.files(__package__).joinpath("templates")


def load_template(name: str) -> str:
    for entry in _templates_dir().iterdir():
        if entry.name == name:
            return entry.read_text(encoding="utf-8")
    names = ", ".join(sorted(entry.name for entry in _templates_dir().iterdir()))
    raise TemplateError(f"unknown template '{name}'; templates: {names}")


def _headings(text: str) -> list[str]:
    return [match.group(1).strip() for match in _HEADING_RE.finditer(text)]


def _bullet_block(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_dev_template(text: str, impossible_set: list[str],
                        sandbox_notes: list[str]) -> str:
    """Substitute the dev template's two token lines.

    The tokens sit under their sections. The headings are found by a
    case-insensitive search; a missing heading is a loud failure naming the
    headings that exist, never a guessed insertion point.
    """
    headings = _headings(text)
    lowered = [h.lower() for h in headings]
    missing = [h for h in _REQUIRED_HEADINGS if h not in lowered]
    if missing:
        raise TemplateError(
            "dev template is missing required heading(s): "
            + ", ".join(missing)
            + "; headings found: " + ", ".join(headings))
    for token in ("{{SANDBOX_NOTES}}", "{{IMPOSSIBLE_SET}}"):
        if token not in text:
            raise TemplateError(f"dev template is missing token {token}")
    rendered = text.replace("{{SANDBOX_NOTES}}", _bullet_block(sandbox_notes))
    return rendered.replace("{{IMPOSSIBLE_SET}}", _bullet_block(impossible_set))
