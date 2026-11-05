# Unit 10 Infrastructure Design Plan - multi-provider runtime

Status: in progress. Next stage after Infrastructure Design = Code Generation (3 staged parts).

## Stage context

Functional Design + NFR Requirements approved (2026-11-05). Unit-10 adds no new container machinery: the subprocess harnesses (claude CLI via claude-agent-sdk, codex CLI via openai-codex) run on the HOST as ordinary processes, and every command they execute routes through the existing unit-3/5 Docker sandbox path via the bridge. The infrastructure design therefore covers: the optional-extras packaging and their installation path into dev/CI environments, the auth/credential store layout, the CI tiers for contract tests versus opt-in live smoke, the MCP server lifecycle, and the dogfood-gate procedure for the live hardening smoke. Existing infra (containers, egress proxy, state dir, CI ubuntu+windows) is reused unchanged.

## Open questions

### ID-1 - Optional extras and CI installation

Context: claude-agent-sdk and openai-codex are optional extras on the main package. Contract tests import the real SDK objects (no network), so CI needs the extras installed; live smoke stays manual.

A) **Extras on the main package; CI installs the extras for the contract-test job** - `pyproject.toml` gains `[project.optional-dependencies] claude` / `copilot` with floor pins; the CI job for `tests/test_sdk_compat.py` + the new contract tests installs `.[claude,copilot]`; the base CI job stays extras-free (light install preserved). (Recommended)

B) **Separate CI job with locked SDK versions only** - contract tests pin exact SDK versions in CI via a separate requirements file, extras remain floor-pinned for users.

C) **Contract tests shipped as part of the extras themselves** - no, rejected: tests must run in the main repo CI, not in consumer installs.

X) Other

[Answer]: A - extras on the main package; CI installs the extras for the compat/contract-test job; base CI job stays extras-free (light install preserved). (user answer 2026-11-05)

---

### ID-2 - Live hardening smoke placement

Context: NFR-4=A - the live smoke suite (harness refuses a native tool call; end-to-end claude/copilot agent run through the bridge) needs real subscriptions and runs at the dogfood gate.

A) **Marked opt-in suite + documented gate procedure** - a `@pytest.mark.live_harness` suite that is always skipped unless `RUN_LIVE_HARNESS=1` is set; the part-2/part-3 dogfood gate procedure documents running it manually with real subscriptions before the parts close; CI never runs it. (Recommended)

B) **GitHub Actions `workflow_dispatch` job** - the suite runs in CI on manual trigger with secrets-held credentials; heavier secret management, and subscription-bucket drain in CI.

X) Other

[Answer]: A - marked opt-in suite + documented gate procedure: pytest.mark.live_harness skipped unless RUN_LIVE_HARNESS=1; the part-2/part-3 dogfood gate procedure documents running it manually with real subscriptions before the parts close; CI never runs it. (user answer 2026-11-05)

---

## Steps

- [x] Analyze FD/NFR artifacts for infrastructure implications (no new containers; host-side harnesses; reused sandbox/egress/state machinery)
- [x] Resolve open questions (ID-1..ID-2, inline; both A)
- [x] Generate artifacts: infrastructure-design.md + deployment-architecture.md under aidlc-docs/construction/unit-10/infrastructure-design/; shared-infrastructure.md updated (sections 6-7: provider auth store, optional extras + CI tiers)
- [ ] Completion message + approval

## Artifact References

- Optional deps today: `pyproject.toml` (openhands-sdk pin + COMPAT-SHIMS version guard in `tests/test_sdk_compat.py`)
- State/auth dirs: `~/.agentic-dev-loop/state` (isolation config), new `~/.agentic-dev-loop/auth/` for the copilot token
- CI: existing ubuntu + windows jobs (unit-3 ID-6)
- Dogfood convention: `agentic-dev-loop/pipeline.yaml` is this repo's own pipeline

## Stage Outcome

Approved by user 2026-11-05 ("B" - Continue to Next Stage: Code Generation Part 1). No change requests.
