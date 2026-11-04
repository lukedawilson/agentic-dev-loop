---
description: Sweep all test suites for low-preservation-value tests (TDD artefacts, cosmetic pins, orphaned suites), report tiered findings, then act on instruction. Usage: /test-reaper
---

# Test Reaper - Preservation-Value Sweep

Sweep every test suite in the repo for tests that carry no preservation value:
TDD artefacts, BCL tautologies, echo tests, duplicate pins, cosmetic
exact-string pins, and orphaned suites that nothing runs. Report findings
tiered by how aggressively they can be removed, ask for instructions, then
carry them out.

## Phase 1: Wiring and timing check

Before reading any test, establish which suites run in automation. Check:

- `.github/workflows/` (deploy.yml, build-and-test.yml, frontend-lint.yml,
  bundle-gates.yml)
- `agentic-dev-loop/pipeline.yaml`
- `package.json` test scripts in `frontend/`, `frontend-editor/`, `scripts/`,
  and `spikes/**`

A suite that no workflow, pipeline step, or npm script invokes is **orphaned**
and belongs in the report's top tier. Use `ls` to find test directories (the
glob tool has given false negatives). Grep each directory name across
`*.yml`, `*.yaml`, and `package.json` files to confirm or rule out wiring.

Then measure durations: run the wired suites with per-test timing
(`pytest --durations`, `dotnet test --logger "console;verbosity=normal"`,
node's test reporter) and record the slowest tests and per-suite sums.

Duration is not preservation value. The slow tests are usually the valuable
ones (real sockets, subprocess fail-fast paths, git operations, retry
sleeps), and deletion candidates are usually fast: the sweep cuts
maintenance burden, not wall-clock. State this honestly in the report: the
timing data is there to show what the sweep will and won't buy, never to
justify deleting a slow test that pins a real contract.

## Phase 2: Read and classify

Read every test file under:

- `src/Yosk.SiteBuilder.Tests/` - all `.cs` files with `[Fact]`/`[Theory]`
- `src/Yosk.SiteBuilder.Owner.Tests/` - compiles only under `#if OWNER_BUILD`
- `frontend/tests/` - node --test, including `tests/property/`
- `frontend-editor/` - vitest, `*.test.js` at the package root
- `scripts/**/*.test.mjs`
- `spikes/**/tests/**`

Classify each candidate:

| Category | Example | Handling |
|----------|---------|----------|
| Orphaned suite | spike tests, scripts tests nothing wires in | Whole-file/directory deletion |
| BCL tautology | base64 round-trip re-asserting Convert | Delete the test; keep any yosk-specific case |
| Echo / pass-through | args decode asserted equal to constructor input | Delete the echo asserts; keep `Assert.Null(error)` |
| Duplicate pin | same contract already pinned by another test, layer, or property test | Delete only the copy that adds nothing; keep layer pins that add status/envelope mapping |
| Cosmetic exact-string pin | log text, CSS formatting whitespace, exact markup tag strings, media strings pinned elsewhere, UI layout: button order, aria labels, element counts, inline styles, CSS-text scans | Trim to the semantic assert (key substring via Contains, ordering via IndexOf, presence/absence); the exact threshold stays wherever it is canonically pinned |
| Dead test | missing `[Fact]`/`test(` so it never runs | Delete |

Never flag:

- Property tests, especially ones pinning adapter == domain equivalence;
  when such a property test exists, the fixed-case duplicates are the
  redundant side and the property test stays
- Wire-format pins consumed by the editor or live-site serving: values.json
  envelopes, scoped ids, `data-breakpoint` wrappers, CSP derivation inputs
- Security pins: zip-slip, antiforgery, rate limits, CORS, cross-window
  envelope validation
- Support files with no tests: `SourceFactory.cs`, `TestAuthHandler.cs`,
  `BundleFixture.cs`, `OwnerTestServiceLocator.cs`, `McpDownloadTestSupport.cs`,
  `dom-setup.ts`

Verify every duplicate-pin and echo claim against the implementation before
reporting it. Read the code path (router dispatch, argument passthrough,
shared helper) and confirm the two tests exercise the same branch with the
same assertions; do not trust the surface resemblance of the tests alone.
Report only verified claims, and state the evidence (e.g. "make_router
branches only on is_sandbox_action, false for both shapes").

## Phase 3: Report

Report only, in chat, tiered:

1. **Wiring summary** - which suites run where, which are orphaned, and
   where the time goes (slowest tests, per-suite sums, and whether the sweep
   is expected to move wall-clock at all).
2. **Wholesale-deletion candidates** - orphaned/superseded suites, strongest
   candidate stated first.
3. **Surgical** - numbered table of whole-test deletions (test, file:line,
   why it is redundant).
4. **Trims** - numbered table of assertion-level trims inside keep-worthy
   tests, stating what replaces each pin.
5. **Keep summary** - per-suite verdicts (KEEP / MOSTLY KEEP) and what was
   kept and why.

Then **ask for instructions**. Do not edit anything before the user
instructs. The user may reply per section or per number:

- `NUKE` - apply that section, or the whole report
- `Delete #1, #3` / `Trim #4` / `Keep #4, delete the rest`
- `Show me #N` - show the full source of test #N before deciding

## Phase 4: Carry out

- **Whole-test deletion**: remove the method with its attribute. If the file
  is left with only usings and an empty class, delete the file. Before
  deleting a duplicate, re-verify against the implementation: read the
  dispatch or branching code and confirm both tests take the same path with
  the same asserts.
- **Trim**: replace the exact-string assert with its semantic half. When
  relaxing a count assert, isolate the fixture so only the contract under
  test varies (e.g. a diverged body appends visibility rules and changes the
  count).
- **Trim audit trail**: for every trim, record which surviving test absorbs
  each lost edge case (a comment in the surviving test or the commit body).
  Fold lost edge cases into the surviving property test where possible (e.g.
  extend the property alphabet) so no contract disappears silently.
- Remove now-unused helpers and usings in the same edit; keep usings still
  referenced by surviving tests.

## Phase 5: Verify

- `dotnet test src/Yosk.SiteBuilder.sln -c Release --filter "Category!=Database"`
  (Database-trait tests need live Postgres; CI filters them out)
- Owner tests only if owner files changed:
  `dotnet test src/Yosk.SiteBuilder.Owner.Tests/Yosk.SiteBuilder.Owner.Tests.csproj -c Release --filter "Category!=Database"`
- `cd frontend && npm test` if frontend files changed
- `cd frontend-editor && npm test` if editor files changed (on a Mac, run
  `npm ci` first if vitest fails on a missing darwin rollup: the pipeline
  warms this node_modules with linux builds, and last `npm ci` wins)
- `python3 tools/check_no_em_dash.py` - every edit must be ASCII; the em dash
  is banned repo-wide and the pre-commit hook enforces it

Report suite results. Do not commit unless the user asks.
