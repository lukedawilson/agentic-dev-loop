# Requirements - Guardrails & Sandboxing for agent-engine

## Unit Ordering (user directive)

1. **Unit 1 - Findings/verdict-passing mechanism** (Q12/FR-1.2, FR-1.3, parsing redesign): a genuine bug-in-waiting; fixed FIRST, before tool-hardening.
2. **Unit 2 - Tool-hardening** (remaining Stage-1 FRs).
3. **Unit 3+ - Kernel-level sandboxing** (Stage 2, always-on).

**Units are vertical slices** - each delivers end-to-end user-visible behavior, not a horizontal layer.

## Intent Analysis

- **User request**: Address the intent in two stages - (1) harden the existing tooling injection via what the OpenHands SDK provides, (2) introduce proper kernel-level isolation for guardrails and sandboxing.
- **Request type**: Enhancement (security hardening) of an existing library.
- **Scope**: Multiple components - SDK tool surface, command steps, action-step subprocess calls, extension tool registration, plus a new always-on isolation subsystem.
- **Complexity**: Complex. Two delivery units; second unit requires new architecture (container runtime integration), not just code changes.

## Stage 1 - SDK-Level Hardening (Unit 1) - ALWAYS-ON, no opt-out

Applies to every run, no opt-out flags ("we're guardrailed by design"). Existing pipelines must keep working.

### Functional Requirements

| ID | Requirement |
| --- | --- |
| FR-1.1 | **Tool-surface semantics (reduced 2026-10-18, unit-2 FD-2/FD-2b).** `tools:` in `.agent.md` is the sole, exact capability allowlist; `FinishTool` + `ThinkTool` remain SDK-default infra (not capabilities). `filter_tools_regex` and an `include_default_tools` frontmatter knob are **out of scope**. Fail fast on unknown tool names (preserve). |
| FR-1.2 | **Replace agent-written reports with pipeline-captured responses.** The pipeline captures the agent's **final SDK response** and writes the artifact itself (implementation summary + verdicts parsed from the captured text). Agent file-writes stop being a feedback channel. |
| FR-1.3 | **Drop `file_editor` from shipped agents.** With FR-1.2 it has no remaining job. Its parsing logic (of captured responses into summary + verdicts) gets dedicated treatment during Functional Design - the existing verdict parser has had bugs; do not carry assumptions into the redesign. |
| FR-1.4 | **Fix tool-list/prompt consistency.** The `task_tracker` instruction in `dev.agent.md` is a no-op (tool not in its allowlist). Either add the tool or drop the instruction. |
| FR-1.5 | **Harden command-step shell invocation.** Review `stages.py` `_run_command` (`shell=True` → argv-list where possible; argument quoting; environment hygiene). Scope = harden what exists; kernel-level sandboxing is Stage 2. |
| FR-1.6 | **Harden action-step subprocess calls.** Review `actions.py` git/lsof/ps invocations for least-privilege and command-injection resistance. |
| FR-1.7 | **Harden extension tool registration.** Extensions register tools into the SDK **global** registry with no namespacing. Validate names at registration (collision detection against built-ins and other extensions; reserved-name rejection). Fail fast at load. |
| FR-1.8 | **Preserve existing fixed policies.** Fail-fast on missing config/secrets, single host-side commit point gated on clean worktree, verdict retry semantics, crash-safe resume. |

## Stage 2 - Kernel-Level Sandboxing (Unit 3+) - ALWAYS-ON

Isolation is **always-on** (amended 2026-10-19, unit-3 FD-4): every run uses the sandbox; the `isolation:` section configures it and can never disable it. No usable runtime -> fail fast.

### Functional Requirements

| ID | Requirement |
| --- | --- |
| FR-2.1 | **Isolation scope**: the agent `terminal` tool **and** command steps run inside the isolation boundary (both are agent-influence channels - conftest argument: a planted test makes the trusted `pytest` command step execute malicious code). Action steps, LangGraph runtime, LLM calls, viz server, and commit stay host-side (trusted code). |
| FR-2.2 | **Mechanism**: the retained **Sandbox surface** is the abstraction; **Docker** is the only concrete backend on all platforms (native backend dropped by user directive 2026-10-19, re-planned as unit-7). Sandboxed steps get their toolchain from a Linux dev image (image-defined toolchain, like CI); bind mounts carry workspace + state_dir. |
| FR-2.3 | **Failure mode**: isolation is always-on; no usable runtime available -> **fail fast, refuse to run**. No silent fallback to unsandboxed execution. |
| FR-2.4 | **Platform support**: macOS, Linux, and Windows. Per-platform adaptation: daemon detection (unix socket vs npipe+WSL2), mount-path translation, CRLF/permission-metadata handling on Windows mounts. |
| FR-2.5 | **Profile defaults**: what the sandbox blocks by default (network, file paths, capabilities) - **deferred to Functional Design** ("we can discuss the mechanism during FD"). |
| FR-2.6 | **Host-side commit**: the commit action remains host-side native git (avoids Windows write-back pitfalls). |

### Open Questions (deferred to Functional Design)

- **Linux backend (Unit 3 FD) - RESOLVED (2026-10-19)**: the native backend was dropped for unit-3 by user directive; Docker is the only backend, with the Sandbox surface retained as the extension seam. The native path (bubblewrap/Landlock/seccomp + kernel-level egress) is re-planned as unit-7.

## Cross-Cutting / Non-Functional

| ID | NFR |
| --- | --- |
| NFR-1 | **Backward compatibility**: Stage 1 hardening must not break existing pipelines (YAML schema unchanged where possible). Stage 2 is always-on, so the sandbox runtime is a hard environment requirement: a machine without a usable runtime fails loudly at startup (amended 2026-10-19). |
| NFR-2 | **Fail-fast culture** (existing policy): missing config/secrets, unknown tools, unknown actions, unavailable isolation - all refuse to run loudly, naming the problem. No silent degradation. |
| NFR-3 | **Security baseline (blocking)**: least privilege for all new subprocess/container surfaces, input validation, supply-chain integrity (image references pinned/digest-verifiable), credential hygiene (no secrets into container env or logs), audit/observability of what ran inside the sandbox. Cloud-shaped rules (network config, HTTP headers, alerting) marked N/A for this library. |
| NFR-4 | **Resiliency (scoped, desktop)**: retry/recovery guidance for transient container failures (image pull, daemon start). Server-grade load/HA/DR/RTO/RPO explicitly out of scope. |
| NFR-5 | **Domain standards (blocking)**: DDD domain model for the pipeline/isolation domain at design time - no persistence abstractions, no behaviourless aggregates; descent audit at validation. |
| NFR-6 | **Property-based testing (partial)**: PBT for pure functions and serialization round-trips (verdict parsing, config (de)serialization) with Hypothesis; property identification in Functional Design. |
| NFR-7 | **Testability**: the isolation backend must be testable without a running container (e.g., fake runtime speaking the same interface); no tests that silently skip when Docker is absent. |

## Stage 4 - Claude Dynamic Workflows driver (unit-4) - WONTSHIP (2026-10-22)

| ID | Requirement |
| --- | --- |
| FR-4.1 | **Module split (composable)**: the package splits into three composable modules - `agentic_dev_loop.core` (pipeline model: PipelineConfig + YAML parsing/validation + workflow-schema projection; light deps: pydantic + PyYAML only, no openhands-sdk/docker), `agentic_dev_loop.engine` (graph, stages, runtime, sandbox, CLI; heavy deps), `agentic_dev_loop.viz` (stdlib-only viz server + event model). Dependency contract enforced by a guard test: `core` imports nothing from `engine`/`viz` and no heavy deps. Existing import paths re-exported where practical (backward compat). |
| FR-4.2 | **Workflow-mode scope**: from Claude Dynamic Workflows we drive exactly (a) parsing pipeline.yaml and creating the workflow steps from it, and (b) the visualiser. NO Docker sandboxing from Claude Code - explicitly out of scope, not attempted. Interactive metering (intent pillar 1) is out of unit-4 scope. |
| FR-4.3 | **Claude skill + bundled workflow JS**: a skill `/agentic-dev-loop` bundles a workflow JS **template**. At BUILD time (workflow creation), Claude - which has tools - imports `agentic_dev_loop.core`, parses pipeline.yaml, and generates the concrete workflow JS with steps baked in (agent prompts resolved from agents_dir; verdicts, `on_pass`/`on_fail`, `max_attempts` encoded). Runtime never parses. (Amended 2026-10-21: original runtime-parse-via-subagent design REVERSED by user - "pipeline.yaml should be parsed at build time, not runtime".) |
| FR-4.4 | **Command steps in workflow mode**: each command step becomes a subagent step; the subagent runs the command in Claude Code's own environment and reports exit code/verdict. No engine process at runtime. |
| FR-4.5 | **Verdict contract preserved**: verdict agents still write `VERDICT: PASS/FAIL` artifacts; subagent steps report their verdict in final output; the workflow JS routes `on_pass`/`on_fail`/`goto` with its own `max_attempts` loop counter. One pipeline.yaml behaves truthfully in both modes. |
| FR-4.6 | **Commit in workflow mode**: a small trusted CLI shim (docker-free) reuses the engine's commit logic - clean-worktree-at-start gate, trusted hooksPath, templated message; the workflow's commit step is a subagent invoking that shim. Policy stays real code, not prompt re-encoding. |
| FR-4.7 | **Visualiser in workflow mode**: a subagent launches the live viz server (`python -m agentic_dev_loop.viz`, free-port fallback) and surfaces the URL; step subagents POST stage events to a new local HTTP ingest endpoint; the browser keeps the live SSE stream. Engine mode keeps its in-process VizBus unchanged. |
| FR-4.8 | **No MCP layer**: the intent doc's ae_* MCP-tool architecture is dropped; subagents reach `core`/`viz` via shell/python directly. |
| FR-4.9 | **Shared pipeline.yaml**: one pipeline.yaml works in both modes. Engine-only fields (`isolation:`, per-step `network:`, `setup:`) are ignored in workflow mode with documented semantics - workflow steps run in Claude Code's own environment (its permission model replaces the engine sandbox; trust-model change documented). |

## Key Requirements Summary

1. **Unit 1 (SDK hardening, always-on)**: expose SDK tool-restriction knobs; pipeline captures final agent responses (agent stops writing reports); drop `file_editor`; fix `task_tracker` no-op; harden command/action subprocess surfaces; validate extension tool registration.
2. **Unit 3 (kernel-level sandboxing, always-on)**: container-based sandboxing of the terminal tool + command steps via the retained Sandbox surface (Docker-only backend); fail fast when unavailable; macOS/Linux/Windows; profile defaults and parsing-logic redesign deferred to Functional Design.
3. **Fixed policies and fail-fast culture preserved; existing pipelines keep working.**
4. **Unit 4 (Claude Dynamic Workflows driver)**: module split (core/engine/viz); skill `/agentic-dev-loop` with bundled workflow JS template (build-time parse: steps baked into the generated JS at workflow creation); command steps become subagent steps; same verdict contract, workflow JS routes; commit via trusted docker-free shim; subagent-launched live viz with HTTP ingest; no MCP; no Docker sandboxing from Claude Code; one pipeline.yaml both modes.
