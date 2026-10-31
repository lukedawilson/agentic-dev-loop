# Plan 051: 049 rollout - pipeline and consumer migration edits

> **Status: DRAFT (hold-off directive active)**
>
> The engine-side implementation of plan 049 (native brief step type) is in
> flight elsewhere. This plan records the concrete edits that follow it, in
> exact before/after form, so they can be applied immediately once the engine
> lands. Per the user's instruction these edits are planned only, not applied.

## Context

Plan 049 (Status APPROVED) makes `brief` a native step type: `brief_inputs:`
becomes the discriminator, and the engine owns the prompt (046), the capture
file (`attempt-brief.md`), and the 12-iteration cap. Its changes list:

1. `src/agentic_dev_loop/core/config.py` - `BRIEF_PRODUCES` /
   `BRIEF_MAX_ITERATIONS` constants, `_check_shape` rework, `kind == "brief"`.
2. `src/agentic_dev_loop/engine/agents.py` - `BRIEF_AGENT_NAME` UUID4
   constant; `load_brief_agent` shape unchanged, call sites pass the constant.
3. `src/agentic_dev_loop/engine/stages.py` - brief call sites use
   `BRIEF_AGENT_NAME`, prints use `step.name`, vendored-file warning checks
   the literal filename `brief.agent.md`, `build_context` expected-output
   paths accept `kind in ("agent", "brief")`.
4. `src/agentic_dev_loop/engine/graph.py` - `kind == "brief"` branch to
   `make_brief_node`.
5. `src/agentic_dev_loop/engine/dryrun.py` - `_step_summary` brief branch.

Items 1-5 are being implemented outside this session. Items 6-8 and the
consumer follow-up (046 D5) are the subject of this plan. Item 9 (publish
registry entry for `-native-brief-step-type`) is already done and committed
with tests.

## Gates (must be true before applying these edits)

- G1: `load_config` accepts a step with `brief_inputs:` and no
  `agent`/`action`/`command`; `step.kind == "brief"`.
- G2: parse-time normalization sets `produces == ["attempt-brief.md"]` and
  `max_iterations == 12` on brief steps (constants `BRIEF_PRODUCES`,
  `BRIEF_MAX_ITERATIONS`).
- G3: the old shape (`agent: brief` + `produces`/`max_iterations`) fails
  validation with a message naming the fix.
- G4: graph/stages/dryrun dispatch on `kind == "brief"` and the internal
  agent is named `BRIEF_AGENT_NAME` (the GUID), never `"brief"`.
- G5: the engine implementation is committed to this repo first. The exact
  error strings it emits are owned by that implementation; tests in Edit 3
  reference message shapes and pin exact text after it lands.

## Edit 1: dogfood pipeline (`agentic-dev-loop/pipeline.yaml`)

Replace the brief step (lines 41-68). Drop `agent: brief`,
`produces: attempt-brief.md`, `max_iterations: 12`. Keep `brief_inputs`
(include, sections, env_facts), `forbidden_commands`, `reasoning_effort`,
`on_pass`. Comment updates only where they named the brief "agent".

New block:

```yaml
  - name: brief                      # any name; brief_inputs is the type
    reasoning_effort: medium
    # Per-run context digestion: a closed input list (resolved relative to
    # the workspace root) plus the stable environment facts the brief step
    # must never discover by running commands. The resolved unit/plan docs and
    # additional_files (../README.md) arrive through the loader context.
    # `produces` (attempt-brief.md) and the 12-turn cap are engine-owned.
    brief_inputs:
      include: [AGENTS.md, coding-standards-ddd.yaml]
      # 046: single-source the digest headings - the coverage gate and the
      # embedded brief prompt both render from this list (neutral defaults).
      sections: [Repo rules digest, Unit scope digest, Environment facts, Open risks]
      env_facts:
        - the workspace is an export with no .git; verify with find/ls, never git status
        - the sandbox is network-off by default; never run package-manager fetches
        - Python packages resolve from .venv-sandbox; run .venv-sandbox/bin/python
    # Banned-command deny-list: custom entries append after the engine
    # defaults (git mutations, package installs, playwright, docker).
    # Releases and GitHub mutations stay operator-only: the loop never
    # publishes itself.
    forbidden_commands:
      - '\btools/publish\.py\b'
      - '\btwine\s+upload\b'
      - '\bgh\s+release\s+create\b'
      - '\bgh\s+pr\s+(create|merge|close)\b'
    on_pass: dev
```

## Edit 2: README.md

Three touch points (no em dashes, ASCII only):

- Example pipeline (lines 164-168): replace the brief step with

  ```yaml
    - name: brief
      reasoning_effort: medium
      brief_inputs: { include: [AGENTS.md, coding-standards-ddd.yaml], env_facts: ["the workspace is an export with no .git; verify with find/ls, never git status"] }
      on_pass: dev
  ```

- Table row `agent:` / `command:` / `action:` (line 208): add that a fourth
  type exists - `brief_inputs:` makes the step a native brief step, carrying
  none of the other three keys; the engine owns `produces`
  (`attempt-brief.md`) and the 12-iteration cap.
- Table row `brief_inputs:` (line 210): rewrite from "Optional agent-step
  input spec" to the native-step wording: `brief_inputs:` is the step type
  discriminator; the shape stays `{ include, env_facts, sections }`;
  `produces` and `max_iterations` are rejected on brief steps because the
  engine sets them (`attempt-brief.md`, 12).
- Prose (lines 252-253): "its `produces:` artifact survives attempt starts"
  becomes engine-owned wording ("its engine-owned `attempt-brief.md`
  artifact survives attempt starts"). Line 265 "re-runs the brief agent"
  becomes "re-runs the brief step".

## Edit 3: tests

### 3a. Mechanical fixture migration (drop `agent` + `produces` +
`max_iterations`, keep `brief_inputs`)

- `tests/test_033_brief_step.py`: `BRIEF_STEP` dict (lines 54-64); fixtures
  at lines 84, 99, 152, 162, 193, 203, 237, 260, 272, 285, 295 (plus any
  other `"agent": "brief"` hits - see sweep); dry-run pipelines at 524-525
  and 549-550; `BRIEF_PIPELINE` at 574-576.
- `tests/test_041_brief_coverage.py`: fixture at lines 82-84.
- `tests/test_e2e_fixtures.py`: pipeline stub at lines 187-188.
- `tests/test_056_*.py` or wherever the current file lives:
  `tests/test_agent_error_guide.py` line 161.

### 3b. Rejection-test reworks (assertions change, not fixtures)

In `tests/test_033_brief_step.py`:

- `test_rejected_on_command_step` (109): the old match
  `"'brief_inputs' requires 'agent'"` becomes the new exclusivity message
  (`brief_inputs` mutually exclusive with `command`).
- `test_rejected_on_action_step` (118): same, for `action`.
- `test_requires_produces` (127): inverts. A brief step now REJECTS
  `produces` with a message naming the engine-owned replacement. Rename to
  something like `test_produces_rejected_on_brief_step`.
- `test_rejects_artifact_verdicts` (138): message changes to the brief-step
  rejection (engine-owned fields list).

New rejection tests (049 item 8): each of `produces`, `artifact`,
`verdicts`, `params`, `max_iterations`, `timeout`, `custom_prompt`,
`finalize_nudge_turns` set on a brief step fails with a message naming the
engine-owned replacement where one exists; `terminal_timeout` and `setup`
remain valid on brief steps; `finalize_nudge_turns` still requires an agent
step; `brief_inputs` combined with `agent` fails.

New positive tests (049 item 8): defaults applied at parse (`produces ==
["attempt-brief.md"]`, `max_iterations == 12`); two brief steps with
different names both load and both get `kind == "brief"`; a step with only
`brief_inputs` (no `include`, no `env_facts`, no `sections`) loads and falls
back to `DEFAULT_BRIEF_SECTIONS`. Where these belong in `test_config.py` vs
`test_033_brief_step.py` is the implementer's call, matching the existing
layout.

### 3c. Internal agent name: the GUID rename

049 D3 names the internal brief agent `BRIEF_AGENT_NAME` (a UUID4), never
`"brief"`. Every assertion that observes the called agent name must use the
constant. Affected:

- `tests/test_033_brief_step.py` lines 376, 389, 402, 409, 631:
  `fake_agents.agents_called() == ["brief", ...]` becomes
  `[BRIEF_AGENT_NAME, ...]`.
- `tests/test_041_brief_coverage.py` lines 243, 263, 280: same.
- `tests/test_e2e_fixtures.py` lines 235, 316, 325, 342: same.
- `tests/test_agents.py` lines 98, 106: `load_brief_agent("brief", ...)`
  becomes `load_brief_agent(BRIEF_AGENT_NAME, ...)`.

Import `BRIEF_AGENT_NAME` from `agentic_dev_loop.engine.agents`.

### 3d. Vendored-file warning

Keep or adapt the existing vendored-brief warning test so it still fires on
the literal filename `brief.agent.md` (049 D3 keeps the literal-filename
check). If the engine implementation moves the warning test, mirror it.

### 3e. Sweep

After migration: `grep -rn 'agent: brief\|"agent": "brief"' tests/` returns
only deliberate old-shape rejection fixtures (3b). Zero remaining positive
fixtures use the old shape. `agents_called()` assertions never contain the
bare string `"brief"`.

## Edit 4: CHANGELOG.md `[Unreleased]`

Pre-write the Breaking entry so the next release moves it into the new
section (per publish.py mechanics, a non-empty Unreleased body moves into
the new section, and `enforce_consumer_adoption` then sees the `### Consumer
adoption` subsection; the 049 slug is already registered):

```markdown
### Breaking

- feat(brief): `brief` is now a native step type. `brief_inputs:` is the
  discriminator: a brief step carries no `agent:`, `produces:`, or
  `max_iterations:`. The engine owns the embedded prompt (046), the capture
  file (`attempt-brief.md`), and the 12-iteration cap. The old
  agent-disguise shape fails validation with a message naming the fix.

### Consumer adoption

- Rewrite the brief step in `agentic-dev-loop/pipeline.yaml` to the new
  shape: drop `agent:`, `produces:`, and `max_iterations:`; keep
  `brief_inputs:` with its declared `sections:` (omit `sections:` to fall
  back to the engine defaults).
- Delete the vendored `agentic-dev-loop/sdk_agents/brief.agent.md` (046 D5):
  the brief prompt is engine-embedded, and a file with that literal filename
  triggers the vendored-file warning and can no longer mint the internal
  agent name.
```

## Edit 5: consumer repo (`../yosk-cms`, separate commit)

Applied only after Edit 1-4 land in this repo and the release is cut (the
consumer pipeline must validate against the released engine).

### 5a. `../yosk-cms/agentic-dev-loop/pipeline.yaml` brief step (lines 74-99)

Drop `agent: brief`, `produces: attempt-brief.md`, `max_iterations: 12`.
Keep `brief_inputs` (include, env_facts; this pipeline declares no
`sections:`, so the neutral engine defaults apply), `forbidden_commands`,
`reasoning_effort: medium`, `on_pass: dev`.

```yaml
  - name: brief
    reasoning_effort: medium
    # Per-run context digestion: a closed input list (resolved relative to
    # the workspace root) plus the stable environment facts the brief step
    # must never discover by running commands. The resolved unit docs and
    # additional_files arrive through the loader context. `produces`
    # (attempt-brief.md) and the 12-turn cap are engine-owned.
    brief_inputs:
      include: [AGENTS.md, coding-standards-ddd.yaml]
      env_facts:
        - the workspace is an export with no .git; verify with find/ls, never git status
        - the sandbox is network-off by default; package fetches (dotnet restore, npm ci) run only in the network-on pipeline steps
        - 'database-tagged tests never run in the loop: dotnet test always runs --filter Category!=Database'
    # Banned-command deny-list: custom entries append after the engine
    # defaults (git mutations, package installs, playwright, docker).
    # GitHub mutations and infra deploys stay operator-only: the loop never
    # publishes, merges, or ships.
    forbidden_commands:
      - '\bgh\s+pr\s+(create|merge|close)\b'
      - '\bgh\s+release\s+create\b'
      - '\bdotnet\s+nuget\s+push\b'
      - '\bterraform\s+(apply|destroy)\b'
      - '\bansible-playbook\b'
    on_pass: dev
```

### 5b. Delete the vendored prompt

Delete `../yosk-cms/agentic-dev-loop/sdk_agents/brief.agent.md` (046 D5).
The consumer's `sdk_agents/` then holds only `dev.agent.md`, `qa.agent.md`,
`review.agent.md`. No consumer README references the brief step (checked:
no matches), so no doc edits follow.

## Sequencing

1. **Step A** (in flight elsewhere): engine items 1-5 land in this repo.
2. **Step B** (this repo, one commit): Edits 1-4 (pipeline.yaml, README.md,
   tests, CHANGELOG). Hold until Gate G1-G5 are true.
3. **Step C**: cut the release with `tools/publish.py`; the registry entry
   enforces the `### Consumer adoption` subsection.
4. **Step D** (consumer repo, separate commit): Edit 5 (5a + 5b).

Constraint: Step D strictly after Step C. The consumer pipeline in the new
shape must validate against the released engine, and 046 D5 makes the
consumer follow-up a separate commit by design.

## Verification checklist

- `load_config` on the migrated dogfood pipeline prints `kind == "brief"`,
  `produces == ["attempt-brief.md"]`, `max_iterations == 12`.
- `--dry-run` against this repo's migrated pipeline passes and reports the
  brief step as a brief-type step.
- The old shape fails validation with a message naming the fix (pinned by
  3b tests).
- Full suite green, ASCII-only (no em dashes; `tests/test_repo_ascii.py`).
- Sweep (3e) clean.
- Consumer: dry-run in `../yosk-cms` passes; the vendored file is gone; a
  fresh plan attempt then briefs with the consumer's own vocabulary.
- Release: `publish.py` accepts the entry only with the Consumer adoption
  subsection present (already pinned by
  `TestConsumerAdoption::test_049_marker_without_adoption_section_fails`).

## Open items owned by the in-flight implementation

- Exact error strings for the new validation messages (3b pins them after
  the implementation lands).
- Whether `load_brief_agent` keeps its `name` parameter (049 D3 says shape
  unchanged, call sites pass `BRIEF_AGENT_NAME`; test_agents.py 3c follows).
- Exact placement of new config tests (test_config.py vs test_033).
