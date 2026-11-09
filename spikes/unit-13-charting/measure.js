// Zero-dep CDP measurement of spike pages at the user's 660x1100 window size.
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const page = process.argv[2];
if (!page) { console.error('usage: node measure.js <file:// url>'); process.exit(1); }

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'adl-measure-'));
const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
  '--headless=new', '--no-first-run', '--disable-gpu',
  '--window-size=660,1100',
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
    try {
      const p = fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').trim().split('\n')[0];
      return p;
    } catch (e) { return null; }
  }, 8000, 'DevToolsActivePort');

  const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const events = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result);
    } else if (msg.method === 'Page.loadEventFired') {
      events.push('load');
    }
  };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  function send(method, params) {
    return new Promise((resolve, reject) => {
      const mid = ++id;
      pending.set(mid, { resolve, reject });
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
  }
  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: page });
  await waitFor(() => events.length > 0, 15000, 'load');
  // wait for charts ready
  await waitFor(async () => {
    const r = await send('Runtime.evaluate', {
      expression: '!!(window.__adl && window.__adl.report)',
      returnByValue: true,
    });
    return r.result.value === true;
  }, 15000, '__adl.report');
  await sleep(1500); // let layout + observers settle

  const metrics = await send('Runtime.evaluate', {
    expression: `(function () {
      function rectOf(el) { if (!el) return null; var r = el.getBoundingClientRect(); return { x: Math.round(r.x), w: Math.round(r.width), h: Math.round(r.height) }; }
      var doc = document.documentElement;
      var out = {
        viewport: { w: window.innerWidth, h: window.innerHeight },
        docScroll: { scrollW: doc.scrollWidth, clientW: doc.clientWidth },
        bodyScroll: { scrollW: document.body.scrollWidth, clientW: document.body.clientWidth },
        xOverflow: doc.scrollWidth > doc.clientWidth,
        panels: [],
        slots: [],
        canvases: [],
        svgs: []
      };
      document.querySelectorAll('.panel').forEach(function (p) { out.panels.push(rectOf(p)); });
      document.querySelectorAll('.chart-slot').forEach(function (s) { out.slots.push(rectOf(s)); });
      document.querySelectorAll('canvas').forEach(function (c) {
        out.canvases.push({ rect: rectOf(c), attrW: c.getAttribute('width'), attrH: c.getAttribute('height'), styleW: c.style.width, styleH: c.style.height });
      });
      document.querySelectorAll('.chart-slot svg').forEach(function (s) {
        out.svgs.push({ rect: rectOf(s), attrW: s.getAttribute('width'), attrH: s.getAttribute('height'), styleW: s.style.width });
      });
      return out;
    })()`,
    returnByValue: true,
  });
  console.log(JSON.stringify(metrics.result.value, null, 2));
  ws.close();
  try { chrome.kill(); } catch (e) {}
  await sleep(300);
  try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 3 }); } catch (e) {}
  process.exit(0);
}
main().catch(e => { console.error(e.message); try { chrome.kill(); } catch (_) {} process.exit(1); });
