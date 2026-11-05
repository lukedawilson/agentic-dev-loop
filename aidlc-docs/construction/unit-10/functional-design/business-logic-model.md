# Business Logic Model - Unit 10 (Multi-Provider Runtime)

How the engine selects, constructs and drives a provider runtime. Companion
artifacts: `domain-entities.md` (the entities), `business-rules.md` (the
rules). Requirements: FR-10.1..10.13.

## 1. Runtime factory and selection

`runtimes/factory.py::build_runtime(cfg, runtime_context) -> AgentRuntime`:

1. Read `cfg.provider` (default `openhands`).
2. `openhands` -> construct `OpenHandsRuntime`. No import of the optional
   driver packages.
3. `claude` / `copilot` -> import the driver package. ImportError because the
   optional extra is not installed -> fail fast naming
   `pip install agentic-dev-loop[claude]` (resp. `[copilot]`).
4. The engine constructs exactly one runtime per `run_agent` call, closes it
   in `finally` (parity with today's `conv.close()`).

The step runner (`stages.py Runtime.run_agent`) becomes a thin dispatcher:
resolve `RuntimeContext` (already step-resolved today), build the runtime,
call `run(message, nudge_policy=...)`, translate `RuntimeResult` to
`AgentResult`, close. All policy that exists today (per-attempt hygiene,
verdict parsing, retry classification, missing-dependency fail-fast, notes,
artifact context) stays in the graph/stage layer, untouched and
provider-independent.

## 2. OpenHandsRuntime - a pure move

The body of today's `Runtime.run_agent` (stages.py lines 135-278) and
`_run_finalize_loop` (250-280) moves into
`runtimes/openhands_runtime.py` with behavior IDENTICAL, including:

- per-step LLM selection (054: named `llms:` profile else `llm:` block; step
  `reasoning_effort` override via `build_llm` - OpenHands-only)
- `load_agent` / `load_brief_agent` (agent body as system-message suffix,
  brief agent for `brief_inputs` steps)
- `register_sandbox_terminal` per run (KD-4) with the same resolved args
- `Conversation`, `send_message`, `run()` or the finalize nudge loop
- the 030 sticky-infra check (`sandbox.infra_error` -> raise, no retry)
- `truncated = status != FINISHED`
- capture via `CapturedOutput.from_agent_result` (unchanged: extraction from
  `conv.state.events` BEFORE `conv.close()`, write, redaction,
  "empty_response" capture_error)

Mapping to RuntimeResult: FINISHED -> FINISHED, ERROR -> ERROR, STUCK ->
STUCK; a budget-exhausted finalize loop (the existing
`turns >= max_iterations` branch setting ERROR) maps to TRUNCATED. NudgePolicy
renders through the existing `_FinalizeNudgeController` with the same 035
template and same turn-index arithmetic. `close()` = `conv.close()`.

The move is mechanical: the step-runner surface it replaces keeps the exact
`AgentResult` shape and field semantics, so the graph layer cannot tell.

## 3. ClaudeAgentRuntime

Driven via claude-agent-sdk as a subprocess harness (the real `claude` CLI),
async, with the shared async-to-sync bridge (`runtimes/async_bridge.py`: one
worker thread per `run()`, exceptions re-raised on the caller side).

Construction / harness policy (FR-10.9):

- `setting_sources=[]`: no project or user settings, skills, memory, hooks
  load (hooks are host-side code execution risk - this is the non-negotiable
  hardening from the design).
- `allowed_tools` = exactly the catalog MCP tool names (`terminal`,
  `file_editor`, `task_tracker`); `permission_mode="deny"` as defense in
  depth (allowed tools still run; anything else is refused - verified by a
  test in Code Generation part 2).
- `mcp_servers` = [the engine-internal stdio MCP server].
- `system_prompt` = the engine agent context (agent body as today, plus the
  shared engine sections: error guide, dependency policy, expected output
  paths) appended to Claude Code's built-in prompt, with a note that the
  harness runs inside the agentic dev loop.
- `cwd` = workspace; env = the minimal explicit set (never pipeline secrets).
- Auth: `login: subscription` uses the user's own `claude login` session
  (presence detected; the engine never reads or replays the credential
  store); `login: key` passes `ANTHROPIC_API_KEY` from the engine environment
  (fail fast if absent).

Run mechanics (`run(message, nudge_policy)`):

- Worker thread runs the SDK `query()`; tool_use events route to the bridge
  (section 5); the turn count is monitored from the result stream.
- `max_turns` = `ClaudeAgentConfig.max_turns` else the step's effective
  `max_iterations`.
- Nudge: at turn `max_turns - turns_before_end`, inject the rendered 035
  message as a session continuation (same text as OpenHands today).
- Final text: the harness result's final assistant message.
- Status mapping: normal end -> FINISHED; SDK/API-level error -> ERROR;
  harness subprocess death -> STUCK; budget (max_turns) exhausted ->
  TRUNCATED.
- Capture: `CapturedOutput.from_runtime_result(final_text, artifact,
  state_dir, redactions)` (shared write + redaction internals with
  `from_agent_result`).

## 4. CopilotRuntime

Driven via openai-codex as a subprocess harness, turn-based, same
async-to-sync bridge.

Construction / harness policy (FR-10.9):

- No project config load, no custom tools: the only tool channel is our MCP
  server (MCP config injected per session).
- Auth: engine-hosted GitHub device flow (FR-10.10, Q3=A). Token cached in
  `~/.agentic-dev-loop/auth/copilot.json`; missing/expired -> the engine runs
  the device flow (prints the user code and URL), polls, stores, proceeds.
  The token reaches the harness session through the SDK auth surface only;
  it is never in the harness process env as a free variable, never in
  pipeline yaml, never printed, redacted in all logs.
- `model` = `CopilotConfig.model` or the harness default.

Run mechanics: mirror of the claude driver: task message per turn, tool calls
routed to the bridge, turn budget from `max_iterations`, same nudge injection
policy, final text from the session's final answer, same status mapping
(subprocess death -> STUCK, budget exhausted -> TRUNCATED), same
`from_runtime_result` capture.

## 5. The bridge execution path (claude / copilot)

Every tool call from either harness takes one path:

1. Harness -> MCP stdio server -> thin tool handler.
2. Handler -> `SandboxToolBridge`.
3. Bridge -> `SandboxExecutionRequest` -> `sandbox.run(request)` (the SAME
   Docker kernel-level sandbox path as today: git guard, allowlist, egress
   policy, mounts, timeout, audit metadata).
4. Result -> `TerminalOutcome` (infra-error classified) -> per-harness
   observation -> harness.
5. Every terminal action emits `on_terminal_result` to the viz bus (unit-9),
   identical to SandboxTerminalExecutor today.

The agent brain stays on the host (the harness process), as the SDK agent
does today; everything the agent EXECUTES goes through the sandbox. Docker
sandboxing is preserved for all three providers (the design's non-negotiable,
FR-10.7).

## 6. Step-runner translation (RuntimeResult to AgentResult)

| RuntimeResult | AgentResult |
| --- | --- |
| `status == FINISHED` | `ok=True, truncated=False, response=final_text` |
| `status in (ERROR, STUCK, TRUNCATED)` | `ok=True, truncated=True, response=final_text or None` |
| `capture_error` set | `ok=True, truncated=<derived>, capture_error=...` (existing unclear-retry channel) |
| exception during `run()` | `ok=False, truncated=False` + last-error.md (unchanged) |
| `sandbox.infra_error` (030) | raise, run fails with no retry (unchanged, all providers) |

The engine-wide rules survive unchanged: `truncated = status != FINISHED`;
truncated runs feed the existing unclear-verdict retry (attempt-bounded) with
the captured text in the note; sticky infra failures never retry.

## 7. Status mapping walkthrough (summary)

Full table in requirements section 5. Core invariant: every provider's
terminal state lands in exactly one RuntimeStatus; the engine above the
protocol only ever sees RuntimeResult, never provider internals.

- OpenHands: SDK statuses map 1:1 (ERROR budget case -> TRUNCATED).
- claude / copilot: harness process death -> STUCK (retryable, attempt-bounded,
  same as today's OpenHands stuck detection); budget exhaustion -> TRUNCATED;
  harness/API errors -> ERROR.
- All providers: a step verdict still parses from `final_text` regardless of
  truncated status (verdict-first truncation policy unchanged).
