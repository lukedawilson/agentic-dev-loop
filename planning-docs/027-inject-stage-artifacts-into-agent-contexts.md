# Inject prior-stage artifacts into downstream agent contexts - Plan

> **Status: COMPLETED**

## Scope

Engine only: `src/agentic_dev_loop/` and `tests/`. Consumer repos adopt the
behaviour by upgrading the engine. The QA prompt-content changes (Changes
5-6) are tooling paths, not swept by the loop commit.

## Background

In a consumer-repo QA incident (2026-10-25), the review stage flagged a real
defect as a non-blocking note (described in `review-findings.md`), verdict
APPROVED. The note had no carrier forward - QA never saw it, the defect
shipped, and only a post-QA probe campaign found it.

The engine mechanism behind the miss: every agent stage receives one shared
context string built at attempt start by `build_context`
(`engine/stages.py:199-221`) from subject + doc bundle + dependency policy +
expected-output-path names + retry notes. Stage artifacts
(`implementation-summary.md`, `review-findings.md`, `qa-report.md`) are
captured to the run state dir after each step (`run_agent`,
`engine/stages.py:129-132`) and never injected into any later stage's
message. Review verdicts are binary (APPROVED / NEEDS CHANGES), so findings
with nuance have no routing path: the only feedback carrier is retry notes,
and those flow only on failure. Review-stage findings are effectively
write-only.

The same probe campaign also motivated four probe-plan rules in the QA agent
prompt (variant matrix, outcome probes, concurrency probes, error messages
are assertions). They live in this repo's dogfood `qa.agent.md` but have no
contract coverage; Changes 5-6 add the engine-side tests and hand the
prompt-copy updates in consumer repos to a human commit.

## Changes

1. **LoopState carries accumulated artifacts** (`engine/graph.py`, LoopState
   class ~line 37-55): add `artifacts: Annotated[dict[str, str],
   _merge_artifacts]` (step name -> artifact text) with a module-level
   reducer `_merge_artifacts(a, b) = {**a, **b}`. The reducer is mandatory:
   the installed LangGraph (>=1.2) gives un-annotated fields a last-write-
   wins channel - verified by probe, one node's entry silently replaces the
   accumulated dict, no key-wise merge exists. LangGraph wraps any two-
   positional-arg callable in a BinaryOperatorAggregate, so the plain
   function works. Initial value `{}` in the initial-state construction
   (~line 173-194). The field is part of the checkpointed state, so
   `--resume` keeps it.

   **Delete the `context` field entirely** - after Changes 3-4 nothing reads
   it: drop the field from LoopState, the initial build at
   `engine/graph.py:176`, and the now-unused `build_context` import at
   `engine/graph.py:30`. Probe-verified safe for old checkpoints: LangGraph
   silently ignores schema-unknown keys in checkpoint values, input state,
   and node returns. Tests: `tests/test_stages.py` make_state drops the key;
   the bump-node test asserts the new bump shape instead.

2. **build_context renders the artifacts** (`engine/stages.py:199-221`): gain
   a parameter `artifacts: dict[str, str] | None = None`. After the doc
   bundle, before the dependency policy, render a section
   `## Stage artifacts` with one subsection per artifact in dict order (run
   order; a re-run step replaces its entry in place). Each content is capped
   at `ARTIFACT_CONTEXT_CAP = 24_000` characters with a truncation marker
   naming the full path under the run state dir. `None`/empty renders no
   section - existing call sites and snapshots stay green.

3. **make_agent_node rebuilds its message and injects the just-captured
   artifact** (`engine/stages.py:281+`):
   - Rebuild per run: stop reading `state["context"]` at line 299; build the
     message instead with `build_context(runtime, state["docs"],
     state["subject"], state["notes"], artifacts=state.get("artifacts",
     {}))`. This is the carrier fix: QA runs after review in the same
     attempt, so its message must be built after review captured its
     findings. A bump-only rebuild would deliver artifacts only on retry and
     never fix the incident.
   - After `run_agent` returns, when the step captures (artifact or
     produces) and `result.response` is not None, add
     `{"artifacts": {step.name: result.response}}` to the returned update on
     the no-verdicts branch (line 330-333, the dev-step branch), the pass
     branch, the unverifiable branch, and the verdict-fail branch (whose
     update gains the key alongside its existing notes). Use
     `result.response`, not a file re-read: it is the exact redacted text
     `CapturedOutput` wrote (`outputs.py`). The capture-error, unclear, hard-
     failure, and locked-deps branches return no entry - those either have
     no response or already carry the feedback in notes.

4. **make_bump_node drops the context rebuild** (`engine/stages.py:549-563`):
   remove the `build_context` call; keep `clear_attempt_artifacts` and the
   attempt/step_verdicts/failed updates (the update no longer carries a
   `context` key). Artifacts survive in state untouched (the files are
   deleted by `clear_attempt_artifacts`, but the state text persists), and
   the retried agent's message is rebuilt by Change 3.

5. **Extend the QA prompt contract tests** (`tests/test_qa_prompt.py`): add
   string-presence assertions for the four dogfood-prompt rules (Variant
   matrix, Outcome probes, Concurrency probes, Error messages are
   assertions), matching the prompt's exact-case headings, continuing the
   024-style contract enforcement.

6. **Tooling paths** - the QA prompt content updates in consumer repos
   (`agentic-dev-loop/sdk_agents/qa.agent.md` in each consumer repo) are
   tooling paths: the loop commit does not sweep them. Deliverable: state
   that these prompt changes will need a human commit. The `src/` and
   `tests/` changes in this plan are sweepable.

## Decisions

- One mechanism, two deliveries: every agent node rebuilds its message from
  state just before running, so accumulated artifacts reach (a) downstream
  stages in the same attempt - the incident fix - and (b) retried agents via
  the bump loop.
- All steps with an artifact or produces file are injected, uniformly - no
  per-step configuration. Downstream stages read what they need; the cap
  bounds the cost.
- Inject everything, including a step's own prior-attempt artifact (a
  retried dev sees its attempt-1 summary as a reminder). Within one attempt
  each step runs at most once, so a step never sees its own same-attempt
  artifact.
- Fail-path duplication is accepted: the retry note already quotes the
  response and the artifact entry repeats it. Both copies are cap-bounded,
  and the note format is an existing contract.

## Edge cases

- Missing artifact file / capture error (a produces step that wrote nothing,
  or empty response): no entry, no error - `result.response` is None, the
  condition skips it.
- Oversized artifact: cap + truncation marker; never fail a run on artifact
  size.
- Secrets: artifacts are the same redacted agent text the pipeline already
  writes to the run state dir; injection adds no new boundary. The existing
  secrets policy (never copy a value into reports) continues to apply.
- Resume: checkpoint restores the artifacts field. Pre-change checkpoints
  lack the key - nodes read `state.get("artifacts", {})`, and the
  hard-failed resume path (`engine/graph.py` ~line 223) must default
  `values.get("artifacts", {})` into the rebuilt initial state. A resumed
  run that re-runs a step replaces that step's entry via reducer merge.
- Context growth: notes and artifacts are never cleared across attempts;
  each artifact copy is cap-bounded, notes are unchanged from today.

## Acceptance checks

1. Unit (`tests/test_stages.py`): `build_context` with a two-entry artifacts
   dict renders one subsection per artifact with its content; content beyond
   the cap is truncated and the marker names the run-state path; no
   artifacts renders no section.
2. Unit (`tests/test_stages.py`): `make_agent_node` with the existing
   fake-agents capture stub returning a marker response yields an update
   whose artifacts entry contains the marker - on the no-verdicts branch
   (dev-like), the pass branch, the verdict-fail branch, and the
   unverifiable branch; the capture-error and unclear branches return no
   artifacts key.
3. Graph-level (`tests/test_graph.py`): two-agent pipeline A -> B with a
   stub LLM; A's captured response contains a marker string; assert B's
   message contains the `## Stage artifacts` section and the marker, and
   A's own attempt-1 message contains no artifacts section (it runs before
   anything accumulates).
4. Retry: review fails, dev retries; the bumped dev's message contains the
   review artifact, the retry notes, and dev's own attempt-1 artifact
   (inject-everything).
5. `tests/test_qa_prompt.py` green with the four new rule assertions.
6. Full suite green; ASCII policy holds in every touched file; the 026 scrub
   holds (committed files name no commercial product).
