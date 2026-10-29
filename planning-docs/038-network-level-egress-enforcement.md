# 038-network-level-egress-enforcement.md

> **Status: PLANNED**

Sequencing: second of the security-domain pair (037 then 038). After
that, per the user's priority order: 035, 034, 036.

## Scope

Engine (sandbox network topology, proxy containerization, lifecycle).
README documentation is the only tooling-path deliverable and needs a
human commit.

## Context

The sandbox's network layer is today advisory, not enforced. Sandbox
containers reach the host through two unfiltered paths: the
`--add-host host.docker.internal:host-gateway` alias (needed because
the proxy binds host loopback and is advertised at that name) and, on
Linux, the docker bridge gateway IP (`172.17.0.1` and similar), which
is the host side of the bridge and dialable by any process. The egress
proxy is a policy and logging point for well-behaved clients only;
`HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` are conventions a process can
ignore or override. The live run demonstrated the gap: the QA agent's
inline `NO_PROXY` env prefix created an unlogged host channel. Prompt
rules do not restrain dependencies or generated code, only the agent.
This plan makes egress enforcement structural: the sandbox has no route
to the host at all, and every byte of egress passes through the
policy-checked, logged proxy.

Threat classes and what closes them, per the chat analysis:

- The agent's own env-override workaround: 036's command deny-list plus
  the 037 prompt rule.
- Hostile code the agent executes (builder, restored dependencies,
  generated code): only this plan.
- Host services' own auth: out of scope, always the host side.

## Decisions

| # | Decision | Proposal |
| --- | --- | --- |
| D1 | One per-run docker internal network (`docker network create --internal`) hosts every network-on sandbox container; no host-gateway alias, no default route to the host | AGREED |
| D2 | The egress proxy becomes a container on that internal network, attached to a second network with host access; it is the only egress path. Policy and logging code unchanged; 037's dial rewrite applies inside the proxy container. The proxy's second attachment is not reachable from the internal network (no route), so it is not a jump host; the proxy container does not enable IP forwarding | AGREED |
| D3 | Sandbox env: `HTTP_PROXY`/`HTTPS_PROXY` point at the proxy container's address on the internal network (docker DNS name); NO_PROXY stays `localhost,127.0.0.1`. Consumer env such as `R2__Endpoint=http://host.docker.internal:9000` keeps working: sandbox to proxy (policy) to host (dial rewrite) | AGREED |
| D4 | Network-off steps (dev) keep `--network none`; nothing changes for them | AGREED |
| D5 | Proxy container runs the engine's isolation image with `egress_proxy.py` and `egress_policy.py` bind-mounted read-only from the host, started with a container-mode entrypoint | AGREED (provisional: requires the isolation image to include python3; otherwise a dedicated minimal proxy image) |
| D6 | Engine creates network and proxy container at run start and tears both down at run end; the orphan reaper (030) covers them. Enforcement events are written to the mounted state dir and merged into the existing audit log | AGREED |
| D7 | Nothing sanctioned breaks: today only proxy-advertised traffic is sanctioned, and it keeps working through the containerized proxy | AGREED |

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
write enforcement events to a mounted file). The engine starts the
proxy container on both networks, then rewrites the sandbox proxy URL
from `http://host.docker.internal:<port>` to the proxy container's
internal DNS name.

### 3. Lifecycle and teardown (engine)

Network and proxy container are torn down at run end and picked up by
the orphan reaper if the engine dies. Enforcement events append to the
existing audit stream.

### 4. Tests

Payload-level tests: sandbox payloads carry the internal network name,
no host-gateway flag, and the containerized proxy URL; proxy payload
carries both attachments. Unit tests for the entrypoint's event file
writer. Actual docker behavior (sandbox cannot dial the host, proxy
relay works) is verified manually per the sandbox probes in the repo's
testing runbook, since the suite does not run under docker.

### 5. Documentation (tooling path)

README sandbox section: describe the internal-network topology, the
containerized proxy, and that sandbox containers have no host route.
Needs a human commit.

## Verification

- New payload/entrypoint tests green; existing suite green.
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
