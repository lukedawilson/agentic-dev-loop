// probe-echarts-builds.js - which series/components are registered in each ECharts 6.1.0 dist variant.
// Probe method: SSR-init a chart, setOption with the series type, then ask the model whether a
// series/component model was actually created. Unknown types log a console warning and create nothing.
"use strict";
const path = require("path");
const VARIANTS = [
  ["simple", "vendor/echarts/echarts.simple.min.js"],
  ["common", "vendor/echarts/echarts.common.min.js"],
  ["full",   "vendor/echarts/echarts.min.js"],
];
const SERIES = ["line","bar","pie","custom","themeRiver","scatter","gauge","heatmap",
  "treemap","graph","funnel","radar","sunburst","boxplot","candlestick","pictorialBar",
  "effectScatter","sankey","parallel","tree","lines"];
const COMPONENTS = ["tooltip","legend","dataZoom","visualMap","aria","timeline","graphic","brush"];

function probe(variantName, file) {
  const echarts = require(path.resolve(__dirname, file));
  const results = { variant: variantName, series: {}, components: {} };
  const warns = [];
  const origWarn = console.warn;
  console.warn = (m) => warns.push(String(m));
  for (const t of SERIES) {
    const c = echarts.init(null, null, { renderer: "svg", ssr: true, width: 400, height: 300 });
    c.setOption({ series: [{ type: t, data: [] }] });
    results.series[t] = c.getModel().getSeriesCount() > 0;
    c.dispose();
  }
  for (const name of COMPONENTS) {
    const c = echarts.init(null, null, { renderer: "svg", ssr: true, width: 400, height: 300 });
    const opt = {};
    if (name === "markArea" || name === "markLine") {
      opt.series = [{ type: "line", data: [], [name]: { data: [] } }];
    } else {
      opt[name] = name === "dataZoom" ? [] : {};
    }
    c.setOption(opt);
    const model = c.getModel().getComponent(name);
    results.components[name] = !!model;
    c.dispose();
  }
  console.warn = origWarn;
  results.warnings = warns.filter(w => /not exist|not exists|unknown/i.test(w)).slice(0, 40);
  return results;
}

const out = {};
for (const [name, file] of VARIANTS) out[name] = probe(name, file);
console.log(JSON.stringify(out, null, 1));
