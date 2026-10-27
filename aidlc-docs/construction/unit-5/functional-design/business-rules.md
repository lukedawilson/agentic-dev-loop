# Business Rules - unit-5 (workspace egress isolation)

> FD-stage (2026-10-25). Rules trace to FD answers Q1-Q9, follow-ups F1/F2, design decisions D-1/D-2, and the multi-toolchain requirement (user reminder 2026-10-25).

## BR-1: Mount sets are strict by default (Q3, Q4)

| Rule | Detail |
| --- | --- |
| BR-1.1 | Empty or missing `mounts:` on a step = empty mount set. The step mounts nothing until paths are declared. |
| BR-1.2 | Every mount entry carries a mode (`rw` | `ro`); source and docs default `ro`; only declared outputs and caches are `rw` (declared-writes model). |
| BR-1.3 | Mount paths are repo-relative; traversal, absolute paths escaping the repo, `.git`, host-only dirs, and `state_dir` are rejected at load (loud). |
| BR-1.4 | Read-only entries become Docker `ro` (sub)mounts; a compromised step cannot tamper with them. |

## BR-2: `.git` blindness by construction (Q5)

| Rule | Detail |
| --- | --- |
| BR-2.1 | The sandbox mounts a fresh engine-materialised export (git worktree-add or archive into a run temp dir), never the real workspace. |
| BR-2.2 | `.git` is absent from the export by construction - no mount-exclusion tricks, nothing to unmask. |
| BR-2.3 | Sync-back is git-aware and is the only path from the export into the real repo; the sandbox never touches the real `.git`. |

## BR-3: Workspace export and caches (F2)

| Rule | Detail |
| --- | --- |
| BR-3.1 | Cache paths are author-declared in `pipeline.yaml` (`caches:`, repo-relative, rw); the engine ships no toolchain cache dirs. The sandbox venv (`.venv-sandbox`) remains engine infrastructure. |
| BR-3.2 (amended 2026-10-25) | A missing declared cache dir is auto-created host-side (empty, uid-owned, `mkdir -p`) and bind-mounted; it is no longer a loud failure. |
| BR-3.3 | The rw window into the real workspace is exactly the declared cache paths - nothing else. |

## BR-4: Sync-back (F1)

| Rule | Detail |
| --- | --- |
| BR-4.1 | After a step that declared rw paths, the engine syncs the export's changes back to the real repo, git-aware. |
| BR-4.2 | Only declared-rw paths sync; ro paths cannot change and are never copied back. |
| BR-4.3 | The sandbox is detached (step complete) before sync-back runs; sync-back is host-side. |
| BR-4.4 (amended 2026-10-27, 029) | A sync conflict (real repo changed underneath) is announced on the console and a loud failure (`AE-SBX-016`), never an overwrite. The baseline is re-snapshotted before the raise and the host's version wins, so the divergence is absorbed once - the next sync-back runs clean instead of re-raising. |
| BR-4.5 (028) | Declared `sync_excludes:` paths are scratch: never copied into the export and never synced (copy, delete, or conflict); excluded from both snapshots; entries validated at load. |
| BR-4.6 (029) | A conflict on a declared `merge_on_conflict:` path is union-merged (three-way, host file plus export-only lines appended in export order): nothing is deleted, host and export converge on the same text, the baseline is re-snapshotted, the run continues, and the console prints a merge line. A both-sides-present merge path only; a deleted side stays a hard conflict. |

## BR-5: Egress enforcement (Q1)

| Rule | Detail |
| --- | --- |
| BR-5.1 | For each distinct egress policy, the engine runs a per-policy egress proxy (local, engine-managed) and forces container HTTP(S) traffic through it (`HTTP(S)_PROXY` env + no direct route). |
| BR-5.2 | Containers keep `--cap-drop ALL`; no in-container NET_ADMIN, ever. |
| BR-5.3 | The proxy is the enforcement point on all platforms (amended 2026-10-25: nftables dropped; kernel-level enforcement deferred to the future native Linux backend - unit-3 FD-2 scope). |
| BR-5.4 | Enforcement is network-level; a prompt rule or a default route is not enforcement. |

## BR-6: Target matching (Q2)

| Rule | Detail |
| --- | --- |
| BR-6.1 | Allowlist entries stay domain-shaped; the proxy matches the request hostname (CONNECT target) per connection - no IP resolution at assembly (Q2 amended). |
| BR-6.2 | An unmatched hostname is denied and logged as an enforcement event. |
| BR-6.3 | A malformed entry that can never match is a loud load-time failure; an allowlist is never silently reduced. |

## BR-7: Browser-step egress class (Q8)

| Rule | Detail |
| --- | --- |
| BR-7.1 | Browser steps (render-check class) use an explicit broad-but-logged egress policy: no target filtering, but every connection is logged (host-side audit, no secrets). |
| BR-7.2 | The class is explicit in config (`egress: { class: browser }` or equivalent); it is never implied by step type alone. |
| BR-7.3 | Command and agent steps never qualify for the broad class; they keep strict allowlists. |

## BR-8: Presets and ownership (Q9)

| Rule | Detail |
| --- | --- |
| BR-8.1 | The engine ships curated presets (`package-registries`, and per-class defaults); steps reference a preset or declare explicit targets. |
| BR-8.2 | The pipeline author owns the allowlist: presets are referenced/extended in `pipeline.yaml`, never imposed. |
| BR-8.3 | The engine never adds an undeclared target; an allowlist miss fails the connection loudly (dropped + surfaced), not silently. |
| BR-8.4 | Unknown preset names and malformed targets are load-time errors. |

## BR-9: Multi-toolchain coverage (user requirement)

| Rule | Detail |
| --- | --- |
| BR-9.1 | The `package-registries` preset covers pypi (`pypi.org`, `files.pythonhosted.org`, `api.osv.dev`), npm (`registry.npmjs.org`, `nodejs.org`), .NET (`api.nuget.org`), Rust (`crates.io`, `static.crates.io`), and VCS/source installs (`github.com`, `codeload.github.com`, `objects.githubusercontent.com`). |
| BR-9.2 | Restore steps reference the preset; plan 018's toolchain-agnostic restore stays agnostic - no toolchain-specific egress config exists. |
| BR-9.3 | Per-toolchain caches are pipeline declarations (`caches:`), not engine preset data; adding a toolchain adds a `caches:` entry (plus egress preset data where needed) - no engine change. |

## BR-10: Container model (D-1)

| Rule | Detail |
| --- | --- |
| BR-10.1 | The container pool is keyed by ContainerProfile = (resolved mount set, resolved egress policy, seccomp/cap policy). |
| BR-10.2 | Containers are created lazily per distinct profile and destroyed at pipeline end and on every error path (no leaked sandboxes). |
| BR-10.3 | The provision container keeps its trust-phase profile (broad egress, stamp-cached setup) - unchanged from unit-3 BR-13; the execution profiles stay frozen. |

## BR-11: Loud failure modes (pillar 5)

| Rule | Detail |
| --- | --- |
| BR-11.1 | Every failure - unknown mount/egress key, bad mode, malformed target, proxy application failure, sync conflict, container-create failure - is loud, naming the problem. |
| BR-11.2 | No silent degradation: a step whose mount set or egress policy cannot be realised refuses to run. |
| BR-11.3 | Error paths always destroy the created networks and containers. |

## BR-12: Testability (Q7)

| Rule | Detail |
| --- | --- |
| BR-12.1 | Pure functions (mount-set resolution, egress assembly, target matching, export planning, sync-back planning, profile keying) are unit-tested everywhere - the bulk of the tiered split. |
| BR-12.2 | Run-loop logic is tested against the fake Sandbox backend (unit-3 FD-5 shape); docker-marked tests remain opt-in and never silently skipped. |
| BR-12.3 | Enforcement-behaviour tests (the proxy actually denying unmatched traffic) are portable (no root) and run in normal CI. |

## Security-baseline mapping

| SECURITY rule | Disposition | Where |
| --- | --- | --- |
| SECURITY-03 application logging | Enforced | browser-class connection logging + sandboxed-execution audit + sync-back audit, host-side, no secrets |
| SECURITY-05 input validation | Enforced | BR-1.3 mount-path validation; BR-8.4 target/preset validation at load |
| SECURITY-06 least privilege | Enforced | BR-1 least-access mounts (strict default, ro default); BR-2 `.git` blindness; BR-3.3 rw window limited to declared caches |
| SECURITY-07 restrictive network config | Enforced | BR-5/6 network-level allowlists; BR-7 browser class explicit + logged |
| SECURITY-09 hardening | Enforced | `--cap-drop ALL` retained; no NET_ADMIN in-container (BR-5.2) |
| SECURITY-11 secure design | Enforced | defense in depth: least-access mounts + egress allowlists + `.git` blindness + loud failures |
| SECURITY-15 fail-safe defaults | Enforced | BR-11 loud failures + cleanup on all error paths |
| SECURITY-01 encryption at rest | N/A - library, no data stores | - |
| SECURITY-02 network intermediary logging | Enforced | the egress proxy is the network intermediary and logs at the enforcement point (BR-5/6) |
| SECURITY-04 HTTP headers | N/A - no web endpoints | - |
| SECURITY-08 access control | N/A - no application endpoints | - |
| SECURITY-10 supply chain | N/A (unit-3 scope: digest-pinned image, unchanged) | - |
| SECURITY-12 credential management | N/A (plan 019 scope: secrets store, unchanged) | - |
| SECURITY-13 integrity verification | N/A (unit-3 scope: image digest verification, unchanged) | - |
| SECURITY-14 alerting/monitoring | N/A - desktop library | - |

## Known residual risks (recorded)

- **R-1 (F2):** the cache bind-mounts are a deliberate, declared rw window into the real workspace. A compromised step can write the cache paths (not arbitrary paths). Accepted: caches are already agent-writable today via the rw workspace mount; this narrows rather than widens exposure.
- **R-2 (Q1 amended 2026-10-25):** proxy enforcement is cooperation-dependent - it covers HTTP(S) traffic from env-respecting clients only; a malicious process that ignores the proxy env can dial out directly (no kernel backstop under Docker). Accepted on all platforms as "best we can do with Docker"; kernel-level enforcement ships with the future native Linux backend (unit-3 FD-2 scope).
- **R-3 (Q8):** the browser broad-but-logged class allows arbitrary egress for that step class; the mitigation is the connection log plus the browser sandbox (plans 023-025). Accepted: the render-check target set is "any page dependency".
