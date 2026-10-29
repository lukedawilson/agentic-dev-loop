# 034-agent-facing-error-guide.md

> **Status: COMPLETED**

Independent of the other churn plans; pairs with 036 (once the deny-list
ships, its structured "[forbidden: <label>]" observation gets a guide
entry too).

## Scope

Engine (one bundled resource + injection into every agent step), README
documentation. No consumer wiring.

## Context

Consumer run 5367f0c488d8: a terminal command was refused with AE-SBX-016
("sync-back conflict in <path> - the real repo changed underneath; edits
to these paths will not sync (<domain-rule-id>)"). The message is
operator-grade: it names an engine mechanism and a domain rule ID, and
tells the agent nothing about what happened or what to do. The agent
debated its own retry policy against the message, retried on a judgment
call, and recovered, but it burned turns guessing. Every agent step that
runs terminal commands (dev, review, qa, brief) can hit agent-visible
engine errors like this one, because the checks that produce them are
engine-level.

User decision (the considered options were recorded in chat): the engine
injects the guide into every agent step. Engine injection wins over the
brief path because the codes are engine-owned knowledge that must be
versioned with the engine code that emits them; routing them through the
consumer-owned brief relocates the same injection one hop away and loses
it whenever the hash-gated brief does not regenerate.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | Engine-owned guide resource bundled with the engine, injected into every agent step's task context (like `additional_files`, but engine-owned and always on) | AGREED |
| D2 | One line per agent-visible error code: code + plain meaning + correct agent response. Only codes an agent can actually observe; internal codes stay out | AGREED |
| D3 | The guide text is consumer-agnostic. Engine messages may reference consumer domain rule IDs; the guide explains the general mechanism without depending on any specific ID | AGREED |
| D4 | Engine changes to agent-visible errors update the guide in the same commit; a registry test enforces the pairing | AGREED |

## Changes

### 1. Bundled guide resource (engine)

New engine resource (for example
`src/agentic_dev_loop/engine/agent_error_guide.md`) holding the code
table. Versioned with the engine so the codes and their guidance cannot
drift.

### 2. Injection (engine)

Every agent step's task context gains the guide block at assembly time,
before the agent prompt's context sections. Command and action steps do
not need it (no agent reads them). No consumer wiring, no opt-in.

### 3. Guide format

One line per code:

| Code | Meaning | Correct response |
| --- | --- | --- |
| AE-SBX-016 | The loop refused to sync an edit because the real repo changed underneath on a path this workspace also touches; the file in this workspace is stale and its edits will not be applied | The command is not the problem: retry once, do not edit or work around the named path, and mention the code in your final response if it persists |

The full list of agent-visible codes is enumerated from the engine source
at implementation time (the AE-SBX-* family; AE-SBX-016 is the only one
observed in the wild). Further entries follow the same shape as codes are
added, and 036's "[forbidden: <label>]" observation gets an entry when
that plan lands.

### 4. Naming rule (engine)

Guide text never references a consumer rule ID or consumer name; the
placeholder form `<domain-rule-id>` is used if an ID appears inside a
quoted engine message.

### 5. README.md documentation (tooling path)

Update README.md to document the guide resource, its format, the
injection rule (every agent step, always on, no opt-out in v1), and the
requirement that engine changes to agent-visible errors update the guide
in the same commit.

## Tooling-path deliverables

README.md only. The guide resource itself ships inside the engine
package. Human commit required for README.md; the loop commit will not
sweep tooling paths.

## Verification

- TDD: injection test (a fake agent step's assembled context contains
  the guide block; a command step's does not), format test (every
  agent-visible error code the engine can emit has exactly one guide
  line; adding a new agent-visible code without a line fails the test),
  content test (no consumer names, ASCII only).
- Backward compatibility: additive context only; no config surface
  changes.
- Post-deploy observation: an agent hitting an engine error responds per
  the guide on the first turn instead of debating the error.
