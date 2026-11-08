"""Read-only run-history reporting (unit 12)."""

from .metrics import Metric, apply_filters, pure_metrics
from .render import render_markdown
from .sources import (
    AuditRecord,
    Attempt,
    DirData,
    ProseFacts,
    RunRecord,
    StepVerdict,
    audit_dir,
    build_run_records,
    discover,
    find_run_dir,
    is_test_command,
    parse_ts,
    read_audit,
    read_dir,
    runs_root,
)

__all__ = [
    "AuditRecord",
    "Attempt",
    "DirData",
    "Metric",
    "ProseFacts",
    "RunRecord",
    "StepVerdict",
    "apply_filters",
    "audit_dir",
    "build_run_records",
    "discover",
    "find_run_dir",
    "is_test_command",
    "parse_ts",
    "pure_metrics",
    "read_audit",
    "read_dir",
    "render_markdown",
    "runs_root",
]
