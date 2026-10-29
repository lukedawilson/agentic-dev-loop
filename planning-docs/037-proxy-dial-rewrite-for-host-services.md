# 037-proxy-dial-rewrite-for-host-services.md

> **Status: PLANNED**

Sequencing: first of the security-domain pair (037 then 038). After
that, per the user's priority order: 035, 034, 036.

## Scope

Engine (egress proxy dial rewrite), plus one prompt section in the qa
agent prompts. The prompt files are tooling paths in both the engine
repo (`agentic-dev-loop/sdk_agents/qa.agent.md`) and the consumer repo
(`agentic-dev-loop/sdk_agents/qa.agent.md`); both copies need a human
commit, the loop's commit action does not sweep tooling paths.

## Context

Consumer run 5367f0c488d8, unit-11 QA: the builder's AWS SDK calls to
MinIO (advertised to the step as
`R2__Endpoint=http://host.docker.internal:9000`) 502'd. The egress
proxy's policy check passed (the qa step's browser-class policy allows
the target), but the proxy dials the upstream from the host, where the
container-only name `host.docker.internal` does not resolve
(`egress_proxy.py` `_open`, `asyncio.open_connection`). The dial fails,
the client sees 502 Bad Gateway. The QA agent's workaround was an inline
`NO_PROXY` env prefix on the builder boot command: a self-invented,
unlogged bypass of the enforcement point.

Two candidate fixes were compared in chat. Adding
`host.docker.internal` to the engine's NO_PROXY list was rejected on
security grounds: NO_PROXY is hostname-scoped (no port syntax), removes
policy enforcement and audit logging for all host traffic, and exposes
every host service to every network-enabled sandbox. The dial-site
rewrite keeps the connection inside the proxy: the policy check runs
first, enforcement events are recorded, port-scoped allowlist entries
remain possible, and no new reachability exists beyond what policy
already admitted.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | Rewrite `host.docker.internal` to a host-reachable address only at the proxy's upstream dial site; never in the request line, the Host header, or the policy input | AGREED |
| D2 | The rewrite default is `host.docker.internal` to `127.0.0.1` (host loopback is correct on macOS Docker Desktop and for published ports on Linux); the mapping is a dict on EgressProxy so platforms and tests can override | AGREED |
| D3 | NO_PROXY stays `localhost,127.0.0.1`. The NO_PROXY bypass is rejected | AGREED |
| D4 | qa agent prompts gain an environment-contract section: local dev container, application-testing layer, not staging; never modify proxy env vars inline; report unreachable host services instead of working around them | AGREED |
| D5 | This is the stopgap; 038 supersedes the enforcement model, and the dial rewrite is retained inside 038's proxy container | AGREED |

## Changes

### 1. Dial-site rewrite (engine)

`egress_proxy.py`: EgressProxy gains a `dial_rewrites` field
(`dict[str, str]`, default `{"host.docker.internal": "127.0.0.1"}`).
`_open` applies the rewrite before `asyncio.open_connection`. The
CONNECT and forward handlers are unchanged: `match_target` runs before
the dial, enforcement events are recorded as today, and the Host header
is forwarded with the original hostname (rewriting it would break
signed requests such as S3 SigV4, which signs the Host header).

### 2. QA environment-contract section (tooling path, both repos)

New section in the qa agent prompt (engine repo and consumer repo
copies, identical text):

- This step runs in a local dev container, not staging. It is an
  application-testing layer.
- `127.0.0.1` services belong to the container itself. Host services
  are reached at `host.docker.internal` through the sandbox egress
  proxy.
- Real external infrastructure (for example real R2, real deploy) is
  out of scope and not verifiable in this sandbox; do not burn turns
  trying.
- Never modify proxy environment variables (`HTTP_PROXY`,
  `HTTPS_PROXY`, `NO_PROXY`) on commands or in processes you start. If
  a host service is unreachable, report the failure and the code path
  in your final response instead of working around the proxy.

Both copies are tooling paths and need a human commit.

### 3. README note (tooling path)

One line in the README sandbox/egress section: `host.docker.internal`
targets are rewritten to host loopback at the proxy dial site so signed
host-service traffic reaches the host through the enforced, logged
path. Needs a human commit.

## Verification

- New engine tests: a real local upstream server on `127.0.0.1`, an
  EgressProxy with the default rewrite, a CONNECT for
  `host.docker.internal:<port>` reaches the server; the enforcement
  event is recorded; the Host header received by the upstream is
  unchanged.
- Existing egress proxy tests stay green (no rewrite for other hosts).
- Full pytest suite green.
- ASCII only, no em dash, no consumer name (repo rules).

## Non-goals

- NO_PROXY changes (rejected).
- Network-level enforcement (038).
- Mechanical denial of env-prefix commands (035's banned-commands
  deny-list).
