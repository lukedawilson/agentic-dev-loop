# Unit 5 Tech Stack Decisions - workspace egress isolation

| ID | Decision | Detail |
| --- | --- | --- |
| TS-1 | **Egress proxy (primary, all platforms)** | The engine runs a per-policy HTTP(S) forward proxy (local, engine-managed); container env is forced through it; the proxy matches the request hostname (CONNECT target) per connection against the expanded policy. No root, no netfilter; logs enforcement events to state_dir. (NFR-5.2, BR-5, Q1 amended) |
| TS-2 | ~~**Egress proxy fallback**~~ SUPERSEDED (2026-10-25) - merged into TS-1; the proxy is no longer a fallback, it is the enforcement point on all platforms | ~~On macOS/Windows (daemon inside a VM) the engine runs an HTTP(S) forward proxy per policy; container env is forced through it; enforcement lives at the proxy. Same config shape as TS-1, different enforcement point; loud platform detection. (NFR-5.2, BR-5.3)~~ |
| TS-3 | **Worktree-export materialisation** | `git worktree add` (or archive) into a temp dir under state_dir; `.git` absent by construction (BR-2); export destroyed at pipeline end + error paths; declared cache paths bind-mounted into the export at the same relative paths, rw (F2). (Q5, BR-3) |
| TS-4 | **Profile-keyed container pool** | ContainerProfile = (resolved mount set, resolved egress policy, seccomp/cap policy); containers created lazily per distinct profile, destroyed at pipeline end + error paths; provision container keeps its broad-egress trust-phase profile. Extends unit-3's persistent-per-run model (D-1, BR-10). |
| TS-5 | **Egress presets as engine data; caches as pipeline data** | package-registries and other target sets ship as engine data resolved at policy-assembly time; author overrides add account-specific hosts; new toolchains = preset data + a `caches:` declaration in `pipeline.yaml`, never mechanism (D-2 amended 2026-10-25, Q9, BR-8/9). |
| TS-6 | **rsync-style git-aware sync-back** | After rw steps, declared-rw changes sync back to the real repo; sandbox detached during sync; conflicts loud (F1, BR-4). |
| TS-7 | **Pure-function core** | resolve_mount_set / assemble_egress_policy / match_target / plan_step_workspace / plan_sync_back / container_profile_key all pure; Docker/proxy transport isolated behind the existing Sandbox surface. (NFR-5.7, PBT targets from FD) |
| TS-8 | **Proxy enforcement tests in existing CI** | Enforcement-behaviour tests (the proxy actually denying unmatched traffic) are portable (no root) and run in normal CI (ubuntu + windows matrix), never silently skipped; export/sync-back tested against real repos in existing jobs. No privileged job. (NFR-5.7) |
| TS-9 | **Sync-back conflicts are one-off operator events, not agent errors** | A concurrent host edit is announced on the console and absorbed (host wins, baseline re-snapshotted) before `AE-SBX-016`; declared `merge_on_conflict:` paths union-merge instead of raising. The console (`_announce`) is the product surface - no logging library in src. (029, BR-4.4/BR-4.6) |

## Deferred

- Kernel-level egress enforcement (Landlock-style network rules) ships with the future native Linux backend (unit-3 FD-2 scope) - supersedes both the nftables path and Docker's network-policy features
- TLS SNI-based per-domain filtering refinements on the egress proxy (TS-1)
- Journal mirror / resume (unchanged unit-4 deferral)
- Interactive metering (unchanged unit-4 deferral)
