# Code Generation Plan - Unit 10 (multi-provider runtime)

Status: in progress. ONE continuous stage (supersedes the 3-part staging per user restructure "A", 2026-11-05: "Why is the code generation split into 3 parts like this? Each part is useless"). De-risking value of the old part-1 gate preserved as commit-level green checkpoints. After Code Generation = the global Build and Test gate.

## Stage context

FD, NFR Requirements and Infrastructure Design are approved. The implementation sequence below is TDD throughout (red -> green -> refactor; repo rule). Mocks only at the SDK subprocess boundaries; runtimes get interface-level fakes (unit-3 FD-5 pattern); the bridge is tested against the real Sandbox request path. All config/default behavior must satisfy BR-10.1 rule zero: a pipeline with no `provider:` key behaves byte-identically to today.

## Dogfood pipeline compatibility (--aidlc-unit)

The `agentic-dev-loop/pipeline.yaml` dogfood loop implements this plan through itself, invoked as `agentic-dev-loop agentic-dev-loop/pipeline.yaml --aidlc-unit unit-10` (or bare run with Current Stage autodetect). Hard constraints:

- **The aidlc loader extension stays untouched.** `src/agentic_dev_loop/engine/extensions/aidlc.py` is a document loader feeding `build_context` - it is provider-agnostic and must not change. `tests/test_aidlc.py`, `tests/test_loaders.py`, and the `--aidlc-unit` e2e fixture (`tests/test_e2e_fixtures.py`) must stay green through every commit, including the pure move.
- **This plan is the loop's build spec.** The loader resolves `aidlc-docs/construction/unit-10/**` plus `plans/unit-10-code-generation-plan.md` (bare-shape glob). The plan must remain precise enough for an agent to implement from. Later amendments to this plan file must not break the plans glob (keep the filename `unit-10-code-generation-plan.md`).
- **The dogfood pipeline stays on openhands.** `agentic-dev-loop/pipeline.yaml` has no `provider:` key and keeps its `llm:`/`llms:`/`reasoning_effort` usage; rule zero guarantees it runs unchanged. Do NOT add a `provider:` key to it.
- **Checkpoint 2/3 live gates use a scratch pipeline.** A committed template (under `aidlc-docs/construction/unit-10/code/`) with `provider: claude` / `provider: copilot`, NO llm fields (forbidden there), and the same step shape, gets copied to a scratch dir for live harness runs. The template is documentation (aidlc-docs is docs-only), not application code.
- **The `Current Stage` line is load-bearing.** Bare-run autodetect parses `Current Stage ... Unit <slug>` from `aidlc-docs/aidlc-state.md`; the slug must stay the directory name `unit-10`. Any state-file edit must preserve that substring.
- **Every checkpoint gate below runs the dogfood pipeline exactly as the operator does** (`--aidlc-unit unit-10`), not just the pytest suite.

## Execution Checklist

### Checkpoint 1 - abstraction + pure move (commit lands with full suite green, zero behavior delta)

- [ ] 1. `config.py`: `provider:` key (openhands default) + `Provider` literal + `ClaudeAgentConfig` / `CopilotConfig` models + strict validation (BR-10.2: unknown provider fails; block not matching provider fails; `llm:`/`llms:`/step `llm:`/`reasoning_effort` forbidden when provider != openhands; `llm:` required for openhands). TDD: validation-failure tests red first.
- [ ] 2. `engine/runtimes/protocol.py`: `RuntimeStatus` (FINISHED|ERROR|STUCK|TRUNCATED), `RuntimeResult` (status, final_text, truncated derived = status != FINISHED, capture_error), `NudgePolicy` (turns_before_end, 035 template), `AgentRuntime` (run(message, *, nudge_policy=None) -> RuntimeResult; close()), `RuntimeContext` (resolved bag per FD domain-entities.md).
- [ ] 3. `engine/runtimes/catalog.py`: catalog definition - `terminal`, `file_editor`, `task_tracker` (MCP-exposed), `finish` (harness-native); `CatalogToolSpec` (name, description, JSON Schema).
- [ ] 4. `engine/runtimes/bridge.py`: `SandboxToolBridge` - terminal -> `SandboxExecutionRequest` -> `sandbox.run` (mirrors `SandboxTerminalExecutor` KD-4 semantics: forbidden-command matching, infra-error classification 030, terminal_timeout, per-step secrets env); file lane (read/write/edit with today's `FileEditorTool` host-side path rules); task tracker in-memory store; `on_terminal_result` -> viz events. Tested against the real Sandbox path.
- [ ] 5. `outputs.py`: `CapturedOutput.from_runtime_result` (shared redaction + artifact write).
- [ ] 6. `engine/runtimes/openhands_runtime.py`: PURE MOVE of `stages.py` `Runtime.run_agent` body (lines 135-278) + `_run_finalize_loop` (250-280) + `_FinalizeNudgeController` (054 llm selection, `load_agent`/`load_brief_agent`, `register_sandbox_terminal`, `Conversation`, infra raise, truncated rule, `from_agent_result` capture, ERROR-budget -> TRUNCATED). No behavior change.
- [ ] 7. `engine/runtimes/factory.py`: `build_runtime(cfg, ctx)` dispatch + missing-extras fail-fast naming `pip install agentic-dev-loop[claude]` / `[copilot]`.
- [ ] 8. `stages.py`: `Runtime.run_agent` becomes a thin dispatcher (resolve `RuntimeContext`, build runtime, run, translate `RuntimeResult` -> `AgentResult` per the FD translation table, close in finally). All graph-layer policy untouched.
- [ ] 9. `tests/test_module_contract.py`: extend for the `runtimes` package if it pins layout (BR-10.10).
- [ ] CHECKPOINT 1 GATE: full suite green (including `test_aidlc.py`, `test_loaders.py`, `test_e2e_fixtures.py`); zero behavior delta (existing tests unchanged except intentional contract updates); dogfood run `agentic-dev-loop agentic-dev-loop/pipeline.yaml --aidlc-unit unit-10` completes byte-identically to pre-change behavior.

### Checkpoint 2 - claude driver

- [ ] 10. `engine/runtimes/mcp_server.py`: first-party stdio MCP server (JSON-RPC over stdio: initialize, tools/list, tools/call only; stdlib-only per NFR-2=A). Contract tests driving it as a real stdio child process.
- [ ] 11. `engine/runtimes/async_bridge.py`: shared async-to-sync bridge (worker thread, exception propagation, clean shutdown).
- [ ] 12. `engine/runtimes/claude_runtime.py`: claude-agent-sdk session - `setting_sources=[]`, `allowed_tools` = the three MCP tool names, `permission_mode=deny` (defense in depth), `mcp_servers` = engine stdio server, `system_prompt` = engine agent context appended to Claude Code's built-in prompt + runs-in-engine note, `cwd` = workspace, explicit minimal env, `max_turns` = config else step max_iterations, nudge injected at `max_turns - turns_before_end` via session continuation, final text from harness result, status mapping (normal -> FINISHED, SDK error -> ERROR, subprocess death -> STUCK, budget -> TRUNCATED), capture via `from_runtime_result`.
- [ ] 13. Claude auth detection: `~/.claude/.credentials.json` presence probe + fail-fast guidance (subscription mode); `ANTHROPIC_API_KEY` required + fail-fast (key mode). Detection never reads or replays the login (BR-10.6).
- [ ] 14. `COMPAT-SHIMS.md` + `tests/test_sdk_compat.py`: claude-agent-sdk entries (version guard + canary).
- [ ] CHECKPOINT 2 GATE: contract tests prove the constructed harness config (exact args, no network); `live_harness` opt-in suite run manually with a real subscription; scratch-pipeline end-to-end step (template from `aidlc-docs/construction/unit-10/code/`, `provider: claude`, no llm fields); container audit proves only bridge-issued execs reach the sandbox.

### Checkpoint 3 - copilot driver

- [ ] 15. `engine/runtimes/copilot_auth.py`: direct GitHub device flow (stdlib urllib, POST github.com/login/device/code with pinned partnership client_id constant asserted by a test, poll token endpoint, token -> `~/.agentic-dev-loop/auth/copilot.json` mode 600; redacted in all logs per NFR-3=A).
- [ ] 16. `engine/runtimes/copilot_runtime.py`: openai-codex session - no project config, no custom tools, MCP-only, turn-based, same nudge/status-mapping/capture rules.
- [ ] 17. `COMPAT-SHIMS.md` + `tests/test_sdk_compat.py`: openai-codex entries.
- [ ] CHECKPOINT 3 GATE: same procedure as checkpoint 2 (contract tests, live_harness manual, scratch-pipeline e2e with `provider: copilot`, container audit).

### Packaging + final gate

- [ ] 18. Scratch pipeline templates committed under `aidlc-docs/construction/unit-10/code/` (one per subprocess provider; `provider:` key set, NO llm fields, same step shape as the dogfood pipeline).
- [ ] 19. `pyproject.toml`: `[project.optional-dependencies]` `claude` / `copilot` floor pins (ID-1=A); CI tiers per infrastructure-design.md (base job extras-free; compat/contract job installs `.[claude,copilot]`; ubuntu + windows).
- [ ] 20. BUILD AND TEST GATE: full suite green (incl. docker-marked + `test_repo_ascii.py` + the aidlc loader tests), dogfood run via `--aidlc-unit unit-10`, live_harness manual smoke with real subscriptions, COMPAT-SHIMS registry current.

## Open Design Decision

### Q1 - Implementation mode (ASK BEFORE CODING - repo rule: user drives by default)

Who writes the code?

A) **The dogfood loop implements it** (unit-5 precedent): the plan is the loop's build spec via `agentic-dev-loop agentic-dev-loop/pipeline.yaml --aidlc-unit unit-10`.

B) **You implement inline in this session**, TDD, checkpoint by checkpoint, running the suite at each gate; I present checkpoint results as we go. (This answer is the explicit inline-implementation approval the repo's ASK-BEFORE-CODING rule requires.)

C) **User-driven outside AI-DLC sessions** (unit-9 precedent): the user implements, the agent stays available for assistance and review.

X) Other

[Answer]: SUPERSEDED (2026-11-05, user): "I'll be running the dogfood loop in my terminal!" - the implementation mode is A (the dogfood loop implements it, unit-5 precedent), with the user operating the loop runs in their terminal. The QA split still applies: the loop's own dev/test/review/qa steps build and probe the plan; the subscription-dependent gates (live_harness smoke, scratch-pipeline e2e, container audit) remain user-run with real subscriptions.
