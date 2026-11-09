# unit-13 charting spike verdict

Date: 2026-11-09
User verdict (verbatim): "Ok, ECharts is good enough now. Record the
verdict in the spike docs"

SELECTED: Apache ECharts 6.1.0, common bundle, SVG renderer.
Pinned artifact: vendor/echarts/echarts.common.min.js
  bytes 715,020
  SHA256 61f13280aba3161f77fc801bf5f9c786f55f10432153a0683fc5cc0700b013d5
  license Apache-2.0

Runner-up: Chart.js 4.6.1 (vendor/chart.js/chart.umd.min.js, 210,082 B,
MIT). Fallback: hand-roll.

Key spike facts this verdict rests on:
- ECharts common renders SVG; screen readers get real chart data
  (AX tree 309 nodes, data-bearing StaticText). Chart.js canvas
  exposes nothing (5 unnamed Canvas roles).
- echarts.simple lacks the SVG renderer and silently falls back to
  canvas; ruled out.
- The custom series is absent from simple/common bundles; verdict
  strips use thick-line segments (lineWidth 14) instead. Validated.
- aria:{enabled:true} is a no-op for SVG in 6.1.0. Validated.
- Grid children need min-width: 0 or charts freeze on shrink
  (Chart.js RO never fires; ECharts has no resize observation and
  needs an explicit window resize handler calling inst.resize()).
  Both validated in this spike.

Vendored files (jsDelivr, downloaded 2026-11-09):
  vendor/echarts/echarts.min.js        1,121,883  (full)
  vendor/echarts/echarts.common.min.js   715,020  (SELECTED)
  vendor/echarts/echarts.simple.min.js   500,315  (ruled out)
  vendor/chart.js/chart.umd.min.js       210,082  (runner-up)

Full record: planning-docs/unit-13-charting-selection.md (repo).
