# Business Rules - Unit 10 (Multi-Provider Runtime)

The rules that bind the design. BR-10.x ids continue the repo's rule-numbering
convention. Requirements: FR-10.1..10.13, NFR-10.1..10.7.

## BR-10.1 Rule zero: absent provider is today's behavior

A pipeline config without a `provider:` key (or `provider: openhands`) is
byte-for-byte behavior-identical to today: same schema requirements, same
tools, same capture, same status semantics. The OpenHandsRuntime is a pure
move, not a rewrite. A backward-compatibility test pins this: the existing
config test corpus must pass unmodified, and a config without `provider`
loads with `provider == "openhands"`.

## BR-10.2 Provider key strictness (Q1=A)

- `provider:` accepts exactly `openhands | claude | copilot`; anything else
  fails at load naming the valid values.
- Provider config blocks (`claude:`, `copilot:`) use `extra="forbid"`.
- A provider block present while `provider` does not name it fails at load
  (dead config, no silent ignore). Error text tells the user which key to
  change or remove.
- `llm:` remains REQUIRED for openhands (zero change). When `provider` is
  `claude` or `copilot`, the `llm:` block, `llms:` profiles, per-step `llm:`
  refs and per-step `reasoning_effort` are FORBIDDEN and fail at load naming
  each offending key (they are OpenHands-only controls; silently ignoring
  them would violate the no-silent-fallback rule). The model/budget knobs for
  those providers live in their own config blocks.

## BR-10.3 The step model is provider-agnostic

Everything else on `StepConfig` applies to every provider unchanged:
`max_iterations` (the turn budget source for claude/copilot),
`finalize_nudge_turns` (035 policy, same template text), `terminal_timeout`,
`network`, `mounts`, `egress`, `secrets`, `forbidden_commands`,
`custom_prompt`, `verdicts`, `artifact`/`produces`, routing, `setup`.
`.agent.md` files and their frontmatter tool names are 100% portable across
providers (catalog names only).

## BR-10.4 Harness hardening (FR-10.9, non-negotiable)

- claude: `setting_sources=[]` (no project/user settings, skills, commands,
  memory, hooks); `allowed_tools` locked to the catalog MCP names;
  `permission_mode="deny"` as defense in depth; native tools off (Bash, Edit,
  Write, Read, Glob, Grep, TodoWrite all unavailable); the only tool channel
  is our MCP server.
- copilot: no project config load, no custom tools; our MCP server only.
- Fail-fast at config/driver construction: if a harness execution tool cannot
  be fully disabled on the pinned SDK version, construction fails with an
  error naming the SDK and the tool - never a best-effort downgrade.
- The isolation verification checklist from the requirements (section 3,
  hardening rows) is an executable test: a harness attempting a native tool
  call must be refused; a harness file write outside the workspace boundary
  must fail.

## BR-10.5 Secrets boundary

- Pipeline step secrets NEVER enter the harness process environment. The
  harness env is constructed explicitly (minimal set: cwd, PATH, and only the
  harness's own credential: `ANTHROPIC_API_KEY` in claude key mode; the codex
  token via its SDK auth surface in device mode).
- Secrets reach the sandbox only through the existing per-step secret env
  resolution (`step_secret_env`), exactly as today.
- Redaction: every secret value (including the copilot token) is in the
  redaction set for logs, last-error.md, capture and viz.

## BR-10.6 Auth fail-fast (FR-10.10)

- claude subscription mode: credential-store presence is detected
  (`~/.claude/.credentials.json`); absent -> the run fails fast with guidance
  ("run `claude login` in your terminal, then re-run"). The engine never
  reads, copies or replays the credential store; only the SDK/harness uses
  it.
- claude key mode: `ANTHROPIC_API_KEY` must exist in the engine environment
  or the run fails fast naming it.
- copilot: the engine hosts the GitHub device flow (prints user code + URL,
  polls, stores in `~/.agentic-dev-loop/auth/copilot.json`, mode 600, outside
  the workspace). Expired or missing token -> device flow re-runs. No
  credential is ever accepted from pipeline yaml.

## BR-10.7 Optional dependency fail-fast (FR-10.11)

- `claude-agent-sdk` and `openai-codex` are optional extras
  (`agentic-dev-loop[claude]`, `agentic-dev-loop[copilot]`); openhands needs
  no new dependency.
- Selecting a provider whose extra is not installed fails fast at runtime
  construction with the install command, never a silent degraded path.
- Each new SDK gets a `COMPAT-SHIMS.md` entry and a version-guard + canary
  pair in `tests/test_sdk_compat.py` (the established pattern: upgrade guard
  and dead-shim canary).

## BR-10.8 Status and retry rules (FR-10.12)

- `truncated = status != FINISHED` everywhere (unchanged).
- Truncated runs follow the existing attempt-bounded unclear-verdict retry
  with the captured text in the note (unchanged policy, all providers).
- Sticky sandbox infrastructure failures (030) fail the run with no retry,
  every provider, checked before any capture/verdict path.
- Harness subprocess death (claude/copilot) maps to STUCK: a fresh attempt
  respawns the harness, which is safe and attempt-bounded; it never flaps
  because the budget gate is the same turn/attempt accounting as today.

## BR-10.9 Testing rules (NFR-10.4, NFR-10.6)

- TDD, red-green-refactor, per repo policy.
- Engine-level tests use interface-level fakes of `AgentRuntime` (the unit-3
  FD-5 pattern): the step runner, translation table, nudge policy arithmetic
  and config validation are tested with fakes, never with a live harness.
- Mocks are permitted ONLY at the SDK subprocess boundary (claude-agent-sdk
  / openai-codex calls); never for file I/O, pure functions or the bridge.
- The bridge is tested against the real Sandbox path (the test_isolation
  fixture pattern), including infra-error classification and the
  forbidden/allowlist/egress policies.
- Part 1 gate: the FULL existing suite must pass with the OpenHandsRuntime
  move alone, plus a dogfood run of this repo's own
  `agentic-dev-loop/pipeline.yaml` showing zero behavior delta. Parts 2 and 3
  do not start until that gate holds.

## BR-10.10 Module contract

The new `src/agentic_dev_loop/engine/runtimes/` package is registered in
`tests/test_module_contract.py` if that test pins the engine module layout
(verify in Code Generation part 1). Public imports follow the engine's
existing `from .x import y` style.

## BR-10.11 Repo hygiene

All new files are ASCII (em dash banned, `tests/test_repo_ascii.py`
enforced); no consumer names; docs use the existing planning/construction doc
conventions.
