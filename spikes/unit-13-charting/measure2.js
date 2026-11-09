// Load at width A (default 660), then optionally resize to width B (default 995), measure both states.
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const page = process.argv[2];
const wA = parseInt(process.argv[3] || '660', 10);
const wB = parseInt(process.argv[4] || '995', 10);
if (!page) { console.error('usage: node measure2.js <url> [wA] [wB]'); process.exit(1); }

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'adl-measure2-'));
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--headless=new', '--no-first-run', '--disable-gpu',
  '--window-size=' + wA + ',1100',
  '--remote-debugging-port=0',
  '--user-data-dir=' + profile,
  'about:blank',
], { stdio: ['ignore', 'ignore', 'ignore'] });

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
async function waitFor(fn, timeout, what) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    const v = await fn();
    if (v) return v;
    await sleep(100);
  }
  throw new Error('timeout waiting for ' + what);
}

async function main() {
  const port = await waitFor(async () => {
    try { return fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').trim().split('\n')[0]; }
    catch (e) { return null; }
  }, 8000, 'DevToolsActivePort');
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  let loaded = false;
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result);
    } else if (msg.method === 'Page.loadEventFired') loaded = true;
  };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  function send(method, params) {
    return new Promise((resolve, reject) => {
      const mid = ++id;
      pending.set(mid, { resolve, reject });
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
  }
  const MEASURE = `(function () {
    var doc = document.documentElement;
    function r(el) { if (!el) return null; var b = el.getBoundingClientRect(); return { w: Math.round(b.width), h: Math.round(b.height) }; }
    var out = { viewport: window.innerWidth, docScrollW: doc.scrollWidth, docClientW: doc.clientWidth, xOverflow: doc.scrollWidth > doc.clientWidth };
    out.slots = []; document.querySelectorAll('.chart-slot').forEach(function (s) { out.slots.push(r(s)); });
    out.canvases = []; document.querySelectorAll('canvas').forEach(function (c) { out.canvases.push({ rect: r(c), attrW: c.getAttribute('width'), styleW: c.style.width }); });
    out.svgs = []; document.querySelectorAll('.chart-slot svg').forEach(function (s) { out.svgs.push({ rect: r(s), attrW: s.getAttribute('width') }); });
    return out;
  })()`;
  async function measure() {
    const res = await send('Runtime.evaluate', { expression: MEASURE, returnByValue: true });
    return res.result.value;
  }

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: wA, height: 1100, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: page });
  await waitFor(() => loaded, 15000, 'load');
  await waitFor(async () => {
    const r = await send('Runtime.evaluate', { expression: '!!(window.__adl && window.__adl.report)', returnByValue: true });
    return r.result.value === true;
  }, 15000, '__adl.report');
  await sleep(1500);
  const afterLoad = await measure();
  // resize to wB
  await send('Emulation.setDeviceMetricsOverride', { width: wB, height: 1100, deviceScaleFactor: 1, mobile: false });
  await sleep(2500);
  const afterResize = await measure();
  console.log(JSON.stringify({ afterLoadAt: wA, afterResizeTo: wB, afterLoad, afterResize }, null, 2));
  ws.close();
  try { chrome.kill(); } catch (e) {}
  await sleep(300);
  try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 3 }); } catch (e) {}
  process.exit(0);
}
main().catch(e => { console.error(e.message); try { chrome.kill(); } catch (_) {} process.exit(1); });
