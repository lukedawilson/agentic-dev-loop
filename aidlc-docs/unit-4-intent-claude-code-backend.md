# Unit 4 Intent (draft) - Claude Code as a second agent backend

**Status**: INTENT ONLY - draft, not started. Written 2026-10-18 from an
exploratory discussion; amended 2026-10-19 to add visualiser integration and
the shared-steps-config framing. No code written, no AI-DLC workflow started,
no decisions committed. Revisit before proceeding.

**Direction (2026-10-18)**: the discussion converged on the *inverted* shape -
not "Claude Code as a backend inside the engine," but "the engine exposed as an
MCP toolkit that a Claude **dynamic workflow** drives." That is the recommended
architecture (see below). The earlier "backend" framing (findings 1–3) is
retained for completeness but is not the target.

## Intent

Plug Claude Code into agent-engine as an alternative agent-execution backend
alongside OpenHands, for users on a Pro/Enterprise (subscription) plan where
an Anthropic API key isn't available. Desired metering: usage counts against
the same limits as interactive use (TUI / web / desktop), not separate API
billing.

### What the user gets (the four load-bearing pillars)

1. **Interactive metering** - every LLM call (workflow session and subagents)
   counts against the user's Claude Pro/Enterprise interactive limits, never
   separate API billing (finding 1).
2. **Sandboxing** - the deterministic command gate still runs inside the
   engine's kernel isolation (unit-3; finding 5).
3. **The visualiser** - the live graph viz keeps working, showing every step
   the workflow drives through the engine (finding 6).
4. **Shared steps config** - one `pipeline.yaml` drives both modes: the
   interactive engine run and the Claude workflow schedule against the same
   validated steps model (finding 7).

## Relationship to existing units

The current AI-DLC intent (units 1–3) is guardrails & sandboxing:

- unit-1 - output-passing fix (capture, don't trust agent file-writes)
- unit-2 - SDK-level hardening
- unit-3 - kernel-level isolation (opt-in) - NOT STARTED

This is a **separate** concern: a second agent backend. It *interacts* with
unit-3's isolation design (see below) but is not part of it.

## Findings (exploratory; live-sourced, not training data)

### 1. Billing / auth landscape - the headline risk

Metering is **auth-driven, not client-driven**. Per current docs
([support.claude.com/15036540](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan),
[17154008](https://support.claude.com/en/articles/17154008)):

- **Pro**: no API key path. Sign in (OAuth) → SDK/`claude -p` draws from plan
  limits (shared with web/desktop/TUI). Pro gets **no** API credits.
- **Max / Team**: since Oct 7 2026, include monthly API credits (Max 5x $100,
  Max 20x $200, Team pooled ≤$500) claimed into a Claude Console org, spent
  via an **API key**.
- **Enterprise**: not eligible for those credits; separate sales/invoiced
  contract.
- Setting `ANTHROPIC_API_KEY` forces API billing even on a subscription -
  the #1 footgun for this intent.

**Durability risk**: unattended/automated usage is exactly what Anthropic has
been trying to pull off subscriptions. Boris Cherny (Apr 2026): "our
subscriptions weren't built for the usage patterns of these third-party
tools." May 2026 → move SDK/`claude -p` to API-rate billing; Jun 15 2026 →
paused ("nothing has changed"); Oct 7 2026 → credits for Max/Team. "Same
limits as TUI" holds today; it is policy, not contract.

### 2. What the SDK actually is

- **Python SDK** (`claude-agent-sdk`, PyPI 0.2.168, "Alpha") is a **thin
  wrapper** that bundles/spawns the Claude Code CLI (Node) as a subprocess
  (`_internal/transport/subprocess_cli.py`). Agent-loop/tool logic lives in
  the CLI.
- **TS SDK** (`@anthropic-ai/claude-agent-sdk`, 0.3.x) ships per-platform
  **native binaries** and peer-depends on `@anthropic-ai/sdk` - the official
  Anthropic API client.
- Both ultimately make HTTPS calls to `api.anthropic.com` (the Messages API
  host). "No API key" ≠ "not hitting the API" - it's the same backend,
  metered differently by auth.

### 3. Efficiency: avoiding a process per message

- One-shot `query()` is stateless (process per call). **`ClaudeSDKClient`** is
  stateful: one `connect()` (one subprocess), many messages, context kept.
- **Session resume/store**: `resume` / `continue` / `session_id`,
  `InMemorySessionStore`, `import_session_to_store` - a retry can resume a
  prior session (system prompt built once, reused) instead of cold-starting.
  Maps to the engine's `thread_id`/checkpoint model.
- **In-process tools** (`@tool` / SDK MCP servers) and **hooks** (e.g.
  `PreToolUse` deny) run in-process - no subprocess per tool call; hooks give
  harder enforcement of "never `git commit`" than prompt text.
- **TS-side** `prewarm()`/`SpareProcess.claim()` (process pooling); Python
  parity unverified.
- **Irreducible floor**: Python SDK still = one CLI subprocess per session.
  Zero-subprocess ⇒ TS native binary or raw Messages API (the latter forfeits
  subscription metering).

### 4. Reverse-engineering the web app - rejected

Technically possible (undocumented `claude.ai/api/...` endpoints), but:
violates Anthropic ToS/usage policy → account termination risk (loses the
very subscription this depends on); fragile/undocumented (the repo's own
Framer lesson); and redundant - the sanctioned OAuth headless path already
delivers TUI-equivalent metering. Do not pursue.

### 5. Kernel isolation interaction

unit-3 (`requirements.md` FR-2.1) splits the agent into trust classes:
LLM calls host-side (trusted); `terminal` tool + command steps sandboxed.
OpenHands keeps these separable. **Claude Code collapses them** - model loop
and Bash/Write tool execution live in the same process being sandboxed.

Two fits:

- **A. Containerize the whole Claude Code session** (reuse the engine's
  `Sandbox`, FR-2.2). Boundary then also contains LLM egress + credentials
  (`.claude/`, key) and sandboxes Read/Write too (stronger). Network-egress
  policy becomes load-bearing.
- **B. Claude Code's native sandbox** (`SandboxSettings` /
  `SandboxNetworkConfig` in `ClaudeAgentOptions`). No container, but a second
  mechanism not unified with the engine's `Sandbox`.

**DDD consequence**: the design forbids a `Sandbox` protocol ("single-impl
interfaces forbidden; extract only when a second backend lands"). A Claude
backend *is* that second backend - for the **agent runner** (`run_agent`,
hardcoded to OpenHands `Conversation` today) and for **isolation**. Sequence
the extraction once, against a real second backend, not speculatively.

**Stays clean**: `command` steps are backend-agnostic and route through
`Sandbox.run` regardless; FR-2.3 fail-fast, NFR-3 least-privilege, host-side
commit all carry over.

### 6. The visualiser - LLM-free, event-driven, but coupled to the graph runner

`src/agent_engine/viz.py` is a deterministic observer with **no LLM calls and
no agent prompt**: it translates LangGraph `tasks`/`updates` StreamParts into
`node_started` / `node_finished` / `state` events (whitelisted state keys
only), publishes them on a thread-safe `VizBus`, and serves a stdlib-only
HTTP/SSE page with an embedded Mermaid flowchart. The flowchart topology is
built from the same `PipelineConfig` (`topology_mermaid(cfg)`), so one config
already feeds guardrails, scheduling, *and* the picture.

Two facts shape its Unit 4 fit:

- **Today it rides on the graph runner.** The bus is created and fed inside
  `run_pipeline` (graph.py): it translates the live `graph.invoke` stream and
  tees stdout/stderr. In workflow mode that runner is bypassed - subagent
  activity happens inside Claude Code, invisible to the engine's bus.
- **It is event-driven by design.** `tests/smoke/viz_demo.py` already drives the full viz
  surface from fabricated events - no pipeline, no checkpoints, no LLM -
  proving the viz does not care where events come from.

**Integration plan (recommended)**: `ae_run_step` publishes the same event
vocabulary to the run's `VizBus`, and `serve_viz` is reused unchanged. The
flowchart then shows every step the workflow drives through the engine.
Subagent-side dev/review/qa activity stays invisible unless Claude-side hooks
forward events (open question 9).

### 7. Shared steps config - one `pipeline.yaml`, both modes

The workflow script has **no filesystem access**, so it cannot read
`pipeline.yaml` itself. `ae_load_pipeline` is the only path: the engine reads
and validates the file with the real `load_config`/`PipelineConfig` and
returns the steps model (with `kind` injected) plus its JSON schema.
Consequences:

- The workflow schedules against the **same validated model** the engine
  executes - no JS re-derivation, no drift (design principles 1–4).
- The same config feeds the viz topology (`topology_mermaid`), so the
  flowchart always matches the steps the workflow runs.
- A step change in `pipeline.yaml` propagates to both modes with **zero
  workflow changes** - the engine-derived schema flows straight into the
  workflow's `agent({schema})`.
- The interactive engine run (OpenHands backend) and the workflow run share
  the one config file; the engine stays the single source of truth for it.

## Recommended architecture - dynamic workflows as orchestrator, engine as guardrails toolkit

### What dynamic workflows actually are (corrected)

A dynamic workflow is a **JavaScript script Claude writes** that orchestrates
many **subagents** via `agent()` / `pipeline()` / `parallel()`. Two hard
constraints shape everything:

- The script has **no filesystem, no shell, no `import()`** - "agents read,
  write, and run commands; the script coordinates the agents."
- Therefore the script **cannot** parse `pipeline.yaml`, run the engine CLI,
  import the library, or call MCP tools directly. The only way it reaches the
  engine is **through a subagent** (subagents inherit the session's tools +
  permission rules).

### Division of labor

- **Workflow owns**: planning, fan-out/parallelism, adversarial verification,
  checkpoint/resume, stall-retry, usage-limit wait. Most of what the engine's
  LangGraph graph provides is already covered natively here.
- **Engine owns**: the *deterministic, non-LLM* guardrails - the pipeline model
  (validated), the mechanical command gate, the verdict protocol, the single
  gated commit, the live visualiser, and (unit-3) kernel isolation. These are
  exactly what the workflow cannot do itself.

### Exposed surface (MCP server, callable by subagents)

| Tool | Responsibility | Notes |
| --- | --- | --- |
| `ae_load_pipeline(path)` | Read + validate `pipeline.yaml` via the real `load_config`/`PipelineConfig`; return the steps model (with `kind` injected) **and** its JSON schema | Fail-fast up front; schema = `PipelineConfig.model_json_schema()`; also the viz topology source (`topology_mermaid`) |
| `ae_run_step(name, context, notes)` | Execute one step inside the engine (command/action semantics + verdict parsing + artifact hygiene + attempt accounting) and return `{verdict, next, notes}` | Policies stay in the engine; publishes viz events (`node_started` / `node_finished` / `state`) to the run's `VizBus` |
| `ae_viz_console(node, stream, text)` | Ship subagent-side console/event lines into the run's `VizBus` | Recommended for structured events (open question 11); raw console bytes go via Claude-side hooks → ingest route instead |
| `ae_commit(message)` | The single gated commit (clean-worktree gate) | Host-side, unchanged |

### Visualiser integration

The viz is deterministic event plumbing - no LLM - so it sits entirely on the
engine side of the division of labor:

- `ae_run_step` publishes `node_started` / `node_finished` / `state` events to
  the run's `VizBus` (the same vocabulary `viz.translate` produces today), so
  the flowchart updates live as the workflow drives steps.
- `serve_viz` and the embedded Mermaid page are reused unchanged; the topology
  comes from the same validated config that `ae_load_pipeline` returned, so
  the picture always matches the shared steps config (pillar 4).
- The viz server runs in the engine MCP process (stdlib-only HTTP/SSE on
  localhost); open questions 9–11 cover subagent-side visibility, terminal
  output, and URL surfacing.
- Fallback if the viz must not live in the engine process: the event log is
  bus-shaped, so a `tests/smoke/viz_demo.py`-style harness can replay a saved run - same
  page, no engine. Not the target; kept as an option.

**Agent terminal output.** Today the console stream works because the agent
runs in-process: `ConsoleCapture` tees `sys.stdout`/`sys.stderr` and the
OpenHands visualizer prints to those streams (plan 008). Engine-side output
survives workflow mode unchanged - `ae_run_step` prints inside the MCP
process. Subagent (dev/review/qa) output lives in a different process and
never touches the tee: the user still sees it in Claude Code's own UI, but
the viz sidebar doesn't. Recommended transport, split by payload type
(open question 11):

- **Structured events** - `ae_viz_console` MCP tool: the same channel
  subagents already use, attribution supplied by the workflow, blast radius
  bounded by the existing caps.
- **Raw console bytes** - Claude-side hooks (`PostToolUse` / `Notification`)
  POST to a new ingest route on the viz server: real output, no LLM
  round-trip. An MCP tool is the wrong transport here - tool arguments are
  model-generated text, so verbatim output would cost tokens and risk
  mangling (the same cost as open question 7).
- **Workflow-script forwarding** - rejected: the script sees summaries only
  and is not a log shipper.

### Design principles

1. **Engine = single source of truth** for both the model and the policies.
   The workflow consumes the validated model; it never re-derives it in JS.
2. **Parse = data, execute = policies.** `ae_load_pipeline` gives the workflow
   something to schedule against; `ae_run_step`/`ae_commit` are where the
   guardrails live. Both from the same engine.
3. **Project the model.** Emit only orchestration-relevant fields (steps with
   `kind`, routes, `max_attempts`, `produces`/`artifact`, `verdicts`) - not the
   `llm` block (`api_key_env`, `base_url`).
4. **Engine-derived schema.** The workflow's `agent({schema})` uses the schema
   from `ae_load_pipeline`, so the steps model can't drift from the engine.
5. **Every engine call is subagent-mediated** (the script can't call tools
   directly). Determinism lives *inside* the tool (exit-code verdict,
   `VERDICT:` parsing), not in the LLM that fronts it. Structured output keeps
   the JSON from mangling.

### Tradeoffs / open consequences

- The engine's **`agent` step type and its LangGraph retry/verdict graph are
  bypassed** in this mode - Claude subagents *are* dev/review/qa. Extracting a
  callable `run_step(step, state)` from the graph node closures is a clean
  refactor, not a rewrite.
- A workflow's strength is **parallel fan-out**, but dev→test→review→qa is
  sequential-with-retries. The workflow earns its keep via parallel review+qa,
  adversarial verification, and checkpoint/resume; for a strictly linear
  pipeline it's a modest scheduler, and a plain interactive session driving the
  same MCP tools may be simpler. (Open question.)
- Isolation becomes **two layers**: Claude Code's own sandboxing for subagent
  Bash, plus the engine's `Sandbox` for the command gate it owns.
- The viz shows what the engine sees - command/action steps. Subagent-side
  dev/review/qa activity is invisible without Claude-side event forwarding
  (open question 9; console text is open question 11). Interim position:
  **TBD** - do not assume the command-gate-only picture is the accepted
  default.

## Open questions (revisit later)

1. Metering durability: how long does signed-in headless use stay on plan
   limits? Treat as fragile, not guaranteed.
2. Isolation fit: A (containerize session) vs B (native CC sandbox) vs both -
   decide in unit-3 FD, not now.
3. Python SDK parity: `prewarm`/spare-process, resume-across-session-store
   behavior - needs a Phase-5 spike before committing.
4. Capacity: plan limits (5-hr session + weekly) are human-sized, not
   pipeline-sized - an agent-engine run may exhaust them in one go.
5. Agent-definition mapping: `.agent.md` → Claude system prompt (inline body
   or `append`); verdict protocol (`VERDICT:` line in final response) should
   carry over unchanged.
6. Workflow vs plain session: is a dynamic workflow the right driver for a
   sequential pipeline, or should a plain interactive session drive the same
   MCP tools?
7. Subagent-mediated deterministic steps: acceptable token cost of the
   JSON-echoing subagent wrapper? (Cheap, but nonzero.)
8. Refactor shape: extracting `run_step(step, state)` from the LangGraph node
   closures - how much of the graph survives vs is bypassed in workflow mode?
9. Viz subagent visibility: forward Claude-side subagent/tool events into the
   engine's viz bus (via Claude hooks), or accept a command-gate-only
   flowchart? **Interim default: TBD.** (Raw console text: question 11.)
10. Viz hosting in workflow mode: `serve_viz` runs in the engine MCP process -
    when is the topology (re)set (each `ae_load_pipeline` call?) and how is
    the URL surfaced to the user (tool return, engine log)?
11. Agent terminal output: engine-side console is captured in-process as
    today; subagent (dev/review/qa) output needs a new ingress path.
    Recommended: `ae_viz_console` MCP tool for structured events; Claude-side
    hooks (`PostToolUse` / `Notification`) POSTing to a new viz ingest route
    for raw console bytes (MCP tool arguments are model-generated text - the
    wrong transport for verbatim output, same cost as question 7);
    workflow-script forwarding rejected. Ingress is a trust-model change -
    the first externally-produced content into the bus - bounded by the
    existing caps (`MAX_LINE`, `console_maxlen`, per-stage `MAX_CONSOLE`) and
    the page's HTML-escaping, but worth recording.

## Sources

- https://github.com/anthropics/claude-agent-sdk-python (repo + README)
- https://pypi.org/pypi/claude-agent-sdk (0.2.168, Alpha)
- https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
- https://support.claude.com/en/articles/17154008 (monthly API credits)
- https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan
- https://news.ycombinator.com/item?id=45644862 (Ask HN: production-ready? No)
- https://news.ycombinator.com/item?id=49997654 (Oct 7 credits change)
- https://arstechnica.com/ai/2026/06/anthropic-pauses-token-based-billing-for-its-claude-agent-sdk/
- https://claude.com/resources/articles/introducing-dynamic-workflows-in-claude-code
- https://code.claude.com/docs/en/workflows
- Repo-internal: `aidlc-docs/inception/requirements/requirements.md`,
  `application-design.md`, `unit-of-work.md`

## Amendment (2026-10-21) - unit-4 Requirements Analysis outcome

Scope sharpened by user directive (2026-10-21): unit-4 = the two parts of agentic-dev-loop drivable from Claude Dynamic Workflows - (1) parsing pipeline.yaml and creating the workflow steps from it, (2) the visualiser web UI. **Docker sandboxing from Claude Code is explicitly OUT - not attempted.** Proposal accepted and elaborated into requirements:

- **Module split** (FR-4.1): `agentic_dev_loop.core` (pipeline model + parsing + workflow-schema projection; light deps), `agentic_dev_loop.engine` (graph/stages/sandbox/CLI), `agentic_dev_loop.viz` (stdlib-only server + event model). Guard-tested dependency contract.
- **Architecture change** (FR-4.3/4.8): a Claude skill `/agentic-dev-loop` with a bundled workflow JS **template** **replaces** this doc's MCP-tools recommendation (ae_load_pipeline / ae_run_step / ae_viz_console / ae_commit are dropped). Subagents reach core/viz via shell/python. Workflow steps are created at BUILD time: at workflow creation Claude (with tools) imports `.core`, parses pipeline.yaml, and generates the concrete JS with steps baked in; runtime never parses. (Reversal 2026-10-21: earlier amendment said runtime parse via subagent - user changed mind: "pipeline.yaml should be parsed at build time, not runtime".)
- **Pillar 2 revised** (FR-4.2/4.9): workflow mode does NOT use the engine sandbox - steps run in Claude Code's own environment; unit-3 sandboxing remains engine-mode-only. Trust-model change documented.
- **Pillar 4 confirmed** (FR-4.5/4.9): one pipeline.yaml both modes; same VERDICT: contract, workflow JS routes; engine-only fields (isolation:, network:, setup:) ignored in workflow mode with documented semantics.
- **Commit** (FR-4.6): trusted docker-free shim reusing engine commit logic, subagent-invoked.
- **Viz** (FR-4.7): subagent-launched live server; new HTTP ingest endpoint for step events; SSE unchanged.
- Interactive metering (pillar 1) remains out of unit-4 scope.
