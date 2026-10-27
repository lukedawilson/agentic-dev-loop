---
description: Migrate a repo's pipeline.yaml to the current engine contract, using the changelog and the dogfood pipeline as the reference. Usage: /migrate-pipeline <repo-path>
argument-hint: <repo-path>
---

You run from the agent-engine repo. The target is `$1`. This file is the single
source of truth for the migration procedure. Do not skip sections.

## 1. Locate the target pipeline

If `$1` is a directory, check in this order:

- `<dir>/agentic-dev-loop/pipeline.yaml`
- `<dir>/agentic-dev-loop/pipeline.yml`
- `<dir>/pipeline.yaml`
- `<dir>/pipeline.yml`

If `$1` is a file, use it directly. If nothing exists at any of those paths,
fail with a clear message naming every path you checked. Do not guess.

## 2. Read the contract (this repo, mandatory, in this order)

1. `CHANGELOG.md` - read every Breaking and Changed entry from 1.0.0a17 to
   Unreleased. These entries define the contract the pipeline must satisfy.
2. `agentic-dev-loop/pipeline.yaml` - the dogfood pipeline. The changelog names
   it as the migration example. Treat its step shapes as the reference.
3. `README.md` (config table + Per-stage networking) and `docs/setup.md`
   (Mounts and egress section) - authoritative per-field semantics.

Build a contract checklist from these before touching the target. It includes,
at minimum:

- Every step declares `mounts:` (strict-by-default: missing mounts mounts
  nothing; steps that need the workspace declare `{path: ".", mode: rw|ro}`).
- Every step with `network: true` declares `egress:` (deny-all default; the
  engine never adds a target). Choose the narrowest form: the
  `package-registries` preset for package-manager fetches, `class: browser`
  only for browser-driven steps, explicit `targets:` otherwise.
- `caches:` and `sync_excludes:` reflect the repo's real scratch and toolchain
  dirs (028), not a stale preset list.
- No env var carries a host-absolute path or a relative path the tool rejects.
  Known trap: `NUGET_PACKAGES` must be absolute and no static absolute path
  reaches a per-run export, so remove that env entirely (NuGet's default under
  /tmp is correct). `PLAYWRIGHT_BROWSERS_PATH` may stay repo-relative.
- Host-side built-in steps (e.g. `commit`) carry no `mounts:`, `egress:`, or
  `network:`.

## 3. Read the target repo's context

- Prefer `ls` over `glob` for discovery. List the repo root and the pipeline's
  directory.
- Toolchain evidence: `*.sln` / `*.csproj`, `package.json`, `pyproject.toml`,
  `Cargo.toml`, and friends. This decides which egress presets each networked
  step needs and which cache dirs exist.
- `docker ps` and `docker compose ps` for containers that mount the repo (live
  writers). Their output dirs are sync-back hazards: propose them for
  `sync_excludes:` (a live log writer blocks every declared-rw sync-back with
  AE-SBX-016, permanently, because the baseline never re-snapshots).
- Check for a live engine run against the target (`ps aux | grep
  agentic-dev-loop`). If one is running, stop and tell the user to kill it
  first. Never edit a pipeline while its run is live (sync-back race).
- `git status --porcelain` in the target repo and note anything uncommitted.

## 4. Gap report

Print one table: contract item, target state, verdict (ok / missing /
broken), one row per step per surface (mounts, egress, network). Then list the
exact edits you will make, and proceed.

## 5. Migrate

Edit only the target pipeline file. Never edit engine code. Apply:

- per-step `mounts:` (rw where the step writes, e.g. dev/restore/test/build;
  ro for review-style agent steps),
- per-step `egress:` for every `network: true` step,
- `caches:` / `sync_excludes:` from section 3,
- removal or repair of broken env vars.

Preserve the pipeline's structure, step order, and comments unless the
migration requires otherwise. Do not add or remove steps.

## 6. Validate and report

From this repo:

```bash
uv run python - <<'PY'
import sys, yaml
from agentic_dev_loop.core.config import PipelineConfig
cfg = yaml.safe_load(open("<pipeline>"))
PipelineConfig.model_validate(cfg)
print("config validates")
PY
```

Also run the no-em-dash pre-commit hook on the migrated file (the repo bans
non-ASCII). Report: a before/after table of the changes, the validation
output, and what the rerun should watch (restore exit 0, networked fetches
routed through the egress proxy).

## Fail fast rules

- Never migrate without completing section 2.
- Never leave a `network: true` step without an `egress:` declaration.
- Never edit while an engine run is live against the target.
- Never leave a `NUGET_PACKAGES`-style env pointing at a host or relative
  path.
