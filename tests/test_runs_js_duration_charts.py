"""Duration and retry-target charts for the Loop health key-stat band.

Three dict-valued loop_health metrics promote from text cards to charts:

- ``audit_step_durations``: mean audit duration_seconds per step, rendered
  as horizontal bars (new ``durationBarsOption`` renderer) with a tooltip
  that names the mean and the n from ``detail.counts``;
- ``per_step_durations``: mean checkpoint delta seconds per step, same
  renderer;
- ``retry_target_frequency``: count of threads per retry-target step name,
  rendered with the existing ``attemptBarsOption`` (a category-frequency
  vertical bar chart, the same shape the metric already has).

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
  "window.__hooks = { durationBarsOption: durationBarsOption, "
  + "attemptBarsOption: attemptBarsOption, "
  + "keyStatsBand: keyStatsBand, KEY_STATS: KEY_STATS };\n})();"
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

function metric(value, detail) {
  return {
    value: value, availability: "available",
    basis: "mean ts delta seconds per step", provenance: "mechanical",
    detail: detail || undefined
  };
}

const durations = h.durationBarsOption(metric(
  { dev: 3.25, test: 12.5 }, { counts: { dev: 2, test: 3 } }));
const durationsNoCounts = h.durationBarsOption(metric({ qa: 1 }));

const emptyDict = h.durationBarsOption(metric({}));
const scalarValue = h.durationBarsOption(metric(42));
const arrayValue = h.durationBarsOption(metric([1, 2]));
const unavailable = h.durationBarsOption({
  availability: "no timed step checkpoints",
  basis: "checkpoint ts deltas"
});

const bandMetrics = {
  verdict_distribution: metric({ dev: { pass: 3 } }),
  attempt_count_distribution: metric({ "1": 5 }),
  retry_target_frequency: metric({ brief: 2, test: 1 }),
  audit_step_durations: metric({ dev: 4.5 }, { counts: { dev: 1 } }),
  per_step_durations: metric({ test: 2 }, { counts: { test: 1 } }),
  thread_count: metric(8)
};
const band = h.keyStatsBand("loop-health", bandMetrics, h.KEY_STATS.loop_health);

process.stdout.write(JSON.stringify({
  durations: {
    xType: durations.xAxis.type,
    yType: durations.yAxis.type,
    yData: durations.yAxis.data,
    seriesType: durations.series[0].type,
    seriesData: durations.series[0].data,
    color: durations.series[0].itemStyle.color,
    tooltipWithCount: durations.tooltip.formatter({ name: "dev", value: 3.25 }),
    tooltipNoCount: durationsNoCounts.tooltip.formatter({ name: "qa", value: 1 })
  },
  emptyDict: emptyDict,
  scalarValue: scalarValue,
  arrayValue: arrayValue,
  unavailable: unavailable,
  bandHtml: band.html,
  bandMounts: band.mounts.map((mount) => mount.id),
  loopTable: h.KEY_STATS.loop_health
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


def test_duration_bars_are_horizontal_with_step_categories(stats):
    chart = stats["durations"]
    assert chart["xType"] == "value"
    assert chart["yType"] == "category"
    assert chart["yData"] == ["dev", "test"]
    assert chart["seriesType"] == "bar"
    assert chart["seriesData"] == [3.25, 12.5]


def test_duration_bars_use_info_colour(stats):
    assert stats["durations"]["color"] == "#08BDBA"


def test_duration_tooltip_names_mean_and_count(stats):
    assert stats["durations"]["tooltipWithCount"] == "dev: 3.25s (n=2)"
    assert stats["durations"]["tooltipNoCount"] == "qa: 1s"


def test_duration_bars_reject_non_chart_values(stats):
    assert stats["emptyDict"] is None
    assert stats["scalarValue"] is None
    assert stats["arrayValue"] is None
    assert stats["unavailable"] is None


def test_loop_health_table_promotes_the_three_charts(stats):
    assert stats["loopTable"] == {
        "verdict_distribution": "verdictMix",
        "attempt_count_distribution": "attemptBars",
        "retry_target_frequency": "attemptBars",
        "audit_step_durations": "durationBars",
        "per_step_durations": "durationBars",
        "thread_count": "bigNumber",
    }


def test_band_renders_the_three_charts_as_mounts(stats):
    html = stats["bandHtml"]
    assert 'data-metric="audit_step_durations"' in html
    assert 'data-metric="per_step_durations"' in html
    assert 'data-metric="retry_target_frequency"' in html
    mounts = stats["bandMounts"]
    assert "stat-loop-health-audit-step-durations" in mounts
    assert "stat-loop-health-per-step-durations" in mounts
    assert "stat-loop-health-retry-target-frequency" in mounts
    # Five charted stats: verdict mix, attempts, retry targets and the two
    # duration charts. thread_count is a big number and mounts nothing.
    assert len(mounts) == 5


def test_duration_charts_share_the_mix_slot(stats):
    html = stats["bandHtml"]
    assert html.count("stat-mix") == 3  # verdict mix + two duration charts
    assert html.count("stat-bars") == 2  # attempts + retry targets
    assert html.count("stat-big") == 1  # thread count
