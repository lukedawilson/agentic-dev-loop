# External Surface Validation Rules

## Overview
These rules apply whenever a design (requirements, functional design,
infrastructure design, or code generation) depends on an external surface: an
SDK, a CLI harness, an HTTP API, an auth flow, or a protocol. They are
MANDATORY for any unit whose design touches an external surface, and N/A for
units that do not.

Born from the unit-10 postmortem
(docs/incidents/2026-11-05-unit-10-false-premise.md): the Copilot driver was
built on an unprobed SDK capability claim and shipped unusable, and the whole
unit had to be reverted.

**Enforcement**: At each applicable stage, the model MUST verify compliance
with these rules before presenting the stage completion message to the user.

### Blocking Finding Behavior
A **blocking finding** means:
1. The finding MUST be listed in the stage completion message under an
   "External Surface Findings" section with the EXTSRF rule ID and
   description
2. The stage MUST NOT present the "Continue to Next Stage" option until all
   blocking findings are resolved
3. The model MUST present only the "Request Changes" option with a clear
   explanation of what needs to change
4. The finding MUST be logged in `aidlc-docs/audit.md` with the EXTSRF rule
   ID, description, and stage context

If an EXTSRF rule is not applicable to the current project (e.g., EXTSRF-01
when the design touches no external surface), mark it as **N/A** in the
compliance summary - this is not a blocking finding.

### Default Enforcement
All rules in this document are **blocking** by default when the design
touches an external surface. If any rule's verification criteria are not
met, it is a blocking finding - follow the blocking finding behavior defined
above.

---

## Rule EXTSRF-01: Pinned-version evaluation

**Rule**: Every external-surface capability the design depends on MUST be
evaluated against the pinned artifact version before the stage completes.
Evaluation = install the exact pinned version and invoke the real interface,
or hit the real endpoint with a real credential where the surface is an API.

**Verification**:
- Every capability claim in the design maps to a probe result, not to
  documentation
- The probed version is the version the implementation will be built and
  tested against
- For APIs: the probe used a real credential; header, scope, and model
  assumptions are confirmed by the probe or explicitly marked unconfirmed

---

## Rule EXTSRF-02: Capability claims are not facts

**Rule**: A capability claim sourced from docs, blogs, changelogs, or
intuition MUST be recorded as UNVERIFIED until a probe establishes it.
UNVERIFIED claims may not drive architecture decisions.

**Verification**:
- Design artifacts list each external-surface assumption with its source and
  status (probed / unverified)
- No architecture decision is justified by an unverified claim alone

---

## Rule EXTSRF-03: Gap -> spike, loudly

**Rule**: When a probe shows a claimed capability missing or different
(absent class, missing constructor arguments, no auth surface, different
login types), the stage MUST surface the gap loudly in the completion
message with the specific mismatch, and MUST recommend a spike to establish
the real surface. The spike result is a completion condition for the
affected design stage; the gap must never be deferred past it.

**Verification**:
- Every probed mismatch appears in the stage completion message
- A spike is recommended for each mismatch
- The design stage does not complete while a spike is outstanding

---

## Rule EXTSRF-04: Validated-model record

**Rule**: Each probed fact MUST be recorded in a validated-model table
(Fact | Established by (probe) | Date) in the stage artifacts. Unverified
assumptions are listed separately.

**Verification**:
- Stage artifacts contain the table with probe evidence per fact
- No probed fact lacks its probe description
