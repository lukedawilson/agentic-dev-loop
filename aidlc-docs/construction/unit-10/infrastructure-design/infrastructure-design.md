# Unit 10 Infrastructure Design - multi-provider runtime

Stage: Infrastructure Design. Sources: FD (business-rules.md BR-10.1..10.11), NFR Requirements (NFR-10.1..10.7, tech-stack-decisions.md TS-1..TS-8), open-question resolutions ID-1..ID-2 (both A, 2026-11-05).

## Scope statement

Unit-10 adds NO new container machinery and NO new network enforcement. The subprocess harnesses (claude CLI via claude-agent-sdk, codex CLI via openai-codex) run on the HOST as ordinary child processes of the engine; every command they execute routes through the existing unit-3/5 Docker sandbox path via the bridge. Existing infrastructure (sandbox containers, egress proxy, state dir, seccomp profiles, CI ubuntu + windows) is reused unchanged.

What IS new infrastructure:
1. Optional-extras packaging and their installation into dev/CI environments (ID-1=A).
2. The auth/credential store layout (`~/.agentic-dev-loop/auth/`, claude credential detection).
3. CI tiers for contract tests (always-on, with extras) versus live smoke (opt-in, manual).
4. The engine-internal MCP stdio server lifecycle (spawn, handshake, per-run scoping, reaping).
5. The dogfood-gate procedure for parts 2/3 including the live hardening smoke (ID-2=A).

## ID-1 (resolved A): Optional extras and CI installation

- `pyproject.toml` gains `[project.optional-dependencies]`:
  - `claude = ["claude-agent-sdk>=<floor>"]`
  - `copilot = ["openai-codex>=<floor>"]`
- Floor pins follow the COMPAT-SHIMS registry discipline: each floor recorded in `COMPAT-SHIMS.md` with the tested SDK version range, the version-guard test, and the canary test (the existing `tests/test_sdk_compat.py` pattern).
- CI layout:
  - Base CI job: installs the package WITHOUT extras (light-install path preserved; provider-absent behavior fully covered).
  - Compat/contract CI job: installs `.[claude,copilot]`; runs `tests/test_sdk_compat.py` plus the new contract tests that construct real SDK objects and assert the exact driver arguments (no network, no subscriptions).
  - Both jobs run on ubuntu and windows (unit-3 ID-6 precedent): the harness-spawn code must be cross-platform (explicit argv lists, no shell metacharacters, no POSIX-only paths).
- Fail-fast behavior (NFR-10.7): a configured provider whose extra is missing fails at load with `pip install agentic-dev-loop[claude]` (resp. `[copilot]`) guidance.

## ID-2 (resolved A): Live hardening smoke placement

- A `live_harness` pytest marker defines the opt-in suite: harness-refusal probes (a claude/codex session attempts a native tool call and must be refused) and end-to-end runs (an agent step through the bridge, sandbox-gated terminal + file lane + task tracker).
- The suite skips unconditionally unless `RUN_LIVE_HARNESS=1` is set in the environment; CI never sets it, so CI never silently skips or runs it (no skip-marked tests outside this marker for these behaviors).
- The part-2 (claude) and part-3 (copilot) dogfood-gate procedures each include a step: run the live_harness suite manually with a real subscription before the part closes, and record the outcome in the gate checklist.

## Auth and credential store layout

- `~/.agentic-dev-loop/auth/copilot.json` - the device-flow token (user-level, mode 600, outside the workspace, never mounted into any sandbox). Expiry check at run start; expired/absent -> device flow re-runs. The token joins every redaction set for the run.
- Claude subscription mode: detection via `~/.claude/.credentials.json` presence only. The engine never reads or replays the credential; the SDK consumes it. Absent -> fail fast with `claude login` guidance (BR-10.6).
- Claude key mode: `ANTHROPIC_API_KEY` read from the engine's own env, passed to the harness process env ONLY as the harness credential; absent -> fail fast naming it.
- Pipeline secrets remain sandbox-only via `step_secret_env` (unchanged); the harness env is constructed explicitly (minimal) so pipeline secrets and sandbox env never leak into the harness process (BR-10.5).

## MCP server lifecycle

- Per run_agent call the engine spawns one stdio MCP server child (engine code, stdlib-only, JSON-RPC over stdio: initialize, tools/list, tools/call).
- The server is a thin adapter: it holds no engine state; every tools/call is forwarded over an in-process channel to the engine's SandboxToolBridge, which executes on the Sandbox.run gate and returns the TerminalOutcome.
- Lifecycle: spawn with explicit argv and cwd; handshake with a timeout (fail -> harness session error -> STUCK/ERROR mapping); stdin/stdout closed and the child reaped in `close()`; no ports, no network listeners (nothing to expose; a test asserts the server binds no socket).
- One server per harness session; a crashed server surfaces as a harness tool error (not a hang): the async bridge times out tool round trips and classifies the failure (BR-10.8).

## Harness process posture

- Both harnesses spawn via their SDKs with the engine workspace as cwd, explicit minimal env, and the SDK's documented options only (setting_sources=[], allowed_tools, mcp_servers, permission_mode for claude; MCP-only, no project config, no custom tools for codex).
- Model-channel egress: the harness process makes its own host-side network calls to the vendor APIs (Anthropic / GitHub) - same posture as the engine's own LLM calls today, governed by the host network, unchanged by unit-10. The sandbox egress policy continues to govern everything the agent EXECUTES (bridge path).
- Process hygiene: fresh harness session per attempt (safe respawn); subprocess death -> STUCK; the async bridge joins its worker thread on close; no orphaned harness processes (reaping verified by a test using the real CLI spawn with a fake session where possible, else at the live smoke gate).

## Dogfood-gate procedure (parts 2 and 3)

1. Full suite green including contract tests (extras installed locally).
2. `live_harness` suite green with a real subscription (`RUN_LIVE_HARNESS=1`).
3. A real agent step through the provider (claude for part 2, copilot for part 3) against a scratch pipeline: terminal command sandbox-gated, file lane write, task tracker round trip, finish/nudge behavior, viz events flowing.
4. Record outcomes in the part's gate checklist (intent + blast radius: verify the harness never touched the sandbox directly - container audit confirms the only exec entries are bridge-issued).
