# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
