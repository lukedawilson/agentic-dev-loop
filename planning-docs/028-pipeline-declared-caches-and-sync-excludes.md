# 028 - Pipeline-declared caches and sync-back excludes

> Follow-up to the 1.0.0a18 migration analysis. Status: plan (not implemented).

## Context

The a18 migration analysis surfaced two design objections against the unit-5
implementation:

1. **Hardcoded cache-dir whitelist is the wrong mechanism.** `CACHE_DIRS` in
   `src/agentic_dev_loop/engine/presets.py:16-23` hardcodes toolchain cache
   locations (`.venv-sandbox`, `node_modules`, `qa/node_modules`,
   `qa/.browsers`, `.nuget-packages`, `target`). A pipeline using a toolchain
   whose cache dir is not on the list (or a differently located one) requires
   an engine change. The author, not the engine, should own this list, and it
   should live in `pipeline.yaml`.

2. **bin/obj pollution via sync-back.** BR-4.2 copies declared-rw export
   changes back to the real repo; the only exclusions are `.git`, `.venv`,
   host-only dirs, and cache paths (`sync_back.py:23-24`). Machine-generated
   dirs (`src/**/obj`, `src/**/bin`) therefore land back in the real repo,
   plus three new costs versus the old shared mount: full-tree sha256
   snapshot hashing per rw step, the BR-4.4 conflict failure (AE-SBX-016)
   when the host repo's build artifacts drift underneath a run, and the
   export copy itself (obj/bin can be multi-GB and the export is
   materialised fresh every run). The fix is an author-declared exclusion
   list with scratch semantics: excluded paths are neither copied into the
   export nor synced back.

### Decisions (user, 2026-10-25)

| Fork | Decision |
| --- | --- |
| Fate of `CACHE_DIRS` | Remove entirely. Engine ships no toolchain cache dirs. |
| Missing declared cache at run start | Auto-create host-side (`mkdir -p`), then bind-mount. Amends BR-3.2. |
| Granularity | Top-level run-wide for both `caches:` and `sync_excludes:`. |
| Export copy of excluded paths | Skip: excludes have scratch semantics (neither copied into the export nor synced back); amendment 2026-10-25. |

## Current state (evidence)

- `CACHE_DIRS` is consumed only by `discover_cache_paths`
  (`isolation.py:233-245`), which filters to dirs that exist in the workspace
  at run start, then `startup_sandbox` (`isolation.py:1297-1302`) feeds the
  list to `StepWorkspace.materialise`.
- `plan_step_workspace` already raises on a missing declared cache
  (`step_workspace.py:62-66`) - dead code today because discovery filters
  absent dirs out first. This is the "silent not-a-cache" footgun: on a fresh
  checkout a missing `.nuget-packages` is not a cache, so dotnet creates it
  inside the export and sync-back copies the whole NuGet cache into the repo.
- Cache bind-mounts attach to every profile container regardless of the
  step's `mounts:` (`docker_create_payload`, `isolation.py:621-631`).
- Sync-back exclusion vocabulary today: `_FORBIDDEN = {".git", ".venv"}`
  (`sync_back.py:24`), cache paths via `cache_paths` params through
  `_declared` / `_snapshot` / `plan_sync_back` / `sync_back`, and the
  baselines snapshotted in `step_workspace.py:133-134` and
  `isolation.py:890-892, 1046, 1052-1053`.
- Config schema: `PipelineConfig` (`config.py:380-405`); path validation
  reusable from `_validate_mount_path` (`config.py:26-51`).
- Rules that change: BR-3.1, BR-3.2, BR-9.3, BR-11.1; design stance D-2 and
  TS-5 flip from "cache declarations are engine preset data" to "pipeline
  author data".

## Proposed config surface

```yaml
# top-level on PipelineConfig; both default to [] (absent = today minus the
# engine list)
caches: [.nuget-packages, qa/node_modules, qa/.browsers]

# top-level run-wide sync-back excludes
sync_excludes: ["**/bin/**", "**/obj/**"]
```

### `caches:` semantics

- Repo-relative paths; validated like mount paths: no absolute, no `..`, no
  `.git` component, no host-only dirs (`.venv`), no path equal to or under a
  relative `state_dir` (mirror `_mounts_avoid_state_dir`, `config.py:447-465`).
- Each entry is a rw bind-mount `{real-workspace}/<cache> -> {export}/<cache>`
  (mechanism unchanged, BR-3.1).
- **Missing at run start: engine `mkdir -p`'s the dir host-side** (parents
  included, `exist_ok=True`), before export planning, so the dir is uid-owned
  (the AE-SBX-009 root-owned-dir class) and the pre-existing loud check in
  `plan_step_workspace` keeps its guard role for races only. Precedent: the
  022 `sandbox_venv_dir` host-side pre-create (`isolation.py:791-794`). The
  mutation is an author-declared empty directory, outside the sandbox.
- **The sandbox venv stays engine infrastructure, not author data.** When
  `isolation.sandbox_venv_dir` is unset, the engine retains implicit cache
  treatment for `.venv-sandbox` (the engine's own dir; 016/022 behavior
  unchanged). Declaring `.venv-sandbox` in `caches:` while
  `sandbox_venv_dir` is set is a contradiction and fails at load.
- Mount interplay: a step `mounts:` entry equal to or under a declared cache
  path is rejected at load - the rw cache submount would silently defeat a
  declared `ro` mount there (BR-1.3 extension). Mounting `.` rw (the common
  case) remains fine; the cache submount layers on top.
- `discover_cache_paths` is removed; `startup_sandbox` reads `cfg.caches`
  (plus the implicit venv case) and auto-creates missing dirs before
  `materialise`. Nothing else changes: cache paths still flow to
  `StepWorkspace` (export copy exclusion + bind-mounts) and to
  `DockerBackend._cache_paths` (sync-back exclusion), and still attach to
  every profile container.

### `sync_excludes:` semantics

- Top-level `list[str]`, default `[]`. Each entry is a repo-relative spec:
  - **No wildcard** -> component-prefix exclusion (the dir and everything
    under it), same matching as `_within_cache` today. Example:
    `qa/.artifacts`.
  - **With wildcard** -> `fnmatch` on the full posix relpath. Document that
    `*` spans path separators (fnmatch string semantics), so `**/bin/**`
    matches a literal `/bin/` segment at any depth and `*.dll` matches at any
    depth. Examples: `**/bin/**`, `**/obj/**`.
- Validation: same load-time checks as caches, applied to the entry's
  literal segments (wildcard segments are skipped): relative, no `..`, no
  `.git`, no host-only dir.
- Effect: excluded paths are skipped in both snapshots (`_snapshot`) and
  never classify as changes (`_declared`) - no copy-back, no deletion
  mirroring, and no BR-4.4 conflicts from them. Combined with the copy skip
  below, this fixes the pollution and all three run-time costs at once.
- Scratch semantics: excluded paths are ALSO skipped at export
  materialisation (`plan_step_workspace`), like cache paths but WITHOUT a
  bind-mount - caches get a rw window from the real workspace, scratch paths
  get nothing (neither copied nor mounted). This turns the export into a
  faithful-minus-scratch copy and removes the multi-GB copy cost per run.
- Consequence (documented, not hidden): a step cannot read pre-existing
  excluded content, because it is never in the export. For obj/bin this
  means in-sandbox builds are clean builds rather than incremental; the
  reusable part (restore inputs) belongs in a declared `caches:` entry,
  which is exactly how `.nuget-packages` already behaves. Authors declaring
  excludes accept this contract.
- Baselines must use the same excludes: `StepWorkspace.materialise`
  (`step_workspace.py:133-134`) and `DockerBackend` (`isolation.py:890-892,
  1046, 1052-1053`) thread `sync_excludes` into every `_snapshot` call.
- Artifact shadowing: if a step's `artifact:` or `produces:` path matches an
  exclude, that artifact would never sync back. Load-time warning (non-fatal)
  naming the step and path; wildcard overlap is fuzzy, so a hard failure
  would false-positive. Documented as a footgun in docs/setup.md.

## Engine changes (file by file)

1. `src/agentic_dev_loop/core/config.py`
   - `PipelineConfig`: add `caches: list[str] = []` and
     `sync_excludes: list[str] = []`; coerce single strings to lists
     (`mode="before"`, like `lockfiles`).
   - New `_validate_declared_path` (shared by both; `allow_wildcards` flag)
     reusing `_validate_mount_path` logic.
   - Cross-validation model validators: cache path equal to/under a relative
     `state_dir` fails; step mount path equal to/under a cache path fails;
     an exclude entry equal to/under a declared cache fails (redundant -
     caches already behave as scratch); `.venv-sandbox` in `caches:` with
     `isolation.sandbox_venv_dir` set fails; `produces`/`artifact` under an
     exclude warns.
2. `src/agentic_dev_loop/engine/presets.py`
   - Delete `CACHE_DIRS`; keep `EGRESS_PRESETS` and `expand_presets`. Update
     the module docstring: D-2 no longer covers caches; cache declarations
     are pipeline data.
3. `src/agentic_dev_loop/engine/isolation.py`
   - Remove `discover_cache_paths` and its import. `startup_sandbox` builds
     the cache list from `cfg.caches` (+ implicit `.venv-sandbox` when
     `sandbox_venv_dir` is None) and auto-creates missing dirs host-side
     before materialisation.
   - Thread `sync_excludes` into the baseline snapshot and `_sync_back`
     (`isolation.py:890-892, 1046, 1052-1053`).
4. `src/agentic_dev_loop/engine/step_workspace.py`
   - Keep the missing-cache loud check (`:62-66`) as a race guard only
     (pre-creation makes it unreachable in normal operation).
   - `plan_step_workspace` / `_excluded` gain an `excludes` parameter:
     excluded paths are skipped at export copy time exactly like cache paths,
     but with no `CacheMount` emitted (caches get the rw window; scratch
     paths get nothing). A shared wildcard matcher with `sync_back.py` keeps
     the literal-prefix + fnmatch semantics in one place.
   - Baseline snapshot takes `sync_excludes` (needed for the real-repo side
     in `sync_back`; the export side never contains excluded paths).
5. `src/agentic_dev_loop/engine/sync_back.py`
   - Add an `excludes` parameter alongside `cache_paths` to `_declared`,
     `_snapshot`, `plan_sync_back`, and `sync_back`. Matching: literal
     entries via parts-prefix (existing `_within_cache` machinery
     generalised), wildcard entries via `fnmatch.fnmatchcase` on the posix
     relpath. `cache_paths` keeps its separate role: excluded from the export
     copy AND bind-mounted rw from the real workspace; excludes are
     copy-skipped with no mount.
6. Tests (TDD red-first per repo policy)
   - Config: parsing/coercion; each rejection case loud (traversal, absolute,
     `.git`, host-only, state_dir collision, mount-under-cache,
     relocated-venv contradiction); exclude warning case.
   - sync_back: literal and wildcard excludes across all four buckets
     (added/modified/deleted/conflict); deep nesting for `**/bin/**`;
     `*`-spans-separators behavior; baseline interplay; PBT determinism with
     excludes.
   - step_workspace: declared-missing cache no longer raises (pre-created);
     auto-created dir appears in the mount plan.
   - isolation: `cfg.caches` wiring into `startup_sandbox`; sandbox-venv
     contradiction at load; no references to `CACHE_DIRS`/`discover_cache_paths`
     remain (grep-checked: today only `test_unit5_pure.py:186,194` and
     `test_sync_back.py:116` use the compatible `cache_paths` params).
7. Docs
   - `README.md` + `docs/setup.md`: `caches:` and `sync_excludes:` sections,
     wildcard semantics, the artifact-shadowing footgun, migration note.
   - `CHANGELOG.md` (Unreleased): Added (both fields, auto-create), Changed
     (BR-3.2 amended), Breaking (removed engine cache whitelist - pipelines
     relying on it must declare `caches:`; dogfood pipeline already relocates
     its venv so it declares nothing).
   - aidlc artifacts: `business-rules.md` BR-3.1/BR-3.2/BR-9.3/BR-11.1 and a
     new sync-exclude rule; `tech-stack-decisions.md` TS-5 and D-2 wording;
     `business-logic-model.md` KD table entries; `domain-entities.md` if the
     FD trace mentions cache discovery. `audit.md` is historical - untouched.
8. Migration
   - Dogfood `agentic-dev-loop/pipeline.yaml`: unchanged (venv relocated via
     `isolation.sandbox_venv_dir`, no toolchain caches).
   - The a18 consumer pipeline: add
     `caches: [.nuget-packages, qa/node_modules, qa/.browsers]` and
     `sync_excludes: ["**/bin/**", "**/obj/**"]`; the `NUGET_PACKAGES:
     .nuget-packages` step env stays (the engine does not inject toolchain
     env; dotnet resolves the relative value against the export cwd).

## Rules amended

- BR-3.1: cache paths are author-declared in pipeline.yaml (`caches:`); the
  engine ships no toolchain cache dirs. The sandbox venv remains engine
  infrastructure.
- BR-3.2 (amended): a missing declared cache dir is auto-created host-side
  (empty, uid-owned) and bind-mounted; it is no longer a loud failure.
- BR-9.3: per-toolchain caches are pipeline declarations, not engine preset
  data; adding a toolchain = a `caches:` entry in pipeline.yaml (plus egress
  preset data where needed), no engine change.
- BR-11.1: drop "missing cache path" from the loud-failure list.
- New: sync-back excludes - declared `sync_excludes:` paths are scratch:
  never copied into the export and never synced (copy, delete, or conflict);
  they are excluded from both snapshots; entries are validated at load.

## Acceptance criteria

1. `load_config` accepts `caches:` + `sync_excludes:`; every invalid entry
   class fails loud at load (enumerated above).
2. Fresh checkout with declared-but-missing caches runs end to end: dirs are
   auto-created host-side, bind-mounted rw, and their contents never sync
   back (whole-cache copy-back footgun gone).
3. With `sync_excludes: ["**/bin/**", "**/obj/**"]`, a run whose steps write
   obj/bin produces zero obj/bin changes in the real repo and zero obj/bin
   content in the export (copy skipped at materialisation); host-side obj/bin
   drift no longer raises AE-SBX-016; `dotnet clean` no longer mirrors
   deletions.
4. Dogfood pipeline loads and runs with no config changes.
5. `CACHE_DIRS` and `discover_cache_paths` are gone; `presets.py` ships
   egress data only.
6. All new pure logic is PBT/unit-covered per BR-12.1; TDD red-green-refactor
   throughout.

## Future work (explicitly out of scope)

- Per-step granularity for `caches:`/`sync_excludes:` if a pipeline ever
  needs it (cache mounts are container-global today, so per-step caches
  would change profile keying).
- gitignore-aware sync-back (a larger change; the exclude list covers the
  known cases).
