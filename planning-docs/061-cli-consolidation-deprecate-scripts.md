# 061 - CLI consolidation: deprecate scripts and the viz launcher

> **Status: APPROVED**

## Scope

Move the remaining script-hosted functionality into the CLI and delete the
scripts:

1. `report` becomes the standalone history server (runs view), with the
   current markdown output kept behind a text-mode flag.
2. `PIPELINE` positional becomes optional, defaulting to
   `agentic-dev-loop/pipeline.yaml`.
3. The standalone viz live mode (`python -m agentic_dev_loop.viz --pipeline`)
   is dropped entirely; `--history` is folded into `report`.
4. `trigger-agentic-dev-loop.sh` is deleted. The CLI gains a `--env` flag for
   optional secret files; the doctor gate, auto-upgrade, and pin-commit
   orchestration are dropped, not moved.
5. `viz_demo.py` moves to `tests/smoke/` and keeps running working-tree code;
   `run-demo.sh` is deleted.
6. Docs sweep: all launcher/trigger/demo references replaced by CLI
   documentation.

The `upgrade` command already exists (pipx install --force plus pin rewrite)
and needs no change. Dev-only tooling stays out of scope: `run-smoke.sh`,
`setup-venv.sh`, `apply-patches.sh`, `tools/*`.

## Decisions (recorded from the planning conversation)

- Report: server mode is the default; markdown mode survives behind
  `--markdown`; server takes `--port`, blocks until ctrl+c, prints the URL,
  no browser auto-open.
- `PIPELINE` default: `agentic-dev-loop/pipeline.yaml` (yaml, matching the
  init/doctor/trigger convention).
- Viz launcher: drop the standalone live mode entirely; fold `--history` into
  `report`; delete `viz/__main__.py` outright.
- `viz_demo.py`: move to `tests/smoke/` as a smoke tool; must run the
  working tree, not the wheel; must not clash with the existing smoke test.
  `run-demo.sh`: delete.
- Trigger: `--env` flag added to the CLI, optional, defaults to environment
  variables; the file must live outside the workspace; a named-but-missing
  file is a loud error; on key collisions the environment wins. Gate/heal
  orchestration (doctor --fast, auto-upgrade, pin commit): dropped.
- Doctor's `env-files` hard check (refuses `.env*` files inside the
  workspace) is kept unchanged for security.

## Design

### 1. PIPELINE positional becomes optional

- When omitted, resolve `agentic-dev-loop/pipeline.yaml` relative to the
  current working directory.
- Bare invocation with no arguments at all shows the command list instead
  of running (see 062); the default applies when run flags are present
  without a positional.
- If the default file does not exist, fail loudly with an error naming the
  searched path and the positional (`no pipeline at agentic-dev-loop/pipeline.yaml and none given`).
  No silent fallback.
- Resolution must happen before the two-phase probe (the probe needs the
  path to load extensions and register loader flags), so the default is
  resolved in the probe stage itself. `--help` and `--version` keep working
  with no pipeline present.
- This aligns the default with everything that already hardcodes the path:
  `init` creates it, `doctor` reads it, and the (now removed) trigger passed
  it explicitly.

### 2. `report` becomes the history server

- Default mode: start the runs-view server and block until ctrl+c, exactly
  like `serve_history` today. Flags:
  - `--port` (default 8321, free-port fallback preserved; port 0 for tests)
  - `--runs-root` (same semantics as today: where run records live; default
    `$AGENTIC_DEV_LOOP_RUNS_ROOT` or `~/.agentic-dev-loop/runs`; the server
    reads through the same `discover()` the markdown report uses)
  - `--audit-dir` (sandbox audit logs; alias `--state-dir` kept)
  - No browser auto-open; print the URL (launcher behavior).
- Markdown mode behind `--markdown`: exactly today's `report` output,
  including the optional `run_id` positional deep dive and `--since`/
  `--until`/`--status` filters. `--markdown` and server flags are mutually
  exclusive.
- Implementation: reuse `viz.server.serve_history(port)`; pass the runs root
  override through (today `serve_history` resolves it internally via
  `discover()`; add a parameter rather than a second resolution path).
- Tests: `tests/test_unit12_reporting.py` keeps covering `--markdown`; new
  tests cover server default (port 0, monkeypatched serve) and flag
  exclusivity. `tests/test_cli.py` / `tests/test_055_cli.py` monkeypatch
  targets updated.

### 3. Viz launcher removal

- Delete `src/agentic_dev_loop/viz/__main__.py` outright. No console script
  is registered, so no pyproject change.
- `serve_viz` stays: the live run path (default viz, `--viz-port`) uses it
  directly, not through `__main__`. `serve_history` stays and is reused by
  `report`.
- Doc updates: unit-13 BR-17 (LAUNCHER) rewritten to the `report` server
  contract; unit-4 functional-design references to a subagent-launched
  `python -m agentic_dev_loop.viz` server rewritten to `agentic-dev-loop
  report`.
- `tests/test_unit13_runs_viz.py` imports `serve_history`/`serve_viz` from
  the server module, so it is unaffected by deleting `__main__.py`.

### 4. Trigger deletion and the `--env` flag

- Delete `trigger-agentic-dev-loop.sh` and its template (055 templates).
- The CLI's run path gains `--env FILE`:
  - Optional; default is the process environment (what the run already reads:
    `llm.api_key_env`, `secrets:` keys).
  - The file must be outside the workspace: resolve both to absolute paths
    and hard-error if the file is inside the workspace or is the workspace
    itself. This preserves doctor's workspace `.env*` refusal.
  - A named file that does not exist is a loud error (the trigger silently
    skipped missing files; forbidden here).
  - Precedence: on key collisions the environment wins (values already set
    in the process environment are not overwritten by the file).
  - Loaded before the registry/loader resolution so secrets are visible to
    the whole run.
- Orchestration dropped: no automatic `doctor --fast` gate, no auto-upgrade,
  no pin commit. Operators run `doctor` and `upgrade` explicitly; both
  commands are unchanged.
- Test updates: `tests/test_trigger_script.py` deleted;
  `tests/test_055_init.py` (executable-bit assertion), `tests/test_055_templates.py`
  (trigger template), and `tests/test_hostboundary.py` (rels entry) updated.
  New tests for `--env`: out-of-workspace enforcement, missing-file loud
  failure, precedence, and default (no file) behavior.
- CHANGELOG entry: trigger removed; replacement is `agentic-dev-loop` with
  the default pipeline plus explicit `doctor`/`upgrade`.

### 5. `viz_demo.py` moves to `tests/smoke/`

- New home: `tests/smoke/viz_demo.py`. It stays a human-run dev tool, never
  CI.
- Source-first bootstrap: mirror `run_smoke.py` (insert `src/` ahead of
  everything so `import agentic_dev_loop` resolves to the working tree, not
  the .venv wheel). `run-demo.sh`'s PYTHONPATH shadowing dies with the
  script; the moved file carries its own sys.path insertion.
- Internal paths: `EXAMPLE` resolves from `__file__` (two parents up to the
  repo root) instead of one parent.
- `tests/test_viz_demo.py`: import changes from `import viz_demo` (repo root
  on sys.path via conftest) to the tests.smoke module; conftest's
  repo-root-on-sys.path comment updated or removed.
- `tests/test_source_resolution.py`: drop the `run-demo.sh` assertions; keep
  a pin that the moved demo (or run_smoke.py) bootstraps `src/` first.
- No clash with the existing smoke test: `run_smoke.py` and its
  `viz-provider/` config dir keep their names; the moved demo is a separate
  module in the same directory, run the same way (directly with the .venv
  python, not under pytest).

### 6. Docs sweep

- README: replace the `--history` launcher line, the trigger/pin section, and
  the `run-demo.sh` line with CLI documentation: `report` (server and
  `--markdown`), `--env`, the default pipeline path, `upgrade`, `doctor`.
- `docs/setup.md`: trigger steps become CLI steps.
- `.opencode/commands/setup.md`: update references.
- aidlc docs: unit-13 BR-17 and domain-entities launcher contract; unit-4
  plans that launch the viz server as a subprocess.
- Public-artefact rules apply: describe the product as it is now, no
  archaeology, ASCII only, unpinned install commands.

## Test impact summary

- Update: `tests/test_unit12_reporting.py`, `tests/test_cli.py`,
  `tests/test_055_cli.py`, `tests/test_viz_demo.py`,
  `tests/test_source_resolution.py`, `tests/test_055_init.py`,
  `tests/test_055_templates.py`, `tests/test_hostboundary.py`.
- Delete: `tests/test_trigger_script.py`.
- Add: `--env` behavior, default pipeline resolution (missing default =
  loud error), `report` server mode and flag exclusivity.
- Unaffected: `tests/test_unit13_runs_viz.py` (server module imports),
  doctor/checks tests, upgrade tests.
