/*
 * cdp-probe.js - zero-dependency CDP harness for the unit-13 chart spike.
 *
 * Usage: node cdp-probe.js --page spike-chartjs.html [--variant simple] [--out results]
 *
 * Launches headless Chrome, loads the spike page from the local file system,
 * and records: runtime report (window.__adl.report()), console errors,
 * accessibility tree (Accessibility.getFullAXTree), dark/light screenshots,
 * and a visibilitychange re-render check. All output is JSON + PNG files.
 */
"use strict";
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const SPIKE = __dirname;
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

function arg(name, dflt) {
  const i = process.argv.indexOf(name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
}
const pageFile = arg("--page", "spike-chartjs.html");
const variant = arg("--variant", null);
const outDir = path.join(SPIKE, arg("--out", "results"));

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.events = { log: [], exception: [], console: [], page: [] };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id !== undefined) {
        const p = this.pending.get(msg.id);
        if (p) {
          this.pending.delete(msg.id);
          if (msg.error) { p.reject(new Error(msg.error.message)); }
          else { p.resolve(msg.result); }
        }
        return;
      }
      if (msg.method === "Log.entryAdded") { this.events.log.push(msg.params.entry); }
      if (msg.method === "Runtime.exceptionThrown") {
        this.events.exception.push(msg.params.exceptionDetails);
      }
      if (msg.method === "Runtime.consoleAPICalled") {
        this.events.console.push(msg.params);
      }
      if (msg.method === "Page.loadEventFired") { this.events.page.push(msg.params); }
    };
  }
  send(method, params = {}, sessionId) {
    const id = ++this.id;
    const msg = { id, method, params };
    if (sessionId) { msg.sessionId = sessionId; }
    this.ws.send(JSON.stringify(msg));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
  }
  close() { try { this.ws.close(); } catch (e) {} }
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function poll(fn, timeoutMs, stepMs) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const v = await fn();
    if (v) { return v; }
    await sleep(stepMs);
  }
  return null;
}

async function launchChrome() {
  const profile = fs.mkdtempSync(path.join(SPIKE, ".chrome-profile-"));
  const chrome = spawn(CHROME, [
    "--headless=new",
    "--remote-debugging-port=0",
    "--user-data-dir=" + profile,
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-gpu",
    "--hide-scrollbars",
    "--force-device-scale-factor=2",
    "about:blank"
  ], { stdio: "ignore" });
  // wait for DevToolsActivePort
  const portFile = path.join(profile, "DevToolsActivePort");
  const t0 = Date.now();
  while (!fs.existsSync(portFile)) {
    if (Date.now() - t0 > 15000) { throw new Error("chrome did not start"); }
    await sleep(100);
  }
  const [port, browserPath] = fs.readFileSync(portFile, "utf8").trim().split("\n");
  return { chrome, profile, port: Number(port), browserPath };
}

async function main() {
  fs.mkdirSync(outDir, { recursive: true });
  const { chrome, profile, port, browserPath } = await launchChrome();

  // browser-level connection
  const version = await (await fetch("http://127.0.0.1:" + port + "/json/version")).json();
  const browserWs = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise((r, j) => { browserWs.onopen = r; browserWs.onerror = j; });
  const cdp = new CDP(browserWs);

  // create + attach target
  const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await cdp.send("Target.attachToTarget",
    { targetId, flatten: true });
  const S = (m, p = {}) => cdp.send(m, p, sessionId);

  await S("Page.enable");
  await S("Runtime.enable");
  await S("Log.enable");
  await S("Accessibility.enable");
  await S("Emulation.setDeviceMetricsOverride",
    { width: 1280, height: 1400, deviceScaleFactor: 2, mobile: false });

  const url = "file://" + path.join(SPIKE, pageFile)
    + (variant ? "?variant=" + variant : "");
  await S("Page.navigate", { url });
  await poll(async () => {
    try {
      const r = await S("Runtime.evaluate", {
        expression: "!!(window.__adl && window.__adl.report)",
        returnByValue: true
      });
      return r.result.value === true;
    } catch (e) { return false; }
  }, 15000, 200);

  const evaluate = async (expression) => {
    const r = await S("Runtime.evaluate", { expression, returnByValue: true });
    if (r.exceptionDetails) {
      return { error: r.exceptionDetails.text };
    }
    return r.result.value;
  };

  const report1 = await evaluate("window.__adl.report()");
  const renderCount1 = await evaluate("window.__adl.renderCount");
  const theme1 = await evaluate("window.__adl.theme");

  // programmatic render proof: per-slot canvas/SVG size + pixel variety
  const renderProof = await evaluate(`(function () {
    var out = {};
    ["strips","durations","timeline","distribution","trend","degraded"]
      .forEach(function (k) {
        var el = document.getElementById("chart-" + k);
        var rec = { canvas: 0, svg: 0, pixelVariety: 0 };
        el.querySelectorAll("canvas").forEach(function (c) {
          if (c.width > 0 && c.height > 0) {
            rec.canvas++;
            try {
              var ctx = c.getContext("2d");
              var img = ctx.getImageData(0, 0, c.width, c.height).data;
              var colors = {};
              var n = img.length / 4;
              for (var i = 0; i < n; i += 7) {
                colors[img[4*i] + "," + img[4*i+1] + "," + img[4*i+2]] = 1;
              }
              rec.pixelVariety = Object.keys(colors).length;
            } catch (e) { rec.pixelVariety = -1; }
          }
        });
        el.querySelectorAll("svg").forEach(function (v) {
          if (v.getBoundingClientRect().width > 0) { rec.svg++; }
        });
        rec.texts = el.querySelectorAll("svg text").length;
        out[k] = rec;
      });
    return out;
  })()`);

  // dark screenshot
  const shotDark = await S("Page.captureScreenshot", { format: "png" });
  const baseDark = pageFile.replace(/\.html$/, "") + (variant ? "-" + variant : "");
  fs.writeFileSync(path.join(outDir, baseDark + "-dark.png"),
    Buffer.from(shotDark.data, "base64"));

  // a11y tree (dark): retry until populated
  await sleep(600);
  let ax = { nodes: [] };
  for (let i = 0; i < 3; i++) {
    const t = await S("Accessibility.getFullAXTree", { fetchRelatives: true });
    if ((t.nodes || []).length > (ax.nodes || []).length) { ax = t; }
    await sleep(400);
  }
  const axPath = path.join(outDir, baseDark + "-ax.json");
  fs.writeFileSync(axPath, JSON.stringify(ax.nodes, null, 2));
  const axSummary = (ax.nodes || []).filter((n) => {
    return n.role && (n.role.value === "img" || n.role.value === "graphics-symbol"
      || n.role.value === "heading" || n.role.value === "button")
      && (n.name && n.name.value && n.name.value.length > 0);
  }).map((n) => ({ role: n.role.value, name: n.name.value.slice(0, 90) }));

  // theme flip to light
  await evaluate("document.getElementById('theme-toggle').click(); true");
  await sleep(400);
  const theme2 = await evaluate("window.__adl.theme");
  const renderCount2 = await evaluate("window.__adl.renderCount");
  const report2 = await evaluate("window.__adl.report()");
  const shotLight = await S("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(path.join(outDir, baseDark + "-light.png"),
    Buffer.from(shotLight.data, "base64"));

  // flip back to dark, then simulate visibilitychange hidden -> visible
  await evaluate("document.getElementById('theme-toggle').click(); true");
  await sleep(200);
  await evaluate(`
    (function () {
      Object.defineProperty(document, "visibilityState",
        { value: "hidden", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
      Object.defineProperty(document, "visibilityState",
        { value: "visible", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
      return true;
    })()`);
  await sleep(300);
  const renderCount3 = await evaluate("window.__adl.renderCount");

  // console/errors summary
  const consoleErrors = cdp.events.log
    .filter((e) => e.level === "error" || e.level === "warning")
    .map((e) => ({ level: e.level, text: (e.text || "").slice(0, 200) }))
    .concat(cdp.events.console
      .filter((p) => p.type === "error" || p.type === "warning")
      .map((p) => ({
        level: p.type,
        text: p.args.map((a) => a.value || a.description || "").join(" ").slice(0, 200)
      })));
  const exceptions = cdp.events.exception.map((e) => ({
    text: (e.exception && e.exception.description || "").slice(0, 300)
  }));

  const summary = {
    page: pageFile,
    variant: variant,
    url: url,
    echartsVersion: report1 && report1.echartsVersion || null,
    renderCount: { afterLoad: renderCount1, afterThemeFlip: renderCount2,
      afterVisibility: renderCount3 },
    theme: { initial: theme1, afterFlip: theme2 },
    themeFlipRerendered: report1 && report2
      && JSON.stringify(report1.tokens) !== JSON.stringify(report2.tokens),
    report1: report1,
    report2: report2,
    renderProof: renderProof,
    axNodeCount: ax.nodes ? ax.nodes.length : 0,
    axSummary: axSummary,
    consoleErrors: consoleErrors,
    exceptions: exceptions,
    files: {
      dark: baseDark + "-dark.png",
      light: baseDark + "-light.png",
      ax: path.basename(axPath)
    }
  };
  const outPath = path.join(outDir, baseDark + ".json");
  fs.writeFileSync(outPath, JSON.stringify(summary, null, 2));

  console.log(JSON.stringify({
    page: pageFile, variant: variant,
    renderCount: summary.renderCount,
    themeFlipRerendered: summary.themeFlipRerendered,
    tokensDark: report1 && report1.tokens.success,
    tokensLight: report2 && report2.tokens.success,
    axNodes: summary.axNodeCount,
    axImgNodes: axSummary.filter((n) => n.role === "img").length,
    consoleErrors: consoleErrors.length,
    exceptions: exceptions.length,
    slotErrors: report1 && report1.errors || [],
    renderProof: renderProof,
    out: outPath
  }, null, 2));

  cdp.close();
  chrome.kill("SIGTERM");
  await sleep(300);
  for (let i = 0; i < 5; i++) {
    try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 3 }); break; }
    catch (e) { await sleep(500); }
  }
}

main().catch((err) => { console.error("PROBE FAILED:", err); process.exit(1); });
