# Unit 11 Functional Design - Domain Entities

## Phase 1: Domain Exposition (DOMAIN-01..03)

The pipeline control layer is a brownfield domain: its concepts already exist in
code (src/agentic_dev_loop/engine/, core/). The exposition below answers the six
probe dimensions from the live codebase, citing the requirements they serve.
No concept below is proposed before its exposition answer exists (DOMAIN-02).

### Q1. Concepts named (serves FR-11.1..11.5)

The domain names: attempt, step, verdict, artifact, completion, failure class,
wire shape. Today: `attempt`/`max_attempts` are LoopState fields (graph.py);
`step` is a pipeline.yaml entry with a kind (brief/command/agent/action);
`verdict` is a prose-parsed value (core/verdict.py Verdict.from_text) keyed to a
step's `verdicts:` config; `artifact` is the captured final response
(engine/outputs.py CapturedOutput); completion is the SDK's
execution_status == FINISHED (stages.py:219). None of these names change in
unit-11; what changes is which channels carry them (FR-11.1 completion from
actions, FR-11.2 verdict from a tool call, FR-11.3 artifact display-only).

### Q2. State ownership (serves FR-11.1, FR-11.2, FR-11.4)

- The conversation's event stream is owned by the SDK conversation object and
  read by the engine before close (stages.py run_agent, capture-first pattern).
  Unit-11 keeps this: the event stream is the single source of truth for
  actions taken and tool calls made.
- The verdict's allowed values are owned by the pipeline config
  (pipeline.yaml `verdicts: {pass, fail, unverifiable, env}`), not by the
  model. FR-11.2 keeps ownership there: the tool argument is validated against
  the config, the model proposes, the config disposes.
- Wire shapes are owned by the adapter layer (engine/provider_wire.py,
  engine/llm.py). FR-11.4 adds validation there, at the boundary.

### Q3. Runtime contexts (serves FR-11.1, FR-11.4, FR-11.5)

- Agent steps (dev, review, qa) run inside an SDK conversation with N
  iterations; command steps and the native brief step do not. Zero-action
  detection (FR-11.1) applies only where an event stream exists: agent steps.
- The chat path (force_string_serializer) and the Responses path split the
  wire layer today; FR-11.4 invariants must state which path each check runs
  on (see business-rules.md D rules and validated-model.md assumptions).

### Q4. Change-togetherness (serves FR-11.2, FR-11.5)

- The verdict channel, the review/qa agent contracts (sdk_agents), and the
  engine's verdict extraction change together: the engine requires
  record_verdict whenever `verdicts:` is configured, and the dogfood contracts
  list the tool in their frontmatter. There is no half-state: a contract
  without the tool against a new engine fails loudly at load, never silently
  falls back to prose parsing.
- Failure classification (FR-11.1, FR-11.5) and the router's bump semantics
  change together: the unclear-verdict bump path exists only for
  prose-parsed verdicts and disappears with them.

### Q5. Invariants and failure modes (serves FR-11.1..11.5, NFR-11.1)

- Invariant: a finished agent step that took zero actions is a failure,
  regardless of execution_status (FR-11.1).
- Invariant: a step with `verdicts:` configured completes only on a validated
  record_verdict tool call (FR-11.2); absent, conflicting, or out-of-set values
  are distinct failure classes (FR-11.5).
- Invariant: every assistant tool_call in the outgoing request has a tool
  result visible in the history; finish_reason=tool_calls implies non-empty
  tool_calls (FR-11.4).
- Failure modes named today by prose parsing (NO_VERDICT, AMBIGUOUS) become
  event-stream classifications; none of them silently pass (NFR-11.1).

### Q6. Actors (serves FR-11.2, FR-11.3, FR-11.6)

- Actors: the dev/review/qa model agents (propose, act, report), the pipeline
  engine (classifies, routes, records), the SDK runtime (executes tool calls,
  emits events), the wire adapter (serializes), and the human operator (reads
  run records and smoke outputs). The model agent's only control-channel power
  after unit-11 is the structured verdict tool call; everything else it says is
  a claim, displayed but never routed on (FR-11.3).

## Phase 2: Answer-derived model (DOMAIN-03..08)

### Concept model with trace (DOMAIN-03)

| Concept | Kind | Trace (exposition answer) |
| --- | --- | --- |
| EventStream | existing read model (SDK conv.state.events) | Q2, Q3 |
| FailureClass (new, enum) | value object | Q5; FR-11.1, FR-11.5 |
| RecordVerdictAction (new) | tool-input schema (pydantic Action subclass) | Q2, Q6; FR-11.2 |
| Verdict | existing (core/verdict.py) with a new constructor from event stream | Q2; FR-11.2 |
| Artifact | existing (CapturedOutput); re-labeled as display-only claim | Q6; FR-11.3 |
| VerifiedState (new section builder) | value object assembled by the engine | Q6; FR-11.3 |
| WireShapeValidator (new) | stateless check at the adapter boundary | Q3; FR-11.4 |
| StepRouter | existing (stages.py make_router); bump semantics narrowed | Q4; FR-11.5 |

### Per-requirement coverage (DOMAIN-04)

FR-11.1 -> EventStream + FailureClass + StepRouter rules A (business-rules.md).
FR-11.2 -> RecordVerdictAction + Verdict + StepRouter rules B.
FR-11.3 -> Artifact + VerifiedState rules C.
FR-11.4 -> WireShapeValidator rules D.
FR-11.5 -> FailureClass + StepRouter rules E.
FR-11.6 -> fixture + smoke plan (business-rules.md acceptance section).
All requirements are covered by at least one concept; no concept lacks a requirement.

### Rich domain audit (DOMAIN-05)

No delegating entities: every concept owns its data (FailureClass owns the
classification vocabulary; RecordVerdictAction owns its argument schema;
Verdict owns its value). No service-named value objects, no "the X model"
labels, no per-endpoint services, no behaviourless aggregates. VerifiedState is
a value assembly, not a manager: it bundles facts the engine already verified.

### Descent audit (DOMAIN-06)

| Proposal | Lowest valid level | Justification |
| --- | --- | --- |
| FailureClass enum | 1 (value object) | A pure classification vocabulary over event streams and verdict-channel states |
| RecordVerdictAction | 3 (new entity) | A new tool-input schema with a real invariant (value must be in the step's configured set); it is data, not a helper |
| Verdict.from_events constructor | 2 (method on existing entity) | The Verdict entity gains a second constructor; it is not a free function because validation stays with the entity |
| WireShapeValidator | 4 (domain service) | Stateless, cross-boundary check coordinating the adapter and the LLM client; named by its rule, not by a person (no Manager/Helper, DOMAIN-07) |
| VerifiedState section builder | 5 (application service) | Assembles the context message consumed by multiple entry points (every agent step's build_context); one shared assembly point |
| StepRouter changes | 2 (methods on existing entity) | The router exists; FR-11.1/FR-11.5 narrow its bump semantics |

No Repository/DAO/unit-of-work abstractions are introduced (DOMAIN-08):
persistence stays the run state directory and audit.md as today.
