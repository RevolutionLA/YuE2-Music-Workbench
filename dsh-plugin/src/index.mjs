// 音乐工作台 YuE2 插件入口：向 dsh 注册 9 个音乐工具。
// 工具实现在 ai_tools.py（纯 Python），经 bridge.callPyTool 子进程桥调用。
import { defineYuE2Tools } from './tools.mjs';

/** 稳定插件名（Cordis 规范）。 */
export const name = 'yue2-lab';

/** 需要的 dsh 服务就绪后才挂载（工具注册表由 dsh-tools 提供）。 */
export const inject = ['tools'];

/**
 * @param {import('@deepseek-ai/cordis').Context} ctx - Cordis 插件上下文
 */
export function apply(ctx) {
  const registry = ctx.tools;
  const disposers = [];
  for (const def of defineYuE2Tools()) {
    disposers.push(registry.register(def));
  }
  return () => { for (const d of disposers) try { d(); } catch { /* noop */ } };
}

export { SPECS } from './tools.mjs';
