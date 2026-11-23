# CLI usage

```text
agentic-dev-loop [PIPELINE] [flags]
```

`PIPELINE` is optional: with no arguments at all, `agentic-dev-loop` prints
the help. Flags without a positional use `agentic-dev-loop/pipeline.yaml`
(relative to the current directory); a missing default fails loudly instead
of falling back.

Exit code is `0` on pipeline success, `1` otherwise.

## Run flags

| Flag | Effect |
| --- | --- |
| `--dry-run` | Full load-and-validate pass, then print the resolved plan. No LLM, no Docker, no state writes, no API key. Exit `0` on a clean load. |
| `--resume <thread-id>` | Continue the latest checkpoint of a previous run (crash, Ctrl-C, exhaustion) with all accumulated notes intact. |
| `--max-attempts <n>` | Override the pipeline's `max_attempts`. |
| `--env <file>` | Load secrets from a host env file outside the workspace. Optional: the process environment is the default, a named-but-missing file is a loud error, and values already set in the environment win. |
| `--no-viz` / `--viz-port <port>` | Suppress the live graph view / set a preferred port (default 8321; falls back to a free port when busy). |

## Other commands

- `agentic-dev-loop run-status <pipeline>` renders the latest run as a
  mermaid flowchart with the current step highlighted, reading the run-state
  checkpoint database directly. `run-status --install` installs the bundled
  `/run-status` command into OpenCode.
- `agentic-dev-loop report` serves the **historical runs view** in the
  browser (default port 8321, falling back to a free port when busy; blocks
  until ctrl+c and prints the URL). It reads the same run records as the
  markdown report and lists past runs with since / until / status filters,
  an aggregate metrics panel, and a per-run detail page carrying the brief,
  implementation summary, review findings, and QA report. The live viz also
  links to it from the **History** button in the graph page header.
- `agentic-dev-loop report --markdown [<run-id>]` renders the same history
  as ASCII markdown instead: an aggregate dashboard (loop health, TDD,
  review, and QA metrics with provenance labels) or a deep dive for one
  12-hex run directory. `--since` / `--until` / `--status` filter the set.
  `--markdown` cannot be combined with `--port`.
- `agentic-dev-loop containers` inspects the engine's sandbox containers and
  networks from the host; `containers --reap` removes registry-owned orphans
  whose owner process is dead. A SIGTERM to a running pipeline still closes
  the sandbox; SIGKILLed runs are reclaimed by the startup reaper on the next
  run or by `containers --reap`.
- `agentic-dev-loop fanout [PIPELINE] [flags]` runs one pipeline per
  document source, all in parallel, each in its own git worktree. Repeat
  the document flags to add entries (`--plan <doc>`, `--aidlc-unit <slug>`,
  or a fanout-capable custom loader flag) and mix them freely; at least two
  entries are required, and a single-entry invocation points you at the
  plain single-run form instead. `PIPELINE` must come before the document
  flags, must live inside the repository, and must be tracked in the base
  commit. Each entry gets a worktree under `../fanout` next to the repo
  root (`--worktree-root` overrides) on branch `loop/fanout/<slug>`, forked
  from `--base-ref` (default `HEAD`), and runs the complete unchanged
  pipeline. Child output streams with a per-entry prefix, live viz URLs are
  collected into a final summary table (PASSED / FAILED / ABORTED /
  SKIPPED), and children keep serving their viz after finishing until one
  ctrl+c stops the whole group; the exit code is `0` only when every entry
  passed, `1` otherwise, and `2` for usage errors. `--dry-run` prints the
  computed plan without touching git; `--max-attempts`, `--env`, and
  `--no-viz` are forwarded to every child. Fanout never merges or pushes:
  each branch is merged by hand.
- `agentic-dev-loop claude-login` authenticates a Claude Pro or Max
  subscription (OAuth PKCE flow) and writes the token file the engine
  reads.
- `agentic-dev-loop copilot-login` authenticates a GitHub Copilot
  subscription (device flow); `copilot-models` lists the models the token's
  tier can use. See [LLM providers](llm-providers.md).

## Onboarding commands

`agentic-dev-loop init` scaffolds the onboarding surface for a repo, and
`doctor` + `upgrade` keep the host and the committed pin honest. They are
your commands to run: the CLI never runs them for you.

```bash
agentic-dev-loop init --profile python        # or: --profile dotnet-node, rust
agentic-dev-loop init --profile ./my-profile.yaml
agentic-dev-loop init --profile ./my-profile.yaml --recipe ./my-recipe.yaml
```

`init` writes the onboarding surface from a bundled toolchain profile:
`agentic-dev-loop/pipeline.yaml`, the three agent files,
`agentic-dev-loop/.engine-version` (the exact installed engine version), any
missing premise files, gitignore additions, and an env-file template. It
detects solution files, `package.json` directories, lockfiles, README, and
AGENTS.md, asks only what detection cannot answer, and refuses to overwrite
an existing `agentic-dev-loop/` directory. A consumer-authored profile file
can be passed by path instead of a bundled name, and `--recipe` accepts a
consumer recipe file path the same way, so a toolchain this engine does not
ship can onboard with its own image and its own steps. A path profile is
stored in the pipeline relative to `agentic-dev-loop/`, so the committed
pipeline resolves from any working directory.

```bash
agentic-dev-loop doctor --fast     # per-run gate: hard failures abort
agentic-dev-loop doctor --json     # machine-readable results
```

`doctor --fast` probes the host and repo before you run: hard failures stop
you here (engine pin mismatch, engine runtime availability, API version, git
repo, `.env*` files); warnings print and continue.

```bash
agentic-dev-loop upgrade
```

`upgrade` installs the latest engine release and rewrites the pin, so the
host install and the committed pin converge in one explicit action.

See [Authoring a loop](authoring.md) for what `init` writes and how to adapt
it.
