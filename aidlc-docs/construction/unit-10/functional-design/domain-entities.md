# Domain Entities - Unit 10 (Multi-Provider Runtime)

This document defines the entities the multi-provider runtime layer works with. It is
the Functional Design artifact for unit-10; requirements are
`aidlc-docs/inception/requirements/unit-10-requirements.md` (FR-10.1..10.13).

## 1. Provider selection entities

### Provider (Literal)

`openhands | claude | copilot`. The pipeline-level runtime selection (Q1=A).
Stored as `PipelineConfig.provider`, default `openhands` (rule zero: absent key
means today's behavior unchanged).

### ClaudeAgentConfig

One YAML block, present only when `provider: claude` (Q1=A: a block that does
not match `provider` fails at load).

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `login` | `subscription \| key` | required | `subscription` = the user's own `claude login` session (detected, never read by the engine); `key` = `ANTHROPIC_API_KEY` supplied by the engine environment |
| `model` | `str \| None` | `None` | Model alias passed to the harness; `None` = the harness default |
| `max_turns` | `int \| None` | `None` | Per-run turn budget override; `None` = the step's effective `max_iterations` (engine default 500) |

`extra="forbid"`: unknown keys fail at load, matching the repo's strict-schema
convention.

### CopilotConfig

One YAML block, present only when `provider: copilot` (Q1=A).

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `auth` | `Literal["device"]` | `device` | GitHub device flow hosted by the engine (Q3=A: token cached in the user-level auth store and re-run on expiry). Only value today; the field exists so future modes are additive |
| `model` | `str \| None` | `None` | Model alias passed to the harness; `None` = harness default |

`extra="forbid"`. No credential-bearing fields can ever appear here (secrets
never live in pipeline yaml, consistent with `SecretSpec` locator-only policy).

## 2. Runtime protocol entities

### RuntimeStatus (enum)

`FINISHED | ERROR | STUCK | TRUNCATED`. The provider-independent terminal
status of one `AgentRuntime.run()`.

### RuntimeResult

| Field | Type | Meaning |
| --- | --- | --- |
| `status` | `RuntimeStatus` | The run's terminal status |
| `final_text` | `str \| None` | The agent's final response text (the capture source). `None` = no final response was produced |
| `truncated` | `bool` | Derived, always `status != FINISHED` (identical to today's rule) |
| `capture_error` | `str \| None` | Set when the run finished but produced no capturable final response (today's "empty_response" channel, provider-independent) |

`RuntimeResult` is the ONLY data the step runner consumes. The graph layer,
verdict parsing, retry classification and artifact context continue to see
today's `AgentResult`; the step runner translates `RuntimeResult` to
`AgentResult` (see business-logic-model section 6).

### NudgePolicy

| Field | Type | Meaning |
| --- | --- | --- |
| `turns_before_end` | `int` | From `StepConfig.finalize_nudge_turns` |
| `message` | `str` | The 035 template: `"{n} turns remain: produce your final response now"`, rendered with the remaining count |

Design refinement (flagged): the approved sketch listed `nudge()` as a protocol
method. It is realized instead as a `NudgePolicy` argument to `run()`:
nudging is turn-synchronous inside a run (035 drives it via the same loop that
runs the conversation), and the engine never nudges outside a run today. A
standalone `nudge()` method would be dead surface.

### AgentRuntime (protocol)

```
class AgentRuntime(Protocol):
    def run(self, message: str, *, nudge_policy: NudgePolicy | None = None) -> RuntimeResult: ...
    def close(self) -> None: ...
```

Implemented by `OpenHandsRuntime`, `ClaudeAgentRuntime`, `CopilotRuntime`.
Construction is the factory's job (`runtimes/factory.py`); drivers receive a
`RuntimeContext` (section 3) at construction. `close()` is idempotent and safe
after any failure.

## 3. RuntimeContext

The engine-side bag a driver is constructed with. Every field is already
resolved at construction time, so drivers contain no resolution logic:

- `workspace`, `state_dir`, `agents_dir`: resolved paths (host)
- `sandbox: Sandbox | None`: the run's Docker sandbox handle (always present in
  real runs; `None` only in the legacy no-sandbox test path)
- `secrets: dict[str, str]`: resolved secret values (redaction set)
- `pipeline`, `step`: names for audit metadata (never sent into the container,
  as today)
- `terminal_timeout`, `network`, `mount_set`, `egress_policy`, `allowlist`,
  `forbidden_commands`: the step-resolved execution policy
- `step_env`, `secret_env`: resolved step environment + per-step secret env
- `viz_bus`: the engine-level event bus (unit-9), for `on_terminal_result`
- `llm` / `llms` (OpenHands driver only, 054): the built SDK LLM objects
- `nudge` config: `finalize_nudge_turns` (folded into a NudgePolicy by the
  step runner)

## 4. Tool catalog entities

### CatalogToolName

`terminal | file_editor | task_tracker | finish`. The single engine-owned tool
vocabulary. `.agent.md` frontmatter names these; the same names work on every
provider.

### CatalogToolSpec

One declared tool: `name` (CatalogToolName), `description`, JSON Schema
`input_schema` (the MCP input shape). The catalog is declared once
(`runtimes/catalog.py`) and bound per driver:

- OpenHands: existing SDK tools (TerminalTool + SandboxTerminalExecutor,
  FileEditorTool, TaskTrackerTool, FinishTool) - the catalog is the naming
  contract, not a new implementation.
- claude / copilot: the three MCP-exposed tools (`terminal`, `file_editor`,
  `task_tracker`) served by the engine-internal stdio MCP server. `finish`
  stays harness-native (claude stream end / copilot turn end), so it is not
  exposed over MCP (refinement recorded in the FD plan Q2 answer).

### TerminalOutcome

The bridge's normalized terminal result: `exit_code`, `stdout`, `stderr`,
`timed_out`, plus the classified `infra_error` (030) when the failure is
sticky infrastructure. Rendered into the per-harness observation shape
(TerminalObservation for OpenHands, MCP ToolResult for claude/copilot) by the
driver binding only.

## 5. Bridge entities

### SandboxToolBridge

Constructed once per run (per `run_agent` call, mirroring today's
`register_sandbox_terminal` per-run re-registration, KD-4) with the resolved
`RuntimeContext`. Owns the execution choke point:

- `run_terminal(command) -> TerminalOutcome`: forbid-check (`forbidden`),
  allowlist, `SandboxExecutionRequest` (argv, cwd, env with per-step secrets,
  timeout = terminal_timeout, network lane, mount_set, egress_policy, pipeline
  + step audit metadata), `sandbox.run(request)`, infra-error classification.
- `read_file / write_file / edit_file`: the host-side file lane with the same
  path policy as today's FileEditorTool (hostboundary rules; details verified
  for binding parity in Code Generation part 2).
- `task_list / task_set`: the per-run in-memory task store (replaces the
  harness-native todo tools that are turned off).
- Every `run_terminal` emits `on_terminal_result` to the viz bus, exactly as
  SandboxTerminalExecutor does today, so viz output is identical across
  providers.

### MCP stdio server (engine-internal)

A stdio JSON-RPC server process the harness spawns (per SDK `mcp_servers` /
MCP config), serving only the three catalog tools. No network listener, no
other surface. Tool handlers are thin adapters over the bridge; all policy
lives in the bridge.

## 6. Auth entities

### CopilotAuthToken (user-level auth store)

Lives at `~/.agentic-dev-loop/auth/copilot.json` (Q3=A). Shape: the OAuth
token payload plus expiry. Properties: outside the workspace (never mounted,
never synced), chmod 600, never printed, redacted in every log/audit path,
read only by the engine's auth module to hand to the copilot runtime session.

### HarnessCredential

The ONLY secret a harness process may receive: its own auth credential
(claude subscription session handled internally by the SDK, `ANTHROPIC_API_KEY`
in key mode, the codex token in device mode). Pipeline step secrets never reach
the harness process env (FR-10.9).
