# Postmortem: Unit-10 multi-provider runtime (2026-11-05)

Status: FINAL (2026-11-06). Batches 1 and 2 approved by the user with
corrections; Phase 3 appendices (A: validated model, B: repair scripts)
approved (committed in 150ad1c); Phase 4 propagation applied - see the
Propagation section below.

Time convention: git and PyPI times are authoritative and quoted in UTC.
Session-record times carried a "Z" suffix but are consistent with local time
(EET, +02:00) and are marked with "~".

## TL;DR

Unit-10, the multi-provider runtime (OpenHands + Claude Agent + Copilot
drivers), went from brainstorm through a full AI-DLC cycle to a dogfood-loop
implementation commit (4a00257, 2026-11-05) in one day. The CopilotRuntime
was coded against a guessed openai-codex SDK surface, and the design's
architectural premise - that openai-codex can authenticate a Copilot
subscription - was false: the SDK's app-server exposes only
apiKey/chatgpt/chatgptDeviceCode/chatgptAuthTokens/amazonBedrock login types.
The premise was never probed before design approval: a GitHub-docs claim that
Codex "can be powered by a Copilot subscription" was treated as a verified
fact about the pinned SDK, and the one gate that could have caught it (live
harness smoke) was opt-in and deferred past the unit's completion. The user's
smoke run exposed the breakage about 11 hours after the commit, and the
user's mechanical revert (526c802) removed all 31 files with zero collateral;
no broken code ever reached PyPI (verified: the published 0.11.1 wheel
predates unit-10 and contains no runtimes package). Unit-10 was marked
WONTSHIP and superseded by planning-docs/058, which rebuilds Copilot support
as an OpenAI-compatible endpoint on the existing runtime: same goal, no
harness-driver architecture.

## Impact

- **What broke**: the copilot provider was completely unusable - it never
  executed successfully once, failing identically on every attempt. The
  claude provider shared the same guessed-interface origin and was never
  live-verified. Of the ~2,900 lines unit-10 shipped, only the device-flow
  concept survived (carried into planning-docs/058).
- **Who was affected**: no users, no consumers. Verified, not assumed: the
  PyPI latest (0.11.1, uploaded 2026-11-05T18:31Z) contains no unit-10 code -
  the wheel was downloaded and inspected (no `runtimes` package, 69 files).
  The broken code existed only on the `main` branch for ~10.4 hours
  (2026-11-05T21:08Z commit 4a00257 to 2026-11-06T07:35Z revert 526c802).
- **What was NOT affected (checked)**: the openhands/DeepSeek path (rule zero
  held - the full suite went 1277 green before, 1347 green with unit-10, 1277
  green after the revert), the Docker sandbox and viz (unit-10 did not modify
  the Sandbox.run path or the viz server), the dogfood pipeline (never gained
  a provider key), and every published artifact.
- **Cost**: a complete AI-DLC unit cycle (requirements, functional design,
  NFR requirements, infrastructure design, code generation, QA, evaluation)
  consumed with zero shipped value; the Claude harness path is deferred
  indefinitely; the host .venv carries two now-unused SDK packages
  (claude-agent-sdk 0.2.178, openai-codex 0.163.0).
- **Data loss**: none. One wrong-scoped OAuth token file remains on the
  operator's machine (~/.agentic-dev-loop/auth/copilot.json, scope
  `read:user` instead of `copilot`) - flagged for deletion in 058.
- **Trust cost**: the harness-as-subprocess-driver approach is now
  distrusted for this project (unit-4 was WONTSHIP on a related rationale;
  unit-10 joins it). The replacement design (058) carries "verify before
  claiming" gates born from this failure. A dogfood-loop QA verdict of PASS
  on a unit whose headline feature was unverified means future PASS verdicts
  carry less weight until the Process action below lands.

## Timeline

Wrong turns are marked W1-W4; DETECTION and MITIGATION are the moments the
failure surfaced and was removed.

| # | When (UTC) | Event |
|---|------------|-------|
| 1 | 11-05, ~afternoon | Brainstorm: user proposes plugging Claude Code agents into the loop; design converges on AgentRuntime + three drivers. **W1**: the "verified external facts" list included a GitHub-docs claim that Codex "can be powered by a Copilot subscription" - treated as a capability of the pinned openai-codex SDK, never probed |
| 2 | ~17:35Z | User approves the design and starts AI-DLC unit-10 |
| 3 | ~17:48Z | Requirements approved: FR-10.5 codifies "CopilotRuntime via openai-codex" on the unprobed premise |
| 4 | ~18:35Z | Functional design approved: engine-internal MCP server for both harnesses |
| 5 | ~19:10Z | NFR approved: **W2** - NFR-4=A chooses contract tests + opt-in live smoke; the live smoke becomes an operator-optional gate, not a completion condition |
| 6 | ~19:30Z | Code generation restructured to one continuous stage, implemented by the dogfood loop |
| 7 | 18:27Z | User tags and releases v0.11.1 (pre-unit-10 code; PyPI upload 18:31Z). Timing, not policy, kept unit-10 off PyPI |
| 8 | 20:08Z | e31f40f "Unit 10 docs" |
| 9 | 21:08Z | 4a00257 "feat: construct unit unit-10": 31 files, +2968/-199. **W3**: the loop's qa-report verdict is PASS with the harness hardening contract and live e2e claude/copilot runs listed as unverifiable - the harness SDKs were never installed in the loop environment and the SDK contract tests silently `importorskip`'d |
| 10 | ~21:30Z | Agent's independent QA evaluation: high confidence on the openhands path; claude/copilot live behavior UNVERIFIED. The gap was named but not treated as blocking |
| 11 | 21:44Z, 21:49Z | 7ebccaf smoke assets, 80a04d0 pre-flight SDK check - the tooling that would later detect the failure |
| 12 | ~21:50-22:00Z | **DETECTION**: the user runs the smoke test with `--provider codex`. Run 1 fails fast with "requires the openai-codex extra" (engine fail-fast working as designed); extras are installed. Run 2: the GitHub device flow succeeds (token written), but the dev step fails every attempt - the driver was written against a guessed SDK surface (CodexSession and kwargs that do not exist) |
| 13 | 11-06, early | User-directed inline probing of openai-codex 0.163.0 establishes the false premise definitively: the app-server login types are apiKey/chatgpt/chatgptDeviceCode/chatgptAuthTokens/amazonBedrock only - no Copilot-subscription auth surface at any layer |
| 14 | 11-06, early | User asks whether to revert; agent verifies the three unit-10 commits are cleanly unit-10-only and recommends full revert; the user runs it |
| 15 | 07:35Z | **MITIGATION**: 526c802 revert - mechanical, zero conflicts, 31 files removed; suite green at 1277 passed |
| 16 | 07:53Z, 08:37Z | 0ff74e2 "WONTSHIP unit 10, fix up smoke test"; 4886c41 "Unit 10 replacement plan" (planning-docs/058-copilot-as-endpoint.md) |

**W4** (process-level wrong turn, spanning steps 2-9): the design-to-release
path had no gate that validates the design's external-system premises against
the pinned SDK, and no gate that blocks completion while the headline feature
is untested. Steps 9-10 show the information was available (the unverifiable
list, the evaluation note) and the process did not act on it.

## Credit

Detection: the user - the user drove the smoke runs against the committed
code with a real Copilot subscription and reported both failures. Diagnosis:
the agent - code reading plus the user-directed inline probing that
established the impossible premise. Decision and execution of the revert: the
user. The agent's QA evaluation had named the UNVERIFIED gap roughly a day
before detection but did not block on it.

## Root-cause analysis

Why did a full planning cycle produce an unusable unit? The chain of whys,
from the observable failure back to the process gap:

1. **The copilot provider never executed once.** Every attempt failed
   identically: run 2 of the smoke test raised TypeError for unknown
   constructor kwargs inside the driver.
2. **Why?** CopilotRuntime was written against a guessed openai-codex API
   surface: `CodexSession`, a `start()`/`run(prompt)` shape, and constructor
   kwargs (auth, system_prompt, mcp_servers, allowed_tools, max_turns) that
   do not exist in openai-codex 0.163.0. The real SDK is CodexConfig +
   thread_start/run/turn with config_overrides.
3. **Why was it guessed?** The implementing agent never had the SDK. The
   dogfood loop's host environment had neither claude-agent-sdk nor
   openai-codex installed, its sandbox had network off, and no subscriptions
   existed there. With no artifact to probe, the agent improvised from names
   and doc snippets.
4. **Why did no earlier stage catch it?** The brainstorm's verified-facts
   list treated "Codex can be powered by a Copilot subscription" (a GitHub
   docs claim about Codex, in public preview) as a capability of the pinned
   openai-codex SDK. FR-10.5 then codified "CopilotRuntime via openai-codex"
   on that unprobed premise, and every later stage (FD, NFR, ID, CG)
   elaborated the design without re-checking the premise. The repo's
   reverse-engineering rule ("never design destructive operations against an
   unvalidated model of an external system") is scoped to destructive
   operations and was not applied to architectural premises.
4b. **Why did the premise enter as a "verified fact" in the first place?**
  (identified by the user in postmortem review) The brainstorming flow was
  used beyond its scope: it produced not just the idea (plug Claude Code
  agents into the loop) but the full architecture - AgentRuntime, three
  drivers, the harness-as-subprocess model - which the AI-DLC unit then fed
  on verbatim. The premise entered at ideation time, in a flow that has no
  probe gate of any kind, and every later AI-DLC stage treated the
  brainstorm's facts as given. Brainstorming is for ideas; design production
  belongs to the stages that carry the probe gates.
5. **Why didn't the completion gates catch it?** NFR-4=A made live harness
   runs opt-in and operator-run; the unit was structured so it could complete
   without them. The loop's contract tests silently skipped (importorskip
   with the SDK absent), so a green suite proved nothing about the harness
   surface. The qa-report honestly listed the unverified items and still
   issued PASS, and nothing in the commit/release path gated on the
   unverified list. The agent's independent evaluation named the same gap
   ("claude/copilot live behavior remains UNVERIFIED") but did not block.

**Meta root cause (process)**: the development process had no gate that
validates an architectural premise against the pinned external artifact (the
actual SDK) before design approval, no gate that treats an untested headline
feature as a blocking rather than cosmetic defect at completion, and an
ideation flow that was used to author design content - so the false premise
entered upstream of every design-stage gate. All are process answers, not
technical ones - the technical facts needed to stop the ship existed at
timeline step 9 and were recorded but not acted on.

## Actions

Grouped Prevent / Detect / Process. Unowned items are marked explicitly;
already-queued work is linked, not duplicated.

**Prevent**

- A1 - Premise probe before design approval. Any brainstorm or AI-DLC design
  that depends on an external-system capability (SDK surface, auth flow,
  endpoint behavior) must include a probe deliverable: install the pinned
  artifact and establish the capability by probe, recording each fact in the
  validated-model table format (Appendix A of this doc), before the design
  is approved. Owner: user (process gate owner). Date: effective immediately;
  058's verification gates 2-5 already embody this for the endpoint design.
- A2 - Extend the AGENTS.md reverse-engineering rule from destructive
  operations to architectural premises: "Never build an architecture on an
  unvalidated external-system capability claim - probe the pinned artifact
  before the design is approved." Owner: unowned; requires user approval
  before touching AGENTS.md (Phase 4).
- A3 - SDK presence for driver code. Code that drives an external SDK is
  written only against a probed SDK: installing the extra and running a
  minimal real invocation of the SDK must accompany the first TDD step of
  driver code, never after. Owner: unowned.
- A4 - Brainstorming scope discipline. Brainstorming produces ideas,
  options, and trade-offs, not approved architecture; design production
  (entities, rules, interfaces, "verified facts" lists) belongs to the
  AI-DLC stages, which carry the probe gates. The brainstorm's verified-facts
  section must carry the same probe discipline as the stages (A1/A5).
  Owner: user (drives brainstorming usage). Date: effective immediately.
- A5 - AI-DLC external-surface extension (user-directed). A new AI-DLC
  extension, active during inception/ideation and functional design as
  appropriate, that (a) detects external surfaces in the design (SDK
  capability claims, endpoints, auth flows, third-party behaviors); (b)
  forces an evaluation of each surface against a pinned version of the
  artifact (installed package, probed endpoint, or its declared interface);
  and (c) when the evaluation fails or cannot run, surfaces the gap loudly
  and recommends a spike to close it - the spike result is a completion
  condition for the affected design stage, never deferred past it. Owner:
  user (AI-DLC rules live in .aidlc/aidlc-rules/). Date: before the next
  AI-DLC unit; flagged for Phase 4 propagation.

**Detect**

- D1 - QA verdict semantics. A unit whose headline feature is unverified
  cannot PASS. Either the qa step must emit UNVERIFIABLE for the unit, or
  the PASS definition must exclude unverified headline features; the commit
  and release path refuses to proceed on an unverified headline feature.
  Owner: user (sdk_agents QA prompt + commit/release actions). Date: before
  the next dogfood unit.
- D2 - Contract tests must fail loudly. SDK contract tests must not silently
  skip when the SDK is absent: replace importorskip with an explicit
  skip-plus-UNVERIFIED marker, or run them in a CI tier that installs the
  extras (unit-10's ID-1 CI tier was designed for this and reverted with the
  unit). Owner: unowned.
- D3 - Real-token smoke as a repeatable gate. tests/smoke/run_smoke.py
  detected this failure; 058 D9/D10 formalize config-dir-based real-token
  runs (copilot-endpoint, openai-endpoint) with a pre-flight that fails fast
  on missing credentials. Linked to planning-docs/058, not duplicated here.

**Process**

- P1 - Completion definition for integration units. An SDK-integration unit
  is not complete until its live/real-token gate has run, or the unit is
  explicitly marked UNVERIFIED or WONTSHIP. No release tag while a headline
  feature is unverified. Owner: user. Date: effective immediately for 058's
  implementation.
- P2 - Vigilance is not a control. The user's insistence on running the
  smoke test against committed code with a real subscription was the safety
  net this time. 058's real-token gates convert that one-off skepticism into
  a repeatable operator-owned gate. Linked.
- P3 - QA evidence persistence. The qa-report.md and review-findings.md
  files rotated out of the run directory before this postmortem (only
  attempt-brief.md, exports, and the sqlite checkpoints remained); the
  session record was the surviving copy. Consider persisting QA verdicts
  into the repo (e.g. aidlc-docs/construction/unit-N/qa/) instead of only
  the ephemeral run dir. Owner: unowned.

## What went well

- **Engine fail-fast (BR-10.7)**: the missing-extra failure produced an
  exact error naming the install command; run 1 was diagnosed in minutes.
- **Rule zero held**: the openhands path stayed byte-identical throughout;
  the suite going 1277 -> 1347 -> 1277 green bounded the blast radius to
  the new code and made the revert cheap.
- **Commit hygiene**: unit-10 landed as self-contained commits; the revert
  was mechanical, conflict-free, and verified green. Rollback cost minutes.
- **Honest QA artifacts**: the qa-report's UNVERIFIABLE list and the
  review findings (which found and fixed two real defects: dead code in
  stages.py and the 030 sticky-infra fail-fast path) named exactly the
  gates that had not run. The information needed to stop the ship existed;
  the process failed to act on it.
- **Cheap detection tooling**: the smoke harness built for the 057 keep-alive
  change doubled as the provider verifier and caught the breakage with a
  real subscription the same night as the commit.
- **User skepticism (noted, not a control)**: the user drove the smoke runs
  against committed code and asked the revert question early. That
  skepticism was the safety net this time; it implies P1/P2 because a
  vigilant user is not a repeatable control.

## Where we got lucky

- L1 - The failure was loud and deterministic (unknown kwargs at runtime
  construction). Had the guessed kwargs been silently accepted and failed
  only at auth time, the broken provider could have sat undetected for much
  longer. Action: D1/D2.
- L2 - The false premise was statically checkable once probed (login types
  are declared in the SDK's generated app-server protocol). Action: A1/A2.
- L3 - Detection landed within ~11 hours of the commit because the user ran
  the real-token smoke the same night. Had it been deferred, the broken code
  would have lingered on main. Action: D3/P1.
- L4 - The release preceded the broken commit (v0.11.1 tagged 18:27Z,
  unit-10 committed 21:08Z). Timing, not policy, kept unit-10 off PyPI.
  Action: P1.
- L5 - Nothing had been built on top of unit-10, so the revert touched
  nothing else. No action needed beyond the existing rule-zero discipline.

## Appendix A: Validated model

Every fact below was established by probe during the incident and its
aftermath. Facts marked NOT YET CONFIRMED are carried into 058's real-token
gates.

### openai-codex 0.163.0 (the SDK the CopilotRuntime was written against)

| Fact | Established by (probe) | Date |
| --- | --- | --- |
| The package is `openai_codex`; 0.163.0 was the latest release on PyPI at probe time | pip index check | 2026-11-05 |
| Exports include Codex, AsyncCodex, CodexConfig, TurnHandle, TurnResult, Thread, AsyncThread, Input types, login handles. There is NO `CodexSession` class | Reading the installed package __init__ in the host .venv | 2026-11-05 |
| CodexConfig fields: codex_bin, launch_args_override, config_overrides (tuple of CLI `--config key=value` pairs), cwd, env, client_name/title/version, experimental_api. No system_prompt / mcp_servers / allowed_tools / max_turns / auth fields | Reading CodexConfig source in the installed package | 2026-11-05 |
| App-server login types (generated protocol): apiKey, chatgpt, chatgptDeviceCode, chatgptAuthTokens, amazonBedrock, amazonBedrockAccessKeys ONLY. No GitHub/Copilot/ghu_ token type exists in the protocol | Reading the generated protocol module (v2_all.py) | 2026-11-05 |
| SDK login surfaces: login_api_key(), login_chatgpt(), login_chatgpt_device_code(). No method authenticates a Copilot subscription device token; no auth kwarg on client construction | Reading api.py and _login.py | 2026-11-05 |
| ThreadStartParams has approval_policy, base_instructions, config (free-form dict), cwd, developer_instructions, model, model_provider, sandbox (SandboxMode readOnly|workspace-write|danger-full-access), thread_source. No per-thread system_prompt / mcp_servers / allowed_tools / max_turns | Reading ThreadStartParams in the generated protocol | 2026-11-05 |
| MCP is app-config level: methods config/mcpServer/reload, mcpServer/oauth/login, mcpServer/tool/call, mcpServerStatus/list, mcpServer/resource/read | Reading protocol method names | 2026-11-05 |
| Constructing the client with the guessed kwargs fails immediately with TypeError (unknown keyword argument) - deterministic, at runtime construction | Observed in smoke run cae6c2819311 (three identical attempts) | 2026-11-05 |

### api.githubcopilot.com (the replacement design's surface)

| Fact | Established by (probe) | Date |
| --- | --- | --- |
| OpenAI-compatible /chat/completions endpoint plus GET /models; bearer = the device-flow ghu_ OAuth token (the same token unit-10's flow stored) | Community-validated pattern; Pydantic AI ships first-party GitHubCopilotOAuthFlow / GitHubCopilotProvider for exactly this shape (websearch) | 2026-11-05 |
| Required headers: Copilot-Integration-Id, Editor-Version, Openai-Intent, X-GitHub-Api-Version. Community values vscode-chat / vscode/1.94.0 / conversation-panel / 2022-11-28. Status: NOT YET CONFIRMED against a real token - 058 verification gates 2-4 | websearch; confirmation deferred to 058 | 2026-11-06 |
| Metering = the subscription's chat quota (200/200 plus premium_interactions pools), not separate API billing | websearch | 2026-11-05 |
| OAuth apps must request scope=copilot; unit-10's device flow requested read:user (scope bug) | websearch; recorded in 058 D4 | 2026-11-05 |
| Third-party OAuth tokens see plan/integration-gated model lists; community evidence confirms base models work end to end (including tool calls), premium models vary | websearch; recorded in 058 | 2026-11-05 |

### openhands-sdk 1.17.0

| Fact | Established by (probe) | Date |
| --- | --- | --- |
| The LLM class has an `extra_headers: dict[str, str] | None` field ("Optional HTTP headers to forward to LiteLLM requests") | Reading sdk/llm/llm.py line 285 in the installed package | 2026-11-06 |

### claude-agent-sdk 0.2.178

| Fact | Established by (probe) | Date |
| --- | --- | --- |
| The package installed cleanly; its documented options include allowed_tools, mcp_servers, setting_sources, system_prompt, cwd, env, max_turns, permission_mode | Installed package + documentation | 2026-11-05 |
| The reverted ClaudeAgentRuntime was built against these documented options but was NEVER executed against the real SDK - the driver surface remains UNVERIFIED | The driver was removed by the revert before any live run; no claude smoke run happened | 2026-11-06 |

## Appendix B: Repair scripts

Commands reconstructed from the session record; marked where reconstructed.

1. Revert the unit-10 implementation (user-executed, produced commit 526c802;
   mechanical, zero conflicts, full suite green after):

```
git revert 4a00257
```

   Exact flags reconstructed from the record; the user reported running git
   revert on 4a00257f75fa28ea70dbe7efb72f1c94aa744b3e.

2. Smoke script restructure (commit 0ff74e2): moves the script to
   tests/smoke/run_smoke.py, drops the provider parameter, takes a config
   directory. This also removed the provider-block injection that pointed at
   the reverted provider key.

3. Host .venv extras cleanup (optional): claude-agent-sdk 0.2.178 and
   openai-codex 0.163.0 remained installed in the host .venv after the
   revert; the pyproject extras were removed with the revert. Uninstall when
   convenient:

```
./.venv/bin/python -m pip uninstall -y claude-agent-sdk openai-codex openai-codex-cli-bin
```

4. Wrong-scope token deletion: unit-10's device flow wrote
   ~/.agentic-dev-loop/auth/copilot.json with scope read:user. Before 058's
   copilot-login (which requests scope=copilot), delete it:

```
rm ~/.agentic-dev-loop/auth/copilot.json
```

5. The pre-flight SDK check (commit 80a04d0) died with the restructure; its
   job is superseded by 058 D10 (run_smoke.py llm pre-flight scanning
   pipeline.yaml for api_key_env / api_key_secret).

## Propagation (Phase 4)

Applied 2026-11-06 by user directive ("Do phase 4 propagation"). One change
per Action; Actions with no file target yet state their status.

- **A2 - premise-probe rule** -> `AGENTS.md` "Reverse engineering
  undocumented systems": section intro now names both incidents; new
  Prevent bullet: never build an architecture on an unvalidated
  external-SDK capability claim (probe the pinned artifact, record the
  validated-model table, docs are not facts, driver code only against a
  probed interface).
- **A5 - AI-DLC external-surface extension** -> new rule set
  `.aidlc/aidlc-rules/aws-aidlc-rule-details/extensions/design/external-surfaces/`
  (`external-surfaces.md` + `external-surfaces.opt-in.md`, EXTSRF-01..04:
  pinned-version evaluation, claims are not facts, gap -> spike loudly with
  the spike result as a design-stage completion condition, validated-model
  record) and registered as enabled in `aidlc-docs/aidlc-state.md`
  Extension Configuration.
- **D1 - QA verdict semantics** -> `agentic-dev-loop/sdk_agents/qa.agent.md`
  OUTPUT rules: PASS now requires the plan's headline feature to be
  verified; an unverifiable headline feature forces VERDICT: UNVERIFIABLE
  naming the feature and its blocking reason. OPEN: the dogfood pipeline's
  qa step still routes `on_unverifiable: commit`, so the new verdict does
  not yet block a commit - changing that routing is a one-line
  `agentic-dev-loop/pipeline.yaml` edit awaiting the user's decision.
- **A1 (premise probe as process gate)** - superseded by A2 + A5 (the probe
  obligation now lives in both the agent instructions and the AI-DLC rule
  set); no separate change.
- **A3 (SDK presence for driver code)** - absorbed into A2's "driver code
  is written only against a probed interface"; no separate change.
- **A4 (brainstorming scope discipline)** -> `.opencode/skills/brainstorming/SKILL.md`: new "Scope Boundary: Ideas, Not Validated Designs" section - brainstorming produces design options, never commits unverified external-surface claims as architecture, and defers validation of those claims to the design stage (EXTSRF gate).
- **D3 (real-token smoke)** - already queued and implemented via
  planning-docs/058-copilot-as-endpoint.md (D9/D10) and the smoke config
  dirs; nothing further.
- **P1 (completion definition)** - enforced by D1's verdict rule once the
  pipeline routing question above is resolved; open until then.
- **P2 (vigilance is not a control)** - same queue as D3; closed with 058's
  real-token gates.
- **P3 (QA evidence persistence)** - unowned and unscheduled; the QA report
  and review findings still live only in the ephemeral run directory.
