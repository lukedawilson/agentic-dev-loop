# Plan 045: Setup-probe self-heal for non-venv cache warmers

> **Status: PROPOSED** (flips to COMPLETED after implementation lands)

## Scope

Extend the 016 warm-stamp self-heal so a `setup:` hook can declare its own
in-container probe command. Today the engine only knows how to probe one
artifact: the sandbox venv. Every other provision artifact (npm caches,
NuGet package caches, any toolchain cache a consumer warms) is trusted on
stamp alone, with no platform self-heal. This plan adds one generic config
field, `setup_probe`, and runs it in place of the venv probe when present.
The engine gains no toolchain vocabulary: consumers author the probe, the
engine executes it and treats a failing probe as a mandate to re-provision.

## Context

The Oct 31 2026 killed-run spiral (run 5367f0c488d8) had two provisioning
holes, both now diagnosed:

1. **Poisoned npm cache, no self-heal.** The dev sandbox (dev-dotnet,
   linux/arm64) received a `frontend-editor/node_modules` cache whose
   esbuild/rollup native binaries were darwin-arm64, built by local macOS
   npm runs. The warm stamp was trusted blindly: the 016 self-heal probes
   only the venv, and for a non-venv setup hook the probe reports
   `missing`, which is deliberately treated as a stamp hit. The cache was
   only repaired because the QA step (network on) re-ran `npm ci`. The
   consumer-side warm is now also correct at provision time
   (yosk-cms `b3a9c1d`: dev setup extended to `npm ci` + `dotnet restore`,
   provision containers have network on).
2. **NuGet premise was never true.** Fixed consumer-side by
   `b3a9c1d` (repo-root `NuGet.Config` pointing `globalPackagesFolder` at
   the declared `.nuget-packages` cache, plus provision-time
   `dotnet restore`). No engine change was needed and none was made.

`b3a9c1d` makes the cache correct on the next provision. What remains is
the engine-side half: if a cache is ever poisoned again (any host-side
toolchain mismatch, any partial warm), the stamp is trusted and the dev
agent spirals until a network-on step masks the failure. The venv already
has this guard; this plan gives every other warmer the same guard through
a consumer-authored probe.

Current plumbing (verified in `src/agentic_dev_loop/engine/isolation.py`):

- `setup_stamp_key(setup_cmd, lockfile_hashes)` (line 507): sha256 of
  canonical JSON `{setup_cmd, lockfiles sorted}`. Probe text is NOT part
  of the key.
- `venv_probe_command()` (line 517): prints exactly one of
  `valid` / `missing` / `broken`, exits 0.
- `venv_probe_argv(container, workspace)` (line 537): `docker exec -w
  <workspace> <container> /bin/sh -c <probe>`.
- `venv_probe_state(stdout, exit_code)` (line 544): nonzero exit is
  `unknown`; caller leaves the stamp alone rather than guessing.
- `provision(setup_cmd, stamp_key, stamp_dir, force)` (line 1135): on a
  warm stamp it runs `_probe_venv()` inside the exec container; `broken`
  removes the venv and falls through to re-provision; `valid` / `missing`
  / `unknown` keep the skip. `_probe_venv()` uses
  `self._export_root or self._workspace` as the probe workdir (line 1171).
- `StepConfig.setup: str | None = None` (core/config.py line 364),
  validator restricts `setup` to command/agent steps (line 493).

## Decisions

| # | Decision | Proposal | Answer |
|---|---|---|---|
| D1 | Config surface: add `setup_probe: str | None = None` to `StepConfig` (core/config.py), plus a validator: `setup_probe` requires `setup` (mirror the existing 493-495 validator). `StepConfig` already sets `extra="forbid"`, so the key is schema-enforced in both repos. Backward compatible: absent key keeps current behavior | Add field + validator + negative test | PENDING |
| D2 | Semantics: when `setup_probe` is set for a declared setup, `provision()` runs the consumer probe instead of the venv probe on a warm stamp. Exit 0 = stamp trusted (skip). Any nonzero exit (probe says broken, or `docker exec` itself fails) = fall through to re-provision, which runs the setup hook with network on. Fail toward freshness: a needless re-warm costs time, a skipped warm serves a poisoned cache. This deliberately differs from the venv probe, whose `unknown` keeps the stamp: the venv probe can crash for docker reasons unrelated to the venv, but for a consumer probe a failure to run is indistinguishable from a broken artifact and the safe direction is re-provision | Exit-code-only, two outcomes | PENDING |
| D3 | Plumbing: generalize the probe executor instead of special-casing. Add `probe_argv(probe_cmd, container, workspace)` (or widen `venv_probe_argv`) that shlex-quotes an arbitrary consumer command through `/bin/sh -c`, with the 30s timeout and export-aware workdir already used by `_probe_venv()`. With no `setup_probe`, the venv probe path stays byte-for-byte identical (016 regression-safe) | Widen the executor, keep venv default | PENDING |
| D4 | Stamp key: do NOT include probe text in `setup_stamp_key`. A probe change almost always accompanies a setup change (new stamp anyway); a probe-only change can force re-provision with `--refresh` or `--clean-stamps`. Document this in the field help/docstring | Keep key unchanged, document | PENDING |
| D5 | Tests (TDD, red first): (a) config: `setup_probe` without `setup` raises the loud ValidationError; `setup_probe` with `setup` parses; (b) unit (test_isolation.py, near the 016 tests at line 909+): warm stamp + probe exit 0 skips provision; warm stamp + probe nonzero re-provisions (setup ran, stamp rewritten); warm stamp + docker-exec failure re-provisions; (c) regression: warm stamp + no `setup_probe` still runs the venv probe with its valid/missing/broken classification (existing tests must stay green untouched); (d) integration (test_isolation_integration.py, mirror the 016 repro at line 193): a consumer-shaped probe that fails inside the container triggers remove-free re-provision (no removal step; the setup hook itself re-warms, so nothing is deleted generically) | Red first, then green | PENDING |
| D6 | Docs: README step-config table row for `setup_probe` with an example consumer probe (the yosk-cms ELF check); docs/setup.md note that a declared probe replaces the venv probe for that setup and that probe-only changes need `--refresh`. No COMPAT-SHIMS entry (no SDK seam involved) | Tooling-path edit, human commit | PENDING |

## Changes (file by file)

1. `src/agentic_dev_loop/core/config.py`: `setup_probe: str | None = None`
   field on `StepConfig`; validator in the existing setup-constraint
   method; field help text covering the stamp-key caveat (D4).
2. `src/agentic_dev_loop/engine/isolation.py`:
   - `provision(...)`: accept the probe (or read it from the caller's
     config), and when set run the generic probe path instead of
     `_probe_venv()` (D2).
   - `probe_argv(probe_cmd, container, workspace)` or widened
     `venv_probe_argv` (D3).
   - `_declared_setups` / the startup provisioning loop: thread the
     per-step `setup_probe` through so each declared setup carries its
     own probe.
   - `venv_probe_*` functions stay as the default path, untouched.
3. `tests/test_config.py`: validator tests (D5a).
4. `tests/test_isolation.py`: provision probe tests (D5b, D5c).
5. `tests/test_isolation_integration.py`: container-shaped repro (D5d).
6. `README.md` + `docs/setup.md`: D6.

## Non-goals

- No platform or toolchain detection in the engine (no ELF/Mach-O, no
  npm/NuGet knowledge). Probes are consumer-authored shell commands.
- No per-cache probes (a `caches:` entry is a mount, not a warm; the
  warmer is the setup hook, so the probe hangs off the hook).
- No change to the venv three-state classification or its
  remove-and-re-provision behavior.
- No rework of `setup_stamp_key`; probe text stays out of the key.

## Verification

- Full suite green (`test_isolation.py`, `test_isolation_integration.py`,
  `test_config.py`, plus the untouched 016 tests as the regression net).
- Consumer follow-up (config-only, after this lands): yosk-cms dev step
  adds a `setup_probe` asserting the warmed artifacts are linux-shaped
  (for example the esbuild binary is ELF). The Oct 31 failure class then
  self-heals at stamp-check time instead of inside the dev agent's turn
  budget.

## User Decision Required

Approve or adjust each of D1-D6. Note D2's deliberate asymmetry with the
venv probe (`unknown` keeps the stamp for venv, nonzero re-provisions for
consumer probes) - say so if you want the venv-style three-state
classification instead.
