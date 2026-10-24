# 024-qa-unverifiable-verdict.md

> **Status: COMPLETED**

## Scope

This repository only (the engine). Consumer repos (e.g. yosk-cms) are not
touched; they adopt the behaviour by updating their own pipeline.yaml and
their own copies of the sdk_agents prompts when they pull the new engine.

## Background

The plan 023 review showed QA fabricated verification: the sandbox cannot
execute docker/host-only checks, so QA substituted unrelated generic CLI
probes and reported PASS. The plan then flipped to COMPLETED while its real
acceptance checks were never executed.

Decision (owner): QA verdicts gain UNVERIFIABLE for checks the sandbox
cannot execute. UNVERIFIABLE is a recorded, non-blocking outcome: the run
moves on, and the record shows exactly what was and was not verified. No
gate, owner, handoff, or BLOCKED concept is added to the domain.

QA must not substitute unrelated generic probes for unreachable acceptance
checks; that fabricates verification. The check is reported UNVERIFIABLE
with the blocking reason instead.

## Changes

### 1. Verdict domain: a third keyword (core/verdict.py, core/config.py)

- `VerdictSpec` (src/agentic_dev_loop/core/config.py, line 17) gains an
  optional third field `unverifiable: str | None = None`, alongside
  `pass_`/`fail`.
- `Verdict.from_text` already parses N keywords for positive matching; no
  parser change is needed for `VERDICT: UNVERIFIABLE`. Documented behaviour:
  with 3 keywords, negated matches (e.g. `VERDICT: NOT UNVERIFIABLE`) stay
  ambiguous and route to retry, same as today.
- `parse_verdict` continues to collapse a clear decision to its canonical
  value and anything unclear to `retry`.

### 2. Routing: on_unverifiable (core/config.py, engine/stages.py)

- `StepConfig` gains `on_unverifiable: RouteTarget | None = None`.
- `_check_graph` validates `step.on_unverifiable` targets like the other two
  routes.
- `make_agent_node` (engine/stages.py, ~line 282): `verdicts_map` includes
  the third mapping when configured. The parsed outcome `unverifiable` gets
  its own branch:
  - record it in `step_verdicts` (so the run state and viz show it),
  - print a distinct line (e.g. `qa verdict: UNVERIFIABLE (moves on)`),
  - route via `on_unverifiable` when set, else via `on_pass` (the move-on
    default). It never routes to `on_fail` and never retries.
- `_unclear_note`'s expected-verdict text mentions the third keyword when
  configured.

### 3. QA agent prompt: no-substitution rule + UNVERIFIABLE report format

(sdk_agents/qa.agent.md in this repo's dogfood copy)

- New rule: never run unrelated generic probes in place of a listed
  acceptance check. If a check cannot be executed in the sandbox (no docker,
  no host access, missing system dependency), report the check as
  UNVERIFIABLE with the blocking reason. Fabricated checks are a QA failure
  in themselves.
- Report format: each acceptance check gets a status PASS / FAIL /
  UNVERIFIABLE, plus an UNVERIFIABLE section listing each skipped check and
  why.
- Verdict rules:
  - `VERDICT: PASS` when every reachable check passes (unverifiable items
    recorded in the report).
  - `VERDICT: UNVERIFIABLE` when the acceptance checks cannot be touched at
    all, or none of them can be verified.
  - `VERDICT: FAIL` on any genuine defect, as today.
- The existing INCONCLUSIVE budget rule stays; it is the per-probe
  counterpart.

### 4. Visualiser: a dedicated UNVERIFIABLE colour (viz/server.py)

Requirement (owner): UNVERIFIABLE needs its own colour that is not red,
amber, green, or blue, and that reads as unsure but not necessarily broken.

Candidate: deep purple (Material 400/50-900). Exact hexes are decided at
implementation against these five existing states, which the new colour
must not collide with:

- running blue `#1976d2`
- pass green `#2e7d32`
- fail amber `#ffb300`
- fatal red `#c62828`
- pending pale violet `#f2f0ff`

Changes in viz/server.py:

- Graph: new `classDef unverifiable fill:<purple>,stroke:<purple>,color:#fff`
  (line ~652 area).
- Badges (line ~471 area): new `.badge-unverifiable` (e.g. background
  `#ede7f6`, color `#4527a0`).
- Status text: new `.status-unverifiable`.
- JS verdict tracking (lines ~541, ~611, ~617, ~749, ~1063): a third set
  `verdictUnverifiable` beside `verdictPass`/`verdictFail`; `statusOf`
  returns `unverifiable`; the badge branch maps `unverifiable` to
  `badge-unverifiable` instead of falling into `badge-unclear`; the stage
  suffix becomes `(verdict: UNVERIFIABLE)`; the event handler keeps the
  third set in sync. `unclear` remains only for null/no-verdict.

### 5. Dogfood pipeline (agentic-dev-loop/pipeline.yaml)

- qa step: `verdicts: { pass: PASS, fail: FAIL, unverifiable: UNVERIFIABLE }`
  and `on_unverifiable: commit`, with a comment explaining the move-on
  semantics and that the record (qa-report.md in the run state) carries the
  unverified list.

### 6. Record placement (no code change, stated fact)

qa-report.md and step_verdicts live in the run state dir
(~/.agentic-dev-loop/runs/<hash>/), which is outside the workspace and is
not committed by the commit action. The UNVERIFIABLE record is therefore
the run state (visible in the viz and via --resume), not the repo. This is
accepted: the record's job is to show what was and was not verified, and
the run state does that. `mark_plan_completed` stamping is unchanged.

## Acceptance checks (sandbox-executable)

1. `VERDICT: UNVERIFIABLE` on its own line parses to the canonical value
   (unit test, core/verdict.py).
2. A response mixing `VERDICT: PASS` and `VERDICT: UNVERIFIABLE` routes to
   retry (ambiguous), never to either outcome silently.
3. A pipeline with a 3-keyword qa step and `on_unverifiable` loads; an
   `on_unverifiable` pointing at an unknown step fails to load.
4. Stage routing: a mocked agent returning `VERDICT: UNVERIFIABLE` records
   `unverifiable` in step_verdicts and follows `on_unverifiable` (or
   `on_pass` when unset); it does not retry and does not follow `on_fail`.
5. qa.agent.md contains the no-substitution rule and the UNVERIFIABLE
   verdict rules (content assertion).
6. viz server output contains `classDef unverifiable`,
   `.badge-unverifiable`, and `.status-unverifiable`, and the fallthrough
   for the new value is `unverifiable`, not `unclear`.
7. Full suite stays green; no em dash or other non-ASCII characters in any
   touched file (repo-wide ASCII policy).

## Verification

TDD per repo rule: red tests first for changes 1, 2, 4, 6; prompt change 3
is verified by acceptance check 5. All checks above run inside the sandbox
(no docker needed). A visual spot-check of the viz (host-side, out of the
loop) confirms the new colour is distinct from the five existing states.

## Follow-ups

None. Consumer adoption of UNVERIFIABLE (their pipeline.yaml + prompt
copies) is each consumer's own plan.

## Open questions

- Exact purple hexes (subject to owner taste; candidate deep purple
  `#7e57c2` fill, `#ede7f6`/`#4527a0` badge).
