/*
 * fixtures.js - RunDetailPayload-shaped fixtures for the unit-13 chart spike.
 *
 * Every field descends from real shapes (unit-12 reporting/sources.py and the
 * unit-13 domain entities doc). Verdict vocabulary is pass/fail/unverifiable/
 * env (engine/run_status.py step_verdicts). Provenance is mechanical/parsed/
 * claimed. Metrics are MetricPayload-shaped (name, value, provenance, basis,
 * availability with reason). No field is invented for the spike.
 */
(function (root) {
  "use strict";

  function v(step, verdict, provenance, ts) {
    return { step: step, verdict: verdict, provenance: provenance, ts: ts };
  }

  var detail = {
    run_id: "run-2026-11-08T14-22-10",
    notice: null,
    threads: [
      {
        thread_id: "tdd-3f2a",
        subject: "TDD loop: red-green-refactor on the failing test",
        ts_start: "2026-11-08T14:22:10Z",
        ts_end: "2026-11-08T14:31:44Z",
        status: "success",
        attempts: [
          {
            number: 1,
            verdicts: [
              v("brief", "pass", "mechanical", "2026-11-08T14:22:10Z"),
              v("deps", "pass", "mechanical", "2026-11-08T14:23:02Z"),
              v("dev", "pass", "mechanical", "2026-11-08T14:24:11Z"),
              v("test", "fail", "mechanical", "2026-11-08T14:26:40Z")
            ],
            retry_target: "dev",
            terminal_step: "test"
          },
          {
            number: 2,
            verdicts: [
              v("dev", "pass", "mechanical", "2026-11-08T14:27:31Z"),
              v("test", "pass", "mechanical", "2026-11-08T14:29:05Z"),
              v("review", "fail", "claimed", "2026-11-08T14:30:02Z")
            ],
            retry_target: "dev",
            terminal_step: "review"
          },
          {
            number: 3,
            verdicts: [
              v("dev", "pass", "mechanical", "2026-11-08T14:30:41Z"),
              v("test", "pass", "mechanical", "2026-11-08T14:31:20Z"),
              v("review", "pass", "claimed", "2026-11-08T14:31:38Z"),
              v("qa", "pass", "mechanical", "2026-11-08T14:31:44Z")
            ],
            retry_target: null,
            terminal_step: "qa"
          }
        ],
        metrics: {
          tdd: {
            name: "tdd", value: 2, provenance: "mechanical",
            basis: "test-command rows in the audit log",
            availability: { available: true, reason: null }
          },
          loop_health: {
            name: "loop_health", value: 0.67, provenance: "mechanical",
            basis: "pass rate across attempt verdicts",
            availability: { available: true, reason: null }
          },
          review: {
            name: "review", value: 1, provenance: "parsed",
            basis: "review step verdicts",
            availability: { available: true, reason: null }
          }
        }
      },
      {
        thread_id: "verify-9c1d",
        subject: "Verification run with unverifiable steps",
        ts_start: "2026-11-08T14:32:10Z",
        ts_end: "2026-11-08T14:33:02Z",
        status: "failed",
        attempts: [
          {
            number: 1,
            verdicts: [
              v("brief", "pass", "mechanical", "2026-11-08T14:32:10Z"),
              v("deps", "unverifiable", "claimed", "2026-11-08T14:32:31Z"),
              v("dev", "env", "mechanical", "2026-11-08T14:33:02Z")
            ],
            retry_target: null,
            terminal_step: "dev"
          }
        ],
        metrics: {}
      }
    ],
    artifacts: {
      brief: { present: true, key_facts: ["subject: TDD loop"] },
      summary: { present: true, key_facts: ["final: success after 3 attempts"] },
      findings: { present: false, key_facts: [] },
      qa: { present: false, key_facts: [] }
    }
  };

  /* Aggregate across runs (RunsListPayload-shaped records) for the trend
     chart. attempt_count/verdict_summary are RunRecordSummary fields. */
  var trend = {
    filters: { since: null, until: null, statuses: [] },
    notice: null,
    runs: [
      { thread_id: "a-1", run_dir: "runs/001", subject: "s1",
        ts_start: "2026-11-01T09:00:00Z", ts_end: "2026-11-01T09:12:00Z",
        status: "success", attempt_count: 4,
        verdict_summary: { pass: 18, fail: 6, unverifiable: 0, env: 0 } },
      { thread_id: "a-2", run_dir: "runs/002", subject: "s2",
        ts_start: "2026-11-02T09:00:00Z", ts_end: "2026-11-02T09:08:00Z",
        status: "success", attempt_count: 2,
        verdict_summary: { pass: 16, fail: 2, unverifiable: 0, env: 0 } },
      { thread_id: "a-3", run_dir: "runs/003", subject: "s3",
        ts_start: "2026-11-03T09:00:00Z", ts_end: "2026-11-03T09:19:00Z",
        status: "failed", attempt_count: 5,
        verdict_summary: { pass: 20, fail: 12, unverifiable: 3, env: 1 } },
      { thread_id: "a-4", run_dir: "runs/004", subject: "s4",
        ts_start: "2026-11-04T09:00:00Z", ts_end: "2026-11-04T09:10:00Z",
        status: "success", attempt_count: 1,
        verdict_summary: { pass: 14, fail: 0, unverifiable: 0, env: 0 } },
      { thread_id: "a-5", run_dir: "runs/005", subject: "s5",
        ts_start: "2026-11-05T09:00:00Z", ts_end: "2026-11-05T09:15:00Z",
        status: "interrupted", attempt_count: 3,
        verdict_summary: { pass: 10, fail: 5, unverifiable: 2, env: 0 } },
      { thread_id: "a-6", run_dir: "runs/006", subject: "s6",
        ts_start: "2026-11-06T09:00:00Z", ts_end: "2026-11-06T09:07:00Z",
        status: "success", attempt_count: 2,
        verdict_summary: { pass: 15, fail: 1, unverifiable: 0, env: 0 } }
    ]
  };

  var out = { detail: detail, trend: trend };
  root.ADL_FIXTURES = out;
  if (typeof module !== "undefined" && module.exports) { module.exports = out; }
})(typeof window !== "undefined" ? window : this);
