"""Structural failure vocabulary for agent steps (unit-11).

These classes are value objects: they name *why* an agent step failed
structurally, never how the run should prose its way around the failure. A
structural failure halts the step and the run - it never enters the retry
path, because retrying re-runs the same refusal (FD-11, rule E-1).
"""

from __future__ import annotations

import enum


class FailureClass(str, enum.Enum):
    """The named structural failure classes an agent step can raise."""

    NO_ACTIONS = "no_actions"
    NO_VERDICT_TOOL = "no_verdict_tool"
    INVALID_VERDICT_VALUE = "invalid_verdict_value"
    CONFLICTING_VERDICT = "conflicting_verdict"
    WIRE_SHAPE = "wire_shape"
