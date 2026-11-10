"""Key-stat bands, drill-down expanders and renderer dispatch in runs.js.

The runs page promotes a small set of metrics per category into a charted
key-stat band and hides the long tail behind a native details expander. The
dashboard also gains a recursive metric-value formatter, so a nested dict
renders one level deeper instead of collapsing to [object Object].

The prod asset is an IIFE, so the test boots it in a node vm with minimal
DOM stubs and exposes the pure renderers through the same hook pattern the
other runs.js tests use.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNS_JS = ROOT / "src" / "agentic_dev_loop" / "viz" / "assets" / "runs.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node required to exercise runs.js"
)

DRIVER = r"""
"use strict";
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[2], "utf8");
const hooked = source.replace(
  /}\)\(\);\s*$/,
  "window.__hooks = { gaugeOption: gaugeOption, "
  + "verdictMixOption: verdictMixOption, "
  + "attemptBarsOption: attemptBarsOption, "
  + "keyStatsBand: keyStatsBand, bigNumberStat: bigNumberStat, "
  + "expanderHTML: expanderHTML, populateExpander: populateExpander, "
  + "formatMetricValue: formatMetricValue, KEY_STATS: KEY_STATS, "
  + "THREAD_KEY_STATS: THREAD_KEY_STATS };\n})();"
);

const sandbox = {
  window: { matchMedia: () => undefined, localStorage: undefined },
  document: {
    readyState: "loading",
    addEventListener: () => {},
    getElementById: () => null,
    documentElement: { setAttribute: () => {} }
  },
  getComputedStyle: () => ({ getPropertyValue: () => "" })
};
vm.runInNewContext(hooked, sandbox);
const h = sandbox.window.__hooks;

function metric(value, basis) {
  return {
    value: value, availability: "available",
    basis: basis || "qa verdicts n=8", provenance: "mechanical"
  };
}

const metrics = {
  verdict_distribution: metric({
    dev: { pass: 3, fail: 1 }, review: { pass: 2 }
  }, "all step verdicts"),
  attempt_count_distribution: metric({ "1": 5, "2": 3 }, "threads n=8"),
  thread_count: metric(8, "filtered run records")
};

const band = h.keyStatsBand("loop-health", metrics, h.KEY_STATS.loop_health);
const gaugeBand = h.keyStatsBand("tdd", {
  pct_attempts_red_then_green: metric(62.5)
}, h.KEY_STATS.tdd);
const gauge = h.gaugeOption(metric(62.5));
const gaugeUnavailable = h.gaugeOption({
  availability: "unavailable: no attempts",
  basis: "test verdict sequences n=0"
});
const mix = h.verdictMixOption(metrics.verdict_distribution);
const bars = h.attemptBarsOption(metrics.attempt_count_distribution);

const body = { innerHTML: "" };
const expander = h.expanderHTML("demo", ["a", "b"],
  '<div class="metric-grid">GRID</div>');
const node = { id: "details-demo", open: false, querySelector: () => body };
h.populateExpander(node);
const bodyBeforeOpen = body.innerHTML;
node.open = true;
h.populateExpander(node);
const bodyAfterOpen = body.innerHTML;

process.stdout.write(JSON.stringify({
  gauge: gauge,
  gaugeUnavailable: gaugeUnavailable,
  mix: mix,
  bars: bars,
  bandHtml: band.html,
  gaugeBandHtml: gaugeBand.html,
  bandMounts: band.mounts.map((mount) => mount.id),
  expanderHtml: expander,
  bodyBeforeOpen: bodyBeforeOpen,
  bodyAfterOpen: bodyAfterOpen,
  recursion: h.formatMetricValue({
    test: { pass: 3, fail: 1 }, review: { pass: 2 }
  }),
  recursionThird: h.formatMetricValue({ a: { b: { c: 1 } } }),
  bigOk: h.bigNumberStat("thread_count", metric(8)),
  bigEmpty: h.bigNumberStat("thread_count", {
    availability: "unavailable: none", basis: "no threads"
  }),
  threadTable: Object.keys(h.THREAD_KEY_STATS)
}));
"""


@pytest.fixture
def stats(tmp_path):
    driver = tmp_path / "driver.js"
    driver.write_text(DRIVER, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(driver), str(RUNS_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"node driver failed: {proc.stderr}"
    return json.loads(proc.stdout)


def test_gauge_option_derives_from_rate_metric(stats):
    series = stats["gauge"]["series"][0]
    assert series["type"] == "gauge"
    assert series["min"] == 0
    assert series["max"] == 100
    assert series["startAngle"] == 180
    assert series["endAngle"] == 0
    assert series["data"][0]["value"] == 62.5
    assert series["pointer"]["show"] is True
    assert "%" in series["detail"]["formatter"]


def test_unavailable_gauge_has_no_option(stats):
    assert stats["gaugeUnavailable"] is None


def test_verdict_mix_is_stacked_horizontal_bars(stats):
    mix = stats["mix"]
    assert mix["xAxis"]["type"] == "value"
    assert mix["yAxis"]["type"] == "category"
    assert mix["yAxis"]["data"] == ["dev", "review"]
    assert mix["legend"]["show"] is True
    names = [series["name"] for series in mix["series"]]
    assert names == ["Pass", "Fail"]
    by_name = {series["name"]: series for series in mix["series"]}
    assert by_name["Pass"]["stack"] == by_name["Fail"]["stack"]
    assert by_name["Pass"]["data"] == [3, 2]
    assert by_name["Fail"]["data"] == [1, 0]


def test_attempt_bars_use_integer_axis_and_info_colour(stats):
    bars = stats["bars"]
    assert bars["xAxis"]["data"] == ["1", "2"]
    assert bars["yAxis"]["minInterval"] == 1
    series = bars["series"][0]
    assert series["type"] == "bar"
    assert series["data"] == [5, 3]
    assert series["itemStyle"]["color"] == "#08BDBA"


def test_key_stats_band_promotes_the_declared_metrics(stats):
    html = stats["bandHtml"]
    assert 'class="stat-band"' in html
    assert 'data-metric="verdict_distribution"' in html
    assert 'data-metric="attempt_count_distribution"' in html
    assert 'data-metric="thread_count"' in html
    assert "stat-mix" in html
    assert "stat-bars" in html
    assert "stat-big" in html
    assert "stat-gauge" in stats["gaugeBandHtml"]
    assert "--" not in stats["gaugeBandHtml"]
    assert len(stats["bandMounts"]) == 2


def test_expander_body_renders_lazily_on_first_open(stats):
    html = stats["expanderHtml"]
    assert '<details class="metric-expander"' in html
    assert "Show more..." in html
    assert "Show less" in html
    assert "(2 metrics)" in html
    assert "metric-chevron" not in html
    assert 'class="metric-detail-body"' in html
    assert stats["bodyBeforeOpen"] == ""
    assert "GRID" in stats["bodyAfterOpen"]


def test_format_metric_value_recurses_two_levels(stats):
    text = stats["recursion"]
    assert "Review (Pass: 2)" in text
    assert "Test (Fail: 1, Pass: 3)" in text


def test_format_metric_value_bails_below_third_level(stats):
    text = stats["recursionThird"]
    assert "B:" in text
    assert "C: 1" not in text


def test_big_number_available_and_unavailable(stats):
    assert "8" in stats["bigOk"]
    assert "stat-value" in stats["bigOk"]
    assert "--" in stats["bigEmpty"]
    assert "no threads" in stats["bigEmpty"]


def test_thread_key_stats_table(stats):
    assert stats["threadTable"] == [
        "attempt_count", "pct_attempts_red_then_green", "qa_pass_rate"
    ]
