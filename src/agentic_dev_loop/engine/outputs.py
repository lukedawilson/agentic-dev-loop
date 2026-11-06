"""Captured agent output: the value object that owns the response→artifact
channel (unit-1).

The pipeline - not the agent - writes the step's artifact from the agent's
final SDK response. Verdict parsing lives in `core.verdict`; this module only
owns the SDK-side capture. Expected conditions are typed `Result` errors,
never exceptions.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

from openhands.sdk.conversation.response_utils import get_agent_final_response

from agentic_dev_loop.core.results import Result
from .secrets import redact_values


class CaptureError(enum.Enum):
    EMPTY_RESPONSE = "empty_response"
    IO_ERROR = "io_error"


@dataclass(frozen=True)
class CapturedOutput:
    """The agent's final response, captured by the pipeline (FR-1.2)."""

    raw_text: str
    artifact_path: Path

    @staticmethod
    def from_agent_result(
        conv, artifact: str, state_dir: Path,
        redactions: tuple[str, ...] = ()
    ) -> "Result[CapturedOutput, CaptureError]":
        text = _final_agent_text(conv)
        if not text.strip():
            return Result.err(CaptureError.EMPTY_RESPONSE)
        text = redact_values(text, redactions)
        return Result.ok(CapturedOutput(raw_text=text, artifact_path=state_dir / artifact))

    def write(self) -> Result[None, CaptureError]:
        """Write the captured response to the step's artifact path (BR-4/BR-5)."""
        try:
            self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
            self.artifact_path.write_text(self.raw_text, encoding="utf-8")
            return Result.ok(None)
        except OSError:
            return Result.err(CaptureError.IO_ERROR)


def _final_agent_text(conv) -> str:
    """Extract the agent's final response from an SDK conversation.

    Delegates to the SDK's own `get_agent_final_response`, which handles both
    ways an agent can end a run - a `FinishTool` call or a plain-text message.
    The events live on ``conv.state.events`` (NOT ``conv.events``), and must be
    read before ``conv.close()`` releases the event stream.
    """
    return get_agent_final_response(getattr(conv.state, "events", []))
