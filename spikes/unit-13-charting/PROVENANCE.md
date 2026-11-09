# Unit-13 charting spike artifacts

Copied from the opencode temp dir on 2026-11-09 after the user's Phase 4
gate chose ECharts (see planning-docs/unit-13-charting-selection.md, Phase 5
and the final verdict). The selection doc is the authoritative record; this
directory preserves the raw evidence the spike produced.

## Contents

- VERDICT.md - the spike's recorded verdict (ECharts 6.1.0 common bundle)
- spike-echarts.html, spike-chartjs.html, test-strips.html - throwaway
  spike pages used by the headless-Chrome probes
- cdp-probe.js, fixtures.js, measure.js, measure2.js,
  probe-echarts-builds.js - the probe harness and fixtures
- tokens.css - theme-token bridge used by the spike pages
- results/ - probe outputs: JSON facts (AX trees, timing, pixel sampling)
  and PNG screenshots (dark/light) for human review
- vendor/ - the pinned library builds probed in Phase 5

## Vendor files

- vendor/echarts/echarts.common.min.js - Apache ECharts 6.1.0 common
  bundle (the unit-13 selection; Apache-2.0). Upstream SHA256:
  61f13280aba3161f77fc801bf5f9c786f55f10432153a0683fc5cc0700b013d5
  (verified on the file before sanitization). Sanitized for the repo
  ASCII rule: 933 non-ASCII bytes (Chinese/Japanese aria-label locale
  strings) replaced with '?' - syntax re-verified with `node --check`.
  Sanitized-file SHA256:
  c86479a36f95b65d8135fc48dffbe1461a76b34e257d21b167eb5561e764b054
- vendor/echarts/echarts.simple.min.js - ECharts 6.1.0 simple bundle
  (rejected in the spike: no SVG renderer, a11y collapse). Kept for
  evidence. Same 933-byte sanitization, `node --check` clean.
- vendor/chart.js/chart.umd.min.js - Chart.js 4.6.1 (runner-up, kept for
  evidence). ASCII as shipped; no sanitization needed.

## Excluded from the copy

- .chrome-profile-* and .cp-* dirs (throwaway headless-Chrome profiles)
- err.txt (probe stderr log, 413 KB of noise)

The unit-13 build (tools/build_viz_page.py) inlines the SANITIZED common
bundle into the generated page; it must never fetch from the network.
