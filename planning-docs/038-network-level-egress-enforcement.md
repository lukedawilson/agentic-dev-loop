# 038-network-level-egress-enforcement.md

> **Status: PLANNED**

Merges and supersedes the former dial-rewrite stopgap
(`proxy-dial-rewrite-for-host-services.md`); that file is deleted by
this plan. The dial rewrite's machinery is retained here, resolved for
the containerized topology.

## Scope

Engine (sandbox network topology, proxy containerization, lifecycle,
dial handling). The qa prompt section and the README paragraph are
tooling-path deliverables and need human commits (both repos for the
prompt file).

## Context

The sandbox's network layer is today advisory, not enforced. Sandbox
containers reach the host through two unfiltered paths: the
`--add-host host.docker.internal:host-gateway` alias (needed because
the proxy binds host loopback and is advertised at that name) and, on
Linux, the docker bridge gateway IP (`172.17.0.1` and similar), which
is the host side of the bridge and dialable by any process. The egress
proxy is a policy and logging point for well-behaved clients only;
`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` are conventions a process can
ignore or override. The live run demonstrated two faces of the gap:
the QA agent's inline `NO_PROXY` env prefix created an unlogged host
channel, and the host-run proxy's own upstream dial of
`host.docker.internal` (a container-only name) 502'd, pushing the
agent toward that bypass. Prompt rules do not restrain dependencies or
generated code, only the agent. This plan makes egress enforcement
structural: the sandbox has no route to the host at all, and every
byte of egress passes through the policy-checked, logged proxy.

Containerizing the proxy resolves the dial-rewrite question. The
host-run proxy needed `host.docker.internal` rewritten because the
name does not resolve on the host. The containerized proxy carries its
own host-gateway alias on its host-access attachment, so the name
resolves natively inside the proxy container; no rewrite is needed in
the default topology (D8).

Threat classes and what closes them, per the chat analysis:

- The agent's own env-override workaround: 036's command deny-list plus
  the qa persona scope rule (this plan, Change 4).
- Hostile code the agent executes (builder, restored dependencies,
  generated code): only this plan.
- Host services' own auth: out of scope, always the host side.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | One per-run docker internal network (`docker network create --internal`) hosts every network-on sandbox container; no host-gateway alias, no default route to the host | AGREED |
| D2 | The egress proxy becomes a container on that internal network, attached to a second network with host access; it is the only egress path. Policy and logging code unchanged. The proxy's second attachment is not reachable from the internal network (no route), so it is not a jump host; the proxy container does not enable IP forwarding | AGREED |
| D3 | Sandbox env: `HTTP_PROXY`/`HTTPS_PROXY` point at the proxy container's address on the internal network (docker DNS name); NO_PROXY stays `localhost,127.0.0.1`. Consumer env such as `R2__Endpoint=http://host.docker.internal:9000` keeps working: sandbox to proxy (policy) to host (native resolution, D8) | AGREED |
| D4 | Network-off steps (dev) keep `--network none`; nothing changes for them | AGREED |
| D5 | Proxy container runs the engine's isolation image with `egress_proxy.py` and `egress_policy.py` bind-mounted read-only from the host, started with a container-mode entrypoint | AGREED (provisional: requires the isolation image to include python3; otherwise a dedicated minimal proxy image) |
| D6 | Engine creates network and proxy container at run start and tears both down at run end; the orphan reaper (030) covers them. Enforcement events are written to the mounted state dir and merged into the existing audit log | AGREED |
| D7 | Nothing sanctioned breaks: today only proxy-advertised traffic is sanctioned, and it keeps working through the containerized proxy | AGREED |
| D8 | Dial-site rewrite machinery (retained from the deleted dial-rewrite stopgap): the proxy resolves `host.docker.internal` natively via its own host-gateway alias, so the default rewrite map is EMPTY. The `dial_rewrites` dict on EgressProxy is retained as an override for platforms where host-gateway resolution fails (map to the gateway IP). The rewrite, when configured, applies only at the proxy's upstream dial site: never in the request line, the Host header, or the policy input. The former host-run default `host.docker.internal -> 127.0.0.1` is DROPPED: inside the proxy container, 127.0.0.1 is the container's own loopback | AGREED |
| D9 | qa agent prompts gain a persona scope section: the step is an application tester in a local dev container, not staging; test the application's product behavior; if a dependency or host service is unreachable, report it and move on; never debug the harness, the sandbox, or infrastructure; never modify proxy env vars inline | AGREED |
| D10 | NO_PROXY stays `localhost,127.0.0.1`. The NO_PROXY bypass is rejected | AGREED |

## Changes

### 1. Sandbox network topology (engine)

`isolation.py`: create the per-run internal network before the first
network-on container; attach network-on containers with
`--network <run-net>` instead of `bridge`; drop the
`--add-host host.docker.internal:host-gateway` flag for sandbox
containers. The proxy container alone keeps a host-gateway mapping on
its host-access attachment.

Implementation note: proxied clients send the CONNECT target as a
literal hostname without local resolution, so the sandbox does not need
the alias. If a client still requires local resolution, the engine adds
an `/etc/hosts` entry at container start mapping `host.docker.internal`
to the proxy container's internal IP (known after the proxy starts);
this restores resolvability without restoring a host route.

### 2. Containerized proxy (engine)

`egress_proxy.py` gains a container-mode entrypoint (serve the policy,
write enforcement events to a mounted file) and the `dial_rewrites`
field (`dict[str, str]`, default `{}`, applied in `_open` before
`asyncio.open_connection`; `match_target` runs before the dial and the
Host header is forwarded with the original hostname, which keeps
signed requests such as S3 SigV4 intact). The engine starts the proxy
container on both networks, then rewrites the sandbox proxy URL from
`http://host.docker.internal:<port>` to the proxy container's internal
DNS name.

### 3. Lifecycle and teardown (engine)

Network and proxy container are torn down at run end and picked up by
the orphan reaper if the engine dies. Enforcement events append to the
existing audit stream.

### 4. QA persona scope section (tooling path, both repos)

New section in the qa agent prompt (engine repo and consumer repo
copies, identical text):

- You are an application tester in a local dev container, not staging.
  Test the application's product behavior.
- `127.0.0.1` services belong to the container itself. Host services
  are reached at `host.docker.internal` through the sandbox egress
  proxy.
- If a dependency or host service is unreachable, report the failure
  and the code path in your final response and move on. Do not debug
  the harness, the sandbox, or infrastructure.
- Real external infrastructure (for example real R2, real deploy) is
  out of scope and not verifiable in this sandbox; do not burn turns
  trying.
- Never modify proxy environment variables (`HTTP_PROXY`,
  `HTTPS_PROXY`, `NO_PROXY`) on commands or in processes you start.

Both copies are tooling paths and need a human commit.

### 5. Tests

Payload-level tests: sandbox payloads carry the internal network name,
no host-gateway flag, and the containerized proxy URL; proxy payload
carries both attachments. Unit tests for the entrypoint's event file
writer and for a configured `dial_rewrites` mapping (a real local
upstream server on `127.0.0.1`, a CONNECT for `host.docker.internal`
with an explicit mapping reaches the server; the enforcement event is
recorded; the Host header received by the upstream is unchanged).
Actual docker behavior (sandbox cannot dial the host, proxy relay
works) is verified manually per the sandbox probes in the repo's
testing runbook, since the suite does not run under docker.

### 6. Documentation (tooling path)

README sandbox section: describe the internal-network topology, the
containerized proxy, that sandbox containers have no host route, and
that `host.docker.internal` resolves natively inside the proxy
container (with `dial_rewrites` as the platform override). Needs a
human commit.

## Verification

- New payload/entrypoint tests green; dial-rewrite unit tests green;
  existing suite green.
- Manual probe matrix (runbook): from a network-on sandbox, a direct
  dial of the bridge gateway and of the host IP fails; a proxied
  request to a host service succeeds with an enforcement event
  recorded; a policy-denied target is refused and logged.
- ASCII only, no em dash, no consumer name (repo rules).

## Non-goals

- Host-service authentication (host side).
- seccomp or iptables filtering (rejected in the chat analysis:
  seccomp connect filtering is UDP-bypassable and platform-fragile,
  host iptables does not apply under Docker Desktop).
- Command-level deny-lists (036).
- NO_PROXY changes (rejected).
