# AI-DLC State Tracking

## Project Information
- **Project Type**: Brownfield
- **Start Date**: 2026-10-17T10:58:41Z
- **Current Stage**: CONSTRUCTION - Units 1-3 COMPLETE; Unit 4 WONTSHIP; Unit 5 intent drafted (2026-10-24)

## Workspace State
- **Existing Code**: Yes
- **Reverse Engineering Needed**: Yes
- **Workspace Root**: /Users/luke/dev/work/agent-engine

## Code Location Rules
- **Application Code**: Workspace root (NEVER in aidlc-docs/)
- **Documentation**: aidlc-docs/ only
- **Structure patterns**: See code-generation.md Critical Rules

## Intent
- **Primary Intent**: Guardrails and sandboxing for the agentic-dev-loop pipeline library, in two stages:
  1. SDK-level hardening - harden the existing tooling injection via what the OpenHands SDK provides
  2. Kernel-level sandboxing - kernel-enforced sandboxing for guardrails (namespaces/cgroups/seccomp via Docker)

## Execution Plan Summary
- **Total Stages**: 12 planned
- **Stages to Execute**: Application Design, Units Generation, Functional Design, NFR Requirements, Infrastructure Design, Code Generation, Build and Test
- **Stages to Skip**: User Stories (user confirmed), NFR Design (no perf/HA work; container infra covered by Infrastructure Design)
- **Unit Order**: Unit 1 output-passing fix + file-tool removal → Unit 2 SDK hardening → Unit 3 kernel-level sandboxing (vertical slices) → Unit 4 Claude Dynamic Workflows driver (pipeline model + viz; no Docker sandboxing from Claude Code) → Unit 5 workspace egress isolation (per-step mount granularity + egress allowlists; intent drafted 2026-10-24)

## Extension Configuration
- **Domain Standards**: Yes - enabled (Requirements Analysis, 2026-10-17)
- **Resiliency Baseline**: Yes (scoped) - enabled as directional guidance, desktop-tool scope (retries/recovery in; server load/HA/DR out) (Requirements Analysis, 2026-10-17)
- **Security Baseline**: Yes - enabled (Requirements Analysis, 2026-10-17)
- **Property-Based Testing**: Yes (Partial) - pure functions + serialization round-trips only (Requirements Analysis, 2026-10-17)

## Stage Progress
- [x] Workspace Detection - 2026-10-17T10:58:41Z
- [x] Reverse Engineering - completed 2026-10-17T10:58:41Z; artifacts at aidlc-docs/inception/reverse-engineering/
- [x] Requirements Analysis - approved by user 2026-10-17T12:15:00Z (directives: Unit 1 = findings/verdict-passing mechanism first; skip User Stories; units = vertical slices)
- [ ] User Stories - SKIPPED (user confirmed; internal tooling)
- [x] Workflow Planning - approved 2026-10-17T12:25:00Z (user restated 3 units: output-passing fix + file-tool removal, SDK hardening, kernel isolation)
- [x] Application Design - approved 2026-10-17T13:20:00Z (DDD amendments applied; domain diagrams approved)
- [x] Units Generation - approved 2026-10-17 (numeric unit names unit-1/2/3; story-map maps FR/NFR IDs; artifacts in aidlc-docs/inception/application-design/)
- [ ] NFR Design - SKIP (no perf/HA; infra in Infrastructure Design)

### 🟢 CONSTRUCTION PHASE

Per-unit construction stages (5 stages per unit: Functional Design, NFR Requirements, NFR Design, Infrastructure Design, Code Generation; Build and Test is the global final step).

- Unit 1: output-passing fix + file-tool removal - Functional Design [x]
- Unit 1: output-passing fix + file-tool removal - NFR Requirements [x]
- Unit 1: output-passing fix + file-tool removal - NFR Design [x] (global SKIP)
- Unit 1: output-passing fix + file-tool removal - Infrastructure Design [x]
- Unit 1: output-passing fix + file-tool removal - Code Generation [x]
- Unit 2: SDK hardening - Functional Design [x]
- Unit 2: SDK hardening - NFR Requirements [x]
- Unit 2: SDK hardening - NFR Design [x] (global SKIP)
- Unit 2: SDK hardening - Infrastructure Design [x]
- Unit 2: SDK hardening - Code Generation [x]
- Unit 3: kernel-level sandboxing (always-on) - Functional Design [x]
- Unit 3: kernel-level sandboxing (always-on) - NFR Requirements [x]
- Unit 3: kernel-level sandboxing (always-on) - NFR Design [x] (global SKIP)
- Unit 3: kernel-level sandboxing (always-on) - Infrastructure Design [x]
- Unit 3: kernel-level sandboxing (always-on) - Code Generation [x]
- Unit 4: Claude Dynamic Workflows driver - WONTSHIP/WONTDO (withdrawn 2026-10-22; excluded from construction progress)
- Unit 5: workspace egress isolation - Functional Design [ ]
- Unit 5: workspace egress isolation - NFR Requirements [ ]
- Unit 5: workspace egress isolation - NFR Design [ ] (global SKIP)
- Unit 5: workspace egress isolation - Infrastructure Design [ ]
- Unit 5: workspace egress isolation - Code Generation [ ]
- Build and Test: green at unit-3 close (2026-10-21, full suite incl. docker-marked tests); re-run at unit-5 close

## Unit Progress

### unit-1 (output-passing fix + file-tool removal) - COMPLETE
- [x] Functional Design - approved 2026-10-17T14:35:00Z
- [x] NFR Requirements - approved 2026-10-17T15:00:00Z
- [x] Infrastructure Design - approved 2026-10-17 (all categories N/A)
- [x] Code Generation - approved 2026-10-18 (user: "Seems to work. Commit and push, mark unit complete")
- [x] Build and Test - full suite green: 373 passed (mechanical `test` command substance)

### unit-2 (SDK hardening) - COMPLETE
Scope FR-1.1 (tool-surface semantics - `tools:` as sole capability allowlist; no `filter_tools_regex`), FR-1.4 (task_tracker instruction removed), FR-1.5 (command-step shell hardening), FR-1.6 (action-step subprocess hardening), FR-1.7 (extension tool-registration validation)
- [x] Functional Design - approved 2026-10-18 (FD-1 drop task_tracker instruction; FD-2 capabilities + FinishTool + ThinkTool; FD-2b drop regex; FD-3 hybrid shell; FD-4 any-registered-name collision; FD-5 deeper subprocess hardening)
- [x] NFR Requirements - approved 2026-10-18 (NFR-1=C hybrid env; refined: LLM key included by default; `env_allowlist` optional key)
- [x] Infrastructure Design - approved 2026-10-18 (all 7 categories N/A; container infra deferred to unit-3)
- [x] Code Generation - complete 2026-10-19 (dev loop self-hosted: `proc.py` + registry/actions/stages/config/agents hardening + `env_allowlist` + tests)
- [x] Build and Test - full suite green: 391 passed (mechanical `test` command substance)

### unit-3 (kernel-level sandboxing, always-on) - COMPLETE (2026-10-21)
Scope FR-2.1–FR-2.6 (always-on sandbox; Docker backend on ALL platforms - native bwrap/Landlock path dropped 2026-10-19, Sandbox surface retained as extension seam; fail-fast when no runtime; host-side commit; agent git-mutation blocking; per-step env + setup provision phase)

- [x] Functional Design - approved 2026-10-19 (FD-1 conservative default profile; FD-3c git-mutation block; FD-4 always-on directive; FD-5 interface-level fakes; artifacts in aidlc-docs/construction/unit-3/functional-design/ - amended in place during NFR stage)
- [x] NFR Requirements - approved 2026-10-19 (NFR-1 per-step env; NFR-2 persistent-per-run; NFR-3 docker CLI; NFR-5 setup hook provision phase + stamp caching; NFR-6 CI ubuntu+windows; major directive: drop native backend, keep interface; artifacts in aidlc-docs/construction/unit-3/nfr-requirements/)
- [x] Infrastructure Design - approved 2026-10-19 (ID-1 own dev image via CI/GHCR; ID-2 any compliant daemon, API ≥1.43, Linux-containers mode on Windows; ID-3 ~/.agentic-dev-loop/state, one stamp/key; ID-4 caps 4g/2cpu exec, 8g/4cpu provision, per-pipeline override; ID-5 JSONL audit + 5MB rotation; ID-6 GitHub-hosted ubuntu+windows CI; ID-7 repo-shipped docs/setup.md user guide; ID-8 terminology = kernel-level sandboxing; artifacts in aidlc-docs/construction/unit-3/infrastructure-design/ + construction/shared-infrastructure.md)
- [x] Code Generation - complete (implemented by the dev loop: isolation.py + Docker sandbox + per-step env/setup + SDK terminal seam; releases v0.3.0 → v1.0.0a5; plan-014 per-stage networking `network: true` added post-FD)
- [x] Build and Test - full suite green incl. docker-marked integration tests (host daemon); validated end-to-end in yosk-cms unit-8 run (user: "tested it thoroughly in the yosk-cms repo and it's working")
- [ ] Operations (placeholder)

### unit-4 (Claude Dynamic Workflows driver) - WONTSHIP/WONTDO (2026-10-22)

Withdrawn by user directive 2026-10-22 ("Let's rip it out and mark unit 4 as wontship/wontdo"). Rationale: the Claude Workflow tool's no-filesystem/no-shell sandbox makes the adapter agent-mediated at every seam (command gates, viz events, prompts) - not worth the divergence from engine-mode guarantees. NO Claude adapter ships (not even viz-only). The core/engine/viz module split IS KEPT (independent value: dependency contracts, guard tests, light installs). Adapter-surface removal = planning-docs/017-remove-unit-4-adapter.md → release 1.0.0a7 (renumbered 2026-10-22; setup-stamp/venv hardening split out to planning-docs/016-sandbox-venv-hardening.md). Historical docs (requirements FR-4.x, unit-4 construction artifacts, intent doc) remain as history.

### unit-5 (workspace egress isolation) - INTENT ONLY (draft, 2026-10-24)

- Intent document: aidlc-docs/unit-5-intent-workspace-egress-isolation.md (drafted 2026-10-23; extended 2026-10-24 with live-sourced findings from plan 023 browser hardening)
- Scope: per-step mount granularity (rw / ro / absent) + egress allowlists + .git blindness; closes plan 023 follow-up 5 (read-only workspace, the residual write/exfil channel after the browser sandbox engages) and supersedes the narrow plan 023 Phase B
- No functional design stage yet; unit is next in order after the dependency plans land (018-021)