# Mechanical Test Stage (`command:` steps) - Implementation Plan

> **Status: DONE**

> **For agentic workers:** Use bite-sized task execution to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a **mechanical test step** - a pipeline step that runs a shell command and treats its exit code as the verdict, with no agent and no LLM call. Per the user:

> "Test stage: Mechanical (not an agent); Specified in the yaml along with the other stages - probably a type field; Do NOT key on the name 'test'! You give it a command to run the tests; Exit code 0 => pass; Exit code nonzero (or fails to exit at all) => fail"

> "No, dogfood it now" (add the step to `examples/self/pipeline.yaml` in this same plan)

> "rename test to command" (the YAML key is `command:`, not `test:`)

> "What happens if you specify both agent and command?" → **load-time error**: `_check_shape` rejects the config (`step 'x': exactly one of 'agent', 'action', or 'command' is required`) and the pipeline never runs - same mechanism as today's agent+action rejection, no precedence, no ambiguity.

> "No, drop the test step and rename suite to test - realistic example please" (the *agent* step named `test` is dropped from the canonical pin and the example; the mechanical command step is **named `test`** - like a realistic pipeline, the `test` step runs the tests)

> "Fine, just use a different name to test to avoid confusion" (the internal fixtures' *agent* step named `test` is renamed to **`check`** - mechanics untouched, no confusing `test` agent next to the new mechanical `test` step)

**Note:** Implemented and verified (2026-10-09). The dogfooded self-run built the whole stage (commit `ea2671c`, 582 insertions across config/graph/stages/viz, the example pipeline, agent prompts, README, and six test files); the user's demo run confirmed the gate behaves (`lgtm`). Two follow-on fixes shipped with this plan's completion: (1) the viz-console hard-wrap fix - `ConsoleCapture` now forces `COLUMNS` to `MAX_LINE` while attached (save/restore on detach) so rich-based SDK output stops baking 80-column newlines into the console stream; (2) `viz_demo.py`'s scripted scenarios were still pre-012 and skipped the `test` node - they now run the mechanical gate between `dev` and `review` on every attempt. Full suite green: 325 passed. See Amendments.

**Architecture:** The command step is a third core step kind alongside `agent` and `action` - exactly one of the three is required per step, keyed by the sibling YAML key `command:`, never by the step's `name`:

```yaml
- name: test
  command: .venv-sandbox/bin/python -m pytest tests/ -q
  timeout: 600            # optional, seconds; default 600
  on_pass: review
  on_fail: { goto: dev, retry: true }
```

1. `src/agent_engine/config.py` - `StepConfig` gains `command: str | None = None` and `timeout: float | None = Field(default=None, gt=0)` (plain string + sibling timeout - no nested spec model, keeps the YAML flat). `_check_shape` becomes "exactly one of `agent`, `action`, `command`"; `timeout` requires `command`; command steps ban `artifact`/`verdicts`/`produces`/`params`/`max_iterations` (none mean anything for a mechanical step - banning now keeps the schema honest).
2. `src/agent_engine/stages.py` - `DEFAULT_COMMAND_TIMEOUT = 600.0`; `_run_command(command, workspace, timeout)` helper: `subprocess.Popen(cwd=workspace, shell=True, start_new_session=True, stdout/stderr→PIPE)`, `communicate(timeout=...)`, on `TimeoutExpired` SIGKILL the **process group** and return `(None, output, True)`; `make_command_node(runtime, step)` closure: exit 0 ⇒ `step_verdicts[name] = "pass"`, else ⇒ `"fail"` + a retry note (`## {name} output (attempt N)` - `(attempt N - timed out)` variant) carrying the last 4000 chars of output, plus `retry_target = step.on_fail.goto` when `on_fail` is a `Route` with `retry=True` (identical to the agent FAIL branch, stages.py:211-217). The router's plain-step gate (stages.py:261) becomes `if step.verdicts is None and step.command is None:` - pass/fail routing for command steps is **exactly** the existing verdict routing; "unclear" is impossible by construction.
3. `src/agent_engine/graph.py` - `build_graph` (:59-62) becomes a 3-way: agent → `make_agent_node`, command → `make_command_node`, action → `make_action_node`.
4. Nicety, same PR: `src/agent_engine/viz.py:51` - the retry-edge label becomes `"fail"` for command steps (`"retry"` remains the plain-step fallback).
5. **Dogfooding (this plan):** `examples/self/pipeline.yaml` gains a mechanical `test` step (`dev → test → review → qa → commit`); the dev/qa agent prompts are rewired from "agent owns the test suite" to "the pipeline's `test` gate verifies"; the e2e fixture suite gets a scripted `_run_command` seam (only there - unit/graph tests run real commands) plus new failure-replay tests.

**Why:** The plan-008 self-run burned **5 dev LLM runs** on four *unclear* agent-written `test` verdicts (the agent wrote nothing, or wrote to the wrong path, then rewound to `dev` each time via `on_fail: {goto: dev, retry: true}`). An agent is the wrong tool for a deterministic check: an exit code never comes back "unclear", costs no tokens, and can't be gamed by prose. The pipeline - not the dev agent - should own the green-tests claim (today both `examples/self/sdk_agents/dev.agent.md` and the README say the dev agent owns the suite).

Design choices:

- **Sibling key `command:`, not a `type:` field, not the name.** The user renamed the key to `command` - settling the `type:` question in favor of the engine's existing mutually-exclusive-sibling idiom (`agent` vs `action`, enforced by `_check_shape` + `extra=forbid`; a `type:` key is loudly rejected). Name-keying stays forbidden even though this plan *removes* the confusing name collision: the kind lives in the `command:`/`agent:`/`action:` key alone, and git history (1641dea "rename checks stage to test") proves step names flip freely - keying behavior on a name would have silently reclassified an agent step twice over.
- **Core, not an extension.** `Registry`/`load_extensions` can only add actions/loaders/tools - no step-kind seam exists - and actions have no verdict channel (README "Extension points"). A verdict-bearing mechanical stage must live in the engine.
- **No `produces` on command steps (v1).** The command runs in the workspace; files it writes persist and any later step (e.g. the commit action) sees them. `produces` adds declare/verify plumbing with no mechanical meaning - banned now, addable later.

**Tech Stack:** unchanged - Python ≥3.11, pytest, no new dependencies (subprocess/os/signal only).

## Verified facts

- `StepConfig` (config.py:38-47): `name, agent, action, params: dict = {}, produces: list[str] = [], artifact, verdicts, max_iterations, on_pass, on_fail`. `_check_shape` (:54-68) currently: `if (self.agent is None) == (self.action is None): raise ValueError(f"step {self.name!r}: exactly one of 'agent' or 'action' is required")`; artifact↔verdicts pairing (:60-63); produces/artifact exclusivity (:64-67). `PipelineConfig._check_graph` (:93-110): dup names, reserved `TERMINALS={'success','failure'}`, route targets must exist (command steps ride through unchanged).
- `make_agent_node` FAIL branch (stages.py:211-217) sets `notes=[f"## {step.name} output (attempt {attempt})\n\n{text}"]` and `retry_target = step.on_fail.goto` only when `on_fail` is a `Route` with `retry=True` - the exact template `make_command_node`'s fail branch copies.
- `make_router` (stages.py:243-275): :261 `if step.verdicts is None: return resolve(step.on_pass)` is the only kind-dispatch in routing; verdict fail → `on_fail` None⇒END / Route+retry⇒bump-while-attempts / else `resolve(on_fail)` (:266-271); `resolve` (:249-256): `None` → next step or `"success"`.
- `build_graph` (graph.py:49-69): :59-62 `node = make_agent_node(runtime, step) if step.agent else make_action_node(runtime, step)` - the only node-kind dispatch; bump node :64 + conditional edge on `retry_target` :65 (any step name works, incl. command steps); `START → steps[0]` :67. `LoopState` (:32-46) needs no new keys - `step_verdicts: dict[str, str | None]` already carries command-step verdicts.
- `run_pipeline` (graph.py:96-234): `Runtime(workspace=Path.cwd())` - the command's cwd. `commit_allowed = git_worktree_clean(...)` computed once before the run; `.gitignore` already ignores `.pytest_cache/` and `.pr/`, so the suite's pytest cache cannot dirty the worktree before the commit action.
- `build_context` (stages.py:104-124): :114 `if step.agent is None: continue` already skips command steps from "Expected output paths" with **no change**.
- viz: `topology_mermaid` (viz.py:33-66) is kind-agnostic; :50-54 `label = step.verdicts.fail if step.verdicts is not None else "retry"` is the only kind-aware spot. `ConsoleCapture` tees node stdout/stderr to the viz bus - command-step output gets viz attribution for free.
- **Real-run cwd:** `trigger-agent-engine.sh` does `cd "$ROOT"` (repo root) before `exec .venv/bin/agent-engine examples/self/pipeline.yaml` - so the example's command `.venv-sandbox/bin/python -m pytest tests/ -q` resolves for real self-runs.
- Tests pinning the old message: `tests/test_config.py` L206/L213 (`match="exactly one of 'agent' or 'action'"`). The canonical `FULL_YAML` pin (:11-67) currently holds an **agent step named `test`** (artifact `ci-fix.md`, PASS/FAIL, `on_fail: {goto: dev, retry: true}`); its round-trip test unpacks 6 steps at L103. **This plan replaces that agent step with the mechanical `test` command step** (`command: python -m pytest`, `timeout: 600`) - same position between `dev` and `review`, same `on_fail` wiring, so the 6-way unpack stays 6-way; only the step's field assertions change. The pin's header comment claims verbatim-from-009 and must note the substitution.
- `tests/test_stages.py`: `STEPS` fixture (:30-39) - `dev` produces, `test` (agent, verdicts on `ci-fix.md`, `on_fail: {goto: dev, retry: true}`), `review` (no `on_fail`). It is index-based (TestRouter uses `runtime.cfg.steps[0/1/2]` + `dataclasses.replace(runtime, cfg=make_cfg([...]))`) - new command-step tests must build their own cfgs, never extend `STEPS`. **This plan renames the `test` agent (step name + agent name) to `check`** - see Task 2.
- `tests/test_graph.py`: `repo` fixture (:79-94) is a real git repo (committed tree, `monkeypatch.chdir(repo)`). `REFERENCE_YAML` (:26-71) has the same agent `test` step; its verdict-retry/exception/crash mechanics are **kept intact** but the step+agent is renamed `test` → `check` (Task 2) - the fixture's verdict vehicle is preserved, only its name changes. `TestVizStream`'s node order (:266-332) includes `'test'` → becomes `'check'`.
- **e2e dogfood surface** (`tests/test_e2e_fixtures.py`): `consumer_repo` fixture (:42-69) copies the shipped example into a fresh git repo (`.pr` excluded), chdirs to repo root, then every test runs `main([.../pipeline.yaml, "--plan", "plan.md", "--no-viz"])` with **real** subprocesses. A real `.venv-sandbox/bin/python` command would 127-fail in that fixture (no `.venv`, no `tests/`) and break all 6 historical tests + `TestExampleExtensionless` (:162-170, loads the config only - stays valid). **Therefore the e2e fixture scripts `_run_command`** (module-local seam, queue of behaviors, default pass; unit/graph tests keep real commands). `_passing_scripts` (:30-39) covers dev/review/qa only - the test step is mechanical, so `agents_called` assertions in historical tests stay valid as-is.
- **Agent prompts to rewire:** `examples/self/sdk_agents/dev.agent.md` L20/L24/L28-37 ("You own the test suite - there is no separate test agent. Before you declare the work done, run the full suite: `.venv-sandbox/bin/python -m pytest tests/ -q`…"); `examples/self/sdk_agents/qa.agent.md` L8 ("already run the test suite (full green required)") and L25 ("Run `.venv-sandbox/bin/python -m pytest tests/ -q` - the full suite must pass"). `review.agent.md` L18-19 (test-quality checks) stays.
- `tests/test_viz.py::test_shipped_self_loop_topology` (:129-145) asserts the shipped example's chain `['dev','review','qa','commit','success']` + the review/qa fail edges - must be updated when the example gains the `test` step.
- **Plan-011 interplay:** plan 011 (COMPLETED) rewrites assertions in `test_stages.py`/`test_graph.py`/`test_e2e_fixtures.py` that reference the agent `test` step (e.g. `messages_for("test")[1]`, flows `["dev","test","test",...]`). After this plan's rename those references must read `check`.
- Uncommitted working-tree changes to `src/agent_engine/verdicts.py` + `tests/test_verdicts.py` (caveat-tolerant verdict parsing) are someone else's in-flight work - **do not touch**.

## Behaviour matrix

| Situation | Result |
|---|---|
| Command exits 0 | pass → `step_verdicts[name]="pass"`, route `on_pass` (default: next step / `success`) |
| Command exits nonzero (incl. 127 not-found, killed-by-signal) | fail → `step_verdicts[name]="fail"`, follow `on_fail` exactly like `VERDICT: FAIL` (None⇒END / `Route`+retry⇒bump while `attempt < max_attempts` / else `resolve(on_fail)`) |
| Command has not exited by `timeout` (default 600s) | fail - entire process group SIGKILLed, note says `(attempt N - timed out)` |
| Retry after fail | bump node rebuilds context with the note: output tail (last 4000 chars) + attempt number - same as agent-verdict retries |
| Command prints to stdout/stderr | printed and teed into the viz console bus, attributed to the step's node; fail notes carry the tail |
| Command step sets `produces`/`artifact`/`verdicts`/`params`/`max_iterations` | config error at load (banned fields) |
| `timeout` set without `command` | config error at load |
| `agent` **and** `command` (or `action`) on one step | config error at load - `exactly one of 'agent', 'action', or 'command' is required`; pipeline never runs |
| Step is *named* `test` | irrelevant - the kind is the `command:`/`agent:`/`action:` key alone; the example's mechanical gate is named `test`, the fixtures' agent gate is named `check` |

## Global Constraints

- TDD red→green→refactor for every task.
- Mock policy (repo rule): `run_agent` remains the only stubbed *agent* seam (`tests/conftest.py::FakeAgents`). Command steps run **real subprocesses** in `test_stages.py` and `test_graph.py`. The **sole** exception: `tests/test_e2e_fixtures.py` scripts `agent_engine.stages._run_command` (module-local queue, default pass) because the shipped example's real command cannot run in the consumer-repo fixture - the seam is local to that file, never global, and `_run_command` itself stays covered by real-subprocess tests elsewhere.
- **Fixture naming (user's "use a different name to avoid confusion"):** the internal fixtures' agent step named `test` is **renamed `test` → `check`** (step name + agent name, mechanics untouched) in `tests/test_stages.py::STEPS` and `tests/test_graph.py::REFERENCE_YAML` + every test that scripts/asserts that agent. The user-facing surfaces drop the agent `test` step entirely and use the name `test` for the mechanical step. After this plan, no agent is ever named `test` anywhere.
- Do not touch `src/agent_engine/verdicts.py` / `tests/test_verdicts.py` (in-flight uncommitted work).
- Full suite green at the end of every task: `.venv-sandbox/bin/python -m pytest tests/ -q` (baseline 258 passed).

## Locked decisions (from user / research)

| Decision | Choice |
|---|---|
| Kind key | Sibling `command:` key (user-renamed) - not `type:`, **never** the step's name |
| Step name in example/pin | The mechanical step is named `test` (user: "drop the test step and rename suite to test"); the old *agent* step named `test` is dropped there |
| Fixture agent name | Internal fixtures' agent gate renamed `test` → `check` (user: "use a different name to avoid confusion"); mechanics untouched |
| Pass/fail source | Solely the exit code: 0 ⇒ pass; nonzero, signal-killed, command-not-found, or **no exit by the timeout** ⇒ fail |
| Both `agent` + `command` (or `action`) | Load-time `ValueError`; no precedence, no silent pick |
| Timeout | Optional sibling `timeout:` (seconds, must be > 0), default 600s; SIGKILL the whole process group (`start_new_session=True`) |
| Command-step config surface | `command` + `timeout` only; `produces`/`artifact`/`verdicts`/`params`/`max_iterations` banned (workspace side effects persist; addable later) |
| Retry notes | Same shape as agent-verdict notes: `## {name} output (attempt N)` + output tail (last 4000 chars); timeout variant `(attempt N - timed out)` |
| `step_verdicts` | Reused as-is: `"pass"` / `"fail"` recorded under the step name; no new state keys (checkpoint format unchanged) |
| Output | Printed (and viz-attributed) on every run - pass or fail |
| Dogfooding | **In this plan**: `examples/self/pipeline.yaml` gains the mechanical `test` step (`command: .venv-sandbox/bin/python -m pytest tests/ -q`, `timeout: 600`, `on_fail: {goto: dev, retry: true}`) between `dev` and `review`; dev/qa agent prompts rewired; e2e fixtures gain the local command seam + failure-replay tests |

## Open questions for the user

1. **Default timeout** - 600s. The engine has no LLM timeout default today; 600s is a judgment call for pytest-class suites. ~~Veto if you want no-timeout-by-default or another number.~~ **Resolved 2026-10-09: user confirmed 600s - locked.**
2. **Test-step placement** - `dev → test → review → qa → commit` (gate before the review stages). ~~Alternatives: after `qa`, or retry `dev` vs a dedicated fixer - say the word if you want different wiring.~~ **Resolved 2026-10-09: user confirmed `dev → test → review` - locked.**

---

### Task 1: config schema + `test_config.py`

**Files:**
- Modify: `src/agent_engine/config.py`
- Test: `tests/test_config.py`

- [x] **Step 1: Failing tests.** (a) Update the two `match=` strings (:206, :213) to `exactly one of 'agent', 'action', or 'command' is required`. (b) Add validation tests: `command: "make check"` stays a plain string with `timeout is None`; `command` + `timeout: 60` round-trips; `timeout: 0`/negative rejected; command+agent, command+action, and agent+action+command rejected (same 3-way message); `timeout` without `command` rejected; each banned field on a command step rejected (message `step 'x': 'command' steps cannot set 'artifact'` etc., style-matched to existing messages). (c) `test_minimal_config_applies_defaults`: assert `step.command is None` and `step.timeout is None`. (d) In the canonical `FULL_YAML` pin: **drop the agent `test` step** (artifact `ci-fix.md`, PASS/FAIL) and put the mechanical step in its place, same position and wiring: `dev.on_pass: test`; `- name: test` / `command: python -m pytest` / `timeout: 600` / `on_pass: review` / `on_fail: {goto: dev, retry: true}`. Update the pin's header comment (verbatim-from-009 claim → note that 012 replaced the agent `test` step with the mechanical `test` step). Update the round-trip test (:103, still 6 steps) to assert `test.agent is None`, `test.command == "python -m pytest"`, `test.timeout == 600`, `test.on_fail == Route(goto="dev", retry=True)`, `dev.on_pass == "test"`.
- [x] **Step 2: Implement.** Add `command: str | None = None` + `timeout: float | None = Field(default=None, gt=0)` to `StepConfig`; rewrite `_check_shape`: exactly one of agent/action/command; `timeout` requires `command`; command-step banned-field checks; existing pairing/exclusivity checks unchanged. Red→green→refactor.

### Task 2: rename the internal fixtures' agent `test` → `check`

**Files:**
- Test: `tests/test_stages.py`, `tests/test_graph.py`

- [x] **`test_stages.py`:** in the `STEPS` fixture (:30-39), rename the step (`"name": "test"` → `"check"`) and its agent (`"agent": "test"` → `"check"`); `dev.on_pass: check`. Update every assertion that mentions the `test` step/agent (e.g. `TestBuildContext`'s `'test agent writes: `.pr/ci-fix.md`'` → `'check agent writes: …'`). Router tests are index-based (`steps[0/1/2]`) - no index changes, but check any `retry_target`/name string assertions.
- [x] **`test_graph.py`:** in `REFERENCE_YAML` (:26-71), rename the step (`- name: test` → `- name: check`) and `agent: test` → `agent: check`. Update everything that scripts or asserts that agent by name: `script_all_pass` (:103-107) and the per-test `fake_agents` scripting keys `"test"` → `"check"` (fail-retries-from-dev, unclear-retry, stale-artifact, agent-exception, exhaustion, max-attempts, `TestResume`'s crash agent); `agents_called()` assertions (`["dev","test",...]` → `["dev","check",...]`); `messages_for("test")` → `messages_for("check")`; `TestVizStream`'s node order `'test'` → `'check'` (:266-332); any topology assertions.
- [x] **Cross-plan note:** plan 011's Tasks 1-3 (COMPLETED) reference `messages_for("test")` and flows like `["dev","test","test","review","qa"]` - those references must read `check`.
- [x] Run `tests/test_stages.py tests/test_graph.py` - green (pure rename, no behavior change).

### Task 3: command node + router gate + `test_stages.py`

**Files:**
- Modify: `src/agent_engine/stages.py`
- Test: `tests/test_stages.py`

- [x] **Step 1: Failing tests.** New `TestCommandNode` class (own cfgs via `dataclasses.replace(runtime, cfg=make_cfg([...]))`, `make_state` helper; name the step `test` for realism - the name must not matter): (1) `command: "true"` → `{"step_verdicts": {"test": "pass"}, "failed": False}`, no notes; (2) `command: "printf 'FAILURE OUTPUT'; exit 1"` with `on_fail: {goto: dev, retry: true}` → fail verdict, `retry_target == "dev"`, note contains `FAILURE OUTPUT` and `attempt 1`; (3) capsys: output printed for both pass and fail; (4) `command: "sleep 5"` + `timeout: 0.2` → fail, note contains `timed out`; (5) workspace-is-cwd: `command: "pwd"` output contains `str(runtime.workspace)`; (6) monkeypatch `agent_engine.stages._run_command` to raise → `{"failed": True}` (hard-fail, no fake_agents); (7) plain `on_fail: dev` (no retry) → fail verdict with **no** `retry_target`. New router cases in `TestRouter` style: command step + `step_verdicts[test]=="pass"` → resolves `on_pass`; `"fail"` attempt 1 → `"bump"`, attempt 3 → `END`; fail + plain `on_fail` → that step; fail + no `on_fail` → `END`. One `TestBuildContext` addition: a command step contributes no "Expected output paths" line. (`STEPS` fixture untouched beyond Task 2's rename.)
- [x] **Step 2: Implement.** `DEFAULT_COMMAND_TIMEOUT = 600.0` (next to :34); `_run_command(command, workspace, timeout)` using `subprocess.Popen(..., cwd=workspace, shell=True, start_new_session=True, stdout=PIPE, stderr=STDOUT)` + `communicate(timeout=...)` + `os.killpg(pgid, signal.SIGKILL)` on `TimeoutExpired` (returns `(exit_code, output, timed_out)`); `make_command_node(runtime, step)` mirroring the agent FAIL branch (:211-217) for notes/retry_target; extend the router gate at :261 to `if step.verdicts is None and step.command is None:`; update the module docstring's fixed-policy bullets (exit code = verdict; timeout kills the process group; output tail into retry notes; "unclear" impossible). Imports: `os`, `signal`, `subprocess`. Red→green→refactor.

### Task 4: graph 3-way dispatch + `test_graph.py`

**Files:**
- Modify: `src/agent_engine/graph.py`
- Test: `tests/test_graph.py`

- [x] **Step 1: Failing tests.** Add a `COMMAND_STAGE_YAML` string (module-level, mirroring `REFERENCE_YAML`'s shape): `dev` (agent, produces `implementation-summary.md`, `on_pass: test`) → `test` (`command: python -c "import sys; import pathlib; print('test output'); sys.exit(0 if pathlib.Path('.pr/fix.marker').exists() else 1)"` - colon-space-free because YAML plain scalars can't contain `: `; `on_pass: commit`, `on_fail: {goto: dev, retry: true}`) → `commit` (action, `params.message: "feat: construct {subject} (agent dev loop)"`). New `TestCommandStep` class: in each test, write `commandstage.yaml` into the repo fixture, `git add -A && git commit` it (clean worktree for `commit_allowed`), then run the pipeline entry on it exactly as `TestRunPipeline` does. Tests: (1) dev writes `fix.marker` → test passes → commit succeeds, `agents_called() == ["dev"]`; (2) dev scripted `[("ok",), ("write", "fix.marker", "")]` → first test run fails, retry from dev, second passes: `agents_called() == ["dev", "dev"]`, second dev message contains `test output` and `attempt 1`; (3) `command: sleep 3` + `timeout: 0.2` variant → never passes, run exhausts (`False`, all attempts), each dev message contains `timed out`, the test itself finishes fast (killpg works); (4) fail-forever variant + CLI `max_attempts 2` → `(False, 2)`, no commit. (`REFERENCE_YAML` untouched beyond Task 2's rename.)
- [x] **Step 2: Implement.** In `build_graph` (:59-62) make the node selection 3-way (`step.agent` / `step.command` / else action) and update the docstring. Leave `REFERENCE_YAML`, `TestResume`, and `TestVizStream` untouched beyond Task 2's rename. Red→green→refactor.

### Task 5: viz retry-edge label + `test_viz.py`

**Files:**
- Modify: `src/agent_engine/viz.py`
- Test: `tests/test_viz.py`

- [x] **Step 1: Failing test.** New test in `TestTopologyMermaid` using the `_pipeline` helper (:121-127) with a dev+test pair (`test.command: "true"`, `on_fail: {goto: dev, retry: true}`): assert the mermaid source contains `test -. &nbsp;fail&nbsp; .-> dev;`. (`test_retry_without_verdicts_falls_back_to_retry` stays green - plain-agent fallback unchanged. `test_shipped_self_loop_topology` is updated in Task 6, not here.)
- [x] **Step 2: Implement.** viz.py:50-54: label = `step.verdicts.fail` if verdicts, else `"fail"` if `step.command is not None`, else `"retry"`. Red→green→refactor.

### Task 6: dogfood - example pipeline, agent prompts, e2e fixtures

**Files:**
- Modify: `examples/self/pipeline.yaml`
- Modify: `examples/self/sdk_agents/dev.agent.md`, `examples/self/sdk_agents/qa.agent.md`
- Test: `tests/test_e2e_fixtures.py`, `tests/test_viz.py`

- [x] **Step 1: Example pipeline.** In `examples/self/pipeline.yaml`: `dev.on_pass: test`; insert between `dev` and `review`:
  ```yaml
    - name: test
      command: .venv-sandbox/bin/python -m pytest tests/ -q
      timeout: 600
      on_pass: review
      on_fail: { goto: dev, retry: true }
  ```
- [x] **Step 2: Agent prompts.** `dev.agent.md` (L20/L24/L28-37): replace the "You own the test suite - there is no separate test agent… run `.venv-sandbox/bin/python -m pytest tests/ -q` … state the final test result in the summary" block with pipeline-gate wording: the pipeline runs the suite mechanically after this stage (the `test` command step); you MAY run the suite locally while working, but the gate - not your summary - is the source of truth; if the gate fails you are retried with the failing output in your notes - fix the root cause. `qa.agent.md` L8 and L25: reword to "the pipeline's `test` gate already ran the full suite green" - QA verifies/reviews, it no longer re-runs the suite itself. (`review.agent.md` L18-19 stays.)
- [x] **Step 3: e2e seam + failing tests.** In `tests/test_e2e_fixtures.py`: add a module-local command queue + `_fake_run_command(command, workspace, timeout)` dispatcher (behaviors: `("pass",)` → `(0, "", False)`; `("fail", output)` → `(1, output, False)`; `("timeout", output)` → `(None, output, True)`; last behavior repeats; default `("pass",)`), installed by the `consumer_repo` fixture via `monkeypatch.setattr("agent_engine.stages._run_command", _fake_run_command)` with the queue reset, plus a `_script_test(*behaviors)` helper. Historical tests need **no edits** (default pass). Add `TestCommandGate` tests: (1) pass-through: rc 0, `agents_called() == ["dev", "review", "qa"]`, commit log present; (2) `_script_test(("fail", "2 failed, 1 passed in 0.05s\n"), ("pass",))` → rc 0, `agents_called() == ["dev", "dev", "review", "qa"]`, `messages_for("dev")[1]` contains `2 failed, 1 passed` and `test output (attempt 1)`; (3) `_script_test(("timeout", "collected 250 items\n"), ("pass",))` → rc 0, `messages_for("dev")[1]` contains `timed out` and `collected 250 items`; (4) `_script_test(("fail", "boom\n"))` → rc != 0, `agents_called() == ["dev"] * 10` (example `max_attempts: 10`), no `(agent dev loop)` commit.
- [x] **Step 4: Shipped-topology test.** `tests/test_viz.py::test_shipped_self_loop_topology`: chain becomes `['dev','test','review','qa','commit','success']`; add the `test -. &nbsp;fail&nbsp; .-> dev;` edge assertion; keep the review/qa edge assertions.
- [x] **Step 5:** Run `tests/test_e2e_fixtures.py` and `tests/test_viz.py` - red where expected, then green with the engine work from Tasks 1-5 in place.

### Task 7: README

**Files:**
- Modify: `README.md`

- [x] Intro (:3-8): mention the three step kinds (agent with verdict protocol / deterministic action / mechanical command).
- [x] "pipeline.yaml reference": re-sync the verbatim-from-example block with the new example (it now contains the mechanical `test` step).
- [x] "What a run looks like" diagram (:57-62) + item 1 (:67-68): `dev → test → review → qa → commit`; the `test` gate (not the dev agent) verifies green tests; a failing suite rewinds to `dev` with the failure output in the retry note.
- [x] "Step semantics" bullet (:137): `agent`, `action`, and `command` are mutually exclusive; exactly one is required. New bullet: a `command` step is **mechanical** - the command runs in the workspace shell; exit 0 passes, anything else (including no exit by `timeout`, default 600s) fails and follows `on_fail` with the output tail in the retry note; the kind is the `command:` key, never the step's name.
- [x] "Fixed policies": add "**Command steps are mechanical** - never an LLM call; the exit code is the verdict; a timeout kills the process group."

### Task 8: full verification

- [x] `.venv-sandbox/bin/python -m pytest tests/ -q` green (baseline 258 passed).
- [x] grep for `exactly one of 'agent' or 'action'` → zero hits outside this plan doc / git history.
- [x] grep tests/planning-docs for stale "You own the test suite" / "dev agent owns the test suite" prose → none beyond deliberate audit-trail text.
- [x] grep tests/ for an agent named `test` (e.g. `agent: test`, `"test"` scripting keys) → zero hits; the name `test` remains only on the mechanical step.
- [x] `git status` confirms `src/agent_engine/verdicts.py` / `tests/test_verdicts.py` remain the only pre-existing modified files (untouched by this work).

## Verification

1. `.venv-sandbox/bin/python -m pytest tests/ -q` green - **325 passed** (was 258 at plan time).
2. Manual (no LLM calls): `python -c` load a temp pipeline with a `command` step via `agent_engine.config.load_config` - confirm validation (`command` + `timeout`; the exactly-one error when `agent` is also set).
3. Real run - dogfooded: the engine's own dev loop implemented this plan against a pipeline whose `test` step runs the repo suite (commit `ea2671c`); the user watched the gate live in the viz demo (`run-demo.sh`) and confirmed it runs (`lgtm`).

## Amendments

- **2026-10-09** - Both open questions resolved by user: default timeout **600s** locked; test-step placement **`dev → test → review`** locked (mechanical gate runs after the dev agent, before the review agent; failure rewinds to `dev` with the output tail in the retry note). No plan changes beyond confirming the already-specified values.
- **2026-10-09 (implementation)** - Plan implemented and dogfooded (commit `ea2671c`). Two follow-on fixes shipped with completion:
  1. **Viz console hard-wrap** - rich-based SDK output was baked with 80-column newlines (the capture pipe isn't a TTY, so rich fell back to terminal-width wrapping); widening the sidebar couldn't reflow it. Fix: `ConsoleCapture.attach()` now sets `os.environ["COLUMNS"] = str(MAX_LINE)` (save/restore in `detach()`), so captured agent output arrives unwrapped and `pre-wrap` does the reflowing. Covered by `TestConsoleCapture::test_columns_env_wide_during_capture` / `test_columns_env_restored_after_capture`.
  2. **Demo drift** - `viz_demo.py`'s scripted scenarios predated 012: the graph rendered the new `test` node (topology comes from the real `examples/self/pipeline.yaml`) but the fabricated run skipped it. `run_scenario()` now runs the mechanical gate between `dev` and `review` on every attempt (both scenarios), with command-flavoured console output (`$ .venv-sandbox/bin/python -m pytest tests/ -q` + green summary). Covered by `TestDemo::test_demo_runs_mechanical_test_step` / `test_demo_failure_scenario_runs_test_step`.
