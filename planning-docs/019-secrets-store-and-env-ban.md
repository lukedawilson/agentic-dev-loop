# 019 - Secrets store + `.env*` ban

> **Status: COMPLETED**

**Origin:** security review of the agentic-dev-loop sandbox boundary (2026-10-23). Secret exfiltration ranked the #1 exposure: network-on containers run agent-written code and installed packages with the workspace mounted - and the workspace contains the repo `.env` (the consumer: Kinde creds, read by `qa/common.sh` directly from the file).

**Agreed design decisions (user, 2026-10-23):**
1. Provide a `secrets:` section in the pipeline config.
2. Banning `.env*` files is the enforcement, and is *sufficient* - a user who deliberately reintroduces secrets into the workspace has accepted the risk; the engine's job is to refuse by default, loudly.
3. All steps - including qa - consume secrets the same way: declared refs, resolved host-side, injected into the step's exec env only. No special-casing.
4. Workspace IP exfiltration (per-step mount granularity, egress policy) is a **separate unit**, untouched by this one.
5. Where the shape of a step allows, secret-consuming execution should be a `command` step (mechanical script), not an `agent` step - so the LLM and the secret never meet. Agents hold secrets only when the step's job genuinely requires the agent to exercise them.

## Problem, precisely

`resolve_sandbox_env` injects declared step env into the exec request; there is no way to inject a *secret* without putting its value in the committed pipeline yaml. So consumers fall back to secret-bearing files in the workspace (`.env`), which every step's container mounts read-write, and which agent-written code / installed packages can read and egress from any network-on step.

## How secrets reach the app

The store is a **host-side, run-start resolution**; the secret travels only in process env, never through the workspace:

1. At run start, the engine process (on the host) resolves every declared ref - e.g. `kinde-client-secret` → `from: env, key: KINDIE_CLIENT_SECRET` → the host's own env var value.
2. When a step that declares the ref executes, the engine injects the value into that step's exec container **env only** - the same `resolve_sandbox_env` path step env uses today.
3. The locally-running app inherits it like any child process: the step's shell reads `$KINDIE_CLIENT_SECRET` and passes it to the app under test (env var of the app process, or config it hands off). The app runs in the same container, so process-env inheritance suffices.

The secret exists in exactly three places: the host's store, the engine process's memory for the run, and the exec env of steps that declared it. Not the workspace, not `state_dir`, not the pipeline yaml.

## Residual: can a rogue LLM still get to it?

**Yes - in the steps that declare it. That is inherent, not a bug.** Anything in a step's env is readable by the agent running in that step, and qa has terminal + network: a rogue qa agent can `printenv`, read the app's process env (`/proc/<pid>/environ`, shared PID namespace), use the creds, and POST them anywhere. Output scrubbing is hygiene - it keeps values out of `state_dir` and retry notes - but it happens engine-side *after* the agent has already seen the value in its own terminal. Scrubbing protects future attempts, not the current one.

What the store actually buys, stated honestly: **the secret exists only inside the steps that must have it, and nowhere else.** A rogue LLM in test/conformance/restore/dev, a typosquatted package's install scripts, a careless `env` log from any other step - all get nothing, because they never had it. The one residual is the secret-touching step itself.

Mitigations for that residual, in order of leverage:

1. **Keep agent steps off the secret list where possible (decision 5).** A secret consumed by a mechanical script doesn't need an LLM in the loop: restructure so the secret-consuming execution is a `command` step (script runs the app with creds, runs playwright mechanically), while the agent writes code in a secret-free context. LLM and secret never meet. qa-as-agent-with-creds is the worst case, not the default case.
2. **Least-privilege creds** - scoped, short-lived, test-tenant-only (Kinde test client, bucket-scoped R2 keys). A leaked test cred shouldn't be worth stealing.
3. **Egress allowlisting** (the IP-exfiltration unit) - bounds *where* a rogue agent holding a secret could send it.

Threat model: for a self-hosted loop the LLM isn't an attacker in itself - the attacker is prompt injection through the spec/docs/package READMEs/Figma content, or a malicious model provider. Against those, "the secret only exists where its job requires it" is the whole game.

## Design

### Config schema

```yaml
secrets:                              # top-level; refs only - never values
  kinde-client-secret:                # ref name (arbitrary)
    from: env                         # provider name
    key: KINDIE_CLIENT_SECRET         # provider-specific locator
  r2-keys:
    from: env
    key: YOSK_R2_KEYS
```

Step-level opt-in (agent/command steps; action steps are host-side and get nothing):

```yaml
  - name: qa
    agent: qa
    secrets: [kinde-client-secret, r2-keys]
```

- Values never appear in the pipeline yaml, the workspace, or `state_dir`. Refs only.
- A secret is injected into a step's exec env **only** when the step lists the ref.
- Unresolvable ref or unknown provider → fail fast at run start, naming the ref, the step, and the provider error. No silent degradation (AGENTS.md: fail-fast config and secrets).

### Provider interface (engine-side)

`resolve(refs) -> dict[str, str]` per provider, executed host-side once at run start:
- Ship **`env` first**: resolve from a host environment variable. Simplest possible store, matches the existing `api_key_env` pattern, zero new dependencies.
- Follow-up providers (same interface, later): `keychain` (macOS), `op` (1Password CLI), `sops` (encrypted file). The interface is the compatibility contract.

### Injection path

Merge resolved secrets into the existing `resolve_sandbox_env` fixed-base + step-env assembly in `isolation.py` - same exec-request path, no new channel. Injection happens at exec time per step; the network-off dev container gets only what dev declares (nothing today).

### `.env*` ban - the enforcement

- At run start (after config load), scan the workspace root for `.env*` files. Any hit → fail fast, naming the files, with the remedy text ("move these values into `secrets:` refs / host env and delete the files").
- **Whitelist convention files:** `.env.example`, `.env.template`, `.env.sample` - documentation fixtures must not trip the ban.
- Applies regardless of `network:` flags: an agent can copy `.env` values into code or reports even network-off; the workspace must simply not contain secrets.
- Engine's own pipeline must pass the ban - this repo must hold no `.env*` secrets by the time the check ships.

### Output scrubbing

The store knows every resolved value at run time. Any engine-written output - captured step output, retry notes ("Previous attempt feedback"), artifacts, `state_dir` files - must have resolved secret values scrubbed before being written. (Hygiene, not a control: the declaring step's agent has already seen the value.)

## Consumer migration (acceptance test - the consumer)

1. `qa/common.sh`: read Kinde creds from environment variables, not the repo `.env`.
2. Delete the repo `.env` (values move to host env / store).
3. the consumer `pipeline.yaml`: add the `secrets:` block and `secrets: [...]` on the qa step.
4. A full run must pass QA against real Kinde/R2 with no `.env` in the workspace.
5. Evaluate restructure per decision 5: can the secret-consuming part of qa move into a command step, leaving the qa agent secret-free?

## Work items

1. **Engine - config:** `secrets:` section in `PipelineConfig` + step-level `secrets` field; validation (refs resolve, providers known, action steps reject secrets, no values in yaml - fail fast on a value-shaped ref).
2. **Engine - providers:** provider registry + `env` provider + `resolve` executed host-side at run start; unresolved → loud error.
3. **Engine - injection:** merge into `resolve_sandbox_env` per step (isolation.py); never into provision container env, never into `state_dir`, never into agent context (except what the step legitimately gets).
4. **Engine - ban:** startup `.env*` scan with convention-file whitelist; fail fast naming files and remedy.
5. **Engine - scrub:** scrub resolved values from every captured output path (stages.py notes, outputs.py artifacts, aidlc captures).
6. **Tests:** unit (schema/validation, provider resolution, injection assembly, ban whitelist, scrubber) + docker-marked integration (secret injected into a network-on step's env, absent from network-off step's env, absent from state_dir; `.env` present → run refused).
7. **Docs:** README secrets section, `docs/setup.md` migration notes, consumer migration checklist.

## Verification checklist

- [ ] Pipeline with `secrets:` + step opt-in injects values only into declaring steps (probe via command step printing env → output scrubbed of values).
- [ ] Run with `.env` in workspace refuses to start and names the file; `.env.example` does not trip it.
- [ ] Missing/unresolvable ref fails fast with the ref and step named.
- [ ] the consumer full run green with no `.env` in the workspace.
- [ ] No resolved value appears in `state_dir`, retry notes, or artifacts after a run that consumed secrets.

## Out of scope (separate units)

- Workspace IP exfiltration: per-step mount granularity, egress policy.
- Host-side code execution (agent-writable extensions/engine code executed on the host).
- Typosquat / restore-step supply-chain posture (mechanism shipped in 018; lockfile dial is its own decision).
