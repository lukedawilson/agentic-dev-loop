# Consumer onboarding: init + doctor

Design doc. 2026-11-03. Status: approved design, pre-plan.

## Background: the setup failure review

Setup-related failures across this repo and the consumer repo fall into four
classes.

**A. Host/sandbox state mismatch** (structurally dead since 052's per-run
step-boundary setup):

- A1 (016): a host-built darwin venv sat in the bind-mounted workspace; the
  setup stamp hit, provision was skipped, and the Linux container got ENOENT
  executing the darwin interpreter.
- A2 (Oct 31 killed-run spiral): a node_modules cache poisoned with
  darwin-arm64 esbuild/rollup binaries was trusted blindly by the warm stamp.
- A3: the NuGet cache premise was never true; the declared cache was not
  wired to NuGet. Fixed consumer-side by a repo-root NuGet.Config plus
  provision-time restore.
- A4: the provision container mounted the real workspace, so `npm ci` flipped
  the host's darwin node_modules.
- A5: `sync_excludes: **/bin/**` over-matched node_modules bin dirs.
- A6: setup was hoisted to startup; placement was purely declarative, so
  mid-run dependency additions left later warms stale.

**B. Engine install / version skew**:

- B1: pipx upgrade lies. Observed: a newer engine was live on PyPI while the
  trigger still ran the previous install; the stale engine then rejected newer
  pipeline keys (deps_policy). Worked around in trigger scripts with
  `--cooldown 0` and `--pip-args=--prerelease=allow`.
- B2: in-workspace engine installs are refused by the startup self-check (020).
- B3: the .env ban (019) forced secrets into a host env file.

**C. Consumer harness knowledge**: custom toolchain image with the
fontconfig/fonts-liberation Chromium fix, the chromium-userns seccomp profile,
compose services (Postgres + MinIO), auth secrets, the host.docker.internal
endpoint, the NuGet.Config premise file, the Figma access doc. Each was
discovered by a burned run.

**D. Knowledge surface**: docs/setup.md is 354 lines, the README is 684, the
README adaptation checklist was already stale (pre-052 node_modules cache
advice), and the CHANGELOG carries eight consumer-adoption migration sections.
Docs drift is a demonstrated failure mode.

Direction: setup pain is now dominated by consumer onboarding. This effort
builds consumer-side help for a new consumer with a fresh repo. Chosen form:
engine commands, so validation lives where the schema lives and cannot drift
from it the way docs did.

## Decisions

1. Consumer-side onboarding first (not further engine simplification).
2. Audience: new consumers.
3. Form factor: engine commands `init` and `doctor`.
4. Bundled profiles (python, dotnet-node) plus a pluggable consumer-authored
   profile format.
5. doctor runs at onboarding and as a per-run gate from trigger scripts; hard
   failures abort the run.
6. Versioning: a committed pin file `agentic-dev-loop/.engine-version`. The
   trigger runs `doctor --fast` before every run and repairs a hard failure
   exactly once with `agentic-dev-loop upgrade`, which installs the latest
   release with pipx and rewrites the pin to the installed version. doctor
   hard-fails on installed != pin and on pin behind PyPI latest (fix text
   prints the upgrade command). The pipx `--cooldown 0` /
   `--prerelease=allow` lore is deleted.
7. Existing repos migrate as part of this effort: both trigger scripts, plus
   the two bundled profiles and recipes derived from the two real pipelines.
8. init/doctor ship inside the engine package. A separate distribution
   channel would recreate B1 and schema drift.
9. Profile overrides live in pipeline.yaml: the pipeline references the
   profile with a top-level `profile:` key, merged at load. Scalars replace;
   collections union.
10. Profiles carry no commands, gates, or steps. Those live in
    engine-resident recipes.

## Architecture

Profiles are a subpackage of core: `src/agentic_dev_loop/core/profiles/`
(bundled data, loader, merge, engine_min compare). They live in core because
the profile merge happens inside `load_config` (the schema layer), and
`viz/__main__.py` calls `load_config` directly while being forbidden from
importing engine. Folding into core adds no new dependency edge: the existing
core guard (FORBIDDEN scan) is extended to cover the subpackage recursively.

New module `src/agentic_dev_loop/engine/onboarding/`:

- `recipes/python.yaml`, `recipes/dotnet-node.yaml` (bundled data: gate sets,
  gate shapes, command vocabulary with placeholders)
- `templates/` (agent files, trigger script, env-file template)
- thin readers `recipes.py`, `checks.py`, `init.py`, `doctor.py`, following
  the repo's existing data-file pattern; they import `core.profiles` (engine
  to core is the allowed direction)
- pyproject package-data gains the onboarding and core.profiles entries

CLI dispatch in `engine/cli.py` `main()`, same slot as `containers`, before
the pipeline parser; no extension loading; deferred imports:

- `agentic-dev-loop init [--profile python|dotnet-node|PATH] [--yes] [--dir DIR]`
- `agentic-dev-loop doctor [--fast] [--json]`

### init flow

1. Load the profile (bundled by name, or a consumer file by path).
2. Validate: the profile fragment merged with defaults is run through the
   existing config validator in-memory.
3. Detect: lockfiles (deps_policy), existing .gitignore lines, README and
   AGENTS.md (additional_files), solution files, package.json directories,
   docker-compose.yml services.
4. Ask what detection cannot answer: repo display name, llm block, secrets
   list, whether qa drives a browser, test filters, which detected frontend
   directories get test gates. `--yes` accepts detected defaults.
5. Write: `agentic-dev-loop/pipeline.yaml` (with the `profile:` reference and
   only deltas), `sdk_agents/{dev,review,qa}.agent.md` from templates,
   `trigger-agentic-dev-loop.sh` (pin-based), premise files that are missing,
   missing gitignore lines appended, an env-file template, and
   `agentic-dev-loop/.engine-version`.
6. Print one screen of next steps.

### doctor flow

Collect bundled + profile-contributed + detection-derived checks; run
host-side probes; print one actionable line per non-pass, grouped hard/warn;
exit 0 if no hard failures, else 1. `--json` for tooling. `--fast` skips
checks that need more than one quick network round trip (see below).

## Profile format

```yaml
profile:
  name: dotnet-node
  engine_min: 0.9.2
  description: .NET backend with a Node.js frontend

isolation:
  image: ghcr.io/lukedawilson/agentic-dev-loop/dev-dotnet@sha256:...
  seccomp_profile: bundled:chromium-userns
  cpus: 6

caches: [.nuget-packages]

premise_files:
  - path: NuGet.Config
    content: |
      <config><add key="globalPackagesFolder" value=".nuget-packages"/></config>

gitignore_additions: [bin/, obj/, node_modules/]

doctor_checks: []
```

- `caches`, `premise_files`, `gitignore_additions`: minimal defaults,
  overridable from the pipeline by union.
- `isolation` scalars: a pipeline value replaces the profile value.
- Escape hatch for real inflexibility: copy the bundled profile to a
  repo-local file, edit anything, `init --profile PATH`.
- `engine_min`: init/doctor diagnose "profile authored for engine X, you
  have Y" instead of an opaque validator error.
- premise_files and gitignore_additions are applied by init at init time;
  doctor re-checks premise file presence.
- Schema lock: a CI test renders every bundled profile against a scratch
  fixture repo and asserts the existing pipeline validator accepts the
  result. Schema changes break bundled profiles in CI; consumer profiles fail
  loudly at init with the validator's own message.
- The engine config gains an optional top-level `profile:` field
  (`PipelineConfig.profile: str | None = None`); when present, the named (or
  pathed) profile is loaded from `core.profiles` and merged in `load_config`
  before validation. Pipelines without it behave exactly as today.

## Profile placement

- `src/agentic_dev_loop/core/profiles/` subpackage: `__init__.py` (loader,
  merge, `engine_min` compare), `data/python.yaml`, `data/dotnet-node.yaml`.
  Light by contract: stdlib + PyYAML only, same as core.
- `test_module_contract.py`: the core AST scan becomes recursive (rglob) so
  `core/profiles/*.py` is covered by the existing FORBIDDEN list, and a
  fresh-import assertion is added for the subpackage. No new top-level
  package, no new unguarded edge.

## Recipes (engine data, not user-facing)

Per toolchain: default gate set, gate shapes (agent vs command, mounts,
network, egress, verdicts), and command vocabulary with `__PLACEHOLDER__`
tokens (for example `dotnet restore __SOLUTION__`). init fills placeholders
from detection and asks the user to confirm or edit each materialized
command. The python recipe is derived from this repo's dogfood pipeline; the
dotnet-node recipe from the consumer repo's pipeline.

Consequence: a genuinely new toolchain means contributing a recipe (and a dev
image) to the engine. Pluggable profiles narrow to customizing environment
facts (image, caches, wiring, checks) for existing toolchains.

## doctor check list

Bundled (hard):

- engine installed and importable
- installed version == .engine-version pin
- pin not behind PyPI latest (kept in --fast: one request; this is the
  per-run enforcement of decision 6; prereleases included in the comparison)
- docker on PATH; daemon reachable; API >= 1.43; OSType linux
- workspace is a git repo (052 precondition)
- no .env* in the workspace (whitelist excepted)
- pipeline image digest pullable and matches (pull check skipped in --fast)

Profile-contributed (warn by default): toolchain probes declared in
`doctor_checks`; premise files exist (NuGet.Config presence is the A3
regression guard).

Detection-derived (warn): the LLM key env var is set; each pipeline secret's
env var is set; services parsed from docker-compose.yml are reachable
(skipped in --fast).

Exit 1 only on hard failures. Trigger scripts run `doctor --fast` before
every run; on hard failure they run `agentic-dev-loop upgrade` once
(install latest, rewrite the pin), re-run doctor, then abort on hard
failure.

## Existing-repo migration

- Both trigger scripts: delete the pipx upgrade lore and the pin read; run
  `doctor --fast`; on hard failure run `agentic-dev-loop upgrade` once
  (install latest, rewrite the pin); re-run doctor; abort on hard failure.
- This repo: derive the python profile and recipe from the dogfood pipeline.
- Consumer repo: derive the dotnet-node profile and recipe from its pipeline
  (NuGet.Config premise, .nuget-packages cache, cpus 6, chromium seccomp).
  Its browser cache becomes the pipeline-side additive override, which
  demonstrates the merge semantics on a real config.
- Optionally slim both pipelines to reference their profiles with deltas
  only; this is the dogfood proof of the `profile:` merge path.
- README: "Authoring a loop for your own repo" rewritten around init; the
  stale node_modules-cache checklist item removed.
- docs/setup.md: init and doctor sections added; troubleshooting gains doctor
  pointers.

## Error handling

- init refuses to overwrite an existing agentic-dev-loop/ without an explicit
  flag.
- doctor hard failures print the exact fix command.
- Unknown profile name: list bundled names, exit 1.
- engine_min newer than the installed engine: loud diagnostic, exit 1.
- Detection ambiguity (multiple solutions, multiple package.json dirs):
  always ask, never guess.

## Testing

- CI acceptance: render each bundled profile against scratch fixture repos;
  the existing validator must accept the result.
- init against fixture repos in temp dirs (real files, no mocks, per repo
  policy).
- doctor checks: unit tests; only network-boundary probes mocked (PyPI,
  registry, service reachability) per the mock policy.
- Migration proven by applying to both real repos and running doctor clean.

## Out of scope

- Cache GC/pruning and per-workspace cache keying (deferred from 052).
- Dev image authoring automation.
- Recipes beyond python and dotnet-node.
- A qa-stage behavior change (browser-only qa) was raised mid-session and
  explicitly struck; qa semantics are unchanged by this work.
