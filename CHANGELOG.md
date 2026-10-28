# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0a29] - 2026-10-28

### Changed

- docs: mark 032 completed and document stop-when-green fields
- feat: construct plan 032-stop-when-green.md (agent dev loop)
- docs(dev): mandate test-first workflow in the dev prompt
- docs(plan): brief created once per run, hash-gated regeneration
- feat(dev): stop-when-green and long-running-command rules

## [1.0.0a28] - 2026-10-28

### Changed

- Fix broken prose and add planning docs
- Uncommitted changes: README.md, docs/run-time-analysis-containerised-loop.md, src/agentic_dev_loop/core/config.py, src/agentic_dev_loop/engine/isolation.py, tests/test_config.py, tests/test_isolation.py

## [1.0.0a27] - 2026-10-27

### Changed

- Dropped native Windows support. The engine now targets Linux and WSL2
  (Windows users run under WSL2, which is plain Linux). The windows-latest
  CI job and every Windows-specific code path and test were removed.

## [1.0.0a26] - 2026-10-27

### Added

- release.yml: tag pushes skip the PyPI publish and GitHub Release steps
  when the version already exists on PyPI.

### Changed

- Consumer name ban added to AGENTS.md; three docs scrubbed of
  consumer-name mentions (026 hardening).

## [1.0.0a25] - 2026-10-27

### Added

- infra-failure classification (030): daemon-side exec failures (docker exit
  125, exit 126 with an I/O error, daemon error responses) now raise
  AE-SBX-019 and are sticky for the run, failing it without an agent retry
  loop; a startup reaper removes exec containers leaked by a crashed run via a
  host-side registry + pid liveness check, and engine containers carry
  `agentic-dev-loop` labels.

### Changed

- AE-SBX-012 (daemon lost mid-run) is now sticky like AE-SBX-019: one daemon
  loss proves the exec path is dead, so every subsequent exec fails instantly.

## [1.0.0a24] - 2026-10-27

### Added

- top-level `merge_on_conflict:` config list (029): concurrent host edits to a
  declared append-style log are union-merged (host wins, export-only lines
  appended in order) instead of raising; both sides converge and the run
  continues.

### Changed

- sync-back conflicts are now announced on the console and absorbed: the
  host's version wins, the baseline is re-snapshotted before the
  `AE-SBX-016` raise, so the next sync-back runs clean instead of wedging.
- BR-4.4 amended and BR-4.6 added (029 merge semantics).

### Breaking

- `AE-SBX-016` wording now says the conflicted edits will not sync (and the
  conflict is absorbed), instead of asking the operator to resolve before
  retrying.

## [1.0.0a23] - 2026-10-27

### Changed

- test: remove flaky graph timeout e2e (Windows CI)
- Uncommitted changes: .opencode/commands/migrate-pipeline.md, src/agentic_dev_loop/engine/isolation.py, tests/test_isolation.py

## [1.0.0a22] - 2026-10-26

### Changed

- fix: make venv shebang repair separator-agnostic (Windows CI)

## [1.0.0a21] - 2026-10-26

### Added

- pipeline-declared `caches:` and `sync_excludes:` (028). `caches:` names
  repo-relative toolchain cache dirs bind-mounted rw from the real workspace
  (auto-created host-side if missing); `sync_excludes:` names scratch paths
  skipped from both the export copy and sync-back (literal component-prefix or
  `fnmatch` wildcards). Console-script shebangs baking a previous run's export
  path are repaired at startup.

### Changed

- BR-3.2 amended: a missing declared cache is auto-created (empty, uid-owned)
  and bind-mounted instead of failing loud.

### Breaking

- removed the engine's hardcoded toolchain cache whitelist (`CACHE_DIRS`);
  pipelines relying on it must declare those dirs in `caches:`. The dogfood
  pipeline already relocates its sandbox venv and declares nothing.

## [1.0.0a20] - 2026-10-26

### Changed

- fix: drain CONNECT headers before opening the egress tunnel

## [1.0.0a19] - 2026-10-25

### Changed

- fix: repair hostboundary tests for the unit-5 export model
- docs: add plan 028 pipeline-declared caches and sync excludes
- docs: apply deferred unit-3 always-on amendments; add unit-5 story-map pillar rows
- Uncommitted changes: src/agentic_dev_loop/engine/step_workspace.py, src/agentic_dev_loop/engine/sync_back.py, tests/test_step_workspace.py, tests/test_sync_back.py

## [1.0.0a18] - 2026-10-25

### Added

- unit-5 workspace egress isolation: per-step mount granularity (strict-by-default
  `mounts:` with `rw`/`ro`), per-policy egress allowlists via an engine-managed
  asyncio proxy, `.git`-free copy-based StepWorkspace, git-aware sync-back, and
  the `package-registries` preset (pypi/npm/NuGet/cargo + VCS)

### Changed

- `StepConfig` gains `mounts` + `egress` (validated at load); `network: true` now
  routes through a per-policy egress proxy (host-gateway + `HTTP(S)_PROXY` env)
- `run_agent` moved onto `Runtime` as a method (no module-level shim)
- dogfood `pipeline.yaml` declares `mounts`/`egress` per step; restore references
  the `package-registries` preset

### Breaking

- strict-by-default mounts (NFR-5.3): a step with no `mounts:` mounts nothing.
  Add explicit `mounts:` to each step - old default `{workspace}` rw +
  unrestricted `network: true` maps to `mounts: [{path: ".", mode: rw}]` plus an
  `egress:` declaration. The shipped dogfood pipeline is updated as the
  migration example.

## [1.0.0a17] - 2026-10-25

### Changed

- docs(planning): add 027 artifact-injection plan; QA probe-plan rules
- Audit: record combined scrub+docs commit 3479d71
- chore: scrub commercial name; add unit-5 ERD, step-execution flowchart, and fold-in docs
- Audit: record planning-docs commit 47777fd
- Unit-5 FD+NFR+ID docs, code-gen plan, unit-6/7 intents, name scrub
- docs(planning): add planning conventions (tooling-path deliverables)
- Uncommitted changes: docs/vps-dev-feasibility.md, planning-docs/027-inject-stage-artifacts-into-agent-contexts.md, src/agentic_dev_loop/engine/graph.py, src/agentic_dev_loop/engine/stages.py, tests/test_graph.py, tests/test_qa_prompt.py, tests/test_stages.py

## [1.0.0a16] - 2026-10-24

### Changed

- feat: construct plan 025-browser-ready-sandbox-config.md (agent dev loop)
- release: v1.0.0a15
- feat(024): ship tooling changes for UNVERIFIABLE verdict
- docs(aidlc): add construction-phase block for /unit-status, flip plan 024 to in progress
- feat: construct plan 024-qa-unverifiable-verdict.md (agent dev loop)
- docs: add plan 025, unit 5 post-intent findings, plan 023 follow-up pointers
- Uncommitted changes: README.md, planning-docs/025-browser-ready-sandbox-config.md

## [1.0.0a15] - 2026-10-24

### Changed

- feat(024): ship tooling changes for UNVERIFIABLE verdict
- docs(aidlc): add construction-phase block for /unit-status, flip plan 024 to in progress
- feat: construct plan 024-qa-unverifiable-verdict.md (agent dev loop)
- docs: add plan 025, unit 5 post-intent findings, plan 023 follow-up pointers

## [1.0.0a14] - 2026-10-24

### Changed

- feat: construct plan 023-sandbox-browser-render-hardening.md (agent dev loop)
- docs(planning): add plan 023 sandbox browser render hardening
- fix(dev-dotnet): add fontconfig and fonts
- feat: enforce the em dash ban at test time and commit time
- chore: scrub em dash characters from all tracked files
- fix: pin explicit UTF-8 I/O for Windows CI and ban em dashes
- Uncommitted changes: planning-docs/024-qa-unverifiable-verdict.md

## [1.0.0a13] - 2026-10-23

### Changed

- Mark plans as completed
- fix(trigger): require exactly one loader flag, safe under set -u
- Uncommitted changes: src/agentic_dev_loop/engine/cli.py, src/agentic_dev_loop/engine/isolation.py, src/agentic_dev_loop/engine/loaders.py, tests/test_cli.py, tests/test_isolation.py, tests/test_isolation_integration.py, tests/test_loaders.py

## [1.0.0a12] - 2026-10-23

### Changed

- feat: construct plan 022-sandbox-venv-boundary-enforcement.md (agent dev loop)
- fix(tests): run trigger-script tests through bash for Windows
- chore: drop tracked .review-venv junk and ignore it
- chore: ignore .tmp-exec pytest tmpdir
- docs: record 022 bootstrap pip-hygiene findings
- fix(pipeline): upgrade pip before restore to keep pip-audit project-scoped
- fix(trigger): converge on latest prerelease reliably
- fix(trigger): upgrade pipx-managed engine install on every run
- Uncommitted changes: agentic-dev-loop/pipeline.yaml, aidlc-docs/construction/plans/unit-2-code-generation-plan.md

## [1.0.0a11] - 2026-10-23

### Changed

- feat: construct plan 021-supply-chain-dependency-dial.md (agent dev loop)
- Uncommitted changes: README.md, agentic-dev-loop/pipeline.yaml, agentic-dev-loop/sdk_agents/dev.agent.md, planning-docs/022-sandbox-venv-boundary-enforcement.md, trigger-agentic-dev-loop.sh

## [1.0.0a10] - 2026-10-23

### Changed

- feat: construct plan 020-host-code-execution-isolation.md (agent dev loop)
- docs: pin README install to 1.0.0a9 + uv --refresh note

## [1.0.0a9] - 2026-10-23

### Changed

- feat: construct plan 019-secrets-store-and-env-ban.md (agent dev loop)
- docs: mark planning docs 001-018 completed
- docs: add security plans 019-021 + unit-5 intent
- fix(pipeline): break dependency-cache deadlock with restore step
- fix: install the engine non-editable in setup-venv bootstrap
- Uncommitted changes: planning-docs/019-secrets-store-and-env-ban.md

## [1.0.0a8] - 2026-10-22

### Changed

- docs: pin README install to 1.0.0a8
- feat: construct plan 017-remove-unit-4-adapter.md (agent dev loop)

## [1.0.0a7] - 2026-10-22

### Changed

- feat: construct plan 016-sandbox-venv-hardening.md (agent dev loop)
- docs: split sandbox-venv hardening plan from unit-4 removal (016/017) + revert agent edits
- docs: wontship unit-4 Claude adapter - removal plan 016 + bookkeeping

## [1.0.0a6] - 2026-10-22

### Changed

- feat: unit-4 Claude Dynamic Workflows driver - hard module break: the flat
  `agentic_dev_loop` package splits into `core` (pipeline model + parse +
  workflow projection), `engine` (graph/stages/sandbox/CLI), and `viz`
  (live-graph server + workflow ingest). No re-export shims.
- feat: build-time workflow projection - `project_workflow`, prompt-reference
  validation, deterministic `render_workflow_js`, and the docker-free commit
  shim; skill payload (`/agentic-dev-loop`) ships in the wheel.
- feat: extras inversion - base install is `core` + `viz` (pydantic + PyYAML
  only); the heavy OpenHands-SDK engine moves to the `[engine]` extra
  (`[workflow]` is the base). The generated workflow JS is a real wheel-shipped
  template implementing the deterministic loop (routing, attempt counter,
  retry-note injection, clean-at-start, viz launch, gated commit), pinned by
  golden-render tests; `viz` is now stdlib-only (no LangChain renderer).

### Migration (breaking)

- `agentic_dev_loop.config` → `agentic_dev_loop.core.config`
- `agentic_dev_loop.results` → `agentic_dev_loop.core.results`
- `agentic_dev_loop.outputs` → `agentic_dev_loop.engine.outputs`
  (`Verdict`/`VerdictError` → `agentic_dev_loop.core.verdict`)
- `agentic_dev_loop.viz` → `agentic_dev_loop.viz` (now a package; the
  `viz.py` module moved to `viz.server`)
- `agentic_dev_loop.{graph,stages,isolation,cli,llm,actions,agents,registry,loaders,proc,dryrun}` → `agentic_dev_loop.engine.*`
- `agentic_dev_loop.extensions.aidlc` → `agentic_dev_loop.engine.extensions.aidlc`
  (update the `extensions:` string in your pipeline.yaml)

## [1.0.0a5] - 2026-10-20

### Changed

- fix: load extensions once across parse, dry-run, and run
- Add dev-dotnet sandbox image: dotnet 10.0.402 + jq/unzip/lsof for dotnet pipelines

## [1.0.0a4] - 2026-10-20

### Changed

- fix: harden sandbox request VO and kernel-proof network-none test
- feat: construct plan 014-per-stage-networking.md (agent dev loop)

## [1.0.0a3] - 2026-10-20

### Changed

- docs: plan per-stage networking (014)
- fix: cap agent-client-protocol below 1.0.0 - its rc drops acp.helpers
- Fix opencode publish command double-cutting

## [1.0.0rc1] - 2026-10-20

### Changed

- Update tagline

## [1.0.0a2] - 2026-10-20

### Changed

- feat: construct plan 015-container-lifecycle-rm-fix.md (agent dev loop)
- docs: plan 015 - container lifecycle --rm cleanup fix

## [1.0.0a1] - 2026-10-20

### Changed

- feat: construct plan 014-dry-run-flag-plan.md (agent dev loop)
- fix: sandbox HOME=/tmp for host-user containers (root's home is unwritable)
- ci: run docker integration tests on Linux only (Windows runners have no Docker daemon)
- test: skip POSIX-only killpg test on Windows
- fix: close the six Windows-CI failures from the unit-3 dogfood run
- fix: make product and suite Windows-CI green; run sandbox as host user
- fix: re-pin dev image digest after multi-arch rebuild (amd64+arm64)
- ci: build the dev image for linux/amd64 and linux/arm64
- docs: plan --dry-run validation flag (unit-3 dogfood)
- fix: pin published dev-image digest (KD-2)
- feat: construct unit unit-3 (agent dev loop)

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

