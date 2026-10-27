# 029: Sync-back conflict absorb, announce, and merge_on_conflict

> **Status: COMPLETED**

## Context

On 2026-10-27 the consumer-repo unit-10 run (1db54eed) passed its restore step for the
first time (the a22 egress + NUGET_PACKAGES pipeline fixes work), then the engine
raised AE-SBX-016 because both the loop's dev agent and a concurrent host session
edited the same ten aidlc/planning docs. Everything after the raise was broken:

1. The baseline is never re-snapshotted after a conflict raise, so the divergence
   is permanent: every subsequent sync-back re-raises, bricking even `echo hello`.
   The dev agent had no recovery path and the run died with zero unit-10 work.
2. The operator console said nothing. The conflict surfaced only inside the agent
   transcript, after the wedge.
3. There is no support for concurrent edits on designated files. The aidlc audit
   log (`aidlc-docs/audit.md`) is append-only and is edited by both the loop
   (the vendored AI-DLC workflow mandates audit.md logging) and the user.

The BR-4.4 guard itself worked as designed: it must keep protecting concurrent
host edits. This plan fixes what comes after the guard fires, and adds a minimal
concurrent-edit path for append-style logs.

## Decisions (user, 2026-10-27)

- Fix 1 + fix 2 are "correct and necessary, but insufficient": re-snapshot the
  baseline after a conflict raise, and print the conflict on the console.
- Add concurrent-edit support for specific files. "Since ai-dlc is first-class
  in this repo, a minimal step would be to not trip on the audit log."
- Mechanism = option B: a top-level `merge_on_conflict:` config list, not an
  engine hardcode of the audit path.
- The three fixes go into this plan and are dogfooded by the agentic-dev-loop.
- "Make sure this is all documented in README.md" (user requirement).

## Current state (evidence)

- `isolation.py:1075` `DockerBackend._sync_back` raises AE-SBX-016 at line 1085.
- `isolation.py:1016` `_run_profile` calls `_sync_back` after every exec when
  `mount_set.rw_paths()` is non-empty.
- Baselines: `_snapshot` at `create_execution_container`, and re-snapshot after
  a successful `_sync_back`. No re-snapshot on the raise path.
- `isolation.py:1313` `_announce(msg)` (a23): module-tagged console print with
  flush. The console is product surface; no logging library in src.
- `sync_back.py:88` `plan_sync_back`, `:124` `_snapshot`, `:155` `sync_back`.
- `config.py:57` `_validate_declared_path(path, allow_wildcards)` is the shared
  validator for `caches:`/`sync_excludes:`; fields at `config.py:447-448`;
  field validators `:463-475`; model validators `:525-610` (state-dir avoidance,
  mounts-vs-caches, excludes-vs-caches redundancy, artifact shadow warnings).
- `core/paths.py` already provides `relative_parts` and `path_excluded`
  (literal prefix + fnmatch wildcards, `*` spans separators).
- Dogfood pipeline (this repo) declares rw "." on dev/restore/test, ro "." on
  review/qa; the loop edits aidlc-docs during its own runs.

## Proposed config surface

Top-level `merge_on_conflict: list[str] = []`, a sibling of `caches:` and
`sync_excludes:`. Single strings coerce to a one-item list (mode=before, same as
lockfiles and sync_excludes). Entries are repo-relative paths matched with the
same vocabulary as `sync_excludes:` (literal = component prefix; wildcard =
fnmatch on the full posix relpath; `*` spans separators).

Validation (reuses `_validate_declared_path`, `allow_wildcards=True`):

- reject traversal (`..`), absolute paths, `.git`, host-only mount dirs;
- reject a literal entry that is redundant: under a `sync_excludes:` literal or
  under a `caches:` literal (excluded and cached paths never sync, so they can
  never conflict). Wildcard entries skip these checks (028 parity).

`merge_on_conflict:` paths are append-style logs. Declaring a non-append file is
the pipeline author's responsibility; the docs say so.

## Semantics

For a conflict on a path that matches `merge_on_conflict:`:

- Union merge, three-way with the baseline as ancestor: the merged text is the
  host file plus the export-only lines appended in export order. Nothing is
  ever deleted by a merge (host wins on deletions; export wins on additions).
- The merged text is written to BOTH the host and the export (the sides
  converge), the baseline is re-snapshotted, and the run continues. No raise.
- Console: `[isolation] merged concurrent edits: <path> (<n> sandbox lines
  appended)` via `_announce`.
- The merge is recorded in the sandbox audit trail.

For a conflict on any other path (fixes 1 + 2):

- Console: `[isolation] sync-back conflict: <paths> - the workspace changed
  underneath; edits to these paths will not sync`.
- The baseline is re-snapshotted BEFORE the raise, absorbing the divergence
  once. The conflicted exec's edits never land; the host keeps its version; the
  next exec (even `echo hello`) runs clean. Only genuinely new both-sides edits
  conflict again.
- AE-SBX-016 still raises for the conflicted exec, scoped to the non-merge
  paths.

A conflict where one side deleted a declared merge path stays in the hard set
(raise + absorb); union merge only handles both-sides-present.

## Engine changes (file by file)

### `src/agentic_dev_loop/core/config.py`

- `merge_on_conflict: list[str] = []` field next to `sync_excludes` (line 448).
- `_coerce_declared_paths` (mode=before) gains "merge_on_conflict".
- `_valid_merge_on_conflict` field validator: `_validate_declared_path(path,
  allow_wildcards=True)` per entry.
- New model validator `_merge_avoid_excludes_and_caches`: literal merge entries
  under a literal `sync_excludes:` entry or a literal `caches:` entry raise
  ("redundant: excluded/cached paths never sync and can never conflict").
  Wildcard merge entries are skipped (028 parity with `_excludes_avoid_caches`).

### `src/agentic_dev_loop/engine/sync_back.py`

- New pure function `merge_append_only(host_text: str, export_text: str) ->
  str`: difflib.SequenceMatcher opcodes; for `insert` opcodes (lines present
  only in the export) append those lines to the host text. Export order is
  preserved; host order is preserved; nothing is removed.

### `src/agentic_dev_loop/engine/isolation.py`

- `DockerBackend.__init__` gains `merge_on_conflict: list[str] | None = None`
  (mirrors `sync_excludes`), stored when `export_root` is set.
- `_sync_back` (line 1075): after the conflict set is known:
  1. partition: merge paths (matched by `path_excluded` against
     `merge_on_conflict`) vs hard paths;
  2. for each merge path: `merge_append_only(host, export)`, write both sides,
     `_announce` the merge line, append an audit record;
  3. re-snapshot the baseline (absorb, fix 1) - this is unconditional on the
     conflict path, before any raise;
  4. if hard paths remain: `_announce` the conflict line (fix 2), then raise
     AE-SBX-016 listing only the hard paths.
- `startup_sandbox` passes `merge_on_conflict=list(cfg.merge_on_conflict)` into
  `DockerBackend` (mirrors `sync_excludes` threading).

### Rules amended

- `aidlc-docs/construction/unit-5/functional-design/business-rules.md`:
  BR-4.4 amended (conflict handling: absorb + announce + raise, one-off, no
  wedge); NEW BR-4.6 (merge_on_conflict union-merge semantics, both sides
  converge, nothing deleted).
- `domain-entities.md`: PipelineConfig row gains merge_on_conflict; sync-back
  planning table notes the merge partition.

### Docs (user requirement: README.md must cover all of this)

- `README.md`: config-table row for `merge_on_conflict:`; a short paragraph in
  the Mounts/egress or Per-stage networking section: union-merge semantics,
  append-style-logs-only caveat, the console line.
- `docs/setup.md`: section on conflict handling (absorb + announce) and
  `merge_on_conflict:` with a yaml example.
- `CHANGELOG.md`: Added (merge_on_conflict + conflict absorb/announce), Changed
  (BR-4.4), and any Breaking note if AE-SBX-016 wording changes.
- `tech-stack-decisions.md`: a TS entry noting "sync-back conflicts are
  one-off operator events, not agent errors; console is the surface".

### Dogfood (this repo)

- `agentic-dev-loop/pipeline.yaml` declares
  `merge_on_conflict: [aidlc-docs/audit.md]` so concurrent host edits to the
  audit log no longer trip the dogfood loop.

## Acceptance criteria

1. Config: `merge_on_conflict:` accepts single strings and wildcards; rejects
   traversal/absolute/`.git`/host-only dirs; raises "redundant" for literal
   entries under a literal `sync_excludes:` or `caches:` entry.
2. Wedge regression: after an AE-SBX-016 raise, the next `_sync_back` with no
   new changes does not raise (baseline absorbed).
3. Console: every conflict prints one `[isolation]` line; every merge prints
   one `[isolation]` merge line.
4. Merge path: conflict on a declared path does not raise; host and export are
   identical after; baseline re-snapshotted; subsequent syncs clean.
5. Merge loses nothing: host-only lines survive, export-only lines appended in
   export order, deletions never applied; identical sides merge to a no-op.
6. TDD red-first at each layer; full suites green (`-m "not docker"` and
   `-m docker`); no-em-dash hook passes; all files ASCII.
7. README.md, docs/setup.md, CHANGELOG.md, and the aidlc artifacts document the
   three fixes (README mandatory).
8. Dogfood: the pipeline declares `merge_on_conflict: [aidlc-docs/audit.md]`
   and a dogfood run with concurrent host edits to audit.md completes without a
   wedge.

## Future work (out of scope)

- Per-path conflict policy beyond merge: host-wins, export-wins, skip+report.
- Section-aware merges for structured docs.
- An agent-side conflict-resolution tool.
- gitignore-aware sync-back.
