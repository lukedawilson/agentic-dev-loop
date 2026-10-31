# 048-egress-proxy-ip-collision.md

> **Status: PROPOSED**

## Scope

This repository (the engine) only. Consumer pipelines need no config or
prompt change; the fix lands through the engine wheel. The one live
consequence sits in a consumer run (see Context) and is closed there by a
QA re-run on the fixed wheel, not by this plan.

## Context

### The incident

A consumer-repo run (run 5367f0c488d8, plan 004 portal-to-root-move)
failed its qa step with AE-SBX-020 on every command across two full
attempt cycles plus retries, roughly 2.5 hours. No QA probe ever
executed: the unit's runtime behaviour remains genuinely unverified. The
qa step's configuration was valid (`network: true`,
`egress: { class: browser }`); the failure is upstream of policy
parsing, in the loop's own proxy lifecycle code.

### Root cause (source-verified, isolation.py)

The run carries two distinct egress policies. The dev lane's
restore/test/editor-tests/conformance steps share the package-registries
preset, while the qa step carries the browser class. Profiles are pooled
by `(container_profile_key(mount_set, policy), network_flag)`
(isolation.py:231, 1215-1250), and `_start_profile` starts one proxy
container per distinct key (isolation.py:1300-1335). Every proxy,
however, is pinned to the same address:
`_proxy_internal_ip()` (isolation.py:1383-1395) inspects the run network
subnet and returns `first_usable_ip(subnets)`, which is `hosts[1]` of the
first IPv4 network (isolation.py:313-326). For the run's 172.20.0.0/16
subnet that is always 172.20.0.2.

So a run with two live policies creates a second proxy and runs
`docker network connect --ip 172.20.0.2` against an address the first
proxy already holds. Docker refuses ("Address already in use"),
`_start_proxy_container` hits its attach-failed branch, removes the
proxy container, and raises AE-SBX-020 (isolation.py:1336-1395). The
collision is deterministic: the first policy's proxy lives for the whole
orchestrator process (the pool is only torn down in `close()`,
isolation.py:1535-1540), so every later attempt to bring up a second
policy fails the same way. Six QA attempts, one collision.

This is why earlier stages were green: they all shared one policy, one
pooled profile, one proxy. QA was the run's first second policy. Two
distinct proxy names in the run confirm the two policies
(`-proxy-7b5f04c9a8ecc3f1` vs `-proxy-690f12e0e28f0b59`).

### Evidence quality notes

- The daemon-side stderr ("Address already in use") was not captured in
  the audit log (zero matches), so the attach-failed branch is inferred
  from source mechanics plus the agent-visible AE-SBX-020 message, not
  from captured daemon output. The error message itself is generic and
  does not name the cause.
- Live inspection after the run found the run network present but empty
  of containers: the loop had exited and `close()` had torn the pool
  down. The live state therefore no longer demonstrates the collision;
  the mechanism stands on the source reading above.
- Not implicated: plan 030's infra classifier (this is a connect
  failure, not a daemon I/O failure) and plan 039's hardening itself.
  039's pinned-IP design (D4) is however the load-bearing assumption
  this bug breaks: one IP per run network was wrong the moment a run
  could carry two proxies.

## Decisions

| # | Decision | Proposal | Answer |
|---|---|---|---|
| D1 | Distinct IP per proxy | Replace the single `_proxy_internal_ip()` with a per-backend allocation: `_proxy_ips: dict[str, str]` keyed by proxy name. The first allocation keeps today's value (`hosts[1]`) so single-policy runs are byte-identical; each further proxy gets the next usable address (`hosts[2]`, `hosts[3]`, ...). `_start_profile` builds its profile's proxy URL from its own allocation and passes that to the exec payload's proxy env (verify the exact injection point during implementation; the profile already carries its own proxy name, so the wiring point exists). | APPROVED as proposed |
| D2 | Exhaustion guard | If a run requests more proxies than the subnet's usable hosts, fail fast with an error naming the subnet size and the proxy count, instead of letting docker emit a confusing error. | APPROVED as proposed |
| D3 | Error clarity | On `docker network connect` failure, include the stderr tail in the AE-SBX-020 message so the agent-visible error names the real cause (for example "Address already in use"), not just "egress proxy network attach failed". | APPROVED as proposed |
| D4 | TDD tests | Unit (fake runner seam): two proxies receive distinct `--ip` values; single-proxy runs still pin `hosts[1]`; exhaustion raises the D2 error; connect failure raises AE-SBX-020 with the stderr tail in the message. Payload tests updated. Docker-marked integration: two proxy containers attach to one run network and both relay a loopback dial. | APPROVED as proposed |
| D5 | No config surface | Always-on fix, no `pipeline.yaml` keys (mirrors plan 030 D5). | APPROVED as proposed |
| D6 | Rollout | Engine change lands through the loop itself from the published wheel (the 030 dogfood pattern). The consumer repo needs no pipeline or prompt change; a QA re-run of unit 004 on the new wheel is the live proof. The consumer loop needs a pipx reinstall to see the new wheel. | APPROVED as proposed |

## Out of scope

- The alternative fix: one proxy per run network with merged policies.
  Deferred unless D1 proves problematic (D1 is smaller and preserves
  per-policy isolation, which 038 deliberately built).
- Cross-process container reaping: containers from an earlier run were
  observed still Up days later while their network was deleted. Separate
  hygiene defect, does not cause this collision.
- Verifying unit 004's runtime behaviour: that is the consumer's QA
  re-run after this fix lands.
- A dedicated audit event for proxy connect failures: D3 improves the
  raised message only.

## Changes (file by file)

### `src/agentic_dev_loop/engine/isolation.py`

- `DockerBackend.__init__`: `self._proxy_ips: dict[str, str] = {}`.
- New helper `_allocate_proxy_ip(subnets, proxy_name)`: returns the
  existing allocation for the name, else the first `hosts[i]` not yet
  allocated (i = 1 for the first proxy), else raises the D2 error.
- `_proxy_internal_ip(proxy_name)` becomes the wrapper used by
  `_start_proxy_container`, or its call sites switch to the helper.
- `_start_proxy_container`: pass the per-proxy IP to the connect
  `--ip`; on connect failure include the stderr tail in AE-SBX-020.
- `_start_profile`: build the profile's proxy URL from its proxy's
  allocated IP and wire it into the exec payload's proxy env (verify
  the current injection point during implementation).
- `close()`: clear `_proxy_ips` with the pool.

### `tests/test_isolation.py`

- `TestProxyIpAllocation`: distinct `--ip` per proxy; first proxy
  keeps `hosts[1]`; exhaustion raises the named error; connect failure
  message carries the stderr tail; payload assertions updated.

### `tests/test_isolation_integration.py`

- Docker-marked: two proxies on one run network both attach and relay a
  loopback dial.

### Docs

- `docs/setup.md`: AE-SBX-020 row updated (message now carries the
  daemon stderr tail).
- `README.md` / `CHANGELOG.md`: multi-policy proxy fix noted.
- `COMPAT-SHIMS.md`: no entry (no SDK reliance).

## Risks

- Allocation state lives in backend memory alongside the pool, so it
  persists exactly as long as the proxies it tracks.
- 039 D4 made the proxy bind the address passed as an argument; distinct
  addresses flow through that argument unchanged. Confirm during
  implementation that the entrypoint wiring passes the per-proxy IP, not
  a shared constant.
- Multi-policy runs now hold more proxies; each already carries 039's
  memory/CPU limits, so the added footprint is bounded.

## Acceptance checks (sandbox-executable)

1. Unit: two proxy starts receive distinct `--ip` values; the first
   keeps today's value.
2. Unit: exhaustion of the usable hosts raises the D2 error before any
   docker call.
3. Unit: a failed connect raises AE-SBX-020 whose message contains the
   daemon stderr tail.
4. Integration (docker-marked): two proxies attach to one run network
   and both relay a dial.
5. Full suite green; ASCII only, no em dash (repo rules).

## Verification

TDD per repo rule: red tests first for D1, D2, D3, then green, then
refactor. The docker-marked integration test runs where docker is
available; the CI/non-docker suite skips it as today.

## Follow-ups

- A dedicated audit event for proxy lifecycle (start, connect, fail)
  so the next failure is diagnosed from the audit alone.
- Revisit the merged-proxy alternative if per-policy proxy counts grow.
- A startup subnet-size sanity note in docs/setup.md for pipelines that
  expect many concurrent policies.
