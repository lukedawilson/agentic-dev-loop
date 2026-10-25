# 021 - Supply-chain dependency dial: locked-by-default lockfile policy

> **Status: COMPLETED**

**Origin:** 018's fix-decision record already made the decision - *"Security dial: locked (human lockfile) default; open (bootstrap) opt-in"* - after shipping the restore-step mechanism. This plan implements that decision.

**Assumptions:** plans 018 (restore step between dev and test in both pipelines), 019 (secrets store + `.env*` ban), and 020 (host executes nothing from the workspace; tooling integrity check) are all implemented. This plan depends on none of their code but assumes the pipelines they leave behind.

---

## 1. Problem

The restore step (018) fetches whatever the dependency manifest declares, network-on, every pass. The only thing standing between the dev agent and the public registry is a prompt rule - advisory, hand-authored, and exactly the class of rule that deadlocked the NuGet cache. Typosquat reach is therefore bounded by *prose*, not mechanism. The locked dial replaces prose with a machine-enforced boundary: restore succeeds only for dependencies a human pinned in a lockfile.

## 2. Design

### 2.1 Config - `deps_policy`

```yaml
deps_policy: locked    # or: open    (default: locked)
```

Pipeline-level, top-level. `locked` = human-owned lockfile is the source of truth; the agent may not add dependencies. `open` = 018's bootstrap protocol (agent may add exact-pinned manifest entries; the restore step fetches them).

### 2.2 Locked semantics

- **Agent-context injection (engine, canonical text - not consumer prose).** When `locked`, the engine injects into every agent step's context:
  *"You may only use dependencies already present in `<lockfile>`. If the design requires a dependency that is not present, declare it in your final response under the fixed header `## Missing dependencies` (one `name==version` per line) and finish what you can. Do not add it to the manifest."*
  This kills the hand-written-rule class that caused the 018 deadlock: the policy lives in the engine, and it is the *correct* rule under locked (the human will extend the lockfile; nothing about the pipeline stalls because the declaration now has a consumer).
- **Missing-dependency fail-fast.** The engine parses the agent's final response for the `## Missing dependencies` header. Non-empty → the run fails fast with: *"Extend `<lockfile>` with these pins and re-run: …"* - one human edit, not ten identical attempts. (The declared list is advisory to the human; the lockfile generation is the human's step, never an engine auto-fetch - see §4.)
- **Locked restore.** The restore step runs the toolchain's locked mode, which fails the step if the manifest and lockfile disagree:
  - .NET: `dotnet restore --locked-mode` (requires `packages.lock.json`, `RestorePackagesWithLockFile=true`)
  - node: `npm ci` (requires `package-lock.json`)
  - python: `uv sync --locked` (or pip-tools `pip-sync requirements.lock`) - pip has no native lockfile; the pipeline's canonical tool for locked installs is `uv`.

### 2.3 Open (bootstrap) semantics

`deps_policy: open` keeps the 018 protocol verbatim (exact-pinned manifest entries, direct manifest edit, offline failures expected until restore fetches) - now injected canonically by the engine instead of living in consumer `dev.agent.md` prose. No missing-dependency parsing needed: the agent just adds the pin. Opt-in is per-pipeline; nothing about `open` is default.

### 2.4 Audit escalation in the restore step

The restore step must fail on known-CVE findings at a documented threshold, per toolchain: NuGetAudit (error), `npm audit --audit-level=high`, pip-audit. Under `locked`, a failing audit means the lockfile pins a vulnerable version → run fails → human regenerates the lockfile at a patched version. Under `open`, the failure routes to dev like any restore failure (dev can bump the pin - with the caveat that the pin the agent chooses is then subject to the same audit on the next pass, so the loop self-corrects).

### 2.5 Prompt migration (both repos)

The bootstrap-protocol paragraphs added to `sdk_agents/dev.agent.md` in 018's implementation become obsolete once injection is canonical. Delete them from the consumer prompts; the engine injects per policy. Consumer prompts keep only toolchain-specific mechanics (e.g. "add `<PackageReference>` XML directly, never `dotnet add package`").

## 3. Migration (acceptance targets)

- **the consumer repo** - human generates `packages.lock.json` for the solution (and lockfiles for any npm workspace); pipeline sets `deps_policy: locked`; restore step becomes `dotnet restore --locked-mode` (and `npm ci` where applicable).
- **agent-engine** - human generates the python lockfile (`uv.lock` via uv, or pip-tools); pipeline sets `deps_policy: locked`; restore step becomes the locked install command.

## 4. Explicitly out of scope

- **Curated feed / registry proxy** (018 option D) - org-level control; a separate piece of work if ever wanted.
- **Engine auto-fetching the agent's declared missing packages** (018 option E) - rejected: it turns LLM strings into registry fetches with no human gate. Under `locked`, declarations are consumed by a human extending the lockfile, never by the engine.
- IP-exfiltration / egress allowlisting - unit-5, not this plan. (Note for unit-5's design: the restore step's allowlist target set is exactly the package registries.)

## 5. Work items

1. **Engine - config:** `deps_policy` field in `PipelineConfig` (default `locked`); validation (unknown values fail fast).
2. **Engine - injection:** canonical policy text per agent step, keyed on policy value and the pipeline's declared lockfile names.
3. **Engine - fail-fast:** parse final agent responses for `## Missing dependencies` under `locked`; non-empty → clear run failure naming the lockfile and the declared pins.
4. **Tests:** unit - policy validation, injection text selection, declaration parsing (header present/absent/malformed), fail-fast routing; integration - a locked pipeline whose dev declares a missing dep fails the run with the extension message, not a retry loop.
5. **Consumer migration:** both pipelines to `locked` + real lockfiles; audit escalation wired into restore steps.
6. **Docs:** README dependency-policy section; lockfile-generation how-to per toolchain; remove the now-canonicalized bootstrap prose from consumer prompts.

## 6. Verification checklist

- [ ] A pipeline with `deps_policy: locked` injects the locked text into dev/review/qa context; `open` injects the bootstrap text.
- [ ] Dev declaring a missing dependency under `locked` → run fails fast with the extension message (no retry loop).
- [ ] Restore in locked mode fails when manifest and lockfile disagree (toolchain-level, proven once per toolchain).
- [ ] `npm ci`-equivalent behavior confirmed: no package outside the lockfile can be installed by any step.
- [ ] Audit escalation fails a run on a known-CVE pin.
- [ ] Both repos' pipelines run green under `locked` from a clean cache.

## 7. Relationship to other plans

- **018** - this plan implements 018's recorded dial decision; the restore step is 018's mechanism.
- **019** - orthogonal; locked installs remove the fetch-anything path a squatted package would ride.
- **020** - orthogonal; the engine-injected policy text is not workspace-mutable (unlike today's consumer prose).
- **unit-5** (egress policy) - builds on this: the restore step's egress allowlist = registries only.
