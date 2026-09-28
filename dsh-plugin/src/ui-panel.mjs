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
        if (timer) clearTimeout(timer);
        return;
      }
      up.pipe(res);
      up.on('end', () => { settled = true; if (timer) clearTimeout(timer); });
      up.on('error', () => { if (!settled) { settled = true; res.destroy(); } });
    },
  );

  const ms = timeoutFor(url);
  // 兜底计时器必须是"墙上时钟"，不能用 target.setTimeout()：那个超时是**绑在 socket 上**的，
  // 连接池（maxSockets:24）被长任务占满时，请求还排在 agent 队列里、压根没拿到 socket，
  // setTimeout 永不触发 → 面板所有 GET 无限转圈（实测：dsh→7863 挂着 24 条 ESTABLISHED，
  // /lab/api/* 与 /lab-status 全 pending；同一条 URL 在刚起的进程上 8 ms 就 200）。
  // 这里给每个限时请求一个与 socket 无关的定时器，宁可返回可读的 504 也不让界面静默卡死。
  let timer = null;
  const poolStats = () => {
    const queued = Object.values(agent.requests || {})
      .reduce((n, q) => n + (Array.isArray(q) ? q.length : 0), 0);
    return `${Object.keys(agent.sockets).length} 在用/${Object.keys(agent.freeSockets).length} 空闲`
      + ` socket，${queued} 条排队`;
  };
  const fail = (msg, code) => {
    if (settled) return;
    settled = true;
    if (timer) clearTimeout(timer);
    target.destroy();
    // 响应头已发出：只能收尾，不能再塞 504（浏览器会当成截断的坏响应）
    if (res.headersSent) { res.end(); return; }
    console.error(`[yue2-lab] ${req.method} ${url} → ${code}（${msg}；网关 :${LAB_PORT}，${poolStats()}）`);
    json(res, 504, { ok: false, error: msg });
  };
  if (ms > 0) {
    const msg = `网关响应超时（${ms / 1000}s），请稍后重试`;
    timer = setTimeout(() => fail(msg, '排队/响应超时'), ms);
    target.setTimeout(ms, () => fail(msg, 'socket 超时'));
  }

  target.on('error', (err) => {
    if (settled) return;
    settled = true;
    if (timer) clearTimeout(timer);
    if (res.headersSent) { res.end(); return; }
    console.error(`[yue2-lab] ${req.method} ${url} → 502 上游错误：${err && err.code ? err.code : err}`);
    json(res, 502, {
      ok: false,
      error: `音乐工作台网关（:${LAB_PORT}）未运行，请先启动主程序。`,
    });
  });

  res.on('close', () => { if (timer) clearTimeout(timer); if (!settled) { settled = true; target.destroy(); } });
  req.on('error', () => { if (timer) clearTimeout(timer); if (!settled) { settled = true; target.destroy(); } });
  req.pipe(target);
}

// --------------------------------------------------------------------------- //
// 面板路由自己的鉴权闸门
//
// 为什么要这一层：ws.register() 把路由挂在 dsh 裸 webServer 上，绕开了 dsh 的
// 浏览器信任栅栏与 token/cookie 校验（栅栏只管 /api/*）。局域网开放之后这就成了
// 一扇没锁的门 —— 实测（2026-09-28）不带任何 cookie：
//   GET  http://<lan>:3081/lab/api/health       → 200
//   POST http://<lan>:3081/lab/api/generate/start → 400（校验后才拦，说明请求已进网关）
//   GET  http://<lan>:3081/lab/api/ai/web        → 200，响应里直接给出带 token 的
//        dsh 地址 —— 等于把"AI 对话"的门票发给任何一个能连上局域网的机器。
// 我们拿不到 dsh 签 cookie 的密钥，所以把校验**外包给 dsh 自己**：拿调用方的
// Host+Cookie 回环访问 dsh 的首页（那是栅栏保护的路径），401 即未鉴权。
// 结果按 (host, cookie) 缓存 30 s，页面 10 s 一轮的轮询不会每次都多跑一趟。
// --------------------------------------------------------------------------- //
const AUTH_CACHE_TTL = 30000;
const authCache = new Map();  // key -> { ok, at }

function dshVerifies(req) {
  return new Promise((resolve) => {
    const r = http.get(
      {
        host: '127.0.0.1',
        port: PORTS.dsh,
        path: '/',
        timeout: 2500,
        headers: {
          host: req.headers.host || `127.0.0.1:${PORTS.dsh}`,
          cookie: req.headers.cookie || '',
          'user-agent': 'yue2-lab-auth-check',
        },
      },
      (up) => { resolve(up.statusCode !== 401 && up.statusCode !== 403); up.resume(); },
    );
    r.on('error', (e) => {
      console.error(`[yue2-lab] 鉴权自检请求失败（按未授权处理，宁可误拒）：${e && e.code ? e.code : e}`);
      resolve(false);
    });
    r.on('timeout', () => { r.destroy(); console.error('[yue2-lab] 鉴权自检超时（按未授权处理）'); resolve(false); });
  });
}

/** 调用方是否已经通过 dsh 的 token/cookie 鉴权。 */
async function isAuthorized(req) {
  const key = `${req.headers.host || ''}\n${req.headers.cookie || ''}`;
  if (!req.headers.cookie) return false;          // 没 cookie 一定是裸请求
  const hit = authCache.get(key);
  if (hit && Date.now() - hit.at < AUTH_CACHE_TTL) return hit.ok;
  if (authCache.size > 64) authCache.clear();     // 粗略封顶，别让缓存无限长
  const ok = await dshVerifies(req);
  authCache.set(key, { ok, at: Date.now() });
  return ok;
}

/** 统一拒绝：留日志（静默退化必须有痕迹），并回可读的 401。 */
function deny(req, res) {
  console.error(`[yue2-lab] ${req.method} ${req.url} → 401 未经 dsh 鉴权（Host=${req.headers.host}，`
    + `来自局域网的裸请求；请用带 token 的地址打开工作台）`);
  json(res, 401, {
    ok: false,
    error: '请先用带 token 的工作台地址打开页面（token 见本机 dsh-plugin\\_dsh_web.log），再访问面板接口。',
  });
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
    handler: async (req, res) => {
      if (!(await isAuthorized(req))) return deny(req, res);
      const url = req.url.slice('/lab-api'.length);
      proxy(req, res, '/api' + (url.startsWith('/') ? url : '/' + url));
    },
  }));

  // 2) 网关存活探测（含 UI 托管方式，便于排障时一眼看出跑的是哪条链路）
  disposers.push(ws.register({
    kind: 'exact',
    path: '/lab-status',
    handler: async (_req, res) => {
      if (!(await isAuthorized(_req))) return deny(_req, res);
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
    handler: async (req, res) => {
      if (!(await isAuthorized(req))) return deny(req, res);
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
