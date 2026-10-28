"""Pipeline model + parse + results (core).

Dependency-light by contract (BR-2): pydantic + PyYAML + stdlib only. A guard
test fails if this package imports engine/viz or heavy deps.
"""

from .config import (PipelineConfig, StepConfig, LlmConfig, IsolationConfig,
                     VerdictSpec, Route, route_target, load_config,
                     default_state_dir, TERMINALS, MountSpec, EgressSpec,
                     StopWhenSpec)
from .results import Result
from .verdict import Verdict, VerdictError, parse_verdict, RETRY

__all__ = [
    "PipelineConfig", "StepConfig", "LlmConfig", "IsolationConfig",
    "VerdictSpec", "Route", "route_target", "load_config",
    "default_state_dir", "TERMINALS", "MountSpec", "EgressSpec",
    "StopWhenSpec",
    "Result", "Verdict", "VerdictError", "parse_verdict", "RETRY",
]
