// dsh runner：网关 /api/ai/chat 的后端内核桥。
// 用法：node runner.mjs "<task>"
// 输出（stdout）：@@JSON@@{reply, via:'dsh'}
// 实现：调 dsh CLI 的 headless profile（一次性 task → 最终文本），reasoning 走 stderr。
// 会话记忆由网关侧注入 task 前缀；删除确认沿用网关 pending 机制。
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DSH_BIN = path.join(HERE, '..', 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js');

export function runDsh(task, timeoutMs = 300000) {
  return new Promise((resolve) => {
    const p = spawn(process.execPath, [DSH_BIN, '--profile', 'headless', task], {
      cwd: HERE + '/..',
      windowsHide: true,
      env: { ...process.env, DSH_NO_BROWSER: '1' },
    });
    let out = '', err = '';
    const timer = setTimeout(() => { p.kill(); resolve({ reply: 'dsh 内核执行超时', via: 'dsh', error: 'timeout' }); }, timeoutMs);
    p.stdout.on('data', (d) => { out += d; });
    p.stderr.on('data', (d) => { err += d; });
    p.on('close', (code) => {
      clearTimeout(timer);
      const text = out.trim();
      if (code === 0 && text) resolve({ reply: text, via: 'dsh' });
      else resolve({ reply: '', via: 'dsh', error: (err || `exit ${code}`).slice(-400) });
    });
  });
}

// 直接执行：node runner.mjs "..."
if (process.argv[1] && process.argv[1].endsWith('runner.mjs') && process.argv[2]) {
  const r = await runDsh(process.argv.slice(2).join(' '));
  process.stdout.write('@@JSON@@' + JSON.stringify(r) + '\n');
  process.exit(r.error ? 1 : 0);
}
