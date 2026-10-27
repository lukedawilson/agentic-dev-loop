# Domain Entities - unit-5 (workspace egress isolation)

> FD-stage (2026-10-25). Per DDD standards: descent audit applied; domain-standards extension enforced. The domain was exposed before this model was proposed (DOMAIN-01 in the plan); every concept below traces to an exposition answer (DOMAIN-03).

## Trace List (DOMAIN-03)

| Domain concept | Exposition answer it came from |
| --- | --- |
| MountSpec / MountSet | Dimension 1 ("Mount Set" concept) + Q3/Q4 answers (declared-writes, strict-by-default) |
| TargetEntry / EgressPreset / EgressPolicy | Dimension 1 ("Egress Policy" concept) + Q1/Q2/Q8/Q9 answers |
| StepWorkspace | Dimensions 1-2 (the .git-free derivative; engine-owned) + Q5/F1/F2 answers |
| SyncBackReport | Dimension 3 (run-time contexts) + F1 answer |
| ContainerProfile | Dimension 4 (change-togetherness: mount + network + caps move together) + D-1 |
| StepConfig.mounts / StepConfig.egress | Dimension 2 (StepConfig owns the declarations) |
| Provision profile | Dimensions 1/6 (trusted complement: provision phase, broad egress) |
| Registries/CDNs/app endpoints | Dimension 6 (external, the allowlist targets - not our domain) |

## New Value Objects (descent step 1)

### `MountSpec` / `MountSet` (NEW - descent step 1, carries invariant behaviour)

| Field | Meaning |
| --- | --- |
| `path` | repo-relative mount path |
| `mode` | `rw` | `ro` |

Behaviour: validation at construction - repo-relative only, no traversal, never `.git`, host-only dirs, or `state_dir` (BR-1.3). `MountSet` = a validated collection; empty set = mount nothing (strict default, BR-1.1). Not anaemic.

### `TargetEntry` / `EgressPreset` / `EgressPolicy` (NEW - descent step 1)

| Field | Meaning |
| --- | --- |
| `host` | domain (or stable IP/CIDR for the Q2 exception) |
| `port` | optional port constraint |
| `class` | `allowlist` | `broad-but-logged` (browser steps, BR-7) |
| `preset` | named preset reference, expanded at assembly (BR-8) |

Behaviour: preset expansion + target validation at construction (BR-8.4); `EgressPolicy` = the resolved target set + its class; serialisation round-trip is a PBT target.

### `ContainerProfile` (NEW - descent step 1)

| Field | Meaning |
| --- | --- |
| `mount_set` | resolved MountSet |
| `egress_policy` | resolved EgressPolicy |
| `seccomp_caps` | seccomp profile + cap set (unit-3/plan-023 surface, now profile-partitioned) |

Behaviour: `profile_key()` yields the stable pool key (D-1); equal profiles ⇒ same container. Pure function.

### `SyncBackReport` (NEW - descent step 1)

| Field | Meaning |
| --- | --- |
| `synced_paths` | declared-rw paths copied back (BR-4.2) |
| `conflicts` | loud on non-empty (BR-4.4; hard paths only after 029 partition) |
| `merged_paths` | conflicts on declared `merge_on_conflict:` paths, union-merged by the engine (029) |
| `step_id` / `timestamp` | audit linkage (BR-11, SECURITY-03) |

## New Entity (descent step 3)

### `StepWorkspace` (NEW - descent step 3, engine-owned state)

The .git-free derivative the sandbox mounts. Owned and mutated by the engine (materialise → bind caches → mount → sync-back → destroy), never by the sandbox:

- `materialise(repo, cache_paths, platform, excludes) -> StepWorkspacePlan` - copy-based materialisation into `state_dir/exports/<run-id>/` (ID-4); `.git` absent by construction; cache existence checks (missing = loud race guard; startup pre-creates declared caches, 028); `sync_excludes:` skipped at copy time.
- `mount_points()` - the Docker mount list: export root + cache bind-mounts at the same relative paths, rw (BR-3.1).
- `sync_back(declared_rw) -> SyncBackReport` - git-aware, sandbox detached, conflicts loud (BR-4).

## Modified Entities

| Entity | Change |
| --- | --- |
| `StepConfig` (config.py) | gains `mounts` (list of `{path, mode}`) and `egress` (targets/preset/class); both `extra=forbid` validated at load. `network: bool` keeps meaning "step may use the network" - the egress policy now decides what it may reach. |
| `PipelineConfig` (config.py) | gains top-level `caches`, `sync_excludes` (028), and `merge_on_conflict` (029) - each list[str], default `[]`, single strings coerced, validated at load with cross-field checks (redundancy against excludes/caches). |
| `DockerBackend` (engine/isolation.py) | the two-persistent-container model becomes the profile-keyed pool (BR-10); gains per-policy egress proxy management (BR-5) and export materialisation/sync-back orchestration. The `Sandbox` surface is unchanged (unit-3 extension seam). |
| `IsolationConfig` | unchanged this unit (mount/egress live per-step); egress presets are engine data, toolchain caches are now `PipelineConfig` author data (028). |

## Pure functions (descent step 0)

| Function | Signature | Notes |
| --- | --- | --- |
| `resolve_mount_set` | `(step_mounts) -> MountSet` | strict default, path/mode validation (BR-1) |
| `assemble_egress_policy` | `(step_egress) -> EgressPolicy` | preset expansion, class classification, validation (BR-7/8) |
| `match_target` | `(hostname, policy) -> allowed | denied` | hostname matching at the proxy; unmatched = deny + logged (BR-6) |
| `plan_step_workspace` | `(repo, cache_paths, platform, excludes) -> StepWorkspacePlan` | export layout + cache bind-mount list + existence checks + scratch excludes (BR-3/028) |
| `plan_sync_back` | `(export_changes, declared_rw, cache_paths, excludes) -> SyncPlan` | git-aware, declared-rw only minus caches/excludes, conflict detection (BR-4/028). At sync-back runtime conflicts are partitioned into merge paths (declared `merge_on_conflict:`, union-merged) and hard paths (absorb + announce + raise) (029). |
| `container_profile_key` | `(mount_set, policy) -> str` | stable pool key (D-1, BR-10) |

All pure - the PBT-03 targets (serialisation round-trips, key stability, strict-default property, preset-expansion determinism, export-path invariants).

## Descent Audit

| Concept | Descent step | Audit result |
| --- | --- | --- |
| MountSpec / MountSet | 1 (VO with invariants) | Correct |
| TargetEntry / EgressPreset / EgressPolicy | 1 (VO with invariants) | Correct |
| ContainerProfile | 1 (VO, pool key) | Correct |
| SyncBackReport | 1 (VO) | Correct |
| StepWorkspace | 3 (entity: engine-owned lifecycle state) | Correct |
| Pure functions | 0 (module-level, platform-independent) | Correct |
| Sandbox / DockerBackend | 3 (modified; surface retained) | Correct - extended, not replaced; the retained single-implementation `Sandbox` interface is the unit-3 user-directed exception, unchanged |
| Registries/CDNs/app endpoints | external actors | Not modelled - outside the domain |

No protocols introduced; no new single-implementation interfaces (the pool is concrete `DockerBackend` behaviour behind the existing seam).

## Traceability

- FR/NFR story-map rows do not yet exist for unit-5 (intent-defined scope; story-map covers units 1-3). This unit's requirements trace to the intent pillars: least-access mounts (BR-1), egress allowlists (BR-5/6/8/9), read-only where possible (BR-1.2/1.4), `.git` blindness (BR-2), loud failure modes (BR-11), plus the multi-toolchain requirement (BR-9).
