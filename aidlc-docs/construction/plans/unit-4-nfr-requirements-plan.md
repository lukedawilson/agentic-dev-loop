# Unit 4 NFR Requirements Plan - Claude Dynamic Workflows driver

Status: in progress. Next stage after NFR Requirements = Infrastructure Design (NFR Design skipped per project precedent).

## Stage context

Functional Design approved (2026-10-21). Unit-4 = build-time parse + viz from Claude Dynamic Workflows; module split `core`/`engine`/`viz`; skill `/agentic-dev-loop` + workflow JS template; no Docker in workflow mode; BR-1..13 + KD-1..10 in place. NFR analysis focuses on: install/dependency strategy, backward compatibility of the module split, performance of build/run, reliability of the workflow-mode pieces (viz, state), auditability without the engine audit JSONL, testability without docker.

## Positioning (user motivation, 2026-10-21)

Primary use-case: developers who know and love the agentic dev loop but are forced to use Claude Code at work, and want their "home comforts". What unit-4 does: export a Claude Dynamic Workflow from the pipeline.yaml the dev already knows and trusts; give the dev a visualisation they like and trust. NOT trying to provide sandboxing or extra security - "we can't". All we really add is the deterministic loop through Claude's mechanism. → The deterministic loop (projection → generated JS routing) IS the product; everything else stays minimal.

## Open questions

### NFR-1 - Dependency strategy for workflow-mode installs

[Answer]: A - extras split. `pip install agentic-dev-loop[workflow]` installs `core` + `viz` only (pydantic, PyYAML, stdlib) without openhands-sdk/docker-touching deps; the default full install keeps everything for engine mode. (Raw: "A")

### NFR-2 - Backward compatibility of the module split

[Answer]: B - hard break now (no shims): clean split with no re-export shims; consumers (this repo's + the consumer repo's pipeline.yaml `agentic_dev_loop.extensions.aidlc` strings, test imports) update to the new module paths immediately. Accepted pre-1.0 since the package is still alpha (1.0.0a5). AGENTS.md backward-compat rule satisfied by documenting the exact migration (list of old→new import paths) in the release notes/docs. (Raw: "B")

### NFR-3 - Performance budget (build time, step overhead)

[Answer]: A - practical targets, no gates: projection <1s; workflow adds no overhead beyond Claude Code's own subagent spawn; viz POST <100ms localhost; measured, not CI-enforced (unit-3 precedent). (Raw: "A")

### NFR-4 - Reliability (viz death mid-run, workflow-state durability)

[Answer]: B, REFINED (2026-10-21, YAGNI) - the journal mirror (`.agentic-dev-loop/workflow-state.json`, one write per step) is written ONLY IF workflow-mode resume is actually in scope; otherwise no journal at all - Claude Code owns workflow state, viz death degrades gracefully (events dropped, run continues). Raw: "Wait - only write journal state if we plan to actually do something with it (e.g. support --resume within Claude Code)". Follow-up resolved: "B" - resume is OUT of unit-4 scope ⇒ NO journal. Design stays journal-ready (resume feature would bring the journal with it).

### NFR-5 - Auditability in workflow mode (no engine audit JSONL)

[Answer]: A - Claude Code's own records are the audit trail; nothing extra written by the engine/skill. (Raw: "A")

### NFR-6 - Testability + CI (core/viz without docker)

[Answer]: A - core/viz unit + PBT suites run in the existing CI jobs (no docker needed); golden tests for JS template rendering (projection → generated JS) guard the deterministic loop - the one thing we claim to add; real Claude Code workflow E2E = manual smoke. (Raw: "For NFR-6, do A")

## Steps

- [ ] Analyze FD artifacts for NFR implications
- [x] Resolve open questions (NFR-1..NFR-6, inline one at a time)
- [x] Generate artifacts: nfr-requirements.md + tech-stack-decisions.md under aidlc-docs/construction/unit-4/nfr-requirements/
- [ ] Security-baseline disposition (blocking set)
- [ ] Completion message + approval
