# Unit 4 Infrastructure Design Plan - Claude Dynamic Workflows driver

Status: in progress. Next stage after Infrastructure Design = Code Generation.

## Stage context

Unit-4 has no traditional infrastructure (no servers beyond the localhost viz, no Docker). The infrastructure here is **packaging & distribution**: the module split layout, wheel extras, the skill installation path, the generated workflow JS artifact, CI wiring, and the consumer migration mechanics. Design inputs: FD (KD-1..10, BR-1..13) + NFR (4.1 extras split, 4.2 hard break w/ migration list, 4.6 no added security, 4.7 golden tests + unit/PBT, positioning = deterministic loop is the product).

## Open questions

### ID-1 - Installer entry point (extras vs engine deps)

[Answer]: REFINED (2026-10-21) - requirement first: workflow-mode install + skill install MUST work on Windows as well as macOS/Linux (posix + powershell entry points). Mechanism: separate console script only if necessary; the necessity is real - the light `[workflow]` install (core+viz, no openhands-sdk) requires the engine CLI to move out of the base package, so the extras are INVERTED (base = core+viz light, `[engine]` extra = openhands-sdk + main `agentic-dev-loop` CLI). Consequence: base ships `agentic-dev-loop-workflow` (posix+powershell); `[engine]` extra adds the existing `agentic-dev-loop` entry point (posix+powershell). One binary would force the base to carry engine deps - defeats the light install. (Raw: "A (posix and powershell)" then "Well, only separate scripts if necessary - the point is we need to support Windows as well")

### ID-2 - Generated workflow JS artifact placement

[Answer]: A - stable workspace file `.agentic-dev-loop/workflow.js`, consumed by the CC workflow-creation flow; inspectable, diffable, golden-testable. (Raw: "A")

### ID-3 - Skill layout + upgrade behaviour

[Answer]: A - idempotent overwrite: `agentic-dev-loop-workflow install-skill` copies the wheel's SKILL.md + workflow JS template into `~/.claude/skills/agentic-dev-loop/`, overwriting on every run; a version marker file records the installed version; skill files are never user-edited (upgrades safe, wheel = single source of truth, Windows-safe). (Raw: "A")

### ID-4 - CI wiring for unit/PBT/golden/guard tests

[Answer]: B - dedicated workflow-mode job on both OSes: install `agentic-dev-loop[workflow]` only → run projection on a fixture pipeline → render template → golden compare; proves the extras split has no hidden engine imports; existing engine job + release.yml unchanged. (Raw: "B")

### ID-5 - Consumer migration list mechanics

[Answer]: A - docs + in-repo updates at implementation: CHANGELOG/release-notes carry an old→new import table; this repo's pipeline.yaml `extensions:` string and docs examples updated; the consumer repo gets a documented upgrade note; no migration script. (Raw: "A")

## Steps

- [x] Stage context analysis (FD + NFR inputs)
- [ ] Resolve open questions (ID-1..ID-5, inline one at a time)
- [ ] Generate artifact: deployment-architecture.md under aidlc-docs/construction/unit-4/infrastructure-design/
- [ ] Completion message + approval
