# 025-browser-ready-sandbox-config.md

> **Status: Ready**

## Scope

This repository only (the engine). Consumer repos are not touched; they
consume the preset by setting one config value and reading the docs.

## Background

Getting a browser running inside an engine sandbox today requires
hand-assembled knowledge: the bundled seccomp profile path, the implicit
`--cap-add SYS_CHROOT` coupling, fonts in the image, network on the right
step, and the consumer-side pieces (the browser binary and its syslibs in
the workspace). All of it was proven the hard way in plan 023's host-side
gates. Follow-up 4 of plan 023: make this a first-class, documented config
surface so consumer loops declare intent instead of rediscovering the
incantation.

## Current state (facts)

- `IsolationConfig.seccomp_profile: str | None` (core/config.py) accepts a
  host path to a Docker seccomp profile JSON; Docker validates it at
  container-create time, not at load.
- The engine bundles exactly one profile:
  `src/agentic_dev_loop/engine/seccomp/chromium-userns.json`, exposed by the
  helper `chromium_userns_profile_path()` (engine/seccomp/__init__.py),
  which currently has no callers.
- `docker_create_payload` emits `--cap-add SYS_CHROOT` whenever a seccomp
  profile is set (the plan 023 cap-add fix). The profile gates chroot(2) on
  exactly that capability, so the coupling is intentional and required for
  Chromium's zygote.
- Network is per-step (`step.network`), unchanged by this plan.
- The dev-dotnet image ships fontconfig + fonts (plan 023 fix 3).

## Change

### 1. Bundled-profile sentinel on the existing field

`isolation.seccomp_profile` additionally accepts `bundled:<name>`. At
config load the name is resolved against the engine's bundled profiles via
`chromium_userns_profile_path()`-style resolution and expanded to the real
path; the payload code keeps receiving a plain path (no payload change
needed beyond what exists).

- Known name today: `bundled:chromium-userns`.
- Unknown names fail at load with an error listing the known bundled names
  (loud failure per repo policy, and unlike raw paths this can be validated
  at load because the package contents are known).
- Raw paths keep working unchanged (backward compatible; validation stays
  Docker's at create time).
- Chosen over a new boolean preset because it reuses one field, extends to
  more bundled profiles later (e.g. a strict default profile), and avoids
  preset-vs-profile conflict validation.

### 2. Documentation of the full recipe

New doc `docs/browser-sandbox.md` (linked from the README), covering:

- What the engine now gives: `seccomp_profile: bundled:chromium-userns`
  (profile + implicit SYS_CHROOT cap), fonts in the dev-dotnet image.
- What the consumer supplies: the browser binary and its syslibs inside the
  workspace (e.g. via playwright-core install, as consumer repos already
  do), `network: true` on the browser step, the syslibs on
  `LD_LIBRARY_PATH` for bare-binary runs, and `chromiumSandbox: true` in
  their playwright launch options.
- Why `--no-sandbox` must not be used, and what the sandbox buys
  (renderers run seccomp-filtered).
- Verification commands for a consumer loop to prove the sandbox engaged:
  `unshare(CLONE_NEWUSER)` returns 0, and renderer processes show
  `Seccomp: 2` + `NoNewPrivs: 1` in `/proc/<pid>/status`.
- The engine does not vendor Chromium (size); consumers bring the binary.

### 3. Docstring updates

- `IsolationConfig.seccomp_profile` docstring: the sentinel form, the
  load-time validation, and the intentional SYS_CHROOT coupling.
- `docker_create_payload` docstring: note that the cap-add applies to any
  seccomp profile by design (the bundled profile requires it; custom
  profiles are expected to follow the same cap-gated pattern).

## Acceptance checks (sandbox-executable)

1. A pipeline with `seccomp_profile: bundled:chromium-userns` loads and the
   resolved value is the real bundled profile path (unit test).
2. `bundled:unknown-name` fails to load with an error naming the known
   bundled profiles (unit test).
3. Payload: the sentinel produces `--security-opt seccomp=<resolved path>`
   and `--cap-add SYS_CHROOT`; a raw path behaves exactly as today
   (existing payload tests extended).
4. `docs/browser-sandbox.md` exists and contains the consumer recipe
   (manual review).
5. Full suite green; no non-ASCII characters in touched files (repo-wide
   ASCII policy).

## Verification

TDD per repo rule: red tests for checks 1-3 first. All checks run inside
the sandbox (no docker needed). A live docker smoke test of the sentinel
(the 023 gate-2 style container) is optional and host-side; it is not
required for completion because the payload shape is already proven by
plan 023's gates and the sentinel resolves to the same path.

## Follow-ups

None.

## Open questions

None blocking. Future bundled profiles (e.g. a hardened default) would slot
into the same sentinel registry.
