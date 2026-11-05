"""Shared harness context and process posture (unit 10).

The subprocess harnesses keep the agent brain on the host, so they receive
the agent body plus the engine-wide context sections the OpenHands SDK agent
gets today. Pipeline secrets never enter their environment.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..agent_error_guide import guide_section
from ..deps import policy_injection
from .protocol import RuntimeContext

# Only these host variables may reach a harness child process (plus the
# harness's own credential, added by the driver).
_BASE_ENV_KEYS = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL",
                  "SYSTEMROOT", "COMSPEC", "PATHEXT")
_RUNS_IN_ENGINE_NOTE = (
    "You are running inside the agentic-dev-loop harness. Every command and "
    "file edit goes through the engine's sandbox bridge; native shell and "
    "file tools are disabled.")


def read_agent_body(agents_dir: Path, name: str) -> str:
    """The markdown body of `<agents_dir>/<name>.agent.md` (frontmatter
    stripped). Missing files fail fast, exactly like the SDK loader."""
    path = Path(agents_dir) / f"{name}.agent.md"
    if not path.is_file():
        raise FileNotFoundError(f"Agent file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    return text.lstrip("\n")


def engine_agent_context(ctx: RuntimeContext) -> str:
    """Agent body + the engine-wide sections every agent step receives."""
    body = ctx.agent_context or read_agent_body(ctx.agents_dir, ctx.agent_name)
    parts = [body.strip(), _RUNS_IN_ENGINE_NOTE, guide_section(),
             f"## Dependency policy\n\n{policy_injection(ctx.cfg)}"]
    if ctx.artifact is not None:
        parts.append(
            "## Expected output paths\n\n"
            f"The engine captures your final response to "
            f"`{ctx.state_dir / ctx.artifact}`; do not write it yourself.")
    return "\n\n".join(part for part in parts if part)


def minimal_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """An explicit minimal harness environment (never the engine's full env)."""
    env = {key: os.environ[key] for key in _BASE_ENV_KEYS if key in os.environ}
    if extra:
        env.update(extra)
    return env
