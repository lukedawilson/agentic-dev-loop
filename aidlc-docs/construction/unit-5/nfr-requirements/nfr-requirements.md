# Unit 5 NFR Requirements - workspace egress isolation

## Positioning

Unit-5 closes the last structural channel in the containment model: every container currently mounts `{workspace}` rw and `network: true` means unrestricted egress. After this unit, a step physically cannot read what it does not mount, cannot send to an address it has not declared, and never sees repository history (`.git`). The five load-bearing pillars (least-access mounts, egress allowlists, read-only where possible, `.git` blindness, loud failure modes) are judged against the same test as units 1-3: the agent is untrusted, the pipeline author is trusted, and every mechanism is network-level or kernel-level, never a prompt rule. Multi-toolchain support (pypi, npm, .NET, Rust) is preset data (D-2), so the mechanism stays toolchain-agnostic.

## NFR requirements

| ID | Category | Requirement |
| --- | --- | --- |
| NFR-5.1 | Performance | **Practical targets, no CI gates.** Workspace export materialisation + egress policy assembly (proxy config generation) within a few seconds for typical repos; sync-back proportional to changed files (rsync-style); profile-keyed pool warm-up amortised across steps. Measured during development, not enforced (unit-3/4 precedent). |
| NFR-5.2 | Portability | **Uniform proxy enforcement everywhere** (amended 2026-10-25). The engine-managed per-policy egress proxy is the enforcement point on all platforms; no root, no netfilter programming. Kernel-level enforcement is deferred to the future native Linux backend (unit-3 FD-2 scope). |
| NFR-5.3 | Backward compatibility | **Hard break now** (pre-1.0 alpha). Strict-by-default mounts + export mounting land with a version bump; CHANGELOG carries an old-to-new config mapping (default `{workspace}` rw + unrestricted `network: true` -> declared `mounts`/`egress`); this repo's pipeline.yaml + the reference consumer repo updated at implementation. Dev/qa steps fail loudly until declared - no silent compat shims. |
| NFR-5.4 | Reliability | **Mirror unit-3 BR-10.** Idempotent infra ops (proxy startup, export materialisation, cache existence checks) retried with bounded backoff; execution and sync-back never retried (side effects); all failures loud. |
| NFR-5.5 | Auditability | **state_dir (host-only) structured logs.** Per-network connection log (browser class), enforcement events (policy applied, proxy deny events), sync-back reports; bounded rotation; no secrets/payloads in logs. |
| NFR-5.6 | Security | **The unit is a security unit: nothing weakens.** Strict-by-default + declared-writes + `.git` blindness reduce the attack surface; enforcement is at the engine-run proxy (BR-5/6), not prompt-level; `--cap-drop ALL` retained, no NET_ADMIN in-container. Residual risks documented with mitigations: R-1 cache rw window (narrowed from whole-workspace rw to declared caches), R-2 proxy enforcement is cooperation-dependent (HTTP-only, env-respecting clients; "best we can do with Docker" until the native Linux backend lands - unit-3 FD-2 scope), R-3 browser broad egress (connection log + browser sandbox). |
| NFR-5.7 | Testability | **Tiered, no privileged CI.** Pure-function + fake-backend tests everywhere; real-Docker tests marked/opt-in; enforcement-behaviour tests (the proxy actually denying unmatched traffic) are portable (no root) and run in normal CI, never silently skipped. |
| NFR-5.8 | Usability | **Loud, actionable failures.** A missing mount declaration or an allowlist miss fails the run naming the step and the missing declaration; engine presets (Q9) keep declarations short; migration notes cover the two known consumers. |

## Category dispositions

| NFR category | Disposition |
| --- | --- |
| Scalability | N/A - single dev, single pipeline per run |
| Performance | Applicable - NFR-5.1 (practical, ungated) |
| Availability | N/A - local, on-demand |
| Security | Applicable - the unit's core purpose - NFR-5.6 |
| Reliability | Scoped - NFR-5.4 (infra-op retries only; no execution retries) |
| Maintainability | Covered - pure-function algorithm split (mount/egress/export/sync-back resolution) + presets-as-data (D-2); transport isolated behind the Sandbox surface |
| Usability | Scoped - NFR-5.8 (loud failures + presets soften the strict-by-default ergonomics) |
| Portability | Applicable - NFR-5.2 (uniform proxy enforcement; kernel-level deferred to the native Linux backend) |

## Security-baseline blocking set dispositions

Consistent with the FD business-rules mapping: Enforced 02, 03, 05, 06, 07, 09, 11, 15 (network intermediary logging via the egress proxy at the enforcement point; application logging now includes the state_dir enforcement/connection/sync-back logs per NFR-5.5; input validation of mount paths + egress targets; least privilege via least-access mounts + `.git` blindness + declared caches only; restrictive network config via proxy allowlists + explicit logged browser class; hardening via retained `--cap-drop ALL` and no NET_ADMIN; secure design via defense in depth; fail-safe defaults via loud failures + cleanup). N/A 01, 04, 08, 10, 12, 13, 14 (no new data stores, no web endpoints, no new supply-chain/credential/integrity surfaces - those stay in unit-3/plan-019 scope; desktop library with no alerting surface).

No blocking security findings.
