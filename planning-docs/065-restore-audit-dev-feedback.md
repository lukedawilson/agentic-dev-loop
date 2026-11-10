# 065 - Restore audit failure feedback to dev

> **Status: APPROVED**

## Scope

Two parts. Part 1 (done): an operator dependency bump that unblocks the
restore step's pip-audit gate, recorded here with its verification evidence.
Part 2 (proposed): close the feedback gap that let the dev agent ignore a
failing downstream gate for all ten attempts of a run. No engine change is
made by this plan yet; the proposed change below is for review.

## The problem

Run 1497bcd2b494 (`--plan planning-docs/060-brief-presence-gate.md`) failed
at the restore step on every one of its ten attempts. Each restore output
was identical: pip upgrade and editable install succeeded, then
`pip_audit --skip-editable` exited 1 on 39 CVEs in pillow 12.3.0 (fix
version 13.0.0). The step's `on_fail: { goto: dev, retry: true }` routed
back to dev each time, per the config comment's design: "dev can bump the
pin, and the bumped pin is audited again on the next pass, so the loop
self-corrects."

Dev never bumped anything. Its summary called the CVEs "pre-existing ...
unrelated to this plan" and passed, so every iteration changed nothing and
the run burned all ten attempts with zero state change.

Two root causes:

1. **The dependency graph genuinely blocked the fix.** openhands-tools
   ~=1.17.0 declares `browser-use>=0.8.0` (unpinned). A fresh pip resolve
   lands browser-use 1.0.0, which hard-pins `pillow==12.3.0`. No manifest
   edit short of pinning browser-use itself (or a conflicting direct pillow
   floor) moves the sandbox off the vulnerable pillow. Bumping
   openhands-tools does not help today: every current browser-use release
   from 0.12.0 up hard-pins pillow==12.x, and the only CVE-free pillow is
   13.0.0.
2. **Dev was never told the failing gate was its job.** The failure reached
   dev's prompt, but in the wrong category (see Verified facts), and nothing
   in dev's instructions binds a dependency-audit failure to dev.

## Verified facts

- The command node (`make_command_node`, stages.py ~1047) records a failing
  gate as `mechanical_notes` (output tail) plus `verified_state`
  (`restore: attempt N: exit 1`). It never writes the `notes` channel.
- Agent steps with structured verdicts (review, qa) write their failing
  output to `notes` (stages.py ~890). `build_context` renders `notes` under
  "### Previous attempt feedback" and `mechanical_notes` under "## Mechanical
  gate output (pipeline-captured process output, not agent claims)".
- dev.agent.md WORKFLOW step 2 makes "Previous attempt feedback" the only
  required-fix trigger. Its TEST GATE section says a failing gate is retried
  "with the failing output in your notes under *Previous attempt feedback*",
  which is not what the engine does for command steps (test and restore both
  render as mechanical gate output). That contract predates the FR-11.3
  channel split documented in `build_context` and was not updated.
- The injected dependency policy (`deps_policy: open`) covers only "a
  dependency that is not already present", not repair of existing pins. The
  restore comment's "dev can bump the pin" lives in pipeline.yaml, which
  dev never reads.
- Constraint chain, verified against PyPI metadata 2026-11-10:
  browser-use 0.11.13 requires `pillow>=11.2.1` (open); 0.12.0 pins
  `pillow==12.1.0`; 0.13.0 pins `pillow==12.2.0`; 1.0.0 pins
  `pillow==12.3.0`. openhands-tools 1.17.0 requires `browser-use>=0.8.0`.
  reportlab requires `pillow>=9.0.0`. openhands-sdk 1.17.0 has no pillow
  requirement.
- pillow 13.0.0 (requires-python >=3.11, uploaded 2026-10-16) has zero
  known vulnerabilities in OSV. pillow 12.3.0 has the 39 CVEs the audit
  reported.

## Part 1: done

Commit `ccec9b5` (`fix(deps): pin browser-use 0.11.13 and pillow>=13.0.0`),
following the existing floor-pin pattern (filelock, pyasn1, pypdf):

- `browser-use==0.11.13`: the last release with an open pillow constraint,
  so a fresh pip resolve lands pillow 13.0.0. Matches what the host
  environment already runs.
- `pillow>=13.0.0`: keeps the audit green and makes a future return to a
  hard-pinned browser-use fail the install loudly instead of silently
  reintroducing the CVEs.
- CHANGELOG entry under Unreleased.

Verification evidence:

- Fresh venv, plain `pip install -e ".[dev]"` (the sandbox resolve path):
  browser-use 0.11.13, pillow 13.0.0, install exit 0.
- `pip_audit --skip-editable`: "No known vulnerabilities found", exit 0.
- Full suite: 1504 passed, 1 skipped (`-m "not docker"`, the pipeline's
  test command). openhands-sdk stays ~=1.17.0, so the COMPAT-SHIMS version
  guard is untouched and no shim re-verification is required.

## Part 2: proposed

Make a failing command gate a required fix in dev's contract, so the loop's
self-correction design actually reaches the agent.

1. **dev.agent.md**: fix the TEST GATE wording (failing gate output arrives
   under "Mechanical gate output" and "Verified state", not notes) and add
   an AUDIT GATE section: a dependency-audit failure means a pin carries
   known CVEs; dev's fix is a manifest edit (the floor-pin pattern), which
   restore re-audits on the next pass.
2. **stages.py** (one of, not both):
   - make command-node failures also write `notes`, so the existing
     "Previous attempt feedback = required fixes" trigger fires for test and
     restore; or
   - make `build_context` render `mechanical_notes` under a heading dev's
     contract treats as required fixes.
3. **pipeline.yaml** (optional): document the audit escalation in the dev
   step's `custom_prompt` instead of a comment only humans read.

Red/green per repo TDD rules: tests exist for `build_context` section
assembly and for the command node's failure channels; extend them to pin the
chosen rendering before changing the node.

## Open risks

- pillow 13.0.0 against browser-use 0.11.13 is metadata-sanctioned
  (`>=11.2.1`) and already running in the host environment, but the test
  suite does not exercise browser-use at runtime; a consumer-path
  incompatibility would surface only in real use.
- The next openhands-tools bump must re-check the browser-use pin: a newer
  openhands-tools may require a browser-use line that no longer admits
  pillow 13, in which case the pillow floor turns the conflict into a loud
  install failure (intended) and the operator resolves it, or the
  browser-use pin moves to a compatible release.
- A future browser-use 0.11.x release adding new CVEs would re-trip the
  audit; the fix then is the same class of pin decision as filelock/pyasn1.
