# Unit 11 Intent: Output-Format Robustness

- **Status**: INTENT (drafted 2026-11-07); APPROVED by user 2026-11-07T18:55Z; Q1-Q5 all answered; Workflow Plan approved; Functional Design in progress
- **Source**: planning-docs/059-copilot-anthropic-review-collapse.md + live engine investigation (2026-11-07)
- **Directive** (user, 2026-11-07, verbatim): "Read planning-docs/059-copilot-anthropic-review-collapse.md. This highlighted brittleness in our loop - it can easily be tripped up by model output. The solution is not to patch Anthropic's particular brand of waffle, but to understand why our pipeline is so sensitive to output format, and make it more robust to it"

## What happened (059 in one paragraph)

The Copilot endpoint's Anthropic models collapse on agent steps: the first turn is tool-less narration, zero terminal requests, then FINISHED. The loop read that as a clean completion, captured the narration as the review artifact, found no verdict line, and burned the attempt budget retrying the same broken review. An engine nudge telling the model to act was ignored (probe-proven), and the self-retry mitigation only re-ran the broken review. The smoke pins were moved to gpt models as a workaround; the brittleness itself is untouched.

## The diagnosis: prose as a control channel

The pipeline is a state machine whose control signals are derived from the shape and prose of model output, not from verifiable state. Four prose-trust points plus one wire-trust point:

| Layer | Control signal today | Where | What trips it |
|---|---|---|---|
| L1 Completion | FINISHED status inherited from turn shape | stages.py:219; SDK agent.py ~643 (content with zero tool calls => FINISHED) | Tool-less narration reads as a clean, non-truncated completion; no engine code path exists for "finished but took zero actions" |
| L2 Capture | last agent prose becomes the artifact | outputs.py:39-43; SDK response_utils.py get_agent_final_response (any final agent MessageEvent) | Narration becomes the review artifact verbatim; only empty strings are rejected |
| L3 Verdict | free-text "VERDICT: X" line parsed from prose | core/verdict.py; stages.py:752-803 (verdict drives routing) | No verdict line => NO_VERDICT => attempt-bounded retry re-runs the same broken review |
| L4 Context | model prose re-injected as fact | stages.py:392-431 build_context ("## Stage artifacts", 24k cap); own-artifact filter at 708-710 | Garbage artifact becomes evidence for later attempts; no verified-vs-claimed distinction |
| L5 Wire | adapter output shapes trusted | engine/provider_wire.py; engine/llm.py | finish_reason=tool_calls with tool_calls=None (opus-4.8); gateway silently drops ARRAY-encoded tool results (kimi) |

The core insight: the loop was designed to read meaning from prose at every control point. Each design choice was locally convenient (execution_status is the obvious SDK field; get_agent_final_response is the obvious capture call; a verdict line is the simplest contract) and globally fragile. Anthropic's waffle tripped it because the loop treats the model as a trustworthy reporter of its own behavior and of world state, and treats free text as a control channel. Both are category errors.

## Why the shipped mitigations failed

The nudge and the self-retry were text-channel responses to a behavior-channel problem: a model that refuses to act will not act on a message telling it to act (probe-proven). Retrying with a modified message does not change a structural refusal. Remediation must differ in kind from the thing that failed.

## Robust patterns already in the codebase (to generalize)

- Command/action steps: exit code is the verdict; "unclear is impossible" (stages.py:935-995).
- Brief step: mechanical anchor check (brief_coverage.py extract_anchors/missing_anchors, COVERAGE_RETRY_CAP=3); detection is mechanical even though the remedy re-prompts.
- Capture-first: the pipeline reads conv.state.events and writes artifacts itself (run_agent); agents never file their own reports.
- Per-attempt artifact hygiene (clear_attempt_artifacts), own-artifact filter (stages.py:708-710).
- Fail-fast, no silent fallbacks (repo rules).

## Design directions

### Direction A - Completion from actions, not turn shape

A step is complete iff it took at least one verifiable action. Zero-action FINISHED becomes a distinct failure class ("no actions taken") on attempt 1, not a phantom pass. Safe for "already at target state" outcomes: a legitimate no-op verification still runs terminal commands, so actions exist.

### Direction B - Verdict on a structured channel

Move the verdict out of prose: a schema-validated tool call (e.g. a record_verdict tool on the review contract) or a structured FinishAction payload. Parsing becomes schema decoding instead of prose scanning. "No verdict tool called" is detectable from the event stream immediately, without text parsing, and routable to a different remediation than retry. A model that refuses to act will not call the verdict tool either, and that is correctly detected as failure instead of NO_VERDICT.

### Direction C - Prose degraded to decoration in context injection

"## Stage artifacts" gets labeled as the model's own claims; mechanically-verified state (file diffs, exit codes, test output) becomes the primary context. Unverified prose never becomes evidence in another attempt. The own-artifact filter was the first instance of this lesson; this generalizes it.

### Direction D - Wire-shape validation at the adapter boundary

Post-build invariant checks in provider_wire/llm: finish_reason=tool_calls implies non-empty tool_calls; every tool call in the outgoing request has its result present in the history. force_string_serializer exists because the gateway drops content silently; invariants surface such drops at the wire instead of as end-to-end mysteries.

### Direction E - Fail fast on structurally-broken behavior

Key retry policy on failure shape, not on message content: zero-actions and no-verdict-tool are model-behavior failures. Bump/retry re-runs the same refusal; fail the step instead (or pick a remediation that differs from the thing that failed).

## Scope estimate

- **Request type**: hardening/refactor of the pipeline control layer (enhancement).
- **Scope**: multiple components (engine stages.py, outputs.py, verdict.py, provider_wire.py, llm.py, agent contracts review.agent.md/dev.agent.md, tests).
- **Complexity**: complex. Each direction is small on its own; the contract changes touch the dogfood agent.md files and the tests that pin current behavior.

## Extension applicability (preview)

- **External Surfaces**: YES. The SDK conversation/event layer is an external surface; L1/L2 rely on probed SDK behavior (agent.py FINISHED-on-content). EXTSRF-01 probes already exist from the 059 investigation; the validated-model table lands in Functional Design.
- **Property-Based Testing (partial)**: verdict-parsing round-trips and completion-shape invariants are PBT-shaped (PBT-02/PBT-03).
- **Security Baseline**: mostly N/A (library, no new network surface); SECURITY-15 fail-safe defaults applies to L1/E (fail closed, never open).
- **Resiliency Baseline (scoped, directional)**: RESILIENCY-10 (timeouts, fail fast on dead behavior) resonates with D/E; most rules N/A.
- **Domain Standards**: applies at Functional Design (descent audit for any new types, e.g. the failure-class model).

## Workflow Plan (unit-11)

### Stage plan (Q5 answer: 3-stage, unit-9 style)

| Stage | Artifact | Gate |
| --- | --- | --- |
| Functional Design | construction/plans/unit-11-functional-design-plan.md + construction/unit-11/functional-design/ | REVIEW REQUIRED; Q2 (verdict channel), Q3 (acceptance), Q4 (prose fate) resolved here |
| Code Generation | construction/plans/unit-11-code-generation-plan.md + construction/unit-11/code/ | Plan approval before edits; TDD red-green-refactor |
| Build and Test | construction/unit-11/build-and-test/ (build-instructions.md, unit-test-instructions.md, integration-test-instructions.md, performance-test-instructions.md, build-and-test-summary.md) | Suite green per Q3 answer |

### Implementation sequence (Q1 answer: all five, A -> E -> D -> B -> C)

- A: completion from actions, not turn shape (zero-action FINISHED = distinct failure class)
- E: failure-shape-keyed retry (fail fast on structural refusals)
- D: wire-shape invariants at the adapter boundary (provider_wire/llm)
- B: structured verdict channel (shape per Q2 answer)
- C: prose degraded to decoration in context injection (fate per Q4 answer)

### Notes

- [ ] Intent approved (2026-11-07T18:55Z) - [x]
- [ ] Q1/Q5 answered and recorded - [x]
- [ ] Q2/Q3/Q4 deferred to Functional Design, answerable anytime - [ ]
- [ ] Functional Design (next)

## Open questions (answers recorded here as they are given)

## Question 1
Which directions does unit-11 ship?

A) All five (A-E) as one cohesive unit, sequenced A -> E -> D -> B -> C

B) A + E first (the fail-fast core; no agent-contract changes), B/C/D as follow-up work

C) Only Direction A (smallest change, kills the phantom pass), others later

X) Other (please describe after [Answer]: tag below)

[Answer]: A) All five (A-E) as one cohesive unit, sequenced A -> E -> D -> B -> C (user, 2026-11-07)

## Question 2
Direction B shape: how should the verdict travel?

A) New record_verdict tool on the review contract (structured tool call; prose final response remains for humans only)

B) Structured FinishAction payload (add FinishTool back to the review contract with a schema)

C) Keep the prose VERDICT line but add mechanical verification around it (strict regex, position checks, event-stream no-op detection)

X) Other (please describe after [Answer]: tag below)

[Answer]: A) New record_verdict tool on the review contract; structured tool call; prose final response remains for humans only (user, 2026-11-07)

## Question 3
Acceptance criteria: what proves unit-11 worked?

A) A regression fixture built from the real 059 collapse event stream (tool-less narration): zero-action FINISHED fails the step fast with no phantom pass and no attempt-budget burn; existing suite stays green

B) A plus a live smoke re-run against the Copilot endpoint with the previously-collapsing Anthropic pins

C) Mechanical tests only (fake event streams); no live-model gate

X) Other (please describe after [Answer]: tag below)

[Answer]: B) Regression fixture from the real 059 collapse event stream + live smoke re-run against the Copilot endpoint with the previously-collapsing Anthropic pins (user, 2026-11-07)

## Question 4
What happens to prose artifacts (the captured final response) after this unit?

A) Keep prose as the human-readable artifact only: logged and displayed, never routed on; control decisions move to structured channels

B) Drop prose artifacts entirely; only structured outputs survive

X) Other (please describe after [Answer]: tag below)

[Answer]: A) Prose stays as the human-readable artifact: logged and displayed, never routed on; control decisions move to structured channels (user, 2026-11-07)

## Question 5
How should unit-11 run through AI-DLC?

A) 3-stage plan like unit-9: Functional Design -> Code Generation -> Build and Test (NFR Requirements/NFR Design/Infrastructure Design N/A, no infra work)

B) Full per-unit stages including NFR Requirements

C) Intent-only for now (like units 6-8); implementation driven later outside AI-DLC

X) Other (please describe after [Answer]: tag below)

[Answer]: A) 3-stage plan like unit-9: Functional Design -> Code Generation -> Build and Test; NFR/Infra stages N/A (user, 2026-11-07)

## Manual follow-up: staged prompt files (2026-11-08)

The new record_verdict agent contracts are staged inactive at
agentic-dev-loop/sdk_agents/review.agent.md.new and qa.agent.md.new (the
active prompts were restored to the HEAD prose contract so the
pre-unit-11 pipx engine keeps running). After the code work lands and the
record_verdict engine is installed, the operator must MANUALLY move each
.new file over its active name and re-point tests/test_qa_prompt.py back
at the active qa prompt. Until then the dogfood loop runs the old prose
contract, and the new engine's B-2 load check refuses the old prompts by
design. See construction/unit-11/code/review-2026-11-08.md.
