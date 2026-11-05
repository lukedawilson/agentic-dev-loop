# Unit 10 NFR Requirements Plan - multi-provider runtime

Status: in progress. Next stage after NFR Requirements = Infrastructure Design (NFR Design skipped per project precedent).

## Stage context

Functional Design approved (2026-11-05). Unit-10 = the runtime abstraction: an `AgentRuntime` protocol with three drivers (OpenHandsRuntime pure move, ClaudeAgentRuntime via claude-agent-sdk subprocess, CopilotRuntime via openai-codex subprocess), a shared tool catalog delivered to subprocess harnesses through an engine-internal stdio MCP server, and a SandboxToolBridge that keeps every executed command on the `Sandbox.run` gate. FD resolutions in place: Q1=A top-level `provider:` key with strict per-provider blocks (llm-fields forbidden on non-openhands providers); Q2=A one catalog implementation behind the MCP server; Q3=A copilot device-flow token in `~/.agentic-dev-loop/auth/`. Business rules BR-10.1..10.11. Requirements NFR-10.1..10.7 already frame the stage; this plan makes them measurable and settles the technology choices.

NFR analysis focuses on: performance of the subprocess/MCP round trips, the technology choices for the MCP server and the device-flow client (dependency footprint, compatibility risk), reliability of harness process lifecycle, and how the harness-hardening guarantees are proven without live subscriptions in CI.

## Open questions

### NFR-1 - Performance budget (harness spawn, MCP round trip, per-turn overhead)

Context: new per-run costs over the OpenHands baseline: harness subprocess spawn (claude/codex CLI), MCP server startup, one stdio round trip per tool call, plus the async-to-sync bridge thread. These sit on top of the existing sandbox execution cost, which dominates real work anyway.

A) **Practical targets, measured not enforced** - harness spawn + MCP handshake within a few seconds; per-tool-call overhead negligible against sandbox execution time; no CI gates (unit-3/5 precedent). (Recommended)

B) **CI-enforced budgets** - runs fail over explicit thresholds.

X) Other

[Answer]: A - practical targets, measured not enforced: harness spawn + MCP handshake within a few seconds; per-tool-call overhead negligible against sandbox execution time; no CI gates (unit-3/5 precedent). (user answer 2026-11-05)

---

### NFR-2 - MCP server implementation (engine-internal)

Context: the engine needs an MCP *server* (stdio JSON-RPC) exposing exactly the three catalog tools (`terminal`, `file_editor`, `task_tracker`). Both harness SDKs spawn it as a child process. The question is who implements the protocol.

A) **First-party minimal MCP stdio server** - a small engine module speaking MCP JSON-RPC over stdio (initialize, tools/list, tools/call; no resources/prompts surface), stdlib-only, zero new dependencies; consistent with the repo's stdlib-only runtime stance (unit-9 NFR-9.1 precedent); the surface is ours to keep closed. (Recommended)

B) **Official `mcp` Python SDK** - less protocol code, but a new pinned dependency with its own version churn and a much wider surface than we need; goes into the COMPAT-SHIMS registry.

X) Other

[Answer]: A - first-party minimal MCP stdio server: engine module speaking MCP JSON-RPC over stdio (initialize, tools/list, tools/call), stdlib-only, zero new dependencies; consistent with the repo's stdlib-only runtime stance; surface stays ours to keep closed. (user answer 2026-11-05)

---

### NFR-3 - Copilot device-flow client

Context: FR-10.10/Q3=A - the engine hosts GitHub's device flow (prints user code + URL, polls, stores the token). Three ways to implement it.

A) **Direct GitHub device flow over stdlib urllib** - POST `github.com/login/device/code` with the partnership client_id (the public constant the Copilot/OpenCode device flow uses), poll the token endpoint, store the result. No new dependency; the flow is a stable OAuth spec; the client_id is a pinned constant asserted by a test. (Recommended)

B) **Use the openai-codex SDK's own auth/login helper** (if the SDK exposes one) - less protocol code, but depends on SDK internals that may not be public API.

C) **Add the GitHub Copilot SDK for auth** - a second heavyweight dependency just for the login step.

X) Other

[Answer]: A - direct GitHub device flow over stdlib urllib: POST github.com/login/device/code with the partnership client_id (public constant), poll the token endpoint, store the result; no new dependency; client_id pinned and test-asserted. (user answer 2026-11-05)

---

### NFR-4 - Proving harness hardening without live subscriptions in CI

Context: BR-10.4/FR-10.9 (native tools off, `allowed_tools` locked, `setting_sources=[]`, no project config on codex) must be *proven*, not asserted. Live harness runs need real Claude/Copilot subscriptions, which CI cannot hold.

A) **Contract tests + opt-in live smoke** - CI proves the constructed harness configuration (the exact args the driver passes: allowed_tools, mcp_servers, setting_sources, permission mode) with contract tests against the real SDK objects; live hardening smoke tests (a harness attempting a native tool call must be refused) run as an opt-in marked suite with real subscriptions, executed manually at the dogfood gate, never silently skipped in CI. (Recommended)

B) **Live harness runs in CI** - CI holds API keys and runs real harnesses; heavier secrets burden, cost, and subscription-bucket drain.

X) Other

[Answer]: A - contract tests + opt-in live smoke: CI proves the constructed harness configuration with contract tests against the real SDK objects; live hardening smoke tests run as an opt-in marked suite with real subscriptions at the dogfood gate, never silently skipped in CI. (user answer 2026-11-05)

---

## Steps

- [x] Analyze FD artifacts for NFR implications
- [x] Resolve open questions (NFR-1..NFR-4, inline; all A)
- [x] Generate artifacts: nfr-requirements.md + tech-stack-decisions.md under aidlc-docs/construction/unit-10/nfr-requirements/
- [x] Security-baseline disposition (blocking set) - no blocking findings; hardening (BR-10.4), secrets boundary (BR-10.5), auth credentials (BR-10.6) each covered by NFRs with executable acceptance criteria; the executable isolation checklist test is the enforcement artifact
- [ ] Completion message + approval

## Stage Outcome

Approved by user 2026-11-05 ("B" - Continue to Next Stage: Infrastructure Design). No change requests.
