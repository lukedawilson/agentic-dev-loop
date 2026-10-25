# AI-DLC Audit Log

## Workspace Detection - Initial Request
**Timestamp**: 2026-10-17T10:58:41Z
**User Input**: "Ok, we're going to address this intent in two stages: 1. Harden the existing tooling injection via what the OpenHands SDK provides 2. Introduce proper kernel-level isolation. First, I want you to bring in AI-DLC - take it from ~/dev/agentic-dev-template" - then "Don't need a port plan, just do it" - then "1. Commit and push AI-DLC stuff 2. Start the ai-dlc flow specifying the SDK-level hardening and kernel-level isolation as a guardrails and sandboxing intent"
**AI Response**: Ported AI-DLC workflow from ~/dev/agentic-dev-template (commit b6e70ee, pushed to origin/main). Started AI-DLC workflow per core-workflow.md: loaded mandatory common rules (process-overview, session-continuity, content-validation, question-format-guide), loaded 4 extension opt-in files (domain-standards, resiliency-baseline, security-baseline, property-based-testing - all have .opt-in.md, none always-enforced), displayed welcome message.
**Context**: INCEPTION - Workspace Detection. Intent recorded: (1) SDK-level hardening of existing tooling injection; (2) kernel-level isolation for guardrails and sandboxing of agent-engine.

## Workspace Detection - Findings
**Timestamp**: 2026-10-17T10:58:41Z
**User Input**: N/A (automated stage execution)
**AI Response**: Scan complete. aidlc-docs/aidlc-state.md does not exist (new project assessment). Existing code found: Python library (agent-engine, src/agent_engine/, ~2,650 LOC across 13 modules), pyproject.toml (setuptools), LangGraph + OpenHands SDK stack, tests/ suite (16 test files), example consumer in agent-engine/. brownfield = true. No reverse engineering artifacts in aidlc-docs/inception/reverse-engineering/ → next phase = Reverse Engineering.
**Context**: INCEPTION - Workspace Detection. aidlc-docs/ directory structure created (inception/{plans,requirements,user-stories,application-design,reverse-engineering}, construction/{plans,build-and-test}, operations).

---

## Workspace Detection - Completion Message Presented
**Timestamp**: 2026-10-17T10:58:41Z
**User Input**: N/A (automated stage execution)
**AI Response**: Presented welcome message + workspace detection completion message. Brownfield project, Reverse Engineering needed, no artifacts present. Proceeding automatically to Reverse Engineering per workspace-detection.md Step 6.
**Context**: INCEPTION - Workspace Detection → Reverse Engineering transition.

---

## Reverse Engineering - Executed
**Timestamp**: 2026-10-17T10:58:41Z
**User Input**: N/A (automated stage execution)
**AI Response**: Read all 13 library modules + pyproject.toml + pipeline.yaml + 3 agent files. Generated 8 artifacts in aidlc-docs/inception/reverse-engineering/ (business-overview, architecture, code-structure, api-documentation, component-inventory, technology-stack, dependencies, code-quality-assessment) + reverse-engineering-timestamp.md. Updated aidlc-state.md. Key finding for the intent: tool injection is prompt-level allowlisting over a full-authority terminal; shell surfaces = terminal tool, command steps (shell=True), actions.py subprocess calls. Presented completion message; waiting for user approval per reverse-engineering.md Step 13.
**Context**: INCEPTION - Reverse Engineering.

---

## Requirements Analysis - Q1 answer
- 2026-10-17T11:05:00Z - Q1 (Stage 1 scope): **C** - Everything: SDK surface, command steps, action-step subprocess calls, extension tool registration

## Requirements Analysis - Q2 answer
- 2026-10-17T11:12:00Z - Q2 (Stage 2 platform): **C** - container runtime. "C is better, because we will want to support Linux (and even, sigh, Windows)". Docker Engine API as abstraction; runtime per platform.

## Requirements Analysis - Q3–Q7 answers
- 2026-10-17T11:20:00Z - Q3 (isolation scope): **B** - terminal tool + command steps; everything else host-side (trusted code)
- Q4 (profile defaults): DEFERRED to FD. Intent: isolation OPTIONAL, no forced container use
- Q5 (compat posture): Stage 2 = opt-in (user statement). Stage 1 = pending Q5a
- Q6 (unavailable-isolation failure mode): DEFERRED to FD (kernel unit)
- Q7 (granularity): two+ units - Unit 1 SDK hardening FIRST, Unit 2+ kernel isolation (intent only here)
- 2026-10-17T11:25:00Z - Q5a (stage 1 posture): **A** - default-on / always-on ("Always-on"); per-pipeline opt-out escape hatches
- 2026-10-17T11:30:00Z - Q5 CORRECTION: Stage 1 = always-on with NO opt-out flags ("We're guardrailed by design")
- 2026-10-17T11:30:00Z - Q6 (unavailable isolation): **A** - fail fast, refuse to run
- 2026-10-17T11:35:00Z - Security extension: **A** - enforce as blocking constraints. Full rules loaded (deferred rule loading per requirements-analysis.md Step 5.1)
- 2026-10-17T11:40:00Z - Domain Standards extension: **A** - enforce as blocking. Full rules loaded
- 2026-10-17T11:45:00Z - Resiliency extension: **A (scoped)** - "keep it light. This is running on someone's desktop... we don't need the full server treatment (load, etc.)". Retry/recovery guidance in scope; server load/HA/DR out. Full rules loaded
- 2026-10-17T11:52:00Z - PBT extension: **B (Partial)** - pure functions + serialization round-trips only. Full rules loaded

## Requirements Analysis - Q11-follow-up (file_editor gap)
- 2026-10-17T12:00:00Z - Follow-up (file_editor host-side surface): **A** - drop file_editor entirely. User rationale: it exists only to write the agent's report, and that mechanism "has failed anyway, since it often forgets to write the report. So we need a better mechanism for it to feed back its findings." → new requirement (Q12).

## Requirements Analysis - Q12 (findings-feedback mechanism)
- 2026-10-17T12:05:00Z - **A** - pipeline captures the agent's final SDK response and writes the artifact itself; verdict parsing reads from the captured response. NOTE: parsing-logic details deferred to Functional Design (existing parsing has had bugs - dedicated FD discussion). Capture mechanism itself = Stage-1 requirement.

## Requirements Analysis - Step 7-8 executed
- 2026-10-17T12:10:00Z - requirements.md generated at aidlc-docs/inception/requirements/requirements.md (intent analysis + FR/NFR tables incorporating all Q1-Q12 answers; Unit 1 always-on, Unit 2 opt-in; extensions scoping recorded). aidlc-state.md Stage Progress updated.
- 2026-10-17T12:10:00Z - Review requested (Step 9 completion message presented). Waiting for user approval.

## Requirements Analysis - APPROVED (user directives)
- 2026-10-17T12:15:00Z - User approved requirements with three directives:
  1. UNIT REORDER: the findings/verdict-passing mechanism (Q12) becomes UNIT 1, executed BEFORE tool-hardening - "It's a genuine bug-in-waiting and should be fixed first."
  2. User Stories: skipped (confirmed - no need).
  3. Units must be VERTICAL SLICES (each unit delivers end-to-end user-visible behavior through the stack), not horizontal layers.
- Proceeding to Workflow Planning.

## Workflow Planning - executed
- 2026-10-17T12:20:00Z - execution-plan.md generated at aidlc-docs/inception/plans/execution-plan.md (risk Medium; US/NFRD skip; AD/UG/FD/NFRA/ID/CG/BT execute; unit order Unit1→Unit2→Unit3 vertical slices). aidlc-state.md updated with Execution Plan Summary. Review requested.

## Workflow Planning - Approval
**Timestamp**: 2026-10-17T12:25:00Z
**AI Prompt**: "Ready to proceed with this plan?"
**User Response**: "Three stages: 1. Fix the way output is passed between stages (and then remove file writing tools from agents that no longer need them) 2. SDK-level hardening 3. Kernel-level isolation"
**Status**: Approved (user restated the three units; Unit 1 explicitly includes removing file-writing tools - file_editor - from agents after the output-passing fix)
**Context**: Workflow plan created with 7 stages to execute, 2 skipped

## Application Design - plan created
- 2026-10-17T12:30:00Z - application-design-plan.md created (mandatory artifacts checklist + 6 Domain Exposition questions Q-AD1..Q-AD6). Presenting questions inline per AGENTS.md override.

## Application Design - Domain Exposition complete
- 2026-10-17T12:45:00Z - Q-AD1..Q-AD6 all answered **A** (with recommendations accepted):
  1. New feedback-domain component (outputs.py-style): capture + verdict parsing together
  2. Pipeline writes captured response to existing artifact path; build_context untouched
  3. Tool restrictions = .agent.md frontmatter only
  4. New isolation component: IsolationBackend protocol + DockerBackend impl
  5. FakeBackend as test seam; real Docker only in opt-in integration tests
  6. Source-agnostic pure parser: parse(text) -> Verdict
- Proceeding to Step 10: generate application design artifacts (components.md, component-methods.md, services.md [descent audit], component-dependency.md, application-design.md).

## Application Design - artifacts generated
- 2026-10-17T12:55:00Z - Generated components.md, component-methods.md, services.md (descent audit), component-dependency.md, application-design.md (consolidated). All six Q-AD decisions incorporated (all A). Review requested.

## Application Design - DDD-rule amendments applied (user approved)
- 2026-10-17T13:05:00Z - /load-backend-rules loaded coding-standards-ddd.yaml; conflicts flagged vs AD; user accepted all revision recommendations. Applied:
  1. Q-AD4: IsolationBackend protocol REPLACED by single concrete `Sandbox` type (Docker Engine API; platform variance internal). Interface extracted only when a second backend (native Linux namespaces) lands.
  2. Q-AD5: FakeBackend seam REMOVED (testability-driven structure forbidden); pure parts unit-tested, real Docker in opt-in integration tests.
  3. Q-AD6: parser returns typed Result[Verdict] (NO_VERDICT/AMBIGUOUS) - no exceptions for expected conditions.
  4. services.md descent audit: Outputs/IsolationProvider services REMOVED (one caller = no service) → CapturedOutput.from_agent_result static constructor, Sandbox.for_config static method. Zero new services.
  5. requirements.md: open question added - Unit 3 FD "Linux: Docker vs native namespace isolation".
- Files amended: application-design-plan.md, components.md, component-methods.md, services.md, component-dependency.md, application-design.md, requirements.md.

## Application Design - Domain Model APPROVED
- 2026-10-17T13:20:00Z - User approved the amended domain model ("I'm happy with this domain model - approved"). Change-surface Mermaid class diagram embedded in application-design.md Domain Shape section. User requested a full-pipeline diagram (all components, change-marked) for reference; per-unit FD docs will reference the approved diagram.

## Full-pipeline domain diagram recorded
- 2026-10-17T13:30:00Z - Full-pipeline Mermaid class diagram (all components, change-marked by unit) recorded in component-dependency.md. User-requested caveat included: parts of the EXISTING unchanged model are known to need refactoring under a future intent (outside this scope); diagram records today's structure for traceability. Proceeding to Units Generation.

## Units Generation - Part 1 Planning answers
- 2026-10-17T13:45:00Z - U-1 unit naming: **A** - numeric `unit-1`/`unit-2`/`unit-3` (matches ai-dlc loader `construction/unit-<N>/` expectation; reconciles the ported-commands layout gap)
- 2026-10-17T13:46:00Z - U-2 story-map source: **A** - map FR/NFR IDs to units; no stories generated
- 2026-10-17T13:47:00Z - U-3 Unit 2 scope: **A** - confirm mapping (FR-1.1, FR-1.4, FR-1.5, FR-1.6, FR-1.7); command-step hardening stays in Unit 2

## Units Generation - Part 1 approval
- 2026-10-17T13:50:00Z - Approval prompt: "Unit of work plan complete. Review the plan in aidlc-docs/inception/plans/unit-of-work-plan.md. Ready to proceed to generation?"
- 2026-10-17T13:50:00Z - User response: "Yes"

## Units Generation - Part 2 Generation executed
- 2026-10-17T14:00:00Z - Generated unit-of-work.md (3 units: unit-1 output-passing fix, unit-2 SDK hardening, unit-3 kernel isolation; responsibilities + key domain objects + out-of-scope), unit-of-work-dependency.md (matrix + mermaid + coupling rules + validation), unit-of-work-story-map.md (all FR/NFR mapped to units; no unassigned). Plan checkboxes [x], state updated. Review requested.

## Units Generation - Approval
- 2026-10-17T14:05:00Z - User response: "Agreed" (approving units generation, proceeding to CONSTRUCTION PHASE)

## Construction - unit-1 Functional Design answers
- 2026-10-17T14:20:00Z - FD-1 capture scope: **A** - capture replaces exactly produces:/artifact: channels; other terminal-produced files untouched
- FD-2 verdict semantics: **A** - last-VERDICT-line-wins + decoration tolerance + caveat handling; typed Result[Verdict] (NO_VERDICT/AMBIGUOUS), no exceptions
- FD-3 truncation: **A** - verdict-first truncation for verdict channel; summary keeps full response; build_context truncates to budget
- FD-4 missing/empty response: **A** - attempt failure, increment attempt, retry per existing policy
- FD-5 YAML: **A** - implicit capture, schema unchanged, no opt-out

## Construction - unit-1 Functional Design artifacts generated
- 2026-10-17T14:30:00Z - Generated business-logic-model.md, business-rules.md, domain-entities.md under aidlc-docs/construction/unit-1/functional-design/. DDD descent audit passed (VOs own parse/write; StagesRuntime delegates only; no new services). PBT-01 properties identified. Review requested.

## Construction - unit-1 Functional Design approval
- 2026-10-17T14:35:00Z - User response: "Approve and continue" (unit-1 FD approved; proceeding to NFR Requirements)

## Construction - unit-1 NFR Requirements generated
- 2026-10-17T14:55:00Z - NFR answers: NFR-1=A (generic Result[T,E]), NFR-2=A (hypothesis dev dep), NFR-3=A (no new data-protection requirements). Generated nfr-requirements.md (5 NFRs; scalability/performance/availability/maintainability/usability N/A per user scoping; security rule dispositions table) + tech-stack-decisions.md (5 decisions, 1 deferred item) under aidlc-docs/construction/unit-1/nfr-requirements/. Review requested.

## Construction - unit-1 NFR Requirements approval
- 2026-10-17T15:00:00Z - User response: "Approve and continue" (unit-1 NFR Requirements approved; proceeding to Infrastructure Design)

## Construction - unit-1 Infrastructure Design generated
- 2026-10-17T15:10:00Z - All 7 infrastructure categories N/A with justification (unit-1 = in-process data-flow change, desktop library; zero infrastructure footprint). Generated infrastructure-design.md + deployment-architecture.md under aidlc-docs/construction/unit-1/infrastructure-design/; unit-3 infra (daemon socket, mounts, images) explicitly deferred. No open questions. Review requested.

## Construction - unit-1 Infrastructure Design approval
- 2026-10-17T15:20:00Z - User response: "Approve and continue" (unit-1 Infrastructure Design approved; proceeding to Code Generation)

## Construction - unit-1 Code Generation plan
- 2026-10-17T15:30:00Z - Logging approval prompt: unit-1 code-generation plan created at aidlc-docs/construction/plans/unit-1-code-generation-plan.md (13 TDD steps; brownfield: modify stages.py + 2 agent files + pyproject; create results.py/outputs.py + 4 test files; delete verdicts.py; N/A steps documented). Awaiting user approval of the plan (covers entire generation sequence).

## Construction - unit-1 code-generation plan approval
- 2026-10-17T15:40:00Z - User response: "Yes, record it as a note. Then TDD unit 1, red/green/refactor" - self-hosting note recorded in plan; plan APPROVED; proceeding to Part 2 (Generation) via strict TDD.

## Construction - unit-1 Code Generation complete
- 2026-10-17T16:30:00Z - All 13 plan steps executed TDD. Full suite 373 passed. Files: created results.py/outputs.py + 4 test files + code docs; modified stages.py, 3 agent files, pyproject.toml, conftest.py, 3 test files; deleted verdicts.py + test_verdicts.py. Review requested.

## Construction - unit-2 Functional Design session resumption
- 2026-10-18T00:00:00Z - Resumed workflow. aidlc-state.md current stage: CONSTRUCTION - unit-1 complete, next = unit-2 Functional Design. Loaded: common rules (process-overview, session-continuity, content-validation, question-format-guide), unit-of-work.md, unit-of-work-story-map.md, unit-of-work-dependency.md, requirements.md, domain-standards + security-baseline + coding-standards-ddd extensions, and unit-1 FD artifacts (format reference). Reverse-engineered current code: agents.py (load_agent), registry.py (add_tool), config.py, actions.py, stages.py (_run_command), sdk_agents/*.agent.md, plus SDK AgentDefinition/Agent schema + register_tool + agent_definition_to_factory.
- 2026-10-18T00:00:00Z - User request (raw): "./trigger-agent-engine.sh --ai-dlc-unit U2" failed with FileNotFoundError (no construction/unit-2/ docs); user selected "Run Unit 2 Functional Design now".

## Construction - unit-2 Functional Design answers
- 2026-10-18T00:00:00Z - FD-1 task_tracker: **B** - drop the instruction; dev stays terminal-only (least-privilege; pipeline owns cross-step progress). User later confirmed "drop task list tool".
- FD-2 tool set: **B** - capabilities + FinishTool + ThinkTool. `tools:` = exact capability allowlist; FinishTool (done signal) + ThinkTool (reasoning scaffold) always present as infra. Empty `tools:` → think + finish only. User confirmed "keep think tool available".
- FD-2b filter_tools_regex: **A** - drop it; explicit allowlist is the whole story.
- FD-3 command shell: **A** - hybrid (shlex argv when no metacharacters, shell=True for complex, env hygiene in both).
- FD-4 reserved names: **A** - collision = any name in the SDK global registry (list_registered_tools) + non-empty identifier.
- FD-5 action subprocess: **B** - deeper (pid-token numeric validation + shutil.which binary resolution + explicit shell=False + env hygiene).

## Construction - unit-2 Functional Design artifacts generated
- 2026-10-18T00:00:00Z - Generated business-logic-model.md, business-rules.md, domain-entities.md under aidlc-docs/construction/unit-2/functional-design/. DDD descent audit passed (all new behaviour = step 0 static helpers or step 2 existing-entity methods; no new VO/entity/service; no single-impl interface; no persistence). PBT-02 properties identified. **Scope amendment**: FR-1.1 reduced by FD-2/FD-2b - no filter_tools_regex, no include_default_tools frontmatter knob (requirements.md FR-1.1 + unit-of-work.md "new frontmatter fields" wording to be amended). Review requested.

## Construction - unit-2 Functional Design approval
- 2026-10-18T00:00:00Z - User response: "Approved" (unit-2 FD approved; proceeding to NFR Requirements). Scope amendment applied: requirements.md FR-1.1 + unit-of-work.md unit-2 responsibility/key-domain-object wording updated to the reduced tool-surface scope.

## Construction - unit-2 NFR Requirements answers
- 2026-10-18T00:00:00Z - NFR-1 environment hygiene: **C** - Hybrid. Fixed known-safe whitelist (PATH + minimal vars) + author-extensible allowlist (new optional config key); secrets excluded by default.

## Construction - unit-2 NFR Requirements artifacts generated
- 2026-10-18T00:00:00Z - Generated nfr-requirements.md (5 NFRs: env hygiene, input validation, fail-closed, PBT, backward compat; scalability/performance/availability/maintainability/usability N/A; security dispositions: SECURITY-05/06/12/15 applicable, rest N/A) + tech-stack-decisions.md (6 decisions: no new deps, whitelist+allowlist env, proc module, add_tool validation, shutil.which, optional config key; 3 deferred to unit-3) under aidlc-docs/construction/unit-2/nfr-requirements/. FD artifacts updated (child_env → whitelist+allowlist; BR-7 → one optional additive key). Review requested.

## Construction - unit-2 NFR Requirements approval
- 2026-10-18T00:00:00Z - User response (raw): "Include OPENAI_API_KEY by default in the allowlist. Then approve and continue" - NFR-1 refined: the LLM API key (`llm.api_key_env`) is **included by default** in the child env (not scrubbed). Env-allowlist key pinned as `env_allowlist: list[str] = []` (pipeline-level, optional, additive). Artifacts updated (nfr-requirements.md, tech-stack-decisions.md, FD business-rules/logic-model/domain-entities). NFR Requirements APPROVED; proceeding to Infrastructure Design.

## Construction - unit-2 Infrastructure Design generated
- 2026-10-18T00:00:00Z - All 7 infrastructure categories N/A with justification (unit-2 = in-process host-side hardening, desktop library; zero infra footprint). Generated infrastructure-design.md + deployment-architecture.md under aidlc-docs/construction/unit-2/infrastructure-design/; container infra (daemon socket, mounts, image toolchain) explicitly deferred to unit-3. No open questions. Review requested.

## Construction - unit-2 Infrastructure Design approval
- 2026-10-18T00:00:00Z - User response: "Approve and continue" (unit-2 Infra Design approved; proceeding to Code Generation). User addition (raw): "Unit also includes updating the example agent Def front matter so it will still work" - unit-2 scope now explicitly includes updating/verifying the shipped example agent definitions (`agent-engine/sdk_agents/*.agent.md`) so they remain valid under the new tool-surface semantics (BR-1) and the FR-1.4 change.

## Construction - unit-2 Code Generation plan
- 2026-10-18T00:00:00Z - Logging approval prompt: unit-2 code-generation plan created at aidlc-docs/construction/plans/unit-2-code-generation-plan.md (14 TDD steps; brownfield: create proc.py + 2 test files; modify registry.py, stages.py, actions.py, config.py, agents.py docstring, dev.agent.md; no new runtime deps; FR-1.1 = test + docstring only, no load_agent logic change). Awaiting user approval of the plan (covers the entire generation sequence).

## Construction - unit-2 Code Generation - delegated to user's dev loop
- 2026-10-18T00:00:00Z - User response (raw): "I'll run the dev loop myself, so just create the artefacts and stop" - unit-2 code generation (Part 2) is delegated to the user's dev loop (self-hosting via `./trigger-agent-engine.sh --ai-dlc-unit U2`). Assistant creates design/plan artefacts only and writes no application code. All unit-2 design artefacts (FD, NFR, Infrastructure Design) + the code-generation plan are in place; the dev agent executes the plan's 14 TDD steps.

## Construction - unit-2 capture-path defects fixed (blocking the dev loop)
- 2026-10-19T00:00:00Z - Root-caused why the dev loop retried to exhaustion: `_final_agent_text` read `conv.events`, which the SDK never provides (the events live on `conv.state.events`), so every agent response was captured empty ("no final response"). Fixed by delegating to the SDK's own `get_agent_final_response(conv.state.events)`; added a real-Conversation integration test (`TestLLM` scripted to finish) so synthetic fixtures can't mask it again. Also fixed `--max-attempts` being dropped on `--resume` (re-applied via `graph.update_state`). Both committed (`34b4891`, `4062fdf`).

## Construction - unit-2 Code Generation complete (dev loop succeeded)
- 2026-10-19T00:00:00Z - Dev loop completed on attempt 16 (first with the capture fix): dev → test → review → qa all passed, qa VERDICT: PASS, outcome=success. Full suite green: 391 passed. Auto-commit skipped by policy (worktree dirty at run start) - unit-2 code committed manually by user. Files: new `src/agent_engine/proc.py` + `tests/test_proc.py`; modified `registry.py`, `actions.py`, `stages.py`, `config.py`, `agents.py`, `sdk_agents/dev.agent.md`, and test files.

## Construction - unit-3 Functional Design session resumption
- 2026-10-19T09:54:51Z - Resumed workflow. aidlc-state.md current stage: CONSTRUCTION - unit-2 complete (approved 2026-10-19), next = unit-3 Functional Design. Loaded: core-workflow.md, session-continuity.md, functional-design.md rules; unit-of-work.md (unit-3 definition), unit-of-work-story-map.md (FR-2.1–2.6 assignments), requirements.md (FR-2.1–2.6), unit-3-functional-design-plan.md (domain exposition drafted, FD-1–FD-5 open), unit-2 FD artifacts (format reference). Extensions enabled per state: domain-standards, security-baseline (scoped), resiliency (scoped), PBT (partial).
- 2026-10-19T09:54:51Z - User request (raw): "continue unit 3" - proceeding with unit-3 Functional Design stage; presenting FD open questions inline, one at a time, writing answers back to the plan after each.

## Construction - unit-3 Functional Design answers
- 2026-10-19T09:54:51Z - FD-1 default isolation profile: **A** - network-egress blocked + writes restricted to the bind-mounted workspace/state_dir (most conservative default).
- 2026-10-19T09:54:51Z - FD-2 Linux backend: **B** - Docker on macOS/Windows + native namespace isolation on Linux. Two backends ⇒ shared Sandbox interface extracted now; FR-2.2 scope amendment required (abstraction = Sandbox surface; Docker Engine API = macOS/Windows backend). Follow-up FD-2b raised re: native-backend toolchain source.
- 2026-10-19T09:54:51Z - User input (raw): "Make sure we have some way to test the linux side - I don't have a linux box at the moment" - Linux-testability constraint recorded; resolution drafted in plan (pure-function unit tests run everywhere; native-backend integration tests run on a GitHub Actions Linux runner and/or an optional local lima VM; macOS dev loop exercises the Docker backend only; backend-selection logic unit-tested via fake availability probe).
- 2026-10-19T09:54:51Z - FD-2b native toolchain: **A** - host toolchain inside namespaces; no image on Linux (matches the no-daemon motivation).
- 2026-10-19T09:54:51Z - FD-2c Linux failure mode: **A** - native-only on Linux, fail fast; explicit backend-selection knob in config feeds FD-4.
- 2026-10-19T09:54:51Z - FD-3 preliminary: User response (raw): "(1) but commit is also inside (it's a destructive action). What do you mean by 'action steps'?" - user wants the commit action inside the boundary; asked for clarification of step-type terminology. Assistant explained step types from code (agent/command/action; actions.py = commit, kill_listeners_on_port, git_worktree_clean); follow-up FD-3b raised.
- 2026-10-19T09:54:51Z - FD-3b: User response (raw): "No, in that case, the commit ACTION doesn't need to be 'in'. What's necessary is to block the agents doing arbitrary commits themselves unless we allow it" - commit action stays host-side (FR-2.6 unchanged); NEW requirement: the sandbox profile blocks agent-initiated git mutations inside the sandbox by default, with an author-controlled allow mechanism. Follow-up FD-3c raised (block scope + allow shape). Residual risk accepted + recorded: agent-influenced git hooks can still execute host-side during the trusted commit action.
- 2026-10-19T09:54:51Z - FD-3c git-mutation block: **A** - block all repo-mutating git operations by default (commit, push, reset, branch/tag create/delete, stash, merge, rebase, worktree add …); reads always allowed; one profile knob re-allows mutations per-pipeline.
- 2026-10-19T09:54:51Z - FD-4 always-on directive: User response (raw): "Sandboxing is always-on. We're not giving developers a foot-gun here, this is an opinionated harness that runs stuff in isolation. If you don't like it, use claude code and have your agents hack your bank account if you want :-)" - **MAJOR SCOPE CHANGE**: isolation is mandatory, not opt-in. Consequences recorded: FR-2.3 becomes "no usable runtime ⇒ fail fast always" (hard prerequisite for every run, including the self-hosted dev loop); unit-of-work.md unit-3 "OPT-IN - not requested ⇒ behavior unchanged" wording overturned; NFR-1 backward-compat now = pipeline semantics unchanged but a sandbox runtime is a new environment requirement; config section becomes always-on sandbox configuration (not an opt-in flag); fail-fast messages must name the missing runtime and how to install it. FD-4 re-posed accordingly.
- 2026-10-19T09:54:51Z - FD-4b config defaults: **A** - optional `isolation:` section with opinionated defaults (default profile, pinned default image, auto-detected backend with override knob); sandbox active regardless.
- 2026-10-19T09:54:51Z - FD-5 testability: **A** - fake at the Sandbox interface level; pure translation logic tested everywhere; real-backend integration tests marked and opt-in (Docker: daemon-present machines; native: CI Linux runner / lima VM), never silently skipped. All FD questions answered; proceeding to artifact generation.

## Construction - unit-3 Functional Design artifacts

- 2026-10-19T10:20:54Z - Generated the three functional-design artifacts under `aidlc-docs/construction/unit-3/functional-design/`: business-logic-model.md (core flow, FD key-decisions table, algorithms incl. pure translation functions, data-flow mermaid, PBT-03 property table), business-rules.md (BR-1..BR-12 + Security-baseline mapping), domain-entities.md (trace list, Sandbox entity + two backends, request/result VOs, modified PipelineConfig/_run_command/terminal-tool wiring, descent audit, FR/NFR traceability, deferred amendments).
- 2026-10-19T10:20:54Z - DDD validation passed (descent audit in domain-entities.md; no service/VO abuse; single extracted interface justified by two day-one backends). Security-baseline compliance check passed (mapping table in business-rules.md; SECURITY-01/02/04/08/14 N/A). PBT properties identified (PBT-03 invariants + PBT-02 round-trips in business-logic-model.md).
- 2026-10-19T10:20:54Z - Plan title/unit-context amended from opt-in to always-on per FD-4; all plan checkboxes marked [x]. Awaiting user review/approval of the functional design.

## Construction - unit-3 Functional Design approved
- 2026-10-19T10:24:25Z - User approval (raw): "Approved, btw" - unit-3 Functional Design approved; stage complete. aidlc-state.md updated: unit-3 IN PROGRESS, FD approved, next = NFR Requirements.

## Construction - unit-3 NFR Requirements started
- 2026-10-19T12:26:17Z - NFR Requirements stage started. FD analysis found two FD-internal conflicts to resolve: BR-9.1 vs BR-9.2 (sandbox env = unit-2 child_env which includes the LLM key by default, vs "no secrets enter the sandbox") and BR-1.3 per-step lifecycle vs Docker container startup cost. Repo has no CI workflows; FD BR-12.4 promises the Linux workflow ships with unit-3. Plan created at aidlc-docs/construction/plans/unit-3-nfr-requirements-plan.md with 6 open questions (NFR-1 secrets-in-sandbox, NFR-2 lifecycle, NFR-3 daemon transport, NFR-4 native kernel-feature strictness, NFR-5 toolchain delivery with network off, NFR-6 Windows CI).
- 2026-10-19T12:32:00Z - NFR-1 answered. User (raw): "I thought we decided this already! The pipeline.yaml steps get respective env sections with the env vars their corresponding steps can see". Follow-up (per-step env doesn't exist today; unit-2 shipped global env_allowlist): **A** - per-step `env:` mapping on StepConfig (VAR: value | null=inherit). Resolved: sandbox env = fixed safe base + declared env only; env_allowlist never leaks into sandbox; LLM key never auto-included inside; host-side steps keep child_env unless they declare env. Resolves BR-9.1/BR-9.2.
- 2026-10-19T12:34:00Z - NFR-2 answered: **A** - persistent per run. Docker: lazy container at first sandboxed exec, `docker exec` per request, destroyed at pipeline end + error paths; native: bwrap per request. Per-command statelessness preserved; lifecycle backend-internal. BR-1.3 amended (per-request exec, per-run container lifecycle).
- 2026-10-19T12:36:00Z - NFR-3 answered: **A** - `docker` CLI subprocess via `shutil.which("docker")`; CLI handles unix socket/npipe/WSL2/contexts; zero new runtime dependencies. Structured data via CLI output (create/start return codes + IDs, `docker inspect --format`).
- 2026-10-19T12:38:00Z - NFR-4 answered: **A** - tiered. bwrap + unprivileged user namespaces mandatory (fail fast with install instructions); Landlock best-effort (kernel ≥ 5.13, else loud one-time warning); seccomp via bwrap's built-in `--seccomp` filter, no libseccomp dependency.
- 2026-10-19T12:52:00Z - NFR-5 discussion. User (raw): "Let's stop and discuss - this is a significant architectural fork". Assistant decomposed the fork (provisioning source vs runtime policy; agent-vs-command asymmetry). User (raw): "The code could be anything - nodejs, react, C#, we're not platform-specific. So all the sandboxes need to be able to somehow access a working dev environment. We could give each step in pipeline.yaml a pre-command or hook which prepares everything and gives it to the sandbox" with `setup: npm i && npm run build` / `setup: dotnet build` examples.
- 2026-10-19T12:58:00Z - NFR-5 refined design agreed. User confirmed: (1) setup runs inside the backend environment, never host (ABI correctness); (2) separate provision container with network ON vs execution container `--network none`; (3) one pinned dev image (python+node+git) + per-pipeline `image:` override; (5) accepted risk: agent can mutate setup outputs in the rw workspace, containment still holds. Caching (point 4) open - user asked "why not just leverage docker's caching?".
- 2026-10-19T13:00:00Z - MAJOR DIRECTIVE. User (raw): "Also, drop native linux sandboxing from this intent (but keep the interface)". Docker becomes the only backend on all platforms; Sandbox interface retained as extension seam. Consequences: BR-3.2 removed; backend-selection simplifies (no `backend:` override knob); bwrap_argv/landlock_ruleset/seccomp_policy dropped; NFR-4 withdrawn; Linux CI = Docker-backend tests on ubuntu-latest. FD amendments to be applied to business-rules/domain-entities/business-logic-model during NFR artifact generation.
- 2026-10-19T13:06:00Z - NFR-5 caching answered. User (raw): "Ok, stamp-caching: skip on match, and a pipeline-level isolation.refresh (or CLI --refresh) flag forces re-provision." Docker builder layer-cache rejected (shadowing, fragile auto-staging, deps hidden in images); stamp = runtime analogue of layer caching, keyed by setup cmd + lockfile hashes, stored in state_dir. NFR-5 fully resolved.
- 2026-10-19T13:10:00Z - NFR-6 answered: **A** - add Windows CI job (windows runner with Docker Desktop) running Docker-backend integration tests; Linux job = Docker-backend tests on ubuntu-latest. All NFR open questions resolved (NFR-4 withdrawn per native-drop directive). Proceeding to FD amendments + NFR artifact generation.

## Construction - unit-3 NFR Requirements artifacts
- 2026-10-19T13:24:00Z - Generated `aidlc-docs/construction/unit-3/nfr-requirements/nfr-requirements.md` (NFR-3.1..NFR-3.10 + category dispositions + Security-baseline blocking-set dispositions) and `tech-stack-decisions.md` (TS-1..TS-9 + deferred items). Security-baseline compliance: no blocking findings.
- 2026-10-19T13:24:00Z - FD artifacts amended in place per NFR-stage decisions (user directives logged above): native backend dropped (BR-3/BR-4, domain-entities, business-logic-model), BR-1.3 persistent-per-run (NFR-2), BR-9 per-step env (NFR-1), new BR-13 setup hook (NFR-5), BR-12 CI ubuntu+windows (NFR-6), SECURITY-07 mapping updated. Amendment banner added to all three FD artifacts.
- 2026-10-19T13:24:00Z - Plan checkboxes complete. Awaiting user review/approval of NFR requirements.

## Construction - unit-3 NFR Requirements approved
- 2026-10-19T13:30:12Z - User approved NFR requirements; raw input: "approve and continue". Proceeding to Infrastructure Design.

## Construction - unit-3 Infrastructure Design started
- 2026-10-19T13:41:15Z - Started Infrastructure Design stage. Plan created at aidlc-docs/construction/plans/unit-3-infrastructure-design-plan.md (logical components listed; 7-category applicability analysis: Deployment PARTIAL, Compute YES, Storage YES, Messaging N/A, Networking YES, Monitoring PARTIAL, Shared YES). Repo confirmed PUBLIC (lukedawilson/agent-engine) - GHCR anonymous pulls viable.
- 2026-10-19T13:42:30Z - ID-1 dev image source: **A** - own agent-engine/dev image published to GHCR via CI, digest-pinned, rebuilt on toolchain bumps.
- 2026-10-19T13:43:45Z - ID-2 engine baseline: **A** - any compliant daemon with engine API version floor (≥1.43/Engine 24); Windows: OSType probe fails fast in Windows-containers mode with switch instructions.
- 2026-10-19T13:45:00Z - ID-3 state_dir: **A** - ~/.agent-engine/state (per-user); one stamp per setup key under state_dir/stamps/; manual CLI clean command; provision outputs stay in workspace.
- 2026-10-19T13:46:15Z - ID-4 resource caps: **A** - default caps on both containers (execution 4g/2cpu, provision 8g/4cpu), per-pipeline override via isolation section.
- 2026-10-19T13:48:00Z - ID-5 audit sink: **A** - JSONL at state_dir/sandbox-audit.jsonl (per-execution records) + console via existing logger; 5MB rotation with one rolled file; host-side only.
- 2026-10-19T13:49:30Z - ID-6 CI runners: **A** - GitHub-hosted ubuntu-latest + windows-latest; CI publishes dev image to GHCR and runs Docker-backend integration tests. All 6 ID questions answered; proceeding to artifact generation.

## Construction - unit-3 Infrastructure Design artifacts
- 2026-10-19T13:55:00Z - Generated infrastructure-design.md (component→infra mapping, category dispositions, engine baseline, image/supply chain, resource caps, storage layout, audit sink, container hardening flag table, CI, open CG considerations) and deployment-architecture.md (topology + runtime-sequence mermaid, per-OS environment matrices, fresh-machine steps, failure-mode table).
- 2026-10-19T13:55:00Z - shared-infrastructure.md warranted: dev image + GHCR registry + CI workflows + per-user state dir are repo-wide shared infra (units 1-2 had none). Created at aidlc-docs/construction/shared-infrastructure.md.
- 2026-10-19T13:55:00Z - Plan checkboxes complete. Awaiting user review/approval of infrastructure design.
- 2026-10-19T14:05:00Z - Change request during infra-design review. User raw: "Also need to provide proper setup instructions for the users' agents, since setup is more complex (e.g. on setting up the dev images, writing the pipeline.yaml file, etc.)". ID-7 answer **A**: user onboarding guide ships WITH the repo (docs/setup.md linked from README); outline fixed in infra design, written at Code Generation. Artifacts amended (infrastructure-design.md new deliverable section + outline; deployment-architecture.md points fail-fast messages at the guide).
- 2026-10-19T14:20:00Z - Change request during infra-design review. User: "does docker truly count as 'kernel-level isolation'?" then "Can we accurately call it kernel-level sandboxing?" Assistant answer: YES - kernel-level sandboxing is accurate (kernel enforces namespaces/cgroups/seccomp/caps); kernel-level isolation would overclaim (separate kernel = VM/gVisor class). ID-8 resolved: terminology amended to "kernel-level sandboxing" across construction docs + state; threat-model section added to infra design; inception-doc wording (requirements.md, unit-of-work.md, unit-4 doc) queued as deferred amendments.

## Construction - unit-3 Infrastructure Design approved
- 2026-10-19T14:08:00Z - User approved infrastructure design; raw input: "Approve and continue". Proceeding to Code Generation.

## Construction - unit-3 Code Generation Part 1 (planning)
- 2026-10-19T14:15:00Z - Code generation plan created at aidlc-docs/construction/plans/unit-3-code-generation-plan.md: 16 numbered TDD steps, story traceability (FR-2.1..2.6, NFR-3/4/6/7, BR-1..13), 8 locked key decisions (KD-1 single isolation.py module; KD-2 image digest bootstrap w/ AGENT_ENGINE_DEV_IMAGE override; KD-3 dual state dirs; KD-4 SDK terminal executor seam + per-run re-registration; KD-5 fake Sandbox in unit tests, marked docker integration; KD-6 --refresh/--clean-stamps; KD-7 root-in-container; KD-8 sleep infinity exec target). Files: created isolation.py + 3 test files + Dockerfile + 2 workflows + docs/setup.md + code/ summary; modified config/stages/agents/graph/cli + 6 test files + COMPAT-SHIMS. actions.py explicitly unchanged.
- 2026-10-19T14:15:00Z - Approval prompt presented to user for the code generation plan (Part 1). Waiting for explicit approval before writing any code.
- 2026-10-19T14:26:00Z - User response to code-generation plan approval (raw): "Get the docs ready for the agent-engine to implement, but do NOT write code." Directive: prepare implementation documentation as the handoff for a future implementing agent; NO code files written. Code execution deferred.

## Construction - unit-3 Code Generation docs handoff
- 2026-10-19T14:35:00Z - Implementation handoff spec created at aidlc-docs/construction/unit-3/code/implementation-spec.md (docs-first directive): exact isolation.py API surface (VOs, 9 pure functions, Sandbox ABC + DockerBackend, probe, audit/stamp helpers, constants incl. KD-2 digest bootstrap), 6 docker command templates (probe/ensure_image/exec-container/exec/provision/destroy incl. exit-124 timeout convention + /usr/bin/timeout requirement), config schema (IsolationConfig extra=forbid + StepConfig.env/setup), AE-SBX-001..012 error catalog pointing at docs/setup.md, SDK terminal seam (SandboxTerminalExecutor + per-run re-registration + COMPAT-SHIMS entry spec), Runtime/stages/cli wiring detail, ci.yml + publish-image.yml + Dockerfile specs, docs/setup.md 6-section outline, full test plan (unit/PBT/marked-docker/sdk-compat), guardrails. Plan file annotated with STATUS note (code execution deferred to implementing agent). No code files written.

## Construction - unit-3 loop-mechanics hardening (docs-first amendments)
- 2026-10-19T14:52:00Z - User intent (raw): "I don't want insurance, I want to be sure an agent won't break the loop mechanics mid-run, or start sandboxing before it has all the right setup etc."
- 2026-10-19T14:52:00Z - Q1 loop-mechanics exposure: **A** - (1) sandbox mount set = workspace ONLY (iso state dir unmounted: stamps/audit become host-only, unreachable); (2) PipelineConfig.state_dir default relocates to user-level ~/.agent-engine/runs/<pipeline-hash>/ (out of the mount entirely; explicit state_dir configs keep working). Remaining decisions pending: eager-vs-lazy startup setup; commit-action trusted hooksPath.
- 2026-10-19T14:55:00Z - Q2 startup setup: **A** - eager startup: probe → digest-verified image → create+start execution container → pre-provision every declared setup (stamp-cached, --refresh respected) → then build graph and run steps. Zero infra operations mid-run; mid-run failure = step result or loud daemon death.
- 2026-10-19T14:57:00Z - Q3 commit hooks: **A** - trusted commit action runs `git -c core.hooksPath=<agent-engine-managed trusted dir>`; FD BR-7 residual reclassified accepted→mitigated. All three hardening decisions made; applying one consistent amendment batch to implementation-spec.md, CG plan, FD/infra/NFR artifacts, and test plan.
- 2026-10-19T15:00:00Z - User requirement (raw): "Also, the plan needs to involve updating pipeline.yaml in this repo so it works with this Brave New World" - the repo's own pipeline must keep working under always-on sandboxing (state_dir relocation, per-step env/setup, git-mutation policy, sandboxed command steps). Adding pipeline.yaml migration to the plan + spec.

## Construction - unit-3 package-rename amendment batch
- 2026-10-19T18:45:00Z - User: "Yes, fix the unit 3 docs" (and confirmed OPENAI_API_KEY is exported in ~/.zshrc - LLM requirement satisfied). Applied docs-only rename batch: `src/agent_engine` → `src/agentic_dev_loop`; `agent-engine` → `agentic-dev-loop` (incl. default image `ghcr.io/lukedawilson/agentic-dev-loop/dev`, matching the renamed GitHub repo); `AGENT_ENGINE_DEV_IMAGE` → `AGENTIC_DEV_LOOP_DEV_IMAGE`; `~/.agent-engine/*` → `~/.agentic-dev-loop/*`. Files: implementation-spec.md, 3 FD artifacts, 2 NFR artifacts, 2 infra artifacts, shared-infrastructure.md, unit-3 CG plan, unit-3 infra plan, aidlc-state.md (descriptive lines only - raw user quotes and workspace path untouched). Unit-1/2 construction docs left as historical records (ID-8 precedent). Verified: zero old-name hits in unit-3 docs.

## Construction - unit-3 marked COMPLETE; unit-4 kickoff
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - User: "Mark unit 3 as complete - tested it thoroughly in the consumer repo repo and it's working. Then onto unit 4. The two parts of agentic-dev-loop that we can drive from Claude Dynamic Workflows are: Parsing the pipeline.yaml file and creating the workflow steps from it; The visualiser web ui. We cannot do docker-sandboxing from Claude Code and won't try to. Proposal: Split the pipeline model, engine and viz into separate, composable modules; Build a claude skill, /agentic-dev-loop, with a bundled workflow js file, which uses the pipeline model and viz"
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - aidlc-state.md updated: unit-3 → COMPLETE (Code Generation + Build and Test checked; duplicate empty Code Generation line removed); Unit Order extended with unit-4; Current Stage = Unit 4 Requirements Analysis in progress; unit-4 section added with scope + stage checkboxes.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Requirements Analysis Q1 (command steps in workflow mode) answered: "A" - subagents execute command steps in workflow mode; each command step becomes a subagent step running the command in Claude Code's own environment, reporting exit code/verdict; no engine process at runtime. Answer written to plans/unit-4-requirements-plan.md.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Requirements Analysis Q2 (module split boundaries) answered: "A but call it .core instead of .model. wdyt? If you don't like it, push back" - three-module split accepted with light module named agentic_dev_loop.core (user's naming call; 'model' collides with LLM-model vocabulary); creep risk mitigated via documented module contract + guard test. Assistant accepted with that one caveat.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Requirements Analysis Q3 (workflow JS mechanism) answered: "B" - parse at runtime via a subagent; bundled workflow JS is generic (step 1 = parse subagent runs the .core one-liner, emits steps JSON into workflow state; subsequent steps read it); covers mid-run edits + shared pipelines.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Requirements Analysis Q4 (action/commit steps in workflow mode) answered: "Ok, C" - small trusted CLI shim reusing the engine's commit logic without the sandbox (clean-at-start, hooksPath, templated message extracted into a docker-free callable); workflow commit step = subagent invoking the shim.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Requirements Analysis Q5 (skill+JS vs MCP architecture) answered: "Yes, drop MCP (Recommended)" - skill + bundled workflow JS only; subagents invoke core/viz via shell/python; intent doc's ae_* MCP-tool recommendation amended out of unit-4 scope.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Requirements Analysis Q6 (visualiser in workflow mode) answered: "A" - subagent-launched live server (free-port fallback, URL into chat/state); step subagents POST stage events to a new local HTTP ingest endpoint; browser keeps live SSE; engine mode keeps in-process VizBus unchanged.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Requirements Analysis Q7 (verdict protocol/retry loops in workflow mode) answered: "A - Same verdict contract (Recommended)" - verdict agents still write VERDICT: PASS/FAIL artifacts; subagent steps report verdicts in final output; workflow JS routes on_pass/on_fail/goto with own max_attempts counter.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Requirements Analysis artifacts generated: requirements.md amended (Stage 4 FR-4.1..4.9 + summary item 4); requirement-verification-questions.md appended (Q1-Q7 with [Answer]: tags); unit-4-intent-claude-code-backend.md amended (MCP dropped, pillar 2 revised, skill+JS architecture); aidlc-state.md unit-4 Requirements Analysis [x]. Awaiting approval.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Requirements Analysis APPROVED: "approve and continue".
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Q3 REVERSAL (post-approval): "You know what - I changed my mind. pipeline.yaml should be parsed at build time, not runtime" - FR-4.3 amended to build-time parse (skill bundles a workflow JS template; Claude with tools imports .core and bakes steps into the generated JS at workflow creation; runtime never parses). Applied across requirements.md, summary item 4, intent-doc amendment, plans/unit-4-requirements-plan.md Q3, requirement-verification-questions.md Q3.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Functional Design STARTED: plans/unit-4-functional-design-plan.md created (unit context, DDD domain exposition, FD-1..FD-5 open questions, step checkboxes).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 FD-1 (projection shape) answered: "B / Should check the agent prompts exist and fail if they don't" - embed step data, reference prompts (.agent.md re-read at spawn); NEW requirement: build-time prompt-existence check (core projection validates agent prompt files resolve, fails loudly if missing - engine-mode fail-fast mirror).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 FD-2 (retry loop) answered: "A" - explicit retry note: JS attempt counter in workflow state; failing subagent's final output = retry note injected into next dev attempt's prompt (engine notes-channel mirror).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 FD-3 (commit clean-at-start in workflow mode) answered: "A" - capture at workflow start: first JS step records git status --porcelain emptiness → commit_allowed in workflow state; commit shim gates on it. Exact engine semantics.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 FD-4 (viz ingest + URL flow) answered: "A" - token-authenticated ingest: server-generated token into workflow state; subagents POST with header; read-only SSE view tokenless; localhost-bound.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 FD-5 (skill packaging + build trigger) answered: "A " - wheel-shipped skill (SKILL.md + workflow JS template + docs in PyPI wheel); agentic-dev-loop install-skill copies to ~/.claude/skills/agentic-dev-loop/; /agentic-dev-loop → Claude follows SKILL.md → python -m agentic_dev_loop.core.project → writes generated workflow JS → creates CC workflow; JS header records core version + pipeline hash.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 FD artifacts generated under aidlc-docs/construction/unit-4/functional-design/: business-logic-model.md (build/run flows, KD-1..10, pure functions, mermaid, PBT targets), business-rules.md (BR-1..13 + security-baseline mapping: Enforced 02/03/05/06/07/09/10/11/12/13/15, N/A 01/04/08/14), domain-entities.md (trace list, WorkflowProjection/generated-JS/IngestToken/commit-shim, descent audit 11 rows all Correct, FR-4.1..4.9 traceability, deferred metering). Plan checkboxes complete; awaiting review.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Functional Design APPROVED: "approved". Next stage = Unit 4 NFR Requirements.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 NFR Requirements STARTED: plans/unit-4-nfr-requirements-plan.md created (NFR-1..NFR-6 open questions, step checkboxes).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 NFR-1 (dependency strategy) answered: "A" - extras split; agentic-dev-loop[workflow] = core+viz only (no openhands-sdk); full install unchanged for engine mode.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 NFR-2 (backward compat of module split) answered: "B" - hard break now, no re-export shims; consumers update import paths (incl. pipeline.yaml extensions strings in this repo + the consumer repo); migration list documented in release notes; accepted pre-1.0 (alpha).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 NFR-3 (performance budget) answered: "A" - practical targets, no CI gates: projection <1s; workflow adds no overhead beyond CC subagent spawn; viz POST <100ms localhost; measured not enforced.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 NFR-4 (reliability) answered: "B" - journal mirror `.agentic-dev-loop/workflow-state.json` in the workspace, one write per step (step id/status/verdict/timestamp/attempt/retry note); survives CC session loss; doubles as the workflow-mode audit trail (NFR-5 folds in).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - NFR-4 REFINEMENT: "Wait - only write journal state if we plan to actually do something with it (e.g. support --resume within Claude Code)" - journal mirror only if workflow-mode resume is in unit-4 scope; otherwise no journal, graceful degradation only. Follow-up question asked: is workflow-mode resume in scope?
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 NFR-4 finalised: "B" - workflow-mode resume OUT of unit-4 scope; journal dropped (YAGNI - only write state if we do something with it); viz death = graceful degradation; design stays journal-ready for a future resume feature.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 NFR-5 (auditability in workflow mode) answered: "A" - Claude Code's own records (transcripts/session logs) are the audit trail; no engine-written journal or summary.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 POSITIONING (user motivation, raw): "Primary use-case: developers who know and love the agentic dev loop, but are forced to use Claude Code at work and want some of their 'home comforts'. What it does: Export a Claude Dynamic Workflow from the pipeline.yaml file that the dev already knows and trusts; Give the dev a visualisation they like and trust. We're not trying to provide sandboxing or extra security - we can't. All we really add is the deterministic loop through Claude's mechanism." Recorded in plan doc as Positioning section.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 NFR-6 answered: "For NFR-6, do A" - core/viz unit+PBT in existing CI jobs; golden template-render tests guarding the deterministic loop; real CC workflow E2E = manual smoke.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 NFR artifacts generated: nfr-requirements.md (positioning + NFR-4.1..4.8 + category dispositions + security blocking-set dispositions, no blocking findings) and tech-stack-decisions.md (TS-1..8 + deferred list) under aidlc-docs/construction/unit-4/nfr-requirements/. Awaiting approval.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 NFR Requirements APPROVED: "approved". Next stage = Unit 4 Infrastructure Design.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Infrastructure Design STARTED: plans/unit-4-infrastructure-design-plan.md created (packaging & distribution scope; ID-1..ID-5 open questions; step checkboxes).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 ID-1 (installer entry point) answered: "A (posix and powershell)" - separate console script agentic-dev-loop-workflow in the workflow extra, with both posix and powershell entry points; entry-point boundary mirrors module boundary, guard-testable.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - ID-1 REFINEMENT: "Well, only separate scripts if necessary - the point is we need to support Windows as well" - answer reframed requirement-first: Windows support mandatory (posix+powershell); separate script only where necessary - and it IS necessary because the light workflow install (core+viz, no openhands-sdk) forces extras inversion (base = light, [engine] extra = main CLI). Base ships agentic-dev-loop-workflow (posix+powershell); [engine] adds agentic-dev-loop.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 ID-2 (generated workflow JS placement) answered: "A" - stable workspace file .agentic-dev-loop/workflow.js consumed by CC creation flow.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 ID-3 (skill layout + upgrade behaviour) answered: "A" - idempotent overwrite; install-skill copies SKILL.md + JS template to ~/.claude/skills/agentic-dev-loop/, version marker file; skill files never user-edited.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 ID-4 (CI wiring) answered: "B" - dedicated workflow-mode job on ubuntu+windows: install [workflow] extra only → project fixture pipeline → render → golden compare; proves no hidden engine imports; existing engine job + release.yml unchanged.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 ID-5 (consumer migration mechanics) answered: "A" - docs + in-repo updates at implementation; CHANGELOG old→new import table; in-repo pipeline.yaml extensions string + docs examples updated; the consumer repo gets documented upgrade note; no script.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Infrastructure Design artifacts generated: infrastructure-design.md (topology, extras inversion, skill install, version stamping, failure modes, mandatory category applicability table) + deployment-architecture.md (per-OS install flows, workflow build/run layout, CI jobs incl. new workflow-mode job, migration mechanics) under aidlc-docs/construction/unit-4/infrastructure-design/. shared-infrastructure.md NOT produced (shared infra = N/A, justified). Awaiting approval.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Infrastructure Design APPROVED: "approved" + "Commit and push all docs and continue". Next stage = Unit 4 Code Generation.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Code Generation Part 1: plan written at aidlc-docs/construction/plans/unit-4-code-generation-plan.md (14 TDD steps, story traceability, files created/modified/unchanged, KD-1..7). AWAITING explicit user approval before any code.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 Round 2 (Claude Workflow-tool feedback): evaluation delivered - Claude's API claims verified (meta literal-first, no fs/shell/network in script body, primitives agent/pipeline/parallel/phase/log, agent()→null on stop, determinism sandbox); shipped template is workflow.cli-runner-only, NOT Workflow-tool-compatible (SKILL.md claim false for its stated consumer); P0 set + two-template approach accepted; 3 push-backs (viz deletion = scope reduction; BR-5 = choice not physics; commit optional ok). R2-Q1 (viz) answered: "without viz, this whole adapter is pointless. So either it's possible to do viz, or we forget about plugging this into Claude" - resolved: viz IS possible via agent mediation (agent launches server with topology parsed by core; step-agents POST events best-effort; final agent terminates or TTL); viz = non-negotiable requirement for CC target.

## Construction - unit-4 marked WONTSHIP/WONTDO
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - User: "In fact, forget it. Let's rip it out and mark unit 4 as wontship/wontdo. Is the refactoring into separate modules still beneficial, or should we just revert the whole release?"
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Decision: keep the core/engine/viz module split; rip out ONLY the Claude-adapter surface (projection, workflow package, commit shim, viz ingest, skill, unit-4 tests, workflow entry points/extras); no yank of a6 (UTF-8 fix + split are real) - removal ships as 1.0.0a7. Rationale recorded in planning-docs/016-remove-unit-4-adapter.md.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Bookkeeping applied: aidlc-state.md unit-4 section → WONTSHIP/WONTDO (duplicate stale checkboxes removed); requirements.md Stage 4 heading annotated WONTSHIP. Code removal deferred to user go-ahead.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-4 removal packaging decision: user agreed ("Yes agreed") to restore the pre-unit-4 packaging shape - openhands-sdk/openhands-tools/acp-cap/langgraph/langgraph-checkpoint-sqlite move back into base [project.dependencies]; extras reduced to dev only; README pins plain ==1.0.0a7; workflow/commit entry points removed. Recorded in planning-docs/016-remove-unit-4-adapter.md Packaging section.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Plan split + revert: setup-stamp/venv hardening extracted to planning-docs/016-sandbox-venv-hardening.md; removal plan renumbered 016-remove → planning-docs/017-remove-unit-4-adapter.md; aidlc-state.md pointer updated. Dev agent's uncommitted removal edits (README/audit/pyproject@1.0.0a7/core/engine/viz files + deleted unit-4 adapter files) REVERTED to HEAD fb4ce5c per user directive "Revert those, then commit and push the docs"; agent's untracked tests/test_module_contract.py removed. Removal code work remains deferred to 017.

## Unit-5 Functional Design STARTED
**Timestamp**: 2026-10-25T08:51:18Z
**User Input**: "Continue unit 5"
**AI Response**: Session resumed (session-continuity check). Verified current state in aidlc-docs/aidlc-state.md (unit 5 = INTENT ONLY, all stage checkboxes empty), re-read unit-5-intent-workspace-egress-isolation.md, unit-3 FD plan template, and audit log style. Created aidlc-docs/construction/plans/unit-5-functional-design-plan.md (unit context from the intent doc, FD step checkboxes, the 8 open questions from the intent doc as Q1-Q8 with A/B(/C)/X options and [Answer]: tags). Presented Q1-Q8 to the user inline in chat via the question tool (AGENTS.md override of the aidlc rule). Answers will be written back to the plan file.
**Context**: CONSTRUCTION - unit-5 Functional Design. Next step: user answers Q1-Q8.

## Unit-5 FD - Q9 raised (allowlist ownership)
**Timestamp**: 2026-10-25T09:12:30Z
**User Input**: "My main questions are: 1. Who decides what's on the allowlist? 2. Is it feasible to have a common allowlist that will work for multiple toolchains with minimal per-project overrides?"
**AI Response**: Answered inline: (1) ownership = pipeline author with engine-shipped presets (package-registries preset enumerated: pypi.org, files.pythonhosted.org, api.osv.dev, registry.npmjs.org, nodejs.org, crates.io, static.crates.io, api.nuget.org, github.com + codeload.github.com) + engine validation, loud failures; mirrors how the engine already owns image/seccomp defaults with author override. (2) Feasible for toolchain-driven steps: registries are a small stable CDN-backed set; Q2's resolve-at-assembly handles IP rotation; per-project additions are 2-4 account-specific hosts (R2 endpoint, Kinde domain, app URL) concentrated in the qa step; browser steps exempt via Q8 broad-but-logged. Added Q9 (allowlist ownership, A/B/C/X) to plans/unit-5-functional-design-plan.md with [Answer]: TBD; presented Q9 + F1 + F2 to user via question tool.
**Context**: CONSTRUCTION - unit-5 Functional Design. Awaiting Q9/F1/F2 answers.

## Unit-5 FD - Q9/F1/F2 answered
**Timestamp**: 2026-10-25T09:13:50Z
**User Input**: Question tool answers: Q9 = "Engine presets + author override"; F1 = "Sync-back after rw steps"; F2 = "Bind-mount real caches" (all three = recommended options).
**AI Response**: Answers written back to plans/unit-5-functional-design-plan.md ([Answer]: tags filled for Q9, F1, F2). All 11 open questions now resolved. Next: domain exposition (DOMAIN-01), then generate the 3 functional-design artifacts.
**Context**: CONSTRUCTION - unit-5 Functional Design.

## Unit-5 FD - multi-toolchain reminder + artifacts generated
**Timestamp**: 2026-10-25T09:16:36Z
**User Input**: "Remember that we need to support ,ultiple toolchains - pypi, npm, .NET, etc." (typo in original preserved)
**AI Response**: Folded the requirement into the FD artifacts: package-registries preset enumerated per toolchain (pypi.org/files.pythonhosted.org/api.osv.dev, registry.npmjs.org/nodejs.org, api.nuget.org, crates.io/static.crates.io, github.com/codeload.github.com/objects.githubusercontent.com) so restore stays toolchain-agnostic; per-toolchain cache declarations (.venv-sandbox, node_modules, qa/node_modules, qa/.browsers, .nuget-packages, target/); BR-9 + D-2 added. Domain exposition (DOMAIN-01, six dimensions) written into plans/unit-5-functional-design-plan.md; design decisions D-1 (profile-keyed container pool) and D-2 (multi-toolchain = preset data) recorded. Generated the three artifacts under aidlc-docs/construction/unit-5/functional-design/: business-logic-model.md (core flow, KD table Q1-Q9/F1/F2/D-1/D-2, multi-toolchain tables, algorithms, mermaid, PBT targets), business-rules.md (BR-1..BR-12, security-baseline mapping, residual risks R-1..R-3), domain-entities.md (trace list, VOs, WorkspaceExport entity, modified entities, pure functions, descent audit). Plan checkboxes completed.
**Context**: CONSTRUCTION - unit-5 Functional Design. Awaiting review.

- 2026-10-25T09:50:34Z - Unit-5 Functional Design APPROVED: "approve and continue". Next stage = Unit 5 NFR Requirements.
- 2026-10-25T09:50:34Z - Unit-5 NFR Requirements STARTED: plans/unit-5-nfr-requirements-plan.md created (NFR-1..NFR-6 open questions, step checkboxes). aidlc-state.md updated (FD stage [x], unit-5 section ACTIVE).

- 2026-10-25T09:56:09Z - Unit-5 NFR-1..NFR-6 answered (all A, recommended options): NFR-1 measured-not-enforced; NFR-2 platform detection (nftables on Linux, proxy on macOS/Windows); NFR-3 hard break + migration notes + in-repo consumer updates; NFR-4 mirror unit-3 BR-10 (idempotent infra retries, no execution/sync-back retries); NFR-5 state_dir host-only structured logs; NFR-6 tiered + privileged Linux CI job. Answers written back to plans/unit-5-nfr-requirements-plan.md.
- 2026-10-25T09:56:09Z - Unit-5 NFR artifacts generated under aidlc-docs/construction/unit-5/nfr-requirements/: nfr-requirements.md (NFR-5.1..5.8 + category dispositions + security-baseline blocking set dispositions, no blocking findings) + tech-stack-decisions.md (TS-1..8 + deferred list). Plan checkboxes complete; awaiting review.

- 2026-10-25T09:59:58Z - Unit-5 NFR review (Request Changes, round 1), user raised 4 points: (1) requested a concise explanation of the extra vulnerability surface on macOS/Windows under proxy enforcement - answered inline (HTTP-only coverage, cooperation-dependent enforcement, no kernel backstop on VM hosts, TLS limits); (2) directive: this repo's committed files must not name the commercial consumer repo - all unit-5 stage docs scrubbed (neutral "reference consumer repo" phrasing); pre-existing mentions elsewhere inventoried and a scope decision requested; (3) NFR-5.5 log scope accepted as-is; (4) requested specifics on privileged-CI maintainer attention - answered inline (fork-PR trigger restrictions, runner image drift + loud capability preflight, zero-secret blast-radius review, flakiness triage).

- 2026-10-25T10:05:22Z - Commercial-name scrub (user directive, round 2): docs scrubbed across aidlc-docs/, planning-docs/, docs/ (34 mentions replaced with neutral "the consumer repo" phrasing, incl. 3 historical audit entries - raw-input quotes in the historical record were redacted per directive); awkward grammar from mechanical replacement fixed in 6 spots. Code/test/README/Dockerfile mentions enumerated with exact replacements in planning-docs/026-commercial-name-scrub.md (14 src/tests lines + 2 root files), awaiting code-mode execution. The product name is not re-introduced in this entry.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-5 NFR-2/NFR-6 REVERSAL (post-review, user directive): "Drop the separate linux enforcement altogether for now. We will eventually build a native linux path (without docker), so that hardening will live there. For now, all three OSs get the same treatment" followed by "SO the product for now is 'best we can do with Docker'". Applied across FD plan (Q1/Q2 superseded, Q7 amended, exposition), business-logic-model (policy assembly, key decisions, match_target, mermaid), business-rules (BR-5/6/11/12, R-2, SECURITY-02 now Enforced), domain-entities (DockerBackend, match_target), NFR plan (NFR-2/NFR-6 superseded, NFR-1/4/5 context), nfr-requirements (NFR-5.1/5.2/5.4/5.5/5.6/5.7 + dispositions), tech-stack-decisions (TS-1 proxy primary, TS-2 superseded, TS-7/8, deferred), intent doc (FD resolution note). Net: engine-managed per-policy egress proxy is THE enforcement point on all platforms; kernel-level enforcement deferred to the future native Linux backend (unit-3 FD-2 scope).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-5 NFR Requirements APPROVED: "Approved". Next stage = Unit 5 Infrastructure Design (NFR Design globally skipped).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-6 intent recorded (user, raw): "Before we continue, record the following intent for the NEXT unit: - Linux-native sandboxing (no docker) - Linux-native egress protection - A simple way to deploy this all (e.g. to a Hetzner VPS or something like Render) and have the viz accessible to me (model would be build the plans locally, run the loop in the cloud)". Intent doc created: aidlc-docs/unit-6-intent-linux-native-and-cloud-deploy.md. aidlc-state.md updated (unit-5 NFR [x], unit-6 INTENT ONLY section, unit order extended).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - UNIT-6/7 SPLIT (user, verbatim): "The third point (simple deployment) is probably a separate unit, that comes before the linux native unit (we need it to test the linux native stuff since I have a mac)". Combined unit-6 intent split: unit-6 = cloud deployment (comes FIRST), unit-7 = Linux-native sandboxing + egress protection (tested on the unit-6 cloud box). Old combined doc removed; aidlc-docs/unit-6-intent-cloud-deployment.md + aidlc-docs/unit-7-intent-linux-native-sandboxing.md created; aidlc-state.md unit order + sections updated.
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-5 Infrastructure Design STARTED: plans/unit-5-infrastructure-design-plan.md created (stage context, ID-1..ID-6 open questions: proxy implementation, container-to-proxy reachability, proxy lifecycle/ports, export materialisation, sync-back mechanics, CI wiring; step checkboxes).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-5 ID answers (all A = recommended): ID-1 hand-rolled asyncio proxy (stdlib only, no new deps), ID-2 host-gateway add-host + proxy env, ID-3 ephemeral loopback port per policy, ID-4 copy-based export (dirty state preserved, .git absent by construction), ID-5 in-process shutil diff-copy sync-back, ID-6 loopback test servers in normal CI (no root, no privileged job).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-5 Infrastructure Design artifacts generated under aidlc-docs/construction/unit-5/infrastructure-design/: infrastructure-design.md (topology, key structures ID-1..6, failure modes, category applicability) + deployment-architecture.md (per-OS runtime layout, state_dir additions, container assembly, CI jobs, migration mechanics). shared-infrastructure.md NOT produced (shared infra = N/A, justified). Plan checkboxes updated; awaiting approval.

- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-5 Infrastructure Design APPROVED: "continue". Next stage = Unit 5 Code Generation (Part 1: plan with checkboxes, awaiting explicit code approval per repo AGENTS.md).
- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-5 Code Generation Part 1 STARTED: code-generation plan being drafted (TDD steps, story traceability, files created/modified/unchanged, key decisions), mirroring the unit-4 code-generation-plan structure. No code is written before explicit user approval.

- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Unit-5 Code Generation Part 1: plan written at aidlc-docs/construction/plans/unit-5-code-generation-plan.md (14 TDD steps, BR/NFR traceability, files created/modified/unchanged, KD-1..7, 026-scrub coordination note). AWAITING explicit user approval before any code per repo AGENTS.md.

- $(date -u +%Y-%m-%dT%H:%M:%SZ) - Planning docs committed and pushed (commit 47777fd, main): unit-5 FD/NFR/ID plans + artifacts, unit-5 code-generation plan, unit-6/7 intent docs, commercial-name scrub of planning docs, 35 files. Pre-commit em-dash check passed. Code Generation Part 1 still AWAITING explicit user approval before any code.

- 2026-10-25T10:28:37Z - Domain-model ERD created: user requested "Generate an ERD for the domain model (mermaid), including for this unit 5 proposal (i.e. the entire app)". Created aidlc-docs/domain-model-erd.md - one mermaid erDiagram spanning unit-1 (Step/StagesRuntime/Verdict/CapturedOutput), unit-2 (proc/Registry - pure functions, not modelled as entities), unit-3 (Sandbox/DockerBackend/SandboxExecutionRequest/SandboxExecutionResult/IsolationProfile/IsolationConfig), unit-5 proposal (MountSpec/MountSet/TargetEntry/EgressPreset/EgressPolicy/EgressProxy/ContainerProfile/WorkspaceExport/SyncBackReport), core config (PipelineConfig/StepConfig) and plans 019 (SecretRef/SecretProvider) + 021 (DependencyPolicy). Entity-source table marks unit-5 entities as pending Code Generation Part 1. External actors (Docker daemon, registries/CDNs, host env) deliberately not modelled.

- 2026-10-25T10:41:02Z - ERD parse-error fix: user reported "Error: Parse error on line 68 ... Expecting 'ATTRIBUTE_WORD', '?', got 'BLOCK_STOP'" (mermaid renderer). Root cause found by reproducing with mermaid v11 + jsdom locally and reading the erDiagram grammar: each attribute is exactly "TYPE NAME" (single ATTRIBUTE_WORD each); multi-word lines like "list StepConfig steps" (3 tokens) shift every subsequent attribute pair in the block, and blocks with an odd token count fail at the closing brace. Fixed all 6 multi-word lines in aidlc-docs/domain-model-erd.md to 2-token form with comments carrying the entity refs (list steps "StepConfig refs", dict secrets "SecretRef refs", list mounts "MountSpec refs" x2, list targets "TargetEntry refs" x2). Verified: mermaid.parse OK + full attribute-pair audit clean. Full SVG render in node is not possible in jsdom (getBBox unsupported) - parse success against the same grammar engine renderers use is the gate.

- 2026-10-25T10:52:14Z - ERD extended (user: "Re-render this showing the public methods too, so I can see the behaviour" + "Also the type (ValueObject, Entity, Domain Service, Application Service, etc.)"): aidlc-docs/domain-model-erd.md now carries a `type X` DDD classification row per entity (ValueObject / Entity / ApplicationService / Adapter / Infrastructure from the descent audits; config models classified VO with a note that the AI-DLC docs loosely call them entities) and `fn` rows with full signatures in comments, grounded in code (outputs.py:35/45, verdict.py:49, registry.py:131/147/163/168/181, secrets.py:33, isolation.py:599/602/653/679/713/750/781, config.py:142) + the unit-5 proposal surface from the domain doc. Registry added as entity (unit-2) with the action/tool-lookup relationship. Full method-signature table + DDD classification table added below the diagram. Verified with local mermaid v11: token-pair audit zero problems, mermaid.parse PARSE OK.

- 2026-10-25T10:57:03Z - ERD title stereotypes (user: "Show the type in the title instead of as a type, e.g. Registry <<Entity>>, Verdicy <<ValueObject>> It's easier to scan"): erDiagram has no native stereotype syntax; tested three forms against the local mermaid v11 parser - direct `Name <<Type>> {` FAILS, entity-alias labels PARSE OK. Adopted variant B: `Name["Name <<Type>>"]` (alias = base name, quoted label carries the <<Type>>, relationship lines keep plain names - requires mermaid 10.5+). Rewrote aidlc-docs/domain-model-erd.md: every entity block header now carries the DDD classification in the title, `type X` attribute rows removed. Verified: token-pair audit zero problems, mermaid.parse PARSE OK.

- 2026-10-25T11:02:31Z - ERD alias fix (user: "Error: Error: Parse error on line 2: ...g <<ValueObject>>"] ||--o{ StepConfig["S ... Expecting ... got 'ONLY_ONE' ... YOU FUCKING IDIOT!!!!!"): the aliased entity names were placed in RELATIONSHIP lines; the user's renderer rejects alias brackets there (my local v11 parse had accepted it - the test environment mismatch is mine). Fixed per the documented alias pattern: alias labels stay ONLY on entity block headers `Name["Name <<Type>>"]`, all relationship lines use plain entity names. Verified: relationship lines zero brackets, 25 block headers carry aliases, mermaid v11 parse PARSE OK.

- 2026-10-25T11:09:44Z - ERD label delimiter fix (user: "It renders, every name is followed by <> with no fucking type in it"): the renderer HTML-strips `<Type>`-shaped text inside alias labels, leaving empty `<>`. Angle-bracket stereotypes cannot survive that renderer's label sanitisation. Switched all 25 entity labels from `Name["Name <<Type>>"]` to `Name["Name (Type)"]` (plain-text parentheses, immune to tag stripping). Verified: zero `<<` remaining, all 25 labels carry `(Type)`, mermaid v11 PARSE OK.

- 2026-10-25T11:21:09Z - ERD smell review (user: "There are several smells: Registry has methods but no data - it looks like an anaemic service in disguise; Step also has no data; StagesRuntime - does it need to exist? I propose run_agent be moved to the entity it acts on; Sandbox also has methods but no data; Same with DockerBackend; I don't understand what WorkspaceExport represents in the domain"). Code-grounded fixes applied to aidlc-docs/domain-model-erd.md: Registry + DockerBackend gained their real data rows (the omission was the ERD's, the code has state); Sandbox relabeled Interface (ABC, run/close only, genuinely stateless - unit-3 user directive); StagesRuntime renamed Runtime (code name; unit-1 docs used the phantom StagesRuntime) with its real dataclass fields; IsolationProfile renamed EffectiveProfile with its real frozen-dataclass fields; Step grounded as conceptual (StepConfig + LoopState, no Step class exists). Prose: classification table + Interface row, method table, sources table, reading notes updated; new Open design questions section (run_agent placement = code change needing explicit approval; WorkspaceExport rename candidates StepWorkspace/WorkspaceSnapshot/WorkingCopy; Step keep-or-drop). Verified: phantom-name grep clean (only deliberate prose notes), token-pair audit NONE, mermaid.parse PARSE OK.

- 2026-10-25T11:26:31Z - ERD regenerated (user: "Then regenerate the erd"): aidlc-docs/domain-model-erd.md diagram re-verified after the StepWorkspace rename + fold-in (Runtime/EffectiveProfile names, Interface Sandbox, data rows, resolved-design-questions section). Verification: token-pair audit NONE, mermaid.parse PARSE OK against local mermaid v11. Diagram presented inline.

- 2026-10-25T11:33:45Z - Step-execution flowchart created: user requested "Now generate me a flowchart showing the execution flow for a step, with the new egress stuff from the planned unit". Created aidlc-docs/construction/unit-5/functional-design/step-execution-flow.md - mermaid flowchart with two subgraphs (load time: validation, preset expansion, StepWorkspace materialisation, cache bind-mounts; per-step: mount resolution, egress policy assembly, profile-keyed pool, egress proxy with hostname matching, step-kind routing, sync-back, verdict loop) plus teardown path and failure-mode notes. Verified with local mermaid v12: PARSE OK (tooling note: mermaid v12 flowcharts need a DOM for DOMPurify - jsdom window must be set before importing mermaid; the ERD path does not hit this).

- 2026-10-25T11:41:00Z - Unit-5 Code Generation Part 1 APPROVED: "Ok, approved, continue" (approves the step-execution flowchart and the code-generation plan). Plan status updated (Part 1 approved; Part 2 implementation pending the code-mode switch), aidlc-state.md updated (line 6 current stage, unit-5 section heading + Next bullet).

- 2026-10-25T12:08:14Z - STEP 0 EXECUTED (user: "Do step 0 now, then STOP."): commercial-name scrub applied across the repo - README key name, Dockerfile comment, 7 engine comment/docstring mentions, 11 test mentions (incl. fixture + assert renames in test_config/test_graph/test_stages), 4 test_secrets env-key fixtures, 1 planning-doc key name, plus a second-pass catch: the uppercase fixture identifier in test_graph.py (6 sites, incl. LOCKED_YAML) renamed together with its 5 planning-doc quotes. The scrub plan doc was rewritten to DONE status with neutral phrasing. Acceptance gates passed: case-insensitive git grep returns nothing on tracked files (audit.md history excluded from the gate; pycache binaries gitignored), full pytest suite green (833 passed, 1 skipped, 153s). STOPPED after step 0 per user directive - code-gen steps 1-15 NOT started.

- 2026-10-25T12:19:00Z - Commit 3479d71 recorded: user "Yes, commit the accumulated work now" then "And push". Single combined commit "chore: scrub commercial name; add unit-5 ERD, step-execution flowchart, and fold-in docs" (25 files, +585/-104; step-execution-flow.md + domain-model-erd.md created). Pre-commit em-dash hook Passed. Pre-commit verification: U+2014 scan of changed files = NONE; the U+2192/U+21D2 arrows flagged by a strict ASCII scan pre-exist at HEAD (planning-docs/012 = 34 such lines at HEAD, README 9, stages 10, test_config 2, aidlc-state 11); the diff introduced zero new non-ASCII; full pytest green was already proven at 12:08Z.

- 2026-10-25T19:27:28Z - QA follow-up task 1 (user: "Do 1-3"): committed the dogfood tooling paths as 77f8c86 "chore: commit dogfood tooling paths: pipeline mounts/egress declarations, README unit-5 docs, pins to 1.0.0a17" (agentic-dev-loop/pipeline.yaml +6 mounts/egress declarations on dev/restore/test/review/qa; README.md +41 unit-5 docs; version pins ==1.0.0a9 -> ==1.0.0a17 at all 4 sites). Pre-commit ASCII hook Passed. Closes QA gap 1 (the loop commit does not sweep tooling paths - plan 020).
- 2026-10-25T19:27:28Z - QA follow-up task 2: fail-closed-on-proxy-death test added TDD red-green (tests/test_isolation.py test_proxy_death_fails_closed). Red: DID NOT RAISE SandboxError. Green: EgressProxy.is_alive property (egress_proxy.py) + _run_profile raises SandboxError AE-SBX-018 when a networked profile's proxy is dead (isolation.py; BR-11.3 - no networked execution runs without egress enforcement). Regression sweep: tests/test_isolation.py + test_egress_proxy.py + test_unit5_config.py + test_unit5_pure.py + test_unit5_pbt.py -m "not docker" = 244 passed. Closes QA gap 2.
- 2026-10-25T19:27:28Z - Unit-5 CLOSEOUT: unit 5 marked COMPLETE in aidlc-state.md (Code Generation [x]; Build and Test re-run recorded: 80 unit-5 tests green locally, 244-test regression sweep green, docker-marked integration tests green on CI Linux; QA verdict PASS run 6cb529022644). Build history recorded: f9231ba "feat: construct unit unit-5 (agent dev loop)", a1dee63 "release: v1.0.0a17", 2e7ea1f "docs(planning): add 027 artifact-injection plan; QA probe-plan rules". Closes QA gap 4. QA gap 5 fixed with task 1 (version pins). Deferred amendments (requirements.md / unit-of-work.md opt-in wording, story-map rows for units 5-7) remain open - user declined recommendation 4.
