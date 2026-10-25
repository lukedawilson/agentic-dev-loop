# 014 - Per-Stage Networking (opt-in network access)

- **Status**: COMPLETED (2026-10-23) - shipped via plan-014; network:true in both pipelines
- **Requested**: 2026-10-20 - "We need to be able to enable networking for a specific stage, either by having networking as a tool, or with a yaml parameter"
- **Written as part of**: planning doc + README update

## Background - current network posture

| Surface | Network | Mechanism |
| --- | --- | --- |
| Agent terminal (every agent step) | **OFF, always** | Execution container `--network none` (`isolation.py:293`) |
| Command steps | **OFF, always** | Same execution container |
| `setup:` hooks (command steps only) | **ON** | Separate provision container `--network bridge` (`isolation.py:315,472`), stamp-cached, destroyed after |
| Host side (LLM, viz, actions, docker CLI) | ON | Not sandboxed |

The profile default is `network_off=True` (BR-5). There is currently **no way** for a
pipeline author to grant network access to an agent step or a command step - the only
networked path inside the sandbox is the `setup:` provision phase, which is command-only,
runs before the step's commands, and is destroyed before the step executes.

## Why this is needed

- Agent steps that must fetch documentation, APIs, or package metadata mid-conversation
  currently fail with `network is unreachable`.
- Command steps that need egress (e.g. `curl` a fixture, query an internal API) have no
  sanctioned channel - the workaround (abusing `setup:`) is a misuse of the provision phase.
- The author knows some stages legitimately need the network; the harness should let them
  say so explicitly instead of forcing an always-on or always-off posture.

## The two candidate mechanisms (user-specified)

### Option A - YAML parameter (per-step `network: true`)

```yaml
steps:
  - name: fetch-docs
    command: curl -fsSL https://example.com/spec.json -o spec.json
    network: true            # ← new StepConfig field
```

- **Mechanics**: Docker network mode is **container-level** - it cannot be toggled per
  `docker exec`. Eager startup (Q2 loop-mechanics decision) therefore creates **two**
  execution containers when any step declares `network: true`: the default `--network none`
  container and a `--network bridge` container (otherwise identical: `--read-only`,
  `--cap-drop ALL`, single workspace mount, caps). Each exec routes to the container
  matching its step's network declaration. Zero infra mid-run is preserved.
- **Scope**: command steps AND agent steps (agent's terminal would run networked for that
  step - direct egress).
- **Residual risk (must be documented)**: command steps are agent-influence channels -
  an agent can plant files (e.g. tests) that a networked command step then executes,
  giving the agent *indirect* egress. Networked agent steps give *direct* egress. The
  networked container remains read-only + cap-dropped, so the blast radius is data
  exfiltration and reachability, not host compromise.

### Option B - Networking as a tool (agent-callable, host-side mediated)

- A new consumer tool (e.g. `fetch`) registered into the agent's tool surface, executed
  **host-side**: the host performs the HTTP(S) request and returns the (bounded) content
  to the agent.
- **Mechanics**: sandbox stays `--network none` **always** - the invariant never weakens.
  The tool enforces policy at the boundary: domain allowlist (per-pipeline config),
  method/size/content-type caps, redirect limits, request auditing (JSONL host-side).
- **Scope**: agent steps only. Command steps keep no-egress (setup covers installs).
- **Trade-off**: command steps cannot `curl`; anything a command step needs must be
  fetched by an agent step (via the tool) first, or pre-staged via `setup:`.

### Option C - Both, scoped by step type (recommended)

- `network: true` YAML parameter for **command steps** (authored by the pipeline owner;
  two-container design from A).
- Network **tool** for **agent steps** (agent terminal stays network-none forever; access
  is mediated, allowlisted, and audited - consistent with the loop-mechanics hardening
  philosophy that the sandbox boundary is never weakened, only given explicit doors).
- Rejected in combination: `network: true` on agent steps (direct unmediated egress
  undermines the isolation guarantee that is the product's core feature).

## Recommendation

**Option C.** Rationale:

| Concern | A only | B only | C |
| --- | --- | --- | --- |
| Agent gets direct network | Yes (weakest) | Never | Never |
| Command steps get egress | Yes | No | Yes |
| Sandbox invariant (`--network none` default) | Weakened per step | Always holds | Holds for agents; declared exception for commands |
| Policy control (allowlist, caps, audit) | None beyond container | Strong (host-side) | Strong for agents; documented residual for commands |
| Implementation cost | Low-mid (second container + routing) | Mid (tool + policy engine) | Sum of both |

## Implementation sketch (for the future unit)

| Area | Change |
| --- | --- |
| `config.py` | `StepConfig.network: bool = False` (command + agent steps; validation: `network: true` requires command step? No - A applies to command only under C; agent steps reject `network:` at load with pointer to the tool). `IsolationConfig` gains tool-policy block (allowlist domains, max bytes, method allowlist) for the `fetch` tool. |
| `isolation.py` | Two-container startup when any command step declares network; exec routing table step→container; payload/argv builders parametrised by network mode (pure, PBT-covered). |
| `agents.py` / registry | Register `fetch` consumer tool host-side; tool executes HTTP via the host (llm-free), applies allowlist/caps, writes audit record; SDK seam per COMPAT-SHIMS procedure. |
| Error catalog | AE-SBX-013 (networked container create failure), AE-SBX-014 (fetch policy violation) - loud, actionable, pointing at `docs/setup.md`. |
| Guard tests / PBT | Network-mode routing totality (every exec lands in the declared container class); profile invariant - `network: false` ⇒ `--network none` always; fetch policy deny-by-default (empty allowlist ⇒ every request rejected); audit record on every fetch; startup-sequence test gains the second container branch. |
| CI | Networked-container integration tests on ubuntu+windows (marked, opt-in locally); fetch-tool tests mock-free via a local HTTP test server. |
| AI-DLC path | Enter as a new unit through Requirements Analysis (story map extension, FR IDs), then the construction loop. |

## README + docs update (part of this request)

| File | Change |
| --- | --- |
| `README.md:33` | Kernel-level sandboxing row: "no network access" → "no network access **by default** - authors opt specific stages in" (links to the new feature) |
| `README.md:156` | `env:`/`setup:` reference row gains `network:` (command steps) + the fetch tool (agent steps) |
| `README.md` Usage section | One short subsection: "Per-stage networking" - YAML example, tool example, policy knobs |
| `docs/setup.md` | §security: networked-command residual (agent-influence channel) + fetch tool policy; §troubleshooting: AE-SBX-013/014 |
| `aidlc-docs` | Deferred until the feature becomes a unit (requirements/story-map at that point) |

**Note on sequencing**: the README describes a shipped product. If the feature lands as a
future unit, the README edits above ship **with that unit's implementation**, not before
it exists. Ask the user if they want the README pre-updated instead.

## Open questions (ask inline, one at a time, when refining)

1. Mechanism choice: A / B / C?
2. If C: should `network: true` on agent steps be a load-time error (recommended) or a warning?
3. Fetch tool policy defaults: empty allowlist (deny everything, author must list domains) vs allow-https-anywhere-by-default with caps?
4. Should networked command steps be restricted to non-`goto`-reachable stages, or is author declaration enough?

## Use-case evaluation - the consumer repo QA agent (Q1 answer)

Evaluated the consumer repo (`agent-engine/pipeline.yaml` + `sdk_agents/qa.agent.md`). The QA agent is runtime-verification: it boots the dotnet stack (`qa/boot-builder.sh`), mints a Kinde M2M token (`qa/mint-token.sh`), uploads fixtures (`qa/upload-source.sh`), curl-probes APIs, and runs a Playwright/headless-Chromium browser tier (`qa/probe.mjs`) against the served bundle.

| QA need | Under today's sandbox | Verdict |
| --- | --- | --- |
| Probe a localhost server it booted itself | `--network none` keeps in-container loopback - `curl localhost:…` works | ✅ already fine |
| Kinde token mint (HTTPS POST to `KINDESUBDOMAIN`) | blocked | ❌ needs real egress |
| `dotnet build` / `dotnet test` NuGet restore | blocked | ❌ needs real egress (or full restore in `setup:`) |
| npm/Playwright/chromium fetches | blocked | ❌ needs egress (chromium itself is an image/provision concern) |

**Conclusion**: the QA use case requires **toolchain-level egress** - NuGet, npm, Playwright, Kinde HTTPS. A host-side `fetch` tool cannot help here: it mediates agent-initiated HTTP calls only, while these are network calls made *by build tools the agent invokes* (dotnet/npm/node). **Mechanism B alone fails this use case.**

**Amended mechanism (supersedes C as recommended)**: `network: true` as a per-step YAML parameter on **both command AND agent steps** (author-declared, default off; action steps never take it - host-side). Agent steps *may* declare network - the earlier "agent steps never get direct egress" stance is overturned by this evidence. The `fetch` tool remains an optional future companion for agents needing light mediated HTTP without full egress.

Security consequence (accepted + documented): a networked agent step gives the agent raw egress for that stage. Author opt-in is the control; the default stays `--network none` everywhere; the networked container keeps `--read-only --cap-drop ALL` and the single-mount set. Threat-model note: networked stages are the only sandbox stages where an agent could exfiltrate - pipeline authors should reserve them for stages that need it (QA, integration).

Side finding: the consumer repo's pipeline still uses `agent_engine.extensions.aidlc` (pre-rename) and would need the rename migration + always-on sandbox reconciliation when it upgrades to the new wheel.

## Resolved decisions

- **Mechanism** (2026-10-20): `network: true` per-step YAML param on command AND agent steps; fetch tool = optional future companion. Direct agent egress permitted by author declaration, default off.
- **README/docs timing** (2026-10-20): edits ship WITH the implementation commit - README describes the shipped product.
