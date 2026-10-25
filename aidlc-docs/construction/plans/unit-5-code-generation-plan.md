# Unit 5 Code Generation Plan - workspace egress isolation

Status: **Part 1 APPROVED (2026-10-25, user: "Ok, approved, continue"). Part 2 (implementation) pending the code-mode switch** (no code is written in planner mode, per repo AGENTS.md).

## Scope

Per-step mount granularity (strict-by-default `mounts` with rw/ro/absent), egress policy via an engine-managed per-policy egress proxy (the 2026-10-25 amendment: proxy on ALL platforms, no nftables, no privileged CI), `.git` blindness via copy-based step-workspace (entity renamed StepWorkspace across unit-5 docs, ERD review 2026-10-25), git-aware sync-back, profile-keyed container pool, multi-toolchain presets as engine data. No new dependencies (ID-1). Folded in from the ERD design review (2026-10-25): `run_agent` moves from a module-level function onto `Runtime` as a method (step 15, KD-8).

## TDD steps

- [ ] 1. **Config guard (red first)** - `tests/test_unit5_config.py`: `StepConfig` accepts `mounts` + `egress`; validators reject `.git` entries, absolute paths, `..` traversal, host-only dirs, `state_dir`, unknown egress preset names, and any `mounts`/`egress` on host-side actions. Fails today (`extra=forbid`).
- [ ] 2. **Config (green)** - `core/config.py`: `MountSpec {path, mode}` + `EgressSpec`; `StepConfig.mounts: list[MountSpec] = []`, `StepConfig.egress: EgressSpec | None = None`; validation per BR-1 (strict-by-default stays: empty/missing `mounts` = empty mount set).
- [ ] 3. **Pure functions (red)** - `tests/test_unit5_pure.py`: `resolve_mount_set` (strict default, declared-writes, cache preservation Q3/Q4/BR-1/BR-3); `assemble_egress_policy` (preset expansion determinism, unknown-preset loud, policy class classification Q8/Q9/BR-8/BR-9); `match_target` (domain semantics BR-6); `container_profile_key` (stability D-1/BR-10); `plan_step_workspace` (path invariants, no `.git`, cache existence loud BR-2/BR-3); `plan_sync_back` (declared-rw only, git-aware, conflict detection BR-4).
- [ ] 4. **Pure functions (green)** - `engine/presets.py` (preset data, D-2/BR-9), `engine/egress_policy.py`, `engine/mounts.py`.
- [ ] 5. **Export runtime (red then green)** - `tests/test_step_workspace.py`: tmp workspace with `.git` + host-only dirs + dirty files; export copies everything except `.git`/host-only dirs into `state_dir/exports/<run-id>/`; missing cache path = loud. Green: `engine/step_workspace.py` (copy-based, ID-4).
- [ ] 6. **Sync-back runtime (red then green)** - `tests/test_sync_back.py`: changed/added/deleted files on declared-rw paths sync back; non-declared paths never sync; `.git` never touched; conflicts loud (BR-4). Green: `engine/sync_back.py` (shutil diff-copy, ID-5).
- [ ] 7. **Egress proxy (red)** - `tests/test_egress_proxy.py`: proxy on `127.0.0.1:0` (ephemeral, ID-3); allowed hostname: CONNECT tunnel + absolute-URI forward both pass; unmatched hostname: deny + logged enforcement event (BR-6, SECURITY-02); loopback test servers only, no root (ID-1/ID-6).
- [ ] 8. **Egress proxy (green)** - `engine/egress_proxy.py`: hand-rolled minimal asyncio forward proxy, Python stdlib only (ID-1).
- [ ] 9. **DockerBackend payloads (red then green)** - `tests/test_isolation.py` additions: per-profile create payload (`--add-host=host.docker.internal:host-gateway`, `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` env, mounts from MountSet, export cwd, existing hardening retained: `--cap-drop ALL`, `--read-only`, seccomp+`SYS_CHROOT`, tmpfs, host uid:gid, ID-2); profile-keyed lazy pool creation replacing the two-container model (D-1/BR-10); provision keeps the broad-egress trust-phase profile; cleanup + fail-closed on proxy death (BR-11). Payload assembly unit-tested; real-Docker marked/opt-in.
- [ ] 10. **Run-loop wiring** - `graph.py`/`stages.py` pass step `mounts`/`egress` into the backend; provision step keeps its trust-phase profile; `network: false` steps keep `--network none`.
- [ ] 11. **Dogfood pipeline.yaml** - explicit `mounts`/`egress` declarations per step (dev, restore, test, review, qa, commit) so strict-by-default (Q4) doesn't break the repo's own loop; restore references the registries preset (BR-9).
- [ ] 12. **PBT** - `tests/test_unit5_pbt.py`: serialisation round-trips (MountSpec/EgressSpec/TargetEntry), profile-key stability, strict-default property (empty input = empty mount set), preset-expansion determinism, export-path invariants (PBT-03).
- [ ] 13. **CI wiring** - proxy enforcement tests (steps 7-8) run in the existing ubuntu+windows matrix jobs: loopback only, no root, never silently skipped; docker-marked opt-in unchanged; NO privileged job (NFR-5.7 amended, ID-6).
- [ ] 14. **Migration docs + full regression** - CHANGELOG (version bump + mounts/egress migration table, NFR-5.3), `docs/setup.md` mounts/egress section + loud-failure docs (NFR-5.8), the reference consumer repo upgrade note; full suite green including docker-marked.
- [ ] 15. **run_agent onto Runtime (folded in from the ERD design review, 2026-10-25)** - red: `tests/test_stages.py` pins the agent-step flow (capture, write, parse verdict, route) unchanged; green: move module-level `run_agent` (`engine/stages.py`) onto `Runtime.run_agent` as a method; update call sites in `graph.py`/`stages.py`; delete the module-level function (no shim). KD-8.

## Story traceability

| Source | Steps |
| --- | --- |
| BR-1 strict-by-default + declared writes + path validation | 1-4 |
| BR-2 .git blindness by construction / BR-3 cache bind-mounts + loud checks | 3, 5, 9 |
| BR-4 sync-back declared-rw only, git-aware, conflicts loud | 3, 6 |
| BR-5 per-policy proxy / BR-6 hostname matching / BR-7 broad-but-logged browser class | 3, 7, 8 |
| BR-8 presets + ownership + loud misses / BR-9 multi-toolchain | 3, 4, 11 |
| BR-10 profile-keyed pool / BR-11 loud failures + cleanup / BR-12 tiered testability | 3, 9, 13 |
| NFR-5.3 hard break + migration notes | 14 |
| NFR-5.4 idempotent infra ops / NFR-5.5 audit logs (proxy deny events, sync-back reports) | 7, 8, 9 |
| NFR-5.7 tiered tests, no privileged job / NFR-5.8 loud actionable failures | 1-13 |
| Intent pillars: least-access mounts, egress allowlists, ro-where-possible, .git blindness, loud failure modes | 1-14 |
| ERD design review (2026-10-25): run_agent onto Runtime | 15 |

## Files

**Created:** `src/agentic_dev_loop/engine/presets.py`, `egress_policy.py`, `mounts.py`, `egress_proxy.py`, `step_workspace.py`, `sync_back.py`; `tests/test_unit5_config.py`, `test_unit5_pure.py`, `test_step_workspace.py`, `test_sync_back.py`, `test_egress_proxy.py` (loopback fixtures), `test_unit5_pbt.py`.

**Modified:** `src/agentic_dev_loop/core/config.py` (StepConfig fields + validators); `src/agentic_dev_loop/engine/isolation.py` (DockerBackend: pool, host-gateway, proxy env, export cwd, proxy lifecycle); `src/agentic_dev_loop/engine/graph.py`, `engine/stages.py` (run-loop wiring + `run_agent` -> `Runtime.run_agent`, step 15); `tests/test_isolation.py`; `tests/test_stages.py` (step 15); `agentic-dev-loop/pipeline.yaml` (declarations); `CHANGELOG.md`; `docs/setup.md`.

**Unchanged:** `pyproject.toml` (no new deps, ID-1); `viz/`; `actions.py`; deps_policy machinery; lockfiles.

**Coordination note:** planning-docs/026-commercial-name-scrub.md (pending code-mode handoff) touches `tests/test_config.py`, `tests/test_actions.py`, `tests/test_agents.py`, `tests/test_graph.py`, `tests/test_stages.py` and engine docstrings. Both land in `config.py`/`tests/`; sequence them (scrub first or same change-set) to avoid merge noise.

## Key decisions

- **KD-1** Profile-keyed container pool replaces the two persistent containers; key = `container_profile_key(mount_set, policy)` (D-1/BR-10).
- **KD-2** Stdlib-only asyncio forward proxy; no new dependencies (ID-1).
- **KD-3** Containers reach the proxy via `--add-host=host.docker.internal:host-gateway` + proxy env; no IP discovery (ID-2).
- **KD-4** Copy-based export into `state_dir/exports/<run-id>/`; `.git` absent by construction; caches bind-mounted rw at same relative paths (ID-4, F2).
- **KD-5** In-process shutil diff-copy sync-back; no rsync binary dependency (ID-5).
- **KD-6** Strict-by-default mounts; dogfood pipeline + consumer repos declare explicitly (Q4, NFR-5.3).
- **KD-7** Presets ship as engine data (Python module), extendable per toolchain without mechanism changes (D-2/BR-9).
- **KD-8** `run_agent` lives on `Runtime` as a method; the module-level function is removed (ERD design review decision, 2026-10-25).
