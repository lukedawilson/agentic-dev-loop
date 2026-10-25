# Unit 5 NFR Requirements Plan - workspace egress isolation

Status: in progress. Next stage after NFR Requirements = Infrastructure Design (NFR Design skipped per project precedent).

## Stage context

Functional Design approved (2026-10-25). Unit-5 = per-step mount granularity (rw / ro / absent) + egress allowlists + .git blindness, closing plan 023 follow-up 5 (read-only workspace) and superseding plan 023 Phase B. FD resolutions in place: strict-by-default mounts with declared-writes (Q3/Q4), worktree-export .git blindness with sync-back (Q5/F1), in-workspace caches bind-mounted into the export (Q6/F2), per-policy egress proxy on all platforms (Q1 amended 2026-10-25), hostname matching at the proxy (Q2 amended), broad-but-logged browser egress (Q8), engine presets + author override (Q9), profile-keyed container pool (D-1), multi-toolchain = preset data (D-2). Business rules BR-1..BR-12. NFR analysis focuses on: performance of the new per-run costs (export, policy assembly, pool, sync-back), portability of proxy enforcement across Docker hosts, backward compatibility of strict-by-default, reliability of the new infrastructure operations, auditability of egress/sync-back logs, testability/CI of real enforcement.

## Open questions

### NFR-1 - Performance budget (export, policy assembly, pool, sync-back)

Context: new per-run costs over unit-3's baseline: step-workspace materialisation, egress policy assembly (proxy config generation), profile-keyed pool warm-up, sync-back after rw steps.

A) **Practical targets, measured not enforced** - export + policy assembly within a few seconds for typical repos; sync-back proportional to changed files; pool warm-up amortised across steps; no CI gates (unit-3/4 precedent).

B) **CI-enforced budgets** - runs fail over explicit thresholds.

X) Other

[Answer]: A - practical targets, measured not enforced: export + policy assembly within a few seconds for typical repos; sync-back proportional to changed files; pool warm-up amortised across steps; no CI gates (unit-3/4 precedent). (Raw: "A")

---

### NFR-2 - Portability of egress enforcement

Context: nftables-in-netns requires programming a Linux netfilter. On macOS/Windows the Docker daemon lives inside a VM (Docker Desktop / OrbStack / Colima); the engine cannot inject rules into that VM's netns from the host.

A) **Platform detection** - nftables-in-netns on Linux hosts (incl. Linux CI); egress proxy on macOS/Windows (same config shape, different enforcement point). (recommended)

B) **Egress proxy everywhere** - uniform mechanism, but a heavier trust surface and TLS interception issues for non-HTTP traffic.

C) **nftables everywhere via a privileged helper container** - needs VM privileges Docker Desktop / OrbStack do not grant.

X) Other

~~[Answer]: A - platform detection: nftables-in-netns on Linux hosts (incl. Linux CI); egress proxy on macOS/Windows; same config shape, different enforcement point. (Raw: "A")~~

[Answer]: SUPERSEDED (2026-10-25, user directive): "Drop the separate linux enforcement altogether for now. We will eventually build a native linux path (without docker), so that hardening will live there. For now, all three OSs get the same treatment". B becomes the answer: the engine-managed per-policy egress proxy is the enforcement point on ALL platforms. Kernel-level enforcement is deferred to the future native Linux backend (unit-3 FD-2 scope).

---

### NFR-3 - Backward compatibility of strict-by-default

Context: strict-by-default mounts + export mounting change the behaviour of every existing pipeline (this repo's dogfood pipeline.yaml, the reference consumer repo); dev/qa steps fail loudly until they declare mounts/egress.

A) **Hard break, version bump, migration notes + in-repo consumer updates at implementation** - unit-4 NFR-2 precedent (hard break accepted pre-1.0); CHANGELOG old-to-new config mapping; this repo's pipeline.yaml + the reference consumer repo updated as part of the unit.

B) **Grace period** - compat defaults + warnings for one release before strict defaults land.

X) Other

[Answer]: A - hard break, version bump, migration notes + in-repo consumer updates (this repo's pipeline.yaml + the reference consumer repo) at implementation; unit-4 NFR-2 precedent, pre-1.0. (Raw: "A")

---

### NFR-4 - Reliability of the new infrastructure operations

Context: new failure-capable ops: proxy startup, export materialisation, cache existence checks. Sync-back and execution are side-effecting.

A) **Mirror unit-3 BR-10** - idempotent infra ops retried with bounded backoff; execution and sync-back never retried; all failures loud.

B) **Fail fast on everything** - no retries anywhere.

X) Other

[Answer]: A - mirror unit-3 BR-10: idempotent infra ops (proxy startup, export materialisation, cache existence checks) retried with bounded backoff; execution and sync-back never retried; all failures loud. (Raw: "A")

---

### NFR-5 - Auditability of egress and sync-back

Context: browser-class connection logging (Q8) and enforcement events (policy applied, proxy deny events, sync-back reports) need a home.

A) **state_dir (host-only)** - structured per-network connection log, enforcement events, sync-back reports; bounded rotation; no secrets/payloads in logs.

B) **stdout only** - nothing persisted.

X) Other

[Answer]: A - state_dir (host-only) structured logs: per-network connection log (browser class), enforcement events (policy applied, proxy deny events), sync-back reports; bounded rotation; no secrets/payloads. (Raw: "A")

---

### NFR-6 - Testability + CI of real enforcement

Context: proxy enforcement (NFR-2 amended) needs no root anywhere; the proxy runs as an ordinary engine process on all platforms. What needs CI proof is that the proxy actually denies unmatched traffic.

A) **Tiered** - pure-function + fake-backend tests everywhere; real-Docker tests marked/opt-in; enforcement-behaviour tests (real nftables blocking, proxy fallback) in a dedicated privileged Linux CI job, never silently skipped; macOS/Windows enforcement = manual smoke + proxy unit tests.

B) **Enforcement tests manual-only** - documented smoke procedure, no CI job.

X) Other

~~[Answer]: A - tiered: pure-function + fake-backend tests everywhere; real-Docker tests marked/opt-in; enforcement-behaviour tests (real nftables blocking, proxy fallback) in a dedicated privileged Linux CI job, never silently skipped; macOS/Windows enforcement = manual smoke + proxy unit tests. (Raw: "A")~~

[Answer]: SUPERSEDED (2026-10-25, follows NFR-2): tiered, no privileged job - pure-function + fake-backend tests everywhere; real-Docker tests marked/opt-in; enforcement-behaviour tests (the proxy actually denying unmatched traffic) are portable (no root) and run in normal CI, never silently skipped.

## Steps

- [x] Analyze FD artifacts for NFR implications
- [x] Resolve open questions (NFR-1..NFR-6, inline; all A, recommended options)
- [x] Generate artifacts: nfr-requirements.md + tech-stack-decisions.md under aidlc-docs/construction/unit-5/nfr-requirements/
- [x] Security-baseline disposition (blocking set) - consistent with FD mapping, no blocking findings
- [x] Completion message + approval
