# Functional Design Plan - Unit 11 (Output-Format Robustness)

Unit intent: `aidlc-docs/unit-11-intent-output-format-robustness.md` (approved 2026-11-07; all Q1-Q5 answered)
Requirements: the intent doc's diagnosis table and Directions A-E serve as the requirements artifact (FR-11.1..FR-11.5 below); no separate requirements.md section exists for unit-11 (minimal-depth intent recording).
Normative ground: planning-docs/059-copilot-anthropic-review-collapse.md plus live engine investigation (2026-11-07).

## Requirements mapping (trace source)

| ID | Requirement |
| --- | --- |
| FR-11.1 | Direction A: completion derives from actions taken, never from turn shape; zero-action FINISHED is a distinct failure class |
| FR-11.2 | Direction B: verdict travels on a structured channel (record_verdict tool, Q2=A); prose never carries the verdict |
| FR-11.3 | Direction C: prose artifacts are display-only (Q4=A); context injection separates verified state from model claims |
| FR-11.4 | Direction D: wire-shape invariants validated at the adapter boundary |
| FR-11.5 | Direction E: failure-shape-keyed routing; structural refusals fail fast without retry |
| FR-11.6 | Acceptance (Q3=B): regression fixture from the real 059 collapse event stream (zero-action FINISHED fails fast, no phantom pass, no budget burn) AND a live smoke re-run against the Copilot endpoint with the previously-collapsing Anthropic pins |
| NFR-11.1 | No silent fallbacks; every failure class reports loudly with the evidence that triggered it (repo rule) |
| NFR-11.2 | Existing suite stays green; behavior changes only where a prose-trust point is replaced by a structured or verified signal |

## Execution Checklist

- [x] Analyze unit context (intent doc Q1-Q5, 059, live engine reads of stages.py/verdict.py/outputs.py/provider_wire.py/llm.py, agent contracts, pipeline.yaml)
- [x] Probe external surfaces (pinned openhands-sdk ~1.17.0: ActionEvent/action/tool schema, tool registration path, agent factory; see validated-model.md)
- [x] Generate `aidlc-docs/construction/unit-11/functional-design/domain-entities.md` (concept model, trace, descent audit)
- [x] Generate `aidlc-docs/construction/unit-11/functional-design/business-rules.md` (failure classes, routing rules per FR-11.1..11.5, security compliance, testable properties)
- [x] Generate `aidlc-docs/construction/unit-11/functional-design/business-logic-model.md` (control flow per step type, where each check lives)
- [x] Generate `aidlc-docs/construction/unit-11/functional-design/validated-model.md` (EXTSRF validated-model table, unverified assumptions)
- [x] Resolve open design decisions FD-Q1, FD-Q2 (answers recorded below)
- [ ] Present Functional Design completion message and await approval

## Open Design Decisions

### Question FD-Q1
Multiple record_verdict calls with conflicting values: the model calls the verdict tool twice in one conversation with different verdict values (e.g. APPROVED then NEEDS CHANGES). Today's prose parser mirrors this case as AMBIGUOUS. What should the structured channel do?

A) Fail fast: conflicting verdict tool calls are a structural malfunction; the step fails loudly with the conflicting values reported, no retry (recommended - the channel is structured, conflict means the model is incoherent, and prose's last-line-wins ambiguity was part of the brittleness)

B) Last call wins: mirror today's prose semantics; the final record_verdict call's value is the verdict

C) First call wins: the first record_verdict call's value is the verdict

[Answer]: A - fail fast: conflicting verdict tool calls are a structural malfunction; the step fails loudly with the conflicting values reported, no retry (user, 2026-11-07)

### Question FD-Q2
record_verdict tool payload schema: what fields does the tool take?

A) verdict only: a single required string field, validated against the step's configured verdict values; the human-facing findings stay in the prose final response (recommended - smallest structured surface, validation is a pure equality check)

B) verdict + reasons: add an optional free-text reasons field carried into the run record for humans

C) verdict + evidence: add optional file paths so the verdict carries mechanically checkable references for later steps

[Answer]: A - verdict only: a single required string field, validated against the step's configured verdict values; the human-facing findings stay in the prose final response (user, 2026-11-07)
