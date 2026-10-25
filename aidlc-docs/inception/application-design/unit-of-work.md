# Unit of Work - Guardrails & Sandboxing

Three vertical-slice units, executed in strict order. Single package (`agent-engine`), no independently deployable services - each unit is a logical grouping of changes within the one module.

## Unit Definitions

### unit-1 - Output-passing fix (capture, don't trust agent file-writes)

**Intent** (user's words): "Fix the way output is passed between stages (and then remove file writing tools from agents that no longer need them)". Marked "genuine bug-in-waiting" - executes FIRST.

**Responsibilities**:
- Pipeline captures the agent's **final SDK response** and writes the artifact itself (FR-1.2). The agent's last message always exists when the agent finishes - forgetting a file-write stops being a failure mode.
- Verdict parsing reads from the captured response (`Verdict.from_text` → typed `Result`, absorbing `verdicts.py`; NO_VERDICT/AMBIGUOUS as typed errors, not exceptions).
- `CapturedOutput` value object: `from_agent_result(result, step, state_dir)` + `write(path)`.
- Remove `file_editor` from shipped agents (FR-1.3) - dev/review no longer write their own reports; `terminal` remains for work (qa still runs pytest).
- Existing pipeline YAML shape unchanged; `build_context` untouched (artifact paths stay `implementation-summary.md` etc.).
- Parsing logic (captured response → summary + verdicts) gets dedicated FD treatment - existing parser has had bugs; no assumptions carried.

**Key domain objects**: `CapturedOutput` (VO, NEW), `Verdict` (VO, NEW), `StagesRuntime.run_agent` (modified).

### unit-2 - SDK-level hardening

**Intent** (user's words): "SDK-level hardening" - the full Stage-1 surface minus output-passing (FR-1.1, FR-1.4, FR-1.5, FR-1.6, FR-1.7). Always-on, no opt-out.

**Responsibilities**:
- Lock the agent tool surface: `tools:` is the exact capability allowlist; `FinishTool` + `ThinkTool` remain infra (FR-1.1, reduced per FD-2/FD-2b). Fail-fast on unknown tool names preserved.
- Fix `task_tracker` no-op instruction in `dev.agent.md` - either add the tool or drop the instruction (FR-1.4).
- Harden command-step shell invocation - `shell=True` → argv list, quoting, environment hygiene (FR-1.5).
- Harden action-step subprocess calls in `actions.py` (git/lsof/ps) for least privilege and injection resistance (FR-1.6).
- Validate extension tool registration - collision detection against built-ins and other extensions, reserved-name rejection, fail fast at load (FR-1.7). `ToolRegistry.validate`.
- Preserve fixed policies: fail-fast, single host-side commit, crash-safe resume.

**Key domain objects**: `ToolRegistry` (entity, modified - validate), `StagesRuntime._run_command` (modified), `ExtensionRegistry` (modified - validation at add_tool), `proc` (new module of pure subprocess helpers).

### unit-3 - Kernel-level sandboxing (always-on)

**Intent** (user's words): "Kernel-level isolation". ALWAYS-ON (amended 2026-10-19, unit-3 FD-4): every run uses the sandbox; the isolation section configures it, never disables it.

**Responsibilities**:
- Sandbox the agent `terminal` tool **and** command steps inside a container boundary (FR-2.1). Action steps, LangGraph runtime, LLM calls, viz server, and commit stay host-side (trusted code).
- Mechanism: Docker Engine API as the abstraction; runtime per platform (FR-2.2). Linux dev image defines the toolchain; bind mounts carry workspace + state_dir.
- Isolation requested but no usable runtime ⇒ fail fast, refuse to run (FR-2.3). No silent fallback.
- Platform support: macOS, Linux, Windows (FR-2.4). Internal behavior of one concrete `Sandbox` type - no protocol.
- Profile defaults (what the sandbox blocks) - deferred to this unit's Functional Design (FR-2.5).
- Host-side commit action unchanged (FR-2.6).
- FD question resolved (2026-10-19): the native backend was dropped for unit-3 (user directive); Docker is the only backend, with the Sandbox surface retained as the extension seam. The native path (bubblewrap/Landlock/seccomp) is re-planned as unit-7.

**Key domain objects**: `Sandbox` (VO, concrete, NEW), `SandboxExecutionRequest`/`SandboxExecutionResult` (VOs, NEW), `PipelineConfig.isolation` (modified - always-on configuration section), `StagesRuntime.run_agent`/`_run_command` (modified - route through Sandbox when enabled).

## Unit Order & Rationale

| Order | Unit | Rationale |
| --- | --- | --- |
| 1 | Output-passing fix | Bug-in-waiting; independent of both later units; lands the capture path that Unit 2/3 shape around |
| 2 | SDK hardening | Always-on host-side hardening; defines the tool/command surfaces Unit 3 will re-locate |
| 3 | Kernel-level sandboxing | Depends on U1 (capture path) and U2 (command shape) interfaces |

## Code Organization

Brownfield - existing structure retained:
- New domain objects: `src/agent_engine/outputs.py` (CapturedOutput, Verdict), `src/agent_engine/isolation.py` (Sandbox + request/result VOs).
- `verdicts.py` absorbed into `outputs.py` by Unit 1 (dead module removed).
- All other modules stay in place; modifications are additive or narrow.

## Not In Scope (this intent)

- Refactoring the existing unchanged model (viz/CLI layering, `LoopState` shape) - recorded in component-dependency.md as future-intent work.
- Always-on isolation (FD-4); the isolation section configures the sandbox, never disables it.
- macOS `es_new_descendants_client` native backend (future option, not foundation).
