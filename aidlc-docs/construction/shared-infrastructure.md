# Shared Infrastructure (Construction)

Infrastructure shared across units and all pipeline runs, first defined during
unit-3 Infrastructure Design (2026-10-19). Units 1-2 had no infrastructure
(all categories N/A - they deferred container infra to unit-3).

## 1. Docker sandbox runtime

- **What**: any compliant Docker daemon (engine API ≥ 1.43 / Engine 24) reachable via the `docker` CLI; Windows requires Linux-containers mode.
- **Who uses it**: every unit-3+ pipeline run (always-on isolation) and all CI jobs.
- **Governed by**: `aidlc-docs/construction/unit-3/infrastructure-design/infrastructure-design.md` (engine baseline, container hardening, resource caps).

## 2. Pinned dev image + GHCR registry

- **What**: `ghcr.io/lukedawilson/agentic-dev-loop/dev` (python + node + git), digest-pinned in code; published by CI on main; per-pipeline `isolation.image` override.
- **Who uses it**: all sandboxed pipelines on all machines (default image), CI.
- **Governed by**: SECURITY-07 (digest pinning + post-pull verification); publish flow in CI `publish-image.yml` (ships with unit-3).

## 3. CI workflows

- **What**: `.github/workflows/ci.yml` (ubuntu-latest + windows-latest matrix: unit tests + Docker-backend integration tests) and `publish-image.yml` (image build/push). First CI in the repo - ships with unit-3.
- **Who uses it**: the whole repo (units 1-3 tests run together; future units extend the matrix).

## 4. Per-user state directory

- **What**: `~/.agentic-dev-loop/state/` - provision stamps (`stamps/`) and the sandbox audit log (`sandbox-audit.jsonl`).
- **Who uses it**: all pipelines on a machine; shared across projects (one stamp per setup key).

## Change process

- Digests and engine-version floors change via normal commits; toolchain bumps must bump the image digest constant in the same commit.
- CI workflow changes follow the same per-unit design/approval flow as code.

## 5. Per-user run state (added 2026-10-19, Q1)

- `~/.agentic-dev-loop/runs/<pipeline-hash>/` - default PipelineConfig.state_dir: checkpoint DB + last-error.md + per-run artifacts, OUTSIDE the sandbox mount.

## Amendment (2026-10-19) - package rename

- Package references updated `agent-engine` → `agentic-dev-loop` throughout (module paths, default image `ghcr.io/lukedawilson/agentic-dev-loop/dev`, env var `AGENTIC_DEV_LOOP_DEV_IMAGE`, user state dirs `~/.agentic-dev-loop/*`). No design change.

## 6. Provider auth store (added 2026-11-05, unit-10)

- **What**: `~/.agentic-dev-loop/auth/copilot.json` - the Copilot device-flow token (user-level, mode 600, outside the workspace, never mounted into any sandbox). Claude subscription mode stores nothing here: the engine only DETECTS `~/.claude/.credentials.json` presence and never reads or replays the credential.
- **Who uses it**: any pipeline with `provider: copilot`; token expiry re-runs the engine-hosted device flow.
- **Governed by**: `aidlc-docs/construction/unit-10/infrastructure-design/infrastructure-design.md`.

## 7. Optional provider extras + CI tiers (added 2026-11-05, unit-10)

- **What**: `[project.optional-dependencies] claude` / `copilot` (floor-pinned SDKs, COMPAT-SHIMS registered); CI runs a base extras-free job plus a compat/contract job installing both extras (ubuntu + windows). Live harness smoke is a `live_harness`-marked opt-in suite (`RUN_LIVE_HARNESS=1`), manual at the dogfood gate, never in CI.
- **Who uses it**: any pipeline with `provider: claude` / `provider: copilot`; the CI jobs and gate procedure.
- **Governed by**: `aidlc-docs/construction/unit-10/infrastructure-design/infrastructure-design.md`.
