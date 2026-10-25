# Unclear Verdicts Retry the Same Stage - Implementation Plan

> **Status: COMPLETED** (2026-10-23)

> **For agentic workers:** Use bite-sized task execution to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the unclear-verdict retry policy so a verdict stage whose agent wrote no artifact - or wrote one with no parseable verdict - retries **that same stage** instead of rewinding to `on_fail`'s `goto` (usually the entry step). Per the user:

> "Write a planning doc under planning-docs to fix the retry policy as you suggest"

(The suggestion: "unclear verdicts should retry the failing stage itself instead of rewinding to dev").

**Architecture:** One engine change, no graph changes:

1. In `make_agent_node` (stages.py:184-225), the unclear branch's `"retry_target"` becomes `step.name` instead of `_unclear_target(runtime, step)`.
2. Delete the `_unclear_target` helper (stages.py:154-160) entirely - it exists only for this one caller.

The `bump` node's conditional edge already routes to **any** step name in `state["retry_target"]` (graph.py:64-65), including the step's own name - `bump → step → bump` is a valid cycle with zero graph changes. Everything else (notes, attempt accounting, `max_attempts` exhaustion, checkpoint/resume, FAIL routing) is untouched.

**Why:** an unclear verdict means *that stage's agent* failed to produce or stamp its own artifact; only that agent can fix it. Rewinding to the entry step re-runs upstream work pointlessly. The plan-008 self-run (2026-10-09, subject "plan 008-viz-stage-console.md") burned **5 dev LLM runs**: four unclear `test` verdicts (the agent kept writing nothing, or writing `test-report.md` at the repo root instead of `.pr/ci-fix.md`) each rewound to `dev` via `on_fail: {goto: dev, retry: true}`. Under this fix the same run costs 1 dev run + N test-stage self-retries. (The example's `test` stage has since been deleted from `examples/self/pipeline.yaml` in the working tree - that deletion is separate work; this plan fixes the engine policy for all consumers.)

**Tech Stack:** unchanged - Python ≥3.12, pytest, no new dependencies.

## Verified facts

- `_unclear_target(runtime, step)` (stages.py:154-160): returns `route_target(step.on_fail)` when it names a non-terminal step, else `runtime.cfg.steps[0].name` (the entry step). Docstring: "the consumer always looped back to dev". Its **sole** caller is `make_agent_node` line 223 (`"retry_target": _unclear_target(runtime, step)`).
- `make_agent_node` (stages.py:184-225): for verdict steps, missing artifact **or** unparseable verdict → `step_verdicts[name] = None`, `notes = [_unclear_note(...)]`, `retry_target = _unclear_target(...)`. `_unclear_note` (:163-181) produces the three wordings (truncated / "did not write `{rel}` on attempt {attempt}." / unparseable + "Its findings so far:\n\n{text}").
- FAIL path (:211-217) is **unchanged by this plan**: `retry_target = step.on_fail.goto` only when `on_fail` is a `Route` with `retry=True`.
- `make_router` (:258-273): unclear → `"bump"` while `attempt < max_attempts`, else `END` - the "never routable, attempt-bounded" policy (README.md:210).
- `make_bump_node` (:278-292): consumes an attempt, clears artifacts, rebuilds context with **accumulated notes** - so a self-retrying agent receives its own previous attempts' notes, exactly like today's cross-stage retries.
- `graph.py:64-65`: `graph.add_conditional_edges("bump", lambda state: state["retry_target"])`; `LoopState.retry_target: str | None` (graph.py:45). A step's own name is a valid target - `bump → step → bump` cycles already work.
- No planning doc locked the unclear target (grep: 007:23 only says attempts bump in the bump node; 001:51 excludes `retry_target` from viz state events). README.md:210's fixed-policy bullet says "**Unclear verdicts are never routable** - always an attempt-bounded retry" - does **not** name the target, so only wording needs amending, not a plan-amendment entry.
- Test surface (all verified by reading):
  - `tests/test_stages.py`: STEPS fixture (:30-39) - `dev` produces, `test` verdicts on `ci-fix.md` with `on_fail: {goto: dev, retry: true}`, `review` has no `on_fail` (comment line 38 says "unclear retries entry" - stale after this plan). `test_missing_artifact_is_unclear` (:127-132) asserts `retry_target == "dev"`. `test_unclear_retries_entry_step_when_no_fail_route` (:153-156) asserts `"dev"` for `review`. `test_fail_verdict_surfaces_artifact_and_retry_target` (:113-119, `"dev"`) is the **fail** path - stays green.
  - `tests/test_graph.py`: `test_unclear_verdict_retries_with_content` (:179-188) - test agent writes undecided prose then PASS; currently asserts `messages_for("dev")[1]`; new flow is `["dev","test","test","review","qa"]` and the note lands in `messages_for("test")[1]`. `test_stale_artifact_deleted_between_attempts` (:190-200) - currently asserts `messages_for("dev")[2]`; new flow `["dev","test","dev","test","test","review","qa"]` (FAIL attempt 1 still rewinds to dev; the empty write on attempt 2 self-retries) and the note lands in `messages_for("test")[2]`. `test_test_fail_retries_from_dev` (:145-156), `test_exhaustion_fails`, `test_max_attempts_cli_override` are all **fail**-path - unchanged.
  - `tests/test_e2e_fixtures.py`: `test_unclear_verdict_retries_with_content_in_notes` (:122-137) - qa writes undecided prose then PASS; currently asserts `messages_for("dev")[1]`; new flow `["dev","review","qa","qa"]` and the note lands in `messages_for("qa")[1]`. `test_stale_artifact_is_deleted_between_attempts` assertions already hold unchanged (flow becomes `dev, review, qa×3`).
- README wording to amend: fixed-policy bullet (line 210); "Step semantics" bullet (lines 146-149, "triggers an attempt-bounded retry with a note"); "What a run looks like" item 2 (lines 66-70, describes fail routing to dev).

## Behaviour matrix

| Situation on a verdict step | Today | After |
|---|---|---|
| Agent writes nothing (artifact missing) | unclear → retry `on_fail`'s `goto` (dev in the example) | unclear → retry **the same step**, note appended |
| Artifact has no parseable verdict | unclear → retry `on_fail`'s `goto` | unclear → retry **the same step**, findings note appended |
| Agent truncated (turn cap / stuck detection) | unclear → retry `on_fail`'s `goto` | unclear → retry **the same step**, truncated note appended |
| `VERDICT: FAIL` + `on_fail: {goto: X, retry: true}` | retry `X` | **unchanged** - fail stays config-driven |
| `VERDICT: FAIL` + `on_fail: X` (no retry) / no `on_fail` | route `X` / `END` | **unchanged** |
| Attempts exhausted (`attempt == max_attempts`) | `END` | **unchanged** |
| Non-verdict step (`verdicts` omitted) | no unclear possible | **unchanged** |

## Global Constraints

- TDD red→green→refactor for every task.
- Mock policy (repo rule): `run_agent` remains the only stubbed *agent* seam; all graph/stage tests here use the existing `fake_agents` fixture - no new seams.
- No config surface is added: same-stage unclear retry is a fixed policy, like every other unclear-verdict rule. `on_fail` keeps governing **fail** verdicts only.
- No graph/viz/serialization changes; `retry_target` semantics are unchanged (a step name, consumed by the bump edge).
- Full suite green at the end of every task: `.venv-sandbox/bin/python -m pytest tests/ -q`

## Locked decisions (from user / research)

| Decision | Choice |
|---|---|
| Unclear retry target | **The failing stage itself** (`step.name`), always - never `on_fail`'s `goto`, never the entry step |
| Fail retry target | Unchanged - `on_fail: {goto: X, retry: true}` still routes fail verdicts to `X` (the example's `{goto: dev}` for review/qa stands) |
| `_unclear_target` helper | Deleted; nothing else calls it (`route_target` stays - the router still uses it) |
| Unclear routability | Still never routable and attempt-bounded (README policy preserved; wording amended to name the target) |
| Notes / attempt accounting / bump / resume | Unchanged - the self-retrying agent gets its own previous notes through the existing bump-node context rebuild |
| Example pipeline | Not touched by this plan (its `test` stage deletion is separate working-tree work); engine policy applies to all consumers |

---

### Task 1: engine change + unit tests in `test_stages.py`

**Files:**
- Modify: `src/agent_engine/stages.py`
- Test: `tests/test_stages.py`

- [ ] **Step 1: Failing tests** - `test_missing_artifact_is_unclear` (:127-132): change `assert update["retry_target"] == "dev"  # on_fail's goto` → `== "test"  # same stage, not on_fail's goto`. `test_unclear_retries_entry_step_when_no_fail_route` (:153-156): rename to `test_unclear_retries_same_step_when_no_fail_route`, update its comment ("review has no on_fail - the unclear-retry policy targets the failing step itself"), assert `retry_target == "review"`. Update the STEPS fixture comment at :38 ("unclear retries entry" → "unclear retries the failing step"). (`test_fail_verdict_surfaces_artifact_and_retry_target` :113-119 stays - fail path.)
- [ ] **Step 2: Implement** - in `make_agent_node`'s unclear branch replace `"retry_target": _unclear_target(runtime, step)` with `"retry_target": step.name`; delete `_unclear_target` (:154-160); update the module docstring bullet (:10-11) to "unclear verdict → implicit, attempt-bounded retry **of the same stage** with the artifact's content surfaced into notes (never routable - policy, not config)". Green + refactor.

### Task 2: graph-level flow tests in `test_graph.py`

**Files:**
- Test: `tests/test_graph.py`

- [ ] **Step 1: Failing tests** - `test_unclear_verdict_retries_with_content` (:179-188): replace the `second_dev = messages_for("dev")[1]` assertion with `assert fake_agents.agents_called() == ["dev", "test", "test", "review", "qa"]` and `second_test = fake_agents.messages_for("test")[1]` containing `"unparseable"` and `"undecided prose"`. `test_stale_artifact_deleted_between_attempts` (:190-200): replace `third_dev = fake_agents.messages_for("dev")[2]` with `third_test = fake_agents.messages_for("test")[2]` and keep the `"did not write \`.pr/ci-fix.md\` on attempt 2"` assertion; additionally assert `agents_called() == ["dev", "test", "dev", "test", "test", "review", "qa"]` (fail attempt 1 still rewinds to dev - only the unclear attempt 2 self-retries). (`test_test_fail_retries_from_dev` :145-156 and the exhaustion/max-attempts tests stay green untouched - fail path.)
- [ ] **Step 2: Run** - both fail for the right reason (retry_target/content destination), then go green with the Task 1 implementation already in place.

### Task 3: e2e fixture test in `test_e2e_fixtures.py`

**Files:**
- Test: `tests/test_e2e_fixtures.py`

- [ ] **Step 1: Failing test** - `test_unclear_verdict_retries_with_content_in_notes` (:122-137): replace `attempt2_dev = fake_agents.messages_for("dev")[1]` with `attempt2_qa = fake_agents.messages_for("qa")[1]`; keep the `"unparseable"` / `"I cannot decide"` assertions; assert `fake_agents.agents_called() == ["dev", "review", "qa", "qa"]`. Update the docstring if it names the target. (`test_stale_artifact_is_deleted_between_attempts` - its assertions already pass under the new flow `dev, review, qa×3`; verify green, no edits needed.)
- [ ] **Step 2: Run** - fails for the right reason, then green.

### Task 4: README wording

**Files:**
- Modify: `README.md`

- [ ] Fixed-policy bullet (line 210) → "**Unclear verdicts are never routable** - always an attempt-bounded retry **of the same stage**, with the artifact's content (or absence) carried in the retry note; only a verdict `FAIL` follows `on_fail`."
- [ ] "Step semantics" bullet (lines 146-149) → "…triggers an attempt-bounded retry **of that same stage** with a note - verdicts are never silently routable, and the **last** verdict line wins…"
- [ ] "What a run looks like" item 2 (lines 66-70) → add: an unclear verdict retries the verdict agent itself (bounded by `max_attempts`); `fail` follows `on_fail` - here `{goto: dev, retry: true}`.
- [ ] Full suite green.

### Task 5: full verification

- [ ] `.venv-sandbox/bin/python -m pytest tests/ -q` green (baseline: 258 passed).
- [ ] grep for `_unclear_target` → zero hits.
- [ ] grep tests/planning-docs for stale "unclear retries entry"-style prose → none beyond deliberate audit-trail text.

## Verification

1. `.venv-sandbox/bin/python -m pytest tests/ -q` green.
2. Manual: `python -m pytest tests/test_stages.py tests/test_graph.py tests/test_e2e_fixtures.py -q` and confirm the two graph flows call `dev` exactly once each despite the unclear verdicts.
3. Real run - **user triggers**: `./trigger-agent-engine.sh --plan <plan doc>` against a pipeline whose verdict agent is instructed to write nothing, to observe same-stage retries live (makes LLM calls; optional).

## Amendments

(none yet)
