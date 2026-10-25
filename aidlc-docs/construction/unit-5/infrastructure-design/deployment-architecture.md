# Unit 5 Deployment Architecture - workspace egress isolation

## Runtime layout (per OS)

| Host OS | Container runtime | Proxy reachability | Enforcement point |
| --- | --- | --- | --- |
| macOS | Docker Desktop (VM) | `--add-host=host.docker.internal:host-gateway` (Docker 20.10+) | Engine-run asyncio proxy, loopback-bound on the host |
| Windows | Docker Desktop (VM) | same host-gateway mapping | same |
| Linux | native Docker | host-gateway mapping supported since 20.10 | same |

No OS branches in enforcement: one proxy implementation, one reachability mechanism, one env-injection shape. The platform difference is only where Docker runs.

## state_dir layout (additions)

```
<state_dir>/                       (host-only, existing)
  exports/<run-id>/                transient export; .git and host-only dirs excluded;
                                   declared cache paths bind-mounted rw at same relative paths
  egress/<policy-id>-<run-id>.log  per-policy connection log (browser class: broad-but-logged)
  sync-back/<run-id>.json          sync-back report (copied/changed/deleted, conflicts)
```

Export and per-run logs are cleaned at run end and on error paths (BR-10/BR-11); egress logs rotate with a bounded size (NFR-5.5).

## Container assembly (per profile)

Each distinct ContainerProfile (resolved mount set + resolved egress policy + seccomp/cap policy) gets one container, created lazily, destroyed at pipeline end:

1. `--network bridge` (network-on profiles) or `--network none` (network-off).
2. `--add-host=host.docker.internal:host-gateway` on network-on containers.
3. Mounts assembled per the resolved MountSet (export root rw/ro, cache bind-mounts rw, ro submounts where declared ro).
4. Proxy env injected (HTTP_PROXY/HTTPS_PROXY/NO_PROXY) when a policy exists; broad-but-logged browser class also routes through the proxy with the any-egress rule set.
5. Existing hardening retained: `--cap-drop ALL`, `--read-only`, seccomp profile + `--cap-add SYS_CHROOT` (023), tmpfs /tmp /root, host uid:gid (unchanged from unit-3).

## CI jobs

| Job | Scope | Trigger |
| --- | --- | --- |
| Unit + PBT (ubuntu + windows matrix) | NEW: proxy enforcement tests against local 127.0.0.1 test servers (allow listed / deny + log unlisted); pure-function tests (match_target, mount resolution, export planning, sync-back planning) | every push |
| Docker-marked opt-in | Unchanged: real-Docker integration (export + sync-back + proxy env through a real container) | opt-in (daemon present) |
| Privileged Linux job | None - removed per user directive (2026-10-25); kernel-level enforcement ships with unit-7 | - |

All enforcement tests are rootless and portable; nothing is silently skipped.

## Migration mechanics (NFR-5.3 hard break)

- Version bump + CHANGELOG note: strict-by-default mounts means pipelines without explicit `mounts:` declarations mount nothing and fail loudly.
- In-repo dogfood `pipeline.yaml` gains explicit `mounts` + `egress` declarations at implementation (dev/restore/test/qa/commit steps).
- The reference consumer repo gets the same treatment as a documented migration example.
- Failures are loud with actionable messages (NFR-5.8): name the missing declaration and the field that fixes it.
