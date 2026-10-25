# 018 - Dependency-cache deadlock: toolchain-agnostic fix design

> **Status: COMPLETED** (2026-10-23) - shipped: restore step + prompt changes committed and pushed in both repos (agent-engine 551d8cc, the consumer repo bf4b9fd).

**Source:** bug report from the consumer repo - the sitebuilder-construction run deadlocked on the NuGet cache: the network-off dev agent is forbidden (by consumer-authored `dev.agent.md`) from adding uncached packages, while the only cache-priming path (the network-on test step's restore) requires the reference to already exist in the csproj. Ten identical attempts; reviewer verdicts correct; no retry count could converge.

**User constraint:** *"This needs to work with any toolchain (.NET/NuGet, node/npm, python/pypi, etc.) - adding NuGet-specific hacks is not the way."* Fix must be engine-level / mechanism-level, toolchain-agnostic.

---

## 1. The problem, restated generically

Every toolchain has the same shape, and the engine's current contracts create the same deadlock in all of them:

1. `dev` runs network-off; the toolchain cache (`.nuget-packages/`, `node_modules/`, a workspace venv) lives in the mounted workspace.
2. The only network-on moment is a later step whose fetch is driven by the dependency manifest (csproj / package.json / requirements.txt). Nothing fetches what isn't declared.
3. The consumer-authored dev prompt forbids declaring uncached dependencies, and its "state the missing package" complaint channel has no consumer (summary lives in host state_dir, deleted at every retry).

Verified engine facts that shape the fix:

- **`setup:` hooks provision ONCE per run** (`startup_sandbox` → pre-provision all declared setups, stamp-cached by `setup_cmd` + watched-file digests). They never re-run mid-run.
- **`command` steps run on every pass.**
- **Retries do not restart the pipeline:** a failed step routes to `bump`, which jumps back to the retry-target node. In the consumer, test's `on_fail: retry → dev` means each retry traverses `dev → (everything between dev and test) → test` again. Steps before the retry target run once per run; steps between the retry target and the failing step re-run every pass.
- **Agent prompts are consumer-authored** (`.agent.md` → SDK system prompt suffix). The engine injects nothing about sandbox or dependency policy - which is how the deadlocking rule got written.
- The setup-stamp watch list (`_lockfile_hashes`) is a fixed glob set: `package-lock.json`, `requirements.txt`, `*.csproj`. Gaps: `package.json`, `pyproject.toml`, `poetry.lock`, yarn/pnpm locks, `Cargo.*`, `go.*`, etc.

## 2. The fix mechanism (zero engine changes required)

**A network-on restore `command` step placed between dev and test**, plus a prompt-rule change.

```
dev → restore → test → [fail] → bump → dev → restore → test → …
```

- **`command`, not `setup:`** - setups are startup-once, stamp-cached; the restore must run every pass.
- **Between dev and test, not before dev** - before dev it would run once per run (retries jump straight to dev).
- **The prompt rule changes from "never add uncached packages" to a bootstrap protocol:** *"If the design needs a dependency not in the cache, add the exact-pinned entry to the dependency manifest directly (do not run a package-manager `add` command - it may need the network). Your offline builds may fail until the restore step fetches it - that is expected; write the code to match and leave the manifest entry in place."*

Convergence is **within one attempt**: dev (attempt N) adds the pinned reference → restore (same attempt, network-on) fetches it into the workspace cache → test compiles and runs → review. Worst case, dev's own offline build fails once; the pipeline's gate is test, not dev's terminal.

The mechanism is toolchain-agnostic: the pipeline author writes the fetch command (`dotnet restore` / `npm ci` / `pip install -r requirements.txt` / `cargo fetch`). The engine treats it as an ordinary network-on command step. The cache must live in the workspace (as the consumer's `NUGET_PACKAGES` env already does; node/python equivalents: workspace `node_modules`, workspace venv).

## 3. The security dial: what may the restore step fetch?

The mechanism above leaves one choice open - the supply-chain posture, set entirely by *which manifest drives the restore*:

### Dial A - open (agent-driven manifest)

Restore installs from the manifest the agent edited (`dotnet restore`, `npm install`, `pip install -r requirements.txt`).

- **Posture:** the dev agent can cause the network-on container to fetch any package from the public registry. Typosquat and hallucinated-ID reach is real - the spec itself names `ModelContextProtocol.AspNetCore.Authentication`, which does not exist on NuGet.
- **Bounding fact:** the pipeline already executes agent-written code network-on in test/qa/conformance; the *incremental* risk here is third-party package provenance, not code execution per se.
- **Mitigations:** exact-version-only rule; direct manifest edit (no `add` commands); per-toolchain audit escalated to error in the restore step (`NuGetAudit`, `npm audit --audit-level=high`, `pip-audit`).

### Dial B - locked (human-owned lockfile)

Restore installs only from a human-generated lockfile (`packages.lock.json` / `package-lock.json` + `npm ci` / `requirements.lock`), generated on a network machine by a human. The dev rule stays closer to today's: *"You may only use dependencies already present in the lockfile. If the design requires more, state it in your final response and finish what you can."*

- **Posture:** the agent's reach is bounded to exactly what a human pinned. Hallucinated or typosquatted IDs fail at restore with zero public-registry traffic. The lockfile doubles as the spec-derived allowlist.
- **Cost:** a human must extend the lockfile when the spec needs a new dependency - a run stalls until then. Must be paired with a **fail-fast**: when the agent declares a missing dependency, the run should abort with "extend the lockfile with X and re-run" - not burn 10 attempts.

**Recommendation:** dial B as the default posture, dial A opt-in per pipeline for rapid iteration - the choice is a pipeline field, not engine code. For the current the consumer run, either dial works; the spec's ghost package (`ModelContextProtocol.AspNetCore.Authentication`) is itself evidence for B: a human generating the lockfile hits that nonexistent ID immediately, at lock time, instead of an agent chasing it for ten attempts.

## 4. Optional engine hardening (small, generic)

The mechanism needs none of these; they make the contract impossible to hand-write wrong:

1. **Canonical dependency-policy injection.** A `deps_policy` field in `PipelineConfig` (`forbidden` = today's behavior, `bootstrap` = dial A, `locked` = dial B); the engine injects the matching policy paragraph into agent-step context. Consumers stop authoring deadlocking rules in prose. The field is toolchain-agnostic - only the *manifest file names* (a configurable list per pipeline) are toolchain-specific.
2. **Missing-dependency fail-fast.** When policy is `locked`/`forbidden` and the agent's final response declares missing dependencies, write a parseable `missing-dependencies` artifact to state_dir and route to a clear failure - instead of retrying ten identical attempts.
3. **Generalize the setup-stamp watch list.** `_lockfile_hashes` currently watches `package-lock.json`, `requirements.txt`, `*.csproj`; make the globs pipeline-configurable (`setup_watch:`) with a generous toolchain-generic default (`package.json`, `pyproject.toml`, `poetry.lock`, yarn/pnpm locks, `Cargo.toml`/`Cargo.lock`, `go.mod`/`go.sum`, `*.csproj`, `*.props`, `*.targets`). This only affects cross-run warm-start correctness of `setup:` hooks, not the core loop.

## 5. Immediate unblock for the consumer (unchanged from the report)

The one-time prime remains correct and low-risk: both pinned IDs verified present on NuGet at 2.3.0. It is housekeeping to unblock the current run - it is *not* the durable fix, which is this doc's mechanism. Watch out: the spec also names a nonexistent third package (`ModelContextProtocol.AspNetCore.Authentication`, business-logic-model.md line 11) that may bite WS4/WS5 - verify the OAuth support ships inside `ModelContextProtocol.AspNetCore` or find the real package ID, and fix the spec doc either way.

## 6. Fix decision record

| Decision | Choice |
|---|---|
| Mechanism | network-on restore command step between dev and test (runs every pass) |
| Prompt rule | bootstrap protocol (exact pins, direct manifest edit, builds may fail offline) |
| Security dial | `locked` (human lockfile) default; `bootstrap` opt-in |
| Engine changes | optional: `deps_policy` injection, missing-dep fail-fast, configurable setup-watch globs |
| Toolchain coverage | .NET/NuGet, node/npm, python/pypi, cargo, go - all via the same mechanism |
