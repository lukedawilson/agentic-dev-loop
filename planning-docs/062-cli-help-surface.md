# 062 - CLI help surface: help command and bare invocation

> **Status: PROPOSED**

## Scope

Two CLI entry behaviors:

1. `agentic-dev-loop help` prints the complete list of subcommands.
2. `agentic-dev-loop` with no arguments prints the same list.

Both must stay side-effect free (no pipeline, extension, or SDK imports),
per the existing lightweight-import pin in `tests/test_cli.py`.

## Current behavior (evidence)

- `main()` dispatches the eight subcommands via an `argv[0]` if-chain, then
  falls through to the two-phase pipeline path (probe parser with
  `pipeline` positional `nargs="?"`).
- Bare `agentic-dev-loop`: the probe finds no pipeline, delegates to
  `_core_parser().parse_args(argv)`, and argparse exits 2 with the
  missing-pipeline error. No test pins this (verified).
- Top-level `--help`: same delegation path; argparse prints only the core
  run-path help (pipeline positional and run flags). No subcommand list
  exists anywhere.
- `agentic-dev-loop PIPELINE --help` already prints loader-contributed
  flags (two-phase registry). Per-subcommand `--help` already works via each
  subparser. Both stay unchanged.

## Contract

- `agentic-dev-loop` (no arguments): print the command list, exit 0.
- `agentic-dev-loop help`: print the same list, exit 0.
- `agentic-dev-loop --help` / `-h`: print the same list, exit 0 (open item 1).
- `agentic-dev-loop PIPELINE --help` and per-subcommand `--help`: unchanged.
- `help` takes no arguments (open item 2).

## Interplay with 061 (default pipeline)

Bare no-args shows the list instead of running. The 061 default pipeline
(`agentic-dev-loop/pipeline.yaml`) still applies when run flags are present
without a positional (e.g. `agentic-dev-loop --dry-run`), and when the
positional is given explicitly it wins as today. Add a cross-reference to
061 section 1.

## Design

### Help text

A module-level `_TOP_HELP` constant (static string, no imports). Content:
run usage line plus the eight subcommands plus `help`, one line each,
reusing the existing parser descriptions to avoid drift:

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
  init             Write agentic-dev-loop pipeline, agent, and trigger files.
  doctor           Probe this host and repo for onboarding prerequisites.
  upgrade          Install the latest engine release and rewrite the project pin.
  help             Show this list.

Run `agentic-dev-loop <command> --help` for per-command options.
```

The `report` line reflects 061 (server default, `--markdown` for text); the
`init` line drops "trigger" once 061 lands (init then writes pipeline and
agent files only).

### Mechanics

In `main()` (cli.py):

- `if argv and argv[0] == "help": print(_TOP_HELP); return 0` added to the
  dispatch chain.
- `if not argv: print(_TOP_HELP); return 0` before the probe.
- `--help`/`-h` override in the pipeline-None branch only: when
  `known.pipeline is None` and argv contains `-h` or `--help`, print
  `_TOP_HELP` and return 0 before delegating to `_core_parser()`. With a
  pipeline present, argparse keeps handling `--help` (loader flags
  included).

All three paths are before any registry/loader import, preserving the
side-effect-free pin.

### Drift guard

The one-liners are hand-written while the parser descriptions live in each
`_run_*` parser. Add a drift test: construct each subcommand parser (all are
lightweight, no SDK imports) and assert its `description` text appears in
`_TOP_HELP`. This catches a renamed/redescribed subcommand without a runtime
cost.

## Test impact

- Add `TestHelp` in `tests/test_cli.py`:
  - `main([])` returns 0 and prints all eight subcommand names plus the
    usage line (capsys).
  - `main(["help"])` prints the identical text.
  - `main(["--help"])` prints the same list (SystemExit(0), per argparse
    convention for other help paths).
  - `main(["help", "extra"])` behaves per open item 2.
  - Spot-check `main([PIPELINE, "--help"])` still includes loader flags.
- Drift test as above.
- Extend `TestCliLightweightImports` to run `help` and bare invocation in a
  clean interpreter, asserting no `openhands.sdk` / `langgraph` /
  `engine.dryrun` / `engine.stages` imports.
- No existing test pins the old bare no-args exit-2 behavior (verified), so
  no test breaks.

## Open items

1. Should `--help`/`-h` at the top level show the full list too? Proposed:
   yes (today it shows only run-path help, which hides the subcommands).
2. `help` with extra arguments: proposed loud error ("help takes no
   arguments"); alternative is `help <command>` printing that command's
   `--help`.
3. Bare no-args exit code: proposed 0 (matches `--help` convention).
