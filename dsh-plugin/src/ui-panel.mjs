// 音乐工作台 · 一体化插件 — Host 半
//
// 职责：把本机 YuE2 网关（:7863）的全部 REST API 以同源前缀反代进 DSH，
//       供浏览器端的原生音乐工作台面板（lib/client.js 注册的 Slot）调用。
//   * /lab-api/*   → 网关 /api/*   （JSON API + 音频文件，创作/批量/换声/历史/音色/模型）
//   * /lab/*       → 网关根路径    （兼容旧页面，可选）
//   * /lab-status  → 网关存活探测（client 探活 / 首次自检）
//
// 不做 iframe 覆盖注入——UI 由原生 DSH 侧栏 + 主面板 Slot 提供，浑然一体。
import http from 'node:http';

export const name = 'yue2-lab-ui';

export const inject = ['webServer'];

const LAB_HOST = '127.0.0.1';
const LAB_PORT = 7863;
const GATEWAY = 'http://127.0.0.1:7863';

function proxy(req, res, url, rawTargetPort = null) {
  // 转发到网关。NO_PROXY 已在网关侧设置；这里直连 127.0.0.1 不依赖系统代理。
  const target = http.request(
    {
      host: LAB_HOST,
      port: LAB_PORT,
      path: url,
      method: req.method,
      headers: { ...req.headers, host: `${LAB_HOST}:${LAB_PORT}`, connection: 'close' },
    },
    (up) => {
      const headers = { ...up.headers };
      // 剥掉 hop-by-hop，避免浏览器端重复 Set-Cookie / 连接复用问题
      for (const h of ['transfer-encoding', 'connection', 'keep-alive', 'upgrade']) delete headers[h];
      if (up.statusCode === 204 || up.statusCode === 304) {
        res.writeHead(up.statusCode, headers);
        res.end();
      } else {
        res.writeHead(up.statusCode, headers);
        up.pipe(res);
      }
    },
  );
  target.setTimeout(20000, () => {
    target.destroy();
    if (!res.headersSent) {
      res.writeHead(504, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: '网关响应超时（20s），请稍后重试' }));
    }
  });
  target.on('error', () => {
    if (!res.headersSent) {
      res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ ok: false, error: '音乐工作台网关（:7863）未运行，请先启动主程序。' }));
    }
  });
  req.pipe(target);
}

function gatewayAlive() {
  return new Promise((resolve) => {
    const r = http.get(`${GATEWAY}/api/models/list`, { timeout: 1500 }, (up) => {
      resolve(up.statusCode < 500);
      up.resume();
    });
    r.on('error', () => resolve(false));
    r.on('timeout', () => { r.destroy(); resolve(false); });
  });
}

export function apply(ctx) {
  const ws = ctx.webServer;
  const disposers = [];

  // 1) 同源 JSON 反代：/lab-api/* → 网关 /api/*
  disposers.push(ws.register({
    kind: 'prefix',
    path: '/lab-api',
    handler: (req, res) => {
      const url = req.url.slice('/lab-api'.length);
      proxy(req, res, '/api' + (url.startsWith('/') ? url : '/' + url));
    },
  }));

  // 2) 兼容旧页面：/lab/* → 网关根路径
  disposers.push(ws.register({
    kind: 'prefix',
    path: '/lab',
    handler: (req, res) => {
      const url = req.url.slice('/lab'.length) || '/';
      proxy(req, res, url);
    },
  }));

  // 3) 网关存活探测
  disposers.push(ws.register({
    kind: 'exact',
    path: '/lab-status',
    handler: async (_req, res) => {
      const alive = await gatewayAlive();
      const body = JSON.stringify({ ok: true, alive, gateway: `${LAB_HOST}:${LAB_PORT}` });
      res.writeHead(200, { 'content-type': 'application/json; charset=utf-8' });
      res.end(body);
    },
  }));

  return () => { for (const d of disposers) try { d(); } catch { /* noop */ } };
}