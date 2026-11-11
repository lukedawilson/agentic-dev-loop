# Authoring a loop for your own repo

A consumer repo needs a pipeline file and the agent files it names, both
committed before the first run. `init` writes them; the layout is:

```
<repo>/agentic-dev-loop/pipeline.yaml
<repo>/agentic-dev-loop/sdk_agents/dev.agent.md
<repo>/agentic-dev-loop/sdk_agents/review.agent.md
<repo>/agentic-dev-loop/sdk_agents/qa.agent.md
```

## Start with `agentic-dev-loop init`

```bash
agentic-dev-loop init --profile python        # or: --profile dotnet-node
```

`init` writes the onboarding surface from a bundled toolchain profile:
`agentic-dev-loop/pipeline.yaml`, the three agent files,
`agentic-dev-loop/.engine-version` (the exact installed engine version), any
missing premise files, gitignore additions, and an env-file template. It
detects solution files, `package.json` directories, lockfiles, README, and
AGENTS.md, asks only what detection cannot answer, and refuses to overwrite
an existing `agentic-dev-loop/` directory. A consumer-authored profile file
can be passed by path instead of a bundled name.

`agentic-dev-loop doctor --fast` probes the host and repo before you run:
hard failures stop you here (engine pin mismatch, engine runtime
availability, API version, git repo, `.env*` files); warnings print and
continue. `agentic-dev-loop upgrade` installs the latest engine release and
rewrites the pin, so the host install and the committed pin converge in one
explicit action. Run both yourself; the CLI never runs them for you.

## The agent files

Each `sdk_agents/<name>.agent.md` is a markdown prompt plus YAML frontmatter:
`name` matches a step's `agent:` value; `tools` lists the tool names the step
may use (`terminal` always; verdict steps also need `record_verdict`; a repo
can add more via extensions). A step configured with `verdicts:` whose agent
file does not grant `record_verdict` fails at load, never at routing time.

```markdown
---
name: review                 # matches a step's `agent:` value
tools: [terminal, record_verdict]
---
# Review Agent
## WORKFLOW                   # numbered steps
## RULES                      # absolute never/always rules
## SANDBOX NOTES              # the sandbox truth for THIS step
## OUTPUT                     # the verdict contract
```

Three roles, three contracts:

| Role | Verdict contract | Typical sandbox |
| --- | --- | --- |
| dev | none - the final response is captured as the `produces:` summary | rw mount, offline |
| review | `record_verdict` with `APPROVED` / `NEEDS CHANGES`; the prose keeps the findings for humans | ro mount, offline |
| qa | `record_verdict` with `PASS` / `FAIL`; map `unverifiable: UNVERIFIABLE` if you want a third way | shaped by the repo: rw + network + `class: browser` when it boots servers and drives a browser; ro + offline when it probes the CLI only |

Conventions that make a good agent file:

- **State the sandbox truth in the prompt.** Every agent file carries a
  `SANDBOX NOTES` section naming exactly what its step allows: mounts ro or
  rw, network on or off, and which gates have already run.
- **Gates are the source of truth - cite the record, never re-run.** Review
  and qa never re-run builds or suites; qa maps every acceptance criterion to
  existing coverage and probes only the remainder.
- **dev enumerates its impossible set and stops at it.** List the commands
  the sandbox cannot run (offline package fetches, browser downloads), one
  attempt per command - keeps the productive loop and kills doomed retries.
- **Failure output is a required fix in dev's next context.** Agent verdict
  steps land their failing output under *Previous attempt feedback*; command
  gates (test, restore) land it under *Mechanical gate output*, with the exit
  code under *Verified state*. Steps route `on_fail: { goto: dev, retry: true }`,
  and the dev prompt makes every listed failure a required fix.
- **No git mutations, ever.** The `commit` step is the single commit point.

## The pipeline file

The full field reference is in the
[pipeline.yaml reference](pipeline.md); this is the stage lineup and what to
adapt per repo.

| Stage | Kind | Typical shape | Why |
| --- | --- | --- | --- |
| dev | agent | rw, offline | does the work; edits are untracked |
| restore | command | network + `presets: [package-registries]` | fetches what dev pinned (`deps_policy: open`) or the locked install |
| test | command | offline is fine (warm cache) | exit code is the verdict; the source of truth |
| editor-tests | command | network + `presets: [package-registries]` | frontend suites that cannot run offline; a separate step keeps its failure diagnosis and timeout independent of the backend suite |
| conformance | command | network + `presets: [package-registries]` | repo-specific fitness checks |
| review | agent | ro, offline | static conformance verdict |
| qa | agent | per repo (see table above) | live-behaviour probes; inject secrets only if the probes need them |
| commit | action | host-side | the single commit point |

Adaptation checklist for a new repo:

1. `llm:` - model, `base_url`, `api_key_env`.
2. `llms:` (optional) - named profiles for multi-provider lanes (keep review
   on the strongest model); a step opts in with `llm: <name>`.
3. `profile:` - name a bundled profile (`python`, `dotnet-node`) or a
   consumer profile path.
4. `caches:` - profile-supplied toolchain caches plus any run-specific dirs;
   caches are never synced back, and a step's `setup:` hook runs with them
   mounted so it can warm them.
5. `sync_excludes:` - build output trees (`**/bin/**`, `**/obj/**`).
6. `secrets:` - refs only, resolved from host env; each step opts in to the
   refs it needs.
7. `egress:` - `presets: [package-registries]` for fetch steps;
   `class: browser` (broad-but-logged) for browser-tier qa only.
8. `deps_policy:` and `lockfiles:` - see
   [dependency policy](pipeline.md#dependency-policy-locked-by-default).
9. The dev prompt's impossible set - write it for your toolchain.
10. `additional_files:` - the repo's `AGENTS.md`, coding standards, design
    docs: shared context for every agent step.

Validate before the first run - dry-run loads and prints the resolved plan
with no LLM, no Docker, no state writes:

```bash
agentic-dev-loop agentic-dev-loop/pipeline.yaml --dry-run
```

Then commit the tooling and run for real.
