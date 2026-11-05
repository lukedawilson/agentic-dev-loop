# Unit 10 NFR Requirements - multi-provider runtime

Stage: NFR Requirements. Source: `aidlc-docs/inception/requirements/unit-10-requirements.md` NFR-10.1..10.7, made measurable; open-question resolutions NFR-1..NFR-4 (all A, 2026-11-05). NFR Design SKIPped per project precedent.

## NFR-10.1 Backward compatibility (rule zero)

**Statement**: A pipeline config with no `provider:` key behaves exactly as today, byte for byte, with the OpenHands provider.

**Acceptance criteria**:
- The existing config corpus (this repo's `agentic-dev-loop/pipeline.yaml` plus every fixture in `tests/test_config.py` / `test_055_schema_lock.py`) loads with zero new warnings or errors and produces the same validated `PipelineConfig`.
- The full test suite is green after the part-1 refactor with zero behavior-changing test edits (pure move).
- The dogfood run of this repo's pipeline (openhands default) completes with no observable delta versus pre-refactor behavior (same verdicts, same artifacts, same capture semantics).
- `provider` default is `openhands`; `llm:` remains required exactly when the provider is openhands.

## NFR-10.2 Security (harness hardening, BR-10.4/BR-10.5)

**Statement**: The subprocess harnesses are locked to the catalog and the sandbox. Non-negotiable items from FR-10.9:
- claude: `setting_sources=[]` (no project/global settings, skills, hooks), `allowed_tools` locked to exactly the three MCP catalog tools, `permission_mode="deny"` as defense in depth (native tools off), `mcp_servers` limited to the engine's stdio server.
- codex: no project config, no custom tools, MCP-only tool surface.
- Pipeline secrets never enter the harness process env; the only credential that may is the harness's own login credential (ANTHROPIC_API_KEY in key mode).
- Config-level fail-fast if a harness execution tool cannot be fully disabled on the pinned SDK version (validated by the version-guard and contract tests).

**Acceptance criteria**:
- An executable isolation checklist test (contract tests against the real SDK objects, NFR-4=A) asserts the exact constructor/config arguments the drivers pass.
- Bridge commands always route through `SandboxExecutionRequest` -> `Sandbox.run`; the harness process never receives sandbox credentials or a shell on the host workspace.
- Copilot token and claude key-mode key are in the run's redaction set everywhere (logs, notes, last-error.md, viz).

## NFR-10.3 Reliability (no-flap STUCK, BR-10.8)

**Statement**: Subprocess death of claude/codex maps to STUCK and never to a silent retry loop or a hang.

**Acceptance criteria**:
- Harness subprocess death -> `RuntimeStatus.STUCK` -> `truncated=True` -> the existing attempt-bounded unclear-verdict retry (same channel as today's OpenHands stuck detection).
- 030 sticky sandbox infra failures remain non-retryable (raise, run fails).
- The async-to-sync bridge propagates harness exceptions across the thread boundary; no leaked threads on close; each attempt starts a fresh harness session (safe respawn).
- A hung harness process is bounded by the step's `max_iterations`/budget and the existing run-level timeout posture; no new unbounded waits.

## NFR-10.4 Testability (TDD, BR-10.9)

**Statement**: TDD red-green-refactor; interface-level fakes for runtimes (unit-3 FD-5 pattern); mocks ONLY at SDK subprocess boundaries.

**Acceptance criteria**:
- Every new behavior lands with a failing test first.
- Engine tests (graph, stages translation, capture, nudge policy) run against a fake `AgentRuntime`, never a real harness.
- Mocking is limited to SDK classes that spawn external processes (claude-agent-sdk session, openai-codex session); the bridge is tested against the real Sandbox path (existing `test_isolation`-style fixtures).
- Contract tests assert the exact harness configuration; live hardening smoke tests are a marked, opt-in suite run at the dogfood gate with real subscriptions, never silently skipped in CI.
- COMPAT-SHIMS entries + version-guard/canary tests for claude-agent-sdk and openai-codex (pattern of `tests/test_sdk_compat.py`).

## NFR-10.5 Maintainability (thin drivers, BR-10.3/BR-10.10)

**Statement**: Drivers are thin; everything shared lives in the engine.

**Acceptance criteria**:
- New package `src/agentic_dev_loop/engine/runtimes/` with one module per concern (protocol, openhands_runtime, claude_runtime, copilot_runtime, bridge, catalog, mcp_server, async_bridge, factory); `test_module_contract.py` extended if it pins layout.
- The engine's step model (.agent.md + AgentSpec) is provider-agnostic; a step's agent file ports across providers unchanged.
- The OpenHandsRuntime move is a pure refactor (no behavior edits) and lands green alone (part-1 gate).

## NFR-10.6 Repo hygiene

**Statement**: Everything committed is ASCII; no em dash (U+2014); no consumer names anywhere.

**Acceptance criteria**: `tests/test_repo_ascii.py` stays green across all new files (enforced on every test run and by the pre-commit hook).

## NFR-10.7 Fail fast (BR-10.6/BR-10.7)

**Statement**: Every misconfiguration and missing prerequisite fails loudly with guidance naming the fix. No silent fallbacks.

**Acceptance criteria** (each covered by a test):
- Unknown `provider:` value -> load error naming the known values.
- Provider block present but not matching `provider` -> load error.
- `llm:` / `llms:` / per-step `llm:` / `reasoning_effort` present with a non-openhands provider -> load error (dead config).
- `provider: claude` with `agentic-dev-loop[claude]` not installed -> error naming `pip install agentic-dev-loop[claude]`; same for copilot.
- claude subscription mode with no Anthropic login (~/.claude/.credentials.json absent) -> error with the `claude login` guidance; claude key mode with no ANTHROPIC_API_KEY -> error naming it.
- copilot with a missing/expired token -> device flow re-runs (not an error); a refused/aborted device flow -> error with guidance.
- A harness tool that cannot be fully disabled on the pinned SDK version -> hard error at construction (never a warning).

## NFR-1 (resolved): Performance

Practical targets, measured not enforced (unit-3/5 precedent):
- Harness spawn + MCP initialize handshake: a few seconds for a cold CLI start.
- Per-tool-call overhead: negligible against the sandbox execution time it dispatches (target < 100 ms added round trip per call on the same host).
- No CI perf gates. Measurements happen at the dogfood gate as observational notes only.

## NFR-4 (resolved): Testing tiers

Tier 1 - always in CI: pure-function and fake-runtime tests; config validation tests; contract tests against real SDK objects (construct config, assert exact args; no network). Tier 2 - marked/opt-in: live harness smoke with real subscriptions (hardening refusal probe + end-to-end claude/copilot agent run through the bridge); run manually at the dogfood gate, never silently skipped.

## Security Baseline disposition (blocking set)

Reviewed against the FD mapping. The Security Baseline's blocking concerns for this unit are harness hardening (BR-10.4), the secrets boundary (BR-10.5), and auth credential handling (BR-10.6) - each is covered by an NFR above with executable acceptance criteria. No blocking findings; the executable isolation checklist test (NFR-10.2) is the enforcement artifact, consistent with the FD design.
