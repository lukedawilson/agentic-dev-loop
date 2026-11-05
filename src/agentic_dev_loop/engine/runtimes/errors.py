"""Runtime-layer failures that the step runner classifies (unit 10)."""

from __future__ import annotations


class RuntimeSetupError(RuntimeError):
    """A provider cannot be constructed; the message names the fix."""


class HarnessTimeoutError(RuntimeError):
    """A harness round trip exceeded its bounded wait."""


class HarnessProcessError(RuntimeError):
    """The harness subprocess died; mapped to STUCK by the drivers."""


def is_process_death(exc: BaseException) -> bool:
    """True when a harness failure looks like subprocess death (-> STUCK)."""
    name = type(exc).__name__.lower()
    return "process" in name or "exit" in name or "spawn" in name
