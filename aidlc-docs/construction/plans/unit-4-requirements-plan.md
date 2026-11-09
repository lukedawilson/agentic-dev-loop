# Unit 4 Requirements Plan - Claude Dynamic Workflows driver

Status: in progress (Requirements Analysis stage, adaptive depth).

## Unit context

Unit-4 drives two parts of agentic-dev-loop from Claude Dynamic Workflows:

1. Parsing `pipeline.yaml` and creating the workflow steps from it
2. The visualiser web UI

Hard constraints (user directive 2026-10-21): NO Docker sandboxing from Claude Code - out of scope, not attempted. Proposal: split the pipeline model, engine and viz into separate composable modules; build a Claude skill `/agentic-dev-loop` with a bundled workflow JS file that uses the pipeline model and viz.

Prior analysis basis: `aidlc-docs/unit-4-intent-claude-code-backend.md` (pillars, findings 1-7, recommended architecture = dynamic workflow orchestrator + engine-owned guardrails; to be amended by this stage).

## Open questions

### Q1 - Command steps in workflow mode (no Docker)

[Answer]: A - subagents execute command steps in workflow mode. Each command step becomes a subagent step in the workflow; the subagent runs the command in Claude Code's own environment and reports exit code/verdict. No engine process needed at runtime. (Raw: "A")

### Q2 - Module split boundaries

[Answer]: A (the three-module split) with the light module named `agentic_dev_loop.core` instead of `.model` - user's call, accepted. Rationale agreed: "model" collides with LLM-model vocabulary (llm.model configs); "core" = the canonical pipeline definition. Accepted caveat: "core" invites junk-drawer creep - mitigated by a documented module contract (pure, light deps, no I/O beyond YAML parse) and a guard test asserting `core` imports nothing from `engine`/`viz` or heavy deps. (Raw: "A but call it .core instead of .model / wdyt? If you don't like it, push back")

### Q3 - Workflow JS mechanism (how pipeline.yaml becomes steps)

[Answer]: A (REVERSED from B on 2026-10-21) - parse at BUILD time, not runtime. At workflow creation, Claude (with tools) imports `agentic_dev_loop.core`, parses pipeline.yaml, and generates the concrete workflow JS with steps baked in; the skill's bundled JS is a template the build step fills. Runtime never parses. (Raw: "You know what - I changed my mind. pipeline.yaml should be parsed at build time, not runtime")

### Q4 - Action steps (commit) in workflow mode

[Answer]: C - a small trusted CLI shim (subagent-invoked) that reuses the engine's commit logic without the sandbox. Commit policy (clean-worktree-at-start, trusted hooksPath, templated message) extracted into a docker-free callable (in `core` or a thin `engine.commit` helper); the workflow's commit step = subagent invoking that shim and reporting the result. Deterministic policy stays real code, not prompt re-encoding. (Raw: "Ok, C")

### Q5 - Skill+workflow JS vs intent doc's MCP-tools architecture

[Answer]: Yes, drop MCP - skill + bundled workflow JS only; subagents invoke `agentic_dev_loop.core`/`viz` via shell/python; the intent doc's ae_* MCP-tool recommendation is amended out of unit-4 scope. (Raw: "Yes, drop MCP (Recommended)")

### Q6 - Visualiser in workflow mode

[Answer]: A - subagent-launched live server. A workflow step's subagent starts `agentic-dev-loop report` in the background (free-port fallback) and emits the URL into chat/state; each step's subagent POSTs stage events to the server's local HTTP ingest endpoint; browser keeps the live SSE stream. Viz gains a small write path (POST events) alongside its read path (SSE); engine mode keeps its in-process VizBus unchanged. (Raw: "A")

### Q7 - Verdict protocol and retry loops in workflow mode

[Answer]: A - same verdict contract in workflow mode. Verdict agents still write `VERDICT: PASS/FAIL` artifacts; each subagent step reports its verdict in its final output; the workflow JS routes on_pass/on_fail/goto with its own max_attempts loop counter. One pipeline.yaml stays truthful in both modes. (Raw: "A - Same verdict contract (Recommended)")

## Round 2 - Claude Workflow-tool feedback (2026-10-22)

Claude Code's install-time feedback on the shipped skill was evaluated and verified against official Workflow-tool docs: the shipped workflow.js.template targets OUR workflow.cli runner (`export default async function workflow($)`, `$.run`, runtime prompt-path reads) - it is NOT Workflow-tool-compatible (no `meta` literal export; script body has no fs/shell/network). Two-template approach accepted (P0 set + P1/P2).

### R2-Q1 - Visualiser in the claude-code target

[Answer]: Viz is NON-NEGOTIABLE. Raw: "without viz, this whole adapter is pointless. So either it's possible to do viz, or we forget about plugging this into Claude". Resolution: viz IS possible via agent mediation - a step-agent launches `agentic-dev-loop report` (agents have full tools incl. Bash; the server serves the runs view and SSE), later step-agents POST stage events to the ingest endpoint (prompt-instructed, best-effort), final agent terminates the server (or TTL). Event emission = prompt-enforced best-effort observability (not a security boundary); the /workflows progress view remains the guaranteed fallback; verdict routing stays deterministic in the script.

### R2-Q2 - Prompt strategy in the claude-code target (BR-5)

[Answer]: *(pending)*
