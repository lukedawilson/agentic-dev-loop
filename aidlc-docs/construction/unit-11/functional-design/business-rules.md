# Unit 11 Functional Design - Business Rules

## A. Completion rules (FR-11.1)

- A-1. For every agent step, completion is `actions_taken >= 1`, where
  `actions_taken` is the count of ActionEvent entries in the conversation's
  event stream (a FinishAction counts: it is a tool call, the SDK's done
  signal).
- A-2. A conversation that ends FINISHED with zero ActionEvents is classified
  FailureClass.NO_ACTIONS. This includes the 059 shape: tool-less narration.
- A-3. NO_ACTIONS fails the step immediately (rule E-2), no bump, no retry.
  The run record reports: step, attempt, event count, the captured prose
  (display-only), and the classifier evidence.
- A-4. A-1..A-3 apply to agent steps only. Command steps keep exit-code
  verdicts; the native brief step keeps its mechanical anchor gate. A
  "tree already carried the implementation" dev run still executes terminal
  commands (tests), so it has actions and is unaffected.
- A-5. Truncation stays as today: execution_status != FINISHED is still
  truncated (stages.py:219). A-2 only reclassifies the phantom-clean case.

## B. Verdict channel rules (FR-11.2)

- B-1. The record_verdict tool is registered at engine import (agents.py
  registry path, same mechanism as terminal) and listed in the review and qa
  agent frontmatter `tools:` lists (BR-1: the frontmatter list is the exact
  capability allowlist; FinishTool/ThinkTool auto-add behavior unchanged).
- B-2. A step whose config carries `verdicts:` requires record_verdict in its
  tool allowlist. Load-time check: configured verdicts without the tool is a
  loud configuration error, never a silent prose fallback (NFR-11.1).
- B-3. Verdict extraction (new `Verdict.from_events`): scan the event stream
  for RecordVerdictAction events. Zero calls -> FailureClass.NO_VERDICT_TOOL.
  One or more calls -> validate each value against the step's verdicts_map.
- B-4. Value not in the configured set -> FailureClass.INVALID_VERDICT_VALUE;
  the report names the value and the configured set.
- B-5. Conflicting values across calls -> per FD-Q1 (fail fast / last wins /
  first wins; recommended: fail fast, FailureClass.CONFLICTING_VERDICT).
- B-6. The prose final response no longer carries the verdict: the VERDICT
  line contract in review.agent.md and qa.agent.md is replaced by the tool
  contract. The prose keeps the findings for humans (Q4=A).
- B-7. Dev has no `verdicts:` config and is unchanged (its pass today is
  stages.py:744-747 capture-present; its real gate is the test step's exit
  code). Verdict.from_text stays for any legacy consumer config, but this
  repo's pipeline.yaml no longer routes on it.

## C. Context-injection rules (FR-11.3)

- C-1. build_context splits the injected material into two labeled sections:
  "## Stage artifacts (each agent's own claims, not verified)" carrying the
  captured prose with the existing 24k cap, and "## Verified state
  (mechanically checked by the pipeline)" carrying only facts the engine
  verified: command-step exit codes (test/restore/deps gates), the brief
  coverage result, and the commit action outcome.
- C-2. The own-artifact filter (stages.py:708-710) stays.
- C-3. Nothing in "## Stage artifacts" may be routed on by any engine
  decision (Q4=A). Notes from prior attempts remain advisory feedback,
  addressed by dev per its contract rule 2, but they stay in the claims
  section, never the verified section.

## D. Wire-shape rules (FR-11.4)

- D-1. After adapter serialization in llm.py: if the surfaced finish_reason
  is tool_calls, the surfaced tool_calls list must be non-empty; otherwise
  raise WireShapeError (the opus-4.8 mis-shape).
- D-2. Before each outgoing request: every assistant tool_call in the
  message history must have a corresponding tool result; otherwise raise
  WireShapeError (the kimi array-drop blindness).
- D-3. Array-typed tool-result content on the chat path must be serialized
  via force_string_serializer (existing policy); the invariant checks confirm
  the policy produced a visible result rather than assuming it.
- D-4. WireShapeError fails fast with the offending shape named (NFR-11.1);
  it is not a retryable model-behavior class.
- D-5. Checks live at the adapter boundary (provider_wire.py/llm.py). Chat
  path: D-1, D-2, D-3 apply. Responses path: D-1, D-2 apply where the
  surfaced shape exposes the same fields (see validated-model.md
  unverified-assumptions list; parity is a Code Generation probe).

## E. Failure-shape routing rules (FR-11.5)

| Failure class | Shape source | Routing |
| --- | --- | --- |
| NO_ACTIONS | event stream | fail step now; halt run with report; no bump |
| NO_VERDICT_TOOL | event stream | fail step now; halt run with report; no bump |
| INVALID_VERDICT_VALUE | tool argument | fail step now; halt run with report; no bump |
| CONFLICTING_VERDICT | tool arguments | per FD-Q1; fail-fast option halts like the above |
| WIRE_SHAPE | adapter boundary | fail now; no bump |
| TRUNCATED | execution_status | existing semantics unchanged |
| verdict: fail / env / unverifiable | structured verdict value | existing on_fail / on_env / on_unverifiable routing unchanged |

- E-1. Structural classes (NO_ACTIONS, NO_VERDICT_TOOL, INVALID_VERDICT_VALUE,
  CONFLICTING_VERDICT under fail-fast, WIRE_SHAPE) never enter the bump path:
  retrying re-runs the same refusal (probe-proven in 059 with the nudge).
- E-2. The unclear-verdict bump path (stages.py router) is removed for
  structured steps: with a validated tool channel, unclear verdicts cannot
  occur. Content verdicts route exactly as before (on_pass/on_fail/on_env/
  on_unverifiable).
- E-3. Every structural failure writes a report (run state, last-error.md
  style) naming the class, the evidence, and the step/attempt before halting.

## Acceptance (FR-11.6, Q3=B)

- Regression fixture: reconstruct the 059 collapse as a fake event stream
  (tool-less narration message events, zero ActionEvents, FINISHED) and
  assert: the step fails fast, no phantom pass, no attempt-budget burn, and
  the report names NO_ACTIONS. Suite stays green (NFR-11.2).
- Live smoke: after implementation, re-run the smoke pipeline against the
  Copilot endpoint with the previously-collapsing Anthropic pins. Expected:
  if an Anthropic pin still narrates with no tool calls, the run fails fast
  with a named class instead of a phantom completion; if it acts and calls
  record_verdict, the run completes through the structured channel.

## Security Compliance

| Rule | Status | Note |
| --- | --- | --- |
| SECURITY-05 input validation | compliant by design | record_verdict values validated against the configured set (B-3/B-4); wire shapes validated (D-1..D-3) |
| SECURITY-15 fail-safe defaults | compliant by design | zero actions and missing verdict channel fail closed, never open (A-2, B-3, E-1) |
| SECURITY-03 app logging | N/A (no change) | failure reports carry evidence text, not credentials; existing scrub behavior unchanged |
| Others (01,02,04,06..14) | N/A | library change, no new network surface, no new persistence |

## Testable Properties (PBT partial: PBT-02/03/07/08/09)

- PBT-02 round-trip: RecordVerdictAction value -> Verdict.from_events -> value
  round-trips for every configured verdict value of review and qa.
- PBT-03 invariants: (a) zero ActionEvents implies NO_ACTIONS for every
  generated event stream regardless of execution_status; (b) verdict-channel
  classification is a pure function of (events, verdicts_map); (c) the wire
  validator rejects every generated (finish_reason, tool_calls) pair that
  violates D-1.
- PBT-07 generators: event-stream and wire-shape generators built from the
  real SDK event classes, not raw primitives.
- PBT-08: Hypothesis shrinking on, fixed seeds, run in CI alongside the
  example-based tests.
- PBT-09: Hypothesis (already the project's property framework, see
  tests/test_outputs_pbt.py).
- Example-based tests pin the 059 fixture and each failure class's routing
  (PBT-10, advisory: keep both).

## Amendments (2026-11-08, review round 1)

- A-5 / E (truncation): "truncation semantics unchanged" applies to
  non-verdict steps only. On a verdict step, a truncated conversation with
  no record_verdict call classifies as NO_VERDICT_TOOL and fails the run
  structurally (operator decision, review round 1; see
  ../code/review-2026-11-08.md). The empty-capture edge is accepted:
  truncated with no captured prose still takes the capture_error
  attempt-bounded retry.
- PBT-03(a) alignment (noted by the dev step): zero-action NO_ACTIONS
  classification applies to FINISHED conversations only.
