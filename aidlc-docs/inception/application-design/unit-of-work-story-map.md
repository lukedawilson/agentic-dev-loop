# Unit of Work Story Map

User Stories stage was skipped (internal tooling) - per U-2 answer, this artifact maps **requirements (FR/NFR IDs)** to units.

## Requirement → Unit Map

| ID | Requirement | Unit |
| --- | --- | --- |
| FR-1.2 | Pipeline captures agent's final SDK response; writes artifact itself | unit-1 |
| FR-1.3 | Drop `file_editor` from shipped agents | unit-1 |
| FR-1.1 | Expose SDK tool-restriction knobs (`filter_tools_regex`, `include_default_tools`) | unit-2 |
| FR-1.4 | Fix `task_tracker` no-op instruction | unit-2 |
| FR-1.5 | Harden command-step shell invocation (argv, quoting, env) | unit-2 |
| FR-1.6 | Harden action-step subprocess calls (least privilege, injection) | unit-2 |
| FR-1.7 | Validate extension tool registration (collisions, reserved names) | unit-2 |
| FR-1.8 | Preserve fixed policies (fail-fast, commit gate, verdict retry, resume) | unit-1, unit-2, unit-3 (continuous) |
| FR-2.1 | Sandbox terminal tool + command steps | unit-3 |
| FR-2.2 | Sandbox surface abstraction; Docker-only backend; image-defined toolchain; bind mounts (native backend deferred to unit-7) | unit-3 |
| FR-2.3 | Fail fast when isolation unavailable (always-on, unconditional) | unit-3 |
| FR-2.4 | macOS / Linux / Windows support | unit-3 |
| FR-2.5 | Profile defaults (deferred to unit-3 FD) | unit-3 |
| FR-2.6 | Host-side commit unchanged | unit-3 |
| NFR-1 | Backward compatibility (existing pipelines keep working) | unit-1, unit-2, unit-3 (continuous) |
| NFR-2 | Fail-fast culture | unit-1, unit-2, unit-3 (continuous) |
| NFR-3 | Security baseline (least privilege, input validation, supply chain, credential hygiene, audit) | unit-2 (host surfaces), unit-3 (sandbox) |
| NFR-4 | Resiliency scoped (container retry/recovery) | unit-3 |
| NFR-5 | Domain standards (no persistence abstractions, no behaviourless aggregates) | unit-1, unit-2, unit-3 (continuous) |
| NFR-6 | PBT partial (pure functions, serialization round-trips, Hypothesis) | unit-1 (parse), unit-3 (config serialisation) |
| NFR-7 | Testability without live container where pure; opt-in real-Docker integration tests | unit-3 |
| U5-P1 | Least-access mounts (per-step mount scope; strict-by-default) | unit-5 |
| U5-P2 | Egress allowlists (per-step; proxy-enforced hostname matching) | unit-5 |
| U5-P3 | Read-only where possible (declared-writes model) | unit-5 |
| U5-P4 | `.git` blindness (StepWorkspace; .git absent by construction) | unit-5 |
| U5-P5 | Loud failure modes (fail-closed proxy death AE-SBX-018; loud mount/export/sync-back validation) | unit-5 |

## Per-Unit Verification Checklist

| Unit | Done when |
| --- | --- |
| unit-1 | Captured-response artifacts replace agent file-writes in the shipped pipeline; `file_editor` gone from dev/review; verdict parsing from captured text passes the historical failure fixtures (decorated verdicts, stale-artifact, unclear-verdict, truncation) |
| unit-2 | Frontmatter knobs wired and tested; `task_tracker` consistency fixed; command/action subprocess surfaces argv-based; extension registration rejects collisions at load |
| unit-3 | Always-on isolation: terminal + command steps run in container; fail-fast when unavailable; host-side steps unchanged; platform adaptation tests |

## Unassigned Requirements

Unit-5 scope is intent-defined (pillars U5-P1..U5-P5 above; traced to BR-1..BR-12 in the unit-5 functional design). Units 6-7 (cloud deployment; Linux-native sandboxing + egress protection) are INTENT ONLY - their rows land at their Functional Design stages.
