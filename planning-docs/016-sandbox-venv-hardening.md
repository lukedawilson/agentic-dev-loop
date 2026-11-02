# 016 - Sandbox venv hardening
> **Status: SUPERSEDED by 052** (2026-11-02) - the stamp/probe/venv apparatus this plan describes is deleted; setup now runs fresh at the step boundary into the export.

> **Status: COMPLETED** (2026-10-23) - shipped: setup-stamp venv self-heal implemented (isolation.py probes the venv before trusting a warm stamp).

Extracted from `planning-docs/017-remove-unit-4-adapter.md` at user request (2026-10-22): the hardening work is independent of the unit-4 adapter removal and travels as its own plan.

## Setup-stamp hardening (added 2026-10-22)

**Trigger**: the first plan-016 run failed its sandboxed test step with `timeout: failed to run command '.venv-sandbox/bin/python': No such file or directory`. Root cause chain: a host-side-rebuilt darwin `.venv-sandbox` (symlink → `/opt/homebrew/...`) sat in the bind-mounted workspace; the setup stamp HIT (same cmd + lockfiles) so provision was skipped; the Linux exec container then executed the darwin venv's `bin/python`, whose symlink target doesn't exist in-container → ENOENT. The image was never broken - workspace state + stamp blindness was.

**Fragility**: `setup_stamp_key` captures setup cmd + lockfile hashes but NOT the venv's validity/interpreter, so any host-side mutation of a workspace venv silently breaks the sandboxed test step until `--refresh`/`--clean-stamps` or manual re-provision.

**Work items**:

1. **Pipeline-level (zero code)** - make the repo pipeline.yaml test-step `setup:` idempotent: validate the existing venv before reuse and rebuild when invalid, with explicit grouping:
   `{ [ -x .venv-sandbox/bin/python ] && .venv-sandbox/bin/python -c 'import sys' ; } || { rm -rf .venv-sandbox && python -m venv .venv-sandbox ; } && .venv-sandbox/bin/pip install -e ".[dev]"`
   A broken symlink fails `[ -x ]` (test follows symlinks) → rebuild with the container's python. Covers the host-tampered-venv class.
2. **Engine-level** - make the provision phase self-healing in `isolation.py` (BR-13 adjacent): before running the declared setup cmd, probe the venv state that the pipeline relies on - specifically, if `.venv-sandbox/bin/python` exists but is not executable/importable, remove it first (fail loud if removal impossible). Add: unit test for the probe/repair helper + a docker-marked integration test for the exact repro (stamp hit + host-darwin venv → self-healed re-provision, command passes). Update `docs/setup.md` troubleshooting with the new behavior.
3. **Verification** - the repro scenario (host-rebuilt venv + warm stamp) must self-heal instead of ENOENT, on top of the existing 016 verification list.
