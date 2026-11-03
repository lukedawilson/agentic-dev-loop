# 055: Consumer onboarding: init + doctor

> **Status: APPROVED**

Authored directly APPROVED (2026-11-03), per planning-docs conventions.
Implementation is TDD red-green-refactor, behind the repo ask-before-coding
gate. Design decisions and their rationale live in
docs/specs/2026-11-03-consumer-onboarding-design.md (commit e60e686); this
plan states the implementation contracts, not the discussion.

## Problem

Setup failures across this repo and the consumer repo were reviewed into four
classes: A) host/sandbox state mismatch (dead since 052), B) engine
install/version skew, C) consumer harness knowledge that cost burned runs,
D) a documentation surface (setup.md 354 lines, README 684 lines, eight
CHANGELOG consumer-adoption migrations) that demonstrably drifts. Setup pain
is now dominated by consumer onboarding. This plan builds the onboarding
surface: two engine commands, `init` and `doctor`, with bundled toolchain
profiles and recipes, plus migration of both repos onto them.

## Decisions

- **Consumer-side onboarding first; audience is a new consumer with a fresh
  repo.** The loop's remaining setup pain is entry cost, not engine behavior.
- **Form factor: engine commands.** Validation lives where the schema lives
  (core/config.py); docs drift is a demonstrated failure mode and a docs
  rewrite would drift again.
- **Bundled profiles (python, dotnet-node) plus a pluggable consumer-authored
  profile format.** New toolchains need a recipe contribution and a dev image;
  consumer profile files customize environment facts for existing toolchains.
- **doctor is a per-run gate.** Trigger scripts run `doctor --fast` before
  every run; hard failures abort, warnings print and continue.
- **Versioning: a committed pin file, not a pipx upgrade ritual.** The
  trigger's current `pipx upgrade --cooldown 0
  --pip-args="--prerelease=allow"` lore (trigger-agentic-dev-loop.sh) exists
  because pipx short-circuits and uv excludes prereleases; it was observed
  failing anyway ("a11 live, trigger still ran an a10 engine"). Replaced by
  `agentic-dev-loop/.engine-version` (the installed version at init time),
  enforced by the trigger, checked by doctor, which blocks when the pin is
  behind the latest release.
- **Both repos migrate as part of this plan.** This repo derives the python
  profile/recipe from its dogfood pipeline (agentic-dev-loop/pipeline.yaml);
  the consumer repo derives dotnet-node from its pipeline.
- **One distribution channel.** init/doctor ship inside the engine package; a
  second package would recreate the B1 staleness and schema drift.
- **Profiles merge at config load.** The pipeline references the profile via
  a new top-level `profile:` key; load_config resolves and merges it. Scalars
  replace; collections union. Rationale: every catastrophic setup failure
  came from a missing piece (the A3 dropped NuGet wiring), never an extra
  one; replace semantics could silently drop `.nuget-packages` and recreate
  A3.
- **Profiles carry no commands, gates, or steps.** The per-line test for
  profile content is "is this true of every app on this toolchain?".
  Gate shape and command vocabulary live in engine-resident recipes.
- **Profiles live in core as a subpackage** (src/agentic_dev_loop/core/profiles/).
  See Placement below.

## Core model

1. **Profile file.** Flat YAML: `name`, `engine_min`, optional `description`,
   `isolation` (image, optional seccomp_profile, cpus), optional `caches`,
   `premise_files` (list of {path, content}), `gitignore_additions`,
   `doctor_checks`. No other keys (strict). Bundled data:
   `src/agentic_dev_loop/core/profiles/data/python.yaml` and
   `data/dotnet-node.yaml`.

2. **Pipeline references the profile.** New optional PipelineConfig field
   `profile: str | None = None` (core/config.py, PipelineConfig at line 596).
   `load_config` (core/config.py line 854) pops `profile` from the loaded
   dict, resolves it, merges, then re-inserts the reference before
   validation. Absent key = today's behavior, byte for byte.

3. **Merge semantics.** The profile supplies pipeline defaults; the pipeline
   overrides them. `isolation` is merged key-wise (pipeline keys win over
   profile keys; profile keys present but pipeline keys absent are kept).
   `caches` is a dedupe-preserving-order union, profile entries first.

4. **Recipes.** Engine-resident per-toolchain YAML in
   `src/agentic_dev_loop/engine/onboarding/recipes/`: the default gate set
   (brief/dev/restore/test/editor-tests/conformance/review/qa/commit as
   applicable), each gate's shape (agent/command, mounts, network, egress,
   verdicts, timeouts), and command vocabulary containing `__PLACEHOLDER__`
   tokens. `init` resolves tokens from detection and questions, then renders
   a pipeline that passes `load_config` validation by construction.

5. **init flow.** Load profile (bundled by name or consumer file by path);
   detect (solution files, package.json directories, lockfiles, existing
   .gitignore lines, README, AGENTS.md, docker-compose services); ask only
   what detection cannot answer; render; write. Outputs:
   `agentic-dev-loop/pipeline.yaml`, `agentic-dev-loop/sdk_agents/` agent
   files, `trigger-agentic-dev-loop.sh` (executable), missing premise files,
   appended gitignore lines, an env-file template, and
   `agentic-dev-loop/.engine-version`.

6. **doctor flow.** Collect bundled + profile-contributed +
   detection-derived checks; probe host-side only; print one actionable line
   per non-pass grouped hard/warn; exit 0 iff no hard failures. `--fast`
   skips checks needing more than one quick network round trip (see Doctor
   checks). `--json` emits machine-readable results.

7. **Pin semantics.** `.engine-version` holds the exact installed engine
   version at init time (currently 0.9.2). The trigger enforces installed ==
   pin (repairing with `pipx install --force` once, then failing hard).
   doctor hard-fails on installed != pin and on pin behind the latest PyPI
   release (prereleases included in the comparison), with a fix line naming
   the exact bump command.

## Placement: profiles are a core subpackage

`load_config` is the one seam every consumer of a pipeline converges on:
engine/cli.py:98 (_registry_for), engine/graph.py:140 (run_pipeline entry),
engine/dryrun.py:137, and viz/__main__.py:25. The module contract
(tests/test_module_contract.py) forbids viz from importing engine
(VIZ_FORBIDDEN, lines 28-29), so load-time profile resolution must be
reachable from core: profile data, loader, merge, and the engine_min
comparison live in `src/agentic_dev_loop/core/profiles/`. Core's own import
guard (FORBIDDEN, lines 23-24) stays satisfied: the subpackage imports
stdlib + pydantic + PyYAML only, same as core. Because the guard's AST scan
(`_forbidden_imports`, line 34) uses a non-recursive `glob('*.py')`, it must
become `rglob` so the subpackage is actually covered, and a fresh-import
assertion for the subpackage is added (see Task 3). Recipes, templates, init,
and doctor are engine concerns and live in
`src/agentic_dev_loop/engine/onboarding/`, importing `core.profiles` (the
allowed direction).

## Contracts

### Task 1: core/profiles subpackage (data + loader + merge)

Files: create `src/agentic_dev_loop/core/profiles/__init__.py`,
`data/python.yaml`, `data/dotnet-node.yaml`; tests
`tests/test_055_profiles.py`. pyproject `[tool.setuptools.package-data]`
gains `'agentic_dev_loop.core.profiles' = ['data/*.yaml']` (this entry is
part of Task 1, not Task 9).

Exported names and contracts:

- `class ProfileError(ValueError)` - all profile failures raise this.
- `class PremiseFile(BaseModel)` - fields `path: str`, `content: str`;
  strict (extra=forbid).
- `class ProfileIsolation(BaseModel)` - `image: str`, `seccomp_profile:
  str | None = None`, `cpus: int` (required); strict.
- `class Profile(BaseModel)` - `name: str`, `engine_min: str`,
  `description: str = ""`, `isolation: ProfileIsolation`, `caches: list[str]
  = []`, `premise_files: list[PremiseFile] = []`, `gitignore_additions:
  list[str] = []`, `doctor_checks: list = []`; strict.
- `parse_version(text: str) -> tuple[int, int, int, int, int]` - accepts
  X.Y.Z with optional a|b|rcN prerelease suffix; ranks a < b < rc < final;
  raises ProfileError on unparseable input. The tuple must be directly
  comparable.
- `check_engine_min(engine_min: str, installed: str) -> None` - raises
  ProfileError when installed < engine_min, message exactly:
  `profile requires engine >= {engine_min}; installed {installed}. Upgrade:
  pipx install --force "agentic-dev-loop=={latest}"` (latest substituted by
  the caller; see Task 7).
- `bundled_profile_names() -> list[str]` - lists names from the `data/`
  directory using `importlib.resources.files(...).joinpath('data').iterdir()`
  (Python 3.12-safe; no glob).
- `load_profile(reference: str, installed_version: str) -> Profile` -
  reference is either a bundled name or a filesystem path to a consumer
  profile file (existence check decides); unknown bundled name raises
  ProfileError `unknown profile '{reference}'; bundled profiles: {names}`;
  bundled files whose `name` field does not match their filename raise
  ProfileError; always runs check_engine_min.
- `merge_profile(data: dict, profile: Profile) -> dict` - returns the merged
  pipeline dict: isolation key-wise (profile under, pipeline over, using
  model_dump(exclude_none=True) on the profile side), caches union
  profile-first dedupe-preserving-order. Other keys untouched.

Data contracts: `python.yaml` = name python, engine_min 0.9.2, isolation
image equal to the engine default dev image reference
(ghcr.io/lukedawilson/agentic-dev-loop/dev@sha256:890105209c127446caf20ce3dc05f0c0f5527e9d679e18cc0075e6b0ee917434,
the value of DEFAULT_DEV_IMAGE_REF, engine/isolation.py line 55), cpus 2,
gitignore_additions [`.venv-sandbox/`], no caches. `dotnet-node.yaml` = name
dotnet-node, engine_min 0.9.2, isolation image
ghcr.io/lukedawilson/agentic-dev-loop/dev-dotnet@sha256:d15d1c6115fafc3d5f3bfb567338d61441081e5d7ff83d7770bc0b5f14f977fc,
seccomp_profile `bundled:chromium-userns`, cpus 6, caches
[`.nuget-packages`], premise_files [NuGet.Config with the globalPackagesFolder
wiring that once silently broke (A3)], gitignore_additions [`bin/`, `obj/`,
`node_modules/`, `.nuget-packages/`].

```yaml
# data/python.yaml
name: python
engine_min: 0.9.2
description: Python project; the sandbox builds its own venv per run
isolation:
  image: ghcr.io/lukedawilson/agentic-dev-loop/dev@sha256:890105209c127446caf20ce3dc05f0c0f5527e9d679e18cc0075e6b0ee917434
  cpus: 2
gitignore_additions:
  - .venv-sandbox/
```

```yaml
# data/dotnet-node.yaml
name: dotnet-node
engine_min: 0.9.2
description: .NET backend with a Node.js frontend
isolation:
  image: ghcr.io/lukedawilson/agentic-dev-loop/dev-dotnet@sha256:d15d1c6115fafc3d5f3bfb567338d61441081e5d7ff83d7770bc0b5f14f977fc
  seccomp_profile: bundled:chromium-userns
  cpus: 6
caches:
  - .nuget-packages
premise_files:
  - path: NuGet.Config
    content: |
      <configuration>
        <config>
          <add key="globalPackagesFolder" value=".nuget-packages" />
        </config>
      </configuration>
gitignore_additions:
  - bin/
  - obj/
  - node_modules/
  - .nuget-packages/
```

Tests (red-first): parse_version ordering across final/prerelease ranks and
malformed input; path-based consumer profile loads; engine_min blocks with
the exact message; merge keeps pipeline isolation scalars and drops profile
ones excluded by exclude_none; caches union order profile-first with dedupe;
unknown profile message lists bundled names; each bundled data file parses
and matches its declared name.

### Task 2: load-time merge in core/config.py

Modify: core/config.py. PipelineConfig gains `profile: str | None = None`
(after merge_on_conflict). load_config (line 854): after the dict check and
before state_dir injection, pop `profile`; if present, lazily import the
loader from `agentic_dev_loop.core.profiles` and read the installed version
via importlib.metadata.version('agentic-dev-loop'); wrap any ProfileError as
`pipeline {path}: {original message}`; merge into data; re-insert
`data['profile'] = reference` so the validated config records which profile
it resolved. No profile key = unchanged behavior.

```yaml
# pipeline.yaml as authored
profile: dotnet-node
name: my-app
caches:
  - qa/.browsers

# effective config after the load_config merge
isolation:
  image: ghcr.io/lukedawilson/agentic-dev-loop/dev-dotnet@sha256:d15d1c6115fafc3d5f3bfb567338d61441081e5d7ff83d7770bc0b5f14f977fc
  seccomp_profile: bundled:chromium-userns
  cpus: 6
caches:
  - .nuget-packages   # profile first
  - qa/.browsers      # pipeline appended, dedupe on match
```

Tests `tests/test_055_profile_loading.py` (red-first), minimal fixture with
`deps_policy: open` (locked pipelines without lockfiles fail validation):
profile: python resolves image/cpus and records profile; caches union
pipeline [.nuget-packages, qa/.browsers] over a profile caches list keeps
profile-first order and dedupes; unknown profile fails loudly with the
wrapped message; a pipeline without profile: validates exactly as before.

### Task 3: module-contract guard coverage

Modify: tests/test_module_contract.py line 34 `glob('*.py')` ->
`rglob('*.py')` so core/profiles/*.py is scanned by the existing FORBIDDEN
list. Add `test_core_profiles_fresh_import_loads_no_heavy_deps`, mirroring
the existing core fresh-import test (lines 59-75): importing
`agentic_dev_loop.core.profiles` in a fresh interpreter with PYTHONPATH=src
loads zero modules from the forbidden set. Red-first: the new test passes
immediately (no violation), but the rglob change must still be in place
before Task 2 lands so an accidental heavy import in profiles fails CI.

### Task 4: recipes

Files: create `src/agentic_dev_loop/engine/onboarding/recipes.py`,
`recipes/python.yaml`, `recipes/dotnet-node.yaml`; tests
`tests/test_055_recipes.py`.

Contracts: `class RecipeError(ValueError)`;
`bundled_recipe_names() -> list[str]` (iterdir, same pattern as profiles);
`load_recipe(name) -> dict` - mapping required, `steps` list required;
`undeclared_tokens(doc) -> set[str]` - recursively find `__[A-Z][A-Z0-9_]*__`
tokens anywhere in the loaded recipe.

Recipe shape: each recipe declares `placeholders` (a map of token name to
default or the marker DETECTED), `steps` (list of gate dicts: name, kind,
agent/command, mounts, network, egress, verdicts, timeouts, iteration caps,
setup), `brief_env_facts` (list, token-bearing allowed), and
`dev_impossible_set` (the dev prompt's command deny-list for this
toolchain).

python recipe: placeholders `__VENV__` (.venv-sandbox), `__DEV_EXTRAS__`
(.[dev]), `__TEST_PATH__` (tests/), `__TEST_FILTER__` (not docker); gates
brief/dev/restore/test/review/qa/commit derived from the dogfood pipeline
(dev setup builds the venv and installs editable deps; restore re-runs pip
install plus pip_audit with network true and egress package-registries; test
runs pytest with the filter; review ro; qa ro with max_iterations 150 and
finalize_nudge_turns 10; ENV verdict deliberately unset so it halts);
dev_impossible_set one bullet (package-manager fetches after a new pin).

dotnet-node recipe: placeholders `__SOLUTION__` (DETECTED),
`__FRONTEND_DIR__` (DETECTED), `__QA_FRONTEND_DIR__` (defaults to
__FRONTEND_DIR__), `__FILTER__` (Category!=Database); gates
brief/dev/restore/test/editor-tests/conformance/review/qa/commit derived
from the consumer pipeline (dev setup runs npm ci in the frontend dir then
dotnet restore the solution; restore = dotnet restore, network true, egress
package-registries, timeout 1800; test = dotnet test with the filter, 1800;
editor-tests = npm ci && npm test in the frontend dir, 1200; conformance
marked optional with detect key `tools/run-conformance.sh`; qa rw, network
true, egress class browser, env PLAYWRIGHT_BROWSERS_PATH qa/.browsers,
max_iterations 100, finalize_nudge_turns 10); dev_impossible_set four
bullets (package fetches outside network-on steps, playwright downloads,
database-category tests, the filter's category).

```yaml
# recipes/python.yaml
name: python
placeholders:
  __VENV__: .venv-sandbox
  __DEV_EXTRAS__: ".[dev]"
  __TEST_PATH__: tests/
  __TEST_FILTER__: "not docker"
brief_env_facts:
  - "the workspace is an export with no .git; verify with find/ls, never git status"
  - "the sandbox is network-off by default; never run package-manager fetches"
  - "Python packages resolve from the sandbox venv; run __VENV__/bin/python"
dev_impossible_set:
  - "package-manager fetches after a new pin run only in the network-on restore step"
steps:
  - {name: brief, kind: brief, brief_inputs: {include: [AGENTS.md]}, on_pass: dev}
  - name: dev
    kind: agent
    agent: dev
    reasoning_effort: high
    max_iterations: 150
    finalize_nudge_turns: 10
    produces: implementation-summary.md
    setup: 'python -m venv __VENV__ && __VENV__/bin/pip install -e "__DEV_EXTRAS__"'
    mounts: [{path: ".", mode: rw}]
    on_pass: restore
  - name: restore
    kind: command
    command: '__VENV__/bin/python -m pip install --upgrade pip && __VENV__/bin/python -m pip install -e "__DEV_EXTRAS__" && __VENV__/bin/python -m pip_audit --skip-editable'
    timeout: 600
    network: true
    egress: {presets: [package-registries]}
    mounts: [{path: ".", mode: rw}]
    on_pass: test
    on_fail: {goto: dev, retry: true}
  - name: test
    kind: command
    command: '__VENV__/bin/python -m pytest __TEST_PATH__ -q -m "__TEST_FILTER__"'
    timeout: 600
    mounts: [{path: ".", mode: rw}]
    on_pass: review
    on_fail: {goto: dev, retry: true}
  - {name: review, kind: agent, agent: review, reasoning_effort: xhigh,
     artifact: review-findings.md, verdicts: {pass: APPROVED, fail: NEEDS CHANGES},
     mounts: [{path: ".", mode: ro}], on_pass: qa, on_fail: {goto: dev, retry: true}}
  - name: qa
    kind: agent
    agent: qa
    reasoning_effort: medium
    artifact: qa-report.md
    verdicts: {pass: PASS, fail: FAIL, unverifiable: UNVERIFIABLE, env: ENV}
    max_iterations: 150
    finalize_nudge_turns: 10
    mounts: [{path: ".", mode: ro}]
    on_pass: commit
    on_fail: {goto: dev, retry: true}
    on_unverifiable: commit
    # on_env deliberately absent: an ENV verdict halts the run
  - {name: commit, kind: action, action: commit,
     params: {message: "feat: construct {subject} (agent dev loop)"}, on_pass: success}
```

```yaml
# recipes/dotnet-node.yaml
name: dotnet-node
placeholders:
  __SOLUTION__: DETECTED
  __FRONTEND_DIR__: DETECTED
  __QA_FRONTEND_DIR__: __FRONTEND_DIR__
  __FILTER__: "Category!=Database"
brief_env_facts:
  - "the workspace is an export with no .git; verify with find/ls, never git status"
  - "the sandbox is network-off by default; package fetches (dotnet restore, npm ci) run only in the network-on pipeline steps and in per-step setup hooks (a one-shot network-on provision container runs before each step entry); agent turns never fetch packages"
  - "database-tagged tests never run in the loop: dotnet test always runs --filter __FILTER__"
dev_impossible_set:
  - "package-manager fetches outside network-on steps and setup hooks"
  - "playwright browser downloads"
  - "database-category tests"
  - "commands that boot servers or need external services"
steps:
  - {name: brief, kind: brief, brief_inputs: {include: [AGENTS.md]}, on_pass: dev}
  - name: dev
    kind: agent
    agent: dev
    reasoning_effort: high
    max_iterations: 100
    finalize_nudge_turns: 10
    produces: implementation-summary.md
    setup: 'cd __FRONTEND_DIR__ && npm ci && cd .. && dotnet restore __SOLUTION__'
    mounts: [{path: ".", mode: rw}]
    on_pass: restore
  - name: restore
    kind: command
    command: 'dotnet restore __SOLUTION__'
    timeout: 1800
    network: true
    egress: {presets: [package-registries]}
    mounts: [{path: ".", mode: rw}]
    on_pass: test
    on_fail: {goto: dev, retry: true}
  - name: test
    kind: command
    command: 'dotnet test __SOLUTION__ --filter "__FILTER__"'
    timeout: 1800
    network: true
    egress: {presets: [package-registries]}
    mounts: [{path: ".", mode: rw}]
    on_pass: editor-tests
    on_fail: {goto: dev, retry: true}
  - name: editor-tests
    kind: command
    command: 'cd __FRONTEND_DIR__ && npm ci && npm test'
    timeout: 1200
    network: true
    egress: {presets: [package-registries]}
    mounts: [{path: ".", mode: rw}]
    on_pass: conformance
    on_fail: {goto: dev, retry: true}
  - name: conformance
    kind: command
    command: 'bash tools/run-conformance.sh'
    timeout: 1800
    network: true
    egress: {presets: [package-registries]}
    mounts: [{path: ".", mode: rw}]
    optional: {detect: tools/run-conformance.sh}
    on_pass: review
    on_fail: {goto: dev, retry: true}
  - {name: review, kind: agent, agent: review, reasoning_effort: xhigh,
     artifact: review-findings.md, verdicts: {pass: APPROVED, fail: NEEDS CHANGES},
     mounts: [{path: ".", mode: ro}], on_pass: qa, on_fail: {goto: dev, retry: true}}
  - name: qa
    kind: agent
    agent: qa
    reasoning_effort: medium
    artifact: qa-report.md
    verdicts: {pass: PASS, fail: FAIL, unverifiable: UNVERIFIABLE, env: ENV}
    max_iterations: 100
    finalize_nudge_turns: 10
    setup: 'cd __QA_FRONTEND_DIR__ && npm ci'
    network: true
    egress: {class: browser}
    env: {PLAYWRIGHT_BROWSERS_PATH: qa/.browsers}
    mounts: [{path: ".", mode: rw}]
    on_pass: commit
    on_fail: {goto: dev, retry: true}
    on_unverifiable: commit
    # on_env deliberately absent: an ENV verdict halts the run
  - {name: commit, kind: action, action: commit,
     params: {message: "feat: construct {subject} (agent dev loop)"}, on_pass: success}
```

Tests (red-first): both recipes load and satisfy shape; every token appearing
in a recipe is declared in its placeholders map; python recipe contains the
exact dev setup and test commands from the dogfood pipeline; dotnet-node
contains the exact dev setup, test, editor-tests commands from the consumer
pipeline; unknown recipe raises RecipeError.

### Task 5: templates

Files: create `src/agentic_dev_loop/engine/onboarding/templates.py` and
`templates/` containing `dev.agent.md`, `qa.agent.md`, `review.agent.md`,
`trigger-agentic-dev-loop.sh`, `env.example`; tests
`tests/test_055_templates.py`.

Contracts: `class TemplateError(ValueError)`;
`load_template(name: str) -> str` (iterdir discovery). The three agent
templates are copies of this repo's dogfood agent files
(agentic-dev-loop/sdk_agents/dev.agent.md, qa.agent.md, review.agent.md);
dev gains two substitution tokens, `{{IMPOSSIBLE_SET}}` and
`{{SANDBOX_NOTES}}`, placed at the locations of the corresponding sections
(identified by a case-insensitive heading search; if a heading is not found
the implementation must STOP and report the actual headings rather than
guess). The trigger template: `set -euo pipefail`; PIN_FILE
`agentic-dev-loop/.engine-version`; engine-on-PATH check with a pipx install
hint; optional sourcing of `AGENTIC_DEV_LOOP_ENV_FILE` (default
~/.config/<repo>/agentic-dev-loop.env pattern, kept generic); the pin gate
runs `agentic-dev-loop doctor --fast` first, on failure repairs once with
`pipx install --force "agentic-dev-loop==$(cat "$PIN_FILE")"`, retries
doctor, then fails hard; exactly-one-loader enforcement (--plan /
--aidlc-unit / --resume with positional unit-* shorthand counting as a mode);
finally `exec agentic-dev-loop "$PIPELINE"` forwarding all arguments. It
must NOT contain `pipx upgrade`. env.example: a commented guide for the host
env file (no secret values).

Tests (red-first): all template names load; dev template contains both token
markers and the token lines replace section content (no duplication);
trigger template contains `.engine-version`, `doctor --fast`, `pipx install
--force` and does NOT contain `pipx upgrade`; trigger template is valid
syntax per bash -n.

### Task 6: init

Files: create `src/agentic_dev_loop/engine/onboarding/init.py`; tests
`tests/test_055_init.py`.

Contracts: `class InitError(ValueError)`. A `Detection` result carries:
solutions (sorted, from `*.sln` search), frontend dirs (sorted unique
parents of package.json, excluding anything under node_modules), lockfiles
(matching the known set: uv.lock, package-lock.json, packages.lock.json,
poetry.lock), existing .gitignore lines, README path if found, AGENTS.md
presence, and a conformance flag (tools/run-conformance.sh exists).
`run_init(argv) -> int` parses --profile (default python), --recipe,
--yes, --dir (default '.'), and orchestrates: load profile; choose recipe
(flag, else profile name when bundled, else python); fail loudly on any
undeclared token in the recipe; refuse (exit 1) if
`agentic-dev-loop/pipeline.yaml` already exists; detect; resolve DETECTED
tokens (single candidate auto, multiple candidates prompt, absent DETECTED
token with no default prompts, absent non-DETECTED token with no default is
an error); ask for llm block, pipeline name, and secrets unless --yes (which
uses deepseek-v4-pro / https://api.deepseek.com / OPENAI_API_KEY and no
secrets); render; write.

Render contract: drop optional gates whose detection is absent
(conformance), rewire the on_pass chain positionally so the last remaining
gate before commit has no on_pass; brief includes AGENTS.md and README
(additional_files with ../ prefix); pipeline dict carries name, profile
reference, llm, max_attempts 10, agents_dir sdk_agents, deps_policy locked
when lockfiles exist else open, lockfiles list, additional_files, and
caches including qa/.browsers when any included gate uses egress class
browser; secrets from answers. Write contract: pipeline.yaml with a
generated-by comment; the three agent files (dev with token substitution);
trigger (chmod 755) at the repo root; .engine-version set to the installed
version; premise files written only if missing; gitignore additions
appended only if missing, plus qa/.browsers/ when the browser tier is
present; env.example. Print one screen of next steps.

```yaml
# agentic-dev-loop/pipeline.yaml as rendered by init --profile dotnet-node
# for a repo with src/App.sln, frontend/package.json, tools/run-conformance.sh
# and no lockfiles, no secrets. (generated-by comment omitted here.)
profile: dotnet-node
name: app-name
llm: {model: openai/deepseek-v4-pro, base_url: "https://api.deepseek.com", api_key_env: OPENAI_API_KEY}
max_attempts: 10
agents_dir: sdk_agents
deps_policy: open
additional_files: ["../README.md", "../AGENTS.md"]
caches: [qa/.browsers]
steps:
  - {name: brief, kind: brief, brief_inputs: {include: [AGENTS.md]}, on_pass: dev}
  - name: dev
    kind: agent
    agent: dev
    produces: implementation-summary.md
    setup: 'cd frontend && npm ci && cd .. && dotnet restore src/App.sln'
    mounts: [{path: ".", mode: rw}]
    on_pass: restore
  - name: restore
    kind: command
    command: 'dotnet restore src/App.sln'
    timeout: 1800
    network: true
    egress: {presets: [package-registries]}
    mounts: [{path: ".", mode: rw}]
    on_pass: test
    on_fail: {goto: dev, retry: true}
  - name: test
    kind: command
    command: 'dotnet test src/App.sln --filter "Category!=Database"'
    timeout: 1800
    network: true
    egress: {presets: [package-registries]}
    mounts: [{path: ".", mode: rw}]
    on_pass: editor-tests
    on_fail: {goto: dev, retry: true}
  - name: editor-tests
    kind: command
    command: 'cd frontend && npm ci && npm test'
    timeout: 1200
    network: true
    egress: {presets: [package-registries]}
    mounts: [{path: ".", mode: rw}]
    on_pass: conformance
    on_fail: {goto: dev, retry: true}
  - {name: conformance, kind: command, command: 'bash tools/run-conformance.sh',
     timeout: 1800, network: true, egress: {presets: [package-registries]},
     mounts: [{path: ".", mode: rw}], on_pass: review, on_fail: {goto: dev, retry: true}}
  - {name: review, kind: agent, agent: review, artifact: review-findings.md,
     verdicts: {pass: APPROVED, fail: NEEDS CHANGES}, mounts: [{path: ".", mode: ro}],
     on_pass: qa, on_fail: {goto: dev, retry: true}}
  - name: qa
    kind: agent
    agent: qa
    artifact: qa-report.md
    verdicts: {pass: PASS, fail: FAIL, unverifiable: UNVERIFIABLE, env: ENV}
    max_iterations: 100
    finalize_nudge_turns: 10
    setup: 'cd frontend && npm ci'
    network: true
    egress: {class: browser}
    env: {PLAYWRIGHT_BROWSERS_PATH: qa/.browsers}
    mounts: [{path: ".", mode: rw}]
    on_pass: commit
    on_fail: {goto: dev, retry: true}
    on_unverifiable: commit
  - {name: commit, kind: action, action: commit,
     params: {message: "feat: construct {subject} (agent dev loop)"}, on_pass: success}
```

Tests (red-first): python profile against a fixture repo renders a pipeline
that load_config accepts, with dev setup exactly
`python -m venv .venv-sandbox && .venv-sandbox/bin/pip install -e ".[dev]"`
and test command exactly
`.venv-sandbox/bin/python -m pytest tests/ -q -m "not docker"`, and
.engine-version equal to the installed version; dotnet-node against a
fixture repo with src/App.sln and a frontend/ package.json renders nine
steps, test command exactly `dotnet test src/App.sln --filter
"Category!=Database"`, editor-tests exactly
`cd frontend && npm ci && npm test`, caches == [qa/.browsers], seccomp
present, NuGet.Config written, gitignore gains bin/, obj/, .nuget-packages/,
qa/.browsers/; without tools/run-conformance.sh the rendered chain reconnects
editor-tests.on_pass to review; unknown profile exits 1; existing
pipeline.yaml exits 1.

### Task 7: doctor

Files: create `src/agentic_dev_loop/engine/onboarding/doctor.py` and
`checks.py`; tests `tests/test_055_doctor.py`.

Contracts: a check is (name, severity in {hard, warn}, ok, message, fix_text).
`collect_checks(profile, pipeline, installed, fast) -> list[check]`;
`run_doctor(argv) -> int` parses --fast and --json, runs checks, prints one
actionable line per non-pass grouped hard then warn, exits 1 iff any hard
failure, else 0. Bundled hard checks: engine package importable and version
readable; installed == .engine-version pin (fix text: the pipx install
--force command); pin not behind the latest PyPI release including
prereleases (kept in --fast; one request); docker on PATH; daemon
reachable; API version >= 1.43; OSType linux; workspace is a git repo (052
precondition); no .env* files (whitelist .env.example/.env.template/
.env.sample); pipeline image digest pullable and matching (skipped in
--fast). Profile-contributed warnings: doctor_checks probes from the
profile; every premise_file exists (the A3 regression guard).
Detection-derived warnings: the llm api_key_env variable is set in the host
environment; every pipeline secret's source env var is set; services parsed
from docker-compose.yml are reachable (skipped in --fast).

Tests (red-first): happy path exits 0 with no output; installed != pin is a
hard failure with the pipx fix text; missing docker is hard; .env is hard;
premise file missing is a warning; llm key missing is a warning; exit code
contract (1 only for hard); --json shape. Only network-boundary probes are
mocked (PyPI, registry, service reachability), per repo mock policy.

### Task 8: CLI dispatch

Modify: engine/cli.py main() (line 161). After the containers branch (lines
166-167) and before the probe parser, dispatch exactly the same way:
`if argv and argv[0] == 'init'` imports onboarding.init lazily and returns
run_init(argv[1:]); same for doctor. No onboarding imports at module load.
Tests `tests/test_055_cli.py`: `main(['doctor', '--help'])` exits 0 and
prints usage; `main(['init', '--help'])` same; `main(['doctor', '--json',
'--fast'])` returns an int; unknown subcommand behavior unchanged (falls
through to the pipeline probe).

### Task 9: packaging

Modify: pyproject.toml `[tool.setuptools.package-data]` gains
`'agentic_dev_loop.engine.onboarding' = ['recipes/*.yaml', 'templates/*']`
(the core.profiles entry is Task 1's). No test file; the schema-lock test
(Task 11) fails loudly if the data is not shipped.

### Task 10: dogfood migration and consumer checklist

Tooling-path deliverables: this repo's trigger-agentic-dev-loop.sh,
agentic-dev-loop/pipeline.yaml, agentic-dev-loop/.engine-version, README.md,
docs/setup.md are tooling paths and are NOT swept by the loop commit; they
need human commits. The plan states this per planning-docs README.

- Add agentic-dev-loop/.engine-version = 0.9.2.
- Rewrite trigger-agentic-dev-loop.sh to the template shape (human commit).
- pipeline.yaml: add `profile: python`, delete the isolation block (human
  commit).
- hostboundary.py: the tooling path set (tooling_rel_paths, line 63) gains
  trigger-agentic-dev-loop.sh and agentic-dev-loop/.engine-version, with a
  unit test.
- README: rewrite "Authoring a loop for your own repo" around init; remove
  the stale node_modules cache checklist item (human commit).
- docs/setup.md: add init and doctor sections; point troubleshooting at
  doctor (human commit).
- Commit the uncommitted spec edits
  (docs/specs/2026-11-03-consumer-onboarding-design.md).
- Consumer-repo migration checklist (human commits in the consumer repo):
  rewrite its trigger script to the template shape, keeping the
  AGENTIC_DEV_LOOP_ENV_FILE sourcing; add profile: dotnet-node to its
  pipeline and delete the now-redundant isolation/caches entries the profile
  supplies; its NuGet.Config already exists (init would have written it);
  add .engine-version.

### Task 11: schema lock

Tests `tests/test_055_schema_lock.py`: for each bundled profile plus its
recipe, render with init's defaults against a scratch fixture repo, write
the pipeline to a temp dir, and assert `core.load_config` accepts it. This
is the CI acceptance test: any future schema change that breaks a bundled
profile fails here in the same release, making schema/profile drift
impossible rather than managed.

## Error handling and edge cases

- Profile/recipe/template loading failures raise their named error types
  (ProfileError/RecipeError/TemplateError); the CLI layer converts them to a
  stderr message and exit 1.
- load_config wraps profile errors with the pipeline path for attribution.
- init never overwrites an existing agentic-dev-loop/ (exit 1, message names
  the blocker and the manual path).
- Multi-candidate detection (two solutions, two frontend dirs) always
  prompts, never guesses.
- Trigger repair happens exactly once per run, then hard failure.
- All new user-facing text is ASCII (em dash ban), no consumer names.

## Tests (red-first)

Per task above; all test files named test_055_*.py per the plan-numbering
convention. Integration surfaces that stay real: load_config, file I/O
against temp fixture repos, argparse, docker CLI checks where the daemon
exists (unit tests skip or use fixtures otherwise). Mocks only at network
boundaries (PyPI, registries, service probes), per repo mock policy.

## Docs

README (authoring section, adaptation checklist, consumer migration notes),
docs/setup.md (init/doctor sections + troubleshooting pointers). Both human
commits. The design doc is the decision record and is already committed.

## Deferred (explicitly out of scope)

Cache GC/pruning and per-workspace cache keying (deferred from 052). Dev
image authoring automation. Recipes beyond python and dotnet-node. A
qa-stage behavior change (browser-only qa) was raised mid-design and
explicitly struck; qa semantics are unchanged by this work.
