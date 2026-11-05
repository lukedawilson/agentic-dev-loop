# Functional Design Plan - Unit 10 (Multi-Provider Runtime)

Unit intent: `aidlc-docs/unit-10-intent-provider-abstraction.md` (approved brainstorm design, verified external facts, concept-mapping table, codebase anchor map)
Requirements: `aidlc-docs/inception/requirements/unit-10-requirements.md` (FR-10.1..10.13, NFR-10.1..10.7, section 3 data flows, section 5 status walkthrough)
Normative design: the brainstorm design (recorded in the intent doc); no separate Application Design artifact exists.

## Execution Checklist

- [x] Analyze unit context (intent doc, requirements, current code: config.py schema, stages.py Runtime.run_agent, agents.py tool registration, outputs.py capture, isolation.py request path)
- [x] Resolve open design decisions Q1-Q3 (answers recorded below)
- [x] Generate `aidlc-docs/construction/unit-10/functional-design/domain-entities.md` - AgentRuntime protocol entities (RuntimeResult, RuntimeStatus, NudgePolicy), provider config models (ClaudeAgentConfig, CopilotConfig), tool catalog entities, bridge entities
- [x] Generate `aidlc-docs/construction/unit-10/functional-design/business-logic-model.md` - runtime factory/selection, per-provider run mechanics (openhands pure move, claude SDK session, copilot session), bridge execution path, capture path (from_agent_result vs from_runtime_result), nudge mechanics per provider, status mapping
- [x] Generate `aidlc-docs/construction/unit-10/functional-design/business-rules.md` - config validation rules (provider key placement, strict per-provider blocks, llm-block rules per provider), harness hardening rules (setting_sources, allowed_tokens, no project config, secrets policy), fail-fast rules (missing package, missing login), no-flap STUCK rules, backward-compat rule zero
- [x] Present Functional Design completion message (standardized 2-option format) and await approval

## Stage Outcome

Approved by user 2026-11-05 ("B" - Continue to Next Stage: NFR Requirements). No change requests.


## Open Design Decisions

### Question 1
Where does the provider selection live in the config, and what happens to the OpenHands-only fields (`llm:` block, `llms:` profiles, per-step `llm:` refs, `reasoning_effort`) when another provider is selected?

A) A new top-level `provider:` key on the pipeline config (values `openhands` (default) | `claude` | `copilot`) selects the provider for the WHOLE pipeline. One provider config block per provider (`claude:` / `copilot:`); a block that does not match `provider` fails at load. When `provider` is not `openhands`, the `llm:` block, `llms:` profiles, per-step `llm:` refs and `reasoning_effort` are FORBIDDEN (fail loud as dead config); `llm:` stays required for `openhands` (zero change for existing pipelines). (Recommended)

B) Provider selection per `llms:` profile, so different steps can mix providers; the `llm:` block keeps its role for openhands steps.

C) Other

[Answer]: A - New top-level provider: key on the pipeline config (openhands (default) | claude | copilot), whole-pipeline selection; one provider config block per provider, mismatched block fails at load; llm/llms/per-step llm refs/reasoning_effort forbidden when provider != openhands, llm required for openhands. (user answer 2026-11-05)

### Question 2
How do the subprocess harnesses (claude-agent-sdk, openai-codex) see the engine's tool catalog (terminal, file_editor, task_tracker, finish)?

A) The engine hosts a local stdio MCP server per run (engine-internal, never network-exposed) that implements the catalog; the SDK config points the harness at it (`mcp_servers` option for claude; MCP config for codex), with `allowed_tools` locked to exactly the four catalog tool names and the harness's native tools off. One implementation serves both harnesses. (Recommended)

B) Per-SDK native tool definitions (claude @tool decorators / codex custom tools), duplicating the binding code per SDK; no MCP server component.

C) Other

[Answer]: A - Engine hosts a local stdio MCP server per run implementing the catalog; SDK config points the harness at it (mcp_servers for claude, MCP config for codex), allowed_tools locked to catalog names, harness native tools off. User asked why MCP at all; answered in chat: codex CLI receives external tools only via MCP config (required for codex); claude has an in-process @tool alternative but dual implementation would duplicate the catalog, two observation formats, two bridge hook points, and drift risk - one stdio server keeps one catalog implementation and one choke point at the bridge. Refinement recorded: finish stays harness-native (stream end / turn end), so the MCP-exposed set is terminal, file_editor, task_tracker. (user answer 2026-11-05)

### Question 3
Where does the Copilot device-flow token live between runs?

A) User-level auth store under `~/.agentic-dev-loop/auth/` (engine state, outside the workspace), reused across runs; on a missing/expired token the engine re-runs the device flow. Token never written into pipeline yaml, never printed, redacted in all logs. (Recommended)

B) Per-run state dir (re-auth via device flow on every run; no persistence between runs).

C) Other

[Answer]: A - User-level auth store under ~/.agentic-dev-loop/auth/ (engine state, outside the workspace), reused across runs; missing/expired token re-runs the device flow; token never in pipeline yaml, never printed, redacted in all logs. (user answer 2026-11-05)

## Artifact References

- Config schema: `src/agentic_dev_loop/core/config.py` (LlmConfig line 598; PipelineConfig line 611; `profile:` = 055 toolchain profile, name collision to avoid)
- Step-runner body to move: `src/agentic_dev_loop/engine/stages.py` Runtime.run_agent (lines 135-278) + `_run_finalize_loop` (250-280) + `_FinalizeNudgeController` (97-111)
- Tool registration today: `src/agentic_dev_loop/engine/agents.py` (register_sandbox_terminal, KD-4)
- Capture today: `src/agentic_dev_loop/engine/outputs.py` (CapturedOutput.from_agent_result)
- Sandbox request path: `src/agentic_dev_loop/engine/isolation.py` (SandboxExecutionRequest/Result, infra_error classifier 030)
- Proposed package: `src/agentic_dev_loop/engine/runtimes/` (protocol, openhands_runtime, claude_runtime, copilot_runtime, bridge, catalog, async_bridge, factory)
- Compat registry: `COMPAT-SHIMS.md` + `tests/test_sdk_compat.py` (version-guard + canary pattern to extend)
