# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The viz chrome now runs on vendored Carbon Web Components: header, banners, summary tiles, verdict tags and the stage accordion on the live page, and status filters, reset button and the runs table on the history page, are real `cds-*` components instead of hand-written equivalents. Carbon `v2.65.0` is vendored at `src/agentic_dev_loop/viz/vendor/` with a SHA-256 manifest (verified offline by `tools/vendor_carbon_wc.py --check`), served same-origin from `/vendor/`, with the Carbon theme sheet inlined ahead of the skin and `g100`/`white` theme zones driven by the existing theme toggle. The plain-HTML fallback skin is removed; Carbon is a hard vendored dependency (plan 064).
- Pinned `browser-use==0.11.13` and `pillow>=13.0.0` so a fresh pip resolve lands on a CVE-free pillow: 12.3.0 carried 39 CVEs (fixed in 13.0.0), and browser-use 0.12.0+ hard-pins pillow==12.x, which deadlocked the restore step's pip-audit gate.
- The brief coverage gate now matches required headings and anchors as line prefixes instead of whole lines: compressed content may follow an anchor on its line, and glyph variants (em dashes, curly quotes) fold to ASCII before matching. Non-ASCII characters inside required items are still reproduced character-exact, never transliterated (plan 060).
- A failing command gate (test, restore) now reaches dev as a required fix: mechanical gate output is rendered with that binding, the dev contract names both feedback channels, and a new AUDIT GATE section makes a dependency-audit failure a required manifest edit (plan 065).

## [0.15.5] - 2026-11-10

### Changed

- The run history view charts three more Loop health metrics: Audit Step Durations and Per Step Durations render as horizontal per-step duration bars with a mean-and-sample-count tooltip, and Retry Target Frequency renders as a per-target frequency bar chart.

## [0.15.4] - 2026-11-10

### Changed

- A failing command gate (test, restore) now reaches dev as a required fix: the mechanical gate output heading binds each failure as a required fix, the dev contract names both feedback channels, and a new AUDIT GATE section makes a dependency-audit failure a required manifest edit.

### Consumer adoption

Plan 065-restore-audit-dev-feedback requires consumer repos to refresh the dev agent prompt (human commit; see planning-docs/065):

- Update agentic-dev-loop/sdk_agents/dev.agent.md from the engine template at src/agentic_dev_loop/engine/onboarding/templates/dev.agent.md (or copy the engine repo copy of agentic-dev-loop/sdk_agents/dev.agent.md): the old copy claims failing command-gate output arrives under Previous attempt feedback, and it lacks the AUDIT GATE section that binds a dependency-audit failure to a manifest edit via the floor-pin pattern.
- Optionally add the audit-escalation custom_prompt to the dev step in agentic-dev-loop/pipeline.yaml (copy from the engine repo dev step): a restore or dependency-audit failure is a required manifest edit, never pre-existing or unrelated.

## [0.15.3] - 2026-11-10

### Changed

- The brief coverage gate now matches required headings and anchors as line prefixes instead of whole lines: compressed content may follow an anchor on its line, and glyph variants (em dashes, curly quotes) fold to ASCII before matching. Non-ASCII characters inside required items are still reproduced character-exact, never transliterated.

## [0.15.2] - 2026-11-10

### Changed

- The run history view moved from /runs to /history; the live viz's History link and all history page links use the new path.
- In the history view, metric drill-downs are now link-styled and gauge needles sit under the centre value in a muted colour so values stay readable.
- The live viz's history page now links back to the current run.

## [0.15.1] - 2026-11-10

### Changed

- The live viz now replays every step's console log when a tab connects or refreshes: console retention is per node (the most recent 500 lines per step, plus one bucket for unattributed output) instead of a single run-wide window, so earlier stage sections no longer show "No output".

## [0.15.0] - 2026-11-09

### Changed

- `agentic-dev-loop report` now serves the run-history browser view by default, blocking until ctrl+c and printing the URL; the ASCII markdown report moved behind `--markdown`.
- `PIPELINE` is optional and defaults to `agentic-dev-loop/pipeline.yaml` relative to the current directory; a missing default fails loudly.
- Bare `agentic-dev-loop`, `agentic-dev-loop help`, and top-level `--help`/`-h` print the command list. `help` takes no arguments.
- The run path accepts `--env FILE` to load secrets from a host env file outside the workspace; values already set in the process environment win.
- The standalone viz launcher (`python -m agentic_dev_loop.viz`) and `trigger-agentic-dev-loop.sh` are gone; doctor, upgrade, and pin commits are explicit operator steps instead of trigger orchestration.
### Consumer adoption
Consumer repos running `trigger-agentic-dev-loop.sh` switch to invoking `agentic-dev-loop` directly: PIPELINE defaults to `agentic-dev-loop/pipeline.yaml`, secrets come from the process environment or `--env FILE`, and `doctor` and `upgrade` are run explicitly; the removed orchestration (doctor gate, auto-upgrade, pin commit) has no replacement.

## [0.14.5] - 2026-11-09

### Changed

- The historical runs page now renders its rate gauges (catch rate, QA pass rate, review missed rate, red-then-green rates): the bundled charting library lacked gauge support, so those key stats rendered blank.

## [0.14.4] - 2026-11-09

### Changed

- The historical runs page now leads each category with 2-3 charted key stats (gauges, bars, big numbers) and hides the full metric set behind a collapsed details expander; per-thread metrics on the run detail page follow the same pattern, and nested dict metrics render readable entries instead of [object Object].

## [0.14.3] - 2026-11-09

### Changed

- The historical runs page now displays metric names as readable titles instead of snake_case keys, and its status filter checkboxes sit in a single row on desktop (stacking on narrow screens).

## [0.14.2] - 2026-11-09

### Changed

- Security and stability fixes.

## [0.14.1] - 2026-11-09

### Changed

- The historical runs page now applies filters automatically as you change them; the Apply and Refresh buttons are gone.
- Runs-page polish: sections sit in their own spaced panels, and dict-valued loop metrics (per-step durations, verdict distributions, etc.) render as readable lists instead of [object Object].

## [0.14.0] - 2026-11-09

### Changed

- The live viz now includes a historical runs view: browse past runs with since/until/status filters, an aggregate metrics panel, and per-run detail with brief, implementation summary, review findings, and QA artifacts, charted by a vendored ECharts bundle.
- The per-run trigger script now commits the engine pin bump after a doctor-repair upgrade, so the next run's tooling integrity check passes without manual git work.

## [0.13.0] - 2026-11-08

### Changed

- `agentic-dev-loop report` renders accumulated run history as read-only ASCII markdown: an aggregate dashboard (loop health, TDD, review, and QA metrics with provenance labels) plus a per-run deep dive, with `--since` / `--until` / `--status` filters.

## [0.12.3] - 2026-11-08

### Changed

- The live run viz now shows the LLM provider (OpenAI or Copilot) in the run summary strip, derived from the configured base_url.

## [0.12.2] - 2026-11-08

### Changed

- Agent-step verdicts now travel on a structured `record_verdict` tool call
instead of a prose `VERDICT:` line; a finished turn with zero tool calls now
fails fast instead of reading as a clean pass; the adapter validates surfaced
and outgoing tool-call shapes.
- Added `agentic-dev-loop run-status` to report the latest run.

### Consumer adoption

- Add `record_verdict` to the `tools:` allowlist of every agent backing a step
  with `verdicts:` (the shipped review and qa agents), and replace the prose
  `VERDICT:` contract in those agent prompts with a `record_verdict` tool call;
  the pipeline now fails at load when the tool is missing.

## [0.12.1] - 2026-11-06

### Changed

- Security and stability fixes.

## [0.12.0] - 2026-11-06

### Changed

- Support the GitHub Copilot subscription endpoint as an LLM backend: `copilot-login` authenticates a device flow, `copilot-models` lists the tier, and the `llm:` block accepts the Copilot base URL with a file-backed access token and the required Copilot headers. Probe-validated pins: `gpt-5.6-luna` (Responses API) and `gpt-4.1` (chat); see docs/setup.md for configuration details and known limitations.
- Completed runs now keep the visualization server alive and wait for ctrl+c after printing `Pipeline finished: press ctrl+c to exit` (skip with `--no-viz` or `--dry-run`).

## [0.11.1] - 2026-11-05

### Changed

- Security and stability fixes.

## [0.11.0] - 2026-11-04

### Changed

- Viz refresh: the pipeline visualization page is redesigned. Stage graph with flow rails and fail loopbacks, status skins including amber lighting on used fail edges, light/dark theme toggle, section-level scrolling, mobile stacking summary tiles, and thread-id click-to-copy with feedback.

## [0.10.5] - 2026-11-04

### Changed

- Add `agentic-dev-loop upgrade`: installs the latest release with pipx and
  rewrites `agentic-dev-loop/.engine-version` to the installed version, so
  the host install and the committed pin converge in one action.
- Trigger scripts (template and this repo's copy) no longer read the pin or
  downgrade the install to it; a failing `doctor --fast` gate now runs
  `agentic-dev-loop upgrade` exactly once, then fails hard if doctor still
  fails.
- doctor pin fix lines (`pin`, `pin-latest`) and the profile engine-min
  message now point at `run agentic-dev-loop upgrade`.

### Consumer adoption

Existing repos must refresh `trigger-agentic-dev-loop.sh` from the new
template (or copy it from this repo): the old script pins the install to a
stale `.engine-version` and deadlocks when the pin is behind the latest
release. Install an engine release that has the upgrade command first, then
replace the script.

## [0.10.4] - 2026-11-04

### Changed

- publish: the release-cut dry run now enforces the consumer-adoption gate, refusing entries that reference a plan with consumer obligations without a `### Consumer adoption` subsection; the tool-rules-to-profiles plan is registered and the 0.10.3 entry was curated to match
- aidlc: bare-slug code-generation plans (`unit-N-code-generation-plan.md`) now load into unit document bundles; the previous glob required an infix and never matched (latent since unit 3)
- docs: unit-9 (viz redesign) functional design artifacts, code-generation plan, and the normative values reference (tokens, layout constants, kept ids)

## [0.10.3] - 2026-11-04

### Changed

- feat: construct plan 056-tool-rules-to-profiles.md (agent dev loop)
- viz refresh docs
- Frontend rules

### Consumer adoption

Plan 056 (tool rules to profiles) requires consumer repos to make these
edits (human commits; see planning-docs/056, Consumer follow-ups):

- Add tool patterns to the brief/dev/review steps' forbidden_commands in
  agentic-dev-loop/pipeline.yaml (npm install, npm ci, dotnet add package,
  dotnet restore, dotnet add, npx playwright, playwright): the engine
  defaults no longer carry package/browser tool rules.
- Move the browser download and qa harness install into the qa step's
  setup hook (cd frontend && npm ci && npm ci --prefix qa && npx
  playwright-core install chromium-headless-shell, with
  PLAYWRIGHT_BROWSERS_PATH=qa/.browsers).
- Delete the npm ci and playwright-core install instructions from
  sdk_agents/qa.agent.md (the setup hook owns installation now).
- Fix qa/probe.mjs findHeadlessShell() to accept
  chrome-headless-shell-linux-* subdirectories.

## [0.10.2] - 2026-11-03

### Changed

- test: update trigger and viz contracts for 055

## [0.10.1] - 2026-11-03

### Changed

- feat: construct plan 055-consumer-onboarding-init-doctor.md (agent dev loop)
- Per-step config
- test: register plan 054 and 055 consumer-adoption slugs in publish.py
- Uncommitted changes: README.md, agentic-dev-loop/pipeline.yaml,
  planning-docs/055-consumer-onboarding-init-doctor.md

### Consumer adoption

Plan 055 (consumer onboarding: init + doctor) requires consumer repos to make
these edits (human commits; see planning-docs/055, Task 10). Fresh repos
should run `agentic-dev-loop init --profile dotnet-node` instead of
hand-writing the harness.

- Rewrite the trigger script to the new template shape: an
  `agentic-dev-loop/.engine-version` pin gate running `doctor --fast`, a
  one-time `pipx install --force` repair, and no `pipx upgrade`; keep the
  existing `AGENTIC_DEV_LOOP_ENV_FILE` sourcing.
- Add `profile: dotnet-node` to `agentic-dev-loop/pipeline.yaml` and delete
  the isolation and caches entries the profile now supplies.
- Add `agentic-dev-loop/.engine-version` pinned to the engine version in use.

## [0.10.0] - 2026-11-03

### Changed

- feat: construct plan 054-per-step-model.md (agent dev loop)
- docs: add plan 054 per-step model specification
- docs: mark plan 053 completed
- docs: add plan 055 consumer onboarding (init + doctor)
- docs: consumer onboarding design (init + doctor)
- Uncommitted changes: README.md, agentic-dev-loop/pipeline.yaml, planning-docs/054-per-step-model.md

## [0.9.2] - 2026-11-02

### Changed

- feat: construct plan 053-viz-stream-watchdog.md (agent dev loop)
- test: git-init docker fixtures for 052 sync-back

## [0.9.1] - 2026-11-02

### Breaking

- 052: setup hooks now run fresh at every step boundary (agent and command
  steps), targeting the per-run export, with the stamp/probe apparatus deleted
  (`--refresh`, `--clean-stamps`, `setup_probe`, `isolation.refresh`,
  `isolation.sandbox_venv_dir` all removed). Declared caches move host-side
  (`{state_dir}/caches/<name>`); the repo `.gitignore` is now the source of
  truth for export copy and sync-back, replacing the hardcoded `.venv` and
  toolchain special cases.

## [0.9.0] - 2026-11-02

0.9.0 consolidates the 1.0.0a1 through 1.0.0a51 pre-releases (plus
1.0.0rc1 and 1.0.1), a snapshot cadence cut on every commit from
2026-10-20 to 2026-11-01, into a single release. It contains every change
made since 0.3.0, re-released under a sane pre-1.0 version number. Nothing
is dropped: the full history lives in git and planning-docs/.

### Added

- Containerised execution: every step runs in an engine-managed Docker
  sandbox (multi-arch dev images, seccomp, host-user containers) with
  per-step `mounts:` and per-policy egress allowlists through an
  engine-managed proxy (plans 014-025).
- Isolation hardening: secrets store and environment ban (019),
  infra-failure classification with sticky errors and a startup reaper for
  leaked exec containers (030, 042, 050), per-proxy egress IPs (047, 048).
- Workspace sync: pipeline-declared `caches:` and `sync_excludes:` (028),
  `merge_on_conflict:` union-merge for append-style logs (029), git-aware
  sync-back with absorbed conflicts.
- New step knobs: native `brief` step type with engine-owned prompt and
  capture (033, 046, 049), stop-when-green (032, 044), custom step prompts
  (040), `reasoning_effort` per-step override (047), `--resume
  <thread-id>` (046).
- Verdict vocabulary: `unverifiable: UNVERIFIABLE` (024) and `env: ENV`
  for environmental failures (031); banned-command deny-lists for agent
  steps (036).
- Tooling: the release workflow skips already-published versions (026),
  the publish tool with Consumer adoption enforcement, and the
  core/engine/viz package split with extras inversion (a6).

### Changed

- Dropped native Windows support: the engine targets Linux and WSL2 (027).
- The package split into `core`, `engine`, and `viz`; the OpenHands-SDK
  engine moved to the `[engine]` extra (a6).

### Breaking

- Steps with no `mounts:` mount nothing (strict by default). The old
  workspace-rw plus unrestricted network maps to
  `mounts: [{path: ".", mode: rw}]` plus an `egress:` declaration (a18).
- The engine's hardcoded toolchain cache whitelist is gone: pipelines must
  declare those directories in `caches:` (a21).
- `brief` is a native step type: the old `agent: brief` shape fails
  validation naming the fix (049).
- Pipelines written for 0.2.x/0.3.x need rework against the containerised
  config; the shipped dogfood pipeline is the migration example.

### Consumer adoption

- 024: qa verdict steps must add `unverifiable: UNVERIFIABLE` to their
  `verdicts` map, and consumer qa prompt copies must gain the UNVERIFIABLE
  verdict rules (see planning-docs/024).
- 031: qa verdict steps must add `env: ENV` to their `verdicts` map
  (`on_env` unset halts the run with a report instead of a dev retry), and
  consumer qa prompt copies must gain the ENV rules and anti-livelock rule
  (plan 031, sections 3 and 5).
- 035: verdict steps should set `max_iterations: 100` and
  `finalize_nudge_turns: 10` (plan 035, D2).
- 036: agent steps should carry `forbidden_commands` deny-lists (engine
  defaults plus repo-specific additions), and the active unit should get
  its lane-boundary allowlist file (plan 036, sections 1 and 2).
- 038 C4 + 039 C7: consumer qa prompt copies must gain the persona-scope
  section (038) and the plan-mandated execution carve-out (039).
- 046 D5 + 049: delete the vendored
  `agentic-dev-loop/sdk_agents/brief.agent.md` and rewrite the brief step
  to the native shape (drop `agent:`, `produces:`, and `max_iterations:`;
  keep `brief_inputs:` with its declared `sections:`).

## [0.3.0] - 2026-10-19

### Changed

- refactor: match --aidlc-unit slugs verbatim against unit dirs; drop mangling
- Unit 3 docs fixes
- test: pin test imports to the working tree src/ (dogfood-safe)

## [0.2.0] - 2026-10-19

### Added

- `env_allowlist` pipeline config to extend the whitelisted child-process
  environment.

### Changed

- Distribution renamed from `agent-engine` to `agentic-dev-loop`; the CLI
  command renamed from `agent-engine` to `agentic-dev-loop`, the shipped
  example-pipeline directory and trigger script renamed to match, and the
  Python import renamed from `agent_engine` to `agentic_dev_loop`.
- Agent steps now capture the agent's final response as the pipeline feedback
  channel; agents no longer write artifacts through `file_editor` (the tool
  was removed).
- `tools:` is now the sole capability allowlist for agent steps; an unknown
  tool name fails to load.
- The `task_tracker` instruction was removed from the shipped `dev` agent
  definition.
- Command and action steps run subprocesses with hardened argv handling (no
  implicit shell, explicit `shell=False`) and a whitelisted child environment.
- Extension tool registration validates a non-empty identifier and rejects
  collisions with SDK-registered tools.
