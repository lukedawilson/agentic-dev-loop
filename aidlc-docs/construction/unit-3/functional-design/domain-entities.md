# Domain Entities - unit-3 (always-on sandbox isolation)

> **NFR-stage amendments (2026-10-19)**: native backend dropped (user directive); Docker is the only backend and the `Sandbox` surface is retained as the extension seam. Per-step `env:` mapping (NFR-1), persistent-per-run lifecycle (NFR-2), `docker` CLI transport (NFR-3), per-step `setup:` hook + stamp caching (NFR-5), Windows CI (NFR-6). Amended sections below carry these changes.

Per DDD standards: descent audit applied; domain-standards extension enforced. The domain was exposed before this model was proposed (DOMAIN-01 in the plan); every concept below traces to an exposition answer (DOMAIN-03).

## Trace List (DOMAIN-03)

| Domain concept | Exposition answer it came from |
| --- | --- |
| Sandbox | Dimension 1 (FR-2.1's "isolation boundary" concept) + Dimension 2 (the Sandbox owns its request → result execution context) |
| Isolation profile | FR-2.5 "profile defaults" + FD-1 answer (what is blocked: network, writes, git mutations, capabilities) |
| Docker backend | FR-2.2 "concrete runtime chosen per platform" + FD-2 answer. **Amended (user directive 2026-10-19):** the native backend is dropped; Docker is the single concrete backend |
| SandboxExecutionRequest / SandboxExecutionResult | Dimensions 2–3 (runtime contexts; the request → result flow each sandboxed execution owns) |
| Setup hook (provision phase) | NFR-5 (user: stack-agnostic provisioning - "the code could be anything") |
| PipelineConfig.isolation | Dimension 2 (PipelineConfig owns the isolation declaration) |
| Host-side set (action steps, LangGraph, LLM, viz, commit) | Dimension 1 - the trusted complement; explicitly NOT a domain object |
| Daemon | Dimension 2 - owns real container state; infrastructure, not our domain |

## New Entity

### `Sandbox` (NEW - descent step 3, `src/agentic_dev_loop/isolation.py`)

The isolation boundary: owns the sandboxed execution lifecycle (persistent-per-run container → per-request exec → destroy → result). Rich behaviour, not a data holder:

- `run(request: SandboxExecutionRequest) -> SandboxExecutionResult` - the execution lifecycle; enforces the effective profile and the git-mutation guard; guarantees destruction on every path (BR-8.3, NFR-2).
- One concrete backend (Docker) behind the retained `Sandbox` surface. **The interface retention is deliberate**: user directive (2026-10-19) "drop native linux sandboxing from this intent (but keep the interface)" - the surface stays as the extension seam for future backends. This is a user-directed exception to the no-single-implementation-interface rule, not a DDD violation.

## Value Objects

### `SandboxExecutionRequest` (NEW - descent step 1, carries invariant behaviour)

| Field | Meaning |
| --- | --- |
| `argv` | command + arguments to execute inside |
| `cwd` | working directory (must be under a mounted root) |
| `env` | resolved sandbox env - fixed safe base + the step's declared `env:` mapping (BR-9.1/NFR-1) |
| `timeout` | per-step bound |
| `mounts` | workspace + state_dir bind mounts |

Behaviour: validation at construction - non-empty argv, cwd inside a mount, bounded sizes (SECURITY-05). Not anaemic.

### `SandboxExecutionResult` (NEW - descent step 1, carries invariant behaviour)

| Field | Meaning |
| --- | --- |
| `exit_code` | the verdict |
| `stdout` / `stderr` | captured output |
| `duration` | timing |
| `backend` | which backend ran it (audit, BR-11) |

Behaviour: `succeeded` / `is_timeout` predicates. Exit-code-is-the-verdict semantics unchanged (unit-2 BR-4.4).

## New Module - pure translation functions (descent step 0)

`src/agentic_dev_loop/isolation.py` also hosts the platform-independent pure functions - the bulk of NFR-7 testability:

| Function | Signature | Notes |
| --- | --- | --- |
| `resolve_effective_profile` | `(defaults, overrides) -> IsolationProfile` | merge + validate (BR-5) |
| `select_backend` | `(platform, probe) -> BackendChoice` | pure decision over probe results (BR-4); always Docker or loud failure |
| `resolve_sandbox_env` | `(fixed_base, declared_env, host_env) -> dict` | fixed safe base + declared env only; `env_allowlist` never consulted; LLM key only if declared (NFR-1) |
| `docker_create_payload` | `(profile, request) -> payload` | profile → Docker create payload (BR-5) |
| `setup_stamp_key` | `(setup_cmd, lockfile_hashes) -> str` | stable provision-stamp cache key (BR-13.5) |
| `translate_mount_path` | `(host_path, platform) -> str` | per-platform mount translation incl. WSL2 (BR-4.5) |
| `is_git_mutation` | `(argv: list[str]) -> bool` | deny-by-default subcommand classifier (BR-6) |

All pure - the PBT-03 targets. ~~`bwrap_argv` / `landlock_ruleset` / `seccomp_policy` removed with the native backend.~~ The availability probe (daemon detection) is an IO adapter beside them, faked in tests (FD-5).

## Modified Entities

### `PipelineConfig` (entity - modified, `src/agentic_dev_loop/config.py`)

- Gains the optional `isolation:` section (profile overrides, image override, `refresh` flag). **Amended:** no backend override (single backend).
- Behaviour: `effective_profile()` resolves defaults + overrides; unknown keys fail at load (BR-5.3).
- Always-on: absence of the section means opinionated defaults (BR-2.4) - the section configures the sandbox, it never enables/disables it.

### `StepConfig` (entity - modified, `src/agentic_dev_loop/config.py`)

- Gains optional `env:` (mapping VAR → value | null=inherit) and optional `setup:` (command steps only; agent/action steps reject it at load - BR-13.1).

### `StagesRuntime._run_command` (module function - modified, `src/agentic_dev_loop/stages.py`)

- Routes command steps through `Sandbox.run` instead of a host subprocess (BR-1.1). The step's declared env (`resolve_sandbox_env`) feeds the request env (BR-9.1/NFR-1). Steps with `setup:` run the provision phase first (stamp-cached, BR-13).

### `StagesRuntime.run_agent` / terminal-tool wiring (modified, `src/agentic_dev_loop/stages.py`)

- The agent's `terminal` tool becomes a trusted host-side wrapper that delegates to `Sandbox.run` (BR-1.1). The tool stays an SDK capability tool - only its execution path changes. The agent step's declared env applies to its terminal; no setup, no network for the terminal (BR-13.6).

### `actions.py` (unchanged)

- Action steps (`commit`, `kill_listeners_on_port`, `git_worktree_clean`) remain host-side in-process (BR-1.2, BR-7). No change in this unit.

## Descent Audit (Domain Validation)

| Behaviour | Placement | Step | Verdict |
| --- | --- | --- |
| Resolve effective profile | `resolve_effective_profile` | 0 static helper | Correct - pure merge/validate |
| Choose backend | `select_backend` | 0 static helper | Correct - pure decision; probe is a faked IO adapter |
| Resolve sandbox env | `resolve_sandbox_env` | 0 static helper | Correct - pure; scrubbing policy as data |
| Translate profile → mechanism | `docker_create_payload` | 0 static helper | Correct - pure, platform-independent |
| Compute setup stamp key | `setup_stamp_key` | 0 static helper | Correct - pure hash function |
| Translate mount paths | `translate_mount_path` | 0 static helper | Correct - pure, per-platform fixtures |
| Classify git mutations | `is_git_mutation` | 0 static helper | Correct - pure predicate |
| Validate request invariants | `SandboxExecutionRequest` constructor | 1 value object | Correct - VO with invariant behaviour |
| Predicates on results | `SandboxExecutionResult.succeeded` / `is_timeout` | 1 value object | Correct - VO with behaviour |
| Execute an isolated command | `Sandbox.run` | 3 new entity | Correct - owns the execution lifecycle; single Docker backend, interface retained per user directive |
| Own the isolation declaration | `PipelineConfig.effective_profile` | 2 method on existing entity | Correct - config owns its declaration |
| Route command steps into the sandbox | `_run_command` | 2 existing orchestration point | Correct - one caller (`make_command_node`); provision phase before sandboxed exec |
| Wire the terminal tool to the sandbox | terminal-tool wrapper | 2 existing tool wiring | Correct - host-side adapter; execution path only |

No delegating entity; no service-named value object; no per-endpoint service; no persistence abstraction (no Repository/DAO - DOMAIN-08 N/A, none proposed); the one extracted interface has a single concrete backend (Docker) by explicit user directive - retained as the extension seam.

## Requirements Traceability

| FR | Covered by | Note |
| --- | --- | --- |
| FR-2.1 (boundary) | BR-1; `Sandbox` + modified `_run_command` / terminal tool | Terminal tool + command steps inside; action/LLM/viz/commit host-side (FD-3 confirmed) |
| FR-2.2 (mechanism) | BR-3, BR-4 | **Amended (NFR stage):** the abstraction is the `Sandbox` surface; Docker is the only concrete backend, on all platforms (user directive 2026-10-19). `requirements.md` FR-2.2 wording to be amended accordingly (deferred-amendments list) |
| FR-2.3 (failure mode) | BR-2, BR-8 | **Amended by FD-4:** isolation is always on ⇒ fail fast ALWAYS, not only "when requested". `requirements.md` FR-2.3 + NFR-1 + `unit-of-work.md` opt-in wording to be amended accordingly |
| FR-2.4 (platforms) | BR-4 | Daemon detection (unix socket/npipe+WSL2), mount translation, CRLF/permissions; `docker` CLI transport; Windows CI (NFR-6) |
| FR-2.5 (profile defaults) | BR-5 | FD-1 default + FD-3c git block + FD-4b pinned image |
| FR-2.6 (host-side commit) | BR-6, BR-7 | Commit stays host-side; NEW: agent git mutations blocked by default (FD-3b/3c); residual hook risk accepted + recorded |
| NFR-1 (backward compat) | BR-2.4 | **Reframed by FD-4:** pipeline YAML/semantics unchanged; the sandbox runtime is a NEW environment requirement - loud startup failure on machines without one |
| NFR-2 (fail fast) | BR-8 | Loud, actionable errors |
| NFR-3 (security baseline) | BR-5/6/8/9/11/13 + Security-baseline mapping table | least privilege, supply chain, credential hygiene, audit |
| NFR-4 (resiliency, scoped) | BR-10 | Idempotent-infra retry only; server-grade out of scope |
| NFR-7 (testability) | BR-12 | Fake at Sandbox surface; pure logic everywhere; marked opt-in integration tests; CI ubuntu-latest + windows-latest (NFR-6) |

## Deferred Amendments (recorded, not applied here)

- `requirements.md`: FR-2.2 abstraction wording (plus the native-backend drop), FR-2.3 always-on wording, NFR-1 opt-in wording, and the Stage-2 title "Kernel-Level Isolation" → "kernel-level sandboxing" (ID-8, infra review: Docker = kernel-enforced sandbox, not separate-kernel isolation).
- `unit-of-work.md`: the "OPT-IN - not requested ⇒ behavior unchanged" bullet is overturned by FD-4; unit-3 title "Kernel-level isolation" → "kernel-level sandboxing" (ID-8).
- `unit-4-intent-claude-code-backend.md`: its opt-in assumption needs a matching amendment when unit-4 is planned; "kernel isolation" references (findings 5, §9) → "kernel-level sandboxing".

> **Applied 2026-10-25:** the `requirements.md` and `unit-of-work.md` amendments above are now applied (always-on wording, FR-2.2 abstraction, Stage-2 title), and the story map gained unit-5 pillar rows (U5-P1..U5-P5). The `unit-4-intent` doc stays as history (unit-4 WONTSHIP).

## Amendment (2026-10-19) - loop-mechanics hardening

- **DockerBackend**: constructor takes workspace only (iso_state_dir param dropped - Q1).
- **Sandbox lifecycle**: create/start at construction (eager, Q2); run() = exec only; close() idempotent destroy. No lazy-create state on the entity.
- **Commit action (actions.py)**: gains trusted hooksPath (Q3) - the ONLY actions.py change.
- **PipelineConfig.state_dir** default relocates out of the workspace (Q1) - descent-audit rows for PipelineConfig/state_dir updated accordingly.

## Amendment (2026-10-19) - package rename

- Package references updated `agent-engine` → `agentic-dev-loop` throughout (module paths, default image `ghcr.io/lukedawilson/agentic-dev-loop/dev`, env var `AGENTIC_DEV_LOOP_DEV_IMAGE`, user state dirs `~/.agentic-dev-loop/*`). No design change.
