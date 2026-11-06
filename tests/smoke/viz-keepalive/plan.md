# 057 - Viz keeps serving after the run ends

Status: PROPOSED (smoke-test plan)

## Problem

Today the viz process dies with the run. `cli.main` returns as soon as the
pipeline finishes, the process exits, and the viz daemon thread dies with it.
The user cannot refresh the browser to inspect the final pipeline state - the
moment the loop finishes, the page is gone.

The fix: after a run ends, the viz server keeps serving the final state and
the terminal prints "Pipeline finished: press ctrl+c to exit". The user
refreshes the viz, inspects the final graph, then presses ctrl+c to exit.

## Decisions

**D1 - Keep-alive lives in `cli.main`, not in `run_pipeline`.** The library
function `run_pipeline` is used by tests and programmatic callers; it must
keep returning `(success, attempts)` the moment the run ends. Only the CLI
blocks. Approved shape:

- `cli.main(argv=None, *, keep_alive=True)` - a keyword-only seam. Existing
  CLI tests that call `main()` on completed runs pass `keep_alive=False`.
- After `run_pipeline` returns (success OR failure; both are completed runs
  with a live viz), when `keep_alive` is true and viz was started
  (`not args.no_viz` and not `--dry-run`), print the exact line
  `Pipeline finished: press ctrl+c to exit`, then block until the user
  presses ctrl+c, then return the same exit code as today.
- Blocking uses a module-level `_wait_for_interrupt()` helper (an interruptible
  wait, no tty dependency) so tests can patch it. The existing SIGTERM
  handler is untouched: a first SIGTERM still exits 143.
- Viz startup happens before the graph loop inside `run_pipeline`, so a
  completed run always has a live viz; pre-run failures (config load,
  host-boundary, sandbox startup) raise before the keep-alive line and exit
  as today.

**D2 - Exact message.** `Pipeline finished: press ctrl+c to exit`, printed to
stdout with a trailing newline. This exact string is load-bearing: the smoke
test detects it.

**D3 - Semantics preserved.** Exit codes (0 success, 1 failure, 143 SIGTERM)
are unchanged. `--no-viz` and `--dry-run` skip the keep-alive entirely (no
viz to serve). `run_pipeline`'s signature and behavior are unchanged.

**D4 - No new dependencies.** The change is stdlib-only (threading).

## Scope

- `src/agentic_dev_loop/engine/cli.py` - `main()` signature, keep-alive block,
  `_wait_for_interrupt()` helper.
- `tests/test_055_cli.py` - new keep-alive tests (names contain "keepalive").
- Nothing else. No change to `graph.py`, `run_pipeline`, viz/server.py, or the
  trigger script.

## Tasks (TDD, red-green-refactor)

1. Red: add tests to `tests/test_055_cli.py`, every new test name containing
   "keepalive":
   - a completed run with `keep_alive=True` prints the exact keep-alive line
     (patch `_wait_for_interrupt` to raise KeyboardInterrupt immediately);
   - the same run returns the same exit code as today;
   - `keep_alive=False` does not print the line and does not block;
   - `--no-viz` (and `--dry-run`) skip the keep-alive line.
   Run `.venv-sandbox/bin/python -m pytest tests/test_055_cli.py -q -m "not docker" -k keepalive`
   and confirm the new tests fail.
2. Green: implement the change in `cli.py` per D1-D3. Re-run the test command
   until green.
3. Refactor: keep the change minimal; no behavior outside D1-D3.

## Verification

- Unit: `.venv-sandbox/bin/python -m pytest tests/test_055_cli.py -q -m "not docker" -k keepalive`
- Regression: `.venv-sandbox/bin/python -m pytest tests/test_055_cli.py -q -m "not docker"`
- Smoke: `tests/smoke/run_smoke.py` (below) - the human watches the
  streamed run for the keep-alive line, checks the viz endpoint after
  completion, and presses ctrl+c to exit. The harness does not automate
  these checks: it just runs the engine and streams the output.

## How to run the smoke test

```
# From the repo root, with the smoke assets committed (git status clean):
./run-smoke.sh openhands tests/smoke/viz-keepalive
# or, without the wrapper:
./.venv/bin/python tests/smoke/run_smoke.py tests/smoke/viz-keepalive --provider copilot
```

- The two arguments are independent: `--provider` (openhands | copilot)
  picks the llm block that run_smoke.py injects into the pipeline template;
  the config directory is the plan only, and its `pipeline.yaml` must be
  provider-free (a template carrying its own `llm:` block is rejected).
- The config directory holds `pipeline.yaml` (the pipeline the run
  executes), `agents/dev.agent.md` (the agent definition), and optionally
  `plan.md` (fed to the run as an additional file).
- The engine runs FROM SOURCE (`PYTHONPATH=src`, no wheel, no pip install) in
  a disposable git worktree of this repo, so the workspace is never the
  source tree (host-boundary check stays green). The pipeline is
  deps -> dev -> test (the test step runs the keepalive test selection).
- Preconditions: Docker running; `.venv` exists; assets committed;
  `OPENAI_API_KEY` exported for `openhands` (DeepSeek via that env var), or
  the token file from `agentic-dev-loop copilot-login` plus a tier-listed
  model for `copilot`.
- The script streams the engine output and waits for the engine to exit on
  its own. It does not probe the viz endpoint, does not signal the engine,
  and does not decide pass or fail (the smoke test is human-run and
  human-gated). Pass `--keep-worktree` to inspect the worktree afterward.

## Risks

- None serious. The keep-alive block is CLI-only and skipped by every
  existing test via the `keep_alive` seam where needed.
