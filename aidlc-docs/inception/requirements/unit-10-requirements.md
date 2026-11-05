# Unit 10 Requirements - Multi-provider runtime (OpenHands + Claude Agent + Copilot)

Unit intent: `aidlc-docs/unit-10-intent-provider-abstraction.md`
Questions and answers: `aidlc-docs/inception/requirements/unit-10-requirement-verification-questions.md`
Date: 2026-11-05. Status: awaiting user review/approval.

## Intent Analysis Summary

- **User request**: Plug Claude Code and GitHub Copilot subscription logins into
  the agentic dev loop as swappable agent providers alongside OpenHands,
  following the OpenCode-style architecture (run the real harness CLI as a
  subprocess, swap in the engine's tools). Preserve the kernel-level (Docker)
  sandboxing and the viz for every provider.
- **Request type**: New Feature (multi-provider capability).
- **Scope estimate**: Multiple Components (core config, engine runner path,
  tool registration, outputs, a new runtime driver package, pyproject extras,
  SDK compat shims). The central step-runner path is touched, so the blast
  radius is engine-wide even though most engine modules stay unchanged.
- **Complexity estimate**: Complex (three provider behaviors, two external
  SDKs, policy and security constraints, backward compatibility).

## Requirements Depth Decision

Standard depth. The design was produced and approved in the 2026-11-05
brainstorm, and the six clarifying questions were answered (all A). A
comprehensive traceability treatment adds no value here; the Functional Design
stage will deepen the data flows recorded in section 5 below.

## Clarifying Question Answers (2026-11-05, all A)

1. **Unit decomposition**: One unit with staged Code Generation: (1)
   abstraction + OpenHandsRuntime refactor landing green alone, (2) claude
   driver, (3) copilot driver.
2. **Claude auth**: User runs `claude login` themselves (Anthropic's own
   flow); the engine detects the session and fails fast with setup guidance if
   absent.
3. **Copilot auth**: Engine hosts GitHub's device flow (prints code + URL,
   user completes in browser, no credentials intermediated).
4. **Policy stance**: Ship the claude provider with a documented risk note.
5. **Missing design sections**: Covered in this requirements document and
   deepened in Functional Design.
6. **User Stories**: Skip (internal tooling, consistent with all prior units).

## 1. Functional Requirements

### FR-10.1 Provider selection with backward compatibility
The pipeline config gains a `provider` key under the profile
(`openhands | claude-agent | copilot`). The default is `openhands`. A config
that predates this feature (no `provider` key) behaves exactly as it does
today: same config validation, same driver, same outputs. A test asserts old
config files run the OpenHandsRuntime path with identical behavior.

### FR-10.2 AgentRuntime protocol
The engine owns a single runtime protocol:
`run(message) -> RuntimeResult(final_text, truncated, status)`, `nudge()`,
`close()`. `RuntimeStatus` is the enum `FINISHED | ERROR | STUCK | TRUNCATED`.
The step runner in stages.py consumes only this protocol; it never references
OpenHands Conversation internals again. RuntimeResult carries the raw final
text plus the truncated flag and status, which drive the existing artifact
policies (see FR-10.8).

### FR-10.3 OpenHandsRuntime (stage 1, pure refactor)
Today's Conversation-driving body of stages.py (Conversation construction,
send_message, conv.run, execution_status checks, finalize loop, infra-error
re-raise, LAST_ERROR policy) moves into OpenHandsRuntime verbatim in behavior.
The reasoning-effort gate (`_agentic_dev_loop_reasoning_effort`) stays
OpenHands-only. This stage must land with the full suite green and zero
behavior delta (dogfood run on the openhands provider) before stages 2 and 3
begin.

### FR-10.4 ClaudeAgentRuntime (stage 2)
Runs the real `claude` CLI through claude-agent-sdk as a subprocess, launched
via the shared async-to-sync bridge. Configuration (ClaudeAgentConfig): login
mode `subscription | key`, model, max_turns budget. No claude credentials are
ever read, stored, or intermediated by the engine.

### FR-10.5 CopilotRuntime (stage 3)
Runs the Codex CLI (openai-codex, pinned runtime) powered by a Copilot
subscription, or the Copilot SDK runtime. Configuration (CopilotConfig): auth
mode (device flow), model. The engine hosts GitHub's device flow: print code +
URL, user completes in browser, SDK stores the resulting OAuth credentials.

### FR-10.6 Shared tool catalog (engine-owned)
One catalog, four tools: `terminal`, `file_editor`, `task_tracker`, `finish`.
The catalog is defined once in the engine. `.agent.md` files (AgentSpec) are
100% portable across providers: existence and name-match are engine policy,
interpretation is per driver. The brief agent works on every provider.
Bindings per driver:
- OpenHands: existing SDK tools (TerminalTool + SandboxTerminalExecutor,
  FileEditorTool, TaskTrackerTool, FinishTool) - unchanged.
- Claude: catalog tools exposed as MCP tools; native claude tools
  (Bash/Edit/Write/Read/Glob/Grep/TodoWrite) OFF.
- Copilot: catalog tools exposed as MCP tools; no custom tools, no project
  config.

### FR-10.7 SandboxToolBridge (shared, thin-driver/fat-bridge)
All three drivers route every `terminal` tool call through the one bridge,
which composes the existing SandboxExecutionRequest: argv, cwd (container side
== host mount path), env (per-step secrets resolved exactly as today), timeout,
network lane, mount_set, egress_policy, allowlist, forbidden rules. Results
come back as SandboxExecutionResult; infra-error classification (030) is
unchanged; every bridge result emits the existing on_terminal_result viz
events. Drivers contribute only a thin tool-call adapter and an observation
formatter (TerminalObservation for OpenHands, MCP ToolResult for the
subprocess drivers).

### FR-10.8 CapturedOutput from runtime results
CapturedOutput gains `from_runtime_result(runtime_result, artifact, state_dir,
redactions)`; the existing `from_agent_result` remains for the OpenHands path
(where final-text extraction must still read the SDK final response before
conv.close()). Redaction and artifact writing stay shared. The engine rule
`truncated = status != FINISHED` is unchanged and applies to all providers.

### FR-10.9 Harness hardening (non-negotiable)
- Claude: `setting_sources: []` (no `.claude/` or `~/.claude/` skills,
  commands, memory, hooks; project hooks are host-side code execution),
  `allowed_tools` locked to the catalog, interactive permission prompts
  disabled (deny non-allowlisted tool use instead of prompting).
- Codex: no project config loading, no custom tools, engine MCP server only.
- Secrets never enter the harness process environment.
- Fail fast at startup if the SDK version cannot guarantee a harness execution
  tool is fully disabled (version-guard test, same pattern as
  tests/test_sdk_compat.py).
- The isolation verification checklist (post-mutation: intent AND blast
  radius) is part of the spec: every catalog tool call must provably execute
  through Sandbox.run and nothing else.

### FR-10.10 Authentication UX
- Claude: engine probes for an existing session at startup; if absent, fail
  fast with guidance to run `claude login` (Anthropic's own flow). Never
  collect, store, or intermediate credentials.
- Copilot: engine hosts GitHub device flow (code + URL, user completes in
  browser); stored OAuth credentials belong to the SDK (GitHub's own flow).
  BYOK (API key) is documented as a future option, out of scope.

### FR-10.11 Dependency handling
`claude-agent-sdk` and `openai-codex` ship as optional extras
`[claude]` and `[copilot]`. A configured provider whose package is missing
fails fast with install guidance (no silent fallback). Both SDKs get
COMPAT-SHIMS.md entries with removal criteria and version-guard + canary
tests.

### FR-10.12 Status mapping and budget semantics
See section 5 (status walkthrough). Budget: OpenHands keeps
max_iteration_per_run; Claude maps to max_turns; Copilot maps to its turn
limit. Budget exhaustion is TRUNCATED, never a silent FINISHED.

### FR-10.13 Policy documentation
The claude provider ships with a documented risk note: the Anthropic policy
gray area (claude.ai login in third-party products), the distinction from the
banned spoofing tools, the OpenCode precedent, and the June 2026 Agent SDK
credit model (subscription use draws from the monthly Agent SDK credit, not
interactive limits).

## 2. Non-Functional Requirements

- **NFR-10.1 Backward compatibility**: pre-feature configs behave identically;
  the move in FR-10.3 is provably behavior-preserving (suite green, dogfood
  run).
- **NFR-10.2 Security**: FR-10.9 hardening is unconditional; sandbox egress
  policy still governs every executed command; harness model-channel egress is
  unchanged (brain on host as today); secrets stay per-step inside
  SandboxExecutionRequest env only.
- **NFR-10.3 Reliability**: infra-error classification unchanged; subprocess
  death of claude/codex CLI maps to STUCK with no engine retry flap (030
  policy); LAST_ERROR file policy unchanged.
- **NFR-10.4 Testability**: TDD; interface-level fakes for runtimes
  (unit-3 FD-5 pattern); mocks ONLY at SDK subprocess boundaries; the bridge
  is tested against the real Sandbox path; SDK compat tests follow the
  version-guard + canary pattern.
- **NFR-10.5 Maintainability**: drivers stay thin; catalog and bridge live in
  the engine; one new package (proposed: src/agentic_dev_loop/engine/runtimes/)
  holds protocol, drivers, bridge, catalog, async bridge utility.
- **NFR-10.6 Repo hygiene**: all new files ASCII-only (no U+2014), consumer
  name ban applies, committed docs follow the repo style.
- **NFR-10.7 Fail fast**: no silent fallbacks anywhere in the new paths
  (missing package, missing session, un-disableable tool, unknown provider
  value all raise with named, actionable errors).

## 3. Per-Provider Data Flow

### 3.1 OpenHands (unchanged today, now behind the protocol)

- **Message in**: runner builds Conversation(agent, workspace,
  max_iteration_per_run, callbacks); `run(msg)` sends the message and calls
  conv.run().
- **Events out**: SDK events consumed by existing callbacks; viz events are
  engine-level (viz_bus), not conversation internals.
- **Final response**: get_agent_final_response(conv.state.events) read before
  conv.close() (existing _final_agent_text).
- **Finish**: SDK FinishTool -> FINISHED.
- **Nudge**: existing _run_finalize_loop (finalize_nudge_turns) via the
  _FinalizeNudgeController callbacks, unchanged.
- **Infra failure**: sandbox.infra_error re-raised -> STUCK; LAST_ERROR file
  written on exception.

### 3.2 Claude (claude-agent-sdk subprocess)

- **Message in**: `run(msg)` starts the SDK async query in a worker thread
  (shared async-to-sync bridge). Options: system_prompt = the same engine step
  prompt OpenHands gets, plus a note that it runs inside the agentic dev loop;
  allowed_tools = catalog MCP tool names; setting_sources = []; max_turns =
  budget; cwd = host workspace path (brain on host, as today); env = minimal,
  no secrets; can_use_tool = locked allowlist; permission_mode = deny
  non-allowlisted tool use (no interactive prompts).
- **Events out**: SDK async messages: tool_use -> bridge -> Sandbox.run ->
  tool result returned to the harness; assistant text -> stream buffer.
- **Final response**: last assistant text from the SDK final result (per
  driver extraction, replacing get_agent_final_response for this path).
- **Finish**: catalog finish tool call marks done; stream end also ends the
  run.
- **Nudge**: a new SDK query continuation (session resume) with the finalize
  prompt.
- **Failure**: process exit without a normal close -> STUCK (infra
  classifier), no flap; max_turns exhausted -> TRUNCATED.

### 3.3 Copilot (codex/copilot SDK subprocess)

- **Message in**: `run(msg)` spawns the Codex CLI via the SDK, powered by the
  Copilot subscription auth (device flow established at setup); no project
  config, no custom tools; the engine MCP server is the only tool surface.
- **Events out**: tool calls via MCP -> bridge -> Sandbox.run; observations
  formatted as MCP ToolResult.
- **Final response**: last assistant message of the turn.
- **Finish**: catalog finish tool call; turn end also ends the run.
- **Nudge**: continuation turn with the finalize prompt.
- **Failure**: subprocess death -> STUCK; turn/budget limit -> TRUNCATED.

## 4. Accepted Trade-offs and Risks

- **Trade-off**: Claude/Copilot agents lose native file-tool polish
  (uniformity chosen over per-harness tools; the read-only-tools hybrid was
  retracted). Mitigation: the engine file lane (host-side) covers the same
  need via the file_editor catalog tool.
- **Risk (policy)**: Anthropic policy gray area for the claude provider.
  Accepted per Q4 with a documented risk note; distinct from the banned
  spoofing pattern; OpenCode ships the same architecture.
- **Risk (SDK churn)**: claude-agent-sdk and openai-codex move fast.
  Mitigation: pinned extras, COMPAT-SHIMS.md entries, version-guard + canary
  tests.
- **Risk (budget semantics)**: subscription metering is a monthly Agent SDK
  credit for claude, not unlimited. Documented in the risk note; engine shows
  TRUNCATED when the budget is exhausted.

## 5. Status-Mapping Walkthrough (full)

| Scenario | openhands | claude | copilot | RuntimeStatus |
|---|---|---|---|---|
| Normal completion | execution_status FINISHED (finish tool) | query returns, finish signaled | turn ends with finish | FINISHED |
| Agent-side error | execution_status ERROR | SDK/CLI exception | SDK exception | ERROR |
| Sandbox infra error | infra_error re-raised | bridge returns infra error | bridge returns infra error | STUCK |
| Harness dies mid-step | n/a | subprocess exit without close | subprocess exit without close | STUCK |
| Budget exhausted | max_iteration_per_run reached (today: status != FINISHED -> truncated) | max_turns reached | turn limit reached | TRUNCATED |
| Engine-side failure | LAST_ERROR file | LAST_ERROR file | LAST_ERROR file | ERROR + artifact policy |

Engine-wide rules that stay constant across all providers:

- `truncated = status != FINISHED` (existing CapturedOutput policy).
- STUCK never auto-retries the same step (no-flap, 030 policy).
- Viz events flow only through on_terminal_result on the bridge; viz is
  provider-agnostic.
- Sandbox is ALWAYS ON: no provider gets a path that bypasses Sandbox.run.

## 6. Extension Compliance (for this unit)

- Domain Standards: applies to new domain terms (provider profile, runtime,
  catalog) - to be enforced in Functional Design domain modeling.
- Resiliency Baseline (scoped, directional): no-retry-on-STUCK and
  fail-fast paths above are the applicable parts; server load/HA/DR N/A.
- Security Baseline: FR-10.9 is the security core of the unit.
- Property-Based Testing (partial): serialization round-trips for the new
  config variants and RuntimeResult (pure data) qualify.

## 7. Next Steps

1. User review of this requirements document (approval gate).
2. Workflow Planning: stage selection for the unit (expected: Functional
   Design, NFR Requirements, Infrastructure Design, Code Generation staged in
   3 parts, Build and Test; User Stories skipped per Q6).
3. Functional Design deepens sections 3 and 5 into per-provider sequence
   models and business rules.
