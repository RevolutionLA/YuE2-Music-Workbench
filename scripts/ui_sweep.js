#!/usr/bin/env node
/* 14 页真实浏览器巡检：逐页点击，检查面板可见 / 横向溢出 / JS 异常，并留截图。
 *
 * 用法（先启动工作台；无引擎也能跑）：
 *   node scripts/ui_sweep.js --launch          # 自动开一个无头 Edge
 *   node scripts/ui_sweep.js --cdp http://127.0.0.1:9223
 *
 * 产物：runtime/data/ui-sweep/<时间戳>/{report.json, <page>.png}
 * 退出码：0 全通过；1 有页面失败/超时；2 连不上浏览器。
 */
const fs = require("fs");
const path = require("path");
const { spawn, execFileSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, "static", "pages.json"), "utf8"));
const PAGES = manifest.pages.map((p) => p.id);

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
const BASE = arg("base", "http://127.0.0.1:7863").replace(/\/$/, "");
let CDP = arg("cdp", "");
const LAUNCH = process.argv.includes("--launch");
const WAIT_EXTRA = { about: 3200, lrc: 1600 };
const CMD_TIMEOUT = 15000;
const HARD_TIMEOUT = 150000;

const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let launched = null;

function killChild(child) {
  if (!child) return;
  try {
    if (process.platform === "win32") execFileSync("taskkill", ["/F", "/T", "/PID", String(child.pid)], { stdio: "ignore" });
    else child.kill("SIGTERM");
  } catch { }
}

async function pickPort() {
  // 不复用固定 9223：上一次被强杀的 Edge 可能留下僵尸实例占着端口，
  // 新浏览器起不来，脚本会连到僵尸页面上并在 evaluate 处一直等。
  for (let p = 9300; p < 9400; p++) {
    try {
      await fetch(`http://127.0.0.1:${p}/json/version`, { signal: AbortSignal.timeout(300) });
    } catch {
      return p;
    }
  }
  throw new Error("9300-9399 没有空闲调试端口");
}

async function waitCdp(deadlineMs = 20000) {
  const end = Date.now() + deadlineMs;
  while (Date.now() < end) {
    try {
      const r = await fetch(CDP + "/json/version", { signal: AbortSignal.timeout(2000) });
      if (r.ok) return true;
    } catch { }
    await sleep(300);
  }
  return false;
}

async function main() {
  let port = null;
  if (LAUNCH) {
    const exe = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
    if (!exe) throw Object.assign(new Error("找不到 Edge/Chrome；请手动开一个带 --remote-debugging-port 的浏览器"), { code: 2 });
    port = await pickPort();
    CDP = `http://127.0.0.1:${port}`;
    const profile = path.join(process.env.TEMP || ".", "yue2-ui-sweep-profile-" + process.pid);
    fs.rmSync(profile, { recursive: true, force: true });
    launched = spawn(exe, [
      "--headless=new", "--disable-gpu", `--remote-debugging-port=${port}`,
      "--user-data-dir=" + profile, "--window-size=1680,1000", "about:blank",
    ], { stdio: "ignore" });
    console.log(`[ui] launch edge pid=${launched.pid} port=${port}`);
  } else {
    CDP = CDP || "http://127.0.0.1:9223";
    console.log(`[ui] attach cdp ${CDP}`);
  }
  try {
    if (!(await waitCdp())) throw Object.assign(new Error("连不上 CDP：" + CDP), { code: 2 });
    console.log(`[ui] cdp ready ${CDP}`);
    const list = await (await fetch(CDP + "/json/list", { signal: AbortSignal.timeout(5000) })).json();
    const page = list.find((t) => t.type === "page");
    if (!page) throw Object.assign(new Error("CDP 没有 page target"), { code: 2 });

    const ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let id = 0; const pending = new Map(); const errors = []; let current = "boot";
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
      if (m.method === "Runtime.exceptionThrown") {
        const d = m.params.exceptionDetails;
        errors.push({ page: current, text: (d.text || "") + " " + ((d.exception && d.exception.description) || "") });
      }
    };
    const cmd = (method, params = {}) => new Promise((res, rej) => {
      const i = ++id;
      const timer = setTimeout(() => { pending.delete(i); rej(new Error("CDP 超时：" + method)); }, CMD_TIMEOUT);
      pending.set(i, (m) => { clearTimeout(timer); res(m); });
      ws.send(JSON.stringify({ id: i, method, params }));
    });
    const ev = async (expr) => {
      const r = await cmd("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 300));
      return r.result.result.value;
    };

    await cmd("Runtime.enable"); await cmd("Page.enable");
    await cmd("Page.navigate", { url: BASE + "/?nc=" + Date.now() });
    await sleep(3500);
    console.log(`[ui] navigated ${BASE}`);

    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const outDir = path.join(ROOT, "runtime", "data", "ui-sweep", stamp);
    fs.mkdirSync(outDir, { recursive: true });
    const results = [];
    for (const p of PAGES) {
      current = p;
      const clicked = await ev(`(() => { const b = document.querySelector('[data-tab="${p}"]'); if (!b) return false; b.click(); return true; })()`);
      await sleep(WAIT_EXTRA[p] || 1100);
      const st = await ev(`(() => {
        const panel = document.getElementById('tab-${p}');
        const active = document.querySelector('.tab.active');
        return {
          clicked: ${clicked}, active: active ? active.dataset.tab : null,
          panel: !!panel,
          visible: !!(panel && getComputedStyle(panel).display !== 'none'),
          overflow: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
          scrollW: document.documentElement.scrollWidth, viewport: window.innerWidth,
        };
      })()`);
      const shot = await cmd("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(path.join(outDir, p + ".png"), Buffer.from(shot.result.data, "base64"));
      const row = { id: p, ...st, errors: errors.filter((e) => e.page === p).length };
      results.push(row);
      const bad = !row.clicked || row.active !== row.id || !row.panel || !row.visible || row.overflow > 1 || row.errors > 0;
      console.log(`[ui] ${bad ? "FAIL" : "ok  "} ${p}` + (bad ? " " + JSON.stringify(row) : ""));
    }
    current = "done";
    ws.close();

    const bad = results.filter((r) => !r.clicked || r.active !== r.id || !r.panel || !r.visible || r.overflow > 1 || r.errors > 0);
    fs.writeFileSync(path.join(outDir, "report.json"),
      JSON.stringify({ base: BASE, at: stamp, results, errors }, null, 2));
    if (!bad.length && !errors.length) {
      console.log(`[ui] ${results.length}/${results.length} ok，overflow=0，errors=0`);
    } else {
      for (const e of errors) console.error(`[ui] JS ${e.page}: ${e.text.slice(0, 200)}`);
    }
    console.log(`[ui] report ${path.relative(ROOT, outDir)}`);
    process.exitCode = bad.length || errors.length ? 1 : 0;
  } finally {
    killChild(launched);
  }
}

const hard = setTimeout(() => {
  console.error(`[ui] 总超时 ${HARD_TIMEOUT / 1000}s`);
  killChild(launched);
  process.exit(1);
}, HARD_TIMEOUT);

main()
  .then(() => { clearTimeout(hard); process.exit(process.exitCode || 0); })
  .catch((e) => { clearTimeout(hard); killChild(launched); console.error("[ui] " + e.message); process.exit(e.code === 2 ? 2 : 1); });
