# 035-finalize-nudge-and-verdict-cap.md

> **Status: COMPLETED**

Assumes 032 implemented first: the turn-level conversation hook it
introduces is the plumbing this plan reuses.

## Scope

Engine (one generic agent-step feature), consumer pipeline (one number).
No new verdict vocabulary, no new outcomes, no alarms.

## Context

Consumer run 5367f0c488d8: the verdict agent hit its 60-iteration cap
twice with no final response; the unclear-verdict retry bounced the run
back through dev both times. The cap is deliberate and the two cut-offs
came from genuine probe load. User decisions: no-verdict stays an
attempt-bounded retry (explicit non-goal); the cap raise and the finalize
nudge are provisional; the engine domain has no concept of any specific
step such as a "qa" step, so everything here is step-agnostic config.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | Generic finalize nudge: optional `finalize_nudge_turns` on any agent step; at `max_iterations` minus that count the engine injects one nudge turn ("N turns remain: produce your final response now"). Template text, never step-named | AGREED (provisional) |
| D2 | Consumer verdict step `max_iterations: 60` to `100` | AGREED (provisional) |
| D3 | Non-goal: no new no-verdict outcome, no alarm, no retry change. Unclear or missing verdicts keep today's attempt-bounded retry exactly as is | AGREED |

## Changes

### 1. Engine: finalize nudge

- New optional `StepConfig.finalize_nudge_turns: int | None` (gt=0;
  validation: requires `max_iterations` and must be less than it).
- Injection point: the turn hook from 032 observes the turn counter; when
  the conversation reaches `max_iterations - finalize_nudge_turns`, the
  engine sends the nudge message once (exactly once, guarded against
  re-injection on retry of the same turn).
- The nudge is advisory: the agent may still exhaust its turns, and the
  existing stuck/unclear handling then applies unchanged.

### 2. Consumer pipeline (tooling path, provisional)

Verdict step `max_iterations: 100` with `finalize_nudge_turns: 10`. The
raise alone would only delay the same silent cutoff; the nudge is what
makes the raised cap safe.

### 3. README.md documentation (tooling path)

Update the `pipeline.yaml` reference (README.md) to document
`finalize_nudge_turns` (one YAML line, one table row) including the
validation rule and the advisory wording.

## Tooling-path deliverables

The consumer pipeline.yaml change and README.md. Human commits required.

## Verification

- TDD: fake-conversation tests asserting the nudge arrives at exactly the
  configured turn index and only once; config validation tests.
- Backward compatibility: absent = current behavior.
- ASCII only; no consumer names in this repo.
- Post-deploy observation: verdict steps no longer end silently at the
  cap; either a verdict arrives or the nudge is visible in the transcript
  before the cap.

## Provisional markers

D1 and D2 ship behind a note that both are provisional pending one
consumer run: if the nudge proves noisy (interrupting work the agent was
about to finish) or the raised cap inflates verdict-step cost without
fewer cut-offs, the numbers revert.
