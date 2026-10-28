# 031-qa-environmental-verdict.md

> **Status: PLANNED**

## Scope

This repository (the engine) plus the dogfood pipeline and qa prompt copy.
Consumer repos adopt the behaviour by updating their own pipeline.yaml and
their own copies of the qa prompt when they pull the new engine.

## Context

Consumer-repo run 5367f0c488d8 livelocked: qa attempt 8 burned all 60
iterations (~96 min) diagnosing a browser-tier landmine. The reference
fixture's served page hotlinks ~12 external images that hang in the
sandbox, so the probe harness's hardcoded `waitUntil: 'load'` with a 30s
timeout can never fire, even though the page renders correctly (body
visible, text present). The qa agent hit its iteration cap with no final
response, and the unclear-verdict retry routed it back to dev
(`_unclear_target` -> `on_fail`'s goto). Dev could not fix a harness
problem ("no further code changes are needed"), review re-approved
unchanged code, qa failed again: ~2.5-3h per cycle, bounded only by
max_attempts 10.

The verdict vocabulary is pass/fail/unverifiable (plan 024). UNVERIFIABLE
is the move-on outcome for checks that cannot be executed here but carry
no defect signal. Neither FAIL (routes to dev) nor UNVERIFIABLE (moves
on, records only) is correct when the verification environment itself is
broken mid-run: the fix lives in the harness, fixture, or pipeline, not
in the product code, and the run must not commit on a harness that could
not verify.

Plan 030 added the mechanical counterpart (AE-SBX-019: daemon-side I/O
failures halt with a report). ENV is the agent-side counterpart: the qa
agent itself detected that the environment is broken.

## Decisions (to be approved)

| # | Decision | Proposal |
|---|---|---|
| D1 | Add a fourth verdict keyword ENV (canonical `env`) to VerdictSpec and the verdict-map handling | APPROVED as proposed |
| D2 | Routing for ENV: halt the run with a report by default (record verdict, write last-error.md with the findings, hard-fail). Optional `on_env` route for consumers who want to override | APPROVED as proposed |
| D3 | Distinct viz colour/status for ENV, not colliding with the existing five states or the unverifiable purple | APPROVED, candidate blue grey |
| D4 | qa prompt rules: when to emit ENV (harness broken, fix belongs outside product code), an ENV report section, and an anti-livelock rule | APPROVED as proposed |
| D5 | No new pipeline config surface beyond the `env` verdict keyword and `on_env`. Backward compatible: 3-keyword pipelines keep loading and behave identically | APPROVED as proposed |

## Changes

### 1. Verdict domain: a fourth keyword (core/verdict.py, core/config.py)

- `VerdictSpec` (src/agentic_dev_loop/core/config.py:145) gains an
  optional fourth field `env: str | None = None`, alongside
  `pass_`/`fail`/`unverifiable`. `extra="forbid"` stays; old pipelines
  without the field still load.
- `Verdict.from_text` already parses N keywords for positive matching; no
  parser change is needed for `VERDICT: ENV`. Documented behaviour
  (existing policy, `_parse_line` negation branch): with 3 or more
  keywords, negated matches (e.g. `VERDICT: NOT ENV`) stay ambiguous and
  route to retry, never a silent mapping.
- `parse_verdict` continues to collapse a clear decision to its canonical
  value and anything unclear to `retry`.

### 2. Routing: on_env (core/config.py, engine/stages.py)

- `StepConfig` gains `on_env: RouteTarget | None = None` next to
  `on_unverifiable` (src/agentic_dev_loop/core/config.py:337).
- `_check_graph` validates `step.on_env` targets like the other routes
  (extend the loop at src/agentic_dev_loop/core/config.py:660).
- `make_agent_node` (src/agentic_dev_loop/engine/stages.py): the
  `verdicts_map` built around line 347 includes the env mapping when
  configured. A new branch for the parsed outcome `env`:

  - record it in `step_verdicts` (so the run state and viz show it),
  - print a distinct line (e.g. `{step.name} verdict: ENV
    (environmental, halting)`),
  - when `step.on_env is None`: write last-error.md with the step's
    findings (title plus the captured response) and return
    `{"failed": True, "failed_step": step.name}` so the run halts and
    --resume re-enters at the verdict step,
  - when `step.on_env` is set: return the update without `failed` and let
    the router resolve the route.

  Sketch of the branch (after the `unverifiable` branch, before the fail
  branch):

  ```python
  if verdict == "env":
      print(f"[{subject}] {step.name} verdict: ENV "
            f"(environmental, halting).", flush=True)
      if step.on_env is None:
          runtime.state_dir.mkdir(parents=True, exist_ok=True)
          (runtime.state_dir / LAST_ERROR).write_text(
              f"# Environmental failure (verdict: ENV)\n\n"
              f"{result.response or 'no captured response'}\n",
              encoding="utf-8")
          return {"step_verdicts": merged,
                  "failed": True, "failed_step": step.name}
      update = {"step_verdicts": merged, "failed": False}
      if captures and result.response is not None:
          update["artifacts"] = {step.name: result.response}
      return update
  ```

- `make_router` (src/agentic_dev_loop/engine/stages.py, after the
  `unverifiable` branch at line 609): add

  ```python
  if verdict == "env":
      return resolve(step.on_env) if step.on_env is not None else END
  ```

  The END fallback is defensive: when `on_env` is unset the node has
  already hard-failed and `state["failed"]` short-circuits the router
  first.
- Applied (2026-10-28): the existing `unverifiable` print at
  stages.py:412 said `qa verdict:` (plan 024 leftover). It now reads
  `{step.name} verdict:` so no qa wording remains in engine source;
  stages and verdict tests stay green (125 passed).
- `_unclear_note`'s expected-verdict text mentions the fourth keyword
  when configured (stages.py ~line 315).

### 3. QA agent prompt: ENV rules (sdk_agents/qa.agent.md, dogfood copy)

- New rule distinguishing ENV from UNVERIFIABLE:
  - UNVERIFIABLE = a check cannot be executed here and no defect is
    suspected.
  - ENV = the verification environment itself is broken mid-run (probe
    timeouts on hanging external resources, required tooling missing,
    browser cannot launch, sandbox network dead) to the degree that
    PASS/FAIL/UNVERIFIABLE cannot be determined.
- Rule of thumb: if the fix belongs in the pipeline, harness, or fixture
  rather than the product code, the verdict is ENV.
- Report format: an ENV section listing each environmental blocker with
  evidence (command, output, expected).
- Verdict rules:
  - `VERDICT: ENV` when the harness is broken as above.
  - `VERDICT: PASS` / `VERDICT: UNVERIFIABLE` / `VERDICT: FAIL` unchanged.
- Anti-livelock rule: if the same environmental blocker persists across
  2 attempts, emit ENV instead of further workarounds (the analysed run
  spent 60 iterations building a synthetic fixture).

### 4. Visualiser: a dedicated ENV colour (viz/server.py)

Requirement: ENV needs its own colour that reads as infrastructure
broken, not product broken, and that does not collide with the existing
states: running blue `#1976d2`, pass green `#2e7d32`, fail amber
`#ffb300`, fatal red `#c62828`, pending pale violet `#f2f0ff`, and the
unverifiable purple from plan 024. Candidate: blue grey (fill `#455a64`,
badge background `#eceff1`, badge text `#37474f`). Exact hexes are
decided at implementation against those six existing states.

Changes in viz/server.py, mirroring plan 024's change 4:

- Graph: new `classDef env fill:<blue-grey>,stroke:<blue-grey>,color:#fff`.
- Badges: new `.badge-env`.
- Status text: new `.status-env`.
- JS verdict tracking: a fourth set `verdictEnv` beside
  `verdictPass`/`verdictFail`/`verdictUnverifiable`; `statusOf` returns
  `env`; the badge branch maps `env` to `badge-env`; the stage suffix
  becomes `(verdict: ENV)`; the event handler keeps the set in sync.
  `unclear` remains only for null/no-verdict.

### 5. Dogfood pipeline (agentic-dev-loop/pipeline.yaml)

- qa step: `verdicts: { pass: PASS, fail: FAIL, unverifiable: UNVERIFIABLE,
  env: ENV }`, `on_env` unset (default halt) with a comment explaining
  the halt-with-report semantics and that qa-report.md plus last-error.md
  carry the blocker list.

### 6. Record placement (no code change, stated fact)

qa-report.md, step_verdicts, and last-error.md live in the run state dir
(~/.agentic-dev-loop/runs/<hash>/), outside the workspace and not
committed by the commit action, same as plan 024's note. The ENV record
is therefore the run state and viz, not the repo.

## Acceptance checks (sandbox-executable)

1. `VERDICT: ENV` on its own line parses to the canonical value `env`
   (unit test, core/verdict.py).
2. A response mixing `VERDICT: PASS` and `VERDICT: ENV` routes to retry
   (ambiguous), never to either outcome silently.
3. A pipeline with a 4-keyword qa step loads; an `on_env` pointing at an
   unknown step fails to load; a 3-keyword pipeline still loads and
   behaves as today (backward compat).
4. Stage routing: a mocked agent returning `VERDICT: ENV` with `on_env`
   unset records `env` in step_verdicts, writes last-error.md, and
   hard-fails the run (failed=True, failed_step=<verdict step name>).
   With `on_env: failure` the run ends via the failure terminal without a
   hard fail. With `on_env: <step>` the run routes to that step.
5. qa.agent.md contains the ENV/UNVERIFIABLE distinction, the ENV report
   section, the verdict rules, and the anti-livelock rule (content
   assertion).
6. viz server output contains `classDef env`, `.badge-env`, and
   `.status-env`, and the fallthrough for the new value is `env`, not
   `unclear`.
7. Full suite stays green; no em dash or other non-ASCII characters in
   any touched file (repo-wide ASCII policy).

## Verification

TDD per repo rule: red tests first for changes 1, 2, 4, 6; prompt change
3 is verified by acceptance check 5. All checks above run inside the
sandbox (no docker needed). A visual spot-check of the viz (host-side,
out of the loop) confirms the new colour is distinct from the six
existing states.

## Follow-ups

- The no-verdict-at-cap path (an agent that dies at max_iterations
  without any verdict) still routes to dev via `_unclear_target`. ENV
  fixes the diagnosed case only when the agent emits the verdict before
  dying; the anti-livelock rule makes that the expected behaviour.
  Engine-level handling of iteration-cap deaths for verdict steps is a
  separate candidate plan.
- Consumer-side adoption (their pipeline.yaml + qa prompt copies) is
  each consumer's own change, guided by sections 3 and 5.

## Open questions

- Exact blue-grey hexes (subject to owner taste; candidate fill
  `#455a64`, badge `#eceff1`/`#37474f`).
- Whether the dogfood pipeline should set `on_env: failure` explicitly or
  rely on the default halt (current proposal: rely on the default).
