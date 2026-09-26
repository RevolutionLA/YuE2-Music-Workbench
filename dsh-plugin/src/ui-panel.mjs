// 音乐工作台 · 一体化插件 — Host 半
//
// 职责：把本机 YuE2 网关（:7863）的 REST API 以同源前缀反代进 DSH，并以
//       本地磁盘托管 UI 页面，使工作台成为单入口。
//
//   * /lab-api/*   → 网关 /api/*   （dsh 侧 client.js 用；JSON API + 音频文件）
//   * /lab/api/*   → 网关 /api/*   （页面侧 index.html 用：它把 fetch("/api/x")
//                                   改写成 "/lab/api/x"，见 LAB_BASE）
//   * /lab/*       → 本仓库 static/ 静态文件（本地托管，不再反代网关根路径）
//   * /lab-status  → 网关存活探测（client 探活 / 首次自检）
//
// 即：跨进程通道只剩 "…/api/*" 这一支，网关的其余路由不再对外可达。
//
// 架构要点（2026-09-26 重构）：
//   1. UI 归 3081：页面从磁盘读，不经网关。网关挂了只会 API 报错，页面照常显示，
//      不再整页白屏——这是旧实现"iframe 套 7863 根路径"最大的故障放大器。
//   2. 反代面收窄到 /api：网关根路径不再对外可达（编译网关自带路由不会经此暴露）。
//   3. 连接池 + 分级超时：旧实现每请求 connection:close + 一刀切 20s 超时，
//      大音频下载会被中途 destroy；现在长任务/媒体不限时，探活类 5s 快速失败。
//   4. 端口不再硬编码：统一读仓库根 ports.json。
import http from 'node:http';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'yue2-lab-ui';

export const inject = ['webServer'];

const HERE = path.dirname(fileURLToPath(import.meta.url));

// --------------------------------------------------------------------------- //
// 仓库根定位。
//
// 坑：dsh 会把插件拷贝/安装到 _dsh_home\profiles\web\node_modules 下再加载，
// 所以 import.meta.url 指向的是副本目录 —— 从它往上找永远找不到仓库根
// （实测会推成 ...\_dsh_home\profiles\web\node_modules，静态页全部 404）。
// 因此优先级为：环境变量 YUE2_ROOT（启动脚本显式注入，最可靠）
//            → 从 cwd 向上找（dsh 以 dsh-plugin 为工作目录启动）
//            → 从模块目录向上找（兜底）。
// --------------------------------------------------------------------------- //
function isRoot(dir) {
  return fs.existsSync(path.join(dir, 'static', 'index.html'))
    && fs.existsSync(path.join(dir, 'app.py'));
}

function findRoot() {
  const env = (process.env.YUE2_ROOT || '').trim();
  if (env) {
    const p = path.resolve(env);
    if (isRoot(p)) return p;
  }
  for (const start of [process.cwd(), HERE]) {
    let dir = path.resolve(start);
    for (let i = 0; i < 8; i++) {
      if (isRoot(dir)) return dir;
      const up = path.dirname(dir);
      if (up === dir) break;
      dir = up;
    }
  }
  return path.resolve(HERE, '..', '..');
}

const ROOT = findRoot();
const STATIC_DIR = path.join(ROOT, 'static');

// --------------------------------------------------------------------------- //
// 端口唯一真源：仓库根 ports.json（与 Python 侧 src/ports.py 同源约定）
// --------------------------------------------------------------------------- //
const PORTS_FILE = path.join(ROOT, 'ports.json');
const DEFAULT_PORTS = { gateway: 7863, dsh: 3081, audiocpp: 8080 };
const PORT_ENV = {
  gateway: 'YUE2_GATEWAY_PORT',
  dsh: 'YUE2_DSH_PORT',
  audiocpp: 'YUE2_AUDIOCPP_PORT',
};

function loadPorts() {
  const ports = { ...DEFAULT_PORTS };
  try {
    const raw = JSON.parse(fs.readFileSync(PORTS_FILE, 'utf8'));
    for (const k of Object.keys(DEFAULT_PORTS)) {
      const v = raw?.[k];
      if (Number.isInteger(v) && v >= 1 && v <= 65535) ports[k] = v;
    }
  } catch { /* 缺失/损坏 → 默认值，配置问题不得阻断启动 */ }
  for (const [k, name] of Object.entries(PORT_ENV)) {
    const v = (process.env[name] || '').trim();
    if (/^\d+$/.test(v) && +v >= 1 && +v <= 65535) ports[k] = +v;
  }
  return ports;
}

const PORTS = loadPorts();
const LAB_HOST = '127.0.0.1';
const LAB_PORT = PORTS.gateway;
const GATEWAY = `http://${LAB_HOST}:${LAB_PORT}`;

// --------------------------------------------------------------------------- //
// 连接池：旧实现每请求 connection:close，30MB 音频一次一握手，慢且易被中间设备掐断。
// --------------------------------------------------------------------------- //
const agent = new http.Agent({
  keepAlive: true,
  keepAliveMsecs: 15000,
  maxSockets: 24,
  maxFreeSockets: 8,
  scheduling: 'lifo',
});

// --------------------------------------------------------------------------- //
// 超时分级：0 表示不限时（长任务由客户端自己取消 / 看门狗兜底）
// 顺序敏感，先命中先生效。
// --------------------------------------------------------------------------- //
const TIMEOUT_RULES = [
  // 探活 / 轻量列表：快速失败，别让首屏卡在死连接上
  [/\/api\/(health|status|models\/list|voices\?|batch\/snapshot)$/i, 8000],
  // 媒体与大文件下载：整首 wav 几十 MB，绝不能中途 destroy
  [/\.(wav|mp3|flac|ogg|opus|m4a|aac|zip|tar|gz|pt|pth|gguf|bin|npy|onnx)(\?|$)/i, 0],
  [/\/api\/(output|file|download|media|assets)\//i, 0],
  // 生成 / 换声 / 转谱 / 音色训练：分钟级长任务
  [/\/api\/(generate|rvc|score|scores|abc|voices\/train|batch)/i, 0],
];
const DEFAULT_TIMEOUT = 30000; // 其余 JSON 接口

function timeoutFor(url) {
  for (const [re, ms] of TIMEOUT_RULES) if (re.test(url)) return ms;
  return DEFAULT_TIMEOUT;
}

function json(res, status, obj) {
  if (res.headersSent || res.writableEnded) return;
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(obj));
}

/**
 * 反代到网关。相比旧实现：
 *  - 走连接池（keepAlive），不再每请求建连/断连；
 *  - 超时按路径分级，长任务与媒体下载不再被 20s 一刀切；
 *  - 响应已开始后才超时 → 只收尾不改写状态码，避免半截响应 + 504 双重污染；
 *  - 客户端断开时同步销毁上游，杜绝连接泄漏。
 */
function proxy(req, res, url) {
  const headers = { ...req.headers, host: `${LAB_HOST}:${LAB_PORT}` };
  delete headers['connection'];
  delete headers['transfer-encoding'];

  let settled = false;
  const target = http.request(
    { host: LAB_HOST, port: LAB_PORT, path: url, method: req.method, headers, agent },
    (up) => {
      const out = { ...up.headers };
      for (const h of ['transfer-encoding', 'connection', 'keep-alive', 'upgrade']) delete out[h];
      res.writeHead(up.statusCode || 502, out);
      if (up.statusCode === 204 || up.statusCode === 304 || req.method === 'HEAD') {
        up.resume();
        res.end();
        settled = true;
        return;
      }
      up.pipe(res);
      up.on('end', () => { settled = true; });
      up.on('error', () => { if (!settled) { settled = true; res.destroy(); } });
    },
  );

  const ms = timeoutFor(url);
  if (ms > 0) {
    target.setTimeout(ms, () => {
      if (settled) return;
      settled = true;
      target.destroy();
      // 响应头已发出：只能收尾，不能再塞 504（浏览器会当成截断的坏响应）
      if (res.headersSent) { res.end(); return; }
      json(res, 504, { ok: false, error: `网关响应超时（${ms / 1000}s），请稍后重试` });
    });
  }

  target.on('error', () => {
    if (settled) return;
    settled = true;
    if (res.headersSent) { res.end(); return; }
    json(res, 502, {
      ok: false,
      error: `音乐工作台网关（:${LAB_PORT}）未运行，请先启动主程序。`,
    });
  });

  res.on('close', () => { if (!settled) { settled = true; target.destroy(); } });
  req.on('error', () => { if (!settled) { settled = true; target.destroy(); } });
  req.pipe(target);
}

function gatewayAlive() {
  return new Promise((resolve) => {
    const r = http.get(
      `${GATEWAY}/api/health`,
      { agent, timeout: 2500 },
      (up) => { resolve(up.statusCode < 500); up.resume(); },
    );
    r.on('error', () => resolve(false));
    r.on('timeout', () => { r.destroy(); resolve(false); });
  });
}

// --------------------------------------------------------------------------- //
// /lab/* → 本地 static/ 静态托管（阶段二：UI 归 3081，不再反代网关根路径）
// --------------------------------------------------------------------------- //
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.htm': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
};

/** 把 URL 路径安全地映射到 static/ 内的文件；越界返回 null。 */
function resolveStatic(urlPath) {
  const clean = decodeURIComponent(urlPath.split('?')[0].split('#')[0]);
  const rel = clean.replace(/^\/+/, '');
  if (!rel || rel === '' || rel.endsWith('/')) return path.join(STATIC_DIR, 'index.html');
  const abs = path.resolve(STATIC_DIR, rel);
  if (abs !== STATIC_DIR && !abs.startsWith(STATIC_DIR + path.sep)) return null; // 目录穿越
  return abs;
}

async function serveStatic(req, res, urlPath) {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    return json(res, 405, { ok: false, error: 'method not allowed' });
  }
  const file = resolveStatic(urlPath);
  if (!file) return json(res, 403, { ok: false, error: 'forbidden' });
  try {
    const st = await fsp.stat(file);
    if (!st.isFile()) return json(res, 404, { ok: false, error: 'not found' });
    const type = MIME[path.extname(file).toLowerCase()] || 'application/octet-stream';
    // 页面迭代频繁 → no-store；静态资源用 etag 协商缓存，减少 161KB 主文档重复传输
    const isHtml = type.startsWith('text/html');
    const etag = `W/"${st.size}-${st.mtimeMs.toString(36)}"`;
    const head = {
      'content-type': type,
      'content-length': st.size,
      'cache-control': isHtml ? 'no-store, must-revalidate' : 'public, max-age=300',
      etag,
    };
    if (req.headers['if-none-match'] === etag) {
      res.writeHead(304, head);
      return res.end();
    }
    res.writeHead(200, head);
    if (req.method === 'HEAD') return res.end();
    const stream = fs.createReadStream(file);
    stream.on('error', () => { if (!res.headersSent) json(res, 500, { ok: false, error: 'read error' }); else res.destroy(); });
    stream.pipe(res);
  } catch {
    return json(res, 404, { ok: false, error: 'not found' });
  }
}

export function apply(ctx) {
  const ws = ctx.webServer;
  const disposers = [];

  // 注册顺序 = 特异性从高到低（/lab-api、/lab-status 都落在 /lab 的前缀里）。
  // 不依赖 dsh 内部的"最长前缀优先"规约：即便它按注册顺序匹配也不会串台。

  // 1) 同源 JSON 反代：/lab-api/* → 网关 /api/*（唯一跨进程通道）
  disposers.push(ws.register({
    kind: 'prefix',
    path: '/lab-api',
    handler: (req, res) => {
      const url = req.url.slice('/lab-api'.length);
      proxy(req, res, '/api' + (url.startsWith('/') ? url : '/' + url));
    },
  }));

  // 2) 网关存活探测（含 UI 托管方式，便于排障时一眼看出跑的是哪条链路）
  disposers.push(ws.register({
    kind: 'exact',
    path: '/lab-status',
    handler: async (_req, res) => {
      const alive = await gatewayAlive();
      const body = JSON.stringify({
        ok: true,
        alive,
        gateway: `${LAB_HOST}:${LAB_PORT}`,
        ports: PORTS,
        ui: 'local-static',   // 页面由 3081 从磁盘托管，非反代
        root: ROOT,
      });
      res.writeHead(200, { 'content-type': 'application/json; charset=utf-8' });
      res.end(body);
    },
  }));

  // 3) UI 本体 + 页面侧 API：/lab/* → 本仓库 static/（本地托管，网关挂了页面照常渲染）
  //
  //    注意：index.html 的 LAB_BASE 是 "/lab"，它把 fetch("/api/x") 改写成
  //    "/lab/api/x"。所以 "/lab/api/*" 这一支必须继续走反代，只有其余路径
  //    才回本地静态文件 —— 否则页面能出来但所有接口 404（改这里务必回归验证）。
  //    dsh 侧 client.js 用的是另一个前缀 /lab-api/*，见下方注释 1)。
  disposers.push(ws.register({
    kind: 'prefix',
    path: '/lab',
    handler: (req, res) => {
      const urlPath = req.url.slice('/lab'.length) || '/';
      if (urlPath === '/api' || urlPath.startsWith('/api/')) {
        return proxy(req, res, urlPath);   // /lab/api/x → 网关 /api/x
      }
      return serveStatic(req, res, urlPath);
    },
  }));

  return () => {
    for (const d of disposers) try { d(); } catch { /* noop */ }
    agent.destroy();
  };
}
