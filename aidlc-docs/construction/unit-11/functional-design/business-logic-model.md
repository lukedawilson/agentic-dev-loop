# Unit 11 Functional Design - Business Logic Model

## Control flow per step kind after unit-11

### Agent step (dev / review / qa)

```
run_agent (stages.py)
  conversation ends (SDK, execution_status FINISHED or truncated)
  engine reads conv.state.events BEFORE close (capture-first, unchanged)
  count ActionEvents
    zero AND FINISHED        -> FailureClass.NO_ACTIONS  -> halt (E-2)
    zero AND not FINISHED    -> TRUNCATED (unchanged path)
    >= 1                     -> proceed
  capture final response (outputs.py, unchanged) -> Artifact (display-only)
  verdict channel (steps with verdicts: only):
    Verdict.from_events(events, verdicts_map)
      no RecordVerdictAction           -> NO_VERDICT_TOOL    -> halt
      value not in verdicts_map        -> INVALID_VERDICT_VALUE -> halt
      conflicting values               -> per FD-Q1         -> halt (fail-fast)
      valid                            -> Verdict(value)     -> route
    route: pass -> on_pass; fail -> on_fail; unverifiable -> on_unverifiable;
           env -> default halt-with-report (unchanged)
  steps without verdicts: (dev) pass on capture-present (stages.py:744-747,
    unchanged; the test gate remains the real gate)
  router (make_router): bump path now reachable only by content verdicts'
    on_fail gotos and truncation paths; unclear-verdict bump removed
```

### Command step

Unchanged: exit code is the verdict (0 => pass, else fail, "unclear is
impossible"). Unit-11 adds nothing here; this is the pattern FR-11.1/11.2
generalize from.

### Brief step (native)

Unchanged: mechanical anchor coverage gate (brief_coverage.py,
COVERAGE_RETRY_CAP=3). Retry on the brief gate remains prompt-based but its
detection is mechanical; out of unit-11 scope.

## Where each check lives (module map)

| Check | Module | Notes |
| --- | --- | --- |
| NO_ACTIONS classification | engine/stages.py (run_agent or a new engine/completion.py helper) | reads events alongside the existing capture |
| Verdict.from_events | core/verdict.py (new constructor, DOMAIN-06 level 2) | validation stays with the entity |
| RecordVerdictAction + tool registration | engine/agents.py (import-time registry) + sdk_agents/{review,qa}.agent.md frontmatter | BR-1 allowlist; load-time requirement check in stages.py or config validation |
| Load-time verdict-tool check | engine config path (where step configs are validated) | loud error if verdicts: without the tool |
| build_context claims/verified split | engine/stages.py build_context (392-431) | VerifiedState builder = application service (level 5) |
| WireShapeValidator | engine/provider_wire.py or llm.py adapter boundary | domain service (level 4); D-1..D-3 |
| Failure routing table | engine/stages.py make_router | narrowed bump semantics per E |

## State changes (LoopState, graph.py)

- New field `failure_class: str | None` recorded on structural failures so the
  run record and any report can name the class.
- `step_verdicts` unchanged: structured verdict values map onto the same
  pass/fail/unverifiable/env vocabulary.
- `attempt`/`max_attempts` semantics unchanged for content retries (on_fail
  goto), unreachable for structural classes.

## Error report channel

Structural failures write the class, the step, the attempt, the triggering
evidence (event count, tool-call summary, conflicting values, wire shape) and
the captured prose (display-only) into the run state report (last-error.md
style). Nothing is silently swallowed (NFR-11.1).
