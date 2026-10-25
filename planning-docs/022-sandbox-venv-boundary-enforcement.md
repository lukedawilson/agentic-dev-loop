# 022 - Sandbox venv boundary enforcement (mask `.venv`, relocate `.venv-sandbox`, purge docs)

> **Status: COMPLETED**

---

## Origin

The 2026-10-23 "ping-pong" incident: the 021 loop's dev agent built a
container-python `.venv` inside the workspace (18:08), which dangled on the
macOS host and broke `/publish` and host pytest. Host repairs with homebrew
python then break the container's view of the same dir. Root cause: `.venv`
(host-only) sits inside the workspace, and the sandbox mount set is exactly
`{workspace}` rw - agents can always touch it, and the same directory cannot
serve both a macOS host and a Linux container.

The requested fix has three items:
1. Exclude `.venv` from the sandbox mount (host-only dir; the container never
   needs it). `.venv-sandbox` stays mounted - sandbox steps genuinely need it.
2. Purge `.venv/bin/python` from sandbox-facing text → `.venv-sandbox/bin/python`.
3. Optional hygiene: move `.venv-sandbox` out of the repo dir entirely (e.g.
   `~/.agentic-dev-loop/venvs/`) and mount it in - keeps the workspace clean
   of container artifacts and removes it from `git add -A` noise, while
   staying writable by sandbox steps either way.

## Why it is more complex than it looks

1. **Single-mount payloads today.** `docker_create_payload` and
   `docker_provision_payload` both emit exactly `-v {ws}:{ws}` (Q1's "mount
   set EXACTLY {workspace}" invariant). Adding a mask plus an optional venv
   mount changes the payload contract and the invariant - it must be
   re-documented deliberately, not silently widened.
2. **The provision container is in the loop too.** It also mounts
   `{workspace}` and runs every `setup:` hook - the test step's setup hook
   creates `.venv-sandbox`. If `.venv-sandbox` moves outside the workspace,
   *both* exec payloads and the provision payload need the new mount, or the
   setup hook silently recreates a workspace-local venv.
3. **The 016 venv self-heal chain touches the same path.** `venv_probe`
   (docker exec `{ws}/.venv-sandbox/bin/python`) and
   `remove_sandbox_venv(workspace)` (`rm -rf {ws}/.venv-sandbox`) assume the
   venv is a plain workspace directory. With a bind mount at that path,
   `rm -rf` on a mountpoint deletes contents but not the dir -
   `remove_sandbox_venv` must be re-verified for the mounted case
   (contents-delete fallback).
4. **Defaults must stay bit-compatible.** With `sandbox_venv_dir` unset,
   today's workspace-local `.venv-sandbox` behavior must be unchanged - the consumer
   and any other consumer must not observe a difference. The relocation is
   strictly opt-in config.
5. **Docker cannot exclude a subdirectory.** The only way to hide `.venv`
   under the workspace bind is a submount: `--tmpfs {ws}/.venv:ro` (no host
   dir to manage) or a bind of an empty host dir `-v {empty}:{ws}/.venv:ro`.
   `ro` matters: agents must fail loudly writing there, not silently succeed
   against a decoy.
6. **Cross-repo impact.** the consumer repo's host engine venv also lives in the
   workspace (`.venv`, non-editable install). Masking is safe there (dotnet
   steps never use `.venv`) and closes the same hole - but no consumer
   breakage is allowed, so the consumer's pipeline must keep loading unchanged.
7. **The docs purge is a classification exercise, not a sed.** 57
   `.venv/bin` hits split into FIX (sandbox/agent-facing), KEEP
   (host-facing), and stale-reference investigation (`examples/self/` no
   longer exists, so `tests/test_e2e_fixtures.py:30`'s comment about the
   example is stale).

## Design

### Item 1 - mask `.venv` in the sandbox

- New constant `HOST_ONLY_DIRS = (".venv",)`.
- `docker_create_payload` (both network variants) and
  `docker_provision_payload` append `--tmpfs {ws}/.venv:ro` after the
  workspace mount (submount shadows the bind).
- Mount-set invariant restated: `{workspace}` + mask mounts (ro empty tmpfs
  at `{ws}/.venv`). Nothing else. State-dir-never-mounted (Q1) unchanged.
- The provision container gets the mask too - setup hooks must not "repair"
  the host venv either.

### Item 3 - relocate `.venv-sandbox` (opt-in)

- `IsolationConfig` gains `sandbox_venv_dir: str | None = None`
  (`extra="forbid"` already enforced). When set: expanduser + absolute, and
  **must resolve outside the workspace** - fail loud otherwise (an
  in-workspace value defeats the purpose).
- Exec + provision payloads add `-v {sandbox_venv_dir}:{ws}/.venv-sandbox`
  (rw). Container paths are unchanged: pipeline commands keep referencing
  `.venv-sandbox/bin/python`; probe and self-heal keep operating at
  `{ws}/.venv-sandbox`.
- Thread through `DockerBackend(..., sandbox_venv_dir=...)` and
  `startup_sandbox`.
- Engine's own `pipeline.yaml` opts in:
  `isolation: { sandbox_venv_dir: ~/.agentic-dev-loop/venvs/agent-engine }`.

### Item 2 - docs purge

- **FIX (sandbox/agent-facing):** planning-docs 001–012 + 017 convention and
  checklist lines (~40 hits), `aidlc-docs/construction/plans/unit-2-code-generation-plan.md:60`,
  `viz_demo.py:11` + `:67`, `tests/test_e2e_fixtures.py:30` (stale comment).
- **KEEP (host-facing):** `setup-venv.sh`, `.opencode/commands/setup.md`,
  `.opencode/commands/publish.md`, `run-demo.sh`,
  `aidlc-docs/construction/unit-3/code/implementation-spec.md:383` (already
  documents the `.venv`=host / `.venv-sandbox`=sandbox split correctly),
  `tests/test_proc.py` argv fixtures (generic shell-classification data).

## Work items (TDD, red → green → refactor)

1. **RED** - `tests/test_isolation.py`: payload mask tests (create +
   provision include `--tmpfs {ws}/.venv:ro`); config tests for
   `sandbox_venv_dir` (load, expanduser, outside-workspace validation
   failure, extra mount present in both payloads); docker-marked integration
   test for probe/remove_sandbox_venv behavior against a mounted venv.
2. **GREEN** - constants + payload changes + `IsolationConfig` field +
   backend/startup threading.
3. Docs purge per classification; engine pipeline.yaml opts in to
   `sandbox_venv_dir`.
4. **REFACTOR** + full suite (`.venv/bin/python -m pytest tests/ -q -m "not docker"`);
   confirm the consumer's pipeline.yaml still loads and its dotnet steps are
   unaffected (default path, bit-compatible payloads).

## Verification checklist

- Host `.venv` survives a run untouched; any container-side write attempt to
  `{ws}/.venv` fails loudly (ro mask).
- With `sandbox_venv_dir` set: `.venv-sandbox` materializes at the outside
  path, absent from the repo dir after the run; test gate and restore step
  green in-container; `git add -A` sweep sees no venv noise.
- Default path (unset): payload argv identical to pre-change snapshots.
- Grep: zero `.venv/bin/python` occurrences in agent-facing files.
- the consumer repo pipeline: loads and runs unchanged (no `.venv` usage in its
  sandbox steps).

## Open questions

- Confirm Docker honors `ro` on a `--tmpfs` submount under a bind mount
  (fallback: bind an empty host dir read-only).
- `remove_sandbox_venv` on a mountpoint: is a contents-delete fallback
  needed?
- Should the consumer also opt in to `sandbox_venv_dir`? Its `.venv` is the engine's
  own install - masking it is safe, but no change is required for its
  sandbox to keep working. Defer to the consumer-migration step.

## Bootstrap pip hygiene (added 2026-10-23)

**Trigger:** the 022 construction run failed at the restore step: `pip-audit`
reported 12 known CVEs, all in pip 25.0.1 - the wheel CPython 3.12 bundles via
ensurepip, inherited by the dev image; nobody's deliberate pin. 021's audit
escalation fails the step, but pip is the bootstrap tool, not a pyproject pin:
dev cannot self-correct it, so the loop deadlocked on a valid audit finding it
has no legitimate way to fix (same shape as the 018 deadlock).

**Two mechanisms, at their natural layers:**

1. **Pipeline freshness (DONE - pipeline.yaml):** the network-on restore step
   now runs `pip install --upgrade pip` first, before the editable install and
   the audit. Runs every pass; a cheap no-op once current. Guarantees the
   audit only ever reports project-pin findings. Continuously fresh - catches
   drift forever, no human in the loop.
2. **Image hygiene baseline (work item):** the dev image should not SHIP a
   stale vulnerable bootstrap.
   - TRAP: `pip install --upgrade pip` in the Dockerfile upgrades only the
     system pip. `python -m venv` copies ensurepip's BUNDLED wheel
     (`stdlib/ensurepip/_bundled/pip-*.whl`), so venvs would still be born
     with 25.0.1. The correct mechanism is `python -m ensurepip --upgrade`,
     which refreshes the bundled wheel.
   - Fold into the S8 image-hardening findings (floating base tag, unpinned
     apt, pipe-to-shell dotnet-install): pin the base image digest, hash the
     install script, and add the ensurepip upgrade.
   - Digest-bump procedure: after republishing the image, update
     `DEFAULT_DEV_IMAGE_REF` in `engine/isolation.py` (both repos' pipelines
     pin their image digests).

**Verification:**

- Fresh container from the new image: `python -m venv /tmp/v && /tmp/v/bin/pip
  --version` shows the upgraded pip, not 25.0.1.
- With the current image (no image change yet), the pipeline fix alone still
  converges: restore passes audit because pip is upgraded in-step.

## Out of scope

- `.env*` files: already banned at scan time (plan 019); no mask needed.
- Full per-step mount granularity and egress policy: that is unit-5
  (intent written), which subsumes this plan's mount machinery.
- The four uncommitted files currently in the agent-engine working tree
  (021-loop output + the user-approved `trigger-agentic-dev-loop.sh` pin
  edit) - unrelated to this plan; left as-is at user's earlier instruction.
