# Plan 067: Fanout CLI command

> **Status: COMPLETED**

## Goal

One command, `agentic-dev-loop fanout`, that takes a mixed set of doc sources
across any document loader (`--plan` repeated, `--aidlc-unit` repeated, custom
loader flags repeated), creates one git worktree and branch per entry, launches
one independent pipeline run per entry as a child process, prints each run's
live-viz URL as it comes up, and ends with a status table. No auto-rebasing, no
dependency ordering, no budgets, no landing: the operator merges each branch by
hand, exactly as today.

## Scope

In scope:

- A new `fanout` subcommand in the CLI, following the existing two-phase parser
  pattern (pipeline discovered first, loader-contributed flags added second).
- Repeated loader flags (`--plan a --plan b`), not comma-separated lists.
- Mixed doc sources in one invocation (`--plan` plus `--aidlc-unit` plus any
  custom loader flag).
- Worktree and branch creation per entry (real `git worktree add`).
- Child process spawning with tagged output streaming and viz-URL capture.
- A final status table and well-defined exit codes.
- `fanout --dry-run` that prints the computed plan without touching git or
  spawning anything.
- DocumentLoader interface additions (multi-value flag registration and a
  fanout entry factory), implemented for the built-in `plan` loader and the
  bundled `ai-dlc` loader.
- `src/agentic_dev_loop/__main__.py` so fanout children can be spawned with
  `python -m agentic_dev_loop`.
- The shared-state registry race fix (caveat 1), landed inside this plan
  (operator decision, resolved question 1).

Out of scope (explicit, do not re-litigate):

- Any rebase, merge, push, or landing automation. Commits land on each
  worktree's branch via the existing commit action; getting them to the base
  branch is the operator's manual step.
- Dependency ordering between entries. All entries launch immediately.
- Retry budgets, conflict recovery, driver state persistence, landing
  controllers: that is unit-8's job, and this plan deliberately does not
  pre-build any of it.
- `--resume` inside fanout. A failed entry is re-run with the existing
  single-run CLI from inside its worktree (`agentic-dev-loop --resume <id>`).
- Worktree cleanup. `git worktree remove` is the operator's tool.
- Concurrency caps or rate-limit awareness. Launch all, as the operator asked.

## Verified facts (the evidence base)

- The commit action runs in the invocation cwd and commits to whatever branch
  that worktree has checked out; nothing in the engine pushes
  (`engine/actions.py:223`, agents are banned from `git push` in
  `engine/forbidden.py`). A fresh worktree is clean at start, so
  `commit_allowed` stays enabled (`engine/graph.py:240`).
- The default run-state dir is `~/.agentic-dev-loop/runs/<hash>/` where the
  hash is the sha256 of the resolved absolute pipeline file path
  (`core/config.py:924-931`). Two worktrees live at two paths, so each gets
  its own checkpoint DB, thread-id space, and last-error.md. A relative
  explicit `state_dir` also resolves against the pipeline's own directory
  (`engine/graph.py:165-167`).
- Doc-source selection for a single run already exists via loader-contributed
  flags: `--plan` is the built-in loader's flag (`engine/loaders.py:66-68`),
  `--aidlc-unit` comes from the bundled ai-dlc extension
  (`engine/extensions/aidlc.py:128-134`), and custom loaders contribute their
  own flags through `DocumentLoader.add_cli_args`. The selection flow
  (`select_loader`, `engine/loaders.py:81-108`) enforces exactly one loader
  per single-run invocation; fanout does not use that flow.
- The two-phase CLI discovers PIPELINE first, then builds the full parser with
  `loader.add_cli_args(parser)` for every loader in the pipeline's registry
  (`engine/cli.py:435-441`, `main` dispatch at 444-476).
- The console script is `agentic-dev-loop = agentic_dev_loop.engine.cli:main`
  (pyproject.toml); there is no `__main__.py` today.
- Each run prints `Live graph viz: http://127.0.0.1:<port>` with a port-sweep
  fallback when the preferred port is busy (`engine/graph.py:297-301`).
- After a finished run, `main` blocks on ctrl+c while serving viz
  (`engine/cli.py:522-527`, `keep_alive` parameter). Fanout children must not
  do this; see the child process model below.
- The viz listener sweep is multi-instance safe by design: killing is
  cwd-attributed and positive-only, and the code comment states a
  multi-instance setup never kills another run's server
  (`engine/actions.py:95-97`).
- The container reaper is owner-aware: it only reaps entries whose owning CLI
  pid is dead or start-time-mismatched, and container/network names carry a
  32-hex per-run id prefix (`engine/isolation.py:84-86`, 1654-1708). The
  registry write, however, is a read-modify-write via `os.replace`
  (`engine/isolation.py:1587-1597`) executed at every run startup
  (`engine/isolation.py:1822`) against the shared isolation state dir
  (default `~/.agentic-dev-loop/state`, `core/config.py:266`). Two concurrent
  startups can lose each other's appended entries. This is the one engine
  defect fanout's concurrency makes worth fixing; see Prerequisites.
- `DocumentLoader` is a Protocol (`engine/registry.py:24-36`); the registry
  maps contributed option strings and argparse dests back to loader names
  (`cli_option_owners`, `cli_dests`), and probes contributed options with a
  throwaway parser at registration time (`_contributed_options`,
  `engine/registry.py:44-51`).
- The next available planning-doc number is 067 (last is 066).

## Design

### Command shape

```
agentic-dev-loop fanout [PIPELINE] --plan A --plan B --aidlc-unit unit-8
                        [--base-ref REF] [--worktree-root DIR] [--dry-run]
                        [--max-attempts N] [--env FILE] [--no-viz]
```

PIPELINE is optional and defaults to `agentic-dev-loop/pipeline.yaml` exactly
as the single-run path does. All doc-source flags are repeatable; every value
is one entry. At least two entries are required across the doc-source flags: a
single entry is a usage error (exit 2) pointing at the plain single-run path,
and an empty fan-out is also a usage error (exit 2, no bare-run autodetect in
fanout).

The single-run path is untouched: `agentic-dev-loop PIPELINE --plan X` keeps
its current behavior, current parser, and current keep-alive semantics. Fanout
is additive.

### Entries and the loader interface

Two additions to the `DocumentLoader` protocol:

1. `add_cli_args(self, parser, *, multi=False)`: when `multi=True`, each
   loader flag registers with `action="append"` so repeated flags collect
   every value. Keyword-only so existing single-arg implementations keep
   working for the single-run path.
2. `fanout_entries(self, args) -> list[FanoutEntry] | None`: builds the
   entries from the parsed values. `None` (or a missing method) means the
   loader is not fanout-capable.

`FanoutEntry` lives in the new `engine/fanout.py` module:

- `slug: str` - the git-safe worktree/branch name segment.
- `label: str` - the human label for the status table (e.g. `plan foo.md`,
  `unit unit-8`).
- `invocation: tuple[str, ...]` - the single-entry argv fragment for a child,
  e.g. `("--plan", "/abs/path/foo.md")`.

Built-in implementations:

- `PlanDocumentLoader`: `multi=True` registers `--plan` with
  `action="append"`, `type=Path`. `fanout_entries` yields one entry per path;
  slug is the sanitized file stem; label is `plan <name>`; invocation is
  `("--plan", str(path.resolve()))`. The doc is read host-side by the child,
  so it may live anywhere, including the main checkout.
- `AidlcDocumentLoader`: `multi=True` registers `--aidlc-unit` with
  `action="append"`. `fanout_entries` yields one entry per unit slug; the
  slug is the unit directory name verbatim, sanitized only for git-branch
  validity; label is `unit <slug>`; invocation is `("--aidlc-unit", slug)`.
  The child resolves unit docs from inside its worktree, where aidlc-docs is
  present from the base commit.
- A `default_slug(value)` helper in `engine/fanout.py` sanitizes any string:
  runs of characters outside `[A-Za-z0-9._-]` become a single `-`, leading
  and trailing `.-` are stripped; an empty result is a loud error. Custom
  loaders may use it or define their own scheme.

Fanout-capability is loud, never silent:

- The fanout parser registers each loader's flags with `multi=True`. A loader
  whose `add_cli_args` does not accept the kwarg raises `TypeError`; the
  fanout path catches that, registers nothing for that loader, and remembers
  it as non-capable. If the invocation then passes any option owned by a
  non-capable loader (checked against `registry.cli_option_owners`), fanout
  fails before any worktree exists: `document loader 'X' does not support
  fanout`.
- A loader with `multi=True` registration but no `fanout_entries` gets the
  same loud refusal when any of its flags are present.

Ordering of entries: loaders in registry order (built-in `plan` first, then
extensions in pipeline order), values in argv order within a loader. This is
deterministic and documented; order has no semantic effect since all entries
launch immediately and independently.

Duplicate slugs across any two entries (two plans with the same stem, a plan
named like a unit, two sanitizations colliding) fail fast before any worktree
is created, naming both entries.

### Worktrees and branches

The fanout command runs from the main checkout, which must be a git repository
(`git rev-parse --show-toplevel`; a non-repo cwd is a loud error).

Per entry:

- Branch: `loop/fanout/<slug>`, created from a base ref resolved ONCE at
  fanout start (`--base-ref`, default HEAD of the main checkout). One shared
  base means all worktrees fork the same commit even if the main checkout
  moves mid-launch.
- Worktree directory: `<worktree-root>/fanout-<slug>`, where
  `--worktree-root` defaults to the repo root's parent directory (i.e. a
  `../fanout-<slug>` sibling of the repo).
- Creation is `git worktree add <worktree-root>/fanout-<slug> -b loop/fanout/<slug>
  <base-commit>` from the main checkout. An existing branch or worktree path
  fails that entry loudly; other entries continue.

Two phases, so a creation failure never strands a running child:

1. Create all worktrees. Per-entry failures are loud and skip that entry.
2. Spawn children for the successfully created entries only.

Pipeline re-rooting: the child must run the WORKTREE's copy of the pipeline
file, not the main checkout's. Otherwise `base` resolves to the main checkout
(`engine/graph.py:147`), every child would share the same run-state dir hash
(`core/config.py:924-931`), and additional_files/agents_dir would resolve
outside the worktree. The fanout planner computes the pipeline path relative
to the repo root and re-roots it into the worktree. The plan requires the
pipeline file to be tracked in the base commit; if it is absent after
checkout, that entry fails loudly (an untracked pipeline file is a
documented limitation, not a silent fallback).

### Child process model

- Child argv: `[sys.executable, "-m", "agentic_dev_loop",
  <worktree-pipeline>, *entry.invocation, *shared_flags]`, `cwd=<worktree>`.
  `shared_flags` forwards `--max-attempts`, `--env`, and `--no-viz` when the
  operator passed them. `--resume`, `--dry-run`, and the fanout's own flags
  are never forwarded.
- Child env inherits the parent's (auth tokens, LLM keys). No keep-alive
  suppression: children run with the engine's default behavior, which keeps
  viz alive after both success AND failure (cli.py:519-528 blocks on ctrl+c
  regardless of outcome, then returns 0 or 1). Resolved question 3.
- `src/agentic_dev_loop/__main__.py` (new, two lines) makes
  `python -m agentic_dev_loop` work:
  `from agentic_dev_loop.engine.cli import main` /
  `raise SystemExit(main())`.
- Completion detection is line-based, not exit-code-based. A child that has
  printed `Pipeline finished: press ctrl+c to exit` (stdout) is terminal and
  idle at its keep-alive; PASSED unless the stream also contains
  `agentic-dev-loop: pipeline failed - exiting with code 1` (stderr, merged
  into the same stream), in which case FAILED. Children spawned with
  `--no-viz` skip the keep-alive block and exit on their own: process death
  without a terminal marker counts as PASSED on exit 0, ABORTED otherwise.
  The two marker strings are stable CLI lines and get their own matcher
  tests.
- Consequence, recorded as a decision: a child's live graph viz stays up
  from run start until the operator stops fanout, including for failed runs,
  matching the single-run UX. The parent itself blocks on ctrl+c once all
  children are terminal, so every viz stays open for inspection while other
  runs continue and after everything finishes.

### Streaming, URLs, status

- Each child runs with `stdout` and `stderr` merged; one reader thread per
  child line-streams into the parent's stdout with a `[<slug>]` prefix.
- The parent matches `Live graph viz: (http://\S+)` in each child's stream,
  records the URL per entry, and prints it immediately. Port collisions
  between children are handled by the existing per-run sweep
  (`engine/graph.py:297-301`); the parent does not assign ports in v1.
- When all children are terminal (finished line seen or process dead), the
  parent prints the summary table and then blocks on ctrl+c itself, keeping
  the fanout process (and therefore every child's keep-alive) running until
  the operator stops it:

  ```
  slug       branch                worktree                 viz URL             outcome
  ---------  --------------------  -----------------------  ------------------  -------
  unit-8     loop/fanout/unit-8    ../fanout/unit-8         http://127.0.0.1:X  PASSED
  foo        loop/fanout/foo       ../fanout/foo            http://127.0.0.1:Y  FAILED
  ```

  Outcomes: PASSED (terminal, no failure line), FAILED (failure line seen),
  ABORTED (process died without a terminal marker and nonzero exit), SKIPPED
  (worktree creation failed). Rendering is a pure function.

### Exit codes and signals

- Exit 0: every entry ran and passed, reported when the operator stops fanout.
- Exit 1: any entry was skipped at creation, failed, or aborted, reported when
  the operator stops fanout.
- Exit 2: usage errors (fewer than two entries, unknown flag, non-capable
  loader flag, duplicate slug, non-repo cwd, missing pipeline file).
- SIGTERM to the parent: forward SIGTERM to live children (each child's
  existing handler raises SystemExit(143) so its graph `finally` closes the
  sandbox), wait a short grace period, exit 143.
- Ctrl+C: children live in the same process group, so the terminal's SIGINT
  reaches them directly. A child still running unwinds through the graph's
  `finally`; a child already idle at its keep-alive catches the
  KeyboardInterrupt (cli.py:524-527) and returns its 0/1 outcome. The parent
  prints the summary table before blocking, catches its own KeyboardInterrupt,
  and exits 0 or 1 according to the outcomes. Any stragglers are covered by
  the owner-pid startup reaper on the next run.

### Dry run

`fanout --dry-run` prints the computed plan and exits 0 without touching git
or spawning: per entry, the slug, branch, worktree path, child argv, and
label, plus the resolved base commit. This is the primary testable seam of
the planner.

## Decisions

1. Fanout is a CLI subcommand, not a script and not a flag on the single-run
   path. The single-run flow is untouched.
2. Repeated flags, not comma-separated lists. Mixed loaders in one invocation
   are supported; each loader's values become independent entries.
3. Fanout requires at least two entries; a single-entry invocation is a usage
   error (exit 2) pointing at the plain single-run path (operator decision).
   Every supported entry gets exactly one worktree: clean branches without
   polluting the main checkout.
4. No auto-rebasing, no dependency ordering, no budgets, no landing, no
   resume, no cleanup command. The operator merges by hand; unit-8 owns
   everything beyond launch-and-monitor.
5. Children keep serving viz after their run finishes, for success and
   failure alike, until the operator stops fanout (resolved question 3). The
   parent detects completion by matching the two stable terminal lines rather
   than exit codes; children spawned with --no-viz still exit on their own
   and are handled by the dead-process fallback.
6. Branch namespace `loop/fanout/<slug>`, worktrees at
   `<repo-root>/../fanout-<slug>` (a sibling of the repo root). Both
   overridable (`--base-ref`, `--worktree-root`). Amended after the first
   implementation: the worktree moved from `<repo-root>/../fanout/<slug>` to
   `<repo-root>/../fanout-<slug>` so the pipeline dir sits at the same depth
   as in the main checkout; pipeline-relative tooling paths (`agents_dir`,
   `additional_files` entries like `../../docs/...`) then resolve identically
   in single runs and fanout children.
7. All worktrees are created before any child spawns.
8. Fanout-capability of a loader is opt-in and failures are loud, never a
   silent last-wins or a dropped value.
9. Fanout is a separate subcommand, not an implicit mode of the pipeline
   path. Justification (resolved question 4): the semantics differ so much
   that implicit switching on input count is a footgun. Single-run flags are
   single-valued by design (select_loader enforces exactly one loader;
   mark_plan_completed stamps exactly one path; args.plan is one Path), and
   mixing fanout's multi-value semantics into the main parser would
   contaminate the battle-tested single-run path. Fanout also mutates the
   repo (creates branches and worktrees) and spawns N processes, where a
   single run runs one pipeline in place: the same argv meaning two
   different execution models depending on how many flags happen to be
   present is exactly the kind of surprise this codebase bans. Internally
   the dispatch stays clean: argv[0] == "fanout" routes to run_fanout, which
   is the delegation point the operator asked for, matching the existing
   subcommand dispatch in main() (help, report, containers, doctor, init,
   and the rest, cli.py:449-476). Fanout-only flags (--base-ref,
   --worktree-root, --dry-run) get their own help scope instead of
   polluting the main --help with flags that are meaningless for a single
   run. Note: with single-entry fanout unsupported (Decision 3), a
   count-based switch would now be expressible, but the subcommand still
   wins on the remaining grounds: semantic surprise for a repo-mutating
   operation, single-valued single-run parsing, and an honest --help scope.

## In-scope fix: shared-state registry race (caveat 1)

Concurrent fanout runs hit a real defect: the reaper's registry write is a
read-modify-write via `os.replace` (`engine/isolation.py:1587-1597`, executed
at every startup, 1822) against the shared `~/.agentic-dev-loop/state`
dir. Two overlapping reaper windows can lose an entry appended by the other
process. Worst case: an orphaned container that cannot be reaped after a
crash. Not a live-run failure, but a real correctness hole that fanout's
concurrency turns from theoretical into routine. Resolved question 1: the
fix lands inside 067, not as a separate plan.

Fix (A, minimal): an exclusive `fcntl.flock` on the registry file around the
reaper's read + filter + write, and around `_append_registry_record` so
appends cannot interleave with the replace. The audit JSONL gets the same
treatment for its 5MB rotation (`engine/isolation.py:1515-1517`). Alternative
(B): make the registry append-only with tombstone records and filter at read
time, which removes the replace entirely but touches more call sites. A is
the default unless implementation shows it is insufficient.

## Relationship to unit-8

This command is deliberately NOT the unit-8 driver: no manifest, no landing
controller, no budgets, no dependency graph. It is the CLI-native convenience
layer that reproduces the operator's current multi-copy workflow with
worktrees. When unit-8 lands, the driver can either shell out to this command
or supersede it; nothing here blocks that. The unit-8 steering decision
(parallelism lives around the engine, per-run guarantees unchanged) is
preserved: every child is a stock single-run pipeline invocation.

## Testing (TDD, no mocks except network boundaries)

- Pure planner tests: entry collection and ordering across mixed loaders;
  slug derivation, sanitization, collision failures; branch/worktree path
  planning; pipeline re-rooting; base-ref resolution.
- Pure table tests: summary rendering for all outcome combinations; exit-code
  aggregation.
- Streaming tests: URL regex extraction against recorded lines (real I/O over
  a pipe or io stream, not mocks); prefixing.
- Loader tests: `multi=True` registration collects repeated values for both
  `plan` and `ai-dlc`; `fanout_entries` builds correct invocations; extend
  `tests/test_aidlc.py` and the loader tests in `tests/test_cli.py`.
- CLI tests: usage errors (fewer than two entries, one entry, unknown flag,
  non-capable loader flag, duplicate slug, non-repo cwd); `--dry-run` output;
  terminal-line matchers (both markers, success and failure streams,
  dead-process fallback for --no-viz children); `__main__.py` exit-code
  passthrough.
- Integration: real `git worktree add` in a temp repo (branch names,
  existing-branch failure, worktree-root creation); smoke test for
  `fanout --dry-run` end to end.
- Registry-race fix (in scope): a test proving concurrent reaper/append
  interleavings never lose entries (two processes against one temp state
  dir).

## Open questions

All resolved by the operator and folded into the doc:

- Question 1: caveat-1 fix lands inside 067 (In-scope fix section).
- Question 2: branch/worktree naming accepted (Decision 6).
- Question 3: children keep serving viz after completion until the operator
  stops fanout (Decision 5, Child process model section).
- Question 4: single-entry fanout is unsupported; a one-entry invocation is
  a usage error (Decision 3). The subcommand-vs-main justification is
  Decision 9.
