// ABC 加工的独立裁判：用第三方解析器 abcjs 算每个音的 MIDI，再和"应该变成什么"对比。
//
// 为什么需要它：我们自己的测试只能证明"我们按自己理解的规则算对了"。
// 移调这类活最怕"用同一把尺子量自己"——旧实现就是这么一路自洽地错下去的
// （漏调号、漏小节继承、后缀变音记号，单测全绿但真唱出来跑调）。
//
// 输入：cases.json（由 tests/test_abc_oracle.py 生成）
// 用法：node tests/abc_oracle.js <cases.json> [abcjs 路径]
global.window = global;
const fs = require('fs');
const path = require('path');

const casesPath = process.argv[2];
const abcjsPath = process.argv[3] ||
  path.join(__dirname, '..', 'checkpoints', 'render_assets', 'abcjs-basic-min.js');
const ABCJS = require(abcjsPath);
const CASES = JSON.parse(fs.readFileSync(casesPath, 'utf8'));

const PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const MODE_UP = new Set([3, 8, 10]);   // 小调 3/6/7 级（升半音 → 大调）
const MODE_DN = new Set([4, 9, 11]);   // 大调 3/6/7 级（降半音 → 小调）

function parseKey(abc) {
  const m = /^K:\s*(\S+)/m.exec(abc);
  if (!m) return null;
  const mm = /^([A-G])([#b]?)(.*)$/i.exec(m[1]);
  if (!mm) return null;
  let pc = PC[mm[1].toUpperCase()];
  if (mm[2] === '#') pc = (pc + 1) % 12;
  if (mm[2] === 'b') pc = (pc + 11) % 12;
  return { pc, minor: /^(m|min|minor)\b/.test((mm[3] || '').toLowerCase()) };
}

function extract(abc) {
  const tunes = ABCJS.parseOnly(abc);
  if (!tunes.length) return { error: 'parse: 无 tune' };
  const t = tunes[0];
  try { t.setUpAudio(); } catch (e) { return { error: 'setUpAudio: ' + e.message }; }
  const notes = [];
  const chords = [];
  for (const line of t.lines || []) {
    for (const staff of line.staff || []) {
      for (const vArr of staff.voices || []) {
        for (const el of vArr) {
          if (el.chord && el.chord.length) for (const c of el.chord) chords.push(c.name);
          if (el.rest || !el.pitches) continue;
          const midis = (el.midiPitches || []).map(m => m.pitch).sort((a, b) => a - b);
          const m0 = (el.midiPitches || [])[0] || {};
          notes.push({ midis, start: m0.start, dur: m0.duration });
        }
      }
    }
  }
  return { notes, chords };
}

function chordParts(raw) {
  const name = raw.replace(/♯/g, '#').replace(/♭/g, 'b');  // abcjs 渲染出的符号
  const m = /^([A-G](?:##|bb|[#b])?)(.*)$/.exec(name);
  if (!m) return null;
  const toPc = (s) => {
    let pc = PC[s[0]];
    for (const ch of s.slice(1)) pc += ch === '#' ? 1 : -1;
    return ((pc % 12) + 12) % 12;
  };
  const slash = /\/\s*([A-G](?:##|bb|[#b])?)\s*$/.exec(m[2]);
  return { root: toPc(m[1]), bass: slash ? toPc(slash[1]) : null };
}

// 独立复述"该动多少"：调式规则只看音级；目标调式等于源调式时整条规则作废（no-op）。
function expected(mode, srcMinor, deg) {
  if (!mode || mode === 'keep') return 0;
  const targetMinor = mode.startsWith('major_to');
  if (targetMinor === srcMinor) return 0;
  if (mode.endsWith('relative_minor') || mode.endsWith('relative_major')) return 0;
  if (mode === 'major_to_minor') return MODE_DN.has(deg) ? -1 : 0;
  return MODE_UP.has(deg) ? 1 : 0;
}

const near = (a, b) => Math.abs(a - b) < 1e-6;
const fails = [];
let ok = 0;

for (const c of CASES) {
  const A = extract(c.orig), B = extract(c.trans);
  const tag = `${c.name} [${c.kind} ${c.param}]`;
  const bad = (why) => fails.push({ tag, why });

  let shift = 0, mode = null, strip = false;
  if (c.kind === 'transpose') shift = c.param;
  else if (c.kind === 'mode') mode = c.param;
  else if (c.kind === 'combo') { shift = c.transpose; mode = c.mode; }
  else if (c.kind === 'strip') strip = true;

  if (A.error) { bad('原谱解析失败: ' + A.error); continue; }
  if (B.error) { bad('转换后解析失败: ' + B.error); continue; }
  if (A.notes.length !== B.notes.length) {
    bad(`音符数 ${A.notes.length} → ${B.notes.length}（吞音/多音）`); continue;
  }

  const key = parseKey(c.orig);
  const srcMinor = key ? key.minor : false;
  const noopMode = !!mode && (mode.startsWith('major_to') === srcMinor);
  if (noopMode && !shift && c.trans !== c.orig) {
    bad('源调式已等于目标调式，谱面却变了（no-op 必须逐字节相同）'); continue;
  }

  let err = null;
  for (let i = 0; i < A.notes.length && !err; i++) {
    const a = A.notes[i], b = B.notes[i];
    if (a.midis.length !== b.midis.length) { err = `第${i}个音的音数变了`; break; }
    if (a.start != null && b.start != null && (!near(a.start, b.start) || !near(a.dur, b.dur))) {
      err = `第${i}个音节奏变了 start ${a.start}→${b.start} dur ${a.dur}→${b.dur}`; break;
    }
    for (let k = 0; k < a.midis.length; k++) {
      const deg = key ? ((a.midis[k] - key.pc) % 12 + 12) % 12 : null;
      const want = shift + (deg === null ? 0 : expected(mode, srcMinor, deg));
      const d = b.midis[k] - a.midis[k];
      if (d !== want) {
        err = `第${i}个音 ${a.midis[k]}→${b.midis[k]}，差 ${d}（应 ${want}，音级 ${deg}）`;
        break;
      }
    }
  }
  if (err) { bad(err); continue; }

  const chordsStripped = strip || (!!mode && !noopMode &&
    (mode === 'major_to_minor' || mode === 'minor_to_major'));
  if (chordsStripped) {
    if (B.chords.length) { bad(`应清空和弦，却剩 ${B.chords.length} 个`); continue; }
  } else {
    if (A.chords.length !== B.chords.length) {
      bad(`和弦数 ${A.chords.length} → ${B.chords.length}`); continue;
    }
    const want = ((shift % 12) + 12) % 12;
    for (let i = 0; i < A.chords.length; i++) {
      const pa = chordParts(A.chords[i]), pb = chordParts(B.chords[i]);
      if (!pa || !pb) { err = `和弦 ${A.chords[i]} 解析不出`; break; }
      if (((pb.root - pa.root) % 12 + 12) % 12 !== want) {
        err = `和弦 ${A.chords[i]}→${B.chords[i]} 根音没跟着移`; break;
      }
      if ((pa.bass === null) !== (pb.bass === null)) {
        err = `和弦 ${A.chords[i]}→${B.chords[i]} 斜杠低音丢失/新增`; break;
      }
      if (pa.bass !== null && ((pb.bass - pa.bass) % 12 + 12) % 12 !== want) {
        err = `和弦 ${A.chords[i]}→${B.chords[i]} 斜杠低音没跟着移`; break;
      }
    }
  }
  if (err) { bad(err); continue; }
  ok++;
}

console.log(`abcjs 独立复核：${CASES.length} 例，通过 ${ok}，失败 ${fails.length}`);
for (const f of fails.slice(0, 20)) console.log(`  ✗ ${f.tag}: ${f.why}`);
process.exit(fails.length ? 1 : 0);
