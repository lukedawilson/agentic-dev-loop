# 061 - CLI consolidation: deprecate scripts and the viz launcher; add help surface

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
7. `agentic-dev-loop help` and bare `agentic-dev-loop` print the command
   list; top-level `--help`/`-h` shows the same list. All three paths
   stay side-effect free (no pipeline, extension, or SDK imports), per
   the lightweight-import pin in `tests/test_cli.py`.

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
- Help surface: bare no-args and `help` print the command list and exit
  0; top-level `--help`/`-h` prints the same list. `help` takes no
  arguments. The remaining open items (top-level `--help` content,
  `help`-with-extra-arguments behavior, bare no-args exit code) are
  recorded under Open items.

## Design

### 1. PIPELINE positional becomes optional

- When omitted, resolve `agentic-dev-loop/pipeline.yaml` relative to the
  current working directory.
- Bare invocation with no arguments at all shows the command list instead
  of running (see section 7); the default applies when run flags are present
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

### 7. Help surface (merged from 062)

Current behavior: `main()` dispatches the eight subcommands via an
`argv[0]` if-chain, then falls through to the two-phase pipeline path
(probe parser with `pipeline` positional `nargs="?"`). Bare
`agentic-dev-loop` delegates to `_core_parser().parse_args(argv)` and
argparse exits 2 with the missing-pipeline error (no test pins this,
verified). Top-level `--help` takes the same path and prints only
run-path help; no subcommand list exists anywhere.
`agentic-dev-loop PIPELINE --help` and per-subcommand `--help` already
work and stay unchanged.

Contract:

- `agentic-dev-loop` (no arguments): print the command list, exit 0.
- `agentic-dev-loop help`: print the same list, exit 0.
- `agentic-dev-loop --help` / `-h`: print the same list, exit 0
  (open item 1).
- `agentic-dev-loop PIPELINE --help` and per-subcommand `--help`:
  unchanged.
- `help` takes no arguments (open item 2).

The section 1 default pipeline still applies when run flags are present
without a positional (e.g. `agentic-dev-loop --dry-run`), and an
explicit positional wins as today.

Help text: a module-level `_TOP_HELP` constant (static string, no
imports). Content: run usage line plus the eight subcommands plus
`help`, one line each, reusing the existing parser descriptions to
avoid drift:

```text
usage: agentic-dev-loop [PIPELINE] [--resume THREAD_ID] [--max-attempts N]
                        [--no-viz] [--viz-port PORT] [--dry-run] [--version]

Run an OpenHands agent pipeline defined in YAML. PIPELINE defaults to
agentic-dev-loop/pipeline.yaml; run `agentic-dev-loop <PIPELINE> --help`
for loader-contributed options (e.g. --plan, --aidlc-unit).

commands:
  containers       Inspect engine sandbox containers and networks.
  copilot-login    Run the GitHub device flow and store the Copilot OAuth token.
  copilot-models   List the Copilot models available to this token's tier.
  report           Serve the run-history browser view (--markdown for text).
  run-status       Report the latest agentic-dev-loop run.
  init             Write agentic-dev-loop pipeline and agent files.
  doctor           Probe this host and repo for onboarding prerequisites.
  upgrade          Install the latest engine release and rewrite the project pin.
  help             Show this list.

Run `agentic-dev-loop <command> --help` for per-command options.
```

The `report` line reflects section 2 (server default, `--markdown` for
text); the `init` line already drops "trigger" (section 4).

Mechanics, in `main()` (cli.py):

- `if argv and argv[0] == "help": print(_TOP_HELP); return 0` added to
  the dispatch chain.
- `if not argv: print(_TOP_HELP); return 0` before the probe.
- `--help`/`-h` override in the pipeline-None branch only: when
  `known.pipeline is None` and argv contains `-h` or `--help`, print
  `_TOP_HELP` and return 0 before delegating to `_core_parser()`. With a
  pipeline present, argparse keeps handling `--help` (loader flags
  included).

All three paths run before any registry/loader import, preserving the
side-effect-free pin.

Drift guard: the one-liners are hand-written while the parser
descriptions live in each `_run_*` parser. Add a drift test: construct
each subcommand parser (all are lightweight, no SDK imports) and assert
its `description` text appears in `_TOP_HELP`. This catches a
renamed/redescribed subcommand without a runtime cost.

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
- Add `TestHelp` in `tests/test_cli.py`:
  - `main([])` returns 0 and prints all eight subcommand names plus the
    usage line (capsys).
  - `main(["help"])` prints the identical text.
  - `main(["--help"])` prints the same list (SystemExit(0), per argparse
    convention for other help paths).
  - `main(["help", "extra"])` behaves per open item 2.
  - Spot-check `main([PIPELINE, "--help"])` still includes loader flags.
- Add the drift test (subcommand parser descriptions appear in
  `_TOP_HELP`).
- Extend `TestCliLightweightImports` to run `help` and bare invocation
  in a clean interpreter, asserting no `openhands.sdk` / `langgraph` /
  `engine.dryrun` / `engine.stages` imports.
- No existing test pins the old bare no-args exit-2 behavior (verified),
  so no test breaks.

## Open items

1. Top-level `--help`/`-h` showing the full list: proposed yes (today it
   shows only run-path help, which hides the subcommands).
2. `help` with extra arguments: proposed loud error ("help takes no
   arguments"); alternative is `help <command>` printing that command's
   `--help`.
3. Bare no-args exit code: proposed 0 (matches `--help` convention).
