---
name: plan
description: Use for writing planning docs under planning-docs/ (frontend, backend, or full-stack) with an exact file list, a dead-code removal section, and a two-subagent plan-review loop before the doc is handed over.
---

# Plan

Write a planning doc that follows this repo's planning-docs convention, and
validate it through a two-subagent review loop before handing it over.

**Announce at start:** "I'm using the plan skill to write and validate the planning doc."

## 1. Gather context

- The prompt is whatever the command invocation passed. Combine it with the
  conversation so far: the requirement may live in earlier turns.
- Read AGENTS.md. Planning docs are written ready: no DRAFT or not-ready
  markers, no placeholders, ASCII only (no em dash).
- Read the two newest plans in the target tier directory to match its
  conventions.

## 2. Determine the tier and load the rules

- The tier is frontend, backend, or full-stack, from the prompt. If it is not
  obvious, confirm with the user in one line before proceeding.
- Load the binding rules for the tier before writing. Rule sets are
  repo-specific; identify them from AGENTS.md and the repo's `.opencode/`
  tree, then read them in full:
  - Frontend or full-stack: the repo's frontend development rules, typically
    a `.opencode/skills/frontend-development/SKILL.md` skill plus the frontend
    lint sources it names (the load-frontend-rules set).
  - Backend or full-stack: the repo's backend coding standards, typically
    `coding-standards-ddd.yaml` at the repo root (the load-backend-rules set).
  - If the repo defines no rule set for the tier, state that in the plan's
    Assumptions and follow the repo's own conventions instead.
- Other directories (infra, spikes, user-journeys, selection docs) are not unit
  tiers; if the prompt targets one, follow that directory's existing convention.

## 3. Research the change against the tree before writing

Every fact the plan states must be checked against the repo before it is
written. Two tests per claim: could the dev agent find this by reading the
code? Will it actually go and look? Anything it could find, you find now:

- Every file path, type, method, and line reference: verify with grep/ls/read.
- Every claim about live behavior (transport state, annotation ids, serving
  routes, publishing semantics): verify against the code, or against a probe
  you can run locally. Unverifiable claims are not facts: they go in the
  Assumptions section as Unverified.
- When the unit removes a feature, machinery, or a surface: run the sibling
  sweep now (error-code members whose only readers are the removed path,
  always-null parameters, import-time registrations, tests, docs, qa fixtures,
  and scripts referencing it) and enumerate the results in the Dead code
  section with file:line.

## 4. Write the plan

- Location: `planning-docs/<tier>/<NNN>-<slug>.md` where NNN is the highest
  existing number in that directory plus one, zero-padded to three digits.
  List the directory; never guess the number.
- The doc is written ready. If a decision needs a user answer, ask first, then
  write the answer in. No placeholders survive.
- ASCII only: no em dash, no non-ASCII characters.
- Sections, in order:

```markdown
# <Title> - Plan

**Goal:** one or two sentences.

**Size:** Small | Medium | Large, and one line on why.

**Tech stack:** the layers and files the unit touches.

## Evidence
What was observed that motivates this unit, with file:line or probe citations.

## Files
**Create:** `exact/path` (what it is)
**Modify:** `exact/path` (what changes and where)
**Delete:** `exact/path` (why it is dead)
**Read (context only):** `exact/path` (why the dev agent needs it)

## Design
Numbered sections, one per decision. Rejected alternatives stated with the
reason, so the dev agent does not re-litigate them.

## Dead code to remove
Present only when the unit removes something. First paragraph:

The dev agent MUST run its own sweep, not only verify the entries below: for
every removed symbol, grep for siblings of the same kind (error-code members
whose only readers were the removed path, call sites passing an always-null
parameter, import-time registrations the removed feature no longer reaches,
tests, docs, qa fixtures, and scripts referencing it). This list is a floor,
not the set.

Then the enumerated entries with file:line from the step-3 sweep.

## Assumptions
- Verified: `<claim>` - checked against `<file:line or probe>`
- Unverified: `<claim>` - resolved in the Verification plan or accepted by the user

## Tests
Which test files and suites change, and what each new test asserts.

## Verification plan
What proves the unit landed: mechanical gates, probes, live checks. Every
Unverified assumption must be resolved here or explicitly accepted by the
user.

## Out of scope
What the unit deliberately does not do.

## Sequencing
Order relative to other units; how the unit is run (for backend and
full-stack units consumed by the agentic-dev-loop, check AGENTS.md for the
plan trigger; yosk repos use `./trigger-agentic-dev-loop.sh --plan <this
file>`).

## Decisions (recorded ready)
Numbered list, each decision stated as made.

## Review findings (recorded ready)
Filled in by the review loop below.
```

## 5. Review loop

Dispatch two subagents in parallel (the Task tool or the session's subagent
tool), both under the strongest available model (deepseek-v4-pro here), each
with fresh context: send it the plan file path, its checklist, and read access
to the repo, nothing else. Do not include this conversation.

Agent 1, mechanical truth:

1. Codebase alignment: every path, type, member, and line reference in the
   plan resolves in the tree. Verify with grep/ls; report only what does not
   exist or does not match the plan's description of it.
2. Removal completeness: for every symbol the plan removes, are the siblings
   covered, enumerated with file:line or handed to the dev agent's mandatory
   sweep?

Agent 2, assumptions and blind spots:

1. Assumption check: every claim about live behavior either matches the code
   or is marked Unverified with a Verification plan item. Flag claims stated
   as fact that cannot be shown to have been checked.
2. Blind spots: read the plan cold; name what the dev agent will stumble on
   that the plan does not answer. Each finding must state the consequence.

Severity and stop rule:

- Critical: the plan names something that does not exist, states behavior the
  code contradicts, or requires something the system cannot do.
- Important: incomplete removal surface, an unverified assumption that changes
  the work, a decision with no test coverage.
- Minor: wording and format. Fix inline; no re-review.
- Apply fixes to the plan, record every finding (severity, what changed, or
  why rejected) in Review findings, then re-review only if the last round had
  Critical or Important findings.
- Stop when a round returns no Critical or Important findings, or after 3
  rounds. At 3 rounds with findings remaining, stop and report to the user
  instead of patching further; the plan likely needs scope renegotiation.

The loop is plain conversation orchestration: no state file beyond the plan's
own Review findings section.

## 6. Hand over

Present the validated plan to the user. Do not run the agentic-dev-loop, do
not implement, do not commit; the user decides the next step.

## Relationship to other skills

- brainstorming produces designs in docs/superpowers/specs/; writing-plans
  produces implementation plans in docs/superpowers/plans/. This skill is the
  planning-docs/ convention only. Do not route planning-docs work through
  those skills, and do not write docs/superpowers plans from this skill.
