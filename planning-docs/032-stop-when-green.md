# 032-stop-when-green.md

> **Status: COMPLETED**

## Scope

Engine (StepConfig surface, run loop, sandbox terminal executor) and the
dev prompt copies in this repo and the consumer repo (tooling paths; the
prompt rule below is already applied in both working trees).

## Context

Consumer run 5367f0c488d8: after the full suite was green (9/9 new tests,
787 passed) and the implementation summary existed, the dev agent spent
five further steps (about 140M input tokens) re-reading files it had just
written and reasoning about gates it cannot run offline. Separately, the
terminal tool's 300s per-action timeout killed the ~310s full suite once
(exit-124), forcing a re-run.

User decisions: the prompt rule is agreed; the mechanical termination must
be prompt-agnostic (it is: config regex + observable signals only) and
tool-agnostic (it keys on observations, never on prompt text or on which
tool produced them).

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | STOP WHEN GREEN + LONG-RUNNING COMMANDS prompt rules in both dev prompts | AGREED; applied to both working trees in this session |
| D2 | Mechanical stop-when-green termination signal in the engine, driven by config and observable signals only | AGREED (tool-agnostic) |
| D3 | `terminal_timeout` per-step knob on agent steps (mechanical half of the timeout fix; the inline `timeout 600` guidance is the prompt half) | AGREED |

## Changes

### 1. Prompt rules (tooling path, human commit)

Already applied to `agentic-dev-loop/sdk_agents/dev.agent.md` in this repo
and the consumer repo:

- STOP WHEN GREEN: once the final response is written, the full suite has
  passed locally, and every *Previous attempt feedback* item is addressed,
  the step is COMPLETE. No re-reading, no re-running checks, no
  cat/tail/grep of just-written files, no pre-arguing the conformance
  gate; the mechanical gate re-runs the exact command.
- LONG-RUNNING COMMANDS: the terminal kills at 300s per action; wrap the
  full suite in `timeout 600 ...`. The consumer copy states the suite
  takes about 5 minutes (310s).

### 2. Mechanical stop-when-green (engine)

- New optional `StepConfig.stop_when`: `{ green_command: <argv regex>,
  post_green_turns: <int, default 2> }`. Absent = current behavior.
- Signal: the agent's most recent terminal observation whose command
  matches `green_command` exits 0, and no workspace writes have happened
  since (the writes channel already tracks writes). No other inputs.
- Action: the engine injects one nudge turn ("Step complete: produce your
  final response now"), allows `post_green_turns` further turns, then
  closes the conversation; the capture-first path writes the artifact
  from the final response, unchanged.
- Degenerate case (the agent keeps verifying instead of summarizing): the
  close still happens; an empty or unusable capture falls into the
  existing unclear-verdict attempt-bounded retry. No new verdict
  vocabulary.
- Implementation seam: turn-level observation requires a turn-level hook
  into the SDK conversation (per-event subscription or a step-by-step run
  loop); the exact API is confirmed during implementation, with polling
  the conversation state from the run thread as fallback. This hook is
  shared plumbing: 035's finalize nudge reuses it.

### 3. Terminal timeout knob (engine)

- New optional `StepConfig.terminal_timeout: float | None` (agent steps,
  gt=0). `SandboxTerminalExecutor` uses it as the per-action timeout when
  set, overriding the SDK tool's 300s default; None = current behavior.
  Validation mirrors the existing command-only `timeout` field.
- The consumer dev step sets 900 (suite duration 310s with headroom).

### 4. README.md documentation (tooling path)

Update the `pipeline.yaml` reference (README.md, "### pipeline.yaml
reference") to document `stop_when` and `terminal_timeout`: one YAML line
in the example and one table row per field, with the same wording as
changes 2 and 3 above. The step-config contract is documented there; new
fields are not discoverable until it is.

## Tooling-path deliverables

`agentic-dev-loop/sdk_agents/dev.agent.md` in both repos (already edited,
uncommitted), the consumer pipeline.yaml (dev step `stop_when` and
`terminal_timeout`), and README.md. The loop commit will not sweep
tooling paths; human commits required.

## Verification

- TDD: config validation tests (invalid `stop_when` shapes fail loudly),
  stop-when-green signal tests against a stubbed conversation and fake
  sandbox (green command + no writes triggers the nudge and close;
  post-green writes reset the signal), timeout override test.
- Backward compatibility: absent config = current behavior; existing
  pipelines load unchanged.
- ASCII only; no consumer names in this repo.
- Post-deploy observation on the next consumer run: the dev step ends at
  the first green full suite instead of five verification steps later.
