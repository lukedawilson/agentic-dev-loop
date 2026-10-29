"""Bundled agent-facing error guide (034).

The guide table is an engine-owned resource, versioned with the engine so the
codes and their guidance cannot drift. Every agent step's task context gets
the table injected at assembly time; command and action steps never do (no
agent reads them).
"""

from __future__ import annotations

from importlib.resources import files

GUIDE_RESOURCE = "agent_error_guide.md"
GUIDE_HEADING = "Agent-facing error guide"


def load_guide() -> str:
    """The bundled guide table text."""
    return (files("agentic_dev_loop.engine")
            .joinpath(GUIDE_RESOURCE)
            .read_text(encoding="utf-8"))


def guide_section() -> str:
    """The guide block injected into an agent step's task context."""
    return f"## {GUIDE_HEADING}\n\n{load_guide()}"
