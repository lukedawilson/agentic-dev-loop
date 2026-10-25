# Unit 4 NFR Requirements - Claude Dynamic Workflows driver

## Positioning (user motivation, 2026-10-21)

Primary use-case: developers who know and love the agentic dev loop but are forced to use Claude Code at work - unit-4 exports a Claude Dynamic Workflow from the pipeline.yaml they already trust and gives them the visualisation they trust. We are NOT adding sandboxing or extra security - we can't. All we add is the deterministic loop through Claude's mechanism. Everything in this stage is judged against that: minimal footprint, no features without a use.

## NFR requirements

| ID | Category | Requirement |
| --- | --- | --- |
| NFR-4.1 | Dependencies | **Extras split.** `pip install agentic-dev-loop[workflow]` = `core` + `viz` only (pydantic/PyYAML, stdlib) - no openhands-sdk, no docker. Full install unchanged for engine mode. The skill's `install-skill` documents both paths. |
| NFR-4.2 | Backward compatibility | **Hard break now** (pre-1.0 alpha). No re-export shims; module paths change cleanly (`config` → `core.config`, engine modules → `engine.*`, `viz` → `viz.*`). Release notes carry a full old→new import migration list, including the `agentic_dev_loop.extensions.aidlc` string in existing pipeline.yaml files (this repo, the consumer repo). |
| NFR-4.3 | Performance | Practical targets, no CI gates: projection <1s; workflow execution adds no measurable overhead beyond Claude Code's own subagent spawn; viz ingest POST <100ms localhost; SSE latency as today. Measured during development, not enforced. |
| NFR-4.4 | Reliability | Viz server death mid-run degrades gracefully - events are dropped, the workflow continues, the run never fails because of viz. NO journal mirror; workflow-mode resume is OUT of scope (YAGNI - Claude Code owns workflow state). Design stays journal-ready so a future resume feature brings the journal with it. |
| NFR-4.5 | Auditability | Claude Code's own records (transcripts, session history) ARE the workflow-mode audit trail. Nothing extra is written by the engine or skill. |
| NFR-4.6 | Security | No new security claims - workflow steps run in Claude Code's own environment under its permission model (documented trust-model change). The only surfaces we own stay hardened: viz binds localhost, ingest token-authenticated, SSE read-only tokenless; commit shim keeps trusted hooksPath + clean-at-start gating. |
| NFR-4.7 | Testability | `core`/`viz` unit + PBT suites run in the existing CI jobs with no docker. Golden tests pin the JS template rendering (projection → generated JS) - the deterministic loop is the product, so its rendering is regression-guarded. Real Claude Code workflow E2E = manual smoke (not CI-able). |
| NFR-4.8 | Usability | One command to install the skill (`agentic-dev-loop install-skill`); invoking `/agentic-dev-loop` walks the dev through build (parse → generated workflow → created in CC); familiar viz; build failures are loud and actionable (missing prompts named - BR-4). |

## Category dispositions

| NFR category | Disposition |
| --- | --- |
| Scalability | N/A - single dev, single workflow per pipeline |
| Performance | Applicable - NFR-4.3 (practical, ungated) |
| Availability | N/A - local, on-demand |
| Security | Applicable but deliberately minimal - NFR-4.6 (no new claims; owned surfaces stay hardened) |
| Reliability | Scoped - NFR-4.4 (graceful degradation only; no journal/resume) |
| Maintainability | Covered - module split + guard test (FR-4.1), golden tests (NFR-4.7) |
| Usability | Applicable - NFR-4.8 |

## Security-baseline blocking set dispositions

Consistent with the FD business-rules mapping: Enforced 02, 03, 05, 06, 07, 09, 10, 11, 12, 13, 15 (localhost viz + token ingest, CC permission model, build-time validation, hooksPath hygiene, wheel-shipped skill version-stamped, fail-closed prompts/commits, guard-tested boundaries, token-gated writes, secure-by-default ignores, documented trust model). N/A 01, 04, 08, 14 (no new secret storage, no new deps, no network segmentation claims, no new logging surface).

No blocking security findings.
