# deja-vu context architecture: memory model and AI-DLC extension

Date: 2026-11-27
Status: approved design, pending implementation plan

## 1. Problem and goal

The host session's fixed context keeps growing. Today the always-loaded
surface includes: repo AGENTS.md (12,516 bytes), core-workflow.md
(25,019 bytes), the four common rules, the deja skills and MCP
instructions, and on every resume the session-continuity bulk-load
mandate. The corpus under .aidlc/aidlc-rules/ is 368 KB across 36 files.

The user's statement of the problem: "Context rot isn't just stale
context, it's the context getting so big the model hallucinates."

Goal: replace gigantic always-loaded system prompts with dynamic
retrieval from deja-vu, so context is loaded only when a phase actually
needs it, and sessions rotate at unit boundaries instead of accumulating
history.

## 2. Scope

In scope:

- The AI-DLC workflow corpus (.aidlc/aidlc-rules/): how we drive work
  in this repo.
- The repo AGENTS.md: slimming pass.

Out of scope:

- The engine source (src/agentic_dev_loop, build_context,
  resolve_unit_docs). Rationale, in the user's words: "we're changing
  how we build the engine, not how the engine behaves when being used.
  We do NOT want to enforce a dependency on deja vu on our consumers!"
  Consumers of the engine get no deja-vu dependency, ever.
- Global personal files (~/.claude/CLAUDE.md, the global OpenCode
  AGENTS.md): deja rules is the master for new personal rules only;
  existing files stay until a separate, later consolidation pass.

## 3. Current state (verified facts)

- deja-vu 0.25.1 installed globally (2026-11-26): plugin, local MCP
  server, hooks, skills deja-history and deja-search, /deja command,
  DEJA_OFFLINE=1. Index at ~/.cache/deja/index.db; at install it held
  303 sessions / 40,156 messages across 8 harnesses.
- Automatic injection verified live: recall reference blocks attach to
  file reads; session-start injection of recent project sessions.
- Redaction verified at the recall boundary: 61 raw credentials across
  39 sessions in the transcript stores; recall output shows placeholders
  only.
- Zero LLM calls anywhere in the pipeline; no API keys.

## 4. Memory model (usage patterns)

Four layers, one fact in exactly one place:

1. Repo memory: .deja/memory.jsonl per repo, kinds decision, rejected,
   gotcha. Committed to the repo; every change is a user-reviewed diff.
   Scope: every repo the user works in.
2. Personal rules: the deja rules file, machine-global. Candidates flow
   only: a rule must appear in at least 2 distinct sessions; at most 15
   rules, most recurring first; the user approves the exact lines before
   deja rules sync copies them to every installed agent.
3. Repo rules: AGENTS.md, slimmed per section 7.
4. Transcripts: automatic index, no curation.

Write rules:

- decision: a settled choice with what was decided, why (alternatives
  considered, tradeoffs), written at decision time.
- rejected: what was tried and why it failed.
- gotcha: a non-obvious trap not visible from the code; self-contained,
  dated, reproducible.
- One fact per entry; ASCII; no duplication with AGENTS.md or with an
  existing entry.

Prune rules:

- gotcha fixed in the code: delete the entry.
- decision replaced: mark the old one superseded with a pointer; delete
  it only when its reasoning adds nothing.
- rule wrong: edit, user reviews.
- duplicate: refuse the new entry and strengthen the existing one.

Rot protocol: curate on correction, prune on proof. No scheduled
audits.

Context-budget policy:

- Retrieval, not injection. Memory never moves into always-loaded
  files; this policy itself stays out of AGENTS.md.
- Budgets: rules hard-capped at 15, replace not append; entries one
  fact, tight, ASCII; consolidation over accumulation.
- Tripwire: memory.jsonl over 50 entries stops new entries until a
  consolidation pass runs.
- Injection hygiene: exact terms, one specific mode per call, batched
  with file reads, no exploratory sweeps.

## 5. Boundary protocol

What the old session writes at unit end (curation, one time, bounded):

1. Repo memory entries for the unit via deja repo add (decision,
   rejected, gotcha), plus deja repo candidates to catch anything
   missed. The user reviews the .deja/memory.jsonl diff and commits it.
2. A deja handoff/wip entry: where the unit stands, what the next
   session picks up.
3. aidlc-state.md: Current Stage / Unit update (existing protocol).
4. audit.md: continuity log (existing protocol).

What the new session loads at start (lean, bounded):

1. The workflow skeleton only: the core-workflow.md mandatory section
   plus the four common rules.
2. The Current Stage line from aidlc-state.md.

That is the entire fixed load. No bulk artifacts, and no mandated
summary read. Retrieval is the operating mode from the first moment:
recall, context, blame, fix, how, orient pull bounded pages on demand.
The committed repo memory file is one retrieval source among these (the
distilled, user-approved layer), pulled when the session needs
decisions or gotchas, never force-loaded.

Rotation rule: at a unit boundary, spawn a new session. Compact only
when spawning is impossible. The deja index, repo memory, and the
handoff entry carry continuity in both cases.

## 6. Mode mapping

Stage names as verified in core-workflow.md. INCEPTION: Workspace
Detection, Reverse Engineering, Workflow Planning, Units Generation.
CONSTRUCTION per-unit loop: Functional Design, NFR Requirements, NFR
Design, Infrastructure Design (all conditional), Code Generation
(always; part 1 planning, part 2 generation). Then Build and Test
(after all units), then Operations.

- Unit start: recall on the unit slug, or orient if the unit is
  unfamiliar. At most 2 pages.
- Stage entry: blame on the artifact paths the stage will actually
  read or write (its rule file, the unit plan files), one page per
  file, only files the stage touches. recall on stage name plus unit
  slug, one page, only if the stage has prior-session history.
- Before running any build or test command: how, one page (real flags
  this machine runs, ranked by session count).
- On an unfamiliar failure: fix with the failing output verbatim, one
  page.
- Before presenting a design or plan for approval: recall on the
  concrete names in the plan (files, libraries, patterns), batched
  with the plan's file reads.
- Before presenting a stage's completion message: recall on unit slug
  plus stage name, one page (did the user correct this stage before,
  was anything missed).

Budgets: at most 3 retrieval pages per stage, and a retrieval fires
only on a concrete trigger (a file to touch, a command to run, a
failure, a plan to present). "Nearest by wording, none about it" is a
negative result: stop, never paginate. --offset paging only when the
first page names the exact target and more detail is needed. No
retrieval while sitting in an approval-wait window.

## 7. Edit inventory and order

Implementation order is fail-safe: retrieval must exist before anything
leaves a always-loaded file.

1. New file: extensions/context/deja-context.md. No opt-in stub, which
   per the core-workflow extension-loading rule makes it always
   enforced. Contents: sections 5 and 6 plus the budgets. It references
   the deja skills (deja-history, deja-search) for mode mechanics
   instead of restating them. The skills stay as they are; they remain
   the guidance for non-AI-DLC sessions.
2. Modified file: session-continuity.md. The MANDATORY bulk artifact
   loading section is replaced by: skeleton load (core-workflow
   mandatory plus four common rules plus the Current Stage line) plus
   the retrieval instructions plus the spawn-at-unit-boundary protocol.
   Everything else in the file stays: welcome message, audit.md
   logging, inline questions with [Answer]: tags.
3. Modified file: repo AGENTS.md, slimming pass. Classification:
   - Stays, reduced to its imperative: every rule whose violation is
     an incident. Em dash ban, consumer name ban, silent-fallback ban,
     deploy-status-check ban, never-merge-uninstructed, TDD mandate,
     mock policy, backward-compat mandate.
   - Moves to repo memory as decision entries (what plus why): the
     reasoning behind those rules, for example the Framer incident and
     the unit-10 false-premise incident, the SDK compat shim
     rationale.
   - Deleted: prose that restates another rule or retells history the
     transcript index already holds.
   - Every line of the diff is user-reviewed before it lands; rule
     text moves out one class at a time, never all at once.
   - Reconciliation with the memory model: the earlier rule "memory
     holds only what AGENTS.md lacks" still applies at every moment.
     AGENTS.md content that moves out becomes repo memory entries; the
     two never hold the same fact.

Unchanged: core-workflow.md (its extension mechanism already enforces
the new file with no edits), all engine code, all deja-vu mechanism
files.

## 8. Verification items

To be proven at implementation time:

1. Spawn mechanics: the concrete OpenCode V2 step for spawning a fresh
   session at a unit boundary, including how the new session starts
   inside the repo with the AI-DLC workflow active.
2. Page-size arithmetic: measure one real recall, blame, and how page;
   write the token ledger of fixed load before and after (AGENTS.md,
   core-workflow.md, four common rules, deja skills and MCP
   instructions) against the per-unit retrieval load. This is the check
   that the savings beat the deja tax.
3. Injection scoping: confirm the automatic session-start and
   file-read injection does not double-inject with the extension's
   orient step.
4. Common-rules sizes: while revising session-continuity.md, record the
   sizes of the other three common rules. Report only; do not expand
   scope.

## 9. Non-goals

- No engine changes; no consumer-facing dependency on deja-vu.
- No scheduled memory audits.
- No changes to deja-vu's own mechanisms (injection, redaction,
  indexing stay as installed).
- No consolidation of the global personal files (CLAUDE.md and the
  global OpenCode AGENTS.md) in this work.

## 10. Decisions recorded

- Rotation: spawn at unit boundaries, compact only when impossible.
- Personal rules: deja rules handles new rules only for now.
- Duplication question: the extension references the deja skills
  rather than restating them; the skills stay for non-AI-DLC sessions.
- Scope boundary and rationale: section 2.
