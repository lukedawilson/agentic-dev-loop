# Plan 049: Brief as a native step type

> **Status: COMPLETED**

## Scope

`brief` stops being an agent step in disguise and becomes a first-class
step type. Today the engine recognizes a brief step only by the optional
`brief_inputs` field riding on a generic agent step (`agent: brief` plus
`produces: attempt-brief.md` plus `max_iterations: 12`), which lets
`brief_inputs` attach to any agent step and smuggles the brief contract
through fields that belong to file-loaded agents. After this plan, the
brief step is:

```yaml
  - name: brief                      # any name; brief_inputs is the type
    reasoning_effort: medium
    brief_inputs:
      include: [AGENTS.md, coding-standards-ddd.yaml]
      sections: [Repo rules digest, Unit scope digest, Environment facts, Open risks]
      env_facts: [ ... ]
    forbidden_commands: [ ... ]
    on_pass: dev
```

`brief_inputs:` is the discriminator. There is no `agent:`, no
`produces:`, no `max_iterations:`. The prompt is the engine-embedded one
from 046, the output file and iteration cap are engine-owned, and the
internal agent name is collision-proof.

## Context

Verified facts:

- `StepConfig` (src/agentic_dev_loop/core/config.py) requires exactly
  one of `agent`/`action`/`command`; `brief_inputs` requires `agent` and
  `produces` and rejects `artifact`/`verdicts`. `kind` returns only
  'agent'/'command'/'action'. `brief_inputs` is therefore a field any
  agent step may carry, and brief-ness is a sub-case of agent-ness.
- `graph.py` line 95 dispatches `kind == "agent"` with
  `brief_inputs is not None -> make_brief_node`. Brief has no branch of
  its own.
- `make_brief_node` (stages.py) reads `step.agent` in four places: the
  vendored-file warning (781), the run print and `run_agent` call
  (848-849), and the same pair in the generic agent node path (679-680).
  `run_agent` line 185 keys the embedded-prompt loader off
  `step.brief_inputs is not None` and passes `agent_name` (= step.agent,
  the string "brief") into `load_brief_agent`.
- `load_brief_agent` (agents.py line 63) builds an `AgentDefinition`
  whose name is that argument. The SDK subagent registry
  (`openhands/sdk/subagent/registry.py`) is a global dict keyed by agent
  name, but the engine's two loader paths call
  `agent_definition_to_factory` directly and never register, so the
  engine has no functional name collision today. The name still appears
  in conversation events, and a consumer vendoring a custom
  `brief.agent.md` would mint a second AgentDefinition named "brief".
- The embedded prompt (brief_prompt.md line 16) already states the
  capture file name: "The pipeline captures it as `attempt-brief.md`."
  The capture write path (run_agent line 213) uses `step.produces[0]`;
  hygiene (`declared_artifacts`, `run_scoped_artifacts`), `_artifact_file`,
  the expected-output-paths block of `build_context` (line 401, which
  skips steps with `agent is None`), and the retry classification
  (line 1083) all read `step.produces`.
- `DEFAULT_MAX_ITERATIONS` is 500 (stages.py line 59); the dogfood brief
  step runs with 12, which must become the engine's brief default.
- Agent-only field validators key on `self.agent is not None`:
  `terminal_timeout`, `finalize_nudge_turns`, `setup`. Brief runs a
  sandboxed LLM agent, so `terminal_timeout` and `setup` stay valid;
  `finalize_nudge_turns` does not (no verdicts, engine-owned cap).
- `reasoning_effort` (047) is an unguarded passthrough consumed in
  `run_agent` and already works for brief steps.
- The consumer repo still carries the pre-046 brief shape plus a
  vendored brief prompt file; its migration follows this plan (046 D5).

## Decisions

| # | Decision | Proposal | Answer |
|---|---|---|---|
| D1 | Step type: `brief_inputs:` presence is the discriminator. A step carries exactly one of `agent`/`action`/`command`/`brief_inputs`; `kind` returns 'brief' when `brief_inputs` is set. `name` stays required but free-form; multiple brief steps with different names are allowed | brief_inputs is the type, name free-form | AGREED |
| D2 | Engine-owned fields: a brief step rejects `produces`, `artifact`, `verdicts`, `params`, `max_iterations`, `timeout`, `custom_prompt`, `finalize_nudge_turns`. At parse time the engine sets `produces := ["attempt-brief.md"]` and `max_iterations := 12` so every downstream consumer (capture write, hygiene, context, retry classification) works unchanged | reject + engine defaults | AGREED |
| D3 | Internal agent name: `load_brief_agent` receives a fixed UUID4 string constant (`BRIEF_AGENT_NAME`), not "brief", so no consumer file stem can mint a same-named AgentDefinition. The vendored-file warning still checks the literal filename `brief.agent.md` (that is the file consumers must delete) | fixed GUID constant, literal filename for the warning | AGREED |
| D4 | Agent-like allowances: `terminal_timeout`, `setup`, `secrets`, `env`, `network`, `mounts`, `egress`, `forbidden_commands`, `reasoning_effort` remain valid on brief steps | allowed | AGREED |
| D5 | Call-site cleanup: brief node prints and `run_agent` calls use the GUID constant for the loader and `step.name` for logs; `build_context` expected-output-paths includes brief steps; `graph.py` dispatches `kind == "brief"`; dry-run summary gets a brief branch | clean dispatch | AGREED |
| D6 | Migration, fail loud: the old shape (`agent: brief` + `brief_inputs` + `produces`/`max_iterations`) fails validation with a message naming the fix. The dogfood pipeline and README are tooling paths and will need a human commit (plan 020). The consumer repo deletes its vendored brief prompt file and adopts the new shape in a separate consumer commit (046 D5) | fail loud, human commit for tooling paths | AGREED |

## Changes (file by file)

1. `src/agentic_dev_loop/core/config.py`: new constants
   `BRIEF_PRODUCES = ["attempt-brief.md"]` and `BRIEF_MAX_ITERATIONS = 12`
   next to `DEFAULT_BRIEF_SECTIONS`. `StepConfig._check_shape` reworked:
   - exactly one of `agent`/`action`/`command`/`brief_inputs` (empty
     values do not count);
   - `brief_inputs` mutually exclusive with `agent`, `action`, `command`,
     and rejected with `produces`, `artifact`, `verdicts`, `params`,
     `max_iterations`, `timeout`, `custom_prompt`,
     `finalize_nudge_turns`, each with an error naming the engine-owned
     replacement where one exists;
   - `terminal_timeout` and `setup` validators extended to accept brief
     steps; `finalize_nudge_turns` keeps requiring an agent step;
   - after validation, brief steps are normalized to
     `produces = BRIEF_PRODUCES`, `max_iterations = BRIEF_MAX_ITERATIONS`.
   - `kind` property returns 'brief' when `brief_inputs is not None`.
2. `src/agentic_dev_loop/engine/agents.py`: `BRIEF_AGENT_NAME` fixed
   UUID4 constant; `load_brief_agent` unchanged in shape, call sites pass
   the constant.
3. `src/agentic_dev_loop/engine/stages.py`: brief call sites use
   `BRIEF_AGENT_NAME`; prints use `step.name`; `_warn_vendored_brief`
   checks `agents_dir / "brief.agent.md"` by literal filename;
   `build_context` expected-output-paths condition becomes
   `step.kind in ("agent", "brief")`.
4. `src/agentic_dev_loop/engine/graph.py`: add the `kind == "brief"`
   branch calling `make_brief_node`.
5. `src/agentic_dev_loop/engine/dryrun.py`: `_step_summary` gains a
   brief branch (sections count, produces, max_iterations);
   `_validate_agents` needs no logic change (a brief step has
   `agent is None`) but its comment updates.
6. `agentic-dev-loop/pipeline.yaml`: brief step rewritten to the new
   shape. Deliverable is a tooling path and will need a human commit.
7. `README.md`: the `brief_inputs` documentation and pipeline example
   updated to the step-type shape. Deliverable is a tooling path and
   will need a human commit.
8. Tests: `test_config.py` gains kind/exclusivity/defaults/rejection
   tests; `test_033_brief_step.py` (fixtures at 524, 549, 574),
   `test_041_brief_coverage.py` (fixture at 82), `test_e2e_fixtures.py`
   (fixture at 187) drop `agent: brief`/`produces` and adopt the new
   shape; a sweep removes any remaining `agent: brief` strings from
   tests. New tests: defaults applied at parse, rejection messages,
   two brief steps with different names, vendored-file warning still
   fires on `brief.agent.md`.
9. `tools/publish.py`: add `"-native-brief-step-type": "plan 049"` to the
   `CONSUMER_ADOPTION_PLANS` registry (this plan adds consumer pipeline
   obligations, so the release gate must enforce the adoption note).
10. `CHANGELOG.md`: Breaking entry (loop commit). The entry MUST carry a
    `### Consumer adoption` subsection naming exactly what the consumer
    repo must change: rewrite the brief step to the new shape (drop
    `agent:`, `produces:`, `max_iterations:`; keep `brief_inputs:` with
    its declared `sections:`), and delete the vendored
    `agentic-dev-loop/sdk_agents/brief.agent.md` (046 D5). Without it,
    `publish.py` refuses the release (CONSUMER_ADOPTION_PLANS).

## Non-goals

- No change to dev/qa/review steps: they stay file-loaded agents.
- No change to the coverage gate semantics, retry budget, hash inputs,
  or last-error.md behavior.
- No prompt-prose override for brief; the embedded prompt stays
  engine-owned.
- No change to how `attempt-brief.md` is consumed downstream (dev and
  review still receive the digest through the artifacts channel).
- No duplicate-name validation for steps (pre-existing, unchanged).

## Verification

- Full suite green, including the adapted 033/041/e2e tests and the new
  config tests.
- Engine dogfood: `--dry-run` against this repo's migrated pipeline
  passes and reports the brief step as a brief-type step; a brief run
  renders the embedded prompt with the configured headings.
- `load_config` on the migrated pipeline prints kind 'brief' with
  produces `['attempt-brief.md']` and max_iterations 12.
- The old shape fails validation with a message naming the fix.
- Consumer follow-up (separate commit, after this lands): delete the
  vendored brief prompt file, adopt the new shape, declare its sections;
  a fresh plan attempt then briefs with the consumer's own vocabulary.
