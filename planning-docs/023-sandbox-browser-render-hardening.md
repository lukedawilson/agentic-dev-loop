# Plan 023: Sandbox browser render hardening (engine scope)

> **Status: COMPLETED** (2026-10-24)
- Scope: agent-engine (this repo) only.
- Source: QA report verification session. All environment claims reproduced by
  hand in live sandbox container fd423aa738a2 (dev-dotnet:10.0, dbc882ac8fdf).
- Scope correction: an earlier revision of this plan listed consumer-side fixes
  (probe finder pattern, probe sandbox flag) as cross-repo work items. Those
  were removed. The engine has no browser-probe code of its own, and engine
  plans cover the engine; consumer repos own their probe fixes in their own
  planning.

## Background (condensed evidence)

Browser rendering inside engine sandboxes was broken in two stacked ways,
neither caused by consumer code:

1. **Skia crash on pages with unstyled text controls**: Chromium 156 aborts
   with `FATAL:third_party/skia/src/ports/SkFontMgr_FontConfigInterface.cpp:163`
   `Not implemented.` That line is `SK_ABORT` inside `SkFontMgr::countFamilies()`
   (skia chrome/m156, the exact revision Chromium 156 pins). Trigger bisected
   to a single fact: an unstyled text-entry form control (`<input type="text">`,
   `<textarea>`, `<select>` with options). Its intrinsic width is computed from
   control-font metrics; with no font resolvable, that path asks the font
   system to enumerate families and lands on the stub. An explicit CSS `width`
   bypasses the sizing path and fixes the full page with zero fonts present.
2. **No font ever resolved in the image**: no fontconfig config, no fonts, and
   fontconfig is statically compiled into the chrome-headless-shell binary
   (`default config` string is in the binary; no fontconfig .so is ever mapped
   per /proc maps). All text rendered zero-width (invisible).
3. **The Chromium sandbox could not start**: the engine payload passes
   `--cap-drop ALL` and no seccomp profile, so `unshare(CLONE_NEWUSER)` returns
   EPERM (Docker's default seccomp) and the zygote dies without `--no-sandbox`.
   Playwright launches browsers with `--no-sandbox` by default, so untrusted
   page JS has been executing unsandboxed since day one.

## Decisions (Luke, 2026-10-24)

1. Fix the image (fonts + fontconfig): agreed, done inline as Fix 3.
2. Enable the Chromium sandbox (profile + payload support): agreed, done as
   Fix 2a plus the cap-add correction below. The read-only-workspace layer
   (Phase B) is deferred as a follow-up, not part of this plan.
3. Engine-only scope: agreed. Consumer-side fixes removed from this plan.

## Fix 3: fonts and fontconfig in the dev-dotnet image - DONE

Exact change to `Dockerfile.dotnet`:

```diff
 RUN apt-get update && apt-get install -y --no-install-recommends \
         git nodejs npm coreutils ca-certificates curl \
         libicu76 libstdc++6 jq unzip lsof \
+        fontconfig fontconfig-config fonts-liberation \
+    && fc-cache -f \
     && rm -rf /var/lib/apt/lists/*
```

Rationale: `fontconfig-config` provides `/etc/fonts/fonts.conf` (which the
in-binary fontconfig reads by default); `fonts-liberation` provides Liberation
Sans, the Arial-metric-compatible match for the control font
`-webkit-small-control` and for general fallback. This fixes both the
unstyled-control crash and the zero-width invisible text.

Applied and verified 2026-10-24: image rebuilt locally; `fc-list` lists 12
Liberation fonts; decisive repro on the new image: bare `<input type="text">`
renders (previously a deterministic crash) and span text width measures 32 px
(previously 0 px). Fix 3 is PROVEN. Committed to main as `7d8d802`
(`fix(dev-dotnet): add fontconfig and fonts`); the `publish-image.yml` workflow
(`GITHUB_TOKEN`, `packages: write`, multi-arch amd64+arm64) is the publisher
for `dev-dotnet:10.0`.

## Fix 2a: enable the Chromium namespace sandbox in engine containers - DONE

Implemented by the loop in commit `5e25464`:

- `IsolationConfig.seccomp_profile` config field (host path to a Docker seccomp
  profile JSON).
- `docker_create_payload` emits `--security-opt seccomp=<path>` when set.
- Bundled profile `src/agentic_dev_loop/engine/seccomp/chromium-userns.json`
  (190 lines): deny-by-default, userns syscalls (`clone`, `clone3`, `unshare`)
  allowed, privileged syscalls gated on their capabilities (chroot on
  `CAP_SYS_CHROOT`, mount/setns/bpf on `CAP_SYS_ADMIN|CAP_BPF|CAP_PERFMON`,
  etc.), plus the `chromium_userns_profile_path()` helper.
- Package-data wiring so the JSON ships in the wheel; unit tests for payload
  assembly and profile properties. 774 passed, 36 docker-marked skipped.

Review defect, found by the host-side gate and fixed (TDD): the payload must
also emit `--cap-add SYS_CHROOT` alongside the profile. The zygote chroots
`/proc/self/fdinfo` at startup; the profile gates `chroot(2)` on exactly
`CAP_SYS_CHROOT`, and `--cap-drop ALL` leaves an empty bounding set, so the
zygote dies even with the profile. The payload now emits:

```
--read-only --cap-drop ALL --security-opt seccomp=<profile> --cap-add SYS_CHROOT ...
```

Red first (3 failing asserts on `--cap-add SYS_CHROOT` in
tests/test_isolation.py), then green (`docker_create_payload` + docstring).
Result: 227 passed (tests/test_isolation.py + tests/test_config.py).

Note: this cap-add edit is in the working tree; the commit is the one
outstanding action (see Follow-ups).

## Host-side gates (executed 2026-10-24, with results)

The plan's acceptance gates needed docker and host access, so they were
excluded from the loop and executed here. Results:

| Gate | Condition | Result |
|------|-----------|--------|
| 1. userns | `unshare(CLONE_NEWUSER)` in a container with the profile returns 0 | PASS (EPERM before profile, 0 with profile; seccomp confirmed as the blocker) |
| 2. renderer sandbox | chrome renderers show `Seccomp: 2`, `NoNewPrivs: 1` | FAIL with profile only (zygote dies on `sys_chroot("/proc/self/fdinfo/")`), PASS with profile + `--cap-add SYS_CHROOT` (6 live procs, 5 renderers `Seccomp: 2` + `NoNewPrivs: 1`) |
| 3. functional | playwright `chromiumSandbox: true` + bare input render | PASS (`{"sandboxOn":true,"inputWidth":185,"shotBytes":4382}`) |

Live verification of the exact engine-built payload (docker run with the full
flag set, workspace /tmp/liveseck-ws, real profile path): `unshare` returns 0;
chrome launched without `--no-sandbox` yields 7 procs all `Seccomp: 2`, 6
renderers `NoNewPrivs: 1`. Containers and temp workspace cleaned up.

## Verification summary

| # | Check | Status |
|---|-------|--------|
| 1 | Crash repro on the OLD image (regression control) | PASS (many times) |
| 2 | Bare input does NOT crash on the NEW image | PASS (local build) |
| 3 | Text visible: span width > 0 px | PASS (32 px) |
| 4 | Userns gate | PASS (executed, above) |
| 5 | Renderer sandbox engages under `chromiumSandbox: true` | PASS (executed, above) |

## Why the plan originally shipped incomplete (review findings)

- The loop's QA passed with generic CLI probes, and the plan flipped to
  COMPLETED while the excluded gates were unexecuted and unowned. QA's report
  noted the gates were unreachable in the sandbox and passed anyway.
- The missing `--cap-add SYS_CHROOT` traces to this plan's 2a spec, which
  asserted the profile alone sufficed; the loop implemented the plan faithfully.
  The gate that would have caught it needed docker, which sandboxes do not have.
- Fixes for the loop process are candidates for a future plan (Follow-ups).

## Follow-ups (not in this plan)

1. **Outstanding action**: commit the cap-add fix (code + tests currently in
   the working tree, live-verified).
2. **Loop process**: QA verdicts gain UNVERIFIABLE for checks the sandbox
   cannot execute (no docker, no host access). UNVERIFIABLE items are recorded
   in the report and do not block completion; the run moves on with the record
   showing exactly what was and was not verified. No gate, owner, or handoff
   concept is added to the domain.
3. **Loop process**: QA must not substitute unrelated generic probes for
   unreachable acceptance checks; that fabricates verification. Report the
   check as UNVERIFIABLE instead.
4. **Engine**: a first-class "browser-ready sandbox" config surface and docs
   (network access, seccomp profile, `--cap-add SYS_CHROOT`) so consumer loops
   do not rediscover the incantation.
5. **Engine**: Phase B read-only-workspace payload support (defense in depth,
   deferred from this plan).

## Open questions

None blocking. The consumer-side items (probe finder pattern, probe sandbox
flag, served-asset 404) are deliberately out of scope for this repo's planning.
