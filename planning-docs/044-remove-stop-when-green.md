# Plan 044: Remove the dormant stop-when-green feature

> **Status: COMPLETED**

## Scope

Remove the 032 stop-when-green mechanism from the engine: the
`stop_when` config surface, the `_StopWhenController` green-signal
state machine, its test file, and its documentation. Keep the shared
turn-level pause machinery, which the 035 finalize nudge depends on.

The dev-step cap+nudge hardening that motivated this removal already
shipped as config-only commits in both repos (agent-engine `f1a4c57`,
yosk-cms `f9034ce`): `max_iterations: 100` + `finalize_nudge_turns: 10`
on the dev step. This plan covers only the engine-side deletion.

## Context

Plan 032 shipped three things: the STOP WHEN GREEN prompt rules, the
`terminal_timeout` per-step knob, and the mechanical `stop_when`
termination signal (`{ green_command, post_green_turns }`). The prompt
rules and `terminal_timeout` remain live and meaningful. The mechanical
signal never found a consumer:

- Zero pipeline YAML in either repo ever configured `stop_when` or
  `green_command` (verified by grep across agent-engine and yosk-cms:
  matches exist only in engine code, tests, README, and COMPAT-SHIMS).
- Its premise was structurally displaced. 032 assumed the dev agent
  runs the suite in its own sandbox (its incident: 787 passed locally).
  038/039 then made the dev sandbox network-off and moved all
  builds/tests into network-on mechanical steps after dev
  (restore -> test -> editor-tests -> conformance). No agent step can
  produce a green observation anymore, so `green_hit` can never fire
  even if the key were configured. The mechanical test gate is the
  modern stop-when-green: binary, fast, and it fails the run directly.
- The killed-run spiral (2026-10-31, run 5367f0c488d8) confirmed it:
  the dev agent's verification commands failed environmentally, so no
  green signal existed to key on; the step burned to the 500-turn
  default with no configured cap or nudge.

Removal follows the engine's dead-code hygiene: a tested feature with
no consumer and no viable mounting point in the current architecture
should not stay in the schema where it implies protection it cannot
deliver. Historical planning docs 032/033 stay untouched as records.

## Decisions

| # | Decision | Proposal | Answer |
|---|---|---|---|
| D1 | Remove `stop_when` from `StepConfig` (core/config.py). `StepConfig` already sets `extra="forbid"`, so a stale `stop_when:` key in any pipeline YAML fails load with a ValidationError naming the unknown key. No pipeline uses it (verified). | Remove field + validator; keep `extra="forbid"`; add a negative config test pinning the loud failure | PENDING |
| D2 | Remove `StopWhenSpec` (core/config.py) and its import/export in core/__init__.py | Delete class, validator, export | PENDING |
| D3 | stages.py: delete `_StopWhenController`; keep `_TurnPauseController` (035 depends on the pause hook) with docstring updated to drop the 032 framing; simplify `Runtime.run_agent` (drop the stop-when attach); rename `_run_stop_when_loop` to `_run_finalize_loop` with signature `(conv, max_iterations, finalize_controller)` and no green-signal branch. Finalize-nudge behavior unchanged | Rename + prune | PENDING |
| D4 | Tests: delete tests/test_032_stop_when_green.py; update the three `_run_stop_when_loop` call sites in tests/test_035_finalize_nudge.py to the new name/signature; update the test_sdk_compat canary docstrings and failure text from "032 turn-level stop-when-green seam" to the 035 finalize-nudge turn loop (same seam, same assertions) | TDD: red first with the D1 negative test | PENDING |
| D5 | COMPAT-SHIMS.md: retitle the "Turn-level pause seam (032 stop-when-green)" entry to the 035 finalize-nudge turn loop and update its removal criterion and pinning-test references. The seam itself stays pinned (the loop still relies on the SDK PAUSED break + callbacks) | Reword only | PENDING |
| D6 | README.md: remove the `stop_when:` table row (line 210) and the `stop_when` example at line 174 (replace with `terminal_timeout` or drop) | Tooling-path edit, human commit | PENDING |

## Changes (file by file)

1. `src/agentic_dev_loop/core/config.py`: delete `StopWhenSpec`
   (lines 309-335ish), delete `stop_when` field (line 390), delete the
   "requires agent" validator (lines 491-494).
2. `src/agentic_dev_loop/core/__init__.py`: drop `StopWhenSpec` from
   the import and `__all__`.
3. `src/agentic_dev_loop/engine/stages.py`: delete
   `_StopWhenController` (lines 111-136ish); update `_TurnPauseController`
   docstring; drop the `controller` attach at lines 205-206; rename
   `_run_stop_when_loop` (lines 298-343) to `_run_finalize_loop`,
   signature `(self, conv, max_iterations, finalize_controller=None)`,
   deleting the `controller`/`green_signal`/`post_nudge_turns` branch;
   update the call site at lines 255-257.
4. `tests/test_032_stop_when_green.py`: delete.
5. `tests/test_035_finalize_nudge.py`: update three call sites to
   `_run_finalize_loop`; add `test_stop_when_key_rejected` asserting a
   step with `stop_when:` raises ValidationError (the D1 pin; red
   before the field is removed).
6. `tests/test_sdk_compat.py`: reword the
   `test_canary_sdk_turn_level_pause_seam_still_present` docstring and
   failure messages from 032/_StopWhenController to the 035 turn loop.
7. `COMPAT-SHIMS.md`: reword the turn-level pause seam entry.
8. `README.md`: remove the two stop_when mentions.

No pipeline files change in either repo (neither uses the key). The
untracked `build/` and `agentic_dev_loop.egg-info/` copies are build
artifacts and refresh on the next publish; no manual edit.

## Verification

- Red first: the new negative test fails against the current schema
  (`stop_when` still accepted).
- Full suite green: `.venv/bin/python -m pytest -q`.
- `grep -rn "stop_when\|StopWhen\|green_command\|post_green_turns\|_StopWhenController" src/ tests/ README.md COMPAT-SHIMS.md` returns
  nothing (planning-docs 032/033 are historical and exempt).
- Both pipelines still load: `agentic-dev-loop agentic-dev-loop/pipeline.yaml --dry-run`
  in each repo (they never used the key; this proves removal did not
  disturb loading).
- ASCII guard passes (tests/test_repo_ascii.py); no em dashes.

## Non-goals

- `terminal_timeout` (032's other knob) stays.
- No replacement green-detection mechanism: the mechanical test gates
  plus the new dev-step cap+nudge cover the killed-run spiral class.
- The prompt/directive/goal rework and any live-nudge feature are
  separate follow-up plans, discussed after this one lands.
