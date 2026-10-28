# Run-time analysis: containerised loop runs

- Date: 2026-10-28
- Run analysed: 5367f0c488d8 (a consumer repo, unit 10)
- State at analysis time: qa step, second attempt; run elapsed ~14h
- Later observation: 08:18:49 qa agent hit its 60-iteration cap with no
  verdict on pipeline attempt 8; engine retrying

## Verdict

The run is slow because the pipeline retried the full chain 8 times, not
because of containers. Containerisation and the smaller sandbox hardware
together account for roughly 10 percent of wall time. LLM agent steps
(review, dev, qa) account for the other 90 percent.

## Where the time went

Decoded from checkpoint UUID v1 timestamps in the run's
loop-checkpoints.sqlite (unix = (u.time - 0x01b21dd213814000) / 1e7;
main thread e2dafd3d44164c97ad40369a86a0767d, 52 checkpoints).

- Run input 17:58:02 (Oct 27); review approved 06:42:38 (Oct 28).
  ~764 min elapsed to review approval; qa attempt 2 still running at 70+
  min when analysed.
- 8 retry cycles. Review failed 6 times before approving (16.7m, 103.2m,
  50.9m, 86.5m, 54.7m, 95.2m). Qa failed once after 85.3m. Dev attempt 1
  took 138.9m; a later dev pass took 38.4m.
- Time budget over ~764 min:

| Component     | Time     | Share  |
|---------------|----------|--------|
| review agent  | ~413 min | ~54%   |
| dev agent     | ~185 min | ~24%   |
| qa agent      | ~155+ min| ~20%   |
| command steps | ~90 min  | ~12%   |

- Pipeline shape (consumer pipeline.yaml, name "sitebuilder-construction"):
  dev -> restore -> test -> editor-tests -> conformance -> review -> qa ->
  commit -> success. Every step carries `on_fail: { goto: dev, retry:
  true }` with max_attempts 10, so any failed gate re-runs the whole chain.

## Containerisation contribution (measured)

- docker exec spawn: ~57 ms per call (10 execs in 0.57s). Negligible.
- Command steps per cycle: restore ~1.5m, dotnet test ~6-7m, editor-tests
  (npm ci + npm test) ~1.5m, conformance 0-2m. The container tax on these
  is maybe 2-4 min per cycle, ~30 min over the run.
- frontend/node_modules (521M) is neither cache-mounted nor sync-excluded,
  so it rides in every export copy and sync-back scan. Minutes, not hours.
- Export dir ~3.1G. NuGet (123M) and npm (217M) caches live in the sandbox
  /tmp per container and are ephemeral, so the first restore per run
  re-fetches.
- macOS Docker runs a Linux VM; bind mounts go through VirtioFS, so
  file-heavy workloads (dotnet, npm, Chromium) pay an I/O tax on top of the
  CPU cap.

## Hardware contribution

- Host: 14 cores, 24 GB RAM.
- Exec containers run with 2 CPUs / 4 GB (engine defaults in
  src/agentic_dev_loop/engine/isolation.py: DEFAULT_EXEC_CPUS=2.0,
  DEFAULT_EXEC_MEMORY="4g"). Provision containers get 4 CPUs / 8 GB. The
  consumer pipeline.yaml does not override these, so defaults apply.
- The caps bind on command steps: dotnet test parallelises (14 -> 2 cores
  explains 6-7 min vs ~2-3 min bare metal); Chromium plus a booted site is
  snug in 4 GB (qa container idles at ~2 GB used).
- They do not bind on agent steps: docker stats during a qa think phase
  showed every container at 0-0.06 percent CPU. Agent steps are LLM round
  trip bound, not local compute bound.
- Net hardware gap: ~30-60 min of a 12.7h run.

## Confounders: containerisation arrived with other changes

- The migration commit that added Docker also added the pipeline restructure
  and the LLM block (openai/deepseek-v4-pro) in one commit, dated 2026-10-20.
- The qa gate landed 2026-10-24 (engine plan 024). On 2026-10-27, the same
  day the analysed run started, the engine committed "fix(qa): probe
  coverage gaps instead of re-running tests", so qa cost was a known issue
  within days of the gate existing.
- "Slower since containerisation" therefore bundles: Docker, new engine,
  new LLM config, and new gates (qa especially).

## Evidence that retry count drives duration

- The checkpoint DB retains 21 threads from prior runs (Oct 20-27). The
  first containerised run (Oct 20-21) took 755 min, nearly identical to
  this run's 764. A green Oct 26 run took 4.5 min (31 checkpoints). Other
  runs range 20-227 min. Duration tracks retry count, not Docker.
- A sibling run the same day (the engine repo's own run, plan 030)
  completed dev -> review -> qa in ~12 min with everything green.
- Conclusion: duration tracks gate strictness and retry count, not
  containerisation.

## Smoking gun: the qa livelock

The retry inflation has two phases, and only one of them is fixable by
code.

- Phase 1 (17:58 -> 04:26, 6 of the 8 bumps): review rejections. Review
  failed 6 times before approving.
- Phase 2 (04:26 -> onward): qa. qa is a new gate (landed 2026-10-24). On
  the 8th pipeline attempt its agent burned all 60 iterations (~96 min)
  and died without emitting a verdict. The engine treats no-verdict as
  failure: bump to dev, full chain re-run, next attempt. Livelock.

Why qa cannot converge (from its own attempt-8 transcript):

- The reference fixture's served page hotlinks ~12 external images that
  hang in the sandbox. The probe waits for the load event with a hardcoded
  30s timeout; hanging images mean load never fires, so the browser tier
  times out. The page itself renders (body visible, text present).
- The agent then burns iterations on tooling friction (ESM module
  resolution, a missing zip binary) while its transcript grows to tens of
  millions of input tokens per iteration.
- It was building a minimal synthetic fixture (the right workaround) when
  it hit the iteration cap.

Why this inflates retries where the old policy did not:

- Every gate before qa (tests, editor tests, review) failed only for
  code-addressable reasons. on_fail goto dev used to terminate because
  dev could fix the failure.
- qa is the first gate that can fail for reasons code cannot fix (sandbox
  network, fixture hotlinks, probe timeouts). Routing those failures to
  dev creates a livelock: dev concludes no code changes are needed,
  review re-approves unchanged code, qa fails again. ~2.5-3h per cycle,
  up to max_attempts 10.

So the retry bump = a new, unpolished gate plus an environmental landmine
in its browser tier plus a failure-routing policy that assumes failures
are code-addressable. qa broke that assumption.

### Routing decision (owner, 2026-10-28)

No-verdict-at-cap routing to dev is correct, conservative behaviour: a
run must not commit on an unclear verdict, and retrying through the
chain beats failing open. The fix is therefore the pipeline itself (the
ENV verdict, the prompt's anti-livelock rule, and probe robustness),
not routing around the conservative default.

### Engine confirmation (source level)

- stages.py DEFAULT_MAX_ITERATIONS = 500; the consumer pins qa to 60.
  Either way the budget is deliberation-sized, not probe-sized.
- The verdict vocabulary is pass/fail/unverifiable only (plan 024 added
  unverifiable). There is no ENV verdict, so a broken harness has no
  expressible outcome: the qa agent's correct finding (page renders,
  probe cannot pass) could not be stated.
- stages.py _unclear_target: an agent with no final response retries at
  on_fail's goto target, i.e. dev. Confirms the livelock route: harness
  failure -> dev.
- Probe-cheap, deliberation-expensive: attempt 2 spent 96 min on 60
  iterations (tens of millions of input tokens each, 97.9% cache hit),
  while the actual probes are seconds-to-minutes of deterministic work.

## Loop health note

The analysed failure mode was healthy. The unit gate (editor tests) caught
a real defect in the change under review: two new tests queried the shadow
DOM before the first Lit render and crashed on a null click target. The
feature code itself was correct; only the tests were wrong (missing an
await, matching the pattern every other shadow-DOM test in that file
uses). The loop then spent 6 review passes converging on 5 findings, and
the review verdict converged to APPROVED.
