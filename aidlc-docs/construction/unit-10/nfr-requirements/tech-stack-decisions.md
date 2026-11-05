# Unit 10 Tech Stack Decisions - multi-provider runtime

Stage: NFR Requirements. Each decision records the choice, the rationale, and the compatibility posture.

## TS-1 - Provider selection: config-level `provider:` key

- **Decision**: A new top-level `provider:` key on the pipeline config (`openhands` default | `claude` | `copilot`), whole-pipeline selection; one strict per-provider config block (`claude:` / `copilot:`); `llm:`-family fields forbidden on non-openhands providers (FD Q1=A).
- **Rationale**: Rule zero (absent key = byte-identical today) keeps every existing pipeline valid; whole-pipeline selection matches the subscription-sharing motivation without a per-step provider matrix.
- **Compat posture**: additive schema change; strict validation (extra=forbid) at every level, matching the repo's load-time fail-fast convention.

## TS-2 - Tool delivery: first-party minimal stdio MCP server

- **Decision**: The engine ships a small stdlib-only MCP server (JSON-RPC over stdio; initialize, tools/list, tools/call only; no resources/prompts) exposing exactly the catalog tools `terminal`, `file_editor`, `task_tracker` (`finish` stays harness-native). Both harness SDKs point `mcp_servers`/MCP config at it (NFR-2=A).
- **Rationale**: codex receives external tools only via MCP config; a single implementation keeps one catalog contract and one observation format for both harnesses; stdlib-only preserves the zero-core-dependency runtime stance (unit-9 NFR-9.1 precedent).
- **Compat posture**: the MCP surface is a closed, engine-owned subset. A contract test asserts the tools/list response exactly matches the catalog spec, so SDK-side drift shows up as a version-guard failure, not a silent tool gap.

## TS-3 - Claude harness: claude-agent-sdk subprocess

- **Decision**: `claude-agent-sdk` as an optional extra `[claude]`; the driver constructs an SDK session with `setting_sources=[]`, `allowed_tools` = the three MCP catalog tools, `permission_mode="deny"`, the engine's stdio MCP server as the only MCP server, engine agent context appended to Claude Code's built-in system prompt, cwd = workspace, minimal explicit env. Auth: subscription mode = the user's own `claude login` (Anthropic's flow), detected via `~/.claude/.credentials.json` presence; key mode = `ANTHROPIC_API_KEY` from the engine env.
- **Rationale**: runs the real Claude Code engine per the verified OpenCode pattern; the engine never reads or replays the saved login; policy gray area documented (requirements section 4 risk note, user-approved Q4=A to ship).
- **Compat posture**: floor version pin in the extra + COMPAT-SHIMS entry + version-guard/canary tests (`tests/test_sdk_compat.py` pattern). Contract tests assert the exact constructor arguments.

## TS-4 - Copilot harness: openai-codex SDK subprocess + direct device flow

- **Decision**: `openai-codex` as an optional extra `[copilot]`; MCP-only tools, no project config, no custom tools; turn-based session. Auth: the engine hosts GitHub's device flow directly over stdlib urllib (POST `github.com/login/device/code` with the partnership client_id constant, poll the token endpoint); token stored at `~/.agentic-dev-loop/auth/copilot.json` (user-level, mode 600, outside the workspace) and re-flown on expiry (NFR-3=A, FD Q3=A).
- **Rationale**: no new dependency for auth; the device flow is the official subscription path (GitHub changelog 2026-01-16); the token never touches pipeline yaml or logs.
- **Compat posture**: floor version pin + COMPAT-SHIMS entry + version-guard/canary tests; the client_id is a pinned constant with a test asserting the value; token file in the redaction set.

## TS-5 - Async bridge: one worker thread per run, stdlib only

- **Decision**: A shared `async_bridge` utility runs the async SDK query in a worker thread and blocks the engine's synchronous step runner, propagating results and exceptions across the boundary; `close()` joins the thread.
- **Rationale**: the engine's step runner is synchronous; no async rewiring of the graph layer; no new dependencies (stdlib threading).
- **Compat posture**: thread lifecycle covered by tests (no leaks on close, exception propagation, fresh session per attempt).

## TS-6 - Capture: from_runtime_result shared, from_agent_result retained

- **Decision**: `CapturedOutput` gains `from_runtime_result(final_text, artifact, state_dir, redactions)` sharing the write + redaction internals; `from_agent_result(conv, ...)` stays for the OpenHands extraction (get_agent_final_response before close).
- **Rationale**: zero behavior delta for OpenHands; one redaction/write path for everything.

## TS-7 - Testing: contract tests + opt-in live smoke

- **Decision**: CI runs pure-function, fake-runtime, config-validation, and contract tests against real SDK objects (no network). Live harness smoke (hardening refusal probe + end-to-end runs) is a marked opt-in suite executed with real subscriptions at the dogfood gate, never silently skipped (NFR-4=A).
- **Rationale**: CI cannot hold subscriptions; the hardening guarantee must still be proven by construction plus contract assertions, and demonstrated live at the gate.

## TS-8 - Dependency footprint

- **Decision**: The core package gains zero new required dependencies. Only the two optional extras add SDK packages. The MCP server and device-flow client are stdlib-only.
- **Rationale**: light installs stay light; every optional path fails fast with install guidance when configured but absent (NFR-10.7).
