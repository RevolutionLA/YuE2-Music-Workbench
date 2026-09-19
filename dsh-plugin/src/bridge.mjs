// -*- coding: utf-8 -*-
// YuE2 工具桥：dsh 工具调用 → Python 子进程执行 ai_tools.py 纯函数。
// 复用现有工具实现，Node 侧零重复逻辑。
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PY = path.join(ROOT, 'py312', 'python.exe');

/** 调用 ai_tools.py 里的一个工具函数（JSON in / JSON out）。 */
export function callPyTool(name, args = {}, timeoutMs = 120000) {
  return new Promise((resolve) => {
    const code = `import json,sys;sys.path.insert(0, ${JSON.stringify(ROOT)});import ai_tools;` +
      `print("@@RESULT@@" + json.dumps(ai_tools.${name}(**json.loads(sys.stdin.read())), ensure_ascii=False))`;
    const p = spawn(PY, ['-I', '-c', code], { cwd: ROOT, windowsHide: true });
    let out = '', err = '';
    const timer = setTimeout(() => { p.kill(); resolve({ error: '工具执行超时' }); }, timeoutMs);
    p.stdout.on('data', (d) => { out += d; });
    p.stderr.on('data', (d) => { err += d; });
    p.on('close', () => {
      clearTimeout(timer);
      const i = out.indexOf('@@RESULT@@');
      if (i >= 0) {
        try { resolve(JSON.parse(out.slice(i + 10))); return; } catch { /* fallthrough */ }
      }
      resolve({ error: (err || out || '执行失败').slice(0, 500) });
    });
    p.stdin.write(JSON.stringify(args));
    p.stdin.end();
  });
}
