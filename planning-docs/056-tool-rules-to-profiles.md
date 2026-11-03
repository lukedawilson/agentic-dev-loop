# 056: Tool rules to profiles; deny-list defaults to invariants only

> **Status: PLANNED**

Supersedes 036's default-list and network-gating decisions in part; absorbs
036's missing README deliverable (#4).

## Scope

Engine (`forbidden.py`, `stages.py`), bundled recipes (`dotnet-node.yaml`,
`python.yaml`), tests (`test_036_...`, `test_055_recipes.py`), README
(`pipeline.yaml reference`), and this repo's own dogfood pipeline
(`agentic-dev-loop/pipeline.yaml`). Consumer-repo changes are a tooling-path
checklist at the end of this plan, executed in the consumer repo separately.

## Context

Consumer run 5367f0c488d8: the QA agent flapped about its environment and
burned tokens for many turns before starting real QA. Root cause: the engine
deny-list shipped by 036 (`forbidden.py` `_DEFAULT_PATTERNS`) carries
tool-specific patterns (npm install, npm ci, pip install, uv add, pip-compile,
dotnet add package, npx playwright, bare word playwright) plus network-gated
dotnet rules, and the consumer's qa step is deliberately the network-on
browser-egress step whose own prompt instructs `npm ci` and
`npx playwright-core install chromium-headless-shell` once. The engine
defaults contradicted the consumer's sanctioned design: sanctioned commands
came back as `[forbidden: ...]` observations, and the bare-word playwright
rule hit read-only path references in ls/grep/find, so the agent spent turns
mapping the filter shape before bypassing it with a glob-expansion trick.
The browser download eventually landed in the `qa/.browsers` cache, but the
flap recurs every run until this plan lands.

User decisions, verbatim:

- "The filter is intended to guide the agent, it's not meant to be a block."
- "Why the hell is playwright mentioned at all in the engine repo????? It's
  a specific tool, it should need no special case!!!"
- "The ONLY place where specific tools should exist is in a bundled
  profile."
- "Plus git and docker" (engine defaults = git mutations + docker only).
- "Network on/off has nothing to do with tool deny-lists. It's a docker
  network-level thing." (So the coupling is removed entirely, not just the
  gated rules.)

Principles: engine defaults shrink to invariants (git mutations + docker).
Tool-specific rules belong to the toolchain profile/recipe and are declared
per step. The deny-list is network-independent by design; `network:` stays a
pure Docker/egress concern.

Open check resolved during planning: this repo's dogfood pipeline has custom
`forbidden_commands` on brief/dev/review/qa (lines 73, 104, 145, 165:
publish.py, twine, gh release/pr), and its comments claim the engine defaults
include "package installs, playwright". Brief/dev/review currently receive
package-install guidance only from the engine defaults, so trimming the
defaults silently removes that guidance unless we add tool patterns to those
steps (Decision D4).

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | `forbidden.py` defaults shrink to invariants: the six git mutations + docker. Remove npm install, npm ci, pip install, uv add, pip-compile, dotnet add package, npx playwright, playwright from `_DEFAULT_PATTERNS` | AGREED |
| D2 | Remove the network gating mechanism entirely: delete `_NETWORK_GATED_PATTERNS` and `NETWORK_GATED_FORBIDDEN_RULES`, drop the `network` parameter from `resolve_forbidden_rules`, delete the `if not network:` branch, `stages.py` stops passing `step.network`. Docstring states the deny-list is network-independent by design | AGREED |
| D3 | Bundled recipes carry tool-specific patterns as per-step `forbidden_commands` on agent steps only (brief/dev/review; command steps bypass the executor filter by design and get none). dotnet-node brief/dev/review: `npm install`, `npm ci`, `dotnet add package`, `dotnet restore`, `dotnet add`, `npx playwright`, `playwright`. python brief/dev/review: `pip install`, `uv add`, `pip-compile`. qa gets none in both recipes (the sanctioned network-on step; for dotnet-node, the browser-egress step whose setup downloads browsers) | AGREED |
| D4 | This repo's dogfood pipeline adds `pip install`, `uv add`, `pip-compile` to brief/dev/review `forbidden_commands`, and its four comments listing engine defaults are updated to "git mutations, docker" | AGREED |
| D5 | README absorbs 036's missing deliverable: a `forbidden_commands` row in the `pipeline.yaml reference` table plus a short section documenting the field, the trimmed defaults, the profile convention (tool rules live in bundled recipes), and the no-escape-hatch rule | AGREED |
| D6 | Consumer-repo follow-ups (tooling path): browser download moves into the qa setup hook; the two install instructions leave `sdk_agents/qa.agent.md`; `qa/probe.mjs` accepts the linux headless-shell layout; brief/dev/review get tool patterns in the consumer pipeline (they lose engine-default guidance under D1) | AGREED |
| D7 | Prompt-level prose stays: `env_facts`, `dev_impossible_set`, and `SANDBOX NOTES` text keep stating the sandbox truth; the deny-list is the structured complement, not a replacement | AGREED |

Recipe `forbidden_commands` needs no renderer change: `init.py` `_render`
passes recipe step dicts through wholesale (verified in 036 planning), so the
field flows into generated pipelines today. `core/config.py` already
validates the field (line 410 default `[]`, validators at 453-469: single
string coerced to list, invalid regex rejected at load).

## Changes

### 1. `src/agentic_dev_loop/engine/forbidden.py`

- Module docstring (lines 1-12): drop the "network gated" sentence; state the
  defaults are run invariants (git mutations, docker) and that tool-specific
  rules are declared per step via `forbidden_commands` (bundled recipes carry
  the engine's own toolchain guidance); note the deny-list is
  network-independent by design, network is a Docker/egress concern.
- `_DEFAULT_PATTERNS` (lines 26-42): delete the npm install, npm ci, pip
  install, uv add, pip-compile, dotnet add package, npx playwright, and
  playwright entries. Keep git commit, git push, git rebase, git checkout,
  git reset, git tag, docker.
- Delete `_NETWORK_GATED_PATTERNS` (lines 44-47) and
  `NETWORK_GATED_FORBIDDEN_RULES` (lines 57-58).
- `resolve_forbidden_rules(custom_patterns)` (lines 61-74): drop the
  `network` parameter, delete the `if not network:` branch, update the
  docstring ("Custom entries are appended after the defaults, so the first
  match wins on overlap; there is no disable escape hatch for the engine
  defaults" stays; drop the network-gated sentence).
- `match_forbidden_command` unchanged.

### 2. `src/agentic_dev_loop/engine/stages.py`

- Lines 160-161: stop passing `step.network` to `resolve_forbidden_rules`.

### 3. `src/agentic_dev_loop/engine/onboarding/recipes/dotnet-node.yaml`

Add `forbidden_commands` to the brief, dev, and review steps:

```yaml
forbidden_commands:
  - npm install
  - npm ci
  - dotnet add package
  - dotnet restore
  - dotnet add
  - npx playwright
  - playwright
```

The qa step gets no `forbidden_commands` (its setup downloads browsers and
`npm ci`s the qa harness in the network-on provision container; its env
declares `PLAYWRIGHT_BROWSERS_PATH: qa/.browsers`).

### 4. `src/agentic_dev_loop/engine/onboarding/recipes/python.yaml`

Add `forbidden_commands` to the brief, dev, and review steps:

```yaml
forbidden_commands:
  - pip install
  - uv add
  - pip-compile
```

The qa step (ro, offline) gets none.

### 5. `tests/test_036_banned_commands_and_lane_boundary.py`

- `test_defaults_cover_deny_list`: expected labels shrink to the seven
  invariants (git commit, git push, git rebase, git checkout, git reset, git
  tag, docker); call `resolve_forbidden_rules([])` with no network arg.
- Delete `test_network_on_drops_network_gated_dotnet`.
- `test_custom_rules_append_with_pattern_label`,
  `test_shell_wrapped_argv_matches`, `test_empty_argv_never_matches`: drop
  the network argument.
- `TestSandboxTerminalDenyList`:
  `test_denied_command_returns_observation_without_spawn` and
  `test_custom_regex_returns_pattern_label`: drop the network argument.
- Delete `test_dotnet_restore_denied_offline` and
  `test_dotnet_restore_allowed_online` (executor-level network-gating tests).
- `TestForbiddenCommandsConfig`, `TestPathAllowed`,
  `TestEngineAllowlistLoader`, `TestSyncBackLaneBoundary`: unchanged.

### 6. `tests/test_055_recipes.py`

Extend `test_python_recipe_gate_set_and_commands` and
`test_dotnet_node_recipe_gate_set_and_commands` (or add sibling tests) to
assert:

- dotnet-node: brief/dev/review `forbidden_commands` ==
  `["npm install", "npm ci", "dotnet add package", "dotnet restore",
  "dotnet add", "npx playwright", "playwright"]`; qa has no
  `forbidden_commands` key.
- python: brief/dev/review `forbidden_commands` ==
  `["pip install", "uv add", "pip-compile"]`; qa has none.

### 7. `README.md`

- `### pipeline.yaml reference` (line 146): add a `forbidden_commands:` line
  to the example yaml (on the dev step) and a table row.
- New short subsection (near the reference table or after "Per-stage
  networking"): field semantics (per-step regex list, single string coerced,
  invalid regex is a load error; enforced on agent terminal actions before
  any shell spawn, returning a structured `[forbidden: <label>]`
  observation; command steps are unaffected); defaults (git mutations +
  docker - run invariants); convention (tool-specific rules are declared per
  step by the toolchain profile/recipe; consumers author their own for their
  toolchain); no disable escape hatch.
- Line 488-489 prose ("npm or vitest offline, playwright downloads, ..."):
  stays accurate after D1-D3 (the recipe dev_impossible_set still lists
  playwright downloads); confirm during implementation, edit only if it
  drifts.

### 8. `agentic-dev-loop/pipeline.yaml` (dogfood, this repo)

- brief (line 73), dev (line 104), review (line 145): append to
  `forbidden_commands`:
  `pip install`, `uv add`, `pip-compile`
  (plain-word patterns, matching the recipe convention; custom label = the
  pattern text).
- qa (line 165): customs stay; no tool patterns (qa is ro and offline here).
- The four comments ("custom entries append after the engine defaults (git
  mutations, package installs, playwright, docker)") become "(git mutations,
  docker)".

### 9. `planning-docs/036-banned-commands-and-lane-boundary.md`

- Append a one-line amendment note pointing at 056: default list trimmed to
  invariants, network gating removed, tool rules moved to bundled recipes,
  README deliverable #4 landed by 056.

## Consumer follow-ups (tooling path, separate repo)

Executed in the consumer repo, never named in this repo:

1. qa setup hook becomes the single install/browser-download point:
   `cd frontend && npm ci && npm ci --prefix qa && npx playwright-core install chromium-headless-shell`
   (verify at implementation time whether step `env:` reaches the provision
   container; if not, prefix the download with
   `PLAYWRIGHT_BROWSERS_PATH=qa/.browsers` inline so the download lands in
   the declared cache).
2. `sdk_agents/qa.agent.md`: drop the `npm ci --prefix qa` instruction and
   the "install chromium-headless-shell once" instruction (lines 102-103);
   the SANDBOX NOTES section instead states the setup hook has installed the
   qa harness and the browser into `qa/.browsers`.
3. `qa/probe.mjs` `findHeadlessShell()`: accept `chrome-headless-shell-linux-*`
   subdirs (current matcher only knows the mac prefix and the exact
   `chrome-linux` name).
4. Consumer pipeline brief/dev/review steps: add tool patterns to
   `forbidden_commands` (npm install, npm ci, dotnet add package, dotnet
   restore, dotnet add, npx playwright, playwright) - under D1 they lose
   the engine-default guidance they relied on. qa gets none.

## Verification

- `pytest tests/test_036_banned_commands_and_lane_boundary.py tests/test_055_recipes.py -q`
  green after the edits; red before (TDD order per repo rules).
- Full suite: `pytest tests/ -q -m "not docker"`.
- `tests/test_repo_ascii.py` green (docs are ASCII; em dash ban applies).
- Recipe sanity: `test_both_recipes_load_and_have_steps` and
  `test_every_token_is_declared` still pass (new fields add no tokens).
- README rendering spot-check: reference table row + new subsection read
  correctly.
- Operator-observable acceptance: a consumer run's qa step proceeds without
  forbidden-command flap (the sanitized step downloads the browser in its
  setup hook, the qa agent probes directly). The flap-triggering run
  (5367f0c488d8) is the before-picture; a fresh consumer run after landing
  is the after-picture.
