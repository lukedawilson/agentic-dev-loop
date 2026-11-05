"""The engine-owned tool catalog (unit 10).

One name vocabulary for every provider. `.agent.md` frontmatter names these;
OpenHands binds them to its SDK tools, while the subprocess harnesses receive
exactly the three MCP-exposed tools. `finish` stays harness-native and is not
exposed over MCP.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogToolSpec:
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


TERMINAL = CatalogToolSpec(
    name="terminal",
    description="Execute a shell command inside the engine sandbox.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command to run."},
            "timeout": {"type": "number", "description": "Seconds, optional."},
        },
        "required": ["command"],
    },
)

FILE_EDITOR = CatalogToolSpec(
    name="file_editor",
    description="Read, write, or edit a file inside the step workspace.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": ["view", "create", "str_replace", "insert"],
            },
            "path": {"type": "string"},
            "file_text": {"type": "string"},
            "old_str": {"type": "string"},
            "new_str": {"type": "string"},
            "insert_line": {"type": "integer"},
        },
        "required": ["command", "path"],
    },
)

TASK_TRACKER = CatalogToolSpec(
    name="task_tracker",
    description="Read or update the per-run in-memory task list.",
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": ["view", "plan"],
            },
            "task_list": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["command"],
    },
)

FINISH = CatalogToolSpec(
    name="finish",
    description="Signal that the run is complete (harness-native).",
    input_schema={
        "type": "object",
        "properties": {"message": {"type": "string"}},
    },
)

CATALOG: dict[str, CatalogToolSpec] = {
    spec.name: spec for spec in (TERMINAL, FILE_EDITOR, TASK_TRACKER, FINISH)
}

# The MCP surface: exactly the three execution tools. `finish` is
# harness-native (claude stream end / copilot turn end).
MCP_TOOL_NAMES: tuple[str, ...] = ("terminal", "file_editor", "task_tracker")
MCP_TOOLS: tuple[CatalogToolSpec, ...] = tuple(CATALOG[n] for n in MCP_TOOL_NAMES)
