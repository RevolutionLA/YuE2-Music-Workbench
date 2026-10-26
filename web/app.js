
const $ = (id) => document.getElementById(id);
// 带超时的 fetch：训练吃满 CPU 时请求可能长时间挂起，
// 按钮 disabled 后若 fetch 永不返回就永远点不动（只能刷新）——超时兜底必设
async function fetchT(url, opts = {}, timeoutMs = 45000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...opts, signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}
const $$ = (sel) => document.querySelectorAll(sel);

// ---------- 反代前缀自适配（经 dsh :3081/lab 访问时生效；7863 直访零影响） ----------
const LAB_BASE = location.pathname.startsWith("/lab") ? "/lab" : "";
if (LAB_BASE) {
  // 1) fetch/XHR 补前缀
  const _fetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    if (typeof input === "string" && input.startsWith("/") && !input.startsWith("//")) {
      input = LAB_BASE + input;
    }
    return _fetch(input, init);
  };
  const _open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, u, ...a) {
    if (typeof u === "string" && u.startsWith("/") && !u.startsWith("//")) u = LAB_BASE + u;
    return _open.call(this, m, u, ...a);
  };
  // 2) 历史播放坞等媒体的绝对地址补前缀
  new MutationObserver((muts) => {
    for (const m of muts) for (const n of m.addedNodes) {
      if (n.nodeType !== 1) continue;
      for (const el of [n, ...n.querySelectorAll("audio[src^='/'],source[src^='/'],img[src^='/'],a[href^='/']")]) {
        for (const attr of ["src", "href"]) {
          const v = el.getAttribute && el.getAttribute(attr);
          if (v && v.startsWith("/") && !v.startsWith("//")) el.setAttribute(attr, LAB_BASE + v);
        }
      }
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
}

// ---------- helpers ----------
// 播报给读屏用户。info 走 polite（等当前朗读告一段落），err 走 assertive（立刻打断）——
// 出错要马上知道，普通进度不该抢话。
function announce(msg, urgent) {
  const el = $(urgent ? "srAlert" : "srStatus");
  if (!el) return;
  // 同一个节点重复写同样的文本，读屏不会二次朗读；
  // 所以先清空再异步写入，强制触发一次变更。
  el.textContent = "";
  setTimeout(() => { el.textContent = msg; }, 30);
}

// 把底层异常翻译成"用户能据此行动"的一句话。
// 直接摆 err.message 的经典翻车：fetch 拿到 HTML 时 JSON.parse 抛
//   `Unexpected token '<', "<!DOCTYPE "... is not valid JSON`
// —— 用户看不懂，也猜不到"哦是网关没起来"。
// 这里只处理最常见的四类；其他异常原样返回（宁可露出原始信息，也不要编一个假原因）。
function friendlyErr(e) {
  const m = String((e && e.message) || e || "");
  if (/Failed to fetch|NetworkError|ERR_CONNECTION|Load failed/i.test(m))
    return "连不上网关（7863）。它可能没启动，或改过 app.py 后还没重启。";
  if (/is not valid JSON|Unexpected token/i.test(m))
    return "网关返回的不是接口数据（多半是没启动、或该接口在旧版本里还不存在）。";
  if (/aborted|AbortError|timeout/i.test(m))
    return "请求超时。任务可能还在后台跑，可去 ⑪ 任务管理查看。";
  if (/40[34]/.test(m))
    return "接口拒绝了这次请求（403/404）。请确认网关版本与服务状态。";
  if (/50\d/.test(m))
    return "网关内部出错（" + m.match(/50\d/)[0] + "）。详细信息在网关日志里。";
  return m.slice(0, 200);
}
function toast(msg, type="") {
  const t = document.createElement("div");
  t.className = "toast " + type;
  t.textContent = msg;
  $("toastWrap").appendChild(t);
  announce(msg, type === "err");
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 320); }, 3600);
}

// 所有 .status / .lint 面板的文字变化都自动播报，不用在每个赋值点手写 announce。
// 这样新增状态位时天然就有无障碍支持，不会漏——"机制"比"逐处记得写"可靠。
// 注意：要播报的是**这次真正发生变化的那个面板**，不能拿 querySelector 去猜
// （页面上常有好几个 .status/.lint，querySelector 只会返回 DOM 里第一个非空的，
// 结果就是"用户改了 A 面板，读屏念的是 B 面板"，比不播报更糟）。
// 节流 600ms：进度类文案高频刷新，逐条播报会把读屏刷成噪音。
(function watchStatusAnnouncements() {
  if (typeof MutationObserver === "undefined") return;
  const DELAY = 600;
  let timer = null, queued = null, last = 0;
  const flush = () => {
    timer = null; last = Date.now();
    const el = queued; queued = null;
    if (!el || !el.isConnected) return;
    const txt = (el.textContent || "").trim();
    if (!txt) return;
    // 带 err 样式的面板（.lint.err / .status.err）用 assertive 立刻播报，
    // 普通进度用 polite，避免把读屏刷成噪音。
    const urgent = el.classList.contains("err") ||
                   el.closest(".lint.err, .status.err") !== null;
    announce(txt.slice(0, 200), urgent);
  };
  new MutationObserver((muts) => {
    for (const m of muts) {
      // characterData 变化时 target 是文本节点，要往上找宿主元素
      let t = m.target;
      if (t && t.nodeType === 3) t = t.parentElement;
      if (!t || t.nodeType !== 1) continue;
      const panel = t.closest ? t.closest(".status, .lint") : null;
      if (!panel) continue;
      if (!(panel.textContent || "").trim()) continue;
      queued = panel;
      if (timer) continue;
      const wait = Math.max(0, DELAY - (Date.now() - last));
      timer = setTimeout(flush, wait);
    }
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
})();
function numOrNull(id) {
  const v = $(id).value.trim();
  if (!v) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}
// ---- 行内小表单：在触发按钮所在卡片下方展开一行控件，替代"连续几个 prompt()" ----
// prompt 要用户凭记忆敲准确的音色名、按看不见的格式敲数字，敲错一个字符只得到一句
// "不存在"。这里改成下拉给候选 + 数字框给范围 + 滑杆给即时读数，并且把「取消 /
// 同一按钮再点一次收起 / 同时只开一个 / 提交期间禁用防连点 / 失败留在原地可改」一次做掉。
function inlineForm(btn, spec, onSubmit) {
  const anchor = spec.anchor || btn.parentElement;
  const wasOpen = btn.getAttribute("data-form-open") === "1";
  document.querySelectorAll(".inline-form").forEach(x => x.remove());
  document.querySelectorAll("[data-form-open]").forEach(x => x.removeAttribute("data-form-open"));
  if (wasOpen) return;                       // 再点一次 = 收起
  btn.setAttribute("data-form-open", "1");
  const fields = spec.fields || [];
  const box = document.createElement("div");
  box.className = "inline-form";
  box.onclick = (ev) => ev.stopPropagation();
  let html = '<span class="if-title">' + escapeHtml(spec.title) + "</span>";
  fields.forEach(f => {
    html += '<label class="lbl" style="margin:0;" for="if_' + f.id + '">' + escapeHtml(f.label) + "</label>";
    if (f.kind === "select") {
      html += '<select id="if_' + f.id + '">' + f.options.map(o =>
        '<option value="' + escapeHtml(o.v) + '"' + (String(o.v) === String(f.value ?? "") ? " selected" : "") + ">" +
        escapeHtml(o.t) + "</option>").join("") + "</select>";
    } else if (f.kind === "range") {
      html += '<input type="range" id="if_' + f.id + '" min="' + (f.min ?? 0) + '" max="' + (f.max ?? 100) +
              '" step="' + (f.step ?? 5) + '" value="' + (f.value ?? 50) + '" aria-label="' + escapeHtml(f.label) + '" />' +
              '<span class="if-ro" id="if_' + f.id + '_ro"></span>';
    } else {
      html += '<input type="' + (f.kind === "number" ? "number" : "text") + '" id="if_' + f.id + '"' +
              (f.min !== undefined ? ' min="' + f.min + '"' : "") + (f.max !== undefined ? ' max="' + f.max + '"' : "") +
              ' maxlength="' + (f.maxlen || 60) + '" placeholder="' + escapeHtml(f.placeholder || "") +
              '" value="' + escapeHtml(f.value ?? "") + '" aria-label="' + escapeHtml(f.label) + '" />';
    }
  });
  if (spec.hint) html += '<span class="if-hint">' + escapeHtml(spec.hint) + "</span>";
  html += '<button class="primary" data-ok>' + escapeHtml(spec.okText || "确定") +
          '</button><button class="ghost" data-no>取消</button>';
  box.innerHTML = html;
  anchor.after(box);
  const get = (id) => box.querySelector("#if_" + id);
  fields.forEach(f => {
    if (f.kind !== "range" || !f.readout) return;
    const ro = get(f.id + "_ro"), el = get(f.id);
    const sync = () => { ro.textContent = f.readout(el.value); };
    el.oninput = sync; sync();
  });
  const ok = box.querySelector("[data-ok]"), no = box.querySelector("[data-no]");
  const close = () => { box.remove(); btn.removeAttribute("data-form-open"); };
  no.onclick = close;
  ok.onclick = async () => {
    const vals = {};
    fields.forEach(f => { const el = get(f.id); vals[f.id] = f.kind === "select" || f.kind === "range" ? el.value : String(el.value).trim(); });
    const bad = spec.validate ? spec.validate(vals) : null;
    if (bad) { toast(bad.msg || bad, "err"); if (bad.focus && get(bad.focus)) get(bad.focus).focus(); return; }
    const oldTxt = ok.textContent;
    ok.disabled = true; no.disabled = true; ok.textContent = spec.busyText || "处理中…";
    try { await onSubmit(vals); close(); }
    catch (err) {
      toast(friendlyErr(err), "err");
      ok.disabled = false; no.disabled = false; ok.textContent = oldTxt;   // 失败时表单留在原地，改完就能再提交
    }
  };
  const first = get(fields.length ? fields[0].id : "") || ok;
  if (first && first.focus) setTimeout(() => first.focus(), 30);
}
function timeAgo(ts) {
  if (!ts) return "";
  const diff = Date.now() - new Date(ts).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return m + " 分钟前";
  const h = Math.floor(m / 60);
  if (h < 24) return h + " 小时前";
  return Math.floor(h / 24) + " 天前";
}
function fmtBytes(n) {
  if (n == null) return "";
  if (n < 1024*1024) return (n/1024).toFixed(1) + " KB";
  return (n/1024/1024).toFixed(2) + " MB";
}
// 任务 ID 徽标：ID 全局唯一（时间戳+随机），且就是 output/ 下的产物文件名。
// 任务名可以重复，ID 不能——定位问题、删错风险都靠它。
function idTag(id) {
  if (!id) return "";
  return `<span class="tid" tabindex="0" role="button" data-id="${escapeHtml(id)}" `
       + `aria-label="任务 ID ${escapeHtml(id)}，回车复制" title="任务 ID（唯一），点击/回车复制">ID ${escapeHtml(id)}</span>`;
}
// 捕获阶段拦截：历史行整行可点（播放/回填），必须赶在行的 onclick 之前吃掉这次点击
document.addEventListener("click", (ev) => {
  const el = ev.target.closest && ev.target.closest(".tid");
  if (!el) return;
  ev.stopPropagation();
  ev.preventDefault();
  copyId(el.dataset.id || "");
}, true);
// 键盘可达：span 加 role=button 后必须自己响应 Enter/空格，否则纯键盘用户复制不了 ID
document.addEventListener("keydown", (ev) => {
  if (ev.key !== "Enter" && ev.key !== " ") return;
  const el = ev.target.closest && ev.target.closest(".tid");
  if (!el) return;
  ev.preventDefault();
  ev.stopPropagation();
  copyId(el.dataset.id || "");
}, true);
function copyId(id) {
  const done = () => toast("已复制 ID：" + id, "ok");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(id).then(done, () => fallbackCopy(id, done));
  } else fallbackCopy(id, done);
}
// 「取出去」的统一出口：页面之间不代填，用户要带去另一页就自己复制粘贴。
function copyOut(text, what) {
  const t = String(text || "");
  if (!t.trim()) { toast("这条产出里没有" + what, "err"); return; }
  const done = () => toast(what + "已复制（" + t.length + " 字）——粘到哪一页由你决定", "ok");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(t).then(done, () => fallbackCopy(t, done));
  } else fallbackCopy(t, done);
}
// 显式创作接力：用户点按钮才把内容带到目标页。
// 这不同于旧的隐式跨页覆盖——切页、刷新、后台渲染都不会触发；目标已有不同内容时必须确认。
function sendTextToTab(value, destId, tab, what, destLabel, afterText) {
  const text = String(value || "").trim();
  if (!text) { toast(what + "是空的，没有可带过去的内容", "err"); return false; }
  const dest = $(destId);
  if (!dest) { toast("找不到目标输入框：" + destLabel, "err"); return false; }
  const old = String(dest.value || "").trim();
  if (old && old !== text && !confirm(
    destLabel + "已有不同的" + what + "（" + old.length + " 字符）。\n"
    + "用当前这份替换？点「取消」不会改动任何内容。")) return false;
  if (old !== text) {
    dest.value = text;
    dest.dispatchEvent(new Event("input", { bubbles: true }));
    dest.dispatchEvent(new Event("change", { bubbles: true }));
  }
  switchTab(tab);
  reveal(dest, "center");
  try { dest.focus({ preventScroll: true }); } catch { try { dest.focus(); } catch {} }
  toast(afterText || ("已把" + what + "带到" + destLabel), "ok");
  return true;
}
function gotoSharedScore() {
  const text = ($("abc").value || "").trim();
  if (!text) { toast("谱框是空的，没有可带过去的内容", "err"); return false; }
  switchTab("scoreSing");
  reveal($("abc"), "center");
  toast("③⑤共用这份谱框：它已在⑤可见；填好 Style 与歌词后仍由你点击生成", "ok");
  return true;
}
function fallbackCopy(text, done) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  ta.remove();
  // execCommand 失败不抛错、只返回 false：不看返回值就会谎报「已复制」
  // （非安全上下文 http://局域网IP 下 clipboard API 根本不存在，只能走这条路）
  if (ok) done();
  else { toast("复制失败，请手动选中 ID：" + text, "err"); }
}
// 下载命名：任务名称 + 年月日（如「稻香 260921」）
function dlName(name, ext = "wav") {
  const d = new Date();
  const ymd = String(d.getFullYear()).slice(2) + String(d.getMonth() + 1).padStart(2, "0") + String(d.getDate()).padStart(2, "0");
  const base = String(name || "").trim().replace(/[\\/:*?"<>|]+/g, "_").slice(0, 80) || "yue2";
  return base + " " + ymd + "." + ext;
}
async function jfetch(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let detail = await r.text();
    try { detail = JSON.parse(detail).detail || detail; } catch {}
    throw new Error(String(detail).slice(0, 300));
  }
  return r;
}

// ---------- state ----------
let lastBlob = null, lastUrl = null;
let lastTaskName = "";  // 当前播放坞音频对应的任务名称（用于下载命名）
let curTab = "style";
let currentModels = [];
// ① 右栏当前显示哪个视图（preset = 曲风配方库 / six = 六要素装配台），localStorage 持久化
const LS_ASM_MODE = "la_asm_mode";
let asmMode = "preset";

// ---------- theme（明暗模式，localStorage 持久化）----------
const THEME_KEY = "la_theme";
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  const dark = t === "dark";
  // 左：当前模式（标签本身说"现在是什么"），右：可切换到的目标（胶囊说"点了会变成什么"）
  const lab = $("themeLabel"), pill = $("themePill");
  if (lab) lab.textContent = dark ? "深色" : "浅色";
  if (pill) pill.textContent = dark ? "切到浅色" : "切到深色";
  try { localStorage.setItem(THEME_KEY, t); } catch {}
  // 明暗切换后重算主题色派生变量（applyAccent 内部读当前 data-theme）
  if (typeof applyAccent === "function") applyAccent($("accentPicker").value || "#0969da");
  // 通知父窗口（dsh 侧栏切换主题时）同步主题色派生
  try { if (window.parent && window.parent !== window) window.parent.postMessage({ type: "yue2-theme", theme: t }, "*"); } catch {}
}
// 初始化主题：若内嵌在 dsh 里（同源 iframe），优先按父窗口 body 的实际背景亮度判定——
// 与运行时轮询（syncFromParent）同一地面真相，硬刷新也不会先闪旧主题。
// 不用属性猜测（dsh 浅色时可能误判）也不用 localStorage（存的是上次会话的旧值）。
applyTheme((() => {
  try {
    if (window.parent && window.parent !== window) {
      const pdoc = window.parent.document;
      const pb = pdoc && pdoc.body;
      if (pb && pdoc.defaultView) {
        const bg = pdoc.defaultView.getComputedStyle(pb).backgroundColor;
        const m = bg.match(/(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
        if (m) return (0.2126 * +m[1] + 0.7152 * +m[2] + 0.0722 * +m[3]) / 255 < 0.5 ? "dark" : "light";
      }
    }
  } catch {}
  try { return localStorage.getItem(THEME_KEY) || "light"; } catch { return "light"; }
})());
$("themeBtn").onclick = () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
};
// 监听 dsh 侧栏的主题通知（postMessage）：仅独立访问时生效。
// 内嵌 dsh 时忽略——顶层轮询（syncFromParent）读父窗口真实背景色才是地面真相，
// 若 client.js 侧判定（属性猜测）与实际不符，两源会来回打架。
if (!(window.parent && window.parent !== window)) {
  window.addEventListener("message", (e) => {
    const d = e.data || {};
    if (d.type === "yue2-theme" && (d.theme === "dark" || d.theme === "light")) {
      applyTheme(d.theme);  // applyTheme 内部会重算 applyAccent，并同步持久化与主题色
    }
  });
}

// 终极兜底：iframe 内主动轮询父窗口（dsh）的实际主题并跟随。
// 前置事实（已实测确认）：/lab/ 与 dsh 主文档同源（同 127.0.0.1:3081），
// 因此可直接读 parent.document，不依赖 dsh 侧 client.js 是否发 postMessage。
(function () {
  if (!(window.parent && window.parent !== window)) return;  // 独立打开时不轮询
  let last = null;
  function syncFromParent() {
    try {
      const pd = window.parent.document;
      const pb = pd && pd.body;
      let t = null;
      // 首选地面真相：父窗口 body 的实际背景亮度（对任何主题机制都成立，
      // 不依赖对 dsh 属性名的猜测）。注意必须用父窗口自身的 getComputedStyle——
      // iframe 的 getComputedStyle 对跨文档元素拿不到样式
      if (pb && pd.defaultView) {
        const bg = pd.defaultView.getComputedStyle(pb).backgroundColor;
        const m = bg.match(/(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
        if (m) {
          const lum = (0.2126 * +m[1] + 0.7152 * +m[2] + 0.0722 * +m[3]) / 255;
          t = lum < 0.5 ? "dark" : "light";
        }
      }
      // 回退：属性检查（背景拿不到时）
      if (!t) {
        const ph = pd && pd.documentElement;
        const dark = (ph && (ph.classList.contains("dark") || ph.dataset.theme === "dark"))
          || (pb && pb.hasAttribute("data-ds-dark-theme"));
        t = dark ? "dark" : "light";
      }
      if (t !== last) {
        last = t;
        if (document.documentElement.dataset.theme !== t) applyTheme(t);
      }
    } catch {}
  }
  syncFromParent();
  setInterval(syncFromParent, 800);   // 轻量属性读取，800ms 足够跟手
})();

// ---------- 主题色自选：取色器 → 派生一组强调色变量（localStorage 持久化） ----------
const ACCENT_KEY = "la_accent";
// 默认强调色从"琥珀铜 #a85a14"改为"GitHub 蓝 #0969da"（v10.9.0 数据密集改版）。
// 老用户 localStorage 里还存着旧默认值，不迁就等于"新配色永远看不到"，所以做一次性搬家：
// 只替换这两个已知的旧默认值（#b0651c 有对比度问题、#a85a14 是旧暖色默认），
// 用户自己挑的其他颜色一律不动。
const ACCENT_LEGACY = { "#b0651c": "#0969da", "#a85a14": "#0969da" };
const ACCENT_DEFAULT = "#0969da";
function loadAccent() {
  let v = "";
  try { v = localStorage.getItem(ACCENT_KEY) || ""; } catch {}
  if (!v) return ACCENT_DEFAULT;
  const fixed = ACCENT_LEGACY[v.toLowerCase()];
  if (fixed) {
    try { localStorage.setItem(ACCENT_KEY, fixed); } catch {}
    return fixed;
  }
  return v;
}
function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function rgbToHex(r, g, b) {
  const c = (v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0");
  return "#" + c(r) + c(g) + c(b);
}
function mix(hex, target, k) {  // k=0 原色，k=1 目标色
  const [r, g, b] = hexToRgb(hex), [tr, tg, tb] = hexToRgb(target);
  return rgbToHex(r + (tr - r) * k, g + (tg - g) * k, b + (tb - b) * k);
}
function rgba(hex, a) {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r},${g},${b},${a})`;
}
function applyAccent(hex) {
  const root = document.documentElement;
  const dark = root.dataset.theme === "dark";
  // 亮部/暗部：向白/黑各混 12%；chip 浅色模式需要更亮的两个端点
  const hi = mix(hex, "#ffffff", dark ? 0.10 : 0.12);
  const lo = mix(hex, "#000000", dark ? 0.10 : 0.16);
  const vars = {
    "--acc": hex,
    // 自定义主题色时，"铜色当文字用"的那档必须跟着走：浅色往黑混（压深到 5:1 以上），
    // 深色往白混（提亮到 7:1 以上）。漏掉这一档的话，用户换主题色就把所有
    // 琥珀色文字（页签选中态、计时、链接）重新变回看不清的 3:1。
    "--acc-text": dark ? mix(hex, "#ffffff", 0.35) : mix(hex, "#000000", 0.25),
    "--acc-dim": mix(hex, "#000000", 0.25),
    "--acc-soft": rgba(hex, dark ? 0.18 : 0.12),
    "--acc-glow": rgba(mix(hex, "#ffffff", 0.25), dark ? 0.28 : 0.35),
    "--acc-hi": hi,
    "--acc-lo": lo,
    "--acc-soft2": rgba(mix(hex, "#ffffff", 0.2), dark ? 0.3 : 0.22),
    "--acc-chip-hi": dark ? mix(hex, "#ffffff", 0.42) : mix(hex, "#ffffff", 0.55),
    "--acc-chip-lo": dark ? mix(hex, "#ffffff", 0.06) : mix(hex, "#ffffff", 0.32),
    // 主按钮：墨色刻字要压在"亮一档的键面"上，所以这两个端点必须往白里走（不能往黑里走，
    // 否则 #1d1003 的刻字就沉进深铜里，回到 3.2:1）。
    // 深色模式原来直接把 --acc-chip-hi 设成用户选的原色，于是选了石墨/绛红这类暗色时，
    // 墨字压暗底只有 2.9:1（全预设都不达标）。现在亮色走原色的亮端（白字够），
    // 深色统一走 42% 提亮端（墨字够），两条路都收在 4.5:1 以上。
    "--acc-btn-hi": mix(hex, "#ffffff", dark ? 0.34 : 0.30),
    "--acc-btn-lo": mix(hex, "#ffffff", dark ? 0.20 : 0.12),
  };
  for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);
  $("accentPicker").value = hex;
  try { localStorage.setItem(ACCENT_KEY, hex); } catch {}
}
// 预设色板：色相都压在低饱和区间（蓝/绿/紫/红/青/灰），刻意避开"AI 味"的高饱和渐变。
// 在这个有大量卡片与细线的数据密集界面上，高饱和原色会抢走全部注意力，看着廉价。
// 每一档只存一个主色，其余（文字/键面/辉光/芯片）由 applyAccent 按同一套规则派生。
// 硬约束：每个主色配白字必须 ≥4.5:1（主按钮就是白字压主色），
// 这几个值都是照着这条线挑的，调色时先用对比度算一遍再改。
const ACCENT_PRESETS = [
  ["#0969da", "湖蓝"],     // 默认：GitHub 蓝（白字 4.63:1）
  ["#1a7f37", "松绿"],     // 4.63:1
  ["#8250df", "紫藤"],     // 5.62:1
  ["#cf222e", "绯红"],     // 4.83:1
  ["#0f766e", "青瓷"],     // 5.14:1
  ["#9a6700", "赭黄"],     // 5.17:1
  ["#57606a", "石墨"],     // 6.44:1
  ["#6639ba", "深紫"],     // 7.21:1
];
function paintAccentSwatches() {
  const box = $("accentSwatches");
  if (!box) return;
  const cur = (($("accentPicker") || {}).value || ACCENT_PRESETS[0][0]).toLowerCase();
  box.innerHTML = "";
  ACCENT_PRESETS.forEach(([hex, name]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "swatch" + (hex.toLowerCase() === cur ? " active" : "");
    b.title = name + " " + hex;
    // 色片本身用"亮一档 → 本色"的渐变：和真正的主按钮是同一组端点，
    // 预览看到的就是点下去会得到的键面，不是一块平涂的色标。
    b.innerHTML = '<i style="background:linear-gradient(180deg,' + mix(hex, "#ffffff", 0.22) + ',' + hex + ')"></i><span>' + name + "</span>";
    b.onclick = () => { applyAccent(hex); paintAccentSwatches(); };
    box.appendChild(b);
  });
}
$("accentPicker").addEventListener("input", (e) => { applyAccent(e.target.value); paintAccentSwatches(); });
applyAccent(loadAccent());
paintAccentSwatches();

// ---------- 动态 favicon：随主题色绘制；空闲/生成中/出错三态区分 ----------
// 空闲：主题色圆角方块 + 白音符；生成中：转圈进度环动画 + 音符；出错：红底 + 白叉。
// 用 Canvas 每帧重绘 dataURL 替换 <link rel="icon">，无需外部资源。
const Favicon = (() => {
  const link = $("favicon");
  const cv = document.createElement("canvas");
  cv.width = cv.height = 64;
  const ctx = cv.getContext("2d");
  let mode = "idle";        // idle | busy | error
  let anim = null, t0 = 0;

  function accent() {
    return getComputedStyle(document.documentElement).getPropertyValue("--acc").trim() || "#0969da";
  }
  function draw(ts) {
    const a = accent();
    ctx.clearRect(0, 0, 64, 64);
    // 底：圆角方块
    ctx.fillStyle = mode === "error" ? "#b23a2e" : a;
    roundRect(ctx, 2, 2, 60, 60, 14);
    ctx.fill();
    // 生成中：进度环（缺口随时间旋转）
    if (mode === "busy") {
      const p = ((ts - t0) / 1200) % 1;
      ctx.strokeStyle = "rgba(255,255,255,.95)";
      ctx.lineWidth = 6;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.arc(32, 32, 24, p * Math.PI * 2, p * Math.PI * 2 + Math.PI * 1.35);
      ctx.stroke();
    }
    // 音符（busy 时压暗让进度环醒目）
    ctx.fillStyle = mode === "busy" ? "rgba(255,255,255,.45)" : "#fff";
    ctx.beginPath();
    // 双音符 stem
    ctx.fillRect(24, 16, 3.2, 26);
    ctx.fillRect(37, 12, 3.2, 26);
    ctx.fillRect(24, 13, 16.2, 4);   // 横梁
    // 两个符头
    ctx.beginPath(); ctx.ellipse(21, 44, 7, 5.4, -0.35, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(34, 40, 7, 5.4, -0.35, 0, Math.PI * 2); ctx.fill();
    // 出错：白叉
    if (mode === "error") {
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 7;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(20, 20); ctx.lineTo(44, 44);
      ctx.moveTo(44, 20); ctx.lineTo(20, 44);
      ctx.stroke();
    }
    link.href = cv.toDataURL("image/png");
  }
  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }
  function set(m) {
    if (m === mode && m !== "busy") return;
    mode = m;
    if (anim) { cancelAnimationFrame(anim); anim = null; }
    if (m === "busy") {
      t0 = performance.now();
      // rAF 循环但节流至 ~4fps：toDataURL 每帧序列化代价高，小时级挂机不必满帧刷新
      let last = 0;
      const loop = (ts) => {
        if (ts - last >= 250) { last = ts; draw(ts); }
        anim = requestAnimationFrame(loop);
      };
      anim = requestAnimationFrame(loop);
    }
    else draw(0);
  }
  draw(0);
  return { set };
})();

// ---------- tabs ----------
// hash 同步收在 switchTab 内部：无论是点页签还是程序化跳转（回填/播放/去换声…），
// 界面状态与 hash 始终一致，避免 stale hash 触发 hashchange 把页面拽回旧页签。
$$(".tab").forEach(t => t.onclick = () => switchTab(t.dataset.tab));
// v2.0 拆页共用：② 与 ⑤ 共用同一份 #genForm（生成表单），③ 与 ⑤ 共用同一份 #abcBlock（谱框）。
// 实现方式是"把节点搬进目标页的槽位"，而不是复制一份 DOM 和一套代码——
// 生成链路里已经修过的坑（CoT 档位纠正、歌词护栏、撤销栈、切页后进度续看）各写两遍必然分叉。
function moveInto(node, slotId) {
  const slot = $(slotId);
  if (node && slot && node.parentElement !== slot) slot.appendChild(node);
}
function switchTab(name) {
  curTab = name;
  $$(".tab").forEach(x => x.classList.toggle("active", x.dataset.tab === name));
  // 窄屏（≤1180px）侧栏是单行横向滚动的，13 个页签装不下一屏：
  // 切换后把当前这个滚进视野，否则人点了却不知道自己在哪一页。
  // block:'nearest' 保证已经在垂直方向可见时不会顺手把整页滚走。
  const act = document.querySelector('.side nav .tab.active');
  if (act) { try { act.scrollIntoView({ inline: "nearest", block: "nearest" }); } catch {} }
  $$(".panel-tab").forEach(p => p.style.display = p.id === "tab-" + name ? "" : "none");
  // 共用件归位：先搬生成表单，再往它内部的谱槽放 #abcBlock（⑤ 用右侧乐谱区，其余页回 ③）
  const scoreMode = name === "scoreSing";
  const gen = $("genForm");
  if (gen) { moveInto(gen, scoreMode ? "genSlotScore" : "genSlotSing"); gen.dataset.mode = scoreMode ? "score" : "sing"; }
  moveInto($("abcBlock"), scoreMode ? "abcSlotSing" : "abcSlotScore");
  // 曲风输入框跟着当前页走：在 ① 就回 ① 的槽，在 ②⑤ 就进生成表单左栏顶部。
  // 只有一份 DOM，所以"① 装配出来的 Style"和"②⑤ 手写的 Style"必然是同一个值。
  const ss = $("scoreSection"); if (ss) ss.hidden = !scoreMode;
  const fb = $("flowBar"); if (fb) fb.hidden = !scoreMode;
  // ② 的关系条讲两段（Style + 歌词），⑤ 讲三段（+ 乐谱）。两条互斥，不同时出现。
  const fbs = $("flowBarSing"); if (fbs) fbs.hidden = !(name === "sing");
  if (scoreMode && typeof updateLyricBarHint === "function") updateLyricBarHint();
  if (typeof paintScoreModeTag === "function") paintScoreModeTag();
  // 进入 ⑤ 时按当前共用谱重放一次锁档（② 不设锁，故仅 ⑤ 需要）；保持"有谱即按谱定档"的设计。
  // ② 上残留的 #abc 内容不会污染 ② 的档位，但切到 ⑤ 必须让它重新接管。
  if (scoreMode && lastAbcAnalysis && lastAbcAnalysis.cot_suggested && typeof renderAbcAnalysis === "function") renderAbcAnalysis(lastAbcAnalysis);
  if (name === "sing" || name === "scoreSing") syncStylePreview();
  try { if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name); } catch {}
  // 内嵌模式（dsh iframe）：通知父窗口同步侧栏页签高亮
  try { if (window.parent && window.parent !== window) window.parent.postMessage({ type: "yue2-lab-tab", tab: name }, "*"); } catch {}
  // 进入历史页时自动刷新，确保看到的是最新列表
  if (name === "history" && typeof renderHistory === "function") renderHistory();
  // 进入声音四页时刷新各自的任务列表 / 音色下拉
  if (name === "convert" && typeof renderRvc === "function") renderRvc();
  // ⑨ 的音源、⑦ 的训练素材都从服务器现取现列：进页面就是最新的一批，
  // 不依赖别的页面刚刚把什么塞进来。
  if (name === "convert" && typeof paintSrcPick === "function") paintSrcPick("rvcSrcPick", "— 不用服务器素材，用上面上传的文件 —");
  if (name === "train" && typeof paintSrcPick === "function") paintSrcPick("trainSrcPick", "");
  if (name === "train" && typeof renderRvcTrainList === "function") renderRvcTrainList();
  if (name === "voices") {
    if (typeof renderRvcModels === "function") renderRvcModels();
    setVoiceTab();
  }
  if (name === "sep" && typeof loadSepList === "function") loadSepList();
  if (name === "ailab" && typeof loadAiSessions === "function") loadAiSessions();
  // 创作四页：曲风配方摘要 / 歌词候选 / 乐谱库与导出归档
  if (name === "style" && typeof updateAsmHint === "function") { updateAsmHint(); updateStyleChecks(); }
  if (name === "score") {
    if (typeof loadScoreList === "function") loadScoreList();
    if (typeof loadScorePicks === "function") loadScorePicks();
  }
  if (name === "scoreSing" && typeof loadScorePicker === "function") loadScorePicker();
  // ④ 乐谱加工：进页面时按本页源谱的调号收窄调式选项（源谱是这一页自己载入或粘贴的）
  if (name === "scoreEdit" && typeof seSyncModeOptions === "function") seSyncModeOptions();
  // 设置页：进页面就把健康灯/模型校验/引擎模式刷一遍，不看陈旧读数
  if (name === "settings") {
    if (typeof refreshHealth === "function") refreshHealth();
    if (typeof refreshModelVerify === "function") refreshModelVerify();
    if (typeof loadModels === "function") loadModels();
    // LoRA 清单只在进 ⑫ 时读：cpp/lora 是目录扫描，放启动路径上只会让首屏多等一次 IO
    if (typeof loadLoras === "function") loadLoras(true);
    if (typeof refreshCpuFallbackSetting === "function") refreshCpuFallbackSetting();
    if (typeof refreshVaeWindowSetting === "function") refreshVaeWindowSetting();
    if (typeof loadCapabilities === "function") loadCapabilities();
    if (typeof refreshDeepseekKey === "function") refreshDeepseekKey();
  }
  // ⑬ 关于：进页面刷新系统信息（独立页，只放只读信息）
  if (name === "about" && typeof loadSysInfo === "function") loadSysInfo();
  if (name === "lrc") {
    if (typeof loadLrcList === "function") loadLrcList();
    if (typeof loadLrcPicks === "function") loadLrcPicks();
  }
  // 换页＝换一屏内容，必须回到顶部。以前进 ① 会直接停在页面最下面：
  // 底部那条粘底产出条撑长了文档，加上浏览器刷新时的滚动恢复，落点就跑到底了。
  // 不归零的话用户打开就看见"下半页"，而页面的标题和左右两栏的关系全在上方，第一眼看不懂。
  scrollPageTop();
  // 换页后左右栏内容变了，可滚动状态要重算（渐隐提示跟着更新）
  requestAnimationFrame(syncScrollAffordance);
}

// 「下面还有内容」的提示：给每个可滚动的 .pane-body 打两个类——
//   .can-scroll = 内容确实超出了可视区；.at-end = 已经滚到底。
// CSS 只在「可滚 且 未到底」时加底部渐隐，所以不会出现"已经在底部还提示下面有"的假信号。
// 用 ResizeObserver 而不是只监听 window.resize：栏内内容变化（如六格/配方展开）
// 也会改变可滚状态，而那种变化不触发 window.resize。
function syncScrollAffordance() {
  $$(".grid.compose .pane-body").forEach(b => {
    const over = b.scrollHeight - b.clientHeight;
    b.classList.toggle("can-scroll", over > 2);
    b.classList.toggle("at-end", b.scrollTop >= over - 2);
  });
}
if (typeof ResizeObserver !== "undefined") {
  const _ro = new ResizeObserver(() => syncScrollAffordance());
  document.addEventListener("DOMContentLoaded", () => {
    $$(".grid.compose .pane-body").forEach(b => _ro.observe(b));
    $$(".grid.compose .pane-body").forEach(b => b.addEventListener("scroll", syncScrollAffordance, { passive: true }));
    window.addEventListener("resize", syncScrollAffordance, { passive: true });
    syncScrollAffordance();
  });
}

// 页脚说明文字被单行省略号截断时，把完整文案挂到 title 上 —— 截断本身可以接受
// （页脚是动作区，不该被两行解释顶高），但"看得见被截了 + 悬停能读全"是底线。
// 写在 sync 之后是因为是否截断取决于当前栏宽，栏宽会随窗口/分栏变化。
function syncFootHintTitles() {
  $$(".pane-foot .hint-xs").forEach(el => {
    if (el.scrollWidth > el.clientWidth + 1) {
      if (!el.title) { el.title = (el.textContent || "").trim(); el.dataset.autoTitle = "1"; }
    } else if (el.dataset.autoTitle === "1") {
      el.removeAttribute("title"); delete el.dataset.autoTitle;
    }
  });
}
document.addEventListener("DOMContentLoaded", () => {
  syncFootHintTitles();
  window.addEventListener("resize", () => requestAnimationFrame(syncFootHintTitles), { passive: true });
});

// 归零主滚动条 + 各栏内滚动条（宽屏下每栏各自可滚，只归零 window 没用）
function scrollPageTop() {
  try { window.scrollTo(0, 0); } catch {}
  try { document.documentElement.scrollTop = 0; document.body.scrollTop = 0; } catch {}
  document.querySelectorAll(".panel-tab:not([style*='display: none']).panel-tab .pane-body").forEach(b => { try { b.scrollTop = 0; } catch {} });
}

// ---------- 曲风库与装配台 ----------
// 词表来源（三处，都可复查）：
//   · YuE 官方 top200 标签表 genre/instrument/mood/gender/timbre —— skill yue2-song-craft 的
//     assets/top200-tags-official.json（模型训练时见过的词，优先用这些词最稳）
//   · 官方六要素写法与精选词表 —— skill yue2-song-craft 的 references/style-guide.md
//   · 本机落地裁定 —— skill yue2-craft-bridge 的 §4.1 硬规则（语言放最前、2–12 短语、
//     禁 epic/emotional/beautiful/amazing/masterpiece/perfect 这类不带可执行信息的词）
// 因此下面情绪词表刻意不收录那六个被裁定为"空词"的形容词；曲风名尽量贴着官方 genre 表。
const LANGS = [
  ["Mandarin", "普通话"], ["Cantonese", "粤语"], ["English", "英语"],
  ["Japanese", "日语"], ["Korean", "韩语"], ["Spanish", "西班牙语"],
  ["Instrumental", "纯音乐（无人声）"],
];
const VOICES = [
  ["clear female vocal", "女声"], ["clear male vocal", "男声"],
  ["female and male duet vocal", "男女对唱"], ["children's vocal", "童声"],
  ["mixed choir vocal", "合唱"], ["", "纯音乐（不写人声）"],
];
// 四张词表刻意互不重复：同一个词既出现在「情绪」又出现在「人声音色」，
// 用户在两格里各点一次就会拼出 "tender ... tender vocal" 这种重复短语，界面上也看不出差别。
const TIMBRES = ["airy", "warm", "breathy", "bright", "clear", "smooth", "mellow", "velvety",
  "husky", "raspy", "gravelly", "powerful", "soaring", "soft", "tender", "sweet", "soulful",
  "youthful", "mature", "raw", "gritty", "nasal", "high-pitched", "low-pitched", "deep",
  "whispery", "polished", "shimmering", "commanding", "delicate",
  // 曲风配方里还会用到的唱法色彩词（官方 timbre 表内均有同类写法）
  "gentle", "expressive", "autotuned", "harsh", "screamed", "guttural", "shouty",
  "piercing", "sharp", "weathered"];
const MOODS = ["uplifting", "energetic", "happy", "joyful", "cheerful", "playful", "romantic",
  "passionate", "heartfelt", "intimate", "longing", "melancholic", "nostalgic", "bittersweet",
  "sad", "dreamy", "calm", "relaxing", "soothing", "hopeful", "inspiring", "empowering", "confident",
  "rebellious", "defiant", "dark", "moody", "intense", "aggressive", "furious", "brooding", "wistful",
  "introspective", "reflective", "pensive", "mysterious", "atmospheric", "ethereal", "groovy",
  "festive", "triumphant", "dramatic", "elegant", "sentimental", "spiritual", "cool",
  // 曲风配方要用到的补充词（同样刻意避开本机裁定为"空词"的那六个形容词）
  "sensual", "angsty", "heroic", "brutal", "peaceful", "meditative", "grateful",
  "experimental"];
const INSTRS = [
  // 键盘 / 吉他 / 贝斯 / 鼓
  "piano", "acoustic piano", "electric piano", "grand piano", "harpsichord", "organ", "accordion", "synth",
  "acoustic guitar", "nylon-string guitar", "electric guitar", "pedal steel guitar", "12-string guitar",
  "bass guitar", "upright bass", "synth bass", "808 bass", "funky bass",
  "drums", "acoustic drums", "drum machine", "brushed drums", "synth drums", "percussion", "tambourine",
  "maracas", "congas", "bongos", "pandeira", "tabla", "turntables", "glockenspiel", "vibraphone", "marimba", "mallets",
  // 管弦 / 铜管 / 木管
  "strings", "violin", "cello", "harp", "brass", "trumpet", "trombone", "saxophone", "alto saxophone",
  "oboe", "flute", "clarinet", "choir", "bell",
  // 民族与原声色彩
  "guzheng", "erhu", "pipa", "dizi", "xiao", "suona", "yangqin", "Chinese zither",
  "koto", "shakuhachi", "sitar", "oud", "qanun", "dholak", "banjo", "mandolin", "ukulele",
  "harmonica", "steel pan", "bagpipes", "fiddle", "dobro",
];
const FEELS = ["slow", "moderate tempo", "mid-tempo", "upbeat", "fast-paced", "ballad tempo",
  "danceable", "groove-driven", "swinging", "syncopated", "laid-back", "driving", "hypnotic",
  "unhurried phrasing", "four-on-the-floor", "half-time beat", "double-time feel", "6/8 feel", "waltz",
  // 配方里出现最多的两种「节奏之外」的时间处理：自由节拍与渐建
  "rubato", "slow build", "building", "drifting",
  // 下面这些是具体曲风自带的律动名（雷鬼的一拍落地、trap 的半速 breakdown…）。
  // 它们以前只写在配方里、进不了词表，于是用户点卡片后那一格永远处于"看不见"状态，
  // 想取消选中都办不到——现在全部进表，芯片一律点亮可见。
  "quiet-loud dynamics", "start-stop dynamics", "stomping", "offbeat upstrokes",
  "boom bap drums", "blast beats", "half-time breakdown", "dembow rhythm", "one-drop rhythm",
  "interlocking grooves"];
const KEYS = ["", "C major", "G major", "D major", "A major", "F major", "B♭ major", "E♭ major",
  "A minor", "E minor", "D minor", "C minor", "G minor", "F♯ minor", "B minor"];

// ---- 候选词的中文标签 ----
// 模型只吃英文（官方词表就是英文），但用户看英文要靠猜。所以芯片上「中文为主、英文为辅」：
// 中文解决"这词什么意思"，英文小字解决"它到底被写成什么样"（改词、对齐官方写法时要用）。
// 送进 Style 的真值永远是英文，中文一个字都不会进模型。
const W_ZH = {
  // 情绪
  uplifting: "振奋", energetic: "有劲", happy: "快乐", joyful: "喜悦", cheerful: "明快",
  playful: "俏皮", romantic: "浪漫", passionate: "热情", heartfelt: "真挚", intimate: "私密",
  longing: "眷恋", melancholic: "忧郁", nostalgic: "怀旧", bittersweet: "苦乐交织", sad: "悲伤",
  dreamy: "梦幻", calm: "平静", relaxing: "松弛", soothing: "抚慰", hopeful: "希望",
  inspiring: "励志", empowering: "给力", confident: "自信", rebellious: "叛逆", defiant: "不屈",
  dark: "暗沉", moody: "阴郁", intense: "浓烈", aggressive: "凌厉", furious: "暴烈",
  brooding: "沉郁", introspective: "内省", reflective: "回望", pensive: "沉思",
  mysterious: "神秘", atmospheric: "氛围感", ethereal: "空灵", groovy: "律动", festive: "欢庆",
  triumphant: "昂扬", dramatic: "戏剧", elegant: "雅致", sentimental: "感伤", spiritual: "灵性",
  cool: "冷峻", sensual: "性感", angsty: "焦躁", heroic: "英武", brutal: "残暴",
  peaceful: "安宁", meditative: "冥想", grateful: "感恩", experimental: "实验", wistful: "怅惘",
  // 人声音色
  airy: "气声", warm: "温暖", breathy: "气音", bright: "明亮", clear: "清亮", smooth: "顺滑",
  mellow: "醇厚", velvety: "丝绒", husky: "沙哑", raspy: "磨砂", gravelly: "沙砾",
  powerful: "有力", soaring: "高亢", soft: "柔和", tender: "温柔", sweet: "甜美",
  soulful: "灵歌味", youthful: "青春", mature: "成熟", raw: "生猛", gritty: "粗粝",
  nasal: "鼻音", "high-pitched": "偏高", "low-pitched": "偏低", deep: "低沉", whispery: "耳语",
  polished: "打磨", shimmering: "闪亮", commanding: "有威势", delicate: "纤细", gentle: "轻柔",
  expressive: "富表现", autotuned: "电音", harsh: "刺耳", screamed: "嘶吼", guttural: "喉吼",
  shouty: "喊唱", piercing: "穿云", sharp: "利落", weathered: "沧桑",
  // 配器
  piano: "钢琴", "acoustic piano": "原声钢琴", "electric piano": "电钢琴", "grand piano": "三角钢琴",
  harpsichord: "羽管键琴", organ: "风琴", accordion: "手风琴", synth: "合成器",
  "acoustic guitar": "木吉他", "nylon-string guitar": "尼龙弦吉他", "electric guitar": "电吉他",
  "pedal steel guitar": "踏板钢棒", "12-string guitar": "十二弦吉他", "bass guitar": "电贝斯",
  "upright bass": "立式贝斯", "synth bass": "合成贝斯", "808 bass": "808贝斯",
  "funky bass": "放克贝斯", drums: "鼓", "acoustic drums": "真鼓", "drum machine": "鼓机",
  "brushed drums": "刷鼓", "synth drums": "电子鼓", percussion: "打击乐", tambourine: "铃鼓",
  maracas: "沙锤", congas: "康加鼓", bongos: "邦戈鼓", pandeira: "巴西铃鼓", tabla: "塔布拉鼓",
  turntables: "唱机", glockenspiel: "钢片琴", vibraphone: "颤音琴", marimba: "马林巴",
  mallets: "槌击乐", strings: "弦乐组", violin: "小提琴", cello: "大提琴", harp: "竖琴",
  brass: "铜管", trumpet: "小号", trombone: "长号", saxophone: "萨克斯",
  "alto saxophone": "中音萨克斯", oboe: "双簧管", flute: "长笛", clarinet: "单簧管",
  choir: "合唱", bell: "钟琴", guzheng: "古筝", erhu: "二胡", pipa: "琵琶", dizi: "笛子",
  xiao: "箫", suona: "唢呐", yangqin: "扬琴", "Chinese zither": "中国筝", koto: "日本筝",
  shakuhachi: "尺八", sitar: "西塔琴", oud: "乌德琴", qanun: "卡龙琴", dholak: "多拉克鼓",
  banjo: "班卓琴", mandolin: "曼陀林", ukulele: "尤克里里", harmonica: "口琴", "steel pan": "钢鼓",
  bagpipes: "风笛", fiddle: "民俗小提琴", dobro: "共振吉他",
  // 节奏与律动
  slow: "慢", "moderate tempo": "中速", "mid-tempo": "中板", upbeat: "轻快", "fast-paced": "快速",
  "ballad tempo": "抒情速度", danceable: "可跳", "groove-driven": "律动驱动", swinging: "摇摆",
  syncopated: "切分", "laid-back": "拖拍", driving: "推进", hypnotic: "迷幻",
  "unhurried phrasing": "乐句从容", "four-on-the-floor": "四踩地板", "half-time beat": "半速拍",
  "double-time feel": "倍速感", "6/8 feel": "六八拍", waltz: "华尔兹", rubato: "自由速度",
  "slow build": "缓缓渐强", building: "层层推进", drifting: "漂浮",
  "quiet-loud dynamics": "强弱对比", "start-stop dynamics": "骤停骤起", stomping: "踩踏重拍",
  "offbeat upstrokes": "反拍扫弦", "boom bap drums": "老派鼓组", "blast beats": "疾踏鼓",
  "half-time breakdown": "半速段落", "dembow rhythm": "雷鬼冻节奏", "one-drop rhythm": "一拍落地",
  "interlocking grooves": "交织律动",
};
// 中文优先、英文小字；词表里没有对应中文时退回英文，绝不显示空白芯片。
const wordZh = (w) => W_ZH[w] || w;

// 106 条曲风配方：一条 = 六要素的一次完整回答（点卡片即整体填入，之后每格都可再改）。
// 字段：en=曲风词 / mood / inst / bpm 建议 / feel / more 氛围与制作质感 / vocal 音色建议
//
// ★ en 用词规则：优先原样使用官方 top200 genre 表里的词（assets/top200-tags-official.json）。
//   全库扫下来有 27 条配方不在官方表内，它们是**刻意保留的描述式组合**，不是随手写的：
//   piano ballad / solo piano / classical guitar solo / string quartet / minimal classical /
//   narrative folk ballad / dream pop / synthwave / 1980s Japanese city pop / Cantopop ballad /
//   Cantonese ballad / lo-fi hip hop / lo-fi beats / Chinese-style pop / Chinese wuxia soundtrack /
//   traditional Chinese instrumental / Chinese zither solo / Chinese opera-influenced pop /
//   anime opening / Japanese ballad / traditional Japanese instrumental / hybrid orchestral soundtrack /
//   meditative New Age / uplifting corporate pop / children's song / ambient soundscape /
//   celebratory orchestral pop
//   理由：官方表里没有 ballad / solo / city pop / wuxia 这类单词（表里连 "ballad" 都没有），
//   但这些是通用乐评词，语义比硬凑的官方词更准；YuE2 的 style 本来就是自由文本，
//   官方自己的样例也写着 "warm piano pop""rounded bass and light drums" 这种组合。
//   复查方法：把每条 en 的 token 与 genre 表逐词比对（本轮用 %TEMP% 里的一次性脚本量的）。
//   —— 其余三张表（情绪 / 配器 / 节奏）已经做到 100% 表内用词，见下面四张词表。
const FAMS = [
  { id: "pop", zh: "流行", items: [
    { id: "piano_ballad", zh: "钢琴抒情", en: "piano ballad", vocal: "breathy", mood: ["romantic", "heartfelt"], inst: ["acoustic piano", "strings"], bpm: 72, feel: ["unhurried phrasing"], more: "spacious reverb" },
    { id: "modern_pop", zh: "主流流行", en: "pop", vocal: "bright", mood: ["uplifting", "confident"], inst: ["synth", "drum machine", "bass guitar"], bpm: 110, feel: ["moderate tempo"], more: "polished modern production" },
    { id: "city_pop", zh: "City Pop", en: "1980s Japanese city pop", vocal: "airy", mood: ["dreamy", "nostalgic", "groovy"], inst: ["electric piano", "electric guitar", "funky bass", "saxophone"], bpm: 108, feel: ["danceable"], more: "warm tape saturation, neon night atmosphere" },
    { id: "synthpop", zh: "合成器流行", en: "synthpop", vocal: "clear", mood: ["nostalgic", "energetic"], inst: ["synth", "drum machine", "bass guitar"], bpm: 120, feel: ["driving"], more: "analog synth layers, gated reverb snare" },
    { id: "dance_pop", zh: "舞曲流行", en: "Dance pop", vocal: "powerful", mood: ["energetic", "festive"], inst: ["synth", "drum machine", "808 bass"], bpm: 124, feel: ["danceable", "four-on-the-floor"], more: "big festival drop" },
    { id: "indie_pop", zh: "独立流行", en: "Indie Pop", vocal: "soft", mood: ["bittersweet", "introspective"], inst: ["electric guitar", "acoustic guitar", "drum machine"], bpm: 100, feel: ["laid-back"], more: "jangly guitars, hazy reverb" },
    { id: "electro_pop", zh: "电子流行", en: "Electro pop", vocal: "bright", mood: ["cool", "energetic"], inst: ["synth", "drum machine", "synth bass"], bpm: 116, feel: ["syncopated"], more: "crisp electronic textures" },
    { id: "cantopop", zh: "粤语流行", en: "Cantopop ballad", vocal: "warm", mood: ["sentimental", "longing"], inst: ["piano", "strings", "acoustic guitar"], bpm: 84, feel: ["moderate tempo"], more: "lush cinematic strings" },
    { id: "kpop", zh: "韩流", en: "K-pop", vocal: "polished", mood: ["confident", "energetic"], inst: ["synth", "808 bass", "drum machine"], bpm: 122, feel: ["danceable", "half-time beat"], more: "sharp brass hits, bright synth lead" },
    { id: "retro_80s", zh: "复古八零", en: "80s", vocal: "shimmering", mood: ["nostalgic", "triumphant"], inst: ["synth", "electric piano", "drums", "saxophone"], bpm: 112, feel: ["driving"], more: "gated reverb drums, wide guitar layers" },
    { id: "dream_pop", zh: "梦幻流行", en: "dream pop", vocal: "whispery", mood: ["dreamy", "wistful"], inst: ["electric guitar", "synth", "brushed drums"], bpm: 96, feel: ["laid-back"], more: "lush reverb, washed-out tape hiss" },
    { id: "operatic_pop", zh: "流行跨界", en: "operatic pop", vocal: "soaring", mood: ["triumphant", "dramatic"], inst: ["grand piano", "strings", "choir"], bpm: 76, feel: ["rubato"], more: "concert hall reverb" } ] },

  { id: "folk", zh: "民谣 · 原声", items: [
    { id: "fingered_folk", zh: "指弹民谣", en: "folk", vocal: "warm", mood: ["nostalgic", "reflective"], inst: ["acoustic guitar", "harmonica"], bpm: 76, feel: ["unhurried phrasing"], more: "intimate close-mic feel" },
    { id: "indie_folk", zh: "独立民谣", en: "indie folk", vocal: "delicate", mood: ["introspective", "bittersweet"], inst: ["acoustic guitar", "cello", "mallets"], bpm: 88, feel: ["moderate tempo"], more: "soft room reverb" },
    { id: "singer_songwriter", zh: "唱作人", en: "singer-songwriter", vocal: "raw", mood: ["intimate", "pensive"], inst: ["nylon-string guitar", "piano"], bpm: 82, feel: ["rubato"], more: "minimal arrangement" },
    { id: "pop_folk", zh: "流行人谣", en: "popfolk", vocal: "clear", mood: ["hopeful", "heartfelt"], inst: ["acoustic guitar", "piano", "strings"], bpm: 92, feel: ["moderate tempo", "building"], more: "warm room reverb" },
    { id: "country", zh: "乡村", en: "country", vocal: "husky", mood: ["nostalgic", "cheerful"], inst: ["acoustic guitar", "fiddle", "pedal steel guitar"], bpm: 96, feel: ["laid-back"], more: "warm analog tone" },
    { id: "country_rock", zh: "乡村摇滚", en: "country rock", vocal: "gravelly", mood: ["defiant", "energetic"], inst: ["electric guitar", "acoustic guitar", "drums"], bpm: 108, feel: ["driving"], more: "road-trip atmosphere" },
    { id: "bluegrass", zh: "蓝草", en: "Bluegrass", vocal: "bright", mood: ["joyful", "playful"], inst: ["banjo", "fiddle", "dobro"], bpm: 132, feel: ["fast-paced"], more: "tight ensemble picking" },
    { id: "americana", zh: "美式乡土", en: "Americana", vocal: "weathered", mood: ["wistful", "reflective"], inst: ["acoustic guitar", "harmonica", "organ"], bpm: 84, feel: ["moderate tempo"], more: "dusty room tone" },
    { id: "celtic", zh: "凯尔特", en: "Celtic", vocal: "clear", mood: ["nostalgic", "triumphant"], inst: ["fiddle", "harp", "accordion"], bpm: 100, feel: ["6/8 feel"], more: "airy flute ornament" },
    { id: "chinese_folkstory", zh: "叙事歌谣", en: "narrative folk ballad", vocal: "gentle", mood: ["sentimental", "longing"], inst: ["acoustic guitar", "piano", "strings"], bpm: 78, feel: ["slow"], more: "sparsely arranged, story-telling tone" } ] },

  { id: "rock", zh: "摇滚", items: [
    { id: "classic_rock", zh: "经典摇滚", en: "Classic Rock", vocal: "powerful", mood: ["confident", "energetic"], inst: ["electric guitar", "bass guitar", "drums", "organ"], bpm: 112, feel: ["driving"], more: "analog amp warmth" },
    { id: "hard_rock", zh: "硬摇滚", en: "Hard rock", vocal: "gritty", mood: ["aggressive", "defiant"], inst: ["electric guitar", "bass guitar", "drums"], bpm: 128, feel: ["driving"], more: "fuzzed guitar wall" },
    { id: "alt_rock", zh: "另类摇滚", en: "alternative rock", vocal: "raw", mood: ["brooding", "introspective"], inst: ["electric guitar", "bass guitar", "drums"], bpm: 118, feel: ["start-stop dynamics"], more: "noisy guitar swells" },
    { id: "indie_rock", zh: "独立摇滚", en: "indie rock", vocal: "youthful", mood: ["energetic", "wistful"], inst: ["electric guitar", "bass guitar", "drums"], bpm: 124, feel: ["syncopated"], more: "lo-fi garage tone" },
    { id: "pop_rock", zh: "流行摇滚", en: "Pop Rock", vocal: "bright", mood: ["uplifting", "confident"], inst: ["electric guitar", "piano", "drums", "bass guitar"], bpm: 116, feel: ["danceable"], more: "shiny hook lift" },
    { id: "post_rock", zh: "后摇", en: "post-rock", vocal: "", mood: ["atmospheric", "triumphant"], inst: ["electric guitar", "strings", "drums"], bpm: 90, feel: ["slow build"], more: "crescendo walls of sound" },
    { id: "new_wave", zh: "新歌特", en: "new wave", vocal: "sharp", mood: ["nostalgic", "playful"], inst: ["synth", "electric guitar", "drum machine"], bpm: 126, feel: ["driving"], more: "angular synth stabs" },
    { id: "grunge", zh: "垃圾摇滚", en: "grunge", vocal: "husky", mood: ["angsty", "defiant"], inst: ["electric guitar", "bass guitar", "drums"], bpm: 104, feel: ["quiet-loud dynamics"], more: "detuned muddy guitars" },
    { id: "pop_punk", zh: "流行朋克", en: "pop punk", vocal: "youthful", mood: ["energetic", "rebellious"], inst: ["electric guitar", "bass guitar", "drums"], bpm: 168, feel: ["fast-paced"], more: "tight double-tracked guitars" },
    { id: "glam_rock", zh: "华丽摇滚", en: "glam rock", vocal: "commanding", mood: ["dramatic", "confident"], inst: ["electric guitar", "piano", "drums"], bpm: 122, feel: ["stomping"], more: "shiny reverb" },
    { id: "rockabilly", zh: "山地摇滚", en: "rockabilly", vocal: "bright", mood: ["joyful", "playful"], inst: ["nylon-string guitar", "upright bass", "drums"], bpm: 150, feel: ["swinging"], more: "slapback echo" },
    { id: "instrumental_rock", zh: "器乐摇滚", en: "instrumental rock", vocal: "", mood: ["energetic", "atmospheric"], inst: ["electric guitar", "bass guitar", "drums"], bpm: 120, feel: ["driving"], more: "reverb-drenched lead guitar" } ] },

  { id: "electronic", zh: "电子", items: [
    { id: "lofi", zh: "低保真", en: "lo-fi hip hop", vocal: "", mood: ["relaxing", "nostalgic"], inst: ["electric piano", "drum machine", "upright bass"], bpm: 78, feel: ["laid-back"], more: "vinyl crackle, tape wobble" },
    { id: "ambient", zh: "氛围电子", en: "ambient electronic", vocal: "", mood: ["calm", "atmospheric"], inst: ["synth", "piano"], bpm: 60, feel: ["slow"], more: "endless pads, wide reverb" },
    { id: "downtempo", zh: "弛拍", en: "downtempo", vocal: "airy", mood: ["dreamy", "intimate"], inst: ["synth", "electric piano", "percussion"], bpm: 92, feel: ["syncopated"], more: "dubbed-out space" },
    { id: "house", zh: "浩室", en: "House", vocal: "soulful", mood: ["groovy", "energetic"], inst: ["drum machine", "synth bass", "electric piano"], bpm: 124, feel: ["four-on-the-floor", "danceable"], more: "warm analog house chords" },
    { id: "techno", zh: "科技舞曲", en: "Techno", vocal: "", mood: ["dark", "moody"], inst: ["drum machine", "synth bass", "synth"], bpm: 134, feel: ["four-on-the-floor", "driving"], more: "relentless machine pulse" },
    { id: "synthwave", zh: "合成器浪潮", en: "synthwave", vocal: "clear", mood: ["nostalgic", "triumphant"], inst: ["synth", "electric piano", "drum machine"], bpm: 112, feel: ["driving"], more: "neon night drive, arpegged synths" },
    { id: "dubstep", zh: "回响贝斯", en: "Dubstep", vocal: "powerful", mood: ["aggressive", "dark"], inst: ["synth bass", "drum machine", "synth"], bpm: 140, feel: ["half-time beat"], more: "wobbling bass drop" },
    { id: "eurodance", zh: "欧陆舞曲", en: "eurodance", vocal: "bright", mood: ["energetic", "festive"], inst: ["synth", "drum machine"], bpm: 140, feel: ["four-on-the-floor", "danceable"], more: "big piano stabs" },
    { id: "chiptune", zh: "芯片音乐", en: "8-bit", vocal: "youthful", mood: ["playful", "cheerful"], inst: ["synth", "drum machine"], bpm: 130, feel: ["fast-paced"], more: "square-wave arpeggios" },
    { id: "glitch", zh: "故障电子", en: "Glitch", vocal: "", mood: ["experimental", "intense"], inst: ["synth", "turntables", "drum machine"], bpm: 110, feel: ["syncopated"], more: "broken-beat edits" } ] },

  { id: "hiphop", zh: "嘻哈 · R&B", items: [
    { id: "boombap", zh: "老派嘻哈", en: "hip hop", vocal: "gravelly", mood: ["confident", "defiant"], inst: ["drum machine", "upright bass", "saxophone"], bpm: 90, feel: ["boom bap drums"], more: "dusty sample flip" },
    { id: "trap", zh: "陷阱说唱", en: "Trap", vocal: "autotuned", mood: ["dark", "confident"], inst: ["808 bass", "drum machine", "synth"], bpm: 140, feel: ["half-time beat", "double-time feel"], more: "sliding 808, rolling hats" },
    { id: "melodic_rap", zh: "旋律说唱", en: "melodic rap", vocal: "airy", mood: ["melancholic", "reflective"], inst: ["808 bass", "piano", "drum machine"], bpm: 130, feel: ["laid-back"], more: "sung hook over spoken flow" },
    { id: "conscious", zh: "写实嘻哈", en: "experimental hip-hop", vocal: "commanding", mood: ["introspective", "defiant"], inst: ["electric piano", "drum machine", "strings"], bpm: 86, feel: ["syncopated"], more: "jazz-sample texture" },
    { id: "rnb", zh: "节奏布鲁斯", en: "R&B", vocal: "smooth", mood: ["romantic", "sensual"], inst: ["electric piano", "synth bass", "drum machine"], bpm: 96, feel: ["laid-back"], more: "silky runs, lush pads" },
    { id: "soul_rnb", zh: "灵魂乐", en: "Soul-R&B", vocal: "soulful", mood: ["passionate", "hopeful"], inst: ["electric piano", "strings", "drums", "trumpet"], bpm: 100, feel: ["swinging"], more: "live band take" },
    { id: "funk", zh: "放克", en: "Funk", vocal: "gritty", mood: ["groovy", "playful"], inst: ["funky bass", "electric guitar", "drum machine", "brass"], bpm: 108, feel: ["syncopated", "groove-driven"], more: "tight horn hits" },
    { id: "disco", zh: "迪斯科", en: "Disco", vocal: "soaring", mood: ["festive", "groovy"], inst: ["strings", "funky bass", "electric piano", "drums"], bpm: 118, feel: ["four-on-the-floor", "danceable"], more: "shimmering disco ball" },
    { id: "gospel", zh: "福音", en: "Gospel", vocal: "powerful", mood: ["inspiring", "uplifting"], inst: ["organ", "piano", "choir", "drums"], bpm: 76, feel: ["swinging"], more: "call-and-response choir" },
    { id: "grime", zh: "英式硬核", en: "grime", vocal: "sharp", mood: ["dark", "aggressive"], inst: ["synth bass", "drum machine"], bpm: 140, feel: ["half-time beat"], more: "cold synth stabs" } ] },

  { id: "jazz", zh: "爵士 · 蓝调", items: [
    { id: "bigband", zh: "大乐队摇摆", en: "big band", vocal: "smooth", mood: ["joyful", "elegant"], inst: ["brass", "saxophone", "upright bass", "brushed drums"], bpm: 120, feel: ["swinging", "danceable"], more: "smoky ballroom" },
    { id: "cool_jazz", zh: "冷爵士三重奏", en: "jazz", vocal: "", mood: ["relaxing", "introspective"], inst: ["piano", "upright bass", "brushed drums"], bpm: 96, feel: ["swinging"], more: "intimate jazz club" },
    { id: "soul_jazz", zh: "爵士放歌", en: "soul-jazz", vocal: "husky", mood: ["sensual", "groovy"], inst: ["alto saxophone", "electric piano", "upright bass", "drums"], bpm: 102, feel: ["laid-back"], more: "late-night lounge" },
    { id: "bossa", zh: "波萨诺瓦", en: "Bossa Nova", vocal: "whispery", mood: ["romantic", "calm"], inst: ["nylon-string guitar", "flute", "percussion"], bpm: 108, feel: ["swinging"], more: "beachside warmth" },
    { id: "samba", zh: "桑巴", en: "Samba", vocal: "bright", mood: ["festive", "joyful"], inst: ["pandeira", "congas", "brass", "acoustic guitar"], bpm: 104, feel: ["syncopated", "danceable"], more: "carnival street energy" },
    { id: "blues", zh: "芝加哥蓝调", en: "blues", vocal: "raspy", mood: ["melancholic", "defiant"], inst: ["electric guitar", "harmonica", "upright bass", "drums"], bpm: 84, feel: ["swinging"], more: "small-room blues take" },
    { id: "chanson", zh: "法语香颂", en: "Chanson", vocal: "delicate", mood: ["romantic", "nostalgic"], inst: ["accordion", "nylon-string guitar", "strings"], bpm: 88, feel: ["rubato"], more: "Paris café ambience" },
    { id: "improv_jazz", zh: "即兴爵士", en: "jazz improvisation", vocal: "", mood: ["introspective", "playful"], inst: ["piano", "upright bass", "drums", "saxophone"], bpm: 116, feel: ["swinging", "syncopated"], more: "loose improvisation" } ] },

  { id: "chinese", zh: "中国风 · 民乐", items: [
    { id: "cpop_chinese", zh: "中国风流行", en: "Chinese-style pop", vocal: "clear", mood: ["nostalgic", "romantic"], inst: ["guzheng", "erhu", "dizi", "piano", "strings"], bpm: 84, feel: ["unhurried phrasing"], more: "spacious ink-wash atmosphere" },
    { id: "gufeng_cinematic", zh: "古风影视", en: "Chinese wuxia soundtrack", vocal: "soaring", mood: ["dramatic", "longing"], inst: ["erhu", "pipa", "Chinese zither", "strings", "choir"], bpm: 76, feel: ["slow build"], more: "cinematic brush-and-ink mood" },
    { id: "traditional_ens", zh: "民乐合奏", en: "traditional Chinese instrumental", vocal: "", mood: ["elegant", "atmospheric"], inst: ["guzheng", "erhu", "pipa", "dizi", "percussion"], bpm: 80, feel: ["rubato"], more: "natural room resonance" },
    { id: "zither_solo", zh: "古筝独奏", en: "Chinese zither solo", vocal: "", mood: ["calm", "reflective"], inst: ["guzheng", "xiao"], bpm: 66, feel: ["rubato"], more: "sparse, meditative" },
    { id: "opera_fusion", zh: "戏腔融合", en: "Chinese opera-influenced pop", vocal: "piercing", mood: ["dramatic", "defiant"], inst: ["suona", "Chinese zither", "drums", "electric guitar"], bpm: 96, feel: ["syncopated"], more: "opera-style ornament" },
    { id: "chinese_hip", zh: "中国风说唱", en: "Chinese hip-hop", vocal: "gravelly", mood: ["confident", "nostalgic"], inst: ["808 bass", "dizi", "guzheng", "drum machine"], bpm: 92, feel: ["laid-back"], more: "pentatonic hook" },
    { id: "cantonese_ballad", zh: "粤语抒情", en: "Cantonese ballad", vocal: "warm", mood: ["melancholic", "grateful"], inst: ["piano", "strings", "acoustic guitar"], bpm: 74, feel: ["unhurried phrasing"], more: "classic Cantopop string line" } ] },

  { id: "jpop", zh: "日系 · 动漫", items: [
    { id: "jpop", zh: "日系流行", en: "J-pop", vocal: "bright", mood: ["hopeful", "energetic"], inst: ["electric piano", "electric guitar", "drums", "strings"], bpm: 128, feel: ["driving"], more: "dense chord changes" },
    { id: "jrock", zh: "日系摇滚", en: "J-rock", vocal: "powerful", mood: ["defiant", "energetic"], inst: ["electric guitar", "bass guitar", "drums"], bpm: 150, feel: ["fast-paced"], more: "shredding lead guitar" },
    { id: "anime_op", zh: "动漫片头", en: "anime opening", vocal: "soaring", mood: ["triumphant", "hopeful"], inst: ["electric guitar", "synth", "drums", "strings"], bpm: 160, feel: ["driving"], more: "rising key change, big final hook" },
    { id: "jballad", zh: "日系抒情", en: "Japanese ballad", vocal: "breathy", mood: ["longing", "hopeful"], inst: ["grand piano", "strings", "electric guitar"], bpm: 70, feel: ["slow build"], more: "cinematic string swell" },
    { id: "wa_folk", zh: "和风器乐", en: "traditional Japanese instrumental", vocal: "", mood: ["calm", "mysterious"], inst: ["koto", "shakuhachi"], bpm: 68, feel: ["rubato"], more: "temple courtyard air" },
    { id: "shibuya", zh: "涩谷系", en: "indie pop", vocal: "sweet", mood: ["playful", "nostalgic"], inst: ["electric piano", "strings", "acoustic guitar", "drum machine"], bpm: 112, feel: ["swinging"], more: "exotic sample collage" } ] },

  { id: "cinematic", zh: "影视 · 古典", items: [
    { id: "orchestral_score", zh: "管弦配乐", en: "orchestral soundtrack", vocal: "", mood: ["dramatic", "triumphant"], inst: ["strings", "brass", "percussion", "choir"], bpm: 86, feel: ["slow build"], more: "concert-hall orchestral depth" },
    { id: "hybrid_score", zh: "混合电子管弦", en: "hybrid orchestral soundtrack", vocal: "", mood: ["intense", "atmospheric"], inst: ["strings", "brass", "synth", "drum machine"], bpm: 100, feel: ["driving"], more: "pulsing synth under orchestra" },
    { id: "minimal_classical", zh: "极简古典", en: "minimal classical", vocal: "", mood: ["reflective", "calm"], inst: ["grand piano", "strings"], bpm: 64, feel: ["slow"], more: "repeating figure, gradual layers" },
    { id: "solo_piano", zh: "钢琴独奏", en: "solo piano", vocal: "", mood: ["introspective", "heartfelt"], inst: ["grand piano"], bpm: 70, feel: ["rubato"], more: "felt piano, close mic" },
    { id: "string_quartet", zh: "弦乐四重奏", en: "string quartet", vocal: "", mood: ["elegant", "melancholic"], inst: ["violin", "cello"], bpm: 80, feel: ["moderate tempo"], more: "intimate hall" },
    { id: "choral", zh: "人声合唱", en: "Choral", vocal: "shimmering", mood: ["spiritual", "triumphant"], inst: ["choir", "organ", "strings"], bpm: 72, feel: ["slow build"], more: "cathedral reverb" },
    { id: "new_age", zh: "新世纪", en: "New Age", vocal: "airy", mood: ["peaceful", "hopeful"], inst: ["synth", "piano", "flute"], bpm: 76, feel: ["unhurried phrasing"], more: "healing pads" },
    { id: "classical_guitar", zh: "古典吉他", en: "classical guitar solo", vocal: "", mood: ["romantic", "intimate"], inst: ["nylon-string guitar"], bpm: 84, feel: ["rubato"], more: "parlour acoustics" } ] },

  { id: "metal", zh: "金属 · 重型", items: [
    { id: "heavy_metal", zh: "重金属", en: "Heavy metal", vocal: "powerful", mood: ["aggressive", "defiant"], inst: ["electric guitar", "bass guitar", "drums"], bpm: 130, feel: ["driving"], more: "tight palm-mute riff" },
    { id: "power_metal", zh: "力量金属", en: "Power metal", vocal: "soaring", mood: ["triumphant", "heroic"], inst: ["electric guitar", "drums", "synth", "choir"], bpm: 160, feel: ["fast-paced"], more: "galloping riff, neoclassical solo" },
    { id: "thrash", zh: "鞭挞金属", en: "Thrash Metal", vocal: "shouty", mood: ["furious", "rebellious"], inst: ["electric guitar", "bass guitar", "drums"], bpm: 180, feel: ["fast-paced"], more: "relentless downpicking" },
    { id: "melodeath", zh: "旋律死亡", en: "Melodic Death Metal", vocal: "harsh", mood: ["dark", "melancholic"], inst: ["electric guitar", "bass guitar", "drums"], bpm: 150, feel: ["driving"], more: "twin-guitar harmony" },
    { id: "death_metal", zh: "死亡金属", en: "Death Metal", vocal: "guttural", mood: ["brutal", "dark"], inst: ["electric guitar", "drums", "bass guitar"], bpm: 170, feel: ["blast beats"], more: "low-tuned guitar wall" },
    { id: "metalcore", zh: "金属核", en: "Metalcore", vocal: "screamed", mood: ["aggressive", "intense"], inst: ["electric guitar", "drums", "bass guitar"], bpm: 140, feel: ["half-time breakdown"], more: "heavy breakdown, clean sung hook" },
    { id: "post_hardcore", zh: "后硬核", en: "post-hardcore", vocal: "raw", mood: ["angsty", "intense"], inst: ["electric guitar", "bass guitar", "drums"], bpm: 132, feel: ["quiet-loud dynamics"], more: "spoken-word delivery" },
    { id: "industrial", zh: "工业金属", en: "industrial", vocal: "harsh", mood: ["dark", "intense"], inst: ["synth", "electric guitar", "drum machine"], bpm: 112, feel: ["driving"], more: "machine-noise texture" },
    { id: "ska_punk", zh: "斯卡朋克", en: "Ska", vocal: "bright", mood: ["playful", "rebellious"], inst: ["electric guitar", "brass", "upright bass", "drums"], bpm: 160, feel: ["offbeat upstrokes"], more: "horn section stab" } ] },

  { id: "world", zh: "拉丁 · 世界", items: [
    { id: "reggaeton", zh: "雷鬼冻", en: "Reggaeton", vocal: "sharp", mood: ["sensual", "playful"], inst: ["drum machine", "synth bass", "brass"], bpm: 96, feel: ["dembow rhythm"], more: "club-ready latin bounce" },
    { id: "salsa", zh: "萨尔萨", en: "Salsa", vocal: "powerful", mood: ["festive", "groovy"], inst: ["brass", "congas", "piano", "upright bass"], bpm: 100, feel: ["syncopated", "danceable"], more: "mambo horn line" },
    { id: "latin_pop", zh: "拉丁流行", en: "latin pop", vocal: "bright", mood: ["romantic", "uplifting"], inst: ["acoustic guitar", "percussion", "strings"], bpm: 102, feel: ["syncopated"], more: "warm coastal air" },
    { id: "reggae", zh: "雷鬼", en: "Reggae", vocal: "smooth", mood: ["relaxing", "hopeful"], inst: ["electric guitar", "funky bass", "drums", "organ"], bpm: 78, feel: ["one-drop rhythm"], more: "skanking guitar offbeat" },
    { id: "middle_east", zh: "中东风情", en: "Middle Eastern", vocal: "husky", mood: ["mysterious", "atmospheric"], inst: ["oud", "qanun", "percussion", "flute"], bpm: 96, feel: ["syncopated"], more: "desert caravan mood" },
    { id: "indian", zh: "印度风情", en: "Indian", vocal: "shimmering", mood: ["spiritual", "festive"], inst: ["sitar", "tabla", "dholak", "strings"], bpm: 100, feel: ["syncopated"], more: "raga-inspired melody" },
    { id: "african", zh: "非洲律动", en: "African", vocal: "soulful", mood: ["groovy", "festive"], inst: ["percussion", "marimba", "acoustic guitar", "choir"], bpm: 112, feel: ["interlocking grooves", "syncopated"], more: "polyrhythmic ensemble" } ] },

  { id: "functional", zh: "氛围 · 功能", items: [
    { id: "sleep", zh: "助眠", en: "ambient", vocal: "", mood: ["calm", "peaceful"], inst: ["piano", "synth"], bpm: 56, feel: ["slow"], more: "long releases, no percussion" },
    { id: "meditate", zh: "冥想", en: "meditative New Age", vocal: "", mood: ["meditative", "spiritual"], inst: ["flute", "synth", "harp"], bpm: 60, feel: ["rubato"], more: "singing-bowl drones" },
    { id: "study", zh: "学习背景", en: "lo-fi beats", vocal: "", mood: ["relaxing", "reflective"], inst: ["electric piano", "drum machine"], bpm: 82, feel: ["laid-back"], more: "rain-on-window ambience" },
    { id: "corporate", zh: "广告企业", en: "uplifting corporate pop", vocal: "clear", mood: ["hopeful", "confident"], inst: ["synth", "electric piano", "drums", "strings"], bpm: 112, feel: ["building"], more: "clean commercial polish" },
    { id: "children", zh: "儿歌", en: "children's song", vocal: "sweet", mood: ["cheerful", "playful"], inst: ["glockenspiel", "acoustic guitar", "acoustic drums"], bpm: 108, feel: ["danceable"], more: "bright sing-along" },
    { id: "soundscape", zh: "声景", en: "ambient soundscape", vocal: "", mood: ["atmospheric", "mysterious"], inst: ["synth", "strings"], bpm: 68, feel: ["drifting"], more: "field-recording texture" },
    { id: "wedding", zh: "婚礼进行", en: "celebratory orchestral pop", vocal: "warm", mood: ["romantic", "triumphant"], inst: ["grand piano", "strings", "choir"], bpm: 88, feel: ["slow build"], more: "ceremonial warmth" } ] },
];

// 官方六要素之外的"空词"（本机裁定不进 style：不携带可执行信息，只占字符预算）
const EMPTY_WORDS = ["epic", "emotional", "beautiful", "amazing", "masterpiece", "perfect"];
// 官方 top200 genre 表 + 各曲风配方名，用于「曲风」维度的识别（不在表内只提示、不拦）
const GENRE_WORDS = (() => {
  const s = new Set();
  FAMS.forEach(f => f.items.forEach(p => p.en.toLowerCase().split(/[\s\-]+/).forEach(w => w && s.add(w))));
  "pop rock electronic classical folk rap hiphop hip-hop punk jazz country indie experimental funk blues ambient soundtrack reggae disco house latin metal techno soul grunge electro ballad instrumental chanson bossa samba bluegrass gospel trap dubstep synthpop eurodance ska rockabilly Americana new-wave opera celtic techno".split(/\s+/).forEach(w => s.add(w));
  return s;
})();

// ---------- presets ----------
// 歌词预设仅官方一组（multimodal-art-projection/YuE · examples/song.json，City Lights）——
// 官方没有第二组样例，词的事交给 lw-* 作词库，这里不自造"歌词模板"。
const OFFICIAL_STYLE = "English, warm piano pop, expressive female voice, acoustic piano, rounded bass and light drums, lyrical memorable melody, unhurried phrasing, 88 BPM";
const OFFICIAL_LYRICS = "[Verse]\nNeon fades along the lane\nFootsteps keep the time of rain\nFold the night and leave it here\nMorning has a sky to clear\n\n[Chorus]\nLet the day come into view\nEvery road begins with you\nHold a little room for light\nWe will sing beyond the night";
const fallbackLyrics = [
  { id: "city_lights", name: "City Lights（官方示例）", lyrics: OFFICIAL_LYRICS },
];
function renderChips(el, items, key, target) {
  if (!el) return;
  el.innerHTML = "";
  items.forEach((item, i) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip mini" + (i === 0 ? " active" : ""); b.textContent = item.name;
    b.onclick = () => {
      [...el.children].forEach(c => c.classList.remove("active")); b.classList.add("active");
      const t = $(target);
      if (target === "lyrics") pushLyricUndo("歌词候选「" + item.name + "」");   // 整块覆盖前留一档
      t.value = item[key];
      if (target === "lyrics") flashField(t);   // 填的是左栏的歌词框：亮一下告诉用户"词已经过去了"
      markStyleDirty(key === "style");
    };
    el.appendChild(b);
  });
}

// ======================= 曲风装配台 ======================= //
// 单向关系，写在这里以免日后又变成两处真相：
//   · Style 文本框 = 提交给模型的唯一真相（左栏的自检也只看它）；
//   · 装配台 = Style 的「编辑器」。点曲风卡片/我的预设是明确的采用意图 → 直接覆盖文本；
//     之后在六格里的任何改动，只在「文本没被手改」时跟着重写文本，手改过就只更新预览条，
//     要覆盖必须点「↻ 重新装配到 Style」。这样两条路（手打 / 装配）都能走完，互不吞掉。
const ASM_KEY = "yue2_compose_asm";
const asm = { fam: "pop", preset: "", mood: [], timbre: [], inst: [], feel: [] };
let styleDirty = false;          // Style 文本 ≠ 装配台产物
let suppressStyleWatch = false;  // 程序写文本时不要把这次写入当成"用户手改"
let autoClearedGender = null;    // 被器乐卡片临时清掉的人声，换回有人声的卡片时还原

function markStyleDirty(manual) {
  if (!manual) return;           // 歌词预设填的是 #lyrics，与 Style 无关
  styleDirty = true;
  updateStyleChecks();
  updateAsmHint();
}
function escRe(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
function setSel(el, v) {
  if (!el) return false;
  const has = [...el.options].some(o => o.value === v);
  if (has) el.value = v;
  return has;
}

// ---- 六要素 → 一句 style ----
// 顺序即官方 style-guide 的六要素顺序（语言 → 曲风 → 情绪 → 人声 → 配器 → 节奏），
// 语言必须打头（本机裁定 §2）。
//
// ★ 关于"12 条短语"（用户问：YuE2 是不是只支持 12 个短语？）——查证结论：
//   · 官方与本机 skill 都没写过 12 是硬上限。craft-bridge §4.1 的原文是
//     "2–12 个短语，约 200 字符——官方评测样本均值"，那是**信息预算**，不是模型限制；
//   · 本机一手证据：runtime/output 的 112 条真实任务里，最长的一条是
//     20 短语 / 330 字符，status=done 且音频文件在，模型照样出片；
//     短语数分布集中在 9–11 条（官方示例 "English, warm piano pop, …" 也是 8 条）。
//   → 所以 12 只当"提醒线"用，超过不裁剪、不拦截，只把数字亮出来并说明代价。
//     早期版本会在超预算时**偷偷少写几件乐器/几个情绪**，用户选中的东西凭空消失且无人告知，
//     那是比超预算更严重的错，已删除。
//   · 真正省预算的地方是人声：官方样本把性别与音色写成**一条**短语
//     （"expressive female voice"），以前这里却发两条（"clear female vocal" + "breathy vocal"），
//     白占一格还容易重复，现在合并成一条。
// 三条预算线怎么定的（都可复查，不是拍脑袋）：
//   · 短语 12 / 字符 200 —— craft-bridge §4.1 记的官方评测样本均值；
//   · 短语 18 / 字符 300 —— 本机经验线，仍低于 runtime/output 里那条成功出片的 20 短语 / 330 字符；
//   · 词数 28 / 40 —— 按本页 106 条曲风配方实测分布定的：配方最高 27 词（City Pop）、中位 19 词。
//     线放在 28，意思是"点任何一张卡片都不会一进来就红"，越过它一定是用户自己往后加词了。
//     为什么要单独量词数：多个情绪会被拼成一条短语（"dreamy nostalgic groovy"），只看短语数
//     会把真实词量藏起来——用户点的第 4 个情绪明明进了 Style，短语数却纹丝不动。
const PHRASE_SOFT = 12;    // 官方样本均值线，越过只提示
const PHRASE_HARD = 18;    // 经验上的"开始顾不过来"线（本机最长成功样本 20）
const WORD_SOFT = 28;      // 曲风配方库实测上界是 27 词（中位 19）
const WORD_HARD = 40;      // 配方库最高 27 词的 1.5 倍：越过它就是用户自己堆的词，够单独说一句了
const CHAR_SOFT = 200;     // craft-bridge 记的官方样本均值
const CHAR_HARD = 300;
// 人声一条短语：把性别短语（clear female vocal）与音色词合成 "breathy clear female vocal"。
// 用户明确选了音色时，把默认那个万金油 "clear" 让位出去（它自己也在音色表里，想要可再点）。
// ★ 这里以前写的是 timbre.slice(0, 2)：第 3 个音色词点了会亮、但根本不进 Style，
//   界面上没有任何交代。现在全量写入，代价交给预算线去说。
function vocalPhrase(gender, timbre) {
  if (!gender && !timbre.length) return "";
  if (!timbre.length) return gender;
  const t = timbre.join(" ");
  if (!gender) return t + " vocal";
  const g = gender.replace(/^clear\s+/, "");
  return (g.startsWith(t) ? g : t + " " + g);
}
function compileParts() {
  const lang = ($("aLang") || {}).value || "";
  const genre = (($("aGenre") || {}).value || "").trim();
  const gender = (($("gender") || {}).value || "").trim();
  const key = (($("aKey") || {}).value || "").trim();
  const more = (($("aMore") || {}).value || "").trim();
  const bpm = parseInt(($("bpm") || {}).value, 10);
  const g = [];
  // 逗号本身就是一句里的"短语分隔符"，所以手打/配方里带逗号的字段要按短语展开，
  // 否则"短语数"的预算会被一句里的三个逗号偷偷穿过去（自检与预算也就对不上了）。
  const spread = s => String(s || "").split(/[,，]/).map(x => x.trim()).filter(Boolean);
  if (lang) g.push(lang);
  g.push(...spread(genre));
  // 多个情绪拼成一条短语（官方样本里 "rounded bass and light drums" 也是这么干的），
  // 但**不再 slice(0, 3)**：第 4 个情绪点了必须真的进 Style，否则芯片亮着、文本里却没有，
  // 用户完全无从发现。省下来的预算靠字符数和词数两条线如实报出来。
  if (asm.mood.length) g.push(asm.mood.join(" "));
  const vp = vocalPhrase(gender, asm.timbre);
  if (vp) g.push(vp);
  asm.inst.forEach(i => g.push(i));
  asm.feel.forEach(f => g.push(f));
  if (key) g.push(key);
  if (bpm >= 40 && bpm <= 220) g.push(bpm + " BPM");
  g.push(...spread(more));
  return g.filter(Boolean);
}
function compileStyle() { return compileParts().join(", "); }
// 当前这句 style 的信息预算占用（短语数 + 字符数 + 该用什么颜色说话）
function budgetOf(text) {
  const phrases = text.split(",").map(s => s.trim()).filter(Boolean).length;
  const words = (text.match(/[\p{L}\p{N}’'-]+/gu) || []).length;
  const chars = text.length;
  // 三条线各自独立判级别，取最严重的那条；over 给出去"到底哪一条超了"，
  // 提示语就不用再猜是短语多了还是字符多了。
  const lvl = (v, soft, hard) => v > hard ? 2 : v > soft ? 1 : 0;
  const sev = Math.max(lvl(phrases, PHRASE_SOFT, PHRASE_HARD),
                       lvl(words, WORD_SOFT, WORD_HARD),
                       lvl(chars, CHAR_SOFT, CHAR_HARD));
  // over 是给提示语用的"到底哪一条超了"。写成 "X 条 / 均值 Y" 这种短横线式，
  // 因为提示语本身已经带一层括号，条目里再套括号会变成 "（词 41（建议 ≤28））" 没法读。
  const over = [];
  if (phrases > PHRASE_SOFT) over.push("短语 " + phrases + " 条 / 均值 " + PHRASE_SOFT);
  if (words > WORD_SOFT) over.push("词 " + words + " 个 / 建议 ≤" + WORD_SOFT);
  if (chars > CHAR_SOFT) over.push("字符 " + chars + " / 均值 " + CHAR_SOFT);
  return { phrases, words, chars, level: sev === 2 ? "hard" : sev === 1 ? "soft" : "ok", over };
}
function writeStyleBox(text) {
  const box = $("styleAsm");
  if (!box) return;
  // 启动时如果存有"用户手改过的那句"，第一笔写入要用它覆盖按六要素重算的结果，
  // 否则刷新会把用户改的词悄悄换回机器拼的那句。
  if (window.__asmTextPending) {
    text = window.__asmTextPending; window.__asmTextPending = "";
    styleDirty = true;
  }
  suppressStyleWatch = true;
  box.value = text;
  try { box.dispatchEvent(new Event("input", { bubbles: true })); } catch {}
  suppressStyleWatch = false;
  // 右栏点一下、左栏亮一下（CSS @keyframes written）：两栏各自滚动之后，
  // 视线可能正在右栏底部，不亮这一下用户不知道刚才那一下到底写进去没有。
  flashField(box);
}
function flashField(el) {
  if (!el) return;
  el.classList.remove("written");
  void el.offsetWidth;          // 不强制回流一次，同一个 class 再加回来不会重启动画
  el.classList.add("written");
}
function recompile(force) {
  const text = compileStyle();
  if (force || !styleDirty) { writeStyleBox(text); styleDirty = false; }
  updateStyleChecks();
  updateAsmHint();
  saveAsm();
}
function saveAsm() {
  try {
    localStorage.setItem(ASM_KEY, JSON.stringify({
      ...asm,
      lang: ($("aLang") || {}).value || "", genre: ($("aGenre") || {}).value || "",
      more: ($("aMore") || {}).value || "", key: ($("aKey") || {}).value || "",
      bpm: ($("bpm") || {}).value || "", gender: ($("gender") || {}).value || "",
      // ① 那个框现在是 ① 自己的（不与 ②⑤ 共用 DOM），所以它的手改也得自己存一份，
      // 否则刷新就只剩"按六要素重算"的那句，用户改过的词会凭空回去。
      asmText: (($("styleAsm") || {}).value || "").trim(),
    }));
  } catch {}
}

// ---- 维度的渲染 ----
// 折叠态露几个：静态常量，**故意不做运行时自适应计算**。
// 理由（踩过的坑，留档）：容器宽度在第 0 帧 / 分栏切换后 / 字体加载完后
// 是三个不同的值（353 / 553 / 642），任何"在某一刻量一次就定下来"的做法
// 都会在真实时序下算错，而且没有自愈路径（实测情绪格被锁死在 6 个词）。
// 布局的事交给 CSS，JS 只负责给出候选词。
//
// 取值依据（1920 视口、分栏态实测容器 642px、gap 6px、尾组横向约 132px）：
//   可放个数 = floor((642 - 132 + 6) / (chip宽 + 6))
//   情绪/人声 chip 44px → 10 个；配器 chip 58px → 8 个；节奏 chip 49px → 9 个。
// 取到"刚好放满一行"，尾组就跟在同一行末尾（靠 .wtail 的 margin-left:auto 吸住）。
// 窄屏时容器更窄，flex-wrap 会自然把尾组落到下一行 —— 这是可接受的降级，
// 不会出现"词莫名消失"。
// ★ 折叠方式重做（用户："这种折叠、滚动方式太呆了"）。旧做法有两处呆：
//   ① 每格词表自带一条 92px 内滚动条——小滚动条嵌在整页滚动条里，滚轮经过就抢走滚动，
//      看着像页面出了 bug；② 展开只靠词表末尾那个虚线「更多 N 个 ▾」芯片，
//      虚线样式像禁用态，而且情绪 55 词 / 配器 71 词，要找某个具体词只能一格一格点开翻。
//   现在：内滚动条删掉（见 CSS .arow .chips），改成「搜索一把梭 + 就地展开」——
//   搜索框同时筛四格（中英都行，输入 治愈 / warm / 钢琴 就能直接定位），
//   没命中的格子压暗表示"这里没有"，展开按钮老实说出还剩多少词。
let wordQuery = "";
const wordHit = (v, q) => !q || v.toLowerCase().includes(q) || String(wordZh(v) || "").toLowerCase().includes(q);
// ★ 点亮式候选词（用户反馈"点一个消失一个"）：
//   早期版本把已选中的词从词表里筛掉了，选中即消失——用户既看不到"我选了什么"，
//   也无法在原位取消，只能靠右上角"已选"计数回忆。现在已选词留在原地高亮，再点一下取消。
//   折叠态也必须让所有已选词露出来，所以取「前 limit 个 + 全部已选」，顺序仍按词表原序。
// ★ 芯片上中文为主、英文为辅（用户反馈"要点'慢'看得见是 slow"）：
//   送进 Style 的真值永远是英文小字那一串，中文只是给人看的标签。
function renderMulti(elId, vocab, key) {
  const el = $(elId);
  if (!el) return;
  el.innerHTML = "";
  const sel = asm[key];
  const extras = sel.filter(v => !vocab.includes(v));      // 曲风配方带进来的表外词也要看得见
  const shown = [...extras, ...vocab];
  const q = wordQuery.trim().toLowerCase();
  const pool = q ? shown.filter(v => wordHit(v, q)) : shown;
  // ★ 四格默认全展开（用户 2026-10-15 明确要求："不要折叠，要默认全部展开"）。
  //   以前按 642px 容器精算出"每格只露 8~10 个常用词 + 一个「+N ▾」"，省了高度，
  //   代价是情绪/人声/配器/节奏四格各自藏着一半可选词，用户根本不知道还有别的词。
  //   现在全部摊开；超出栏高的部分交给右栏自己的滚动条（见 .grid.compose .pane-body）。
  const list = pool;
  const row = el.closest(".arow");
  if (row) row.classList.toggle("dim", !!q && pool.length === 0);   // 搜的时候这一格没命中：压暗而不是留个空框
  if (!pool.length) {
    const s = document.createElement("span");
    s.className = "nomatch";
    s.textContent = "这一格没有匹配「" + wordQuery.trim() + "」的词";
    el.appendChild(s);
  }
  list.forEach(v => {
    const on = sel.includes(v);
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip mini word" + (on ? " tag-on" : "");
    // 芯片**只露中文**：英文真值放进 title 与 aria-label，DOM 里不再挂 <i>。
    // 以前 <i> 靠 hover 翻出来，那一下会把芯片从 ~60px 撑到 135–175px，
    // 310px 的栏宽立刻换行、整格往下跳（见 CSS .chip.word 那段）。
    // 只露中文后芯片恒定 ~60px，一行 4–5 个，尺寸在任何状态下都不变。
    b.textContent = wordZh(v);
    b.setAttribute("aria-label", wordZh(v) + " / " + v);
    b.title = wordZh(v) + " / " + v + "\n" + (on ? "已选入 Style · 再点一下取消" : "点击选入 Style");
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.onclick = () => toggleWord(elId, vocab, key, v, on);
    el.appendChild(b);
  });
  // 「已选 N · 清空」是"读词表的附属动作"，
  // 用 .wfill（弹性填充）+ .wtail 的组合让它**贴着词表最后一行末尾**，
  // 不会被挤成孤零零的第二行（见 CSS 里 .wfill 那段注释）。
  // ★ 这里以前还有一个「+N ▾ / 收起 ▴」的逐格折叠钮，已按用户要求删除：四格恒定全展开。
  const tail = document.createElement("span");
  tail.className = "wtail";
  if (sel.length) {
    const c = document.createElement("button");
    c.type = "button"; c.className = "chip mini cnt";
    c.textContent = "已选 " + sel.length + " · 清空";
    c.title = "取消这一格的全部选择";
    c.onclick = () => { asm[key] = []; recompile(); renderMulti(elId, vocab, key); };
    tail.appendChild(c);
  }
  if (tail.childNodes.length) el.appendChild(tail);
}
// 选/取消一个词 → 重算 Style → 再把"信息预算"的代价当场说出来（超线不拦、不偷偷删词，只报数）
function toggleWord(elId, vocab, key, v, wasOn) {
  const arr = asm[key];
  if (wasOn) { const i = arr.indexOf(v); if (i >= 0) arr.splice(i, 1); }
  else arr.push(v);
  recompile();
  renderMulti(elId, vocab, key);
  if (wasOn) return;
  // 提示说的是"真正会提交的那一句"：文本被手改过之后装配台不再覆盖它，就该按文本框算。
  const submitted = styleDirty ? ((($("styleAsm") || {}).value || "").trim() || compileStyle()) : compileStyle();
  const b = budgetOf(submitted);
  if (b.level === "hard") {
    toast("加入「" + wordZh(v) + "」后 Style 是 " + b.phrases + " 条短语 / " + b.words + " 词 / " + b.chars +
          " 字符，超过建议上限（" + b.over.join("，") + "）。模型不会报错，但越靠后的词越容易被忽略——" +
          "想保住重点就删掉一个次要的词，把最要紧的往前挪。", "err");
  } else if (b.level === "soft") {
    toast("加入「" + wordZh(v) + "」后 Style 是 " + b.phrases + " 条短语 / " + b.words + " 词 / " + b.chars +
          " 字符，越过官方样本均值线（" + PHRASE_SOFT + " 条 / " + WORD_SOFT + " 词 / " + CHAR_SOFT + " 字符）。" +
          "这不是硬性上限，照样能生成；只是词越多，单个词的分量越轻。", "warn");
  }
}
function renderAllMulti() {
  renderMulti("moodChips", MOODS, "mood");
  renderMulti("timbreChips", TIMBRES, "timbre");
  renderMulti("instChips", INSTRS, "inst");
  renderMulti("feelChips", FEELS, "feel");
  updateWordToggle();
}
// 右上角那个按钮：以前是「全部展开 / 全部收起」的折叠总开关。
// 现在四格恒定全展开（用户要求），它只在搜索态出现，做那个位置唯一还有用的动作——清掉搜索词。
function updateWordToggle() {
  const b = $("wordExpand");
  if (!b) return;
  b.hidden = !wordQuery.trim();
  if (!b.hidden) {
    b.textContent = "清掉搜索词 ✕";
    b.title = "取消筛选，恢复完整词表";
  }
}
function renderFamTabs() {
  const el = $("famTabs");
  if (!el) return;
  el.innerHTML = "";
  FAMS.forEach(f => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip mini" + (f.id === asm.fam ? " active" : "");
    b.textContent = f.zh;
    b.title = f.zh + " · " + f.items.length + " 条配方";
    b.onclick = () => { asm.fam = f.id; renderFamTabs(); renderPresetGrid(); saveAsm(); };
    el.appendChild(b);
  });
}
// 曲风卡片：本大类的所有配方一次全摊开（以前只露前 8 张、其余收进「+N 张 ▾」，
// 藏起来的那几张用户根本不知道存在 —— 2026-10-15 按要求撤掉折叠）。
// 和候选词共用一条右栏滚动条，不再各自套小滚动条。
// 搜索态改走"跨大类找"：用户心里想的是"我要做国风"，不是"我要在流行这一类里找国风"，
// 所以搜的时候把所有大类的命中都列出来，并在卡片上标出它属于哪个大类。
let presetQuery = "";
function presetHit(p, fam, q) {
  if (!q) return true;
  return [p.zh, p.en, p.more, p.vocal, String(p.bpm),
          (p.mood || []).join(" "), (p.inst || []).join(" "), (p.feel || []).join(" "),
          fam.zh, fam.id].join(" ").toLowerCase().includes(q);
}
function renderPresetGrid() {
  const el = $("presetGrid");
  if (!el) return;
  el.innerHTML = "";
  const q = presetQuery.trim().toLowerCase();
  const pool = [];
  FAMS.forEach(f => {
    if (q || f.id === asm.fam) f.items.forEach(p => { if (presetHit(p, f, q)) pool.push({ p, f }); });
  });
  // ★ 本大类的所有配方恒定全展开（用户 2026-10-15："下面的选项不用折叠"）。
  //   以前只露前 8 张、其余收进「+N 张 ▾」——曲风卡片是要"看全了比较"的东西，
  //   藏起来的那几张用户根本不知道存在，等于凭空少了几个选项。
  const list = pool;
  if (!pool.length) {
    const s = document.createElement("span");
    s.className = "nomatch";
    s.textContent = "没有匹配「" + presetQuery.trim() + "」的曲风配方（换个词试试，如 国风 / city pop / 二胡 / 808）";
    el.appendChild(s);
  }
  list.forEach(({ p, f }) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "pcard" + (p.id === asm.preset ? " on" : "");
    b.innerHTML = (q ? "<em>" + escapeHtml(f.zh) + "</em>" : "") +
                  "<b>" + escapeHtml(p.zh) + "</b><span>" + escapeHtml(p.en) +
                  " · " + p.bpm + "</span>";
    b.title = f.zh + " · " + p.en + " · " + p.bpm + " BPM\n情绪：" + (p.mood || []).join(" / ") +
              "\n配器：" + (p.inst || []).join(" / ") +
              "\n人声：" + (p.vocal || "器乐/不写人声") + "\n" + (p.more || "");
    // 跨大类命中时由 applyPreset 把页签切过去（保证"我的预设"等其它入口也走同一条路）
    b.onclick = () => applyPreset(p);
    el.appendChild(b);
  });
}

function applyPreset(p) {
  asm.preset = p.id;
  asm.mood = [...(p.mood || [])];
  asm.inst = [...(p.inst || [])];
  asm.feel = [...(p.feel || [])];
  asm.timbre = p.vocal ? [p.vocal] : [];
  if ($("aGenre")) $("aGenre").value = p.en;
  if ($("bpm")) $("bpm").value = p.bpm || "";
  if ($("aMore")) $("aMore").value = p.more || "";
  // 器乐配方会把人声格子落到"不写人声"；但那是卡片替用户做的决定，
  // 下一张有人声的卡片要把它还原回去，否则会莫名其妙一直出不了人声。
  const gs = $("gender");
  if (gs) {
    if (!p.vocal && gs.value) { autoClearedGender = gs.value; setSel(gs, ""); }
    else if (p.vocal && !gs.value && autoClearedGender) { setSel(gs, autoClearedGender); autoClearedGender = null; }
  }
  renderPresetGrid();
  renderAllMulti();
  syncGenreNow();
  recompile(true);   // 点卡片是明确的采用意图 → 覆盖文本
  // 跨大类搜索命中的卡片也要把页签切过去，否则左边 Style 用的是"中国风"的配方、
  // 右边页签还亮着"流行"，两处真相差一个类。
  const owner = FAMS.find(f => f.items.some(x => x.id === p.id));
  if (owner && owner.id !== asm.fam) { asm.fam = owner.id; renderFamTabs(); }
  toast("已填入「" + p.zh + "」配方", "ok");
}
function syncGenreNow() {
  const el = $("genreNow");
  if (!el) return;
  const g = (($("aGenre") || {}).value || "").trim();
  el.textContent = g || "—";
  el.title = g ? "当前曲风词：" + g : "尚未选择曲风";
}
function updateAsmHint() {
  const el = $("presetNote");
  if (!el) return;
  if (typeof updateStyleRecipe === "function") updateStyleRecipe();
  const preview = compileStyle();
  const cur = (($("styleAsm") || {}).value || "").trim();
  // 预算说的是"真正会提交的那一句"：没手改时就是装配产物，手改后就是文本框本身。
  const b = budgetOf(styleDirty ? (cur || preview) : preview);
  // 这段文字只在"状态确实不一样"时才有信息量，而且必须**短**——
  // 以前这里是三段长句（最长 118px），把左栏顶出可视区、主按钮沉到折线以下，
  // 而且和顶部 budgetrow 的预算数字、上面文本框里的 Style 正文**三重重复**。
  // 现在压成一行状态 + 一条可展开的说明：默认只占一行，超出部分进 <details>。
  // 预算数字**只报一次**——顶部 budgetrow 的 #asmBudget 是它唯一的家，
  // 这里再报一遍就是重复（旧版两处都报，用户看到两个说法不一的数字）。
  let state, tip;
  if (styleDirty) {
    state = "⚠ Style 已被手改 —— 装配台只刷新预览，不再覆盖文本框";
    tip = "第 ③ 步的改动不会写回这个文本框。要按六要素重写它，点「↻ 重新装配到 Style」。";
  } else if (cur && cur !== preview) {
    state = "沿用上次的 Style 文本；点卡片或动一格即按配方重写";
    tip = "点一张曲风卡片（第 ② 步）或动任何一格（第 ③ 步），就会按配方重写它。";
  } else {
    state = "装配台与 Style 文本已同步";
    tip = "点一张曲风卡片 = 一次性把六个维度填满并写进 Style；六个维度是卡片的明细，任何一格都能单独增删，改动实时重写 Style 文本。一旦你在文本框里手改，装配台就只给预览、不再覆盖产出框。";
  }
  el.innerHTML = '<span class="pn-state">' + state + '</span>' +
    '<details class="pn-more"><summary>?</summary><div>' + tip + '</div></details>';
  el.title = tip;
  const m = $("asmBudget");
  if (m) {
    m.textContent = b.phrases + " 短语 · " + b.words + " 词 · " + b.chars + " 字符" +
      (b.level === "ok" ? "" : b.level === "soft" ? " · 超建议" : " · 偏多");
    m.className = "budget " + b.level;
    m.title = "这就是要提交给模型的那一句的长度。官方评测样本平均 " + PHRASE_SOFT +
      " 条短语 / 约 " + WORD_SOFT + " 词 / " + CHAR_SOFT + " 字符；本机最长的一条成功生成" +
      "（20 短语 / 330 字符）也出了片，所以这不是上限，而是" +
      (b.level === "ok" ? "信息密度还很富余。" : b.level === "soft" ?
        "每个词分到的注意力开始变少。" : "后面追加的词基本会被前面的淹没。");
  }
}

// ---- 左栏自检：Style 文本缺哪一要素 ----
// tender / soulful / hypnotic 这三个词本轮从「情绪」表挪到了「人声音色 / 律动」表，
// 但用户历史上存下来的 Style 文本里它们还在当情绪用。自检只负责告诉用户"这一格覆盖了没有"，
// 所以这里继续认它们为情绪词，免得老草稿一打开就被误报"缺情绪"。
const MOOD_SET = new Set([...MOODS.map(s => s.toLowerCase()), "tender", "soulful", "hypnotic"]);
const INST_WORDS = new Set(INSTRS.flatMap(s => s.toLowerCase().split(/[\s\-]+/)).filter(Boolean));
const SIX_LABELS = [["语言", "lang"], ["曲风", "genre"], ["情绪", "mood"],
                    ["人声", "vocal"], ["配器", "inst"], ["节奏", "tempo"]];
function analyzeStyle(text) {
  const t = (text || "").trim();
  const phrases = t.split(",").map(s => s.trim()).filter(Boolean);
  const lower = t.toLowerCase();
  const words = new Set(lower.match(/[a-z][a-z']*/g) || []);
  const first = (phrases[0] || "").toLowerCase();
  const hit = (pred) => phrases.find(p => pred(p.toLowerCase())) || "";
  return {
    phrases,
    has: {
      lang: LANGS.some(([v]) => v.toLowerCase() === first) || /^(mandarin|cantonese|english|japanese|korean|spanish|instrumental)\b/.test(first),
      genre: !!hit(p => p.split(/[\s\-]+/).some(w => GENRE_WORDS.has(w))),
      mood: [...words].some(w => MOOD_SET.has(w)),
      vocal: /female|male|duet|children|choir|\bvocal\b|\bvoice\b/.test(lower),
      inst: !!hit(p => p.split(/[\s\-]+/).some(w => INST_WORDS.has(w))),
      tempo: /\b\d{2,3}\s*bpm\b|slow|upbeat|fast|mid-tempo|danceable|tempo|ballad|waltz|driving|laid-back/.test(lower),
    },
  };
}
function updateStyleChecks() {
  const box = $("styleAsm");
  if (!box) return;
  const text = box.value;
  const { phrases, has } = analyzeStyle(text);
  const six = $("sixBox");
  if (six) {
    six.innerHTML = "";
    SIX_LABELS.forEach(([zh, k]) => {
      const d = document.createElement("div");
      if (has[k]) d.className = "hit";
      const ex = typeof has[k] === "string" ? has[k] : "";
      d.innerHTML = "<i>" + (has[k] ? "✓" : "○") + "</i>" + zh +
                    (ex ? "<em title=\"" + escapeHtml(ex) + "\">" + escapeHtml(ex) + "</em>" : "");
      six.appendChild(d);
    });
  }
  const lint = $("styleLint");
  if (!lint) return;
  const msgs = [];
  const t = text.trim();
  if (!t) msgs.push(["err", "Style 为空 —— 提交会被拦，先点一张曲风卡片或手写一句。"]);
  else {
    if (phrases.length < 2) msgs.push(["warn", "只有 1 条短语，六要素建议至少写满 语言 / 曲风 / 人声 / 节奏 四条。"]);
    // 12 条短语是官方样本的均值（信息预算），不是模型上限——本机 112 条历史里最长的
    // 一条 20 短语 / 330 字符同样 status=done 出了音频。所以这里只报"分量变轻"，
    // 不说"超出会被截断/失败"，那种说法是错的。
    // 三个数一起报（短语 / 词 / 字符）：多个情绪会拼成一条短语，只报短语数会把真实词量藏起来。
    const b = budgetOf(t);
    if (b.level === "hard") msgs.push(["warn", "信息量偏大：" + b.phrases + " 条短语 / " + b.words + " 词 / " + b.chars + " 字符（" + b.over.join("，") + "）。能生成，但越靠后的词越容易被前面的淹没，删到 " + PHRASE_SOFT + " 条左右更稳。"]);
    else if (b.level === "soft") msgs.push(["tip", "比官方样本多一些：" + b.phrases + " 条短语 / " + b.words + " 词 / " + b.chars + " 字符（均值 " + PHRASE_SOFT + " 条 / " + WORD_SOFT + " 词 / " + CHAR_SOFT + " 字符）。能生成，不拦；只是每个词分到的注意力变少，重点要素记得放前面。"]);
    if (/[\u4e00-\u9fa5]/.test(t)) msgs.push(["warn", "含中文：Style 要用英文，中文不进模型。"]);
    const empty = EMPTY_WORDS.filter(w => new RegExp("\\b" + w + "\\b", "i").test(t));
    if (empty.length) msgs.push(["warn", "空词 " + empty.join(" / ") + "：不带可执行信息（官方表里有，但本机裁定不写）。"]);
    if (/\b(verse|chorus|bridge|intro|outro)\b/i.test(t)) msgs.push(["warn", "Style 里出现了段落词（verse/chorus…）：分段是歌词的事，曲风描述不写结构。"]);
    // 整条短语重复才报（不做"单词重复"那档）：像 "piano ballad, … acoustic piano" 里
    // piano 出现两次是完全正常的，按单词查重会天天误报；而一模一样的短语写两遍
    // 只会白占预算——通常是同一词在「情绪」和「人声」两格都点了（tender 这类历史遗留词）。
    const phSeen = new Map();
    phrases.forEach(p => { const k = p.toLowerCase(); phSeen.set(k, (phSeen.get(k) || 0) + 1); });
    const dupPh = [...phSeen].filter(([, n]) => n > 1).map(([k]) => k);
    if (dupPh.length) msgs.push(["warn", "重复短语：" + dupPh.join(" / ") + "。同一条写了两次，删掉一处就够（六个维度里有同一词时最容易发生）。"]);
    if (!has.tempo) msgs.push(["tip", "没写 BPM：节奏由模型自己决定，想控制就在第 6 格填或直接在文本尾部加 “88 BPM”。"]);
    if (!has.vocal && (($("aLang") || {}).value || "") !== "Instrumental") msgs.push(["tip", "没写人声：默认由模型决定性别，想指定就用第 4 格。"]);
    const miss = SIX_LABELS.filter(([zh, k]) => !has[k]).map(([zh]) => zh);
    // 「缺哪一格」**不再在这里重复**：上面那排 ✓/○ 状态条已经在讲同一件事，
    // 文字再写一遍"还缺：情绪"是同一信息出现两次（用户会以为两处在说不同的问题）。
    // 这里只在"真的能采取行动"时出声；全部覆盖、又没别的问题时，给一句明确的正向收尾。
    // （以前这条分支排在其它提示之前，缺「情绪」时上面是空 ○、下面却写"六要素齐了"，
    //   自相矛盾的绿灯——现在只有在 miss 为空时才敢说"齐了"。）
    if (!msgs.length && !miss.length) msgs.push(["ok", "六要素齐了，提交内容就是左边文本框里这一句。"]);
    else if (msgs.length && miss.length) msgs.push(["tip", "未覆盖：" + miss.join(" · ")]);
    // msgs 为空且 miss 非空 → 什么都不说：状态条已经画出来了，保持这一行安静。
  }
  lint.innerHTML = msgs.map(([lv, m]) => {
    const icon = lv === "ok" ? "✓ " : lv === "warn" ? "⚠ " : "· ";
    // 走主题令牌 --ok，不写死浅绿：#7bc47f 压在浅色面板上只有 1.8:1，
    // 而 --ok 是按两套主题各量过的（浅 #3f6a33 / 深 #7fae6d）。
    return "<div" + (lv === "ok" ? ' style="color:var(--ok)"' : "") + ">" + icon + escapeHtml(m) + "</div>";
  }).join("");
}

// ---- 我的预设（服务端 templates.json，与 AI 工作台共用同一份存储）----
let myPresets = [];
function renderMyPresets() {
  const el = $("myPresetChips");
  if (!el) return;
  el.innerHTML = "";
  if (!myPresets.length) {
    const s = document.createElement("span");
    s.className = "hint-xs"; s.textContent = "我的预设：还没有。在上面调好六格后点「💾 存为我的预设」。";
    el.appendChild(s);
    return;
  }
  myPresets.forEach(t => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip mini";
    b.textContent = "★ " + (t.name || "未命名");
    b.title = (t.style || "") + "\n存于 " + (t.created_at || "").slice(0, 16).replace("T", " ") + "\n点击回填这套配方（只动本页，不碰歌词/档位/乐谱）";
    b.onclick = () => restoreMyPreset(t);
    const x = document.createElement("button");
    x.type = "button"; x.className = "chip mini"; x.textContent = "✕";
    x.title = "删除这条预设";
    x.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm("删除预设「" + (t.name || "") + "」？")) return;
      try {
        const r = await fetch("/api/templates/" + encodeURIComponent(t.id), { method: "DELETE" });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "HTTP " + r.status);
        myPresets = myPresets.filter(p => p.id !== t.id);
        renderMyPresets();
        toast("已删除", "ok");
      } catch (err) { toast("删除失败：" + friendlyErr(err), "err"); }
    };
    el.appendChild(b);
    el.appendChild(x);
  });
}
async function loadMyPresets() {
  try {
    const r = await fetch("/api/templates");
    const j = await r.json();
    myPresets = (j.templates || []).filter(t => t.style || t.asm);
  } catch { myPresets = []; }
  renderMyPresets();
}
function restoreMyPreset(t) {
  const a = t.asm || {};
  ["mood", "timbre", "inst", "feel"].forEach(k => { asm[k] = Array.isArray(a[k]) ? [...a[k]] : []; });
  if (a.fam) asm.fam = a.fam;
  asm.preset = a.preset || "";
  setSel($("aLang"), a.lang != null ? a.lang : (($("aLang") || {}).value || ""));
  setSel($("gender"), a.gender != null ? a.gender : (($("gender") || {}).value || ""));
  setSel($("aKey"), a.key != null ? a.key : (($("aKey") || {}).value || ""));
  if ($("aGenre") && a.genre != null) $("aGenre").value = a.genre;
  if ($("aMore") && a.more != null) $("aMore").value = a.more;
  if ($("bpm") && a.bpm != null) $("bpm").value = a.bpm;
  // 预设**不再**往别的页灌东西：以前这三行会改 ②⑤ 的档位、② 的歌词、③⑤ 的谱框，
  // 用户在 ① 点一下"回填我的预设"，另一页的内容就被换掉了而且毫无线索（评审 F-3）。
  // 一条曲风配方就是曲风配方——它只该由本页的六要素与那句 Style 组成。
  // 老预设里存着的 lyrics/cot/abc 就此作废，不再被读取。
  renderFamTabs();
  renderPresetGrid();
  renderAllMulti();
  syncGenreNow();
  if (t.style) { writeStyleBox(t.style); styleDirty = t.style.trim() !== compileStyle().trim(); }
  else recompile(true);
  updateStyleChecks();
  updateAsmHint();
  saveAsm();
  toast("已回填预设「" + (t.name || "") + "」", "ok");
}
async function saveMyPreset() {
  const name = (prompt("给这套配方起个名字（存进服务端，AI 工作台也能看到）：",
                       (($("aGenre") || {}).value || "").trim() || "我的曲风") || "").trim();
  if (!name) return;
  const payload = {
    name,
    taskName: (($("taskName") || {}).value || "").trim(),
    style: ($("styleAsm") || {}).value.trim(),
    // 不存 lyrics / cot / abc：那是 ②⑤ 与 ③⑤ 的输入，存进 ① 的预设里
    // 就等于承认"一条曲风配方附带别人的歌词和档位"（评审 F-3）。
    gender: ($("gender") || {}).value,
    asm: { ...asmSnapshot() },
  };
  try {
    const r = await fetch("/api/templates", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || "HTTP " + r.status);
    myPresets.unshift(j.template || { name, asm: payload.asm, style: payload.style });
    renderMyPresets();
    toast("已存为我的预设：" + name, "ok");
  } catch (err) {
    toast("保存失败：" + friendlyErr(err), "err");
  }
}
function asmSnapshot() {
  return {
    fam: asm.fam, preset: asm.preset, mood: asm.mood, timbre: asm.timbre,
    inst: asm.inst, feel: asm.feel,
    lang: ($("aLang") || {}).value || "", genre: ($("aGenre") || {}).value || "",
    more: ($("aMore") || {}).value || "", key: ($("aKey") || {}).value || "",
    bpm: ($("bpm") || {}).value || "", gender: ($("gender") || {}).value || "",
  };
}

// ---- 歌词结构与骨架 ----
// 6 段最小曲式骨架：本机裁定的 4–8 行只豁免 [Intro]/[Interlude]（纯器乐段），
// [Outro] 不在豁免名单里 —— 所以骨架以最后一次副歌收尾，不挂空 Outro。
const LYRIC_SKELETON = [
  "[Intro]", "",
  "[Verse 1]", "（第 1 行：给一个具体的画面）", "（第 2 行）", "（第 3 行）", "（第 4 行）", "",
  "[Chorus]", "（副歌 4 行：标题落在最后一行）", "（第 2 行）", "（第 3 行）", "（第 4 行）", "",
  "[Verse 2]", "（第 1 行：换一个细节推进）", "（第 2 行）", "（第 3 行）", "（第 4 行）", "",
  "[Chorus]", "（副歌 4 行：标题落在最后一行）", "（第 2 行）", "（第 3 行）", "（第 4 行）", "",
  "[Bridge]", "（第 1 行：换个角度或时间）", "（第 2 行）", "（第 3 行）", "（第 4 行）", "",
  "[Chorus]", "（副歌 4 行：标题落在最后一行）", "（第 2 行）", "（第 3 行）", "（第 4 行）", "",
].join("\n");
let lyricLintHasErr = false;
function showLyricLint(lines, cls) {
  lyricLintHasErr = Array.isArray(lines) && lines.some(l => /^✕/.test(String(l)));
  const el = $("lyricLint");
  if (el) { el.innerHTML = lines.join("<br>"); el.style.color = cls || ""; }
  updateLyricHelp();
  return el;
}
// 把一份校验结果排成要显示的几行（错误在前、警告在后，全过就报一句好消息）
function lintLines(v) {
  const lines = [];
  (v.errors || []).forEach(m => lines.push("✕ " + escapeHtml(m)));
  (v.warnings || []).forEach(m => lines.push("⚠ " + escapeHtml(m)));
  if (!lines.length) lines.push("✓ 结构与本机校验规则一致（" + (v.sections || 0) + " 段）。听感与倒字仍需人耳。");
  else lines.push("<span style=\"opacity:.75\">规则真源：服务端 _validate_lyrics_impl（与 AI 工作台同一个实现）</span>");
  return lines;
}
// ---- 歌词的三道护栏：提交前自动校验 / 覆盖可撤销 / 没词时指路 ----
// 为什么把校验挪到提交前：一次完整生成 60-90 分钟（草稿档 1-2 分钟）、6GB 显存全程占着，而"副歌没写全 /
// 某段只有 3 行"这类结构错原来只有用户自己想起去点「🔍」才会发现。
// 校验接口挂了不拦路：拿不到结论就放行，但如实说一句本次没校验成（不静默降级）。
async function validateLyricsNow() {
  // 校验的对象必须是"真正会送出去的那份词"：机器补的收尾说明行由后端在送引擎前加，
  // 不该出现在这里被判成用户的结构错误。
  const txt = stripMachineOutro(($("lyrics") || {}).value || "");
  if (!txt.trim()) return { ok: true, empty: true, errors: [], warnings: [] };
  try {
    const r = await fetch("/api/lyrics/validate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lyrics: txt }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return { ok: true, skipped: String(j.detail || ("HTTP " + r.status)), errors: [], warnings: [] };
    return { ok: !(j.errors || []).length, errors: j.errors || [], warnings: j.warnings || [], sections: j.sections || 0 };
  } catch (e) {
    return { ok: true, skipped: String((e && e.message) || e), errors: [], warnings: [] };
  }
}
async function checkLyrics() {
  const btn = $("lyricCheck");
  if (!($("lyrics") || {}).value.trim()) { showLyricLint(["· 歌词为空，先填或点「＋ 插入曲式骨架」。"]); return; }
  if (btn) btn.disabled = true;   // 校验要过服务端，连点会排出一串请求、结果还会后发先至
  const el = $("lyricLint"); if (el) el.textContent = "校验中…";
  try {
    const v = await validateLyricsNow();
    if (v.skipped) { showLyricLint(["· 校验接口不可用：" + escapeHtml(v.skipped) + "（网关重启后 /api/lyrics/validate 才生效）"]); return; }
    showLyricLint(lintLines(v), v.errors.length ? "var(--err)" : "");
  } finally {
    if (btn) btn.disabled = false;
  }
}
// 「仍然提交」：拦下来之后必须留一条明路，否则遇到"我就是要这样试"的场景就把人堵死。
// 点过一次即放行；之后改过歌词框，放行自动作废（见 #lyrics 的 input 绑定）。
let lyricsForce = false;
function lintWithForce(v, target) {
  const el = showLyricLint(lintLines(v), "var(--err)");
  if (!el) return;
  const b = document.createElement("button");
  b.type = "button"; b.className = "ghost"; b.style.cssText = "margin-top:7px;";
  b.textContent = "仍然提交（我知道结构有问题）";
  b.title = "明知有 " + v.errors.length + " 处结构问题还要生成一次。改过歌词框后这个放行会自动作废。";
  b.onclick = () => { lyricsForce = true; const t = $(target); if (t) t.click(); };
  el.appendChild(b);
  // 歌词框被聚焦时会把它自己滚进视野，报错行往往正好被顶出去——报错就在框下面，
  // 用 nearest 再拉一次：已经在屏上就不动，不在屏上才滚。
  requestAnimationFrame(() => { try { el.scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch {} });
}
async function lyricsGate(target) {
  if (lyricsForce) return true;
  const v = await validateLyricsNow();
  if (v.ok) {
    // 上一次拦下来的红字必须清掉：留着会让人以为还在被拦，而实际上歌已经在生成了。
    // 反过来，"接口没连通"也不能画成 ✓：拿不到结论就说拿不到结论，
    // 更要把上一次那行 ✓ 顶掉——不然屏幕上挂的是上一版词的结论，看着像这一版过了。
    if (v.skipped) {
      showLyricLint(["· 歌词结构这次没校验成：" + escapeHtml(v.skipped) + "（不拦你提交，但这一版没人检过）"], "");
      toast("歌词结构这次没校验成（" + v.skipped + "），直接提交了", "warn");
      return true;
    }
    if (lyricLintHasErr || (v.warnings || []).length) showLyricLint(lintLines(v), "");
    return true;
  }
  lintWithForce(v, target);
  fieldProblem($("lyrics"), $("status"),
    "歌词结构有 " + v.errors.length + " 处问题，已列在歌词框下面 —— 改好再点一次；确实要照现在这样生成，就点那行「仍然提交」。");
  return false;
}
// 护栏①的接线点：校验要过服务端（几十到几百毫秒），期间必须把提交按钮按住，
// 否则连点会排出两次校验、第二次还会盖掉第一次的报错。放行/拦下都在这一个函数里。
async function guardThen(btn, target) {
  if (!btn) return lyricsGate(target);
  const was = btn.disabled;   // 生成中按钮本来就是禁的：校验完不能把它错误地点亮
  btn.disabled = true;
  try { return await lyricsGate(target); } finally { btn.disabled = was; }
}
// 覆盖前入栈：歌词候选、最近歌词、骨架插入，三条路都会整块换掉歌词框内容。
// 以前只有骨架有 confirm，点错一张卡片旧词就当场没了——手写二十行词的人经不起这种丢法。
const lyricUndoStack = [];
// 998a99d 那次补 [Outro] 是加在存盘之前，所以从那版到本次修复之间生成的记录，
// lyrics 里都带着这句用户没写过的话。回填时摘掉它——提交时后端会在送引擎的最后一刻
// 按同一规则重新补上，一字不差，听感不变。
function stripMachineOutro(t) {
  return String(t || "").replace(/\n+\[Outro\][ \t]*\n[ \t]*（器乐渐弱收尾）[ \t]*\n*$/i, "").replace(/\s+$/, "");
}
function pushLyricUndo(why) {
  const box = $("lyrics"); if (!box) return;
  const v = box.value;
  if (!v.trim()) return;                                  // 空框不值得留档
  if (lyricUndoStack.length && lyricUndoStack[lyricUndoStack.length - 1].v === v) return;   // 连点同一张卡片不重复入栈
  lyricUndoStack.push({ v, why });
  if (lyricUndoStack.length > 5) lyricUndoStack.shift();
  renderLyricUndo();
}
function renderLyricUndo() {
  const b = $("lyricUndo"); if (!b) return;
  const top = lyricUndoStack[lyricUndoStack.length - 1];
  b.style.display = top ? "" : "none";
  if (!top) return;
  b.textContent = "↶ 恢复上一版" + (lyricUndoStack.length > 1 ? "（" + lyricUndoStack.length + " 步）" : "");
  b.title = "撤销刚才那次「" + top.why + "」，把歌词换回之前那一版（" +
            top.v.split("\n").filter(Boolean).length + " 行）。最多连撤 " + lyricUndoStack.length + " 步。";
}
function popLyricUndo() {
  const box = $("lyrics"); if (!box) return;
  const top = lyricUndoStack.pop();
  if (!top) return;
  box.value = top.v;
  try { box.dispatchEvent(new Event("input", { bubbles: true })); } catch {}
  flashField(box);
  renderLyricUndo();
  toast(lyricUndoStack.length ? "已换回上一版，还可再撤 " + lyricUndoStack.length + " 步" : "已换回最初那一版", "ok");
}
// 从参考音频识别歌词（翻唱路线）。
// 这一步原先挂在 ⑧ 的「YuE2 参考音色」栏里，跟"存一个音色"绑在一起；
// 但那栏存下来的音频复制不了音色（YuE2 没有参考音频参数），整栏已删，
// 而"把原唱的词识别出来"是真有用的，所以搬到它真正属于的地方——歌词框旁边。
async function transcribeLyricAudio(file) {
  const btn = $("lyricFromAudio");
  const st = $("status");
  if (btn) { btn.disabled = true; btn.textContent = "识别中…"; }
  const done = (msg, kind) => {
    if (btn) { btn.disabled = false; btn.textContent = "🎤 从参考音频识别歌词"; }
    if (st) { st.className = "status" + (kind ? " " + kind : ""); st.textContent = msg; }
    toast(msg, kind === "err" ? "err" : "ok");
  };
  try {
    const fd = new FormData(); fd.append("audio", file);
    const r = await fetch("/api/voices/transcribe", { method: "POST", body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || "识别失败");
    if (!j.text) { done("这段音频没能识别出文字（换一段干净的人声试试）", "err"); return; }
    const box = $("lyrics");
    if (box) {
      // 有词就先问：识别结果会整段替换，而用户可能只是想把第二段接上去
      if (box.value.trim() && !confirm("歌词框里已经有内容：\n· 确定 = 用识别结果整段替换\n· 取消 = 什么都不改")) { done("已取消，歌词未改动"); return; }
      pushLyricUndo("把参考音频识别成歌词");
      box.value = j.text;
      try { box.dispatchEvent(new Event("input", { bubbles: true })); } catch {}
      flashField(box);
    }
    // 翻唱走 melody（只规划旋律，伴奏由模型配），这是 YuE2 官方翻唱路线
    if ($("cot")) $("cot").value = "melody";
    syncGenPresetFromFields();
    if (typeof checkLyrics === "function") checkLyrics();
    done("已识别并填入歌词（" + j.text.split("\n").filter(Boolean).length + " 行），CoT 已设为 melody", "ok");
  } catch (e) {
    done("识别失败：" + friendlyErr(e), "err");
  }
}
// 词从哪来：只在"框是空的"或"刚校验出错"时出现，平时不收着占左栏高度。
// 内嵌在 dsh 壳里时「AI 工作台」页签是隐藏的（壳本身就是工作台），所以两种模式指的路不同。
function updateLyricHelp() {
  const el = $("lyricHelp"); if (!el) return;
  const empty = !(($("lyrics") || {}).value || "").trim();
  const show = empty || lyricLintHasErr;
  el.style.display = show ? "" : "none";
  if (!show) return;
  const embed = document.documentElement.classList.contains("embed");
  el.innerHTML = "词从哪来：" + (embed
      ? "在<b>左边的对话框</b>里说「帮我写一首……」，让它把整首词写完，你整段粘回这个框"
      : "切到<b>「AI 工作台」</b>页签说「帮我写一首……」，让它把整首词写完，你整段粘回这个框")
    + "；要翻唱就把原唱音频交给「🎤 从参考音频识别歌词」，词直接落进这个框"
    + "（<code>[Verse]</code> <code>[Chorus]</code> 标签和段间空行都留着）。只想先搭架子就点「＋ 插入曲式骨架」，把括号里的提示换成自己的话。";
}

// ---- 参数摘要（护栏③：折叠之后仍要看得见自己填了什么）----
// 只写"会改变结果的四件事"：量化 / 谱走哪条路 / seed / 首数。
// steps、cfg 这些默认值不进摘要——一行塞满反而让人以为它们也得天天管。
function updateParamSummary() {
  const el = $("paramSum");
  if (!el) return;
  const cot = ($("cot") || {}).value || "full";
  // ★ 摘要要说"本页真实交出去的东西"。②⑤ 的提交永远不带谱（见 abcForSubmit），
  //   而 #abc 是与 ③⑤ 共用的一块 DOM，切页时里面可能还留着上一首的谱——
  //   照旧读它，这一行就会写"按谱走 melody"，实际交的却是空谱
  //   （评审 F-5：和七里香事故同一个方向，界面向用户描述了一份并不存在的输入）。
  const hasAbc = curTab === "scoreSing" && !!((($("abc") || {}).value || "").trim());
  const route = (hasAbc && lastAbcAnalysis && lastAbcAnalysis.cot_suggested) || "";
  const seedRaw = (($("seed") || {}).value || "-1").trim();
  const fixed = seedRaw !== "" && seedRaw !== "-1";
  const n = Math.max(1, Math.min(20, numOrNull("genCount") || 1));
  const ms = $("modelSel");
  // 量化名自带中文注解（"Q4_K_M（省显存 · 6GB卡首选）"），摘要只要代号：
  // 括号里那句是选型说明，塞进一行摘要会把 seed/首数挤到看不见。
  const quant = ms && ms.selectedOptions && ms.selectedOptions[0]
    ? String(ms.selectedOptions[0].textContent).split(/[（(]/)[0].trim() : "";
  const bits = [];
  if (quant) bits.push(quant);
  bits.push(route ? (route === cot ? "按谱走 " + route : "谱要 " + route + "，档位选了 " + cot)
                  : (hasAbc ? cot + "·谱待分析" : cot));
  bits.push(fixed ? "seed " + seedRaw : "seed 随机");
  if (n > 1) bits.push(n + " 首");
  // 用户自己没定种子（-1/空）而这一首已经落了一个真实值 —— 把它说出来，
  // 但不塞进输入框：看得见实际用了什么，同时输入框仍然表达"我的选择是随机"。
  if (!fixed && runningSeedActual !== null && runningSeedActual !== undefined)
    bits.push("本次实际种子 " + runningSeedActual);
  el.textContent = bits.join(" · ");
}
// 歌词框手改过，先前点过的「仍然提交」立刻作废：放行只针对当时那一版词。
{ const box = $("lyrics");
  if (box) box.addEventListener("input", () => {
    if (lyricsForce) lyricsForce = false;
    updateLyricHelp();
  }); }
// 参数区任何一格改动都要刷新摘要：委托一次，不给每个控件各挂监听。
document.addEventListener("input", (ev) => {
  if (ev.target && /^(cot|seed|steps|cfg|genCount|abc|modelSel)$/.test(ev.target.id || "")) updateParamSummary();
});
document.addEventListener("change", (ev) => {
  if (ev.target && /^(cot|seed|steps|cfg|genCount|modelSel)$/.test(ev.target.id || "")) updateParamSummary();
});

// ---- 装配台初始化 ----
function fillSelect(el, opts, dflt) {
  if (!el) return;
  el.innerHTML = "";
  opts.forEach(([v, label]) => {
    const o = document.createElement("option");
    o.value = v; o.textContent = label;
    el.appendChild(o);
  });
  setSel(el, dflt);
}
function initAssembler() {
  // 表单自动恢复（yue2_compose_form）可能已经往 #gender 里写过旧值：先接着，别被默认值盖掉
  const preGender = (($("gender") || {}).value || "").trim();
  fillSelect($("aLang"), [["", "— 选择语种 —"], ...LANGS.map(([v, zh]) => [v, zh + " · " + v])], "English");
  fillSelect($("gender"), VOICES.map(([v, zh]) => [v, v ? zh : "纯音乐 · 不写人声"]), "clear female vocal");
  fillSelect($("aKey"), KEYS.map(k => [k, k || "— 不指定调性 —"]), "");

  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(ASM_KEY) || "null"); } catch {}
  const hadSnapshot = !!(saved && typeof saved === "object");
  if (hadSnapshot) {
    ["mood", "timbre", "inst", "feel"].forEach(k => { if (Array.isArray(saved[k])) asm[k] = saved[k]; });
    if (saved.fam && FAMS.some(f => f.id === saved.fam)) asm.fam = saved.fam;
    asm.preset = saved.preset || "";
    setSel($("aLang"), saved.lang || "");
    setSel($("gender"), saved.gender != null ? saved.gender : "clear female vocal");
    setSel($("aKey"), saved.key || "");
    if ($("aGenre") && saved.genre != null) $("aGenre").value = saved.genre;
    if ($("aMore") && saved.more != null) $("aMore").value = saved.more;
    if ($("bpm") && saved.bpm !== undefined) $("bpm").value = saved.bpm;
    // 手改过的产出文本要盖回框里（写在下面渲染之后才有效，这里先存进 pending）。
    if (saved.asmText) window.__asmTextPending = saved.asmText;
  } else {
    // 第一次用（或换了浏览器）：没有装配台快照。从现有 Style 文本里接住语种 / 人声 / BPM
    // 三格硬信息，其余留空。此时不算"手改"，但文本也不是装配台产物 —— 点任何一格即接管。
    if (preGender) setSel($("gender"), preGender);
    const parts = (($("styleAsm") || {}).value || "").split(",").map(s => s.trim()).filter(Boolean);
    if (parts.length) {
      setSel($("aLang"), parts[0]);
      const g = parts.find(p => /\b(female|male)\b/i.test(p) && /vocal|voice/i.test(p));
      if (g) {
        const m = /\b(female|male)\b/i.exec(g);
        setSel($("gender"), "clear " + m[1].toLowerCase() + " vocal");
      }
      const b = parts.join(",").match(/\b(\d{2,3})\s*BPM\b/i);
      if (b && $("bpm")) $("bpm").value = b[1];
      // 剩下的短语也要接回装配台，否则用户点第一格芯片时装配台按"空配方"重算，
      // 官方样例那句会被压成「English, uplifting, 88 BPM」——曲风、配器、氛围全丢了，
      // 而且一声不吭。规则保守：整条正好等于词表里的词才归格，其余逐字进
      // 「曲风」（含曲风词的）或「补充」（其余），不做半句拆解、不做删减。
      const VOCAB = [["inst", INSTRS], ["mood", MOODS], ["feel", FEELS], ["timbre", TIMBRES]];
      const genreHits = [], leftover = [];
      parts.slice(1).forEach(p => {
        if (p === g || /\b\d{2,3}\s*BPM\b/i.test(p)) return;
        const low = p.toLowerCase();
        const toks = low.split(/[\s\-/,]+/).filter(Boolean);
        // 整条等于词表里的词，或整条每个词都在同一个词表里 —— 才敢归格。
        // 只看"含不含曲风词"会把「uplifting energetic」当曲风（uplifting 出现在某条曲风配方名里），
        // 于是情绪词被搬进曲风格子，用户再点情绪芯片就出现同词重复。
        const cell = VOCAB.find(([, list]) => {
          if (list.some(w => w.toLowerCase() === low)) return true;
          const set = new Set(list.map(w => w.toLowerCase()));
          return toks.length > 0 && toks.every(t => set.has(t));
        });
        if (cell) { if (!asm[cell[0]].includes(p)) asm[cell[0]].push(p); return; }
        (low.split(/[\s\-/,]+/).some(w => GENRE_WORDS.has(w)) ? genreHits : leftover).push(p);
      });
      if (genreHits.length && $("aGenre")) $("aGenre").value = genreHits.join(", ");
      if (leftover.length && $("aMore")) $("aMore").value = leftover.join(", ");
    }
  }
  renderFamTabs();
  renderPresetGrid();
  renderAllMulti();
  syncGenreNow();

  // 六格里任何一格改动 → 重算；Style 文本框手改 → 进入 dirty 模式
  [["aGenre", 1], ["aMore", 1], ["bpm", 1], ["aKey", 1], ["aLang", 1], ["gender", 1]].forEach(([id]) => {
    const el = $(id);
    if (!el) return;
    const h = () => {
      if (id === "aLang" && el.value === "Instrumental" && $("gender")) setSel($("gender"), "");
      if (id === "aGenre") syncGenreNow();
      recompile();
    };
    el.addEventListener("input", h);
    el.addEventListener("change", h);
  });
  // ---- 折叠重做的两个入口：搜索 + 就地展开（见 CSS .wtools / renderMulti / renderPresetGrid）
  const ws = $("wordSearch");
  if (ws) ws.oninput = () => { wordQuery = ws.value; renderAllMulti(); };
  const we = $("wordExpand");
  // 这个按钮只剩"清掉搜索词"一个动作（四格恒定全展开，折叠总开关已按用户要求删除）。
  if (we) we.onclick = () => {
    if (!wordQuery.trim()) return;
    wordQuery = "";
    if (ws) ws.value = "";
    renderAllMulti();
  };
  const ps = $("presetSearch");
  if (ps) ps.oninput = () => { presetQuery = ps.value; renderPresetGrid(); };

  const sb = $("styleAsm");
  if (sb) sb.addEventListener("input", () => {
    if (suppressStyleWatch) { updateStyleChecks(); return; }
    styleDirty = true;
    // 每次按键都要重算：预算读数就贴在文本框上方，它停在第一次改动的值上就是假读数
    updateStyleChecks(); updateAsmHint();
  });
  const apply = $("asmApply");
  if (apply) apply.onclick = () => {
    writeStyleBox(compileStyle());
    styleDirty = false;
    updateStyleChecks(); updateAsmHint(); saveAsm();
    toast("已按六要素装配到 Style", "ok");
  };
  const reset = $("asmReset");
  if (reset) reset.onclick = () => {
    // 清空不只是清六格：recompile(true) 会连 Style 文本框一起重写，而文本框才是提交给模型的内容
    const box = $("styleAsm");
    if (box && box.value.trim() && !confirm(
      `清空装配台会把左栏的 Style 文本框一起重写（现在是「${box.value.trim().slice(0, 36)}…」）。
`
      + `确定清空？想保留现有文本的话点「取消」，直接改文本框即可（装配台会自动转为只预览不覆盖）。`)) return;
    asm.mood = []; asm.timbre = []; asm.inst = []; asm.feel = []; asm.preset = "";
    setSel($("aLang"), ""); setSel($("gender"), ""); setSel($("aKey"), "");
    if ($("aGenre")) $("aGenre").value = "";
    if ($("aMore")) $("aMore").value = "";
    if ($("bpm")) $("bpm").value = "";
    renderPresetGrid(); renderAllMulti(); syncGenreNow();
    recompile(true);
    toast("装配台已清空", "ok");
  };
  const save = $("asmSave");
  if (save) save.onclick = saveMyPreset;
  const lc = $("lyricCheck");
  if (lc) lc.onclick = checkLyrics;
  const sk = $("lyricSkeleton");
  if (sk) sk.onclick = () => {
    const box = $("lyrics");
    if (!box) return;
    if (box.value.trim() && !confirm("歌词框已有内容：\n· 确定 = 在末尾追加骨架\n· 取消 = 不改动（要整体替换请先清空）")) return;
    pushLyricUndo("插入曲式骨架");
    box.value = (box.value.trim() ? box.value.replace(/\s*$/, "") + "\n\n" : "") + LYRIC_SKELETON;
    box.dispatchEvent(new Event("input", { bubbles: true }));
    checkLyrics();
  };
  const lu = $("lyricUndo");
  if (lu) lu.onclick = popLyricUndo;
  const lfa = $("lyricFromAudio");
  if (lfa) lfa.onclick = () => $("lyricAudio").click();
  const lfai = $("lyricAudio");
  if (lfai) lfai.onchange = () => {
    const f = lfai.files && lfai.files[0];
    lfai.value = "";                 // 同一个文件能连点两次（否则第二次 change 不触发）
    if (!f) return;
    transcribeLyricAudio(f);
  };

  // 首屏判定：有快照却和文本框不一致 = 上次是手改收尾的，进 dirty 模式（装配台不覆盖）；
  // 没有快照 = 第一次接管，不算手改，只提示"点任一格即接管"。
  styleDirty = hadSnapshot && ($("styleAsm") || {}).value.trim() !== compileStyle().trim();
  updateStyleChecks();
  updateAsmHint();
}
// 外部（如刷新后恢复运行中任务）改写 Style 后调用：重新判定一致性
function syncStyleFromOutside() {
  styleDirty = ($("styleAsm") || {}).value.trim() !== compileStyle().trim();
  updateStyleChecks();
  updateAsmHint();
}

async function loadPresets() {
  renderChips($("lyricChips"), fallbackLyrics, "lyrics", "lyrics");
  try { initAssembler(); } catch (e) { console.warn("曲风装配台初始化失败：", e); }
  loadMyPresets();
  loadRecentLyrics();
  // 三条护栏的初始态：恢复表单后歌词框可能已有内容（指路条该收起）、
  // 参数摘要要先算一次（否则折叠标题右边是空的）、撤销按钮先按栈置灰。
  try { updateLyricHelp(); updateParamSummary(); renderLyricUndo(); } catch (e) { console.warn("创作页护栏初始化失败：", e); }
}

// ---------- 最近生成过的歌词（用户要求：候选里能一键取回最近几次用的词）----------
// 数据就在创作历史那份 meta 里（style/lyrics/task_name 都存了），不用新后端接口。
// 过滤条件都是真实踩过的坑：换声/训练任务的 lyrics 只有 20 来字（不是歌词）、
// 同一首歌反复生成会留 4-5 份完全相同的词，所以按内容去重、按时间倒序取 10 份。
let recentLyrics = null;
async function loadRecentLyrics(force) {
  const box = $("recentLyricBox"), el = $("recentLyricChips");
  if (!box || !el) return;
  if (recentLyrics && !force) { renderRecentLyrics(el, box, recentLyrics); return; }
  try {
    const r = await fetch("/api/generate/list");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const items = (await r.json()).items || [];
    const seen = new Set(), picks = [];
    items
      .filter(it => (it.kind || "generate") === "generate" && it.status === "done")
      .sort((a, b) => String(b.ts || "").localeCompare(String(a.ts || "")))
      .forEach(it => {
        const L = String(it.lyrics || "").trim();
        if (L.length < 40) return;                       // 短到不是一首词
        const sig = L.replace(/\s+/g, " ");
        if (seen.has(sig)) return;                        // 同一份词只留最新一次
        seen.add(sig);
        if (picks.length >= 10) return;
        picks.push({ id: it.id, name: (it.task_name || "").trim() || L.split("\n").find(x => x && x[0] !== "[") || "未命名", lyrics: L, ts: it.ts || "" });
      });
    recentLyrics = picks;
    renderRecentLyrics(el, box, picks);
  } catch (e) {
    // 拉不到历史就别留半个标题在这里
    box.hidden = true;
    console.warn("读取最近歌词失败：", e);
  }
}
function renderRecentLyrics(el, box, picks) {
  el.innerHTML = "";
  if (!picks.length) { box.hidden = true; return; }
  box.hidden = false;
  picks.forEach(p => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip mini";
    b.textContent = p.name.length > 14 ? p.name.slice(0, 14) + "…" : p.name;
    const lines = p.lyrics.split("\n").filter(Boolean);
    b.title = "《" + p.name + "》 · " + (p.ts || "").slice(0, 16).replace("T", " ") +
              "\n" + lines.slice(0, 3).join(" / ") + "\n共 " + lines.length + " 行 · 点击填进左边的歌词框";
    b.onclick = () => fillRecentLyrics(p);
    el.appendChild(b);
  });
}
function fillRecentLyrics(p) {
  const box = $("lyrics");
  if (!box) return;
  p = Object.assign({}, p, { lyrics: stripMachineOutro(p.lyrics) });   // 老记录里那句机器补的 [Outro] 不算用户写的
  const cur = box.value.trim();
  if (cur && cur !== p.lyrics.trim() &&
      !confirm("歌词框已有内容（" + cur.length + " 字）。\n确定 = 用《" + p.name + "》的歌词整体替换\n取消 = 保留现在的内容")) return;
  pushLyricUndo("最近歌词《" + p.name + "》");
  box.value = p.lyrics;
  flashField(box);
  try { box.dispatchEvent(new Event("input", { bubbles: true })); } catch {}
  if (typeof checkLyrics === "function") checkLyrics();
  toast("已填入《" + p.name + "》的歌词（" + p.lyrics.length + " 字）", "ok");
}

// ---------- health & models ----------
let engineMode = "cuda";
// 「GPU」在核显机器上是 Vulkan、在 N 卡机器上是 CUDA —— 由后端按引擎构建能力告知
let engineGpu = "cuda";
// 离线时「网关未连通」可点击：通过 yue2workbench:// 协议唤起启动脚本
// （协议需先运行 scripts/register_protocol.bat 注册一次；页面由网关自己服务，
//   网关停了页面自然没有任何 API 可调，只能靠浏览器→系统的这条路。）
function bindOfflineStart(h) {
  if (h.dataset.offlineBound) return;
  h.dataset.offlineBound = "1";
  h.title = "点击启动后端（需先运行 scripts/register_protocol.bat 注册协议）";
  h.onclick = () => {
    try { location.href = "yue2workbench://start"; } catch {}
  };
}
function unbindOfflineStart(h) {
  if (h.dataset.offlineBound) { delete h.dataset.offlineBound; h.title = ""; h.onclick = null; }
}
async function refreshHealth() {
  try {
    const r = await fetch("/api/health");
    const j = await r.json();
    const h = $("health");
    // /api/health 返回 {gateway:"ok", audiocpp:{status,backend}, models:{...}}
    const be = j.audiocpp || {};
    const st = be.status || "";
    const mode = (be.backend || "").toUpperCase();
    const model = j.models?.yue2 || "yue2";
    // 只有引擎自己回 ok 才算"就绪"；busy = 端口在、生成中被打满，不是坏了；
    // down 才是真没起来。以前用 gateway==="ok" 兜底会让引擎全死时也亮绿灯（评审 B1）。
    if (r.ok && st === "ok") {
      h.className = "foot-row ok";
      $("healthText").textContent = "引擎就绪 · " + model + (mode ? " · " + mode : "");
      unbindOfflineStart(h);
    } else if (r.ok && st === "busy") {
      h.className = "foot-row ok";
      $("healthText").textContent = "引擎忙碌（生成中）· " + model + (mode ? " · " + mode : "");
      unbindOfflineStart(h);
    } else {
      h.className = "foot-row err";
      $("healthText").textContent = "引擎未就绪";
    }
  } catch {
    const h = $("health");
    h.className = "foot-row err startable";
    $("healthText").textContent = "网关未连通 · 点击启动";
    bindOfflineStart(h);
  }
  refreshEngineMode();
}

// ---------- 模型完整性校验 + 局部修复（借鉴 YuE2-Studio：目录存在 ≠ 可用） ----------
async function refreshModelVerify() {
  const row = $("modelVerifyRow"), txt = $("modelVerifyText"), btn = $("modelRepairBtn");
  try {
    const j = await (await fetch("/api/models/verify")).json();
    row.style.display = "";
    if (typeof loadModelFiles === "function") loadModelFiles(j);
    if (j.repair_running) {
      row.className = "foot-row loading";
      txt.textContent = "模型修复中…";
      btn.style.display = "none";
      if (typeof watchRepair === "function") watchRepair();   // 修复要有进度，不能只剩一个转圈的词
      setTimeout(refreshModelVerify, 5000);
      return;
    }
    if (j.ok) {
      row.className = "foot-row ok";
      txt.textContent = "模型完整";
      btn.style.display = "none";
    } else {
      row.className = "foot-row err";
      const bad = (j.files || []).filter(f => !f.ok);
      txt.textContent = "模型缺件 " + bad.length + " 项：" + bad.map(f => f.name).join("、").slice(0, 40);
      btn.style.display = "";
    }
  } catch {
    row.style.display = "none";
  }
}
$("modelRepairBtn").onclick = async () => {
  if (!confirm("开始修复模型？只重新下载缺失/损坏的文件（断点续传，多镜像回退），已完整的不会重下。")) return;
  $("modelRepairBtn").disabled = true;
  try {
    const r = await jfetch("/api/models/repair", { method: "POST" });
    statusSay(r.message || "修复已开始", "ok");
    if (typeof watchRepair === "function") watchRepair();
    toast(r.message || "修复已开始", "ok");
    refreshModelVerify();
  } catch (e) { toast("修复失败：" + friendlyErr(e), "err"); }
  finally { $("modelRepairBtn").disabled = false; }
};
async function refreshEngineMode() {
  try {
    const j = await (await fetch("/api/backend/mode")).json();
    engineMode = j.mode || "cuda";
    const avail = Array.isArray(j.available) ? j.available : [];
    engineGpu = avail.includes("cuda") ? "cuda" : (avail.includes("vulkan") ? "vulkan" : "cuda");
    $("modeRow").style.display = "";
    let modeText = engineMode === "cpu"
      ? "CPU 模式 · 慢 5-10 倍"
      : engineMode === "vulkan"
        ? "GPU 模式 · Vulkan（集显，共享内存）"
        : (j.vram_ok === false
          ? "GPU 模式 · 显存不足(" + Math.round((j.vram_free_mb||0)/1024) + "GB空闲)"
          : "GPU 模式 · 显存 " + (j.vram_free_mb ? (j.vram_free_mb/1024).toFixed(1) + "GB 空闲" : "状态未知"));
    if (j.cpu_fallback_allowed === false) modeText += " · 分配失败不回退 CPU";
    $("modeText").textContent = modeText;
    $("modeBtn").textContent = engineMode === "cpu" ? "切GPU" : "切CPU";
    $("modeBtn").title = engineMode === "cpu"
      ? (engineGpu === "vulkan" ? "切回 GPU 模式（本机引擎走 Vulkan 集显）" : "切回 GPU（CUDA）模式，速度快")
      : "切到 CPU 兜底（慢但能出结果）";
  } catch { $("modeRow").style.display = "none"; }
}
$("modeBtn").onclick = async () => {
  const target = engineMode === "cpu" ? engineGpu : "cpu";
  if (target === "cpu" && !confirm("切到 CPU 模式？生成会慢 5-10 倍，但显存不足也能出结果。")) return;
  $("modeBtn").disabled = true;
  try {
    const r = await jfetch("/api/backend/mode", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ mode: target }) });
    const j = await r.json();
    toast(j.message || "已切换", "ok");
    statusSay(j.message || "引擎模式已切换", "ok");
    refreshEngineMode();
    setTimeout(refreshHealth, 4000);
  } catch (e) { toast("切换失败：" + friendlyErr(e), "err"); }
  finally { $("modeBtn").disabled = false; }
};

async function refreshCpuFallbackSetting() {
  try {
    const j = await (await fetch("/api/settings")).json();
    const on = !!j.cpu_fallback_allowed;
    $("cpuFallbackToggle").checked = on;
    $("cpuFallbackHint").textContent = on
      ? "开启：GPU 分配失败会自动切到 CPU，整首从头重算，核显上可能慢十几倍。"
      : "关闭：GPU 分配失败直接报错并保留 AR 检查点，不会把整首歌重算到 CPU。";
    $("cpuFallbackStatus").textContent = j.cpu_fallback_source === "setting"
      ? "（本页设置优先于 secrets/local_env.bat）" : "";
  } catch (e) {
    $("cpuFallbackStatus").textContent = "读取失败：" + friendlyErr(e);
  }
}
$("cpuFallbackToggle").onchange = async () => {
  const el = $("cpuFallbackToggle");
  const on = el.checked;
  el.disabled = true;
  try {
    const r = await jfetch("/api/settings", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ cpu_fallback_allowed: on }),
    });
    const j = await r.json();
    toast(j.message || "设置已保存", "ok");
    $("cpuFallbackStatus").textContent = on ? "已允许自动降级 CPU" : "已禁止自动降级 CPU";
    refreshEngineMode();
  } catch (e) {
    el.checked = !on;
    toast("设置失败：" + friendlyErr(e), "err");
  } finally { el.disabled = false; }
};

// VAE 窗口大小切换
async function refreshVaeWindowSetting() {
  try {
    const j = await (await fetch("/api/vae/window")).json();
    const is512 = j.frames === 512;
    // 报的是**当前加载模型**那份的值；标出模型名，避免多档不一致时又成"显示 A 实际 B"
    const who = j.model ? ` · ${j.model}` : "";
    const warn = j.active ? "" : " · 未定位到在用配置，切换将改全部";
    $("vaeWindowToggle").checked = is512;
    $("vaeWindowLabel").textContent = (is512
      ? "当前：512 帧窗口（推荐，显存占用约 1.95GB）"
      : `当前：${j.frames || 1024} 帧窗口（官方默认，显存占用约 3.89GB）`) + who + warn;
    $("vaeWindowLabel").title = (j.configs || [])
      .map((c) => `${c.path} → ${c.frames}${c.active ? "（在用）" : ""}`).join("\n");
  } catch (e) {
    $("vaeWindowLabel").textContent = "读取失败：" + friendlyErr(e);
    $("vaeWindowLabel").title = "";
  }
}
$("vaeWindowToggle").onchange = async () => {
  const el = $("vaeWindowToggle");
  const targetFrames = el.checked ? 512 : 1024;
  if (!confirm(`切换到 ${targetFrames} 帧窗口？这会立即重启音频引擎，正在生成的任务会中断。`)) {
    el.checked = !el.checked;
    return;
  }
  el.disabled = true;
  try {
    const r = await jfetch("/api/vae/window", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ frames: targetFrames }),
    });
    const j = await r.json();
    toast(j.message || "VAE 窗口已切换，引擎正在重启…", "ok");
    // 重新拉一次真实状态，别拿本地目标值直接写进界面（那正是"显示 A 实际 B"的来源）
    await refreshVaeWindowSetting();
    setTimeout(refreshHealth, 5000);
  } catch (e) {
    el.checked = !el.checked;
    toast("切换失败：" + friendlyErr(e), "err");
  } finally { el.disabled = false; }
};

async function loadCapabilities() {
  const box = $("capabilityList");
  if (!box) return;
  box.textContent = "检测中…";
  try {
    const j = await (await fetch("/api/capabilities")).json();
    box.innerHTML = (j.features || []).map((f) => {
      const mark = f.ok ? '<span style="color:var(--ok);">✓</span>' : '<span style="color:var(--err);">✕</span>';
      return `<div style="margin:5px 0;">${mark} ${escapeHtml(f.label)}` +
        (f.note ? ` <span style="color:var(--muted);">· ${escapeHtml(f.note)}</span>` : "") + `</div>`;
    }).join("");
  } catch (e) {
    box.textContent = "检测失败：" + friendlyErr(e);
  }
}
$("capabilityRefresh").onclick = loadCapabilities;

async function refreshDeepseekKey() {
  try {
    const j = await (await fetch("/api/settings")).json();
    const set = !!j.deepseek_api_key_set;
    // 来源必须说清：env 优先于本页保存的文件，此时"清除"清不掉（issue #4-2）
    const src = j.deepseek_key_source || (set ? "file" : null);
    const el = $("deepseekKeyState");
    if (src === "env") {
      el.textContent = "已配置，来自环境变量 DEEPSEEK_API_KEY（优先级高于本页保存的）。"
        + "本页清除不了它——要换 Key 请改 local_env.bat 后重启工作台。";
      el.style.color = "var(--warn)";
    } else if (src === "file") {
      el.textContent = "已配置（保存在 secrets/deepseek_api_key.txt）。输入新 Key 可覆盖；留空不会改动。";
      el.style.color = "var(--ok)";
    } else {
      el.textContent = "未配置；填入 DeepSeek Key 后 AI 写词/AI 工作台才可用。";
      el.style.color = "var(--muted)";
    }
  } catch (e) { $("deepseekKeyStatus").textContent = "读取失败：" + friendlyErr(e); }
}
$("deepseekKeySave").onclick = async () => {
  const el = $("deepseekKey");
  const key = (el.value || "").trim();
  if (!key) { toast("请先填入 Key", "err"); return; }
  $("deepseekKeySave").disabled = true;
  try {
    const r = await jfetch("/api/settings", { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ deepseek_api_key: key }) });
    const j = await r.json();
    el.value = "";
    $("deepseekKeyStatus").textContent = j.message || "已保存";
    toast(j.message || "DeepSeek Key 已保存", "ok");
    refreshDeepseekKey();
    loadCapabilities();
  } catch (e) { toast("保存失败：" + friendlyErr(e), "err"); }
  finally { $("deepseekKeySave").disabled = false; }
};
$("deepseekKeyClear").onclick = async () => {
  if (!confirm("清除已保存的 DeepSeek Key？\n（若 Key 来自环境变量 DEEPSEEK_API_KEY，这一步清不掉它）")) return;
  try {
    const r = await jfetch("/api/settings", { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ deepseek_api_key_clear: true }) });
    const j = await r.json();
    $("deepseekKeyStatus").textContent = j.message || "已清除";
    toast(j.message || "DeepSeek Key 已清除", "ok");
    refreshDeepseekKey();
    loadCapabilities();
  } catch (e) { toast("清除失败：" + friendlyErr(e), "err"); }
};

// ================= ⑭ 歌词时间轴（独立任务） =================
let lrcJobId = null, lrcPollTimer = null, lrcLast = null;
function lrcSay(msg, kind) {
  const el = $("lrcStatus");
  el.className = "status" + (kind === "ok" ? " ok" : kind === "err" ? " err" : "");
  el.textContent = msg;
  // 新增“服务器歌曲”下拉后内容更高：提交/完成时把状态行滚进视野，
  // 别让人点了按钮却看不到结果（其余页面的状态更新也都这么收口）。
  if (typeof reveal === "function") reveal(el, "nearest");
}
function lrcShow(st) {
  lrcLast = st;
  lrcJobId = st.id || lrcJobId;
  $("lrcOutputs").style.display = st.status === "done" ? "" : "none";
  $("lrcElrcDownload").style.display = st.has_elrc ? "" : "none";
  $("lrcMethod").textContent = st.status === "done"
    ? "对齐方式：" + (st.method || "align") + (st.diagnostics && st.diagnostics.method ? " · " + st.diagnostics.method : "")
    : "";
  const pv = $("lrcPreview");
  if (st.status === "done" && st.lrc) {
    pv.style.display = ""; pv.textContent = st.lrc.slice(0, 12000);
  } else { pv.style.display = "none"; pv.textContent = ""; }
  if (st.status === "done") lrcSay("完成 · " + (st.lrc_lines || 0) + " 行" + (st.has_elrc ? " · 含逐字 eLRC" : ""), "ok");
  else if (st.status === "error") lrcSay("失败：" + (st.error || "未知错误"), "err");
  else lrcSay((st.step || "对齐中") + "… 后台任务，刷新页面不会丢", "");
}
async function lrcStatus(id) {
  const r = await fetch("/api/lrc/status/" + encodeURIComponent(id));
  if (!r.ok) throw new Error("任务不存在或已被清理");
  const st = await r.json();
  lrcShow(st);
  if (st.status === "running") {
    clearTimeout(lrcPollTimer);
    lrcPollTimer = setTimeout(() => lrcStatus(id).catch((e) => lrcSay("查询失败：" + friendlyErr(e), "err")), 2000);
  }
}
async function loadLrcList() {
  const box = $("lrcList");
  try {
    const j = await (await fetch("/api/lrc/list")).json();
    const items = j.items || [];
    if (!items.length) { box.textContent = "还没有独立歌词时间轴任务。"; return; }
    box.innerHTML = "";
    items.forEach((it) => {
      const b = document.createElement("button");
      b.className = "ghost btn-sm";
      b.style.margin = "0 6px 6px 0";
      b.textContent = (it.name || it.id) + " · " + (it.status === "done" ? "完成" : it.status === "error" ? "失败" : "进行中");
      b.onclick = () => lrcStatus(it.id).catch((e) => lrcSay("读取失败：" + friendlyErr(e), "err"));
      box.appendChild(b);
    });
  } catch (e) { box.textContent = "任务列表读取失败：" + friendlyErr(e); }
}
async function loadServerAudioOptions() {
  const out = [];
  const seen = new Set();
  try {
    const g = await (await fetch("/api/generate/list?limit=100")).json();
    for (const it of (g.items || [])) {
      if (!it || !it.id || seen.has(it.id)) continue;
      if (it.status && it.status !== "done") continue;
      const kind = it.kind || "generate";
      if (kind !== "generate" && kind !== "upload" && kind !== "history") continue;
      seen.add(it.id);
      const name = it.task_name || it.name || it.id;
      out.push({ id: it.id, label: name + " · 生成" });
    }
  } catch { }
  try {
    const h = await (await fetch("/api/history")).json();
    for (const it of (h.items || [])) {
      if (!it || !it.id || seen.has(it.id)) continue;
      seen.add(it.id);
      out.push({ id: it.id, label: it.id + " · 入库" });
    }
  } catch { }
  return out;
}
async function fillServerPick(selId, placeholder) {
  const sel = $(selId);
  if (!sel) return;
  const cur = sel.value;
  const opts = await loadServerAudioOptions();
  sel.innerHTML = '<option value="">' + escapeHtml(placeholder) + '</option>' +
    opts.map((o) => '<option value="' + escapeHtml(o.id) + '">' +
      escapeHtml(o.label) + '</option>').join("");
  if (cur && Array.from(sel.options).some((o) => o.value === cur)) sel.value = cur;
}
function loadLrcPicks() { fillServerPick("lrcPick", "— 不选，使用下面上传的音频 —"); }
function loadScorePicks() {
  fillServerPick("abcPick", "— 不选，使用下面的文件 —").then(() => {
    if (($("abcPick") || {}).value && $("abcPick").onchange) $("abcPick").onchange();
  });
}
$("lrcGo").onclick = async () => {
  const rid = ($("lrcPick") && $("lrcPick").value || "").trim();
  const f = $("lrcAudio").files && $("lrcAudio").files[0];
  const lyrics = ($("lrcLyrics").value || "").trim();
  if (!rid && !f) { toast("先选服务器歌曲，或上传音频", "err"); return; }
  if (!lyrics) { toast("先粘贴歌词", "err"); return; }
  const btn = $("lrcGo"); btn.disabled = true;
  lrcSay(rid ? "读取服务器歌曲并排队…" : "上传并排队…", "");
  try {
    const fd = new FormData();
    if (rid) fd.append("rid", rid); else fd.append("file", f);
    fd.append("lyrics", lyrics);
    fd.append("task_name", ($("lrcName").value || "").trim());
    const r = await fetch("/api/lrc/start", { method: "POST", body: fd });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || "提交失败");
    lrcJobId = j.id;
    lrcSay("已提交，开始对齐…", "");
    await lrcStatus(j.id);
    loadLrcList();
  } catch (e) { lrcSay("提交失败：" + friendlyErr(e), "err"); }
  finally { btn.disabled = false; }
};
async function lrcDownload(fmt) {
  if (!lrcJobId) return;
  try {
    const r = await fetch("/api/lrc/download/" + encodeURIComponent(lrcJobId) + "?fmt=" + fmt);
    if (!r.ok) throw new Error("文件不存在");
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = dlName((lrcLast && lrcLast.name) || "歌词", fmt);
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    toast("已开始下载 " + (fmt === "elrc" ? "逐字 eLRC" : "LRC"), "ok");
  } catch (e) { toast("下载失败：" + friendlyErr(e), "err"); }
}
$("lrcDownload").onclick = () => lrcDownload("lrc");
$("lrcElrcDownload").onclick = () => lrcDownload("elrc");
$("lrcRefresh").onclick = () => { loadLrcList(); loadLrcPicks(); };
async function loadModels() {
  try {
    const r = await fetch("/api/models/list");
    const j = await r.json();
    currentModels = Array.isArray(j.models) ? j.models : [];
    const sel = $("modelSel");
    sel.innerHTML = "";
    if (!currentModels.length) {
      const o = document.createElement("option"); o.value = ""; o.textContent = "未检测到模型"; sel.appendChild(o); return;
    }
    currentModels.forEach(m => {
      const o = document.createElement("option");
      o.value = m.path; o.textContent = m.quant + " · " + m.size_gb + " GB";
      sel.appendChild(o);
    });
    // 6GB 显存默认选 Q4 系（Q8/Q4 峰值差异见 modelHint）
    const q4 = currentModels.find(m => /q4/i.test(m.path)) || currentModels[0];
    if (q4) sel.value = q4.path;
    try {
      const cur = await (await fetch("/api/models/current")).json();
      if (cur.path && currentModels.some(m => m.path === cur.path)) sel.value = cur.path;
    } catch {}
    updateModelHint();
    sel.disabled = false;
    updateParamSummary();   // 量化是摘要的第一项，模型列表异步到货后得补一次
  } catch {}
}
function updateModelHint() {
  const m = currentModels.find(x => x.path === $("modelSel").value);
  const el = $("modelHint");
  if (!m) { el.textContent = ""; return; }
  el.textContent = /q4/i.test(m.path)
    ? "6GB 显存推荐；长歌词峰值会升高"
    : "高精度；6GB 卡显存吃紧，可能加载失败";
}
$("modelSel").onchange = async () => {
  const path = $("modelSel").value;
  if (!path) return;
  const old = $("modelSel").dataset.last;
  if (old === path) return;
  $("modelSel").dataset.last = path;
  const m = currentModels.find(x => x.path === path);
  if (!m) return;
  $("modelSel").disabled = true;
  statusSay("正在切换到 " + m.quant + " 并重启音频引擎…（模型较大时需 1-2 分钟）", "");
  try {
    const r = await jfetch("/api/models/switch", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ path, vae: m.vae }) });
    const j = await r.json();
    toast("已切换到 " + m.quant, "ok");
    statusSay(j.message || "模型已切换", "ok");
    updateModelHint();
    setTimeout(refreshHealth, 4000);
  } catch (err) {
    statusSay("模型切换失败：" + friendlyErr(err), "err");
    $("modelSel").dataset.last = "";
  } finally { $("modelSel").disabled = false; }
};

// ---------- ⑫ LoRA 适配器 ----------
// 这是**引擎级**配置（写在 server.json 的 session_options 里），改一次对之后每一次生成都生效，
// 所以它待在 ⑫，不进 ②⑤ 的表单、也不参与任何跨页回填。
// 契约来自 audio.cpp 自带的 docs/models/yue2.md：unfused 的 A/B 文件，_comfyui 那种融合版引擎会拒；
// 换适配器要开新 session（我们直接重启引擎，最省事也最不容易留半新半旧的状态）。
// 状态写在本节按钮下面那条 .lint 上：⑫ 的页面级 #settingsStatus 在右栏（外观那一列），
// 点「挂载并重启引擎」的人眼睛不在那儿——别留一个谁也不写的空 div。
function loraSay(msg, cls) {
  const el = $("loraStatus");
  if (!el) return;
  el.className = "lint" + (cls ? " " + cls : "");
  el.textContent = msg;
}
async function loadLoras(silent, fromServer) {
  const arSel = $("loraAr"), narSel = $("loraNar");
  if (!arSel || !narSel) return;
  // keep* 是给「重新扫描」留的：用户正在挑的东西不该被一次刷新抹掉。
  // 但 fromServer（刚挂载/刚卸下）必须反过来以服务端为准——卸下之后下拉框还留着
  // 那两个适配器名，就等于界面在说"还挂着"，而它已经不挂了。
  const keepAr = arSel.value, keepNar = narSel.value;
  try {
    const j = await (await jfetch("/api/models/loras")).json();
    const items = j.items || [];
    const fill = (sel, want, role) => {
      sel.innerHTML = "";
      const o0 = document.createElement("option");
      o0.value = "";
      o0.textContent = items.length ? "— 不挂（裸模型）—" : "— cpp/lora 里没有 .safetensors —";
      sel.appendChild(o0);
      items.forEach(it => {
        const o = document.createElement("option");
        o.value = it.file;
        o.textContent = it.file.replace(/\.safetensors$/i, "") + " · " + it.size_mb + "MB"
          + (it.role && it.role !== role ? "（文件名看着像 " + it.role.toUpperCase() + " 段）" : "");
        sel.appendChild(o);
      });
      if (want && [...sel.options].some(o => o.value === want)) sel.value = want;
    };
    fill(arSel, fromServer ? j.ar : (keepAr || j.ar), "ar");
    fill(narSel, fromServer ? j.nar : (keepNar || j.nar), "nar");
    if (j.ar_scale !== undefined) { $("loraArScale").value = String(j.ar_scale); $("loraArScaleVal").textContent = String(j.ar_scale); }
    if (j.nar_scale !== undefined) { $("loraNarScale").value = String(j.nar_scale); $("loraNarScaleVal").textContent = String(j.nar_scale); }
    if (!silent) {
      loraSay((j.ar || j.nar)
        ? "当前挂着：" + [j.ar ? "AR " + j.ar : "", j.nar ? "NAR " + j.nar : ""].filter(Boolean).join(" ＋ ")
        : "LoRA：未挂载（裸模型）。可用适配器 " + items.length + " 个。", "");
    }
  } catch (err) {
    loraSay("读取 LoRA 清单失败：" + friendlyErr(err), "err");
  }
}
async function pushLora(body) {
  const btns = [$("loraApply"), $("loraClear")];
  btns.forEach(b => { if (b) b.disabled = true; });
  loraSay("正在改适配器并重启音频引擎…（模型较大时需 1-2 分钟）", "");
  try {
    const r = await jfetch("/api/models/lora", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || ("HTTP " + r.status));
    loraSay(j.message || "适配器已更新", "ok");
    toast(j.message || "适配器已更新", "ok");
    setTimeout(() => loadLoras(true, true), 1000);
    setTimeout(refreshHealth, 5000);
  } catch (err) {
    loraSay("LoRA 操作失败：" + friendlyErr(err) + "（引擎状态未确认，必要时点「重新扫描」核对）", "err");
  } finally { btns.forEach(b => { if (b) b.disabled = false; }); }
}
if ($("loraApply")) {
  $("loraApply").onclick = () => pushLora({
    ar: $("loraAr").value, nar: $("loraNar").value,
    ar_scale: Number($("loraArScale").value), nar_scale: Number($("loraNarScale").value) });
  $("loraClear").onclick = () => pushLora({ ar: "", nar: "" });
  $("loraScan").onclick = () => loadLoras();
  $("loraArScale").oninput = e => { $("loraArScaleVal").textContent = e.target.value; };
  $("loraNarScale").oninput = e => { $("loraNarScaleVal").textContent = e.target.value; };
}

// ---------- audio player ----------
function loadAudio(blob, name, taskName) {
  lastBlob = blob;
  lastTaskName = taskName || "";
  if (lastUrl) URL.revokeObjectURL(lastUrl);
  lastUrl = URL.createObjectURL(blob);
  const p = $("player"); p.pause(); p.src = lastUrl; p.load();
  if (name) $("audioName").textContent = name;
  $("download").disabled = false;
  $("dockPlay").disabled = false;
  p.play().catch(() => {});
}

// ---------- 自绘播放控件（播放坞）：与隐藏 #player 双向同步 ----------
(function () {
  const p = $("player"), btn = $("dockPlay"), track = $("dockTrack"),
        fill = $("dockFill"), knob = $("dockKnob"), cur = $("dockCur"), dur = $("dockDur");
  const fmt = (t) => {
    if (!Number.isFinite(t)) return "0:00";
    const m = Math.floor(t / 60), s = Math.floor(t % 60);
    return m + ":" + String(s).padStart(2, "0");
  };
  function setIcon(playing) {
    btn.querySelector(".i-play").style.display = playing ? "none" : "";
    btn.querySelector(".i-pause").style.display = playing ? "" : "none";
  }
  function setBar(ratio) {
    const r = Math.max(0, Math.min(1, ratio || 0));
    fill.style.width = (r * 100).toFixed(2) + "%";
    knob.style.left = (r * 100).toFixed(2) + "%";
  }
  btn.onclick = () => {
    if (!p.src) return;
    if (p.paused) p.play().catch(() => {}); else p.pause();
  };
  // ★ 原生控件已被 .dock audio{display:none} 撤掉，浏览器那条"解码失败"的默认红字也一起没了。
  //   所以媒体错误必须由我们自己送到用户面前，否则"点了播放键没反应"会变成无声故障。
  //   注意 error 事件的 target 可能是 <audio> 也可能是它内部的 <source>，两处都听。
  p.addEventListener("error", () => {
    const map = { 1: "播放被中止", 2: "网络错误，音频没下下来", 3: "音频解码失败（文件可能损坏）", 4: "格式不支持或音频不可用" };
    toast(map[p.error && p.error.code] || "音频无法播放", "err");
    btn.disabled = true;
  });
  p.addEventListener("stalled", () => { $("audioName").textContent = "音频加载停滞，请重试"; });
  p.addEventListener("play", () => setIcon(true));
  p.addEventListener("pause", () => setIcon(false));
  p.addEventListener("timeupdate", () => {
    cur.textContent = fmt(p.currentTime);
    if (p.duration) setBar(p.currentTime / p.duration);
  });
  p.addEventListener("durationchange", () => { dur.textContent = fmt(p.duration); });
  p.addEventListener("ended", () => setIcon(false));
  // 点击 / 拖动轨道跳转
  let dragging = false;
  function seek(ev) {
    const rect = track.getBoundingClientRect();
    const x = (ev.touches ? ev.touches[0].clientX : ev.clientX) - rect.left;
    const ratio = Math.max(0, Math.min(1, x / rect.width));
    setBar(ratio);
    if (p.duration) p.currentTime = ratio * p.duration;
  }
  track.addEventListener("pointerdown", (e) => {
    if (!p.src) return;
    dragging = true;
    track.setPointerCapture(e.pointerId);
    seek(e);
  });
  track.addEventListener("pointermove", (e) => { if (dragging) seek(e); });
  track.addEventListener("pointerup", () => { dragging = false; });
  track.addEventListener("pointercancel", () => { dragging = false; });
})();
$("audioFile").onchange = () => {
  const f = $("audioFile").files && $("audioFile").files[0];
  if (f) loadAudio(f, f.name);
};
$("download").onclick = () => {
  if (!lastUrl) return;
  const a = document.createElement("a");
  a.href = lastUrl;
  a.download = dlName(lastTaskName || "yue2");
  a.click();
};

// ---------- generation（服务端托管 + 轮询，刷新页面不丢任务）----------
// 运行中那首任务**实际**用的种子（服务端把 -1/随机 具体化出来的那个数）。
// 只用于展示，绝不写回 #seed —— 见 resumeRunningJob 里那条注释。
let runningSeedActual = null;
function setGenerating(on) {
  if (!on) runningSeedActual = null;
  $("go").disabled = on;
  $("go").innerHTML = on ? '<span class="spinner"></span> 生成中…' : "生成歌曲";
  $("stopGen").style.display = on ? "" : "none";
  Favicon.set(on ? "busy" : "idle");  // 标签栏图标随任务状态变化
}

// ---------- 用户主动终止当前生成/连发任务（网关不停，引擎下次生成自动重启） ----------
$("stopGen").onclick = async () => {
  if (!confirm("确定终止当前任务？\n正在生成的这一条会被丢弃（不保存）。\n批量队列里其它任务不受影响，会接着跑下一条；要把整条队列停下，去 ⑪ 任务管理按「⏹ 停止队列」。")) return;
  $("stopGen").disabled = true;
  try {
    const r = await fetch("/api/generate/stop?scope=current", { method: "POST" });
    const j = await r.json();
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    setGenerating(false);
    $("progress").classList.remove("show");
    $("status").className = "status err";
    $("status").textContent = j.message || "任务已终止。";
    toast(j.message || "任务已终止", "ok");
    renderHistory();
  } catch (err) {
    toast("终止失败：" + friendlyErr(err), "err");
  } finally {
    $("stopGen").disabled = false;
  }
};

// 进度估算：基于本机 44 次历史任务实测拟合（1660S + Q4，GPU 模式，2026-09-14/19 批次）：
//   耗时与"歌曲时长"呈二次关系（sec vs L² 相关 0.92），歌词字数是可用的代理变量；
//   旧线性公式 t=(110+1.5u) 会把长歌低估 3~5 倍（287 字实测中位 41 分钟，旧公式只估 24 分钟）。
// 模型：t = (1.2×u + 0.0008×u²) × f_cot × f_steps   （u = 中文字×2.4 + 其他字符）
//   f_cot: full 1.0 / melody 0.85 / off 0.75（实测 cot 对总耗时影响远小于预期，off/32 反而比 full 慢）
//   f_steps = 0.6 + 0.4×steps/32（实测 16 步并不比 32 步快一倍，故取平缓系数）
// 关键结论：同一歌词多次生成波动 ±40%（模型随机性），单点 ETA 天生不准 → 区间显示。
// 全 34 条 full/32 校验：实测/估计 中位 1.15（略偏保守），P10 0.84 / P90 2.13。
function estimateTotalSec(job) {
  const s = (job.lyrics || "").replace(/\s/g, "");
  let units = 0;
  for (const ch of s) units += /[\u3400-\u4dbf\u4e00-\u9fff]/.test(ch) ? 2.4 : 1;
  const fCot = job.cot === "melody" ? 0.85 : job.cot === "off" ? 0.75 : 1.0;
  const steps = (job.params && job.params.num_inference_steps) || 32;
  const t = (1.2 * units + 0.0008 * units * units) * fCot * (0.6 + 0.4 * steps / 32);
  return Math.min(21600, Math.max(60, t));
}

let pollTimer = null;

function renderJobProgress(job) {
  const chain = { index: job.chain_index || 1, total: job.chain_total || 1 };
  const t0 = new Date(job.ts).getTime() || Date.now();
  const elapsed = (Date.now() - t0) / 1000;
  const total = estimateTotalSec(job);
  const ratio = Math.min(1, elapsed / total);
  // 前段稍快、尾段放缓的缓动曲线，视觉上更接近真实节奏
  let pct = Math.min(97, 4 + 93 * Math.pow(ratio, 0.9));
  const prog = job.progress;
  if (prog && typeof prog.pct === "number") pct = Math.max(2, Math.min(99, prog.pct));
  $("progressBar").style.width = pct.toFixed(1) + "%";
  // 实测波动 P10~P90 = 0.84~2.13×估计值 → 显示 0.9×~1.8× 区间；超过 2× 才提示异常
  const etaLo = Math.max(0, total * 0.9 - elapsed);
  const etaHi = Math.max(0, total * 1.8 - elapsed);
  const overtime = elapsed > total * 2;
  const pre = chain && chain.total > 1 ? `第 ${chain.index || job.chain_index || 1}/${chain.total} 首 · ` : "";
  const fmt = (t) => t > 90 ? Math.round(t / 60) + " 分钟" : Math.round(t) + " 秒";
  const etaStr = etaHi <= 0 ? "" : etaLo > 0 ? fmt(etaLo) + "~" + fmt(etaHi) : "不超过 " + fmt(etaHi);
  $("status").innerHTML = pre + (overtime
    ? "仍在生成（耗时超过常见区间，受显存挤占影响属正常）… "
    : (job.cpu_fallback ? "CPU 模式生成中（较慢，请耐心等待）… " : "服务端生成中（刷新页面不会丢失任务）… "))
    + (prog && prog.label ? " · " + escapeHtml(prog.label) : "")
    + "<span class='timer'>" + Math.floor(elapsed/60) + "分" + Math.round(elapsed%60) + "秒</span>"
    + (etaStr ? " · 预计还需 " + etaStr : "")
    + " · " + idTag(job.id);
}

// ---------- 完成通知：浏览器通知(requireInteraction) + 页内常驻横幅(带X) ----------
function stickyNotify(title, body, isOk) {
  // 页内常驻横幅：点 X 才消失
  const banner = document.createElement("div");
  banner.className = "sticky-notify " + (isOk ? "ok" : "err");
  banner.innerHTML = `
    <div class="sn-body"><div class="sn-title">${escapeHtml(title)}</div><div class="sn-text">${escapeHtml(body)}</div></div>
    <button class="sn-x" title="关闭">✕</button>`;
  banner.querySelector(".sn-x").onclick = () => banner.remove();
  $("stickyWrap").appendChild(banner);
  // 浏览器原生通知（后台标签页也可见），常驻直到用户点掉
  try {
    if ("Notification" in window) {
      if (Notification.permission === "granted") {
        const n = new Notification(title, { body, requireInteraction: true, tag: "yue2-gen" });
        n.onclick = () => { window.focus(); n.close(); };
      } else if (Notification.permission === "default") {
        Notification.requestPermission();
      }
    }
  } catch {}
}

function applyFinishedJob(job) {
  const name = job.task_name || job.batch_name || job.name || job.id || (job.style || "").split(",")[0] || "未命名任务";
  if (job.status === "done") {
    $("progressBar").style.width = "100%";
    $("status").className = "status ok";
    $("status").innerHTML = "生成完成 · " + fmtBytes(job.bytes) + " · seed="
      + escapeHtml(String(job.seed_actual ?? "")) + " · <span class='timer'>" + (job.sec || "?") + "s</span>"
      + " · " + idTag(job.id);
    fetch("/api/generate/audio/" + job.id).then(r => r.ok ? r.blob() : null).then(b => {
      if (b) loadAudio(b, job.id + ".wav", job.task_name || (job.style || "").split(",")[0]);
    });
    toast("歌曲生成完成，已保存到 output/", "ok");
    stickyNotify(name + " 成功", "已保存到 output/，可在历史页回放。" + (job.batch ? "批量队列将继续下一首。" : ""), true);
    renderHistory();
    if (typeof renderGenResult === "function") renderGenResult(job);
  } else {
    $("status").className = "status err";
    let msg = "生成失败：" + (job.error || "未知错误");
    if (/memory|alloc|oom/i.test(msg)) msg = "显存不足（6GB 卡建议 Q4_K_M + 缩短歌词/减小 Steps）\n" + msg;
    $("status").textContent = msg;
    // 显存/分配类失败：AR 可能已完成，给一个"从检查点继续"（不会重跑 AR）
    if (job.id && /memory|alloc|oom/i.test(String(job.error || ""))) {
      const btn = document.createElement("button");
      btn.className = "ghost btn-xs";
      btn.style.marginLeft = "8px";
      btn.textContent = "↻ 从检查点继续";
      btn.title = "用原参数重新提交；AR 已跑完时直接跳过 AR 渲染（波形会重渲染，语义与时长不变）";
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          await jfetch("/api/generate/resume/" + encodeURIComponent(job.id), { method: "POST" });
          toast("已从检查点继续（不重跑 AR）", "ok");
          setGenerating(true);
          pollJob();
        } catch (e) {
          toast("继续失败：" + friendlyErr(e), "err");
        } finally {
          btn.disabled = false;
        }
      };
      $("status").appendChild(btn);
    }
    toast("生成失败", "err");
    Favicon.set("error");
    setTimeout(() => Favicon.set("idle"), 8000);  // 红叉停留 8 秒后回到空闲图标
    stickyNotify(name + " 失败", (job.batch ? "批量队列将自动继续下一首。" : "") + "点击本条回 ② 歌词+style生成歌曲查看。", false);
  }
  setGenerating(false);
  setTimeout(() => $("progress").classList.remove("show"), 1500);
}

let netMiss = 0;   // 生成状态轮询的连续失败次数：达阈值即如实报断连，不再谎报「生成中」
function pollJob() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    try {
      const j = await (await fetch("/api/generate/current")).json();
      netMiss = 0;
      const job = j.job;
      if (job && job.status === "running") {
        renderJobProgress(job);
        pollJob();
        return;
      }
      if (job && (job.status === "done" || job.status === "error")) {
        applyFinishedJob(job);
        return;
      }
      // 兜底：服务端已无任务记录但页面仍在生成中——以服务端为准结束状态
      if (!job) {
        setGenerating(false);
        $("progress").classList.remove("show");
        $("status").className = "status err";
        $("status").textContent = "任务已结束（服务端未保留运行记录，详见历史页最近一条，可能是生成失败）。";
      }
    } catch (e) {
      // 一次网络抖动/网关重启不能把轮询弄死：那样页面会永远停在「生成中」，
      // 而服务端其实早已结束——谎报进行中比报一次错更糟。退避后继续问。
      netMiss++;
      if (netMiss >= 4) {
        netMiss = 0;
        setGenerating(false);
        $("progress").classList.remove("show");
        $("status").className = "status err";
        $("status").textContent = "连不上工作台服务（" + friendlyErr(e) + "）进度已停止刷新。恢复后点 ⑪ 任务管理看结果。";
        return;
      }
      const wait = 2000 * netMiss;
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = setTimeout(pollJob, wait);
    }
  }, 2000);
}

async function resumeRunningJob() {
  try {
    const j = await (await fetch("/api/generate/current")).json();
    const job = j.job;
    if (job && job.status === "running") {
      // 刷新后让表单与"正在跑的那一首"对得上 —— 但**只填空白的格子**。
      // 以前这里是无条件覆写：用户刷新前正在写的歌词、刚挑好的档位，
      // 会被运行中那一首的参数整段盖掉（旧注释自己都写着"这条路最凶：
      // 它不问任何人，每次刷新都会盖掉框里的词"）。运行中任务长什么样，
      // 该由进度条与 ⑪ 去说，不该拿它去覆写用户的输入。
      const fillIfEmpty = (el, v) => {
        if (!el || (el.value || "").trim()) return false;
        el.value = v || "";
        return true;
      };
      const filled = [];
      if (fillIfEmpty($("style"), job.style)) filled.push("曲风");
      const haveLyrics = String((($("lyrics") || {}).value) || "").trim();
      if (haveLyrics) {
        // 用户已经有词：一个字都不动，只把"在跑的那首用的不是这个"说清楚
        if (String(job.lyrics || "").replace(/\s+/g, "") &&
            stripMachineOutro(job.lyrics).trim() !== $("lyrics").value.trim())
          filled.push("歌词保持你写的（在跑那首用的是另一份）");
      } else if (fillIfEmpty($("lyrics"), stripMachineOutro(job.lyrics))) {
        pushLyricUndo("刷新时回填正在生成的那一首");
        filled.push("歌词");
      }
      if (fillIfEmpty($("abc"), job.abc)) filled.push("乐谱");
      // 档位是 <select>，永远有值，"只填空白"对它没有意义 —— 干脆一律不动：
      // 刷新后保留用户自己的选择，在跑那一首实际用了哪一档由进度条与 ⑪ 的说明去讲。
      if (job.cot && ($("cot") || {}).value && job.cot !== $("cot").value)
        filled.push("档位仍是你选的 " + $("cot").value + "（在跑那首用的是 " + job.cot + "）");
      if (filled.length) $("status").textContent = "接着显示在跑的那一首：" + filled.join("、");
      // 这里**不再**从在跑那首的 style 里反解性别去写 ① 的 #gender：
      // 那是把"另一页的控件"当成这一首的附属品来回写（评审 F-7），
      // 而现在 ②⑤ 提交根本不读 #gender，反写只会让 ① 的下拉框莫名变一个值。
      // 外部改写 Style 后要重新判定"装配台 vs 手改"：运行中任务的文本不是装配台的产物，
      // 判成一致就会在下次点格子时被悄悄覆盖掉。
      if (typeof syncStyleFromOutside === "function") syncStyleFromOutside();
      const p = job.params || {};
      // ★ seed 不回写输入框。服务端在提交时把"-1/随机"落成一个真实随机数
      //   （app.py:2520，为了让历史记录能复现），那是**这一首**的种子，不是用户的选择。
      //   以前这里把它写进 #seed，表单自动保存再存进 localStorage ——
      //   用户下一首以为是随机，其实沿用了上一首那个数，而且摘要行会显示"固定 xxx"，
      //   等于界面向用户撒了两次谎。现在只记下来给摘要行如实展示。
      if (p.seed !== undefined && p.seed !== null) runningSeedActual = p.seed;
      if (typeof updateParamSummary === "function") updateParamSummary();
      // cfg / 步数与 seed 同罪：以前这里无条件把在跑那一首的参数写回输入框，
      // 表单自动保存再存进 localStorage——用户下一首"自己的选择"其实是被上一首顶掉的，
      // 界面上还显示成是他选的（评审 F-6）。改成只说不写，控件一律不动。
      const pDiff = [];
      if (p.cfg_scale !== undefined && p.cfg_scale !== null && String(p.cfg_scale) !== String($("cfg").value))
        pDiff.push("cfg " + p.cfg_scale);
      if (p.num_inference_steps && String(p.num_inference_steps) !== String($("steps").value))
        pDiff.push(p.num_inference_steps + " 步");
      if (pDiff.length)
        $("status").textContent = (($("status").textContent || "").trim() ? $("status").textContent + "；" : "")
          + "在跑那首用的是 " + pDiff.join(" / ") + "（你表单里的选择我没有动）";
      setGenerating(true);
      $("progress").classList.add("show");
      renderJobProgress(job);
      pollJob();
      return true;
    }
    if (!job && j.latest && j.latest.status === "done") {
      // 页面刷新后恢复最近一次成果（不自动播放，仅提示）
      $("status").className = "status ok";
      $("status").textContent = "上次生成已完成（" + j.latest.id + "），可在历史页回放。";
    }
  } catch {}
  return false;
}

// ---- 提交前后的"看得见"三件套 ----
// 这页是一张长表单：只把错误写进某一行 status 的话，停在左栏顶端的人根本不知道
// 是哪儿错了、也不知道提交后该看哪里。校验失败要带他到出错的字段，提交要带他到进度。
function openFolds(el) {
  // 参数区现在默认折叠：<details> 收起时里面的控件既滚不到也聚焦不了。
  // 要跳到某个字段，必须先把它的祖先折叠全打开——但只在真要定位过去时打开，
  // 不做"永久展开"，否则折叠省下的那 2 屏又回来了。
  for (let p = el ? el.parentElement : null; p; p = p.parentElement) {
    if (p.tagName === "DETAILS") p.open = true;
  }
}
function fieldProblem(el, statusEl, msg) {
  if (statusEl) { statusEl.className = "status err"; statusEl.textContent = msg; }
  if (!el) return;
  openFolds(el);
  try { el.scrollIntoView({ behavior: "smooth", block: "center" }); } catch { el.scrollIntoView(); }
  try { el.focus({ preventScroll: true }); } catch { el.focus(); }
}
function reveal(el, block) {
  if (!el) return;
  openFolds(el);
  try { el.scrollIntoView({ behavior: "smooth", block: block || "center" }); } catch {}
}
// 健康灯已经红了就别把请求打出去：以前是"允许点，然后报一句没有下一步的提交失败"。
// 只认 err（网关不通/引擎未就绪），loading 不拦——刚开页面时灯还没测完，不能因此不让提交。
function engineGate(statusEl) {
  const h = $("health");
  if (!h || !/err/.test(h.className)) return true;
  // 文案按灯的状态归类现推，不抄侧栏那行旧标签：侧栏文本是上一次探测写的，
  // 状态刚翻转时两者会不同步，抄过来就会出现"没有提交：引擎就绪 · CUDA"这种自相矛盾的话。
  const why = h.classList.contains("startable") ? "网关未连通" : "引擎未就绪";
  if (statusEl) { statusEl.className = "status err"; statusEl.textContent = "没有提交：" + why + " —— 已跳到左侧状态栏，按那里的提示启动或等它就绪。"; }
  toast(why + "，任务没有提交", "err");
  reveal(h, "center");
  return false;
}

// ★ 提交时到底带不带乐谱，由"这一页是谁"决定，不由 DOM 里有没有残留决定。
//   #abcBlock 是 ③⑤ 共用的同一份 DOM（切页时 moveInto 搬来搬去），而且 _FORM_IDS
//   把 abc 存进了 localStorage —— 于是 ② 上看不见谱框，$("abc").value 却读得到上一次
//   ③ 转出来的那份谱。一旦带上，服务端 _resolve_cot 会因为"谱里没有和弦记号"
//   把用户选的 full 强改成 melody，并且**按那份谱的旋律去生成**：
//   用户只想给曲风+歌词，听到的却是别的歌的调子（实测投诉就是这个）。
//   ② 的初衷是"只给 Style 和词，旋律交给模型规划"，所以它必须恒定交空谱。
function abcForSubmit() {
  return (curTab === "scoreSing") ? ($("abc") || { value: "" }).value : "";
}

$("go").onclick = async () => {
  // 送出的是用户自己写的那份词，收尾段由后端在送引擎前统一补（app.py: _gen_run）
  const lyrics = stripMachineOutro($("lyrics").value.trim());
  // ★ 这一页交的就是 #style 框里那一段，一个字不多一个字少。
  //   以前这里过 styleWithGender()，会去读 ① 装配台上的 #gender/#bpm 并改写这句曲风——
  //   那两个控件在本页根本不存在（它们在 #tab-style 里），用户看不见也改不了，
  //   却在为"界面上没有的东西"买单（评审 F-2）。
  const style = $("style").value.trim();
  if (!engineGate($("status"))) return;
  if (!lyrics || !$("style").value.trim()) {
    fieldProblem(!$("style").value.trim() ? $("style") : $("lyrics"), $("status"),
      "还差" + (!$("style").value.trim() ? "曲风（Style）" : "歌词") + "才能生成 —— 已跳到该填的地方。");
    return;
  }
  // 护栏①：结构校验挪到提交前。一次完整生成 60-90 分钟（草稿档 1-2 分钟）、6GB 显存全程占着，
  // 以前"副歌没写全 / 某段只有 3 行"要等歌出来才听出来，或者得自己记得点「🔍」。
  if (!(await guardThen($("go"), "go"))) return;
  const genCount = Math.max(1, Math.min(20, numOrNull("genCount") || 1));
  const payload = {
    task_name: $("taskName").value.trim(),
    lyrics, style,
    cot: $("cot").value,
    abc: abcForSubmit(),
    model: "yue2",
    count: genCount,
    seed: numOrNull("seed"), cfg_scale: numOrNull("cfg"), num_inference_steps: numOrNull("steps"),
    abc_temperature: numOrNull("abc_temperature"), abc_top_p: numOrNull("abc_top_p"), abc_top_k: numOrNull("abc_top_k"),
    semantic_temperature: numOrNull("semantic_temperature"), semantic_top_p: numOrNull("semantic_top_p"), semantic_top_k: numOrNull("semantic_top_k"),
  };
  setGenerating(true); $("download").disabled = true;
  $("progress").classList.add("show"); $("progressBar").style.width = "6%";
  reveal($("progress"), "nearest");   // 进度条在按钮下方：不在视野里就等于"点了没反应"
  $("status").className = "status";
  $("status").textContent = genCount > 1 ? `已提交 ${genCount} 首连发任务到服务端…` : "任务已提交到服务端…";
  try {
    const r = await fetch("/api/generate/start", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(payload) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = j.detail || JSON.stringify(j);
      throw new Error(r.status === 409 ? "已有生成任务在进行中（刷新页面可恢复进度）" : String(d).slice(0, 300));
    }
    if (j.cot_note) toast(j.cot_note, "ok");
    else if (payload.abc.trim()) toast("已按你填写的乐谱生成（cot=" + j.cot + "）", "ok");
    pollJob();
  } catch (err) {
    $("status").className = "status err"; $("status").textContent = "提交失败：" + friendlyErr(err);
    setGenerating(false);
    $("progress").classList.remove("show");
    reveal($("status"), "nearest");
  }
};
$("randSeed").onclick = () => { $("seed").value = String(Math.floor(Math.random() * 2147483647)); };

// 生成档位预设：只改两个成本参数，不自动提交。
// 数字来自本机实测：cot=off + 8 步约 80 秒；full + 32 步 60-90 分钟（6GB 卡）。
const GEN_PRESETS = {
  draft:    { cot: "off",  steps: 8,  name: "草稿", hint: "草稿：off · 8 步（有谱时 melody），最快 1-2 分钟" },
  standard: { cot: "full", steps: 16, name: "标准", hint: "标准：full · 16 步，速度与质量折中" },
  final:    { cot: "full", steps: 32, name: "成曲", hint: "成曲：full · 32 步，本机可能 1 小时以上" },
};
function genPresetKey(cot, steps) {
  const n = Number(steps);
  if (n === 8 && (cot === "off" || cot === "melody")) return "draft";
  if (n === 16 && cot === "full") return "standard";
  if (n === 32 && cot === "full") return "final";
  return null;
}
function syncGenPresetFromFields() {
  const key = genPresetKey($("cot").value, $("steps").value);
  document.querySelectorAll("[data-gen-preset]").forEach((b) => {
    b.classList.toggle("on", b.dataset.genPreset === key);
  });
  const hint = $("presetHint");
  if (hint) hint.textContent = key
    ? GEN_PRESETS[key].hint
    : "自定义：CoT " + $("cot").value + " / " + $("steps").value + " 步";
}
function applyGenPreset(key) {
  const p = GEN_PRESETS[key];
  if (!p) return;
  const hasScore = abcForSubmit().trim().length > 0;
  $("cot").value = (key === "draft" && hasScore) ? "melody" : p.cot;
  $("steps").value = String(p.steps);
  syncGenPresetFromFields();
}
document.querySelectorAll("[data-gen-preset]").forEach((b) => {
  b.onclick = () => applyGenPreset(b.dataset.genPreset);
});
$("cot").addEventListener("change", syncGenPresetFromFields);
$("steps").addEventListener("input", syncGenPresetFromFields);
syncGenPresetFromFields();
// 歌词 TXT 导出：纯前端，不依赖生成/VAE。LRC/eLRC 仍需音频时间轴，见历史页。
$("exportLyricsTxt").onclick = () => {
  const text = ($("lyrics").value || "").trim();
  if (!text) { toast("歌词框是空的", "err"); return; }
  const blob = new Blob([$("lyrics").value], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = dlName(($("taskName") && $("taskName").value.trim()) || "歌词", "txt");
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  toast("已开始下载歌词 TXT", "ok");
};

// 加入队列：复用批量队列 API，把创作页当前参数作为单任务排入队尾（不抢占正在生成的任务）
$("queue").onclick = async () => {
  // 与「生成歌曲」同一份口径：入队的也是用户自己写的那份词与那句曲风（见上面 F-2 的说明）
  const lyrics = stripMachineOutro($("lyrics").value.trim());
  const style = $("style").value.trim();
  if (!engineGate($("status"))) return;
  if (!lyrics || !$("style").value.trim()) {
    fieldProblem(!$("style").value.trim() ? $("style") : $("lyrics"), $("status"),
      "还差" + (!$("style").value.trim() ? "曲风（Style）" : "歌词") + "才能入队 —— 已跳到该填的地方。");
    return;
  }
  if (!(await guardThen($("queue"), "queue"))) return;   // 入队同样先过结构校验（排进队尾再被拦更浪费）
  const btn = $("queue"); btn.disabled = true;
  try {
    // 与 /api/generate/start 同构的 payload；batch API 取同名字段
    const task = {
      name: $("taskName").value.trim() || undefined,
      lyrics, style,
      cot: $("cot").value, abc: abcForSubmit(), model: "yue2",
      seed: numOrNull("seed"), cfg_scale: numOrNull("cfg"), num_inference_steps: numOrNull("steps"),
      abc_temperature: numOrNull("abc_temperature"), abc_top_p: numOrNull("abc_top_p"), abc_top_k: numOrNull("abc_top_k"),
      semantic_temperature: numOrNull("semantic_temperature"), semantic_top_p: numOrNull("semantic_top_p"), semantic_top_k: numOrNull("semantic_top_k"),
    };
    const r = await fetch("/api/batch/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: task.name, tasks: [task] }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || "HTTP " + r.status);
    toast(j.message || "已加入队列", "ok");
    if (j.cot_note) toast(j.cot_note, "ok");
    $("status").className = "status ok";
    // 明确报出这一批的队列身份（名字 + 唯一 ID），避免和历史队列混淆
    $("status").textContent = (j.message || "已加入队列，将在后台依次生成（可在历史页查看进度）。")
      + " 队列「" + (j.name || "") + "」" + (j.queue_id ? " ID " + j.queue_id : "");
  } catch (err) {
    $("status").className = "status err"; $("status").textContent = "入队失败：" + friendlyErr(err);
  } finally { btn.disabled = false; }
};

// ---------- ABC 乐谱分析（调性/拍号/BPM/曲式/音域摘要） ----------
let abcAnalyzeTimer = null;
let lastAbcAnalysis = null;
function renderAbcAnalysis(a) {
  const el = $("abcAnalysis");
  if (!el) return;
  lastAbcAnalysis = a || null;
  updateParamSummary();   // 谱分析一出结论（或清空），折叠标题上的摘要就要跟着改口径
  if (!a || (!a.key && !a.meter && !a.bpm && !(a.sections || []).length && !a.cot_suggested)) { el.textContent = ""; return; }
  const parts = [];
  if (a.key) parts.push("🎼 调性 " + a.key);
  if (a.meter) parts.push("拍号 " + a.meter);
  if (a.bpm) parts.push(a.bpm + " BPM");
  if (a.range) parts.push("音域 " + a.range);
  if (a.note_count === 0) parts.push("⚠ 谱里没有音（全是休止）");
  if ((a.sections || []).length) parts.push("曲式 " + a.sections.join(" → "));
  const route = a.cot_suggested || "";
  if (route) {
    const n = a.chord_count || 0;
    // 有谱时档位由谱面决定，不再让人手选：谱里有和弦记号 → full（模型照"旋律+和弦"走），
    // 没有 → melody（只走旋律，伴奏由模型配）。选错档位是最常见的"生成出来不对"的原因，
    // 而这件事谱面本身就答得出来，不该交给用户猜。
    // 自动锁档只在 ⑤（歌词+style+乐谱）生效：那份谱是可见的、且与档位强相关。
    // ②（歌词+style，无乐谱 UI）不应被共用的 #abc 残留内容影响，档位完全交回用户手选。
    const cotSel = $("cot");
    if (cotSel && curTab === "scoreSing" && cotSel.value !== route) cotSel.value = route;
    parts.push(route === "full"
      ? ("带和弦谱" + (n ? "（" + n + " 处和弦记号）" : "") + " → 已自动定为 full")
      : "纯旋律谱（无和弦记号） → 已自动定为 melody");
  }
  // 一份没音的谱不该顶着一个绿勾——同一行里"✓"和"⚠ 谱里没有音"互相打脸
  el.textContent = (a.note_count === 0 ? "⚠ " : "✓ ") + parts.join(" · ");
}
function analyzeAbcDebounced() {
  clearTimeout(abcAnalyzeTimer);
  abcAnalyzeTimer = setTimeout(async () => {
    const abc = $("abc").value.trim();
    if (!abc) {
      // 谱被清空时上一份分析结论要一起作废。以前只擦显示区，lastAbcAnalysis 还留着，
      // 于是 ⑤ 进页会"重放"上一首歌的分析、② 的摘要跟着说"谱要 melody"——
      // 而那首歌的谱早就不在了（评审 F-4）。
      lastAbcAnalysis = null;
      $("abcAnalysis").textContent = "";
      updateParamSummary();
      return;
    }
    try {
      // POST body 规避 GET query 请求行上限（h11 16KB），中长乐谱也能安全分析
      const r = await fetch("/api/abc/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ abc: abc.slice(0, 200000) }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const j = await r.json();
      renderAbcAnalysis(j.analysis);
    } catch { $("abcAnalysis").textContent = ""; }
  }, 500);
}
$("abc").addEventListener("input", () => {
  analyzeAbcDebounced();
  // 档位提示以前挂在跨页 store 的订阅上；store 删了，改成本页控件自己变化就重画
  if (typeof paintScoreModeTag === "function") paintScoreModeTag();
});
// 切换 CoT 时即时重算提示（不重新请求分析）：让用户当场看到"会不会按我的谱走"。
// 只在 ⑤ 才重放自动锁档；② 上用户手选的档位必须保留，不被共用 #abc 的残留内容弹回。
$("cot").addEventListener("change", () => {
  if (curTab === "scoreSing" && $("abc").value.trim() && lastAbcAnalysis) renderAbcAnalysis(lastAbcAnalysis);
});

// ---------- 从参考歌曲提取乐谱（SheetSage2：音频 → ABC，填入创作页直接改词翻唱） ----------
// 异步任务模式：提交后立即拿 job_id，轮询取结果（真实歌曲转谱常超 1 分钟，
// 长请求会被 dsh 反代超时掐断，故不用同步等待）。
$("extractAbc").onclick = () => {
  const rid = ($("abcPick") && $("abcPick").value || "").trim();
  if (rid) submitScore(null, rid); else $("abcAudio").click();
};
$("abcAudio").onchange = () => {
  const f = $("abcAudio").files[0];
  if (!f) return;
  // 明确拖/选文件时，服务器歌曲下拉让位，避免按钮文案与真正提交的东西不一致
  if ($("abcPick")) $("abcPick").value = "";
  $("extractAbc").textContent = "🎸 选择歌曲开始转谱";
  submitScore(f, "");
};
$("abcPick").onchange = () => {
  const on = !!($("abcPick").value || "").trim();
  $("extractAbc").textContent = on ? "🎸 转谱所选服务器歌曲" : "🎸 选择歌曲开始转谱";
  if (on && $("abcAudio")) $("abcAudio").value = "";
};
async function submitScore(file, rid) {
  if (!file && !rid) return;
  const form = new FormData();
  if (rid) form.append("rid", rid); else form.append("audio", file);
  // 默认纯旋律谱（SheetSage2 两种模式都会识别和弦，只是导出时写不写进 ABC）
  form.append("melody_only", $("abcChords").checked ? "false" : "true");
  // 服务端转谱有串行锁，并发两份会互相覆盖 sheetsage2-output/ —— 提交即锁住上传口
  setScoreLock(true);
  $("abcStatus").textContent = rid
    ? "正在读取服务器歌曲并提交转谱任务" + ($("abcChords").checked ? "（带和弦）" : "") + "…"
    : ($("abcChords").checked ? "正在上传并提交转谱任务（带和弦）…" : "正在上传并提交转谱任务…");
  try {
    const r = await fetch("/api/score", { method: "POST", body: form });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      const msg = j.detail || j.error || (typeof j === "string" ? j : JSON.stringify(j));
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    const jobId = j.job_id;
    if (!jobId) throw new Error("网关未返回任务 ID");
    // 轮询结果（每 3 秒，最长 20 分钟）
    $("abcStatus").textContent = "转谱进行中（首次需加载模型，请耐心等待）…";
    const deadline = Date.now() + 20 * 60 * 1000;
    let miss = 0;
    while (Date.now() < deadline) {
      await new Promise((res) => setTimeout(res, 3000));
      // 一次 502/网络抖动不等于提取失败：服务端任务还在跑并会成功入库，
      // 这里整体抛错会让用户白重传一遍（GPU 时间）。连错 5 次才认输。
      let rr;
      try { rr = await fetch("/api/score/result?job_id=" + encodeURIComponent(jobId)); }
      catch (e) {
        if (++miss >= 5) throw new Error("查询任务失败（连续 " + miss + " 次连不上服务）");
        continue;
      }
      const rj = await rr.json().catch(() => ({}));
      if (!rr.ok) {
        if (rr.status !== 404 && ++miss < 5) continue;
        throw new Error(rj.detail || "查询任务失败");
      }
      miss = 0;
      if (!rj.done) continue;
      if (rj.error) throw new Error(rj.error);
      if (!rj.abc) throw new Error("没有得到乐谱");
      $("abc").value = rj.abc;
      // 提取完记下这份谱该走哪条路线 —— 但**不在 ③ 写 ②⑤ 的档位下拉框**。
      // #cot 在 #genForm 里（②⑤ 共用），此刻人在 ③，写了看不见，
      // 等他切到 ② 才发现"我选的 full 自己变成 melody"——这正是 ② 带残留谱那个
      // 投诉的同一个形状。⑤ 的进入钩子（switchTab 里 replay renderAbcAnalysis）
      // 已经会按当前共用谱重放一次锁档，所以这里只交给它。
      const want = (rj.analysis && rj.analysis.cot_suggested)
        || ($("abcChords").checked ? "full" : "melody");
      if (curTab === "scoreSing") $("cot").value = want;
      syncGenPresetFromFields();
      renderAbcAnalysis(rj.analysis);
      if (!rj.analysis) analyzeAbcDebounced();
      const wantedChords = $("abcChords").checked && !(rj.analysis && rj.analysis.has_chords);
      // 实测：很轻/很安静的片段，SheetSage2 只会吐一串休止（新旧代码一样，不是回归）。
      // 这种"成功"必须说不清——绿灯一句"已保存"，用户会拿着一份没音的谱去生成 40 分钟。
      const noNotes = !!rj.analysis && rj.analysis.note_count === 0;
      if (noNotes) {
        $("abcStatus").textContent = "⚠ 转出来这份谱里一个音都没有（只有休止）——多半是这段太轻或太安静，"
          + "模型没抓到旋律。换一段人声明显的（副歌那种）再转，或先把音量归一化。";
        $("abcStatus").style.color = "#ff9f0a";
      } else {
        $("abcStatus").textContent = wantedChords
          ? "⚠ 谱里没出现和弦记号（这首歌可能识别不出可用和弦），已按 melody 路线准备生成"
          : "✓ 乐谱已保存（走 " + want + "），可改词后生成（下方列表可随时复用）";
        $("abcStatus").style.color = wantedChords ? "#ff9f0a" : "var(--ok, #34c759)";
      }
      setScoreLock(false);
      $("abcAudio").value = "";
      // 转谱成功的谱已落盘 data/scores/，记下 score_id 才谈得上导出 MIDI
      scoreCurrentId = rj.score_id || null;
      $("abcRetryWrap").hidden = true;
      stickyNotify(noNotes ? "转谱没抓到旋律" : "乐谱提取完成",
                   noNotes ? "谱里一个音都没有（只有休止），换一段人声明显的再转。"
                           : "已从参考歌曲提取 ABC 乐谱，可直接改词生成。", true);
      loadScoreList();
      return;
    }
    throw new Error("转谱超时（超过 20 分钟）");
  } catch (err) {
    $("abcStatus").textContent = "提取失败：" + friendlyErr(err);
    $("abcStatus").style.color = "var(--err, #ff3b30)";
    setScoreLock(false);
    $("abcAudio").value = "";
    // 带和弦时个别和弦性质不被记号表认得会让整份谱失败——把"改纯旋律重试"做成一键，
    // 而不是让用户自己去猜该取消哪个勾选
    $("abcRetryWrap").hidden = !$("abcChords").checked;
    stickyNotify("乐谱提取失败", friendlyErr(err), false);
  }
}

// ---------- 乐谱历史（服务端持久化 data/scores/，刷新/重启不丢，可复用回填） ----------
async function loadScoreList() {
  const el = $("scoreList");
  try {
    const list = await (await fetch("/api/scores")).json();
    if (!list.length) { el.innerHTML = ""; return; }
    el.innerHTML = '<div style="font-size:12px;color:var(--dim);margin:2px 0 4px;">已保存乐谱（点击回填，✕ 删除）</div>';
    list.forEach((s) => {
      const row = document.createElement("div");
      row.style.cssText = "display:flex;align-items:center;gap:8px;padding:4px 8px;margin:2px 0;border-radius:var(--r-sm);font-size:12px;cursor:pointer;background:rgba(127,127,127,.06);";
      const label = document.createElement("span");
      label.style.cssText = "flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
      label.textContent = (s.title ? s.title + " · " : "") + (s.time || "") + (s.cot ? " [" + s.cot + "]" : "") + " · "
        + (s.abc_preview || "").replace(/\s+/g, " ").slice(0, 40);
      label.title = "点击回填到乐谱框";      label.onclick = async () => {
        try {
          const rec = await (await fetch("/api/scores/" + encodeURIComponent(s.id))).json();
          $("abc").value = rec.abc || "";
          const sug = rec.analysis && rec.analysis.cot_suggested;
          // 同上：③ 回填历史谱只填谱框，档位交给 ⑤ 进入时自己重放（见 switchTab）。
          if (sug && curTab === "scoreSing") $("cot").value = sug;
          syncGenPresetFromFields();
          renderAbcAnalysis(rec.analysis);
          if (!rec.analysis) analyzeAbcDebounced();
          $("abcStatus").textContent = "✓ 已回填历史乐谱 " + s.id;
          $("abcStatus").style.color = "var(--ok, #34c759)";
          // 记下这是库里哪一份，导出 MIDI 才知道取哪个归档目录
          scoreCurrentId = s.id;
        } catch (e) { $("abcStatus").textContent = "回填失败：" + friendlyErr(e); }
      };
      const del = document.createElement("button");
      del.className = "ghost";
      del.textContent = "✕";
      del.title = "删除该乐谱";
      del.style.cssText = "padding:0 8px;font-size:12px;";
      const idc = document.createElement("span");
      idc.innerHTML = idTag(s.id);
      idc.style.cssText = "display:inline-flex;align-items:center;";
      del.onclick = async (ev) => {
        ev.stopPropagation();
        await fetch("/api/scores/" + encodeURIComponent(s.id), { method: "DELETE" });
        loadScoreList();
      };
      row.appendChild(label); row.appendChild(idc); row.appendChild(del);
      el.appendChild(row);
    });
  } catch (e) { el.innerHTML = ""; }
}
loadScoreList(); // 页面加载即展示历史（刷新后乐谱不丢）

// ---------- batch（批量生成队列）----------
let batchPoll = null;
let histQueuePoll = null; // 历史页队列区轮询句柄

// batch UI 已移除：创作页「加入队列」+ 历史页「生成队列」区承接（API /api/batch/* 保留）

// ---------- history（output/ 自动落盘，含 style/歌词/参数）----------
// ---------- history：进行中任务（生成/换声/音色训练） ----------
const _KIND_LABEL = { generate: "🎵 生成", rvc: "🎙 换声", train: "🎨 音色制作", score: "🎼 乐谱提取", separate: "✂ 分离" };
// 历史页当前子页签（all/generate/rvc/train）
let histKind = "all";
let histPage = 60;            // 一屏先渲这么多行（HIST_KEEP 上限 1000，全量渲染会卡）
// 类型筛选（歌曲/换声/音色制作）与状态筛选（全部/进行中/已完成/失败）双维组合
let histType = "all";
$("histKindTabs").querySelectorAll(".subtab").forEach(btn => {
  btn.onclick = () => {
    histType = btn.dataset.kind || "all";
    histPage = 60;
    $("histKindTabs").querySelectorAll(".subtab").forEach(b => b.classList.toggle("active", b === btn));
    renderHistory();
  };
});
$("histTabs").querySelectorAll(".subtab").forEach(btn => {
  btn.onclick = () => {
    histKind = btn.dataset.kind || "all";
    histPage = 60;
    $("histTabs").querySelectorAll(".subtab").forEach(b => b.classList.toggle("active", b === btn));
    renderHistory();
  };
});
// 搜索框：防抖 250ms，输入即过滤（任务名/曲风/ID）
(() => {
  const el = $("histSearch");
  if (!el) return;
  let timer = null;
  el.oninput = () => { clearTimeout(timer); histPage = 60; timer = setTimeout(renderHistory, 250); };
})();

async function fetchExtraTasks() {
  // 队列 + 活跃两个数据源，归一化成与历史记录同构的条目，由 renderHistory 合并渲染
  const out = [];
  try {
    const b = await (await fetch("/api/batch/status")).json();
    // 队列条目全量带回：失败/取消的条目也要能被历史列表认出来（isQueue），
    // 否则「重新排队这一条」永远不显示——后端以前只给 pending/running，
    // 而失败条目的 output meta 又让它们看起来只是普通历史记录。
    const allQ = (b && b.items) || [];
    // 队列身份按「一次提交」划分：一次投 2 首就只报 2 首，历史条目单独计数，
    // 不再把状态文件里所有旧任务的总数当成当前这批（寻兰 58/2 那次误导的根因）
    const q = (b && b.queue) || null;
    const curQ = q && q.id ? allQ.filter(x => x.qid === q.id) : allQ;
    // 位次：正在跑 = 第 1 位，其余 pending 顺延；历史队列条目不算位次
    const posMap = {};
    let pos = 0;
    curQ.forEach(x => {
      if (x.status === "running") posMap[x.id] = 1;
      else if (x.status === "pending") { pos += 1; posMap[x.id] = pos + (curQ.some(y => y.status === "running") ? 1 : 0); }
    });
    const bar = $("queueBar");
    if (bar) {
      if (q && q.id) {
        const seg = [
          (b.running ? "🏃 正在跑" : "⏸ 已停") + " · 队列「" + escapeHtml(q.name || "未命名") + "」",
          idTag(q.id),
          q.done + "/" + q.total + " 完成",
          q.error ? q.error + " 失败" : "",
          q.pending ? q.pending + " 待跑" : "",
          b.queued_hist ? "另有 " + b.queued_hist + " 条历史队列记录" : "",
        ].filter(Boolean);
        bar.innerHTML = seg.join(" · ");
      } else bar.innerHTML = "";
    }
    // 队列按钮：跑着时提供「重启失败任务」（只排回待跑，不打断当前这首）；
    // 停着时提供「一键继续」。失败数与可继续数都只看当前队列，不碰历史队列。
    const failedN = curQ.filter(x => x.status === "error" || x.status === "cancelled").length;
    const unfinished = curQ.filter(x => ["pending", "running", "error", "cancelled"].includes(x.status)).length;
    const resumeBtn = $("batchResume");
    if (resumeBtn) {
      if (b && b.running && failedN > 0) {
        resumeBtn.style.display = "";
        resumeBtn.textContent = "↻ 重启失败任务（" + failedN + "）";
        resumeBtn.title = "把当前队列里失败/取消的任务重新排队；正在算的这首不受影响，跑完按顺序接上";
      } else if (b && !b.running && unfinished > 0) {
        resumeBtn.style.display = "";
        resumeBtn.textContent = "▶ 一键继续";
        resumeBtn.title = "把意外终止/已停止/失败的队列任务重新入队继续执行";
      } else {
        resumeBtn.style.display = "none";
      }
    }
    // 「⏹ 停止队列」只在队列正在跑时出现：它停的是"下一条不再开始"，
    // 与「终止任务」（掐掉正在算的这条）是两件事，别让用户猜
    const stopQBtn = $("batchStop");
    if (stopQBtn) stopQBtn.style.display = b && b.running ? "" : "none";
    allQ.forEach(it => {
      const p = it.payload || {};
      out.push({
        id: it.id, kind: "generate", status: it.status,
        ts: it.started_ts || it.ts || "", task_name: it.name || "未命名",
        style: p.style || "", lyrics: p.lyrics || "", cot: p.cot || "full", abc: p.abc || "",
        params: p, error: it.error || "", sec: it.sec, bytes: it.bytes,
        // 排队中的批量条目也要能看见"服务端改了什么"。这个字段以前只从产物 meta 里取，
        // 于是 pending/running 的队列行是最需要看说明的时候反而没有（评审 B-6）。
        cot_note: it.cot_note || "",
        isQueue: true,
        qid: it.qid || "",
        queue_pos: posMap[it.id] || 0,
        progress: it.progress || null,
      });
    });
    // 队列执行中保持轮询刷新（离开任务管理页即停）
    if (b && b.running && !histQueuePoll) {
      histQueuePoll = setInterval(() => {
        if (curTab !== "history") { clearInterval(histQueuePoll); histQueuePoll = null; return; }
        renderHistory(120);
      }, 5000);
    }
  } catch {}
  try {
    const d = await (await fetch("/api/history/active")).json();
    (d.items || []).filter(j => j.id).forEach(j => {
      const kind = j.kind || (j.step !== undefined ? "train" : (j.model && j.src_duration !== undefined ? "rvc" : "generate"));
      out.push({
        // 状态如实透传：写死 running 会把"排队等前一个换声算完"显示成"正在转换"
        id: j.id, kind, status: j.status || "running", ts: j.ts || "",
        queue_pos: j.queue_pos || 0,
        task_name: j.task_name || j.name || (j.model || "").replace(/\.pth$/i, "") || "未命名",
        style: j.style || "", lyrics: j.lyrics || "", cot: j.cot || "full", abc: j.abc || "",
        params: j.params || {}, sec: j.sec, bytes: j.bytes,
        progress: j.progress || null,
      });
    });
  } catch {}
  // 乐谱提取 / 分离 两类不在 output/*.json 的主口径里（乐谱在 data/scores/），
  // 由聚合端点一次带回，省掉前端再拼两个源。去重交给 renderHistory 按 id 做——
  // seen 在它自己那一层，本函数里引用不到（引用了会整页抛错、历史列表直接空掉）。
  try {
    const u = await (await fetchT("/api/tasks/unified?limit=400", {}, 30000)).json();
    (u.items || []).filter((x) => x.kind === "score" || x.kind === "separate")
      .forEach((x) => out.push(x));
  } catch {}
  return out;
}

async function renderHistory(fetchLimit) {
  try {
    // 审计 C-T2：队列轮询时只需头部若干条（一屏 60 行），传 fetchLimit 减少每 5s 全量拉 1000 条的开销
    const r = await fetch("/api/generate/list" + (fetchLimit ? "?limit=" + fetchLimit : ""));
    const j = await r.json();
    let list = Array.isArray(j.items) ? j.items : [];
    // 队列 + 活跃条目并入统一列表（按 id 去重：历史里已完结的优先）
    const extra = await fetchExtraTasks();
    // 同一 id 在 output meta 与队列里都有时，队列的 pending/running 才是新状态：
    // 批量任务失败后又被「重新排队」，meta 还留着上一轮的 error；不去覆盖，
    // 卡片就会把"已在队列里等着"继续画成"这次又失败了"（31 首那次全是这样）。
    const qmap = new Map(extra.filter(x => x.isQueue).map(x => [x.id, x]));
    list.forEach(h => {
      const q = qmap.get(h.id);
      if (!q) return;
      h.isQueue = true;
      if (q.queue_pos) h.queue_pos = q.queue_pos;
      if (q.progress) h.progress = q.progress;
      if (q.status === "pending" || q.status === "running") {
        h.status = q.status;
        h.error = "";
        if (q.cot_note) h.cot_note = q.cot_note;
      }
    });
    const seen = new Set(list.map(h => h.id));
    // extra 内部也要去重：同一 id 可能同时来自 batch/status 与 history/active
    // （刚提交、output meta 尚未落盘的那几秒），不去重就渲染成两行同 ID 任务
    extra.forEach(x => { if (!seen.has(x.id)) { seen.add(x.id); list.push(x); } });
    // 排序：进行中（running/pending）置顶，其余按时间由近及远（新→旧）；
    // ts 是 ISO 字符串可直接字典序比较，缺失的排最后
    const rankOf = h => (h.status === "running" || h.status === "pending") ? 0 : 1;
    const tsOf = h => h.ts || "";
    list.sort((a, b) => rankOf(a) - rankOf(b) || (tsOf(a) < tsOf(b) ? 1 : tsOf(a) > tsOf(b) ? -1 : 0));
    $("histCount").textContent = list.length;
    // 页头那份计数（和侧栏徽牌同源，但不是同一个元素 —— 侧栏那个 id 是 histCount）
    const hcHead = $("histCountHead"); if (hcHead) hcHead.textContent = list.length;
    // 状态标签过滤：全部/进行中(running+pending)/已完成(done)/失败(error+cancelled)
    const stateOf = h => h.status === "done" ? "done"
      : (h.status === "error" || h.status === "cancelled") ? "error"
      : "running";
    const counts = { all: list.length, running: 0, done: 0, error: 0 };
    list.forEach(h => counts[stateOf(h)]++);
    for (const k of ["all", "running", "done", "error"]) {
      const n = $("histN_" + k); if (n) n.textContent = counts[k];
    }
    if (histKind !== "all") list = list.filter(h => stateOf(h) === histKind);
    // 类型双维筛选：队列条目 kind 固定 generate；历史条目按 kind 字段
    if (histType !== "all") list = list.filter(h => (h.kind || "generate") === histType);
    // 关键字筛选：任务名 / 曲风 / 任务 ID（历史条目上千后，光靠翻页找不回来）
    const kw = (($("histSearch") || {}).value || "").trim().toLowerCase();
    if (kw) list = list.filter(h => [h.task_name, h.name, h.style, h.id]
      .some(v => String(v || "").toLowerCase().includes(kw)));
    const shown = list.slice(0, histPage);
    // 搜索时把「全部」页签变成 匹配数/总数，否则用户看到 8 行却写着 113 会以为漏了
    const allBadge = $("histN_all");
    if (allBadge) allBadge.textContent = kw ? shown.length + "/" + counts.all : counts.all;
    const el = $("histList");
    // 「⋯ 更多」菜单展开时本轮不重建 DOM：5s 队列轮询整表 innerHTML 重绘会把菜单
    // 悄悄关掉（实测 6.5s 后自动关闭），用户正在点的按钮随之丢失。
    // 菜单收起后的下一轮轮询自然补回最新状态。
    if ([...el.querySelectorAll("[data-more-menu].open")].length) return;
    if (!shown.length) { el.innerHTML = `<div class="empty">${histKind === "all" ? "暂无历史记录。生成歌曲后会自动保存（含音频与参数）。" : "该分类下暂无记录。"}</div>`; return; }
    el.innerHTML = "";
    // ★ 列头：五列各是什么写在最上面。它和 .hist-item 共用 --hcol-* 列模板，
    //   所以"任务"两个字正好压在任务列上，不需要任何手工对齐。
    //   第 3 列的表头写成"歌词 / 摘要"：乐谱行放的是 ABC 摘要、分离行放的是产物清单，
    //   只写"首句歌词"会让这两类行看起来"放错了列"。
    if (shown.some(h => h.kind !== "score" && h.kind !== "separate")) {
      const head = document.createElement("div");
      head.className = "h-head";
      head.innerHTML = "<span>任务</span><span>状态</span><span>歌词 / 摘要</span>"
        + '<span class="hh-meta">时长 · 耗时 · 大小</span><span class="hh-act">操作</span>';
      el.appendChild(head);
    }
    shown.forEach(h => {
      // 乐谱提取 / 分离 两类没有音频产物与歌词，用轻量行，不套生成任务那 12 个按钮
      if (h.kind === "score" || h.kind === "separate") { el.appendChild(lightTaskRow(h)); return; }
      const d = document.createElement("div");
      d.className = "hist-item";
      const title = (h.task_name || (h.style || "").split(",")[0] || "未命名");
      const size = h.bytes ? " · " + fmtBytes(h.bytes) : "";
      const isTrain = h.kind === "train";
      const isRvc = h.kind === "rvc";
      const stMap = { done: "✓ 成功", error: "✕ 失败", cancelled: "⏹ 已终止", pending: "⏳ 排队中" };
      const progTxt = h.status === "running" && h.progress && h.progress.label
        ? " · " + h.progress.label : "";
      const stTxt = (h.status === "done" ? stMap.done
        : (h.status === "pending" && Number(h.queue_pos) > 1) ? `⏳ 排队中（第 ${h.queue_pos} 位）`
        : stMap[h.status] || "⏳ 进行中") + progTxt;
      // 状态色走主题令牌，不走硬编码色值：#2c8a4b/#c33/var(--acc) 在面板上分别只有
      // 3.3/4.0/3.4:1（12px 要 4.5），而 --ok/--err/--acc-text 是量过的 4.6-5.5:1。
      const stColor = h.status === "done" ? "var(--ok)" : (h.status === "error" || h.status === "cancelled") ? "var(--err)" : "var(--acc-text)";
      const firstLyric = (h.lyrics || "").split("\n").map(s => s.trim()).find(s => s && !s.startsWith("[")) || "";
      const dur = h.sec ? (typeof h.sec === "number" ? Math.round(h.sec) + "s" : h.sec) : "";
      // 生成耗时：服务端未记录时（历史旧数据）留空
      const elapsed = h.elapsed_sec ? Math.round(h.elapsed_sec * 10) / 10 + "s" : "";
      const kindTag = isTrain ? " · 🎨 音色" : isRvc ? " · 🎙 换声" : "";
      d.innerHTML = `
        <div class="h-col">
          <div class="h-title">${escapeHtml(title)}${kindTag}</div>
          ${h.ts || h.id ? '<div class="h-sub">' + [h.ts ? timeAgo(h.ts) : "", idTag(h.id)].filter(Boolean).join(" ") + '</div>' : ''}
          ${h.status === "error" && h.error ? `<div class="h-sub h-errline" style="color:var(--err); cursor:pointer;" data-err title="点击展开/收起完整错误">⚠ ${escapeHtml(String(h.error).slice(0, 120))}${String(h.error).length > 120 ? " …（点击查看全部）" : ""}</div>` : ""}
          ${h.cot_note ? `<div class="h-sub" style="color:var(--muted);" title="服务端在送引擎前对你提交的内容做的调整（当时只在屏幕上弹了 3.6 秒，这里才是能查的地方）｜完整原文：${escapeHtml(String(h.cot_note))}">🛈 ${escapeHtml(String(h.cot_note).slice(0, 120))}${String(h.cot_note).length > 120 ? " …（悬停看全文）" : ""}</div>` : ""}
        </div>
        <div class="h-col h-col-st" style="color:${stColor};">${stTxt}</div>
        <div class="h-col">
          <div class="h-sub" style="margin-top:0; font-size:var(--fs-xs);">${firstLyric ? escapeHtml(firstLyric) : '<span style="color:var(--muted)">—</span>'}</div>
        </div>
        <div class="h-col h-col-meta" title="${escapeHtml([dur && '时长 ' + dur, elapsed && '耗时 ' + elapsed, h.bytes && '大小 ' + fmtBytes(h.bytes)].filter(Boolean).join(" · ") || '无')}">
          ${[dur && '🎵 ' + dur, elapsed && '⏱ ' + elapsed, h.bytes && fmtBytes(h.bytes)].filter(Boolean).join(' · ') || '—'}
        </div>
        <div class="h-actions">
          ${h.status === "done" && !isTrain && !(isRvc && h.assets && h.assets.full_song) ? '<button class="ghost" data-play>▶ 播放</button>' : ''}
          ${h.status === "done" && isRvc && h.assets && h.assets.full_song ? '<button class="ghost" data-play="full" title="换声人声 + 伴奏">▶ 成品</button><button class="ghost" data-play="dry" title="只有换声后的人声，无伴奏">🎤 干声</button>' : ''}
          ${h.status === "done" && !isTrain ? '<button class="ghost" data-dl title="下载 WAV">⬇ 下载</button>' : ''}
          ${!isTrain && !isRvc && (h.lyrics || "").trim() ? '<button class="ghost" data-lyrics-txt title="下载纯文本歌词（不依赖音频/VAE）">⬇ 歌词 TXT</button>' : ''}
          ${(h.status === "error" || h.status === "cancelled") && !isTrain && !isRvc
            ? (h.isQueue
              ? '<button class="ghost" data-restart title="只把这一条排回队列；正在算的这首不受影响，跑完按顺序接上">↻ 重启</button>'
              : '<button class="ghost" data-regen title="按原参数重新提交；AR 检查点还在时会跳过 AR（不会从头重算）">↻ 重生成</button>')
            : ''}
          ${h.status !== "pending" ? '<button class="ghost" data-more>⋯ 更多</button>' : ''}
          <div class="h-more" data-more-menu>
            ${h.status === "running" && !isRvc && !isTrain ? '<button class="danger" data-stop>⏹ 终止</button>' : ''}
            ${h.status === "done" && !isTrain && !isRvc ? '<button class="ghost" data-rvc>🎙 送去换声</button>' : ''}
            ${!isTrain && !isRvc && (h.lyrics || "").trim() ? '<button class="ghost" data-lyrics>⬇ LRC歌词</button>' : ''}
            ${!isTrain && !isRvc && (h.lyrics || "").trim() ? '<button class="ghost" data-elrc>⬇ 逐字 eLRC</button>' : ''}
            ${!isTrain && !isRvc && (h.style || "").trim() ? '<button class="ghost" data-copy-style>📋 复制 Style</button>' : ''}
            ${!isTrain && !isRvc && (h.lyrics || "").trim() ? '<button class="ghost" data-copy-lyrics>📋 复制 歌词</button>' : ''}
            <button class="ghost" data-rename>✏ 任务改名</button>
            <button class="danger" data-del>🗑 删除</button>
          </div>
          ${h.status === "done" && isRvc && h.assets && Object.keys(h.assets).length ? `
          <div class="h-more" data-dl-menu>
            <button class="ghost" data-part-dl="full_song">🎵 成品（带伴奏）</button>
            <button class="ghost" data-part-dl="">🎙 换声人声（干声）</button>
            ${h.assets.vocals_original ? '<button class="ghost" data-part-dl="vocals_original">🎤 原人声</button>' : ''}
            ${h.assets.accompaniment ? `<button class="ghost" data-part-dl="accompaniment">🎹 伴奏${h.acc_pitch && h.acc_pitch.applied ? `（${h.acc_pitch.applied > 0 ? "+" : ""}${h.acc_pitch.applied}音）` : ""}</button>` : ''}
            ${h.assets.accompaniment_untuned ? '<button class="ghost" data-part-dl="accompaniment_untuned" title="分离出来的原调伴奏，没跟着变调；想自己配原调时用它">🎹 伴奏（原调）</button>' : ''}
          </div>` : ''}
        </div>`;
      // 这一页只负责"这一条记录"的事：播放、下载、改名、删除、拿歌词。
      // 以前点一行会顺手把 style/歌词/档位/种子等六个控件写进 ② 的创作表单（还不切页），
      // 那是最隐蔽的一种跨页代填——用户看不见自己面前的表单何时被人改过。已删除。
      // 要把参数带去创作页：用下面的「复制 Style / 复制 歌词」，粘到哪一页由你决定。
      const hasFullMix = isRvc && h.assets && h.assets.full_song;
      const playRow = async (mode) => {
        if (h.status !== "done") { toast("这条记录" + (h.status === "error" ? "生成失败" : "还没跑完") + "，没有音频可播", "err"); return; }
        const wantFull = hasFullMix && mode !== "dry";
        const rr = await fetch("/api/generate/audio/" + h.id);
        if (!rr.ok) { toast("音频文件缺失（可能被清理了），记录仍在", "err"); return; }
        if (wantFull) {
          const fr = await fetch("/api/rvc/audio/" + h.id + "?part=full_song");
          if (fr.ok) {
            loadAudio(await fr.blob(), h.id + "_full_song.wav", h.task_name + " · 成品（人声+伴奏）");
            return;
          }
          toast("成品文件缺失，改播干人声", "err");
        }
        loadAudio(await rr.blob(), h.id + ".wav",
                  h.task_name + (isRvc ? " · 干人声（无伴奏）" : ""));
      };
      d.onclick = () => playRow();
      // 错误详情：点击展开/收起完整错误信息（不触发行点击的回填）
      const errLine = d.querySelector("[data-err]");
      if (errLine) errLine.onclick = (e) => {
        e.stopPropagation();
        const full = String(h.error || "");
        if (errLine.dataset.open === "1") {
          errLine.dataset.open = "0";
          errLine.textContent = `⚠ ${full.slice(0, 120)}${full.length > 120 ? " …（点击查看全部）" : ""}`;
        } else {
          errLine.dataset.open = "1";
          errLine.textContent = "⚠ " + full;
        }
      };
      d.querySelectorAll("[data-play]").forEach((one) => {
        one.onclick = (e) => { e.stopPropagation(); playRow(one.dataset.play || undefined); };
      });
      // 进行中任务的终止/重启（仅队列条目有这两个按钮）
      const sb = d.querySelector("[data-stop]");
      if (sb) sb.onclick = async (e) => {
        e.stopPropagation();
        // 终止会丢掉已经算了一半的进度（这张卡跑一首要十几分钟），创作页那颗按钮有确认，
        // 历史页这颗没有——同一个动作两个入口不该两种手感
        if (!confirm("终止这个正在跑的任务？\n已经算到一半的进度不会保留，也不会产出文件。\n只停这一条：批量队列里未开始的任务照常继续。")) return;
        sb.disabled = true;
        try {
          const r = await fetch("/api/generate/stop?scope=current", { method: "POST" });
          if (!r.ok) throw new Error("终止失败");
          // 后端会回"还有 N 条待跑，会继续执行"——别让用户以为整条队列都没了
          const j = await r.json().catch(() => ({}));
          toast(j.message || "已请求终止，正在生成的任务将停止", "ok");
          setTimeout(renderHistory, 1200);
        } catch (err) { sb.disabled = false; toast(friendlyErr(err), "err"); }
      };
      const rsBtn = d.querySelector("[data-restart]");
      if (rsBtn) rsBtn.onclick = async (e) => {
        e.stopPropagation();
        rsBtn.disabled = true;
        try {
          // 不再"先终止再重排"：后端 batch_retry 在队列 running 时一律 409，
          // 那次盲发的 /api/generate/stop?scope=current 掐的是**此刻正在算的那一条**，
          // 未必是用户点的这一条，重排还照样失败（评审 B-4：破坏范围必须等于点击意图）。
          // 按钮因此只出现在 error/cancelled 的条目上；要重跑在跑的那条，先按「⏹ 终止」，
          // 它变成"已取消"后这个按钮自然出现。
          const r = await fetch("/api/batch/retry", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: h.id }) });
          const j = await r.json().catch(() => ({}));
          if (!r.ok || !j.ok) {
            throw new Error(r.status === 409
              ? (j.detail || "这条正在生成，等它跑完再重启")
              : (j.detail || j.message || "重启失败"));
          }
          toast(j.message || ("已重新排队：" + (h.task_name || h.id)), "ok");
          setTimeout(renderHistory, 800);
        } catch (err) { rsBtn.disabled = false; toast(friendlyErr(err), "err"); }
      };
      // 非队列条目（单首生成失败/取消）：按原参数重提，AR 检查点还在时会跳过 AR
      const rgBtn = d.querySelector("[data-regen]");
      if (rgBtn) rgBtn.onclick = async (e) => {
        e.stopPropagation();
        rgBtn.disabled = true;
        try {
          const r = await jfetch("/api/generate/resume/" + encodeURIComponent(h.id), { method: "POST" });
          const j = await r.json().catch(() => ({}));
          toast(j.message || "已按原参数重新提交", "ok");
          setTimeout(renderHistory, 800);
        } catch (err) { rgBtn.disabled = false; toast(friendlyErr(err), "err"); }
      };
      // 「⋯ 更多」/下载下拉：切换菜单显隐；点条目其他区域时收起
      const moreBtn = d.querySelector("[data-more]");
      const moreMenu = d.querySelector("[data-more-menu]");
      const dlMenu = d.querySelector("[data-dl-menu]");
      if (moreBtn) moreBtn.onclick = (e) => {
        e.stopPropagation();
        const open = !!(moreMenu && moreMenu.classList.contains("open"));
        // 同一时刻只允许一行展开：先把别的行的菜单收起来，避免互相遮挡
        closeHistoryMenus(moreMenu, dlMenu);
        // 「更多」与「下载产物」互斥：同时展开会两个面板叠在一起
        if (moreMenu) moreMenu.classList.toggle("open", !open);
        if (dlMenu) dlMenu.classList.toggle("open", open);
        // 靠近列表底部时向上弹，否则菜单会被视口/滚动容器裁掉
        if (moreMenu && moreMenu.classList.contains("open")) {
          const r = moreBtn.getBoundingClientRect();
          const spaceBelow = window.innerHeight - r.bottom;
          const need = Math.min(360, moreMenu.scrollHeight || 240);
          const flip = spaceBelow < need + 20;
          moreMenu.classList.toggle("up", flip);
          if (dlMenu) dlMenu.classList.toggle("up", flip);
        }
      };
      // 直接下载该条 WAV；换声任务默认下载完整歌曲（产物下拉可选其他部分）
      const dl = d.querySelector("[data-dl]");
      if (dl) dl.onclick = async (e) => {
        e.stopPropagation();
        const url = (isRvc && h.assets && Object.keys(h.assets).length)
          ? "/api/rvc/audio/" + h.id + "?part=full_song"
          : "/api/generate/audio/" + h.id;
        try {
          const rr = await fetch(url);
          if (!rr.ok) { toast("音频文件缺失", "err"); return; }
          const blob = await rr.blob();
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = dlName(h.task_name || (h.style || "").split(",")[0]);
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(a.href), 5000);
          toast("已开始下载", "ok");
        } catch (err) { toast("下载失败：" + friendlyErr(err), "err"); }
      };
      // 换声多产物下载（换声人声/原人声/伴奏/完整歌曲）
      d.querySelectorAll("[data-part-dl]").forEach(btn => {
        btn.onclick = async (e) => {
          e.stopPropagation();
          const part = btn.dataset.partDl || "";
          try {
            const rr = await fetch("/api/rvc/audio/" + h.id + (part ? "?part=" + part : ""));
            if (!rr.ok) {
              let msg = "产物不存在";
              try { msg = (await rr.json()).detail || msg; } catch {}
              toast(msg, "err"); return;
            }
            const blob = await rr.blob();
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            const partLabel = { "vocals_original": "原人声", "accompaniment": "伴奏", "accompaniment_untuned": "伴奏（原调）", "full_song": "成品（带伴奏）" }[part] || "换声人声（干声）";
            a.download = dlName((h.task_name || (h.style || "").split(",")[0]) + " " + partLabel);
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(() => URL.revokeObjectURL(a.href), 5000);
            toast("已开始下载", "ok");
          } catch (err) { toast("下载失败：" + friendlyErr(err), "err"); }
        };
      });
      const cs = d.querySelector("[data-copy-style]");
      if (cs) cs.onclick = (e) => { e.stopPropagation(); copyOut(h.style, "Style"); };
      const cl = d.querySelector("[data-copy-lyrics]");
      if (cl) cl.onclick = (e) => { e.stopPropagation(); copyOut(stripMachineOutro(h.lyrics), "歌词"); };
      // 下载歌词时间轨：已生成直接下载，否则触发后端对齐并轮询。
      // LRC（行级）与 eLRC（逐字卡拉OK）出自同一次对齐，只是取不同字段。
      async function downloadTrack(btn, fmt) {
        const oldTxt = btn.textContent;
        btn.disabled = true;
        try {
          let r = await fetch("/api/generate/lrc/" + h.id);
          let j = await r.json();
          if (!r.ok) { throw new Error(j.detail || "状态查询失败"); }
          if (j.status === "none") {
            btn.textContent = "对齐中…";
            const pr = await fetch("/api/generate/lrc/" + h.id, { method: "POST" });
            if (!pr.ok) { const pj = await pr.json().catch(() => ({})); throw new Error(pj.detail || "提交失败"); }
            j = { status: "running" };
          }
          if (j.status !== "done") {
            btn.textContent = "对齐中…";
            toast("正在对齐歌词时间轴，请稍候…", "ok");
            for (let i = 0; i < 120; i++) {
              await new Promise(res => setTimeout(res, 2000));
              const rr = await fetch("/api/generate/lrc/" + h.id);
              const jj = await rr.json();
              if (jj.status === "done") { j = jj; break; }
              if (jj.status === "error") throw new Error(jj.error || "对齐失败");
            }
            if (j.status !== "done") throw new Error("对齐超时，请稍后重试");
          }
          const text = fmt === "elrc" ? j.elrc : j.lrc;
          if (!text) {
            throw new Error(fmt === "elrc"
              ? "这份对齐结果没有逐字版本（当前语言/引擎只产出行级时间轨），可下载 LRC"
              : "没有可用的行级歌词文本");
          }
          const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = dlName(h.task_name || (h.style || "").split(",")[0]) + (fmt === "elrc" ? ".elrc" : ".lrc");
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(a.href), 5000);
          toast("已开始下载 " + (fmt === "elrc" ? "逐字 eLRC 歌词" : "LRC 歌词"), "ok");
        } catch (err) { toast((fmt === "elrc" ? "eLRC" : "LRC") + " 下载失败：" + friendlyErr(err), "err"); }
        finally { btn.disabled = false; btn.textContent = oldTxt; }
      }
      const tx = d.querySelector("[data-lyrics-txt]");
      if (tx) tx.onclick = (e) => {
        e.stopPropagation();
        const blob = new Blob([h.lyrics], { type: "text/plain;charset=utf-8" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = dlName(h.task_name || (h.style || "").split(",")[0] || h.id, "txt");
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 5000);
        toast("已开始下载歌词 TXT", "ok");
      };
      const lb = d.querySelector("[data-lyrics]");
      if (lb) lb.onclick = (e) => { e.stopPropagation(); downloadTrack(lb, "lrc"); };
      const eb = d.querySelector("[data-elrc]");
      if (eb) eb.onclick = (e) => { e.stopPropagation(); downloadTrack(eb, "elrc"); };
      // 「去使用」（训练完跳换声页并替它选中音色）已删除：那是在替别的页面做选择。
      // 训练好的音色本来就在 ⑨ 的音色下拉里，名字就写在训练卡片上。
      // 一键换声：只把「这条记录的 ID + 当场选的音色/变调」交给服务端，
      // 服务端直接从 output/ 取那份音频去转。不读 ⑨ 页的控件、不替 ⑨ 页写任务列表。
      const rv = d.querySelector("[data-rvc]");
      if (rv) rv.onclick = (e) => {
        e.stopPropagation();
        convertServerSong(h.id, h.task_name || (h.style || "").split(",")[0] || h.id,
                          d, () => renderHistory());
      };
      // 重命名任务：改 meta 的 task_name，历史展示与下载命名同步生效
      const rn = d.querySelector("[data-rename]");
      if (rn) rn.onclick = async (e) => {
        e.stopPropagation();
        const cur = h.task_name || (h.style || "").split(",")[0] || "";
        const name = prompt("修改任务名称（最多 100 字）：", cur);
        if (name === null) return;
        const trimmed = name.trim();
        if (!trimmed) { toast("任务名不能为空", "err"); return; }
        rn.disabled = true;
        try {
          const rr = await fetch("/api/generate/" + h.id + "/name", {
            method: "PATCH", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ task_name: trimmed.slice(0, 100) }),
          });
          const j = await rr.json().catch(() => ({}));
          if (!rr.ok) throw new Error(j.detail || "改名失败");
          h.task_name = j.task_name;
          const t = d.querySelector(".h-title");
          if (t) t.textContent = h.task_name + kindTag + st;
          toast("已重命名", "ok");
        } catch (err) { toast(friendlyErr(err), "err"); }
        finally { rn.disabled = false; }
      };
      d.querySelector("[data-del]").onclick = async (e) => {
        e.stopPropagation();
        const btn = e.currentTarget;
        // 这个删除走的是服务端 _output_purge：音频/乐谱/换声 stem 全在白名单里一起清掉，
        // 本机一首歌要跑十几分钟，误点一次没有后悔药。同页的音色删除、模型删除都有确认，
        // 唯独这条最常点的没有。
        const label = (h.task_name || (h.style || "").split(",")[0] || h.id);
        if (!confirm(`删除「${label}」？
音频与乐谱等产物文件会一起删除，本机不可恢复。
（进行中的任务删不了，服务端会返回 409 拦下）`)) return;
        btn.disabled = true;
        try {
          const rr = await fetch("/api/generate/" + h.id, { method: "DELETE" });
          const j = await rr.json().catch(() => ({}));
          if (!rr.ok) throw new Error(rr.status === 409 ? (j.detail || "任务进行中，不能删除") : (j.detail || "删除失败"));
          toast("已删除（含产物文件）", "ok");
          renderHistory();
        } catch (err) { toast(friendlyErr(err), "err"); btn.disabled = false; }
      };
      el.appendChild(d);
    });
    // 分页尾巴：一屏 60 行，剩下的点"加载更多"。1000 条全量渲染会把主线程按死。
    // ★ 它**不是一行数据**，不能穿 .hist-item 的衣服：
    //   穿了就会继承 .hist-item 的 padding/边框/列网格（还带 hover 与 pointer），
    //   于是"加载更多"这行看起来像第 61 条任务，实测行高 67px、与数据行的 72px 并存 ——
    //   一张表里出现第三种行高。改用 .hist-more 这个只属于"列表尾巴"的类，
    //   它不参与列网格，居中排布，视觉上是"表格之后的一个动作"。
    if (list.length > shown.length) {
      const more = document.createElement("div");
      more.className = "hist-more";
      const b = document.createElement("button");
      b.className = "ghost"; b.type = "button";
      b.textContent = "↓ 加载更多（已显 " + shown.length + " / " + list.length + "）";
      b.onclick = () => { histPage += 120; renderHistory(); };
      const tip = document.createElement("span");
      tip.className = "hint-xs"; tip.textContent = "再往下翻可用上方搜索框直接定位";
      more.appendChild(b); more.appendChild(tip);
      el.appendChild(more);
    }
  } catch { $("histList").innerHTML = '<div class="empty">后端未连接。</div>'; }
}
$("histRefresh").onclick = () => { renderHistory(); renderRvcModels(); toast("已刷新", "ok"); };

// ⋯ 菜单的唯一收起入口：点页面空白处收起；开另一行时先收起旧菜单。
// 以前没有这个全局监听，菜单只能再点一次「更多」才关，还会和下一行的菜单叠在一起。
function closeHistoryMenus(exceptMore, exceptDl) {
  document.querySelectorAll("#histList [data-more-menu].open, #histList [data-dl-menu].open").forEach((m) => {
    if (m !== exceptMore && m !== exceptDl) m.classList.remove("open");
  });
}
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-more], [data-more-menu], [data-dl-menu]")) return;
  closeHistoryMenus();
});
// 点了菜单里的动作后也收起，不让它继续挂在页面上挡下一行
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-more-menu] button, [data-dl-menu] button")) closeHistoryMenus();
});
// 一键继续：把意外终止/停止/失败的队列任务重新入队执行
$("batchResume").onclick = async () => {
  const btn = $("batchResume");
  btn.disabled = true;
  try {
    const j = await (await jfetch("/api/batch/resume", { method: "POST" })).json();
    toast(j.message || "已恢复队列", "ok");
    renderHistory();
  } catch (e) { toast(friendlyErr(e), "err"); }
  finally { btn.disabled = false; }
};
// 停止队列：正在算的这条会跑完，未开始的置为已取消（之后「一键继续」可全部复位）
$("batchStop").onclick = async () => {
  if (!confirm("停止批量队列？\n正在算的这一条会跑完；未开始的条目会标为已取消，之后点「▶ 一键继续」可全部复位重跑。\n只想掐掉正在算的这一条，请用「终止任务」。")) return;
  const btn = $("batchStop");
  btn.disabled = true;
  try {
    const j = await (await jfetch("/api/batch/stop", { method: "POST" })).json();
    toast(j.message || "队列已停止", "ok");
    renderHistory();
  } catch (e) { toast(friendlyErr(e), "err"); }
  finally { btn.disabled = false; }
};
$("histClear").onclick = async () => {
  if (!confirm("清空任务记录？\n已完成/失败的记录会连同音频、乐谱一起删除，本机不可恢复。\n进行中与排队中的任务会跳过，不受影响。")) return;
  const btn = $("histClear");
  const old = btn.textContent;
  btn.disabled = true;
  try {
    const j = await (await fetch("/api/generate/list")).json();
    const targets = (j.items || []).filter(h => h.status !== "running" && h.status !== "pending");
    if (!targets.length) { toast("没有可清理的记录", "ok"); return; }
    let done = 0, fail = 0;
    for (const h of targets) {
      btn.textContent = `清理中 ${done + fail + 1}/${targets.length}`;
      // 一次网络异常不能拖垮整批：以前循环里没有 try，中途断了只显示"没清干净"，
      // 用户既不知道停在哪一条，也不知道有多少已经删了
      try {
        const rr = await fetch("/api/generate/" + h.id, { method: "DELETE" });
        if (rr.ok) done++; else fail++;
      } catch { fail++; }
    }
    renderHistory();
    toast(`已清理 ${done} 条` + (fail ? `，${fail} 条失败（可逐条重试）` : ""), fail ? "err" : "ok");
  } catch (e) {
    toast("清空失败：网关没有返回记录列表", "err");
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
};

// ---------- rvc（换声）----------
// rid → setInterval 句柄。必须用 Map：这里按 rid 取/存句柄，Set 上没有 get/set，
// 之前用 Set 会让每次「提交成功」都抛 rvcPolls.set is not a function 误报成提交失败，
// 并且定时器每周期翻倍、完成时的 clearInterval 也抛（被 catch 吞掉）→ 界面永远显示转换中。
const rvcPolls = new Map();
const rvcMiss = new Map();   // rid → 连续查不到状态的次数

function stopRvcPoll(rid) {
  const t = rvcPolls.get(rid);
  if (t) clearInterval(t);
  rvcPolls.delete(rid);
  rvcMiss.delete(rid);
}

async function renderRvc() {
  try {
    const mr = await fetch("/api/rvc/models");
    if (mr.ok) {
      const { models, items } = await mr.json();
      const byName = {}; (items || []).forEach((it) => { byName[it.name] = it; });
      const sel = $("rvcModel");
      const cur = sel.value;
      // 下拉里就带上音域：不用先跳去 ⑧ 查"这个嗓子能唱多高"再回来填变调
      sel.innerHTML = (models || []).map((m) => {
        const it = byName[m] || {};
        const tag = it.range_summary || (it.f0_range ? `中位 ${it.f0_range.median_hz}Hz` : "无音域");
        return `<option value="${escapeHtml(m)}">${escapeHtml(m.replace(/\.pth$/i, ""))} · ${escapeHtml(tag)}</option>`;
      }).join("");
      if (cur && models.includes(cur)) sel.value = cur;
      else if (models && models.length) sel.value = models[0];
      // 音色定了就把推荐参数拉下来并自动套用：用户不该面对一堆空输入框。
      // 启动、切页、刷新都可能走到这里，因此静默准备默认值，不弹"已填入"。
      loadRvcReco(true, false);
    }
  } catch {}
}
// 本会话内跟踪的换声任务（服务重启后仅丢失进行中的状态，落盘结果仍在历史页）
const rvcJobs = [];

function renderRvcList() {
  const el = $("rvcList");
  if (!rvcJobs.length) { el.innerHTML = '<div class="empty">暂无换声任务。结果也会出现在 ⑪ 任务管理（标注 🎙 换声）。</div>'; return; }
  el.innerHTML = "";
  // 列表顺序 = 执行顺序：正在转换的排第一，其后按排队位次，已结束的最后（新→旧）。
  // 直接遍历 rvcJobs 会让"刚提交的"永远显示在最上面，看着像插队。
  const _rk = (j) => j.status === "running" ? 0 : j.status === "pending" ? 1 : 2;
  const shown = rvcJobs.slice().sort((a, b) =>
    _rk(a) - _rk(b)
    || (Number(a.queue_pos || 1) - Number(b.queue_pos || 1))
    || String(b.ts || "").localeCompare(String(a.ts || "")));
  shown.forEach(j => {
    const d = document.createElement("div");
    d.className = "hist-item";
    const pos = Number(j.queue_pos || 0);
    const st = j.status === "done" ? "✓ 完成" : j.status === "error" ? "✕ 失败"
      : j.status === "cancelled" ? "⊘ 已取消（未开始转换）"
      : j.status === "lost" ? "⚠ 状态丢失（服务重启过，结果见历史页）"
      : j.status === "pending" ? `⏳ 排队中（第 ${pos || 1} 位）` : "🎵 转换中";
    const rvcTitle = j.task_name || (j.model || "").replace(/\.pth$/i, "");
    // 换声结果有两份：合成好的成品（人声+伴奏）与干人声。以前只给一个"播放"
    // 按钮且固定播干声，用户听到的是半成品——这里两份都给，各自点名放的是哪个。
    const hasFull = !!(j.assets && j.assets.full_song);
    const gateTxt = j.gate && j.gate.gate_closed_ratio != null
      ? ` · 🔇 静音门已压 ${Math.round(j.gate.gate_closed_ratio * 100)}% 无人声段` : "";
    // 听感体检：以前这个数只落进 output/*.json，页面上一个字都不显示，
    // 于是"电音是谁的账"只能靠猜。tip 第一句就是结论，整段进悬停。
    const q = j.quality || {};
    const qTip = (q.tips || [])[0] || "";
    const qBad = !!(q.roughness && q.roughness.worse);
    d.innerHTML = `
      <div class="h-body">
        <div class="h-title">${escapeHtml(rvcTitle)} · ${st}${j.sec ? " · " + j.sec + "s" : ""}</div>
        <div class="h-sub truncate">${escapeHtml((j.model || "").replace(/\.pth$/i, ""))} · ${escapeHtml(j.src_name || j.srcName || "")}${escapeHtml(gateTxt)}</div>
        <div class="h-sub">${idTag(j.id)}</div>
        ${j.status === "done" && j.src_f0 && !j.src_f0.error ? `<div class="h-sub" title="分离后真实人声实测（比换声前整曲探针可信）${j.pitch_advice? ' · 建议变调 '+(j.pitch_advice.suggested_pitch>0?'+':'')+j.pitch_advice.suggested_pitch+' 半音':''}">🎼 源音域 ≈ 中位 ${j.src_f0.median_hz}Hz</div>` : ""}
        ${j.status === "done" && j.pitch_note ? `<div class="h-sub" title="${escapeHtml(j.pitch_note)}">🎼 ${escapeHtml(j.pitch_note)}</div>` : ""}
        ${j.status === "done" && j.acc_pitch && j.acc_pitch.note ? `<div class="h-sub" title="人声变的是音区（含整八度），伴奏只跟非八度那部分——整八度不换调，挪伴奏只会把底鼓贝斯推坏">${escapeHtml(j.acc_pitch.applied ? "🎹 " : "⚠️ ")}${escapeHtml(j.acc_pitch.note)}</div>` : ""}
        ${j.status === "done" && qTip ? `<div class="h-sub" style="${qBad ? "color:var(--err,#c0392b);" : ""}" title="${escapeHtml((q.tips || []).join("\n"))}">🩺 ${escapeHtml(qTip)}</div>` : ""}
        ${j.status === "error" ? `<div class="h-sub truncate" title="${escapeHtml(j.error || "")}">${escapeHtml((j.error || "").slice(0, 80))}</div>` : ""}
        ${j.status === "pending" ? `<div class="h-sub">${j.step && j.step !== "排队中" ? escapeHtml(j.step) + "，等到空闲就自动开跑" : "换声一次只跑一个（分离和推理都吃满显存），排队是正常状态"}</div>` : ""}
      </div>
      ${j.status === "done" ? (hasFull
        ? '<button class="ghost" data-play="full_song">▶ 播放成品（带伴奏）</button><button class="ghost" data-play="">🎤 干人声</button>'
        : '<button class="ghost" data-play="">▶ 播放干人声</button>') : ''}
      ${j.status === "pending" ? '<button class="ghost" data-cancel>✕ 取消排队</button>' : ''}`;
    const cb = d.querySelector("[data-cancel]");
    if (cb) cb.onclick = async (e) => {
      e.stopPropagation();
      const rr = await fetch("/api/rvc/cancel/" + j.id, { method: "POST" });
      if (rr.ok) { toast("已取消排队的换声任务", "ok"); pollRvc(j.id); }
      else {
        const ej = await rr.json().catch(() => ({}));
        toast(ej.detail || "取消失败（任务可能已经开跑）", "err");
        pollRvc(j.id);   // 状态已被服务端改判，拉一次真实状态回来
      }
    };
    d.querySelectorAll("[data-play]").forEach(pb => {
      pb.onclick = async (e) => {
        e.stopPropagation();
        const part = pb.dataset.play;
        const rr = await fetch("/api/rvc/audio/" + j.id + (part ? "?part=" + part : ""));
        if (rr.ok) {
          loadAudio(await rr.blob(), j.id + (part ? "_full_song.wav" : ".wav"),
                    (j.task_name || "换声结果") + (part ? " · 成品（人声+伴奏）" : " · 干人声"));
          toast(part ? "正在播放合成成品（换声人声+伴奏）" : "正在播放干人声（无伴奏）", "ok");
        } else toast(part ? "成品文件缺失，可改播干人声" : "结果文件缺失", "err");
      };
    });
    el.appendChild(d);
  });
}

// 页面加载/iframe 重载后恢复进行中的换声任务（进度条 + 轮询）
async function restoreRvcActive() {
  try {
    const r = await fetch("/api/rvc/active");
    if (!r.ok) return;
    const d = await r.json();
    (d.items || []).forEach(j => {
      if (rvcJobs.some(x => x.id === j.id)) return;
      rvcJobs.unshift(j);
      pollRvc(j.id);
    });
    renderRvcList();
  } catch {}
}

async function pollRvc(rid) {
  try {
    const r = await fetch("/api/rvc/status/" + rid);
    if (r.ok) {
      rvcMiss.delete(rid);
      const j = await r.json();
      const i = rvcJobs.findIndex(x => x.id === rid);
      if (i >= 0) rvcJobs[i] = { ...rvcJobs[i], ...j };
      renderRvcList();
      if (j.status === "done") {
        stopRvcPoll(rid);
        $("rvcProgress").classList.remove("show");
        $("rvcProgressBar").style.width = "100%";
        $("rvcStatus").className = "status ok";
        $("rvcStatus").textContent = `换声完成（${j.sec}s），已保存到 output/，历史页同步可见。`;
        toast("换声完成", "ok");
        renderHistory();
        return;
      }
      if (j.status === "error") {
        stopRvcPoll(rid);
        $("rvcProgress").classList.remove("show");
        $("rvcStatus").className = "status err";
        $("rvcStatus").textContent = "换声失败：" + (j.error || "未知错误").slice(0, 160);
        return;
      }
      if (j.status === "cancelled") {
        stopRvcPoll(rid);
        $("rvcProgress").classList.remove("show");
        $("rvcStatus").className = "status";
        $("rvcStatus").textContent = "该换声任务已在排队阶段取消。";
        renderHistory();
        return;
      }
      if (j.status === "pending") {
        // 排队中的条目一行算力都没花，画进度条就是撒谎——只报位次，然后继续轮询
        $("rvcProgress").classList.remove("show");
        const ahead = Math.max(0, Number(j.queue_pos || 1) - 1);
        $("rvcStatus").className = "status";
        $("rvcStatus").textContent = ahead
          ? `已排队：前面还有 ${ahead} 个换声任务，串行执行，轮到会自动开始（可离开页面）`
          : (j.step && j.step !== "排队中"
            ? `已轮到本任务，${j.step}（可离开页面）`
            : "已轮到本任务，正在启动…（可离开页面）");
        if (!rvcPolls.has(rid)) rvcPolls.set(rid, setInterval(() => pollRvc(rid), 5000));
        return;
      }
      // 耗时两套口径：
      //  · 只换声（干声输入）：本机 3 次实测（29.4s 源音频首跑 43.7s、缓存后 18.9~20.2s）
      //    → 15s 模型加载 + 1×时长，可以给区间。
      //  · 带人声分离：最早那次"828s ≈ 3.75×"的样本被三任务并发抢 GPU 污染（正是 v1.1.3
      //    串行队列要修的根因），系数虚高 3.8 倍（评审 v1.2.0 G1）。同一首 221.1s 的歌
      //    干净串行两次实测：222.0s / 226.4s ≈ 1.0×时长——与干声口径基本重合。
      const dur = j.src_duration || (rvcJobs.find(x => x.id === rid) || {}).src_duration || 30;
      const sep = !!(j.separate_vocal || j.sep_stage);
      const total = Math.max(20, sep ? 20 + 1.0 * dur : 15 + 1.0 * dur);
      const t0 = new Date(j.started_ts || j.ts).getTime() || Date.now();   // 按真正开跑计时
      const elapsed = (Date.now() - t0) / 1000;
      const ratio = Math.min(1, elapsed / total);
      $("rvcProgress").classList.add("show");
      $("rvcProgressBar").style.width = Math.min(sep ? 90 : 97, 5 + (sep ? 85 : 92) * ratio).toFixed(1) + "%";
      const fmtR = (t) => t > 90 ? Math.round(t / 60) + " 分钟" : Math.round(t) + " 秒";
      let etaStrR;
      if (sep) {
        etaStrR = elapsed < total ? `粗估还需 ${fmtR(total - elapsed)}（含分离按两次干净串行实测 ≈1×时长）`
                                  : "已超出粗估，仍在正常转换";
      } else {
        const etaLo = Math.max(0, 0.65 * dur + 10 - elapsed);   // 缓存后快路径
        const etaHi = Math.max(0, total - elapsed);             // 首跑慢路径
        etaStrR = etaHi <= 0 ? "" : etaLo > 0 ? fmtR(etaLo) + "~" + fmtR(etaHi) : "不超过 " + fmtR(etaHi);
        if (etaStrR) etaStrR = "预计还需 " + etaStrR;
      }
      $("rvcStatus").textContent = `转换中（${j.model || ""}）${sep ? " · 含人声分离" : ""}… 已 ${Math.floor(elapsed/60)}分${Math.round(elapsed%60)}秒` + (etaStrR ? ` · ${etaStrR}` : "") + "（可离开页面）";
    } else if (r.status === 404) {
      // 服务端重启过、任务记录已丢：轮下去永远不会有心，连 3 次就停表并如实标状态丢失
      const n = (rvcMiss.get(rid) || 0) + 1;
      rvcMiss.set(rid, n);
      const i = rvcJobs.findIndex(x => x.id === rid);
      if (n >= 3) {
        if (i >= 0) { rvcJobs[i] = { ...rvcJobs[i], status: "lost" }; renderRvcList(); }
        stopRvcPoll(rid);
        $("rvcProgress").classList.remove("show");
        return;
      }
    }
  } catch {}
  if (!rvcPolls.has(rid)) {
    rvcPolls.set(rid, setInterval(() => pollRvc(rid), 5000));
  }
}

// 快捷按钮改完值要补发 change：面板上「当前是否偏离推荐值」的状态是靠 change 事件刷新的，
// 直接赋 .value 不触发事件，就会出现"旋钮已经改到 0.3，状态栏还挂着 ✓ 已是推荐值"的假绿灯。
function rvcSetKnob(id, v) {
  const el = $(id);
  if (!el) return;
  el.value = v;
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

// 变调快捷按钮：一键填入推荐值
$$("[data-pitch]").forEach(b => {
  b.onclick = () => rvcSetKnob("rvcPitch", b.dataset.pitch);
});
// 音域分析（评审 C4）：源唱 f0 中枢 vs 目标音色舒适音域 → 一句人话建议 + 一键应用
$("rvcAnalyze").onclick = async () => {
  const f = $("rvcFile").files[0];
  if (!f) { toast("先在上方选择音频文件", "err"); return; }
  const box = $("rvcAdvice"), btn = $("rvcAnalyze"), apply = $("rvcApplyAdvice");
  const doSep = $("rvcAnalyzeSep").checked;
  apply.style.display = "none";
  btn.disabled = true;
  btn.textContent = doSep ? "分离 + 分析中…（约 1~2 分钟）" : "分析中…";
  box.textContent = "";
  try {
    const fd = new FormData();
    fd.append("file", f);
    fd.append("model", $("rvcModel").value);
    fd.append("separate", doSep ? "1" : "0");
    const r = await fetch("/api/rvc/pitch/advice", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) { box.textContent = "分析失败：" + (j.detail || r.status); return; }
    box.textContent = j.text + "　" + (j.caveat || "");
    if (j.suspect) {
      // 源音域测错了（整曲带伴奏的典型症状）→ 一个字都不套用。
      // 上次就是这里把被伴奏污染的 124Hz 算成 +16、钳成 +2 后自动填进变调框的。
      box.innerHTML += `<br><span style="color:var(--err);">${escapeHtml(j.suspect.hint || "")}</span>`;
      rvcSrcF0 = null;
      if (!doSep) $("rvcAnalyzeSep").checked = true;   // 直接帮他勾上，下一步就是重测
      loadRvcReco(false);
      toast("源唱音域没测准，已改为「先分离」——再点一次分析", "err");
      return;
    }
    if (j.suggestion && j.suggestion.apply) {
      const p = j.suggestion.suggested_pitch;
      apply.textContent = `应用 ${p > 0 ? "+" : ""}${p}（当前 ${$("rvcPitch").value}）`;
      apply.style.display = "";
      apply.onclick = () => {
        $("rvcPitch").value = p;
        apply.style.display = "none";
        toast("变调已设为 " + p, "ok");
      };
      // 量出源唱音域就记住：推荐档据此把变调也算出来，下次同曲不用再量一遍
      rvcSrcF0 = j.source || null;
      loadRvcReco(!rvcTouched);
    }
  } catch (e) {
    box.textContent = "分析请求失败：" + e;
  } finally {
    btn.disabled = false; btn.textContent = "分析音域 → 变调建议";
  }
};
// ===== 推荐参数：页面三个入口（换声页默认值 / 一键套用 / 历史页送去换声）共用同一份 =====
// 以前推荐值只写在文档和对话里，用户看到的是一堆空输入框，根本不知道该填多少。
// 现在由 /api/rvc/models/{name}/recommend 下发，选音色即自动套用。
// var 而非 let：renderRvc() 可能在切页初始化时先于本行执行，let 的 TDZ 会直接抛
// ReferenceError 把整段脚本打断；var 提升到脚本顶部，最多是 undefined，不会炸。
var rvcReco = null;        // 当前音色的推荐档（含 why）
var rvcSrcF0 = null;       // 源唱音域（点过「分析音域」才有，用来算变调）
var rvcTouched = false;    // 用户手动改过旋钮后，不再自动覆盖他的选择

function rvcSetVal(id, v) { const el = $(id); if (el && v !== undefined && v !== null) el.value = v; }

function rvcRecoMatches() {
  if (!rvcReco) return false;
  const r = rvcReco.recommend || {};
  const eq = (id, v) => String(($(id) || {}).value) === String(v);
  let ok = eq("rvcF0", r.f0_method) && eq("rvcIndexRate", r.index_rate)
        && eq("rvcProtect", r.protect) && eq("rvcRms", r.rms_mix_rate)
        && eq("rvcFilter", r.filter_radius) && eq("rvcResample", r.resample_sr);
  if (rvcReco.pitch !== null) ok = ok && eq("rvcPitch", rvcReco.pitch);
  return ok;
}

// 变调越界检查：区间外的变调会把大段音高推到该音色没学过的高度，
// 听感就是"一片一片的失真"——蛋卷×《等你回来》用 +2（区间 -5~-1）时 28% 的音高越界。
function rvcPitchOutOfRange() {
  const sr = rvcReco && rvcReco.safe_range;
  // 区间两端打架（源音域比音色还宽）时不 feasible —— 此时 min>max，
  // 拿它判断会让任何值都"越界"，连推荐值自己都被判越界（上次显示成"+17 ~ +0"，
  // 推荐 +2 却说 +2 超区间，自相矛盾）。无解就明说无解，不做判断。
  if (!sr || !sr.feasible || rvcReco.pitch === null) return 0;
  const p = Number($("rvcPitch").value) || 0;
  if (p > sr.max) return p - sr.max;
  if (p < sr.min) return sr.min - p;
  return 0;
}

function renderRvcPitchWarn() {
  const el = $("rvcPitchWarn");
  if (!el) return;
  const over = rvcPitchOutOfRange();
  if (!over) { el.style.display = "none"; el.textContent = ""; return; }
  const sr = rvcReco.safe_range, p = Number($("rvcPitch").value) || 0;
  el.style.display = "";
  el.innerHTML = `⚠️ 变调 <b>${p > 0 ? "+" : ""}${p}</b> 超出这个音色的安全区间 `
    + `<b>${sr.min > 0 ? "+" : ""}${sr.min} ~ ${sr.max > 0 ? "+" : ""}${sr.max}</b>（超了 ${over} 个半音）。`
    + `区间外会有大段音高落在该音色练过的范围之外——高音发劈、失真、金属声基本都出自这里。`
    + `推荐 ${rvcReco.pitch > 0 ? "+" : ""}${rvcReco.pitch}。`;
}

function renderRvcReco() {
  const list = $("rvcRecoList"), st = $("rvcRecoState");
  renderRvcPitchWarn();
  if (!rvcReco) { st.textContent = "暂无推荐数据"; list.innerHTML = ""; return; }
  const r = rvcReco.recommend || {}, w = rvcReco.why || {};
  const same = rvcRecoMatches();
  st.innerHTML = same
    ? '<span style="color:var(--ok);">✓ 当前已是推荐值</span>'
    : '<span style="color:var(--warn);">· 当前与推荐值不一致，点「一键套用」恢复</span>';
  if (rvcReco.pitch === null) st.innerHTML += "（变调：点上方「分析音域」自动算出）";
  const rows = [
    ["变调", rvcReco.pitch === null ? "先分析音域" : rvcReco.pitch, rvcReco.pitch_why || ""],
    ["变调算法", r.f0_method, w.f0_method],
    ["音色检索强度", r.index_rate, w.index_rate],
    ["protect", r.protect, w.protect],
    ["音量跟随原唱", r.rms_mix_rate, w.rms_mix_rate],
    ["音高平滑半径", r.filter_radius, w.filter_radius],
    ["输出采样率", r.resample_sr === 0 ? "模型原生" : r.resample_sr, w.resample_sr],
  ];
  list.innerHTML = rows.map(([k, v, why]) =>
    `<div><b>${escapeHtml(k)}</b>：<code>${escapeHtml(String(v))}</code>` +
    (why ? `<span style="color:var(--muted)"> — ${escapeHtml(why)}</span>` : "") + `</div>`
  ).join("")
    + (rvcReco.safe_range
        ? (rvcReco.safe_range.feasible
            ? `<div style="margin-top:4px;"><b>安全区间</b>：<code>${rvcReco.safe_range.min > 0 ? "+" : ""}${rvcReco.safe_range.min} ~ ${rvcReco.safe_range.max > 0 ? "+" : ""}${rvcReco.safe_range.max}</code>`
              + `<span style="color:var(--muted)"> — ${escapeHtml(rvcReco.safe_range.basis || "")}</span></div>`
            : `<div style="margin-top:4px;"><b>安全区间</b>：<span style="color:var(--muted)">`
              + `源唱音域与该音色练过的范围不重叠，给不出区间（怎么调都会有音高落在覆盖外）</span></div>`)
        : "")
    + (rvcReco.suspect ? `<div style="margin-top:4px; color:var(--err);">⚠️ ${escapeHtml(rvcReco.suspect.hint || "")}</div>` : "")
    + (rvcReco.warn ? `<div style="margin-top:4px;">⚠️ ${escapeHtml(rvcReco.warn)}</div>` : "")
    + `<div style="margin-top:4px; color:var(--muted)">这些数字来自该音色的训练实测音域与官方默认口径；`
    + `就算一个都不改，服务端用的也是同一套默认值。</div>`;
}

function applyRvcReco(silent) {
  if (!rvcReco) return;
  const r = rvcReco.recommend || {};
  if (rvcReco.pitch !== null) rvcSetVal("rvcPitch", rvcReco.pitch);
  rvcSetVal("rvcF0", r.f0_method);
  rvcSetVal("rvcIndexRate", r.index_rate);
  rvcSetVal("rvcProtect", r.protect);
  rvcSetVal("rvcRms", r.rms_mix_rate);
  rvcSetVal("rvcFilter", r.filter_radius);
  rvcSetVal("rvcResample", r.resample_sr);
  renderRvcReco();
  if (!silent) toast("已套用推荐参数", "ok");
}

async function loadRvcReco(auto, announce) {
  const m = $("rvcModel").value;
  if (!m) return;
  const q = new URLSearchParams();
  if (rvcSrcF0) {
    q.set("src_median_hz", rvcSrcF0.median_hz);
    if (rvcSrcF0.p95_hz) q.set("src_p95_hz", rvcSrcF0.p95_hz);
    if (rvcSrcF0.p5_hz) q.set("src_p5_hz", rvcSrcF0.p5_hz);
  }
  try {
    const res = await fetch(`/api/rvc/models/${encodeURIComponent(m)}/recommend?${q}`);
    if (!res.ok) return;
    rvcReco = await res.json();
    renderRvcReco();
    // 只在用户没手动动过旋钮时才自动套用——不抢他的手感。
    // 启动/切页/刷新静默填默认值；只有用户在⑨主动换音色或点一键套用才给可见反馈。
    if (auto && !rvcTouched) {
      applyRvcReco(true);
      if (announce) toast("已按「" + m.replace(/\.pth$/i, "") + "」的推荐参数填入旋钮", "ok");
    }
  } catch (e) { /* 推荐读不到不影响换声本身，静默 */ }
}

// 选好文件就把"变调怎么定"这条路径点亮：变调是唯一必须知道源唱音高才能定的参数，
// 不提醒的话用户只会默认填 0，然后抱怨怎么换完发闷/尖细。
$("rvcFile").addEventListener("change", () => {
  if (rvcSrcF0) return;
  const box = $("rvcAdvice");
  if (box) box.textContent = "👉 已选好音频：点下面「分析音域 → 变调建议」，变调会自动算出来并填进输入框（CPU 几秒，不进换声队列）。不分析也能跑，但变调要靠自己听。";
});
$("rvcApplyReco").onclick = () => applyRvcReco(false);
// 换音色 = 换推荐值：源唱音域要重新量，用户改动标记也一并复位
$("rvcModel").onchange = () => { rvcSrcF0 = null; rvcTouched = false; loadRvcReco(true, true); };
// 用户一旦手动调了任何一个旋钮，就不再自动覆盖（但仍刷新"是否偏离推荐值"的状态提示）
["rvcPitch", "rvcF0", "rvcIndexRate", "rvcProtect", "rvcRms", "rvcFilter", "rvcResample"]
  .forEach(id => { const el = $(id); if (el) el.addEventListener("change", () => { rvcTouched = true; renderRvcReco(); }); });

// 效果预设：一键设置检索强度 + protect
$$("[data-preset]").forEach(b => {
  b.onclick = () => {
    const [ir, pr] = b.dataset.preset.split(",");
    rvcTouched = true;   // 选了预设就是明确表态，别再被推荐值覆盖
    rvcSetKnob("rvcIndexRate", ir);
    rvcSetKnob("rvcProtect", pr);
  };
});

function rvcParamValues(pitch) {
  // 换声页这套滑杆只在这里读一次，也只有这一处读——它就是 ⑨ 页自己的输入。
  // 以前历史页/成片出口的「送去换声」也来读这十个控件，于是同一个滑杆在两个入口含义不同，
  // 而且 ⑨ 页的界面会被别的页面当成隐式参数源。现在那两个入口改走服务端为该音色推荐的官方档
  // （见 convertServerSong），要自己调档位就到 ⑨ 页来。
  const v = {
    pitch: pitch === undefined ? (Number($("rvcPitch").value) || 0) : pitch,
    f0_method: $("rvcF0").value,
    index_rate: Number($("rvcIndexRate").value || "0.5"),
    protect: Number($("rvcProtect").value || "0.33"),
    rms_mix_rate: Number($("rvcRms").value || "0.25"),
    filter_radius: Number.isFinite(Number($("rvcFilter").value)) ? Number($("rvcFilter").value) : 3,
    resample_sr: Number($("rvcResample").value || "0"),
    separate_vocal: $("rvcSep").checked ? "auto" : "off",
    strip_harmony: $("rvcHarmony").checked ? "on" : "off",
    gate: $("rvcGate").checked ? "on" : "off",
  };
  return v;
}

// 给服务器上已有的一首成品换声：⑨ 之外的入口（⑪ 任务管理、② 成片出口）都走这里。
// 只问用户两件事——用哪个音色、变调几个半音；其余档位一律用服务端为该音色推荐的官方档，
// 因为那两个入口没有滑杆，读 ⑨ 页的滑杆等于让一个页面替另一个页面做决定。
// 要自己调检索强度/protect/去和声/静音门，请到 ⑨ 歌曲换声页提交。
async function convertServerSong(rid, title, anchor, onDone) {
  let items = [];
  try {
    const j = await (await fetchT("/api/rvc/models", {}, 25000)).json();
    items = j.items || [];
  } catch (e) { toast("读音色列表失败：" + friendlyErr(e), "err"); return; }
  if (!items.length) { toast("没有可用音色：先到 ⑦ 音色制作训练，或在 ⑧ 音色库导入", "err"); return; }
  inlineForm(anchor, {
    anchor: anchor && anchor.parentElement ? anchor.parentElement : anchor,
    title: "🎤 送去换声：" + String(title || rid).slice(0, 26),
    okText: "开始换声", busyText: "提交中…",
    hint: "只定音色与变调，其余用服务端为该音色推荐的官方档；要细调档位请到 ⑨ 歌曲换声页。" +
          "变调半音：男转女 +12 / 女转男 −12 / 不变 0。",
    fields: [
      { id: "model", label: "音色", kind: "select", value: items[0].name,
        options: items.map(x => ({ v: x.name, t: (x.name || "").replace(/\.pth$/i, "")
          + (x.range_summary ? " · " + x.range_summary : "") })) },
      { id: "pitch", label: "变调（半音）", kind: "number", value: 0, min: -36, max: 36, maxlen: 4 },
    ],
    validate: (v) => {
      // Number("") 是 0、Number("12半音") 是 NaN：任何一种都会把变调静默吞掉，
      // 成品比预期低一个八度还像"按我填的做的"。这里宁可不提交。
      if (!/^[+-]?\d{1,3}$/.test(v.pitch)) return { msg: "变调要填整数半音（例如 12、-12、0），没有提交", focus: "pitch" };
      const p = Number(v.pitch);
      if (p < -36 || p > 36) return { msg: "变调超出 ±36 半音（三个八度），确认没填错", focus: "pitch" };
      if (!items.some(x => x.name === v.model)) return { msg: "音色列表已变，请重新展开选择", focus: "model" };
      return null;
    },
  }, async (v) => {
    const it = items.find(x => x.name === v.model) || {};
    const rec = it.recommend || {};
    const model = v.model, pitch = Number(v.pitch);
    const body = { model, pitch,
      task_name: (String(title || "") + "·" + model.replace(/\.pth$/i, "")).slice(0, 100) };
    // 官方推荐档由服务端给：前端不再抄一份默认值，免得两边的数分叉后
    // 页面显示的和真正跑的不是同一套
    ["f0_method", "index_rate", "protect", "rms_mix_rate", "filter_radius", "resample_sr"]
      .forEach(k => { if (rec[k] !== undefined && rec[k] !== null) body[k] = rec[k]; });
    const r = await fetchT(`/api/rvc/convert/${encodeURIComponent(rid)}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }, 60000);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || "提交失败");
    const ahead = Math.max(0, Number(j.position || 1) - 1);
    toast(ahead ? `已排队（第 ${j.position} 位，前面还有 ${ahead} 个）——进度在本页列表里看`
                : "已送入换声（" + model.replace(/\.pth$/i, "") + "）——进度在本页列表里看", "ok");
    if (typeof onDone === "function") onDone();
  });
}

// ---------- 服务器素材下拉 ----------
// 每个页面自己到服务器的库里取输入：⑨ 的音源、⑦ 的训练素材都从这里挑。
// 提交时只传标识（<rid> 或 <rid>|<part>），那份文件由服务端自己去取——
// 既不再"把 A 页的产物塞进 B 页的文件框"，浏览器也不用把几十 MB 音频下载再上传绕一圈。
async function serverAudioOptions() {
  const out = [];
  try {
    const j = await (await fetchT("/api/audio/separate/list", {}, 25000)).json();
    (j.items || []).filter(m => m.status === "done" && (m.parts || []).length)
      .slice(0, 12).forEach(m => {
        const label = String(m.task_name || m.src_name || m.id).slice(0, 26);
        m.parts.forEach(p => out.push({
          v: m.id + "|" + p,
          t: "🎤 分离产物 · " + label + " · " + ((SEP_PARTS.find(x => x.key === p) || {}).label || p),
        }));
      });
  } catch (e) { /* 没有分离记录只是少一批选项，不是错误 */ }
  try {
    const g = await (await fetchT("/api/generate/list", {}, 25000)).json();
    (g.items || []).filter(it => (it.kind || "generate") === "generate" && it.status === "done")
      .slice(0, 30).forEach(it => out.push({
        v: it.id,
        t: "🎵 成品歌 · " + String(it.task_name || (it.style || "").split(",")[0] || it.id).slice(0, 26),
      }));
  } catch (e) { /* 同上 */ }
  return out;
}
async function paintSrcPick(selId, noneLabel) {
  const sel = $(selId);
  if (!sel) return;
  const keep = sel.multiple ? [...sel.selectedOptions].map(o => o.value) : sel.value;
  const opts = await serverAudioOptions();
  sel.innerHTML = "";
  if (!sel.multiple) {
    const o0 = document.createElement("option");
    o0.value = ""; o0.textContent = noneLabel;
    sel.appendChild(o0);
  }
  opts.forEach(o => {
    const el = document.createElement("option");
    el.value = o.v; el.textContent = o.t;
    sel.appendChild(el);
  });
  sel.disabled = !opts.length;
  if (!opts.length) {
    const el = document.createElement("option");
    el.value = ""; el.textContent = "（服务器上还没有可选的音频）";
    sel.appendChild(el);
    return;
  }
  if (sel.multiple) {
    const set = new Set(keep);
    [...sel.options].forEach(o => { if (set.has(o.value)) o.selected = true; });
  } else if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
}

$("rvcSrcRefresh").onclick = () => paintSrcPick("rvcSrcPick", "— 不用服务器素材，用上面上传的文件 —");
$("trainSrcRefresh").onclick = () => paintSrcPick("trainSrcPick", "");
// 选了纯人声/已去和声的产物却还勾着「先做人声分离」= 白等一遍 GPU。
// 这里只把代价说清楚，不替用户改勾选——以前那种"自动取消勾选"就是在替别人做决定。
$("rvcSrcPick").onchange = () => {
  const v = $("rvcSrcPick").value || "";
  const now = $("rvcFileNow");
  if (!now) return;
  if (!v) { now.textContent = ""; return; }
  const dry = /\|vocals(_noharmony)?$/.test(v);
  const acc = /\|accompaniment$/.test(v);
  const whole = v.indexOf("|") === -1;
  now.textContent = "音源：服务器上这份 " + v.split("|").join(" · ") +
    (dry && $("rvcSep").checked
      ? "（已是纯人声，勾着「先做人声分离」会再分离一遍，白等约 1.5 分钟——不需要就自己取消勾选）"
      : dry ? "（已是纯人声，可以直接换声）"
      : acc ? "（这是伴奏：里面基本没有人声可换，换声多半不是你要的效果）"
      : whole ? "（整首歌：想只换人声就勾着「先做人声分离」）"
      : "");
};

$("rvcGo").onclick = async () => {
  if (!engineGate($("rvcStatus"))) return;
  const f = $("rvcFile").files && $("rvcFile").files[0];
  const srcId = ($("rvcSrcPick") || {}).value || "";
  if (!f && !srcId) {
    // 指到虚线区，不是 input：input 现在是 display:none，
    // 对它 scrollIntoView/focus 都是空操作，用户只会看到一行字却不知道该去哪儿。
    fieldProblem($("rvcDrop") || $("rvcFile"), $("rvcStatus"),
      "换声要有输入：要么在上面选一个音频文件，要么在下面「服务器素材」里挑一首已经在本机上的歌/干声。");
    return;
  }
  if (f && srcId) {
    fieldProblem($("rvcSrcPick"), $("rvcStatus"),
      "上传的文件和服务器素材只能用一个：现在两边都选了，请清掉一个（默认用你选中的服务器素材就不必重传）。");
    return;
  }
  const model = $("rvcModel").value;
  if (!model) {
    fieldProblem($("rvcModel"), $("rvcStatus"), "没有可用的音色模型（rvc/assets/weights 下需有 .pth）—— 先到「音色制作」做一个，或把 .pth 放进权重目录后刷新。");
    return;
  }
  // 越界的变调会让大段音高落在该音色练过的范围之外 → 一片一片的失真。
  // 不硬拦（用户可能就是要这个效果），但必须让他看着警告再点一次确认。
  const _over = rvcPitchOutOfRange();
  if (_over && !confirm(
    `变调 ${$("rvcPitch").value} 超出「${model.replace(/\.pth$/i, "")}」的安全区间 `
    + `${rvcReco.safe_range.min} ~ ${rvcReco.safe_range.max}（超了 ${_over} 个半音）。\n\n`
    + `区间外的音高该音色基本没学过，成品很可能发劈、失真、带金属声。\n`
    + `推荐值 ${rvcReco.pitch} 就在区间内。\n\n仍要按当前变调提交吗？`)) return;
  const fd = new FormData();
  if (srcId) fd.append("source_id", srcId); else fd.append("file", f);
  fd.append("model", model);
  fd.append("task_name", $("rvcTaskName").value.trim());
  Object.entries(rvcParamValues()).forEach(([k, val]) => fd.append(k, val));
  $("rvcGo").disabled = true;
  $("rvcStatus").className = "status";
  $("rvcStatus").textContent = (srcId ? "取服务器上的那份音频并提交转换…" : "上传并提交转换…")
    + ($("rvcSep").checked ? "（已开启人声分离，分离阶段较慢，请耐心等待）" : "");
  try {
    const r = await fetch("/api/rvc/convert", { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || "提交失败");
    // 状态一律以服务端回包为准（排队 or 开跑），不在前端写死 running——
    // 写死会让第二条明明在排队也画出进度条，这正是"看起来在并行"的来源。
    // ★ src_name 也只能来自回包：走「服务器素材」时前端没有 File 对象，
    //   原先这里读 f.name 会抛 TypeError，被 catch 报成"提交失败"——
    //   而请求早在上一行已经成功入队、GPU 已经开始烧，用户于是再点一次。
    rvcJobs.unshift({ id: d.id, model,
                      task_name: $("rvcTaskName").value.trim(), ...(d.job || {}) });
    renderRvcList();
    const ahead = Math.max(0, Number(d.position || 1) - 1);
    $("rvcStatus").textContent = ahead
      ? `已排队：前面还有 ${ahead} 个换声任务，一次只跑一个，轮到会自动开始。`
      : ($("rvcSep").checked ? "转换已开始（含人声分离，整条耗时会明显拉长），可离开页面。"
                             : "转换进行中（首次加载模型需 1-2 分钟），可离开页面。");
    pollRvc(d.id);
  } catch (e) {
    $("rvcStatus").className = "status err";
    $("rvcStatus").textContent = "提交失败：" + friendlyErr(e);
  } finally { $("rvcGo").disabled = false; }
};

// ---------- ⑥ AI 工作台：原生对话 ----------
// v10.14 起对话不再借外部界面：POST /api/ai/chat → 网关内的 ai_lab.chat_turn
// （DeepSeek + 9 个工具，skill 由 ai_prompt 注入）。产出卡只给"复制"，不代填别的页面。
let chatSid = "", chatBusy = false, chatBooted = false;

function chatSay(text, cls) {
  const el = $("chatState");
  if (el) { el.textContent = text; el.className = "row-label" + (cls ? " " + cls : ""); }
}

function chatCard(role, text) {
  const d = document.createElement("div");
  d.className = "chat-msg " + role;
  const who = document.createElement("span");
  who.className = "chat-who";
  who.textContent = role === "user" ? "我" : "AI";
  const body = document.createElement("div");
  body.className = "chat-body";
  body.textContent = text;
  d.appendChild(who); d.appendChild(body);
  return d;
}

function chatFeedCard(f) {
  const d = document.createElement("div");
  d.className = "chat-feed";
  const t = document.createElement("b");
  t.textContent = f.type === "generate" ? "🎵 已交给生成链" : "产出卡";
  d.appendChild(t);
  const line = document.createElement("div");
  line.className = "chat-feed-line";
  line.textContent = (f.job_id ? "任务 " + f.job_id + (f.cot ? " · 档位 " + f.cot : "") : "");
  d.appendChild(line);
  [["Style", f.style], ["歌词", f.lyrics]].forEach(([label, val]) => {
    if (!val) return;
    const pre = document.createElement("pre");
    pre.className = "chat-feed-pre";
    pre.textContent = val;
    d.appendChild(pre);
    const b = document.createElement("button");
    b.className = "ghost"; b.type = "button"; b.textContent = "📋 复制 " + label;
    b.onclick = () => copyOut(val, label);
    d.appendChild(b);
  });
  return d;
}

async function chatShow(sid) {
  const log = $("chatLog");
  if (!log) return;
  chatSid = sid || "";
  log.innerHTML = "";
  if (!sid) {
    log.innerHTML = '<div class="empty">还没有对话。下面说一句你想要什么样的歌。</div>';
    return;
  }
  try {
    const j = await (await fetchT("/api/ai/session/" + encodeURIComponent(sid), {}, 25000)).json();
    (j.messages || []).forEach((m) => {
      if (!m || (m.role !== "user" && m.role !== "assistant")) return;
      log.appendChild(chatCard(m.role, String(m.content || "")));
    });
    (j.feeds || []).forEach((f) => log.appendChild(chatFeedCard(f)));
    if (!log.children.length) log.innerHTML = '<div class="empty">这条会话还是空的。</div>';
  } catch (e) { chatSay("读会话失败：" + friendlyErr(e), "err"); }
}

async function chatSend(confirm) {
  if (chatBusy) return;
  const inp = $("chatInput");
  const msg = confirm ? "" : (inp.value || "").trim();
  if (!msg && !confirm) { chatSay("先说要什么样的歌。", "err"); return; }
  chatBusy = true;
  $("chatSend").disabled = true;
  if (!confirm) {
    const log = $("chatLog");
    const ph = log.querySelector(".empty");
    if (ph) ph.remove();
    log.appendChild(chatCard("user", msg));
    inp.value = "";
    log.scrollTop = log.scrollHeight;
  }
  chatSay(confirm ? "执行确认的操作中…" : "AI 正在想并调工具，可能要 1-2 分钟…");
  try {
    const r = await fetchT("/api/ai/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: chatSid, message: msg, confirm: !!confirm }),
    }, 300000);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || ("HTTP " + r.status));
    chatSid = j.session_id || chatSid;
    $("chatLog").appendChild(chatCard("assistant", String(j.reply || "（空回复）")));
    (j.feeds || []).forEach((f) => $("chatLog").appendChild(chatFeedCard(f)));
    const cf = $("chatConfirm");
    if (j.needs_confirm) {
      cf.hidden = false;
      cf.innerHTML = "";
      const tip = document.createElement("span");
      tip.textContent = "AI 要执行一个不可恢复的操作：" + (j.confirm_hint || "请确认");
      cf.appendChild(tip);
      const ok = document.createElement("button");
      ok.className = "danger"; ok.type = "button"; ok.textContent = "确认执行";
      ok.onclick = () => { cf.hidden = true; chatSend(true); };
      const no = document.createElement("button");
      no.className = "ghost"; no.type = "button"; no.textContent = "取消这次操作";
      no.onclick = () => { cf.hidden = true; chatSay("已取消，什么都没执行。"); };
      cf.appendChild(ok); cf.appendChild(no);
    } else { cf.hidden = true; }
    chatSay("");
    if (typeof loadAiSessions === "function") loadAiSessions();
  } catch (e) {
    chatSay("这轮没跑成：" + friendlyErr(e), "err");
  } finally {
    chatBusy = false;
    $("chatSend").disabled = false;
  }
}

$("chatSend").onclick = () => chatSend(false);
$("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); chatSend(false); }
});
$("chatNew").onclick = () => { chatShow(""); chatSay("新会话：下一条消息开一条新的记录", ""); };
if ($("aiReload")) $("aiReload").onclick = () => chatShow(chatSid);
const _origSwitchTab = switchTab;
switchTab = function (name) {
  _origSwitchTab(name);
  if (name === "ailab") {
    // 首次进这页才拉会话列表；对话区保持上一次的会话，不空转
    if (!chatBooted) { chatBooted = true; if (typeof loadAiSessions === "function") loadAiSessions(); }
  }
  // 回到创作页时把"最近歌词"刷新一次：刚生成完的那首应该立刻能被点回来
  if (name === "sing") loadRecentLyrics(true);
};

// ---------- rvc 音色模型管理（音色库页）----------
let rvcModelsAll = [];             // 最近一次拉到的完整列表（筛选在本地做，不再打接口）
function mBadge(text, cls, title) {
  return `<span class="m-badge${cls ? " " + cls : ""}"${title ? ` title="${escapeHtml(title)}"` : ""}>${escapeHtml(text)}</span>`;
}
async function renderRvcModels() {
  const el = $("rvcModelList");
  if (!el) return;
  try {
    const r = await fetch("/api/rvc/models");
    if (!r.ok) return;
    const j = await r.json();
    rvcModelsAll = j.items || [];
    paintRvcModels();
  } catch {}
}
function paintRvcModels() {
  const el = $("rvcModelList");
  if (!el) return;
  const kw = (($("rvcModelFilter") || {}).value || "").trim().toLowerCase();
  const items = kw ? rvcModelsAll.filter(m => m.name.toLowerCase().includes(kw)) : rvcModelsAll;
  const cnt = $("rvcModelCount");
  if (cnt) {
    // 没筛选时"5/5"是自说自话，只报总数；打了关键字才需要"命中 / 总数"两个数。
    cnt.textContent = rvcModelsAll.length ? (kw ? `${items.length}/${rvcModelsAll.length}` : `${rvcModelsAll.length} 个`) : "";
    cnt.title = kw ? `筛选命中 ${items.length} 个 · 库内共 ${rvcModelsAll.length} 个` : `库内共 ${rvcModelsAll.length} 个音色模型`;
  }
  el.innerHTML = "";
  if (!rvcModelsAll.length) { el.innerHTML = '<div class="empty">暂无音色模型。可在「⑦ 音色制作」页训练，或点「导入音色文件」装入外部 .pth。</div>'; setVoiceTab(); return; }
  if (!items.length) { el.innerHTML = `<div class="empty">没有名字含「${escapeHtml(kw)}」的音色（共 ${rvcModelsAll.length} 个）。</div>`; return; }
  items.forEach(m => el.appendChild(rvcModelCard(m)));
  setVoiceTab();
}
function rvcModelCard(m) {
  const d = document.createElement("div");
  d.className = "m-item";
  const fr = m.f0_range;
  // 音域那一长句以前直接摊在卡片上（"舒适音域 ≈ …Hz（中位 …，来源：…）"），
  // 现在压成两三个字的一枚徽牌，细节全放 title：扫列表时看名字和徽牌就够了。
  const f0Badge = fr
    ? mBadge(`音域 ${fr.p5_hz}–${fr.p95_hz}Hz`, "f0",
        `舒适音域 ≈ ${fr.p5_hz}–${fr.p95}Hz（中位 ${fr.median_hz}Hz）\n来源：` +
        (fr.source === "reference" ? `参考音频${fr.analyzed_sec ? "，分析了 " + Math.round(fr.analyzed_sec) + " 秒" : ""}` : "训练素材实测"))
    : mBadge("无音域", "nof0", "没有音域档案，变调建议只能靠猜。\n可点「建档音域」上传该歌手本人 10~30 秒片段量一次。");
  const canRef = !fr || fr.source !== "training";   // 训练实测过的后端 409 拒绝，按钮也就不给
  d.innerHTML = `
    <div class="m-top">
      <span class="m-name">${escapeHtml(m.name.replace(/\.pth$/i, ""))}</span>
      <span class="m-badges">
        ${f0Badge}
        ${mBadge(m.size_mb + " MB", "", "模型文件大小")}
        ${mBadge((m.mtime || "").slice(0, 10), "", "文件时间 " + (m.mtime || ""))}
      </span>
    </div>
    <div class="m-note" hidden></div>
    <div class="m-actions">
      <button class="ghost" data-check>体检</button>
      ${canRef ? `<button class="ghost" data-f0ref title="上传该音色本人的一段歌（干声最佳，10~30 秒），量出舒适音域给变调建议用${fr ? "；重传会覆盖旧参考档案" : ""}">${fr ? "↻ 重建档" : "建档音域"}</button>` : ""}
      <button class="ghost" data-merge>融合</button>
      <button class="ghost" data-rename>重命名</button>
      <button class="danger" data-del>删除</button>
    </div>`;
  const note = d.querySelector(".m-note");
  const setNote = (html) => { note.hidden = !html; note.innerHTML = html || ""; };
  {
    const fb = d.querySelector("[data-f0ref]");
    if (fb) fb.onclick = (e) => {
      e.stopPropagation();
      const inp = document.createElement("input");
      inp.type = "file"; inp.accept = "audio/*";
      inp.onchange = async () => {
        const f = inp.files[0];
        if (!f) return;
        fb.disabled = true; fb.textContent = "建档中…";
        try {
          const fd = new FormData();
          fd.append("file", f);
          const rr = await fetch(`/api/rvc/models/${encodeURIComponent(m.name)}/f0-reference`,
                                 { method: "POST", body: fd });
          const jj = await rr.json().catch(() => ({}));
          if (!rr.ok) throw new Error(jj.detail || "建档失败");
          const s = jj.stats || {};
          toast(`「${m.name.replace(/\.pth$/i, "")}」音域已建档：中位 ${s.median_hz}Hz` +
                `（${s.p5_hz}–${s.p95_hz}，分析 ${s.analyzed_sec || "?"} 秒，来源：参考音频）`, "ok");
          renderRvcModels(); renderRvc();
        } catch (err) { toast(friendlyErr(err), "err"); fb.disabled = false; fb.textContent = fr ? "↻ 重建档" : "建档音域"; }
      };
      inp.click();
    };
  }
  d.querySelector("[data-check]").onclick = async (e) => {
    e.stopPropagation();
    const btn = e.currentTarget; btn.disabled = true; btn.textContent = "体检中…";
    setNote("");
    try {
      const rr = await fetch(`/api/rvc/models/${encodeURIComponent(m.name)}/check`);
      const jj = await rr.json().catch(() => ({}));
      if (!rr.ok) throw new Error(jj.detail || "体检失败");
      // 结果写进卡片自己的备注行：以前只有一枚 toast，四秒后消失，
      // 想对照两个音色的体检结论都做不到。
      // ★ 服务端返回的 version/epoch 字符串本身就带单位（"v2"、"1000epoch"），
      //   前端不能再补一遍，否则渲染成 "vv2 · epoch 1000epoch"。这里把尾部的单位剥掉再自己拼。
      const i = jj.info || {};
      const ep = String(i.epoch ?? "").replace(/epoch$/i, "").trim();
      const ver = String(i.version ?? "").replace(/^v/i, "").trim();
      const info = (i.sr || ver || ep) ? ` · ${[i.sr, ver && "v" + ver, ep && "epoch " + ep].filter(Boolean).join(" · ")}` : "";
      const rows = Object.entries(jj.checks || {})
        .map(([k, v]) => `<span class="${v ? "good" : "bad"}">${v ? "✓" : "✕"} ${escapeHtml(k)}</span>`).join("　");
      setNote(`<b>${escapeHtml(String(jj.summary || ""))}${escapeHtml(info)}</b><br>${rows}`);
    } catch (err) { setNote(`<span class="bad">体检失败：${escapeHtml(friendlyErr(err))}</span>`); }
    btn.disabled = false; btn.textContent = "体检";
  };
  // 融合：三个字段（选 B / 比例 / 新名称）摊成卡片下方一行行内表单，不再用 prompt()。
  // 原来要用户凭记忆敲另一个音色的准确名字，敲错一个字符服务端只回"音色不存在"；
  // 下拉 + 滑杆把"名字敲错、比例写成 0.3,、新名称带空格"这三处出错可能一次消掉。
  d.querySelector("[data-merge]").onclick = (e) => {
    e.stopPropagation();
    const btn = e.currentTarget;
    const others = rvcModelsAll.filter(x => x.name !== m.name);
    if (!others.length) { toast("只有一个音色，没有可融合的对象", "err"); return; }
    const cur = m.name.replace(/\.pth$/i, "");
    inlineForm(btn, {
      anchor: d,
      title: "🎚 与「" + cur + "」融合出新音色",
      okText: "开始融合", busyText: "融合中…",
      hint: "比例是「音色 B」在新音色里的占比（权重插值，约 10-30 秒）。",
      fields: [
        { id: "b", label: "音色 B", kind: "select", value: others[0].name,
          options: others.map(x => ({ v: x.name, t: x.name.replace(/\.pth$/i, "") })) },
        { id: "share", label: "B 占比", kind: "range", min: 0, max: 100, step: 5, value: 30,
          readout: (s) => "B " + s + "% · " + cur + " " + (100 - (Number(s) || 0)) + "%" },
        { id: "name", label: "新名称", kind: "text", maxlen: 40, placeholder: "例如：燕姿混王菲" },
      ],
      validate: (v) => v.name ? (others.some(x => x.name === v.b) ? null
                                      : { msg: "音色列表已变，请重新展开选择", focus: "b" })
                              : { msg: "给新音色起个名字", focus: "name" },
    }, async (v) => {
      const bs = Math.max(0, Math.min(100, Number(v.share) || 0)) / 100;   // B 在新音色里的占比
      toast("融合中（约 10-30 秒）…", "ok");
      const rr = await fetch("/api/rvc/models/merge", {
        method: "POST", headers: { "Content-Type": "application/json" },
        // 服务端 alpha 是「a（当前音色）的权重」（app.py: alpha*va + (1-alpha)*vb），
        // 滑杆给的是 B 的占比，所以这里必须传 1-bs；直接传 bs 会把比例反掉。
        body: JSON.stringify({ a: m.name, b: v.b, alpha: Math.round((1 - bs) * 100) / 100, name: v.name }),
      });
      const jj = await rr.json().catch(() => ({}));
      if (!rr.ok) throw new Error(jj.detail || "融合失败");
      toast("融合完成，新音色：" + (jj.name || v.name), "ok");
      renderRvcModels(); renderRvc();
    });
  };
  // 重命名也走行内表单：一个 prompt() 弹窗没有校验、没有错误位，
  // 敲了同名/空名只能看服务端脸色。
  d.querySelector("[data-rename]").onclick = (e) => {
    e.stopPropagation();
    const cur = m.name.replace(/\.pth$/i, "");
    inlineForm(e.currentTarget, {
      anchor: d,
      title: "✏️ 重命名「" + cur + "」",
      okText: "保存新名称", busyText: "提交中…",
      hint: "只改显示名，索引文件跟着走；正在被换声任务使用时服务端会拦下。",
      fields: [{ id: "name", label: "新名称", kind: "text", value: cur, maxlen: 40 }],
      validate: (v) => !v.name ? { msg: "名称不能为空", focus: "name" }
                   : v.name === cur ? { msg: "名称没有变化", focus: "name" } : null,
    }, async (v) => {
      const rr = await fetch(`/api/rvc/models/${encodeURIComponent(m.name)}/rename`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: v.name }),
      });
      const jj = await rr.json().catch(() => ({}));
      if (!rr.ok) throw new Error(jj.detail || "重命名失败");
      toast("已重命名为 " + jj.name, "ok");
      renderRvcModels(); renderRvc();
    });
  };
  d.querySelector("[data-del]").onclick = async (e) => {
    e.stopPropagation();
    const btn = e.currentTarget;
    if (!confirm(`删除音色「${m.name}」？\n模型文件和它的音色索引会一起删除，本机不可恢复。\n（正在被换声任务使用时服务端会拦下）`)) return;
    btn.disabled = true;
    try {
      const rr = await fetch(`/api/rvc/models/${encodeURIComponent(m.name)}`, { method: "DELETE" });
      const jj = await rr.json().catch(() => ({}));
      if (!rr.ok) throw new Error(jj.detail || "删除失败");
      toast("已删除 " + m.name, "ok");
      renderRvcModels(); renderRvc();
    } catch (err) { toast(friendlyErr(err), "err"); btn.disabled = false; }
  };
  return d;
}

// ---------- rvc 音色制作 ----------
const rvcTrainPolls = new Map();  // rid → setInterval 句柄（Set 没有 .get/.set，早期版本用 Set 会在注册定时器时抛 TypeError）
const rvcTrainMiss = new Map();   // rid → 连续查不到状态（404）的次数
const rvcTrainJobs = [];

function stopRvcTrainPoll(rid) {
  const t = rvcTrainPolls.get(rid);
  if (t) clearInterval(t);
  rvcTrainPolls.delete(rid);
  rvcTrainMiss.delete(rid);
}

let _lastTrainKey = "__init__";
function renderRvcTrainList() {
  const el = $("rvcTrainList");
  // 数据没变就不重建 DOM：轮询每 10s 触发一次，重建会闪掉播放器/按钮状态，
  // 长期闲置时空转消耗也是页面变卡的原因之一
  const key = JSON.stringify(rvcTrainJobs);
  if (key === _lastTrainKey) return;
  _lastTrainKey = key;
  if (!rvcTrainJobs.length) { el.innerHTML = '<div class="empty">暂无音色制作任务。</div>'; return; }
  el.innerHTML = "";
  rvcTrainJobs.forEach(j => {
    const d = document.createElement("div");
    d.className = "hist-item";
    const st = j.status === "done" ? "✓ 完成" : j.status === "error" ? "✕ 失败"
      : j.status === "lost" ? "⚠ 状态丢失（服务重启过，成品见音色列表）"
      : j.status === "checked" ? "📥 素材已体检·未开练"
      : j.status === "pending" ? "⏳ 排队" : "🎵 " + (j.step || "进行中");
    // 训练轮次：进行中且拿到轮次数据时，显示百分比 + 进度条 + 预计剩余时间
    const total = j.epochs_total || j.epochs || 0;
    const inTrain = j.status === "running" && (j.step || "").indexOf("训练中") === 0 && j.epoch && total > 0;
    const pct = inTrain ? Math.min(100, Math.round(j.epoch / total * 100)) : null;
    let eta = "";
    // 优先用服务端从训练日志稳健解析的每轮耗时（剔除偶发卡顿轮）；
    // 其次退回前端两次轮询观测值；刚开跑还没数据时用服务端的规模预估（实测或保守回落）
    const perEpoch = j.per_epoch_sec || j._per_epoch_last || j.epoch_est_sec;
    if (inTrain && perEpoch) {
      const remain = Math.max(0, (total - j.epoch) * perEpoch);
      eta = " · 剩余约 " + (remain > 90 ? Math.round(remain / 60) + " 分" : Math.round(remain) + " 秒");
    }
    // 有效日志行：取 log_tail 最后一条非空行（避免一直停在旧步骤的输出）
    let liveLog = "";
    if (j.status === "running" && j.log_tail) {
      const lines = String(j.log_tail).split(/[\r\n]+/).filter(s => s.trim());
      liveLog = (lines[lines.length - 1] || "").trim();
    }
    // 检查点：训练完成后服务端留最终档 + 中段两档（约 50%/75% 处，共 kept 个）；
    // 训练中则以最近一次试听返回的列表为准。有得选才显示选择器，不然凭空多一排死按钮。
    const cks = (j.ckpts && j.ckpts.length ? j.ckpts : (j.kept_ckpts || [])).filter(Boolean);
    const loss = j.loss && j.loss.head && j.loss.tail
      ? `损失 g ${(j.loss.head.loss_gen ?? "?")}→${(j.loss.tail.loss_gen ?? "?")} · d ${(j.loss.head.loss_disc ?? "?")}→${(j.loss.tail.loss_disc ?? "?")}`
      : "";
    // 自检不光"有没有出声"，还量了高频纹理：练过头的音色是出声了、但 8–16k 被磨成宽带噪声
    const rg = j.self_check && j.self_check.roughness;
    const rgText = rg ? (rg.out === null
      ? `高频噪声化 ${rg.in}→量不出（产物连基频都没有）`
      : `高频噪声化 ${rg.in}→${rg.out}（${rg.ratio} 倍）` + (rg.onset_ratio != null
          ? ` · 辅音音头 ${rg.onset_in}→${rg.onset_out}/秒（剩 ${Math.round(rg.onset_ratio * 100)}%）` : "")) : "";
    const sc = j.self_check
      ? (j.self_check.ok
          ? "自检片段已生成" + (rgText ? ` · ${rgText}` : "")
          : "自检片段失败：" + (j.self_check.error || ""))
      : "";
    const scWarn = (j.self_check && j.self_check.warn) || "";
    // 净化留痕（评审 A1）：跑了哪两个模型、几个文件、有没有回退——原始与净化后
    // 两个目录都写进悬停提示，A/B 对比不用猜路径
    const cleanInfo = j.clean
      ? `🧹 已净化（${j.clean.label}档：去混响 → 降噪）· ${j.clean.files} 个文件 · 完整净化 ${j.clean.fully_cleaned}${j.clean.carried ? " · " + j.clean.carried + " 次回退用净化前版本" : ""}${j.clean.sec ? " · " + (j.clean.sec > 90 ? Math.round(j.clean.sec / 60) + "分" : j.clean.sec + "s") : ""}`
      : "";
    // 长跑守卫留痕（v1.6.0）：中断过几次、有没有自己续跑回来、开训前让没让显存、
    // 当时显卡是什么状态。这些数字以前只在服务端的 job.json 里，页面上看不见，
    // 于是"练了一晚断了"这种事只能事后翻日志——现在当场就说清。
    const gw = j.gpu_watch || {};
    const gBits = [];
    if (j.gpu_yield && j.gpu_yield.action) gBits.push("开训前" + j.gpu_yield.action);
    if ((j.attempts || []).length) gBits.push("中断 " + j.attempts.length + " 次");
    if (j.recovered_after) gBits.push("第 " + j.recovered_after.attempt + " 次续跑成功");
    if (j.pace_warn && j.pace_warn.expect_sec) gBits.push("曾慢 " + (j.pace_warn.epoch_sec / j.pace_warn.expect_sec).toFixed(1) + " 倍");
    if (gw.max_mem_used_mb) gBits.push("显存峰值 " + gw.max_mem_used_mb + "MB");
    if (gw.max_temp_c) gBits.push("核心峰值 " + gw.max_temp_c + "℃");
    if (gw.throttle_samples) gBits.push("降频采样 " + gw.throttle_samples + " 次");
    const guardInfo = gBits.join(" · ");
    const guardTitle = (j.attempts || []).map(a =>
      `${a.at} 停在第 ${a.epoch} 轮：${a.reason}`).join("\n");
    d.innerHTML = `
      <div class="h-body">
        <div class="h-title">${escapeHtml(j.name || "")} · ${st}${inTrain ? `（${j.epoch}/${total} · ${pct}%）` : ""}${j.sec && j.status !== "running" ? " · " + (j.sec > 90 ? Math.round(j.sec / 60) + "分" : j.sec + "s") : ""}</div>
        <div class="h-sub truncate">${escapeHtml(`${j.samples || "?"} 个样本 · ${j.epochs || "?"} 轮`)}${j.batch_note ? " · " + escapeHtml(j.batch_note) : ""}${j.model ? " · 产出 " + escapeHtml(j.model) : ""}${j.promoted_ckpt ? " · 定稿 " + escapeHtml(j.promoted_ckpt) : ""}${eta}</div>
        ${loss || sc ? `<div class="h-sub truncate" title="${escapeHtml([loss, sc].filter(Boolean).join(" · "))}">${escapeHtml([loss, sc].filter(Boolean).join(" · "))}</div>` : ""}
        ${scWarn ? `<div class="h-sub" style="color:var(--warn,#c60);">⚠ ${escapeHtml(scWarn)}</div>` : ""}
        ${cleanInfo ? `<div class="h-sub truncate" title="原始素材：${escapeHtml(j.clean.before || "")}｜净化后：${escapeHtml(j.clean.after || "")}｜模型：${escapeHtml(j.clean.dereverb || "")} + ${escapeHtml(j.clean.denoise || "")}">${escapeHtml(cleanInfo)}</div>` : ""}
        ${guardInfo ? `<div class="h-sub truncate" title="${escapeHtml(guardTitle || guardInfo)}">🛡 ${escapeHtml(guardInfo)}</div>` : ""}
        <div class="h-sub">${idTag(j.id)}</div>
        ${pct !== null ? `<div class="progress show" style="margin-top:6px;"><div style="width:${pct}%;"></div></div>` : ""}
        ${j.status === "error" ? `<div class="h-sub truncate" title="${escapeHtml(j.error || "")}">${escapeHtml((j.error || "").slice(0, 100))}</div>` : ""}
        ${liveLog ? `<div class="h-sub truncate" style="color:var(--acc-text);" title="${escapeHtml(liveLog)}">${escapeHtml(liveLog)}</div>` : ""}
      </div>
      ${j.status === "done" ? '<button class="ghost" data-copy-name title="复制这个音色名，到 ⑨ 的音色下拉里找它">📋 复制音色名</button>' : ''}
      ${j.status === "error" ? '<button class="ghost" data-resume>↻ 续跑</button>' : ''}
      ${j.status === "done" ? '<button class="ghost" data-resume title="从断点继续；填别的轮数=归档旧检查点从头重训">↻ 重训</button>' : ''}
      ${j.status === "paused" ? '<button class="ghost" data-resume>▶ 继续</button>' : ''}
      ${j.status === "running" ? '<button class="ghost" data-pause>⏸ 暂停</button>' : ''}
      ${["done", "error", "paused", "checked", "lost"].indexOf(j.status) >= 0
        ? '<button class="ghost" data-deltask title="只删这个任务目录（上传的素材、试听、重训备份），已练好的音色与索引不动；要删音色请到音色管理">🗑 删任务</button>' : ''}
      ${(j.status === "running" || j.status === "done") ? '<button class="ghost" data-preview>🎧 试听</button>' : ''}
      ${j.preview_url ? `<audio controls preload="none" src="${j.preview_url}" style="width:100%;margin-top:6px;height:32px;"></audio>` : ''}
      ${cks.length ? `<div style="width:100%;display:flex;gap:6px;align-items:center;margin-top:6px;">
        <select class="rvc-ck" style="flex:1;min-width:0;">${cks.map(c => `<option value="${escapeHtml(c)}">${escapeHtml((j.ck_labels && j.ck_labels[c]) || c)}</option>`).join("")}</select>
        <button class="ghost" data-ckplay>试听这个点</button>
        <button class="ghost" data-ckpromote title="把这个检查点导出为最终成品（音色名不变，索引照用）">📌 定稿</button>
      </div>
      ${j.preview_source ? `<div class="h-sub">当前试听：${escapeHtml(j.preview_source)}</div>` : ''}` : ''}`;
    const rb = d.querySelector("[data-resume]");
    if (rb) rb.onclick = async (e) => {
      e.stopPropagation();
      rb.disabled = true;
      try {
        // done 任务重跑会覆盖同名成品，服务端要求显式 confirm=yes（裁定 F-1）
        const isDone = j.status === "done";
        let body = "";
        if (isDone) {
          if (!window.confirm("该任务已完成。重跑会用新结果覆盖现有成品音色（旧成品会先在任务目录里存一份），确定继续？")) {
            rb.disabled = false;
            return;
          }
          // 换个轮数重训：练过头（成品发沙/电音）时的退路。以前只能删了重传素材，
          // 等于没有这条路——42 分钟的干声谁也不想再传一遍。
          const ans = window.prompt(
            `重训轮数：留空=沿用 ${j.epochs || "?"} 轮从断点继续；` +
            `填别的数字=归档旧检查点、从底模重新练满这个轮数。`, "");
          body = "confirm=yes";
          const n = parseInt(ans, 10);
          if (ans !== "" && (!n || n < 1 || n > 1200)) {
            toast("轮数须在 1-1200 之间", "err");
            rb.disabled = false;
            return;
          }
          if (n && n !== Number(j.epochs)) body += `&epochs=${n}&restart=yes`;
        }
        const r = await fetchT("/api/rvc/train/resume/" + j.id, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body,
        });
        const x = await r.json();
        if (!r.ok) throw new Error(x.detail || "续跑失败");
        const i = rvcTrainJobs.findIndex(t => t.id === j.id);
        if (i >= 0) rvcTrainJobs[i] = { ...rvcTrainJobs[i], ...x.job };
        renderRvcTrainList();
        pollRvcTrain(j.id);
        toast(body.includes("restart=yes")
          ? `已按 ${x.job.epochs} 轮从头重训（旧检查点已归档，旧成品已备份）`
          : "任务已从断点续跑（已完成的分离产物会复用）", "ok");
      } catch (err) {
        rb.disabled = false;
        toast("续跑失败：" + friendlyErr(err), "err");
      }
    };
    const pa = d.querySelector("[data-pause]");
    if (pa) pa.onclick = async (e) => {
      e.stopPropagation();
      pa.disabled = true;
      try {
        const r = await fetchT("/api/rvc/train/pause/" + j.id, { method: "POST" }, 60000);
        const x = await r.json();
        if (!r.ok) throw new Error(x.detail || "暂停失败");
        const i = rvcTrainJobs.findIndex(t => t.id === j.id);
        if (i >= 0) rvcTrainJobs[i] = { ...rvcTrainJobs[i], ...x.job };
        renderRvcTrainList();
        toast("任务已暂停（GPU 已释放，可去跑其他任务；随时点「▶ 继续」断点续跑）", "ok");
      } catch (err) {
        pa.disabled = false;
        toast("暂停失败：" + friendlyErr(err), "err");
      }
    };
    const db = d.querySelector("[data-deltask]");
    if (db) db.onclick = async (e) => {
      e.stopPropagation();
      // 体检过却从没开练的素材以前在页面上根本没有条目，也就无从删除，上传的几个 GB
      // 就一直留在 trains/ 下；这里给出唯一的清理入口。音色与索引不在本目录，删不到。
      if (!window.confirm(j.status === "checked"
        ? "这会连同你上传的素材一起删掉（这个任务还没练出音色）。确定删除？"
        : "删除任务目录会带走上传的素材、试听与重训备份；已练好的音色和检索库不受影响。确定删除？")) return;
      db.disabled = true;
      try {
        const r = await fetchT("/api/rvc/train/" + j.id, { method: "DELETE" }, 60000);
        const x = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(x.detail || "删除失败");
        const i = rvcTrainJobs.findIndex(t => t.id === j.id);
        if (i >= 0) rvcTrainJobs.splice(i, 1);
        _lastTrainKey = "";
        renderRvcTrainList();
        toast("任务目录已删除（音色与索引保留）", "ok");
      } catch (err) {
        db.disabled = false;
        toast("删除失败：" + friendlyErr(err), "err");
      }
    };
    const pb = d.querySelector("[data-preview]");
    if (pb) pb.onclick = async (e) => {
      e.stopPropagation();
      pb.disabled = true; pb.textContent = "试听生成中…（CPU 推理约 20-40 秒）";
      try {
        const r = await fetchT("/api/rvc/train/preview/" + j.id, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
        }, 180000);
        const x = await r.json();
        if (!r.ok) throw new Error(x.detail || "试听失败");
        const i = rvcTrainJobs.findIndex(t => t.id === j.id);
        if (i >= 0) rvcTrainJobs[i] = { ...rvcTrainJobs[i], preview_url: x.url,
          preview_source: x.source, ckpts: x.ckpts || [], ck_labels: x.ck_labels || {} };
        renderRvcTrainList();
        toast("试听已生成（" + x.source + "），播放器在任务卡片下方", "ok");
      } catch (err) {
        pb.disabled = false; pb.textContent = "🎧 试听";
        toast(friendlyErr(err), "err");
      }
    };
    // 逐点试听 / 选点定稿：训练完成留最终档 + 约 25%/50%/75% 共四档（练过头要听早中段，
    // 10-03 实测 30/42/60 轮越练越脏，只留最近两档等于把唯一干净的那档删了），
    // 不必"练到第几轮"全凭运气
    const cp = d.querySelector(".rvc-ck");
    const cb = d.querySelector("[data-ckplay]");
    if (cb) cb.onclick = async (e) => {
      e.stopPropagation();
      if (!cp || !cp.value) { toast("请先选一个检查点", "err"); return; }
      cb.disabled = true; cb.textContent = "生成中…";
      try {
        const r = await fetchT("/api/rvc/train/preview/" + j.id, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ck: cp.value }),
        }, 180000);
        const x = await r.json();
        if (!r.ok) throw new Error(x.detail || "试听失败");
        const i = rvcTrainJobs.findIndex(t => t.id === j.id);
        if (i >= 0) rvcTrainJobs[i] = { ...rvcTrainJobs[i], preview_url: x.url,
          preview_source: x.source, ckpts: x.ckpts || [], ck: cp.value };
        renderRvcTrainList();
        toast("试听已生成（" + x.source + "）", "ok");
      } catch (err) {
        cb.disabled = false; cb.textContent = "试听这个点";
        toast(friendlyErr(err), "err");
      }
    };
    const cb2 = d.querySelector("[data-ckpromote]");
    if (cb2) cb2.onclick = async (e) => {
      e.stopPropagation();
      if (!cp || !cp.value) { toast("请先选一个检查点", "err"); return; }
      if (!window.confirm(`用 ${cp.value} 覆盖现有成品音色「${j.name}」？\n音色名与索引都不变，换声页立即生效。`)) return;
      cb2.disabled = true;
      try {
        const r = await fetchT("/api/rvc/train/promote/" + j.id, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ck: cp.value }),
        }, 120000);
        const x = await r.json();
        if (!r.ok) throw new Error(x.detail || "定稿失败");
        const i = rvcTrainJobs.findIndex(t => t.id === j.id);
        if (i >= 0) rvcTrainJobs[i] = { ...rvcTrainJobs[i], promoted_ckpt: x.ckpt, model: x.model };
        renderRvcTrainList();
        renderRvcModels(); renderRvc();
        toast(x.message, "ok");
      } catch (err) {
        cb2.disabled = false;
        toast("定稿失败：" + friendlyErr(err), "err");
      }
    };
    // 「去使用」（跳到 ⑨ 并替它选中音色）已删：那是别的页面的表单，选哪个音色该由用户在 ⑨ 自己定。
    const ub = d.querySelector("[data-copy-name]");
    if (ub) ub.onclick = (e) => { e.stopPropagation(); copyOut(j.model || (j.name + ".pth"), "音色名"); };
    el.appendChild(d);
  });
}

async function pollRvcTrain(rid) {
  if (document.hidden) return; // 后台标签页跳过请求（浏览器节流下徒增积压），回前台立即补一次
  try {
    const r = await fetchT("/api/rvc/train/status/" + rid, {}, 15000);
    if (r.ok) {
      rvcTrainMiss.delete(rid);
      const j = await r.json();
      const i = rvcTrainJobs.findIndex(x => x.id === rid);
      if (i >= 0) {
        const prev = rvcTrainJobs[i];
        // 记录轮次观测点：用于估算每轮耗时与剩余时间
        if (j.epoch && (!prev.epoch_started_at || j.epoch !== prev.epoch)) {
          if (prev.epoch && j.epoch > prev.epoch && prev.epoch_started_at) {
            j._per_epoch = (Date.now() - prev.epoch_started_at) / (j.epoch - prev.epoch);
          }
          j.epoch_started_at = Date.now();
          j.epoch_at_restore = j.epoch;
        }
        if (j._per_epoch) { j._per_epoch_last = j._per_epoch; }
        rvcTrainJobs[i] = { ...prev, ...j };
      }
      renderRvcTrainList();
      updateRvcTrainProgress(j);
      if (j.status === "done") {
        stopRvcTrainPoll(rid);
        $("rvcTrainStatus").className = "status ok";
        $("rvcTrainStatus").textContent = `音色「${j.name}」制作完成（${Math.round(j.sec / 60)} 分钟），已出现在音色下拉框。`;
        $("rvcTrainProgress").classList.remove("show");
        toast("音色制作完成：" + j.name, "ok");
        renderRvc();
        return;
      }
      if (j.status === "error") {
        stopRvcTrainPoll(rid);
        $("rvcTrainStatus").className = "status err";
        $("rvcTrainStatus").textContent = "音色制作失败：" + (j.error || "").slice(0, 160);
        $("rvcTrainProgress").classList.remove("show");
        return;
      }
      $("rvcTrainStatus").textContent = `当前步骤：${j.step || "进行中"}…（可离开页面，完成会通知）`;
    } else if (r.status === 404) {
      // 网关重启过：内存任务表没了，继续挂着定时器就是永久空转
      const miss = (rvcTrainMiss.get(rid) || 0) + 1;
      rvcTrainMiss.set(rid, miss);
      if (miss >= 3) {
        stopRvcTrainPoll(rid);
        const i = rvcTrainJobs.findIndex(x => x.id === rid);
        if (i >= 0) rvcTrainJobs[i] = { ...rvcTrainJobs[i], status: "lost" };
        renderRvcTrainList();
        $("rvcTrainProgress").classList.remove("show");
      }
    }
  } catch {}
  if (!rvcTrainPolls.has(rid)) {
    const t = setInterval(() => pollRvcTrain(rid), 10000);
    rvcTrainPolls.set(rid, t);
  }
}

function updateRvcTrainProgress(j) {
  const wrap = $("rvcTrainProgress"), bar = $("rvcTrainProgressBar");
  if (!wrap || !bar) return;
  if (j.status !== "running" && j.status !== "pending") { wrap.classList.remove("show"); return; }
  wrap.classList.add("show");
  const total = j.epochs_total || j.epochs || 0;
  if ((j.step || "").indexOf("训练中") === 0 && total > 0 && j.epoch) {
    bar.style.width = Math.min(100, Math.round(j.epoch / total * 100)) + "%";
  } else if (j.status === "pending") {
    bar.style.width = "4%";
  } else {
    // 预处理/F0/特征/索引等无轮次步骤：走马灯式半程
    bar.style.width = "50%";
  }
}

// 页面加载/iframe 重载后恢复进行中的音色制作任务（进度条 + 轮询不断线）
async function restoreRvcTrainActive() {
  try {
    const r = await fetch("/api/rvc/train/active");
    if (!r.ok) return;
    const d = await r.json();
    (d.items || []).forEach(j => {
      if (rvcTrainJobs.some(x => x.id === j.id)) return;
      rvcTrainJobs.unshift(j);
      // 只对进行中的任务启动轮询——done/error 的历史任务若也轮询，
      // 刷新页面会逐个弹"完成/失败"toast、横幅被旧信息覆盖
      if (j.status === "running" || j.status === "pending") pollRvcTrain(j.id);
    });
    renderRvcTrainList();
  } catch {}
}

// 回前台立即补一次轮询：后台期间定时器被节流，回来先拿最新状态
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  rvcTrainPolls.forEach((_t, rid) => pollRvcTrain(rid));
});

// 素材体检：结果与服务端那份素材的指纹放在一起。换了文件就必须重传，
// 没换则训练直接复用（dataset_id），几十分钟的素材不必传两遍。
let rvcChecked = { id: "", sig: "" };
const rvcTrainSrcIds = () => [...(($("trainSrcPick") || {}).selectedOptions || [])]
  .map(o => o.value).filter(Boolean);
// 指纹要把"服务器上选的那几份"一起算进去：只按上传文件算的话，
// 换了服务器素材却仍"复用刚体检过的这一份"，练出来的就不是用户以为的那批。
const rvcFileSig = () => [...(($("rvcTrainFiles") || {}).files || [])]
  .map(f => f.name + ":" + f.size).concat(rvcTrainSrcIds()).join("|");

function rvcCheckText(rep) {
  const min = (rep.total_sec || 0) / 60;
  const pct = v => (v == null ? "?" : Math.round(v * 100) + "%");
  const sr = (rep.sample_rates || []).join("/") || "?";
  const ca = rep.clean_advice;
  const caCn = { light: "轻", medium: "中", off: "不净化" }[(ca || {}).tier] || "?";
  let s = `样本 ${rep.files} 个 · 共 ${min.toFixed(1)} 分钟 · 采样率 ${sr} sr · `
        + `静音占 ${pct(rep.silence_ratio)} · 人声响度约 ${rep.voiced_dbfs == null ? "?" : rep.voiced_dbfs} dBFS · `
        + `底噪约 ${rep.noise_floor_dbfs == null ? "?" : rep.noise_floor_dbfs} dBFS · 爆音 ${pct(rep.clipped_ratio)}\n`
        + `净化建议：${caCn}${ca && ca.reasons && ca.reasons.length ? " —— " + ca.reasons.join("；") : ""}\n`
        + `建议轮数：${rep.suggest_epochs} 档 —— ${rep.advice}\n`
        + `${rep.est_min ? `耗时预估：训练那一步约 ${Math.round(rep.est_min)} 分钟（${rep.pace_note}）`
                        : `耗时预估：${rep.pace_note}`}`;
  (rep.warnings || []).forEach(w => { s += "\n⚠ " + w; });
  return s;
}

// 用户手动改过净化档位后，体检建议就不再自动覆盖他的选择
$("rvcTrainClean").onchange = () => { $("rvcTrainClean").dataset.touched = "1"; };

$$("[data-epochs]").forEach(b => {
  b.onclick = () => { $("rvcTrainEpochs").value = b.dataset.epochs; };
});

$("rvcTrainCheck").onclick = async () => {
  const files = $("rvcTrainFiles").files;
  const srcIds = rvcTrainSrcIds();
  if ((!files || !files.length) && !srcIds.length) {
    // 与"开始制作音色"那条同一个出口：写状态 + 把视线带到该操作的地方。
    // 以前这里只写一行字就走，用户在长页面里看到红字，却不知道去哪儿补。
    fieldProblem($("rvcTrainDrop") || $("rvcTrainFiles"), $("rvcTrainStatus"),
      "体检要有素材：上传样本文件，或在下面「服务器素材」里勾选几段（⑩ 分离出的人声最直接）。");
    return;
  }
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  if (srcIds.length) fd.append("source_ids", srcIds.join(","));
  const box = $("rvcTrainCheckReport");
  $("rvcTrainCheck").disabled = true;
  box.style.display = "block";
  box.textContent = "上传并体检中…（只读音频头部与波形，不训练，几十秒到一两分钟）";
  try {
    const r = await fetch("/api/rvc/train/check", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "体检失败");
    rvcChecked = { id: j.id, sig: rvcFileSig() };
    box.textContent = rvcCheckText(j.report);
    if (j.report.suggest_epochs) {
      $("rvcTrainEpochs").value = j.report.suggest_epochs;
      toast(`体检完成，轮数已设为 ${j.report.suggest_epochs} 档`, "ok");
    }
    if (j.report.clean_advice && !$("rvcTrainClean").dataset.touched
        && $("rvcTrainClean").value !== j.report.clean_advice.tier) {
      $("rvcTrainClean").value = j.report.clean_advice.tier;
      const t = { light: "轻", medium: "中", off: "不净化" }[j.report.clean_advice.tier];
      toast(`体检按底噪把净化档位定为「${t}」`, "ok");
    }
    $("rvcTrainStatus").className = "status";
    $("rvcTrainStatus").textContent = "体检完成：确认没问题就点「开始制作音色」（素材已在服务器，不会重传）。";
  } catch (e) {
    box.textContent = "体检失败：" + friendlyErr(e);
  } finally { $("rvcTrainCheck").disabled = false; }
};

$("rvcTrainGo").onclick = async () => {
  if (!engineGate($("rvcTrainStatus"))) return;
  const name = $("rvcTrainName").value.trim();
  const files = $("rvcTrainFiles").files;
  const srcIds = rvcTrainSrcIds();
  if (!name) { fieldProblem($("rvcTrainName"), $("rvcTrainStatus"), "请先填写音色名称（英文/拼音）。"); return; }
  if ((!files || !files.length) && !srcIds.length) {
    fieldProblem($("rvcTrainDrop") || $("rvcTrainFiles"), $("rvcTrainStatus"),
      "请至少给一段人声样本：上传文件，或在下面「服务器素材」里勾选几段（干声最佳）。");
    return;
  }
  const fd = new FormData();
  const reuse = rvcChecked.id && rvcChecked.sig === rvcFileSig();
  if (reuse) fd.append("dataset_id", rvcChecked.id);
  else {
    for (const f of files) fd.append("files", f);
    if (srcIds.length) fd.append("source_ids", srcIds.join(","));
  }
  fd.append("name", name);
  const ep = parseInt($("rvcTrainEpochs").value || "200", 10);
  if (!(ep >= 1 && ep <= 1200)) {
    fieldProblem($("rvcTrainEpochs"), $("rvcTrainStatus"), "训练轮数须在 1-1200 之间（30 试听 / 100 常用 / 200 精训）");
    return;
  }
  fd.append("epochs", String(ep));
  fd.append("separate_vocal", $("rvcTrainSep").checked ? "auto" : "off");
  fd.append("clean_tier", $("rvcTrainClean").value);
  $("rvcTrainGo").disabled = true;
  $("rvcTrainStatus").className = "status";
  $("rvcTrainStatus").textContent = ($("rvcTrainSep").checked
    ? "提交训练…（已勾选自动分离，训练前会先逐文件分离人声，耗时相应增加）"
    : "提交训练…") + ($("rvcTrainClean").value !== "off"
    ? `（含「${{ light: "轻", medium: "中" }[$("rvcTrainClean").value]}」档净化）` : "")
    + (reuse ? "（复用刚体检过的素材，不再重传）" : "");
  try {
    const r = await fetch("/api/rvc/train", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "提交失败");
    rvcTrainJobs.unshift({ id: j.id, name: j.name, status: "pending", step: "排队中", samples: files.length, epochs: $("rvcTrainEpochs").value });
    renderRvcTrainList();
    $("rvcTrainStatus").textContent = "已提交，训练排队中（独占 GPU，期间不要同时生成歌曲）。";
    pollRvcTrain(j.id);
  } catch (e) {
    $("rvcTrainStatus").className = "status err";
    $("rvcTrainStatus").textContent = "提交失败：" + friendlyErr(e);
    // 服务端说这份素材目录没了（多半被清理），指纹跟着作废，下一次自动回到重传
    if (/体检记录/.test(e.message)) rvcChecked = { id: "", sig: "" };
  } finally { $("rvcTrainGo").disabled = false; }
};

// ================= v2.0 · 跨页共享 + 创作五页（①②③④⑤） =================

// ---------- 页面独立性（v10.12 起的硬规矩）----------
// 每个页面都必须能单独使用：输入只来自①用户自己填的、②服务器上的库（乐谱库/音色库/历史/
// 分离产物）、③本页的运行状态。不存在"把 A 页的数据自动填进 B 页"这条路——要带过去就复制。
//
// 以前这里有一个 LabStore 跨页 store，实测下来它写的字段几乎没人读（唯一真实用途是用别的页
// 留下的 scoreId 去预选 ⑤ 的乐谱下拉），删除。②⑤ 共用同一份表单 DOM、③⑤ 共用同一个谱框
// （switchTab 里 moveInto 搬节点），这是"同一页的不同视图"，不是跨页搬运：用户在哪儿改的都是
// 同一个控件，没有第二份会漂移的副本。

// ---------- 统一轮询器 ----------
// 新增的长任务（⑩ 分离、⑫ 模型修复）一律走这里，别再抄第三份、第四份。
// 老的三份（pollJob / pollRvc / pollRvcTrain）行为已被真实长跑验证过，本次不并轨：
// 动它们等于拿"生成中断点续看"和"换声排队位次"的既有正确性冒险。
const POLLERS = new Map();
const pollKey = (kind, id) => kind + ":" + id;
function stopPoller(kind, id) {
  const k = pollKey(kind, id), h = POLLERS.get(k);
  if (!h) return;
  POLLERS.delete(k);
  if (h.timer) clearTimeout(h.timer);
}
function isPolling(kind, id) { return POLLERS.has(pollKey(kind, id)); }
// opts: {url, interval=4000, missMax=5, maxMs, onState(j)->true 表示已结束, onDone(j), onGiveUp(err)}
// 连不上 ≠ 失败：单次 502 / 网络抖动会让用户白等一次 GPU 时间，miss 计数到上限才认输。
function startPoller(kind, id, opts) {
  const { url, interval = 4000, missMax = 5, maxMs = 40 * 60 * 1000, onState, onDone, onGiveUp } = opts || {};
  stopPoller(kind, id);
  const k = pollKey(kind, id);
  const h = { miss: 0, startedAt: Date.now(), timer: null };
  POLLERS.set(k, h);
  const loop = async () => {
    if (!POLLERS.has(k)) return;
    let j = null, ok = false;
    try {
      const r = await fetchT(url, {}, 20000);
      ok = r.ok;
      j = await r.json().catch(() => null);
      if (!ok) { const d = j && (j.detail || j.error); h.lastError = d || ("HTTP " + r.status); }
    } catch (e) { j = null; h.lastError = friendlyErr(e); }
    if (!POLLERS.has(k)) return;                       // 等待期间被别的页取消了
    h.miss = (ok && j) ? 0 : h.miss + 1;
    const finish = (fn, arg) => { POLLERS.delete(k); if (fn) fn(arg); };
    if (ok && j) {
      let done = false;
      try { done = !!onState && await onState(j); } catch (e) { console.warn(e); }
      if (done) { finish(onDone, j); return; }
    } else if (h.miss >= missMax) {
      finish(onGiveUp, new Error("连续 " + h.miss + " 次查询不到任务" + (h.lastError ? "（" + h.lastError + "）" : "")));
      return;
    }
    if (Date.now() - h.startedAt > maxMs) {
      finish(onGiveUp, new Error("等待超时（超过 " + Math.round(maxMs / 60000) + " 分钟）"));
      return;
    }
    h.timer = setTimeout(loop, interval);
  };
  loop();
  return k;
}

// ---------- ① Style 设计：产出条 / 配方摘要 / 发送 ----------
function styleText() { return (($("styleAsm") || {}).value || "").trim(); }
function findPreset(id) {
  if (!id) return null;
  for (const f of FAMS) { const p = (f.items || []).find(x => x.id === id); if (p) return { fam: f, p }; }
  return null;
}
// 人话摘要：让用户在点"发送"之前确认自己调出来的到底是什么
function updateStyleRecipe() {
  const el = $("styleRecipe");
  if (!el) return;
  const zh = (w) => W_ZH[w] || w;
  const bits = [];
  const langVal = (($("aLang") || {}).value || "").trim();
  const langZh = (LANGS.find(l => l[0] === langVal) || [])[1];
  if (langZh) bits.push(langZh);
  const hit = findPreset(asm.preset);
  if (hit) bits.push(hit.fam.zh + " · " + hit.p.zh);
  const voiceZh = (VOICES.find(v => v[0] === (($("gender") || {}).value || "")) || [])[1];
  if (voiceZh) bits.push(voiceZh);
  if (asm.mood.length) bits.push(asm.mood.map(zh).slice(0, 3).join("/"));
  if (asm.inst.length) bits.push(asm.inst.map(zh).slice(0, 3).join("+"));
  const bpm = parseInt(($("bpm") || {}).value, 10);
  if (bpm >= 40 && bpm <= 220) bits.push(bpm + " BPM");
  el.textContent = bits.length ? "🧾 当前配方：" + bits.join(" · ") : "";
}
function syncStylePreview() {
  // 曲风来源条已取消：②⑤ 有自己的一份曲风框（以前是搬 ① 的 #styleBox，现在各页各一份），
  // 不需要再把同一段文本复述一遍。这里只剩"模型读数 + 批次摘要"要跟着刷。
  const mn = $("modelNow");
  if (mn) {
    const sel = $("modelSel");
    const m = currentModels.find(x => x.path === (sel && sel.value));
    mn.textContent = m ? (m.quant + " · " + m.size_gb + " GB") : ((sel && sel.value) ? "已选" : "未检测到模型");
  }
  if (typeof updateBatchSummary === "function") updateBatchSummary();
}
$("styleCopy").onclick = async () => {
  const t = styleText();
  if (!t) { toast("产出框是空的", "err"); return; }
  try { await navigator.clipboard.writeText(t); toast("Style 已复制（" + t.length + " 字符）", "ok"); }
  catch { const ta = $("styleAsm"); ta.select(); document.execCommand("copy"); toast("已复制（剪贴板 API 不可用，走了选中复制）"); }
};
$("styleToSing").onclick = () => sendTextToTab(styleText(), "style", "sing", "Style", "② 的曲风框");
$("styleToScoreSing").onclick = () => sendTextToTab(styleText(), "style", "scoreSing", "Style", "⑤ 的曲风框");
// ① 右栏「配方库 / 装配台」二选一。
// 两个视图共用同一批 DOM id（famTabs / presetGrid / aLang / moodChips …），只是 hide 掉一个，
// 所以 JS 里所有装配逻辑、渲染函数一行都不用改——隐藏视图里的节点照样在，照样被初始化。
// 唯一要小心的是"进页面时哪个视图在"，它决定要不要把滚动位置归零。
function setAsmMode(mode, opts) {
  const isPreset = mode !== "six";
  const vp = $("asmPresetView"), vs = $("asmSixView");
  if (vp) vp.hidden = !isPreset;
  if (vs) vs.hidden = isPreset;
  const bp = $("segPreset"), bs = $("segSix");
  if (bp) { bp.classList.toggle("active", isPreset); bp.setAttribute("aria-selected", String(isPreset)); }
  if (bs) { bs.classList.toggle("active", !isPreset); bs.setAttribute("aria-selected", String(!isPreset)); }
  asmMode = isPreset ? "preset" : "six";
  try { localStorage.setItem(LS_ASM_MODE, asmMode); } catch {}
  if (!opts || !opts.silent) {
    // 切视图等于换了一屏内容，停在上一屏的滚动位置会直接落进空白
    const body = (vp && vs) ? (isPreset ? vp : vs).closest(".pane-body") : null;
    if (body) body.scrollTop = 0;
  }
}
$("segPreset").onclick = () => setAsmMode("preset");
$("segSix").onclick = () => setAsmMode("six");
$("toSettingsModel").onclick = () => switchTab("settings");

// ---------- ② 批次：连发与队列是两件事，摘要写在折叠标题上 ----------
function updateBatchSummary() {
  const sum = $("batchSum");
  if (sum) {
    const n = parseInt(($("genCount") || {}).value, 10) || 1;
    sum.textContent = "连发 " + Math.max(1, Math.min(20, n)) + " 首 · 队列走后台";
  }
  const go = $("go");
  if (go) {
    const n = Math.max(1, Math.min(20, parseInt(($("genCount") || {}).value, 10) || 1));
    const label = curTab === "scoreSing" ? "按谱生成" : "生成歌曲";
    go.textContent = n > 1 ? label + " ×" + n : label;
  }
}
$("genCount").addEventListener("input", () => { updateBatchSummary(); });
$("queue").addEventListener("click", () => { $("batchMini").textContent = "队列已提交：去 ⑪ 任务管理看位次与「一键继续」。"; });

// ---------- ③ 上传：拖拽与转谱锁 ----------
// 转谱在服务端有串行锁（并发两份会互相覆盖输出目录），所以提交后必须锁住上传入口。
function setScoreLock(on) {
  const go = $("extractAbc"), drop = $("abcDrop"), box = $("abcAudio");
  if (go) go.disabled = on;
  if (box) box.disabled = on;
  if ($("abcPick")) $("abcPick").disabled = on;
  if (drop) drop.classList.toggle("on", on);
}
function bindDropZone(zoneId, inputId, onFiles) {
  const zone = $(zoneId), input = $(inputId);
  if (!zone || !input) return;
  zone.addEventListener("click", () => { if (!input.disabled) input.click(); });
  // 点选文件走的是 input 的 change；以前只有拖拽路径调用 onFiles，
  // 于是"点虚线框→选文件"没有任何反应（2026-10-22 用户实测）。
  input.addEventListener("change", () => { if (onFiles) onFiles(input.files[0]); });
  zone.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && !input.disabled) { e.preventDefault(); input.click(); } });
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); if (!input.disabled) zone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("over"); }));
  zone.addEventListener("drop", (e) => {
    if (input.disabled) return;
    const dropped = (e.dataTransfer && e.dataTransfer.files) || [];
    if (!dropped.length) return;
    const dt = new DataTransfer();
    // input.multiple 时要把整拖进来的都收下（⑦ 的训练样本一次拖 5 段是常态），
    // 只取 files[0] 会让用户以为"拖了 5 段"，实际只交了 1 段去体检。
    if (input.multiple) { for (const f of dropped) dt.items.add(f); }
    else dt.items.add(dropped[0]);
    input.files = dt.files;
    if (onFiles) onFiles(input.files[0]); else input.dispatchEvent(new Event("change"));
  });
}
bindDropZone("abcDrop", "abcAudio");
// ⑨ 与 ⑦ 的上传口：以前直接裸露原生 file 控件，现在是同一套虚线区。
// 点选和拖拽都要把文件名报到 *Now 行，否则用户选完文件看不到任何反馈。
bindDropZone("rvcDrop", "rvcFile", () => {
  const f = ($("rvcFile").files || [])[0];
  $("rvcFileNow").textContent = f ? "已选：" + f.name + " · " + fmtMb(f.size) : "";
});
bindDropZone("rvcTrainDrop", "rvcTrainFiles", () => {
  const fs = [...(($("rvcTrainFiles").files) || [])];
  $("rvcTrainFilesNow").textContent = fs.length
    ? "已选 " + fs.length + " 个文件：" + fs.map(f => f.name).join("、").slice(0, 160)
    : "";
});
$("abcCopy").onclick = async () => {
  const v = $("abc").value.trim();
  if (!v) { toast("谱框是空的", "err"); return; }
  try { await navigator.clipboard.writeText(v); toast("ABC 源码已复制", "ok"); } catch { $("abc").select(); document.execCommand("copy"); toast("已复制"); }
};
$("abcToEdit").onclick = () => sendTextToTab($("abc").value, "seSrc", "scoreEdit", "乐谱", "④ 的原谱框");
$("abcToScoreSing").onclick = () => gotoSharedScore();
$("abcRetryPure").onclick = () => {
  $("abcChords").checked = false;
  $("abcRetryWrap").hidden = true;
  $("abcStatus").textContent = "已改为纯旋律，请重新点选歌曲（带和弦里认不出的和弦性质会让整份谱失败）";
  $("abcStatus").style.color = "var(--muted)";
  $("abcAudio").click();
};
// 导出：mid 由转谱时归档，abc 直接给源码
// 这是 ③ 自己的运行状态（本页正在摆弄哪一份谱），不是跨页状态：别的页面看不到也不需要知道。
let scoreCurrentId = null;
async function exportScore(fmt) {
  const st = $("abcIoStatus");
  const id = scoreCurrentId;
  if (!id) { st.textContent = "还没有可导出的乐谱：先在左边转一份谱，或从乐谱库点一行载入。"; return; }
  st.textContent = "导出中…";
  try {
    const r = await fetchT("/api/scores/" + encodeURIComponent(id) + "/export?format=" + encodeURIComponent(fmt));
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new Error(j.detail || j.error || ("HTTP " + r.status));
    }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (fmt === "mid" ? "score-" + id + ".mid" : "score-" + id + ".abc");
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    st.textContent = "✓ 已下载 " + a.download;
  } catch (e) {
    st.textContent = "✕ 导出失败：" + friendlyErr(e) + "（MIDI 只在转谱成功时才归档；手填的谱没有 MIDI 可导）";
  }
}
// ③ 页不再放 MIDI / ABC 下载按钮（谱就在框里，复制即可；MIDI 也不是本工作台任何一页的输入）。
// exportScore 保留给 ④ 乐谱加工（加工结果存进乐谱库后按 score_id 导出 .abc）。

// ---------- ⑦ 乐谱来源三选 + 填词助手 ----------
async function loadScorePicker() {
  const sel = $("scorePick");
  if (!sel) return;
  try {
    const list = await (await fetchT("/api/scores", {}, 15000)).json();
    sel.innerHTML = "";
    if (!list.length) {
      const o = document.createElement("option"); o.value = ""; o.textContent = "乐谱库为空（去 ③ 转一份）";
      sel.appendChild(o); return;
    }
    list.forEach((s) => {
      const o = document.createElement("option");
      o.value = s.id;
      o.textContent = (s.title ? s.title + " · " : "") + (s.time || s.id) + (s.cot ? " [" + s.cot + "]" : "") + " · " + (s.abc_preview || "").replace(/\s+/g, " ").slice(0, 34);
      sel.appendChild(o);
    });
    // 不再按"别的页面留下的 scoreId"预选：那等于替用户做选择。默认停在列表第一条。
  } catch (e) { /* 拉不到列表只是不能"从库里选"，手填不受影响 */ }
}
$("scoreSrc").onchange = () => {
  const v = $("scoreSrc").value;
  $("scoreLibWrap").hidden = v !== "lib";
  $("scoreLoadBtn").hidden = v !== "lib";
  if (v === "lib") loadScorePicker();
  if (v === "manual" || v === "keep") $("abc").focus();
};
$("scoreLoadBtn").onclick = async () => {
  const id = $("scorePick").value;
  if (!id) { toast("乐谱库是空的", "err"); return; }
  try {
    const rec = await (await fetchT("/api/scores/" + encodeURIComponent(id), {}, 20000)).json();
    $("abc").value = rec.abc || "";
    const sug = rec.analysis && rec.analysis.cot_suggested;
    if (sug) $("cot").value = sug;
    syncGenPresetFromFields();
    if (typeof renderAbcAnalysis === "function") renderAbcAnalysis(rec.analysis);
    if (typeof analyzeAbcDebounced === "function") analyzeAbcDebounced();
    toast("已载入乐谱 " + id, "ok");
    updateLyricBarHint();
  } catch (e) { toast("载入失败：" + friendlyErr(e), "err"); }
};
$("scoreClear").onclick = () => {
  if (!confirm("清空乐谱？清空后 ⑤ 就等于 ②：旋律由模型自己规划。")) return;
  $("abc").value = "";
  if (typeof renderAbcAnalysis === "function") renderAbcAnalysis(null);
  $("abcAnalysis").textContent = "";
  updateLyricBarHint();
};
$("scoreFromAudio").onclick = () => switchTab("score");
// 让引擎只跑到 ABC 规划：几秒看到"模型打算这么写"，改完再花几十分钟出音频。
// 后端是直连引擎的（内核的 GenerateJSON 没有 stop_after 字段，从网关塞会被静默丢掉）。
function planAbcSay(msg, cls) {
  const el = $("planAbcStatus");
  if (!el) return;
  el.className = "lint" + (cls ? " " + cls : "");
  el.textContent = msg;
}
$("planAbcBtn").onclick = async () => {
  const style = $("style").value.trim();
  if (!style) { planAbcSay("至少要填 Style：引擎规划谱也要知道写成什么样", "err"); return; }
  if (($("abc").value || "").trim() &&
      !confirm("谱框里已经有一份谱了，让引擎规划的结果把它覆盖掉？")) { planAbcSay("没动：你取消了", ""); return; }
  const body = { style, lyrics: $("lyrics").value.trim(), cot: $("cot").value,
                 seed: Number($("seed").value) };
  for (const k of ["abc_temperature", "abc_top_p", "abc_top_k"]) {
    const el = $(k);
    const v = el ? String(el.value || "").trim() : "";
    if (v) body[k] = Number(v);
  }
  const btn = $("planAbcBtn");
  btn.disabled = true;
  planAbcSay("正在让引擎规划谱（只跑 ABC，不渲染音频；本机 6GB 卡约 8 秒，排队时更久）…", "");
  try {
    const r = await jfetch("/api/generate/plan_abc", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail && j.detail.message ? j.detail.message : JSON.stringify(j.detail || ("HTTP " + r.status)));
    $("abc").value = j.abc || "";
    if (typeof analyzeAbcDebounced === "function") analyzeAbcDebounced();
    if (typeof updateLyricBarHint === "function") updateLyricBarHint();
    planAbcSay(j.message + "（seed " + j.seed + "）——改完这份谱再点生成，就是按它唱", "ok");
  } catch (err) {
    planAbcSay("规划失败：" + friendlyErr(err), "err");
  } finally { btn.disabled = false; }
};
// 填词助手：每段行数对不上谱的段落数/小节数时提前警告，别等生成 5 分钟才发现唱不下
function updateLyricBarHint() {
  const el = $("lyricBarWarn");
  if (!el || curTab !== "scoreSing") return;
  const abc = ($("abc") || {}).value || "";
  const lines = (($("lyrics") || {}).value || "").split(/\n+/).map(s => s.trim()).filter(Boolean);
  if (!abc.trim()) {
    el.hidden = false;
    el.innerHTML = "⚠ 还没有乐谱：⑤ 是「按谱演唱」，谱空着等于回到 ② 让模型自己编旋律。";
    return;
  }
  const bars = (abc.match(/\|/g) || []).length;
  const words = lines.filter(l => !/^\[.*\]$/.test(l)).length;
  const notes = (abc.match(/[a-gA-G]/g) || []).length;
  const msgs = [];
  if (bars && words && bars < words) msgs.push("谱只有 " + bars + " 小节，歌词却有 " + words + " 行 —— 词多半唱不下，会被挤掉或糊在一起");
  if (bars && words && bars > words * 3) msgs.push("谱有 " + bars + " 小节、词只有 " + words + " 行 —— 大量小节没人声，可能出器乐段");
  if (!notes) msgs.push("谱里没解析到音符");
  el.hidden = !msgs.length;
  el.innerHTML = msgs.length ? "⚠ " + msgs.join("；") + "（这是轻量提示，不拦提交）" : "";
}
$("lyrics").addEventListener("input", () => { if (curTab === "scoreSing") updateLyricBarHint(); });

// ---------- 状态摘要条（②⑤ 右栏顶部的"我填了什么"） ----------
function paintScoreModeTag() {
  const tag = $("scoreModeTag");
  if (!tag) return;
  const abc = (($("abc") || {}).value || "").trim();
  if (!abc) { tag.textContent = "无谱 · 模型自己编旋律"; return; }
  const chords = lastAbcAnalysis && lastAbcAnalysis.chord_count;
  tag.textContent = "有谱 · " + (chords ? "带和弦（走 full）" : "纯旋律（走 melody）");
}

// ================= v2.0 · ④ 乐谱加工 =================
// 这一页没有任务、没有进度、没有 GPU：改一个参数就打一次 /api/abc/edit，同步返回新谱。
// 所以"预览"和"结果"是同一个东西——右边的参数就是全部输入，左边只负责显示与带走。
const SE_TRANS_CHIPS = [
  ["−12", -12, "降一个八度"],
  ["−7", -7, "女声谱给男声：低五度"],
  ["−5", -5, "女声谱给男声：低四度"],
  ["0", 0, "不改音高"],
  ["+5", 5, "男声谱给女声：高四度"],
  ["+7", 7, "男声谱给女声：高五度"],
  ["+12", 12, "升一个八度"],
];
let seTimer = null, seResult = null, seScoreId = null, seBusy = false;
function sePaintChips() {
  const box = $("seTransChips"); if (!box) return;
  const v = parseInt($("seTrans").value, 10) || 0;
  box.innerHTML = "";
  SE_TRANS_CHIPS.forEach(([label, n, tip]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip mini" + (n === v ? " active" : "");
    b.textContent = label;
    b.title = tip;
    b.onclick = () => { $("seTrans").value = String(n); seSyncRange(); sePaintChips(); seRun(); };
    box.appendChild(b);
  });
}
function seSyncRange() {
  const n = parseInt($("seTrans").value, 10) || 0;
  const r = $("seTransRange");
  if (r) r.value = String(Math.max(-12, Math.min(12, n)));
}
// 源谱现在是大调还是小调：只从 K: 头里读，不调接口（判定"同名/关系"够用了）。
// 读不出来时不猜，全部选项照常可用——选错了服务端也不会崩，只是改不出想要的效果。
function seKeyMode(abc) {
  // 只吃本行：`[ \t]*` 而不是 `\s*`——\s 会吞掉换行，把空 K: 行的下一行音符当成调名
  const m = /^[ \t]*K:[ \t]*([^\n\r]*)/m.exec(abc || "");
  if (!m) return null;
  // 先切出"音名 + 后缀"再判，别在整行上找 m：
  // 旧写法 /m(\b|in|aj|$)/ 会把 K:Gmaj 判成小调（m+aj 命中），
  // 于是"大调→小调"两项被禁用，用户以为页面做不了；K:Gmix 反而是靠运气对的。
  const t = /^([A-G])[ \t]*([#b]?)[ \t]*([^\s]*)/i.exec(m[1].trim());
  if (!t) return null;
  return /^(m|min|minor)\b/i.test(t[3]) ? "minor" : "major";
}
function seSyncModeOptions() {
  const sel = $("seMode"), hint = $("seModeHint");
  if (!sel) return;
  const abc = ($("seSrc").value || "").trim();
  const mode = abc ? seKeyMode(abc) : null;
  const want = mode === "minor" ? ["keep", "minor_to_major", "minor_to_relative_major"]
             : mode === "major" ? ["keep", "major_to_minor", "major_to_relative_minor"]
             : null;
  const cur = sel.value;
  Array.from(sel.options).forEach(o => {
    if (!want) { o.disabled = false; return; }
    // 不匹配源调式的两项留着但禁用：让用户看见"还有这两种"，而不是以为页面少做了东西
    o.disabled = want.indexOf(o.value) < 0;
  });
  if (cur && Array.from(sel.options).some(o => o.value === cur && !o.disabled)) sel.value = cur;
  else sel.value = "keep";
  if (hint) {
    hint.textContent = abc
      ? (mode ? ("检测到源谱调号是" + (mode === "minor" ? "小调" : "大调") + "，只能选与之匹配的两项（灰掉的那两项对这份谱没有意义）。")
              : "没读出调号（K: 行缺失），所有调式项都先放开。")
      : "先在左边放一份谱，这里的选项才会按源调式收窄。";
  }
}
async function seRun(opts) {
  const src = ($("seSrc").value || "").trim();
  const out = $("seOut"), chg = $("seChanged"), ana = $("seAnalysis");
  if (!src) {
    seResult = null;
    if (out) out.value = "";
    if (chg) { chg.className = "lint"; chg.textContent = "先在上面放一份谱，再到右边选加工项。"; }
    if (ana) ana.textContent = "";
    seSyncModeOptions();
    return;
  }
  const tempo = parseInt($("seTempo").value, 10);
  const scaleRaw = parseFloat($("seTempoScale").value);
  const payload = {
    abc: src,
    transpose: parseInt($("seTrans").value, 10) || 0,
    mode: $("seMode").value || "keep",
    strip_chords: !!$("seStrip").checked,
    tempo: (isNaN(tempo) ? null : tempo),
    tempo_scale: (isNaN(scaleRaw) ? null : scaleRaw),
  };
  if (opts && opts.save) payload.save = true;
  seBusy = true;
  try {
    const r = await fetch("/api/abc/edit", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || j.error || ("HTTP " + r.status));
    seResult = j.abc || "";
    if (j.score_id) { seScoreId = j.score_id; }
    if (out) out.value = j.abc || "";
    if (chg) {
      const n = (j.changed || []).length;
      if (j.mode_noop && n === 1) {
        // 调式操作对这份谱无效（源调式已达标）：别打绿勾说"已加工"，
        // 那会让人以为谱子变了；用中性色如实说明。
        chg.className = "lint";
        chg.textContent = "ⓘ " + j.changed[0] + (j.key ? "　·　当前调号 " + j.key : "");
      } else {
        chg.className = "lint" + (n ? " ok" : "");
        chg.textContent = n ? ("✓ 已加工：" + j.changed.join("；") + (j.key ? "　·　新调号 " + j.key : ""))
                            : "没有勾选任何加工项，结果与源谱一致。";
      }
    }
    const a = j.analysis || {};
    if (ana) {
      const bits = [];
      if (a.key) bits.push("调性 " + a.key);
      if (a.meter) bits.push("拍号 " + a.meter);
      if (a.bpm) bits.push(a.bpm + " BPM");
      if (a.range) bits.push("音域 " + a.range);
      ana.textContent = bits.length ? "加工后：" + bits.join(" · ") : "";
    }
    if (opts && opts.save) {
      toast("已存进乐谱库：" + seScoreId, "ok");
      $("seFootNote").textContent = "已另存为 " + seScoreId + "（可在 ⑤ 的乐谱库里挑到；原谱没有被覆盖）。";
    }
  } catch (e) {
    if (chg) { chg.className = "lint err"; chg.textContent = "✕ 加工失败：" + friendlyErr(e); }
  } finally { seBusy = false; }
}
function seRunDebounced() {
  clearTimeout(seTimer);
  seTimer = setTimeout(() => { seSyncModeOptions(); seRun(); }, 260);
}
$("seSrc").addEventListener("input", () => { seScoreId = null; $("seFootNote").textContent = "加工结果只在这里和乐谱库里，原谱不会被覆盖。"; seRunDebounced(); });
["seTrans", "seTempo", "seTempoScale"].forEach(id => $(id).addEventListener("input", () => { seSyncRange(); sePaintChips(); seRunDebounced(); }));
$("seTransRange").addEventListener("input", () => { $("seTrans").value = $("seTransRange").value; sePaintChips(); seRunDebounced(); });
$("seMode").addEventListener("change", () => seRun());
$("seStrip").addEventListener("change", () => seRun());
$("seClear").onclick = () => {
  $("seSrc").value = ""; $("seTrans").value = "0"; $("seMode").value = "keep";
  $("seTempo").value = ""; $("seTempoScale").value = ""; $("seStrip").checked = false;
  seScoreId = null;
  seSyncRange(); sePaintChips(); seSyncModeOptions(); seRun();
};
// 「↩ 用 ③ 当前的谱」已删：那是读另一个页面的谱框。③ 每次转谱都自动落进乐谱库，
// 本页的「📁 从乐谱库载入」和「📋 粘贴」就是自己的两个入口。
$("seFromLib").onclick = async () => {
  const sel = $("seLibPick");
  if (sel && !sel.hidden && sel.options.length) { sel.hidden = false; sel.focus(); return; }
  try {
    // /api/scores 直接返回数组（与 ⑤ 的 loadScorePicker 同一个接口、同一种读法）
    const j = await (await fetchT("/api/scores", {}, 15000)).json();
    const list = Array.isArray(j) ? j : [];
    if (!list.length) { toast("乐谱库是空的：先去 ③ 转一份谱", "err"); return; }
    sel.innerHTML = "";
    list.forEach(it => {
      const o = document.createElement("option");
      o.value = it.id;
      o.textContent = (it.time || it.id) + (it.cot ? " [" + it.cot + "]" : "") + " · " +
                      String(it.abc_preview || "").replace(/\s+/g, " ").slice(0, 34);
      sel.appendChild(o);
    });
    sel.hidden = false;
    toast("选一份乐谱载入", "ok");
  } catch (e) { toast("读取乐谱库失败：" + friendlyErr(e), "err"); }
};
$("seLibPick").onchange = async () => {
  const id = $("seLibPick").value;
  if (!id) return;
  try {
    const rec = await (await fetchT("/api/scores/" + encodeURIComponent(id), {}, 20000)).json();
    $("seSrc").value = rec.abc || "";
    seScoreId = null;
    seSyncModeOptions(); seRun();
    toast("已载入乐谱 " + id, "ok");
  } catch (e) { toast("载入失败：" + friendlyErr(e), "err"); }
};
$("sePasteClip").onclick = async () => {
  try {
    const t = await navigator.clipboard.readText();
    if (!t) { toast("剪贴板是空的", "err"); return; }
    $("seSrc").value = t; seScoreId = null; seSyncModeOptions(); seRun();
  } catch { toast("读不了剪贴板：请直接点上面的框 Ctrl+V", "err"); $("seSrc").focus(); }
};
$("seCopy").onclick = async () => {
  const t = $("seOut").value;
  if (!t) { toast("还没有加工结果", "err"); return; }
  try { await navigator.clipboard.writeText(t); toast("已复制加工后的谱（" + t.length + " 字符）", "ok"); }
  catch { $("seOut").select(); document.execCommand("copy"); toast("已复制（剪贴板 API 不可用，走了选中复制）"); }
};
$("seToScoreSing").onclick = () => sendTextToTab($("seOut").value, "abc", "scoreSing", "加工结果谱", "⑤ 的 ABC 谱框");
$("seSave").onclick = () => {
  if (!$("seSrc").value.trim()) { toast("原谱是空的", "err"); return; }
  seRun({ save: true });
};
$("seDownload").onclick = () => {
  const t = $("seOut").value;
  if (!t) { toast("还没有加工结果", "err"); return; }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([t], { type: "text/plain;charset=utf-8" }));
  a.download = "score-edited.abc";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
};
// 「→ ⑤ 用这个谱生成」已删：那会把结果写进另一个页面的谱框再替用户切页。
// 现在只有「💾 存入乐谱库」这一条出路——存进库之后，⑤ 自己从库里挑，两份谱各自留痕。
sePaintChips(); seSyncRange(); seSyncModeOptions();
// ================= v2.0 · ⑫ 设置页 =================

// 侧栏搬来的三样（模型校验 / 引擎模式 / 主题色）现在住在 ⑫。
// 它们原来的回执写在 $("status")（② 的状态条）上：人在设置页切模型，却看不见任何回音。
// 两个地方一起说——② 那边仍是原行为，不设"只在设置页才写"的条件（那会让人以为没生效）。
function statusSay(msg, cls) {
  for (const id of ["status", "settingsStatus"]) {
    const el = $(id);
    if (!el) continue;
    el.className = "status" + (cls ? " " + cls : "");
    el.textContent = msg;
  }
}

// ---------- 系统信息（只读） ----------
let sysCache = null;
// 键必须写成 renderSys 实际产出的点号路径（walk 会把父级拼进前缀），
// 只写叶子名会一行都匹配不上，整页退化成英文裸键。
/* ★ 系统信息必须分组，不能 33 行平铺。
   实测（1920x1174）：33 行 × 29px ≈ 1021px 高，把「关于」页顶到 1264px ——
   视口只有 1174，于是出现滚动条（顺带把整页内容推偏 5px），
   而"33 行没有小标题的键值"本身也没法一眼扫完，找"引擎模式"要一行行读。
   分 4 组（端口 / 目录 / 存储与显存 / 引擎），每组一个 .kv-group + 小标题，
   再让分组本身走多列流式排布 —— 高度腰斩，且"我要看什么"变成了"我去哪一组找"。 */
const SYS_GROUPS = [
  { key: "port",  title: "端口与版本", match: (k) => /^(version|ports\.|gateway$|read_only|gateway_kernel\.|security\.)/.test(k) },
  { key: "path",  title: "目录",       match: (k) => /^paths\./.test(k) },
  { key: "disk",  title: "存储与显存", match: (k) => /^(disk\.|gpu\.|backend$)/.test(k) },
  { key: "eng",   title: "引擎与模型", match: (k) => /^(engines\.|audiocpp$)/.test(k) },
];
function sysGroupOf(k) {
  for (const g of SYS_GROUPS) if (g.match(k)) return g.key;
  return "port"; // 兜底：未定义的键归到第一组，不丢行
}
const SYS_LABEL = {
  version: "版本", read_only: "只读说明",
  // 网关走的是编译内核（pyd）还是开源 shim（main.py）——同一版本两台机器可能不同，
  // 能力也不同（pyd 不做字段白名单/丢键日志），所以必须能在⑬看见（issue #4-7）
  "gateway_kernel.kind": "网关内核", "gateway_kernel.path": "内核位置",
  "gateway_kernel.note": "内核说明",
  "security.lan_mode": "局域网模式", "security.direct_access_token": "直连令牌",
  "security.loopback_exempt": "回环免令牌",
  // 后三个是 /api/system/info 缺席时退回 /api/health 用的裸键
  gateway: "网关", backend: "引擎模式", audiocpp: "音频引擎",
  "ports.gateway": "网关端口", "ports.dsh": "局域网入口 (dsh)", "ports.audiocpp": "引擎端口",
  "ports.ports_file": "端口真源文件", "ports.ports_file_exists": "端口文件在位",
  "paths.root": "项目根目录", "paths.output_dir": "输出目录", "paths.runtime_dir": "运行目录",
  "paths.scores_dir": "乐谱目录", "paths.records_dir": "听歌记录目录",
  "paths.rvc_weights_dir": "RVC 音色目录", "paths.model_dir": "模型目录", "paths.log_dir": "日志目录",
  "disk.output_free_mb": "输出盘剩余", "disk.output_total_mb": "输出盘总量",
  "disk.output_files": "产物文件数", "disk.upload_floor_mb": "上传闸门",
  "gpu.mode": "引擎模式", "gpu.total_mb": "显存总量", "gpu.free_mb": "显存空闲",
  "gpu.used_mb": "显存已用", "gpu.headroom_mb": "显存留白", "gpu.query_failed": "显存查询",
  "engines.audiocpp_alive": "音频引擎在线", "engines.vocal_separation_ready": "人声分离可用",
  "engines.loaded_main": "当前主模型", "engines.loaded_vae": "当前 vae",
  "engines.loaded_lora": "当前 LoRA",
  "engines.sheetsage2_ready": "SheetSage2 权重",
  "engines.ai_workbench.ready": "AI 工作台就绪", "engines.ai_workbench.key_configured": "对话模型密钥",
  "engines.ai_workbench.runner_exists": "dsh 启动脚本", "engines.ai_workbench.model": "对话模型",
  "engines.ai_workbench.dsh_listening": "dsh 端口在听",
};
function fmtSysVal(k, v) {
  if (v && typeof v === "object") return JSON.stringify(v);
  // ★ 反向标签要翻着说：`gpu.query_failed` 为 false 是**好事**，
  //   但通用规则会打成"⛔ 否"，一眼看过去像出了故障（实测 ⑬ 就这一行红着，
  //   而同一屏的显存空闲 5.1GB 明明是查成功才有数）。这类键直接说结论。
  if (typeof v === "boolean" && /_failed$/.test(k)) return v ? "⛔ 失败" : "✅ 正常";
  // 安全项里 false 多半是“没开启”而不是故障，别用红叉吓人
  if (typeof v === "boolean" && /^security\./.test(k)) return v ? "✅ 开启" : "— 未开启";
  if (typeof v === "boolean") return v ? "✅ 是" : "⛔ 否";
  if (typeof v === "number" && /_mb$/.test(k)) return v >= 1024 ? (v / 1024).toFixed(1) + " GB" : v + " MB";
  return String(v);
}
function kvAdd(rows, k, v) {
  const d = document.createElement("div");
  d.dataset.grp = sysGroupOf(k);
  const kk = document.createElement("span");
  kk.className = "k";
  kk.textContent = SYS_LABEL[k] || SYS_LABEL[k.split(".").pop()] || k;
  const vv = document.createElement("span"); vv.className = "v";
  vv.textContent = fmtSysVal(k, v);
  d.appendChild(kk); d.appendChild(vv); rows.push(d);
}
function renderSys(j) {
  const box = $("sysInfo");
  if (!box) return;
  // ⑬ 右栏那句自我介绍以前写死 "v10.9.0"：左栏系统信息已经显示 v10.14.1，
  // 同一屏两个版本号，用户不知道该信哪个。改由同一份 payload 填，只有一个真源。
  const line = $("aboutLine");
  if (line && j && j.version) line.textContent = "音乐工作台 " + j.version + " · 13 页 · 全部跑在本机";
  const rows = [];
  const walk = (obj, prefix) => {
    Object.entries(obj || {}).forEach(([k, v]) => {
      if (v === null || v === undefined || v === "") return;
      if (typeof v === "object" && !Array.isArray(v)) walk(v, prefix + k + ".");
      else kvAdd(rows, prefix + k, Array.isArray(v) ? v.join("、") : v);
    });
  };
  walk(j, "");
  box.innerHTML = "";
  if (!rows.length) { box.innerHTML = '<div class="empty">没有读到信息。</div>'; return; }
  rows.forEach((r) => box.appendChild(r));
  groupSysRows(box);
}
/* 把平铺的键值行收进 4 个带小标题的分组。
   ★ 分组不能写死在 HTML 里：系统信息的键随后端版本增减，硬编码目录会在后端加字段时
     静默丢行（"我明明有 33 项，怎么只剩 31"）。按 key 前缀归类 + 兜底组，加字段自动有位。 */
function groupSysRows(box) {
  const buckets = new Map(SYS_GROUPS.map((g) => [g.key, []]));
  Array.prototype.slice.call(box.children).forEach((el) => {
    const g = el.dataset && el.dataset.grp;
    if (!g || !buckets.has(g)) return;
    buckets.get(g).push(el);
  });
  const frag = document.createDocumentFragment();
  SYS_GROUPS.forEach((g) => {
    const items = buckets.get(g.key);
    if (!items || !items.length) return;
    const sec = document.createElement("section");
    sec.className = "kv-group";
    const h = document.createElement("div");
    h.className = "kv-group-title";
    h.textContent = g.title;
    sec.appendChild(h);
    const body = document.createElement("div");
    body.className = "kv";
    items.forEach((el) => body.appendChild(el));
    sec.appendChild(body);
    frag.appendChild(sec);
  });
  box.innerHTML = "";
  box.appendChild(frag);
}
async function loadSysInfo() {
  const box = $("sysInfo");
  if (!box) return;
  try {
    const j = await (await fetchT("/api/system/info", {}, 15000)).json();
    sysCache = j;
    renderSys(j);
    return;
  } catch (e) {
    sysCache = null;
  }
  // /api/system/info 不在（旧网关没重启）时退回 /api/health：至少引擎状态是真的，
  // 并且要如实说明缺了什么，不能把半截数据摆成完整档案。
  try {
    const h = await (await fetchT("/api/health", {}, 8000)).json();
    const rows = [];
    kvAdd(rows, "gateway", h.gateway || "—");
    kvAdd(rows, "backend", (h.audiocpp || {}).backend || "—");
    kvAdd(rows, "audiocpp", (h.audiocpp || {}).status || "—");
    kvAdd(rows, "端口", "只读项需要 /api/system/info（改过 app.py 后必须重启 7863 才生效）");
    box.innerHTML = "";
    rows.forEach((r) => box.appendChild(r));
    box.insertAdjacentHTML("beforeend", '<div class="empty">⚠ 系统信息不完整：/api/system/info 还没生效（网关未重启？）</div>');
  } catch (e2) {
    // 不要把这句原始异常（"Unexpected token '<'... is not valid JSON"）直接摆给用户看：
    // 它唯一有意义的场景是"接口没接上"，说清这一件事就够了，技术细节收进 title。
    box.innerHTML = '<div class="empty" title="' + escapeHtml(e2.message) + '">✕ 暂时读不到系统信息：网关（7863）没连上，或该版本的 /api/system/info 还没生效。</div>';
  }
}
$("sysRefresh").onclick = () => { loadSysInfo(); refreshHealth(); refreshModelVerify(); };
$("setLogPath").onclick = async () => {
  const p = (sysCache && (sysCache.log_dir || (sysCache.paths && sysCache.paths.log_dir))) || "";
  if (!p) { toast("还没读到日志目录：先点「↻ 刷新」", "err"); return; }
  try { await navigator.clipboard.writeText(p); toast("日志目录已复制：" + p, "ok"); }
  catch { toast("复制失败，路径是：" + p, "err"); }
};
$("setClearHist").onclick = async () => {
  if (!confirm("清空任务历史？\n\n会删除服务端保存的历史任务记录与对应音频文件，且不可撤销。\n正在运行的任务不受影响（会返回 409）。")) return;
  $("setClearHist").disabled = true;
  try {
    const r = await jfetch("/api/history/clear", { method: "DELETE" });
    toast("已清空：" + ((r && (r.message || r.removed)) || "记录与音频已删除"), "ok");
    statusSay("任务历史已清空", "ok");
    renderHistory();
  } catch (e) { toast("清空失败：" + friendlyErr(e), "err"); }
  finally { $("setClearHist").disabled = false; }
};

// ---------- 模型文件清单 + 修复进度 ----------
async function loadModelFiles(pre) {
  const box = $("modelFiles");
  if (!box) return;
  try {
    const j = pre || await (await fetchT("/api/models/verify", {}, 30000)).json();
    const files = j.files || [];
    if (!files.length) { box.textContent = ""; return; }
    const bad = files.filter((f) => !f.ok);
    box.innerHTML = "";
    box.insertAdjacentHTML("beforeend",
      '<div style="margin-bottom:4px;">逐文件校验：<b>' + (j.ok ? "全部完整" : ("缺件 " + bad.length + " 项")) + "</b>（共 " + files.length + " 个文件）</div>");
    files.forEach((f) => {
      const d = document.createElement("div");
      d.style.cssText = "display:flex;gap:8px;align-items:baseline;padding:2px 0;";
      d.innerHTML = '<span style="flex:none;">' + (f.ok ? "✅" : "❌") + "</span>"
        + "<span>" + escapeHtml(f.name || f.path || "?") + "</span>"
        + (f.ok ? "" : '<span style="color:var(--err);">' + escapeHtml(f.reason || "不可用") + "</span>");
      box.appendChild(d);
    });
  } catch (e) { box.textContent = "校验读不到：" + friendlyErr(e); }
}
function renderRepair(j) {
  const bar = $("repairProgressBar"), txt = $("repairStatus");
  const files = (j && (j.files || j.total)) || 0;
  const done = (j && ((j.repaired || []).length + (j.failed || []).length)) || 0;
  if (bar) bar.style.width = (j && j.running && files) ? Math.round(done / files * 100) + "%" : (j && j.running ? "35%" : "100%");
  if (txt) {
    if (!j) { txt.textContent = ""; }
    else if (j.running) txt.textContent = "修复中：" + (j.message || "…") + (files ? "（" + done + "/" + files + "）" : "");
    else {
      const okn = (j.repaired || []).length, badn = (j.failed || []).length;
      txt.textContent = okn || badn
        ? (badn ? "✕ 修复结束：补回 " + okn + " 个，仍失败 " + badn + " 个（" + escapeHtml((j.message || "") + "") + "）"
               : "✓ 修复结束：补回 " + okn + " 个文件")
        : (j.message ? "修复状态：" + j.message : "");
      txt.className = "lint" + (badn ? " err" : "");
    }
  }
}
function watchRepair() {
  if (isPolling("repair", "state")) return;
  startPoller("repair", "state", {
    url: "/api/models/repair/status",
    interval: 3000,
    missMax: 8,
    maxMs: 90 * 60 * 1000,
    onState: (j) => { renderRepair(j); refreshModelVerify(); return !j.running; },
    onDone: () => { loadModelFiles(); loadSysInfo(); toast("修复任务已结束", "ok"); },
    onGiveUp: (e) => { $("repairStatus").textContent = "✕ 修改进度读不到了：" + friendlyErr(e); },
  });
}
$("modelVerifyNow").onclick = () => { refreshModelVerify(); loadModelFiles(); };
// ================= v2.0 · ⑩ 人声伴奏分离 =================
const SEP_PARTS = [
  { key: "vocals",           label: "人声（含和声）",  hint: "副歌有叠唱时里面混着和声；只想换音色用它" },
  { key: "vocals_noharmony", label: "人声（去和声）",  hint: "只留主唱，训练素材与干净干声用它" },
  { key: "accompaniment",    label: "伴奏",            hint: "无人声，转谱/做 instrumental/自己配人声用它" },
];
let sepCurrent = null;          // 本页正在盯的任务 id
let sepAssets = null;           // 当前任务的产物清单 {part: 文件名}

function sepSay(msg, cls) {
  const el = $("sepStatus");
  if (!el) return;
  el.className = "status" + (cls ? " " + cls : "");
  el.textContent = msg;
}
function sepSetRunning(on) {
  const go = $("sepGo"), drop = $("sepDrop"), box = $("sepFile");
  if (go) go.disabled = on;
  if (box) box.disabled = on;
  if (drop) drop.classList.toggle("on", on);
  const stop = $("sepStop");
  if (stop) stop.style.display = on ? "" : "none";
}
function fmtMb(bytes) { return bytes ? (bytes / 1048576).toFixed(1) + " MB" : ""; }

// 产物卡：三行，每行都能试听 / 下载 / 送去别的页
function renderSepParts(job) {
  const box = $("sepResults");
  if (!box) return;
  const assets = job.assets || {};
  const sizes = job.sizes || {};
  if (!Object.keys(assets).length) {
    box.innerHTML = '<div class="empty">暂无分离结果。</div>';
    return;
  }
  box.innerHTML = "";
  SEP_PARTS.forEach((p) => {
    if (!assets[p.key]) return;
    const url = "/api/audio/separate/audio/" + encodeURIComponent(sepCurrent) + "?part=" + p.key;
    const row = document.createElement("div");
    row.className = "h-item";
    row.style.cssText = "display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:7px 0;border-bottom:1px solid var(--line-soft);";
    const name = document.createElement("b");
    name.textContent = p.label;
    name.style.cssText = "font:600 12.5px var(--sans);min-width:112px;";
    const size = document.createElement("span");
    size.className = "hint-xs";
    size.textContent = fmtMb(sizes[p.key]) + " · " + assets[p.key];
    const mk = (text, fn, cls) => {
      const b = document.createElement("button");
      b.className = cls || "ghost"; b.type = "button"; b.textContent = text;
      b.style.cssText = "padding:3px 10px;font-size:11.5px;";
      b.onclick = (e) => { e.stopPropagation(); fn(); };
      return b;
    };
    row.appendChild(name);
    row.appendChild(mk("▶ 试听", () => playRemoteAudio(url, p.label)));
    row.appendChild(mk("⬇ 下载", () => downloadRemoteAudio(url, assets[p.key])));
    // 这里不再有「→⑨ 换声 / →⑦ 训练素材 / →③ 转谱」：产物已经在服务器上，
    // 需要它的页面自己从服务器素材列表里选，不在页面之间递来递去。
    box.appendChild(row);
    const h = document.createElement("div");
    h.className = "hint-xs";
    h.textContent = p.hint;
    box.appendChild(h);
  });
  if (job.harmony_note) {
    const n = document.createElement("div");
    n.className = "lint";
    n.style.color = "var(--warn)";
    n.textContent = "⚠ " + job.harmony_note;
    box.appendChild(n);
  }
}
// 播放/下载走服务端文件：与历史页同一套（底部播放坞是全局共用的出口）
function playRemoteAudio(url, label) {
  fetchT(url, {}, 120000).then((r) => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.blob();
  }).then((b) => loadAudio(b, label, label)).catch((e) => toast("试听失败：" + friendlyErr(e), "err"));
}
function downloadRemoteAudio(url, fname) {
  const a = document.createElement("a");
  a.href = url; a.download = fname || "audio.wav";
  document.body.appendChild(a); a.click(); a.remove();
}
// （原先这里的 handoffAudio / pullServerAudioInto 负责"把 ⑩ 的产物塞进别的页的文件框"，
//  连同 ⑨/⑦ 的「从 ⑩ 导入」按钮一起删除：页面之间不递文件，需要这份音频的页面
//  自己从服务器素材列表里选，由服务端直接取那个文件。）

// ---------- 状态轮询 ----------
function sepTick(j) {
  const bar = $("sepProgressBar");
  const pct = Number(j.progress || 0);
  if (bar) bar.style.width = Math.max(pct, 3) + "%";
  const step = j.step || (j.status === "pending" ? "排队中（等待 GPU 空闲）" : "进行中");
  const el = j.elapsed_sec;
  const extra = el ? "　已用 " + Math.round(el) + "s（约需 90-120s/首歌）" : "";
  sepSay((j.status === "running" || j.status === "pending" ? "⏳ " : "") + step + extra, "");
  renderSepParts(j);
  return j.status === "done" || j.status === "error";
}
function sepWatch(id) {
  sepCurrent = id;
  startPoller("sep", id, {
    url: "/api/audio/separate/status/" + encodeURIComponent(id),
    interval: 4000,
    missMax: 6,
    maxMs: 40 * 60 * 1000,
    onState: (j) => {
      sepAssets = j.assets || null;
      return sepTick(j);
    },
    onDone: (j) => {
      sepSetRunning(false);
      if (j.status === "error") {
        // 失败必须说清原因，绝不静默给空文件（规划 §四-⑦ 验收点）
        sepSay("✕ 分离失败：" + (j.error || "未知原因"), "err");
        stickyNotify("人声分离失败", j.error || "未知原因", false);
      } else {
        $("sepProgress").querySelector("div").style.width = "100%";
        sepSay("✓ 分离完成：" + Object.keys(j.assets || {}).length + " 份产物，可试听 / 下载 / 送去别的页", "ok");
        stickyNotify("人声分离完成", "产物在 ⑩ 右侧，也已登记进 ⑪ 任务管理。", true);
      }
      loadSepList();
    },
    onGiveUp: (e) => { sepSetRunning(false); sepSay("✕ 查询不到这个分离任务：" + friendlyErr(e), "err"); },
  });
}
$("sepGo").onclick = async () => {
  const f = $("sepFile").files[0];
  if (!f) { toast("先点上面的虚线框选一首歌", "err"); $("sepFile").click(); return; }
  const f2 = new FormData();
  f2.append("audio", f);
  f2.append("strip_harmony", $("sepHarmony").checked ? "on" : "off");
  f2.append("task_name", $("sepName").value.trim());
  // 输出目录随提交一起送：服务端在开跑之前就校验（不存在会建、不可写会 400），
  // 不会等 1.5 分钟跑完才发现落不了盘。留空就是默认 output 目录，行为与以前一致。
  const od = ($("sepOutDir").value || "").trim();
  if (od) f2.append("out_dir", od);
  $("sepGo").disabled = true;
  sepSay("上传中（" + fmtMb(f.size) + "）…", "");
  try {
    const r = await fetchT("/api/audio/separate", { method: "POST", body: f2 }, 300000);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || j.error || ("HTTP " + r.status));
    // 400 里最常见的就是目录不可写：既然被拒了，就把焦点送回那个输入框，别让人对着状态条猜
    if (r.status === 400 && od) { $("sepOutDir").focus(); }
    if (j.out_dir) $("sepOutDirNote").textContent = "本次产物目录：" + j.out_dir;
    sepSay(j.gpu_busy ? "已提交：" + (j.gpu_note || "排队中") : "已开始分离（本机约 1.5 分钟/3.5 分钟歌）…", "");
    $("sepFileName").textContent = "音源：" + f.name + " · " + fmtMb(f.size);
    sepWatch(j.id);
    setTimeout(loadSepList, 1200);
  } catch (e) {
    $("sepGo").disabled = false;
    sepSay("✕ 提交失败：" + friendlyErr(e), "err");
  }
};
$("sepStop").onclick = () => {
  // 只能取消"本页不再盯"：GPU 上的子进程由服务端串行队列管，前端无权强杀
  if (!sepCurrent) return;
  if (!confirm("停止跟踪这个分离任务？\n\n服务端仍在跑（跑完的产物照样能在 ⑪ 任务管理与下方记录里看到），本页只是不再轮询。")) return;
  stopPoller("sep", sepCurrent);
  sepSetRunning(false);
  sepSay("已停止跟踪；任务仍在服务端排队/进行，稍后可在下方记录里看到结果。", "");
};
$("sepOutDirClear").onclick = () => {
  $("sepOutDir").value = "";
  $("sepOutDirNote").textContent = "填绝对路径。目录不存在会自动创建；不能是系统盘根目录或系统目录（会被服务端拒绝）。";
};
$("sepOutDir").addEventListener("change", () => {
  // 本地先粗查一遍（只提示，真判定在服务端）：路径里带盘符之外的相对写法，服务端一律拒
  const v = ($("sepOutDir").value || "").trim();
  const note = $("sepOutDirNote");
  if (!v) { note.textContent = "留空 = 落在工作台默认的 output 目录。"; return; }
  const looksAbs = /^[A-Za-z]:[\\/]/.test(v) || v.startsWith("\\\\") || v.startsWith("/");
  note.textContent = looksAbs
    ? "提交时服务端会检查可写性；不可写会直接拒绝，不会白跑一轮。"
    : "⚠ 这不像一个绝对路径（Windows 请写成 E:\\素材 这种）。服务端会拒绝相对路径。";
});
bindDropZone("sepDrop", "sepFile", () => {
  const f = $("sepFile").files[0];
  $("sepFileName").textContent = f ? ("已选：" + f.name + " · " + fmtMb(f.size)) : "";
  if (f) $("sepGo").click();     // 与 ③ 一致：选完即提交，少一次"忘了点开始"
});
async function loadSepList() {
  const box = $("sepList");
  if (!box) return;
  try {
    const j = await (await fetchT("/api/audio/separate/list", {}, 20000)).json();
    const items = j.items || [];
    if (!items.length) { box.innerHTML = '<div class="empty">暂无分离记录。</div>'; return; }
    box.innerHTML = "";
    items.slice(0, 12).forEach((m) => {
      const row = document.createElement("div");
      row.className = "hist-item";
      const head = document.createElement("div");
      head.style.cssText = "display:flex;align-items:center;gap:8px;flex-wrap:wrap;";
      const nm = document.createElement("b");
      nm.textContent = (m.task_name || m.src_name || m.id);
      nm.style.cssText = "font:600 12.5px var(--sans);flex:1;min-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
      const st = document.createElement("span");
      st.className = "hint-xs";
      st.textContent = (m.status === "done" ? "✓ 完成" : m.status === "error" ? "✕ 失败" : "⏳ " + (m.step || m.status))
        + " · " + (m.ts || "") + " · " + ((m.parts || []).length) + " 份";
      row.appendChild(head); head.appendChild(nm); head.appendChild(st);
      (m.parts || []).forEach((p) => {
        const b = document.createElement("button");
        b.className = "ghost"; b.type = "button";
        b.textContent = "⬇ " + ((SEP_PARTS.find((x) => x.key === p) || {}).label || p);
        b.style.cssText = "padding:2px 9px;font-size:11.5px;margin-top:4px;";
        b.onclick = () => downloadRemoteAudio("/api/audio/separate/audio/" + encodeURIComponent(m.id) + "?part=" + p, m.id + "_" + p + ".wav");
        row.appendChild(b);
      });
      if (m.status === "error" && m.error) {
        const e2 = document.createElement("div");
        e2.className = "lint"; e2.style.color = "var(--err)"; e2.textContent = m.error;
        row.appendChild(e2);
      }
      if (m.status === "running" || m.status === "pending") { sepCurrent = m.id; sepSetRunning(true); sepWatch(m.id); }
      box.appendChild(row);
    });
  } catch (e) {
    // 说清"怎么恢复"就够了，原始异常（含换行/引号）塞进 title，
    // 免得把 "<!DOCTYPE..." 这类解析细节糊在用户脸上。
    box.innerHTML = '<div class="empty" title="' + escapeHtml(e.message) +
                    '">分离记录读不到：网关（7863）没连上，或任务记录目录暂时不可用。</div>';
  }
}
// ================= v2.0 · ⑧ 音色库（子页签 / 分析卡 / 导入） =================

// 子页签只管显隐，不重画列表：筛选词与已渲染的卡片都留在原处，切回来不丢状态。
// ⑧ 现在只有 RVC 一种音色，不再有子页签（只剩一栏时那两个切换按钮是纯装饰）。
// 侧栏徽标的数字改成 RVC 模型数——以前它是参考音色条数，而那栏已经删掉了。
function setVoiceTab() {
  const n = (typeof rvcModelsAll !== "undefined" && rvcModelsAll.length) || 0;
  const badge = $("voiceCount");
  if (badge) badge.textContent = String(n);
}

// ---------- 音色分析卡 ----------
// 判断"这个嗓子能不能唱这首歌"需要体检 + 音域 + 安全区间 + 索引四样同时在眼前；
// 以前这些数字分散在体检提示与训练日志里，得翻两个地方才凑齐。
let voiceInfoSel = null;
async function showVoiceInfo(name, force) {
  const box = $("voiceInfo");
  if (!box || !name) return;
  voiceInfoSel = name;
  const t = $("voiceInfoName"); if (t) t.textContent = name.replace(/\.pth$/i, "");
  $("voiceInfoRecheck").disabled = false;
  $("voiceInfoCopy").disabled = false;
  box.innerHTML = '<div class="empty">读取中…</div>';
  const item = rvcModelsAll.find((m) => m.name === name) || {};
  let check = null, checkErr = "";
  try {
    const r = await fetchT("/api/rvc/models/" + encodeURIComponent(name) + "/check" + (force ? "?force=1" : ""), {}, 60000);
    const j = await r.json().catch(() => ({}));
    if (r.ok) check = j; else checkErr = j.detail || j.error || ("HTTP " + r.status);
  } catch (e) { checkErr = friendlyErr(e); }
  const rows = [];
  const row = (k, vHtml) => {
    const d = document.createElement("div");
    const kk = document.createElement("span"); kk.className = "k"; kk.textContent = k;
    const vv = document.createElement("span"); vv.className = "v"; vv.innerHTML = vHtml;
    d.appendChild(kk); d.appendChild(vv); rows.push(d);
  };
  if (check) {
    const cs = check.checks || {};
    row("体检", escapeHtml(check.summary || (check.ok ? "通过" : "有问题"))
        + "<br>" + Object.entries(cs).map(([k, ok]) => (ok ? "✅ " : "❌ ") + escapeHtml(k)).join("　"));
    const inf = check.info || {};
    row("底模规格", escapeHtml([inf.sr, inf.version, inf.f0 ? "带 f0 预测头" : "无 f0 头", inf.epoch ? "epoch " + inf.epoch : ""]
        .filter(Boolean).join(" · ") || "—"));
    row("检索索引", check.index ? "✅ " + escapeHtml(check.index)
        : '<span style="color:var(--err)">❌ 没有索引：换声会退化成无检索模式，音色相似度上不去。可去 ⑦ 用素材训练一份，或「导入」时带上 .index</span>');
  } else {
    row("体检", '<span style="color:var(--err)">✕ 读不到：' + escapeHtml(checkErr || "未知错误") + "</span>");
  }
  const fr = item.f0_range;
  if (fr) {
    row("音域档案", "中位 <b>" + fr.median_hz + " Hz</b>　舒适区 " + fr.p5_hz + "–" + fr.p95_hz + " Hz　p99 " + (fr.p99_hz || "—") + " Hz");
    row("音域来源", fr.source === "training"
        ? "本机训练素材实测（" + (fr.frames || 0) + " 帧 / " + (fr.files || 0) + " 个切片）"
        : "外部参考音频建档" + (fr.analyzed_sec ? "（读了 " + Math.round(fr.analyzed_sec) + " 秒）" : ""));
  } else {
    row("音域档案", '<span style="color:var(--warn)">无档案——⑨ 换声页对这个音色给不出变调建议（不猜）。点卡片上的「建档音域」上传该歌手本人 10~30 秒干声量一次。</span>');
  }
  row("安全变调", item.safe_range ? escapeHtml(item.safe_range)
      : (fr ? "要有一首源歌才算得出：去 ⑨ 点「分析音域 → 变调建议」" : "没有音域档案，算不出安全变调区间"));
  row("规格", escapeHtml([item.size_mb ? item.size_mb + " MB" : "", item.mtime ? "文件时间 " + item.mtime : ""]
      .filter(Boolean).join(" · ") || "—"));
  row("来源", escapeHtml(item.f0_source === "training" ? "本机训练" : "外部导入 / 下载")
      + (item.range_note ? "　·　" + escapeHtml(item.range_note) : ""));
  const rec = item.recommend || {};
  if (Object.keys(rec).length) row("官方推荐档", escapeHtml(Object.entries(rec).map(([k, v]) => k + "=" + v).join("　")));
  box.innerHTML = "";
  rows.forEach((r) => box.appendChild(r));
}
// 点卡片＝选中看分析。卡片上原有 5 个操作按钮各自 stopPropagation，不会被这一击误触发
{
  const bindCards = () => {
    const el = $("rvcModelList");
    if (!el) return;
    el.querySelectorAll(".m-item").forEach((card) => {
      if (card.dataset.infoBound) return;
      card.dataset.infoBound = "1";
      card.addEventListener("click", () => {
        const nm = card.querySelector(".m-name");
        if (!nm) return;
        el.querySelectorAll(".m-item").forEach((c) => c.classList.remove("on"));
        card.classList.add("on");
        const want = nm.textContent.trim();
        const hit = rvcModelsAll.find((m) => m.name === want || m.name.replace(/\.pth$/i, "") === want);
        if (hit) showVoiceInfo(hit.name);
      });
    });
  };
  new MutationObserver(bindCards).observe($("rvcModelList") || document.documentElement, { childList: true, subtree: true });
}
$("voiceInfoRecheck").onclick = async () => {
  if (!voiceInfoSel) return;
  const btn = $("voiceInfoRecheck");
  btn.disabled = true; btn.textContent = "体检中…";
  await showVoiceInfo(voiceInfoSel, true);   // force=1：绕过服务端的 mtime/size 缓存，真重检一遍
  btn.disabled = false; btn.textContent = "↻ 重新体检（不吃缓存）";
};
// 「→ ⑨ 用这个音色换声」已删：它会切页并替 ⑨ 的下拉做选择。
// 这一页是看这个音色"体检怎么样、音域多少、安全变调区间"的，要拿去用就复制名字。
$("voiceInfoCopy").onclick = () => { if (voiceInfoSel) copyOut(voiceInfoSel, "音色名"); };

// ---------- 导入外部音色 ----------
// 后端强制建档：外部 .pth 没有训练实测音域，不建档 ⑨ 就没有变调建议，
// 所以参考音频是该音色的必需项（本机训练过的音色则不许用参考档案降级）。
let imp = { pth: null, idx: null, ref: null, overwrite: false };
function impRepaint() {
  const box = $("rvcImportPick"), go = $("rvcImportGo"), cancel = $("rvcImportCancel");
  if (!box) return;
  const size = (f) => f ? (f.size / 1048576).toFixed(1) + " MB" : "";
  if (!imp.pth) {
    box.hidden = true; go.style.display = "none"; cancel.style.display = "none"; box.innerHTML = "";
    $("rvcImportStatus").textContent = ""; return;
  }
  box.hidden = false;
  box.innerHTML = "待导入：<b>" + escapeHtml(imp.pth.name) + "</b>（" + size(imp.pth) + "）"
    + (imp.idx ? "　索引 " + escapeHtml(imp.idx.name) : "　<i>无索引（可选，缺了相似度下降）</i>")
    + (imp.ref ? "　建档参考音频 " + escapeHtml(imp.ref.name) : '　<span style="color:var(--warn)">缺建档参考音频（外部音色必需）</span>')
    + (imp.overwrite ? "　<b>同名将替换</b>" : "");
  go.style.display = ""; cancel.style.display = "";
  go.textContent = imp.overwrite ? "确认替换并导入" : "提交导入";
}
function pickFile(inputId, cb) {
  const el = $(inputId);
  el.onchange = () => { cb(el.files[0] || null); impRepaint(); };
  el.click();
}
$("rvcImportBtn").onclick = () => pickFile("rvcImportPth", (f) => {
  if (!f) return;
  if (!/\.pth$/i.test(f.name)) { toast("只能导入 .pth 权重文件", "err"); $("rvcImportPth").value = ""; return; }
  imp.pth = f; $("rvcImportStatus").textContent = "";
  pickFile("rvcImportIndex", (g) => { imp.idx = g; });        // 索引可选：取消也能继续
});
$("rvcImportIndex").addEventListener("change", () => { imp.idx = $("rvcImportIndex").files[0] || null; });
$("rvcImportRefBtn") && ($("rvcImportRefBtn").onclick = () => pickFile("rvcImportRef", (f) => { imp.ref = f; }));
$("rvcImportCancel").onclick = () => {
  imp = { pth: null, idx: null, ref: null, overwrite: false };
  $("rvcImportPth").value = ""; $("rvcImportIndex").value = ""; $("rvcImportRef").value = "";
  impRepaint();
};
async function doImport() {
  const st = $("rvcImportStatus");
  $("rvcImportGo").disabled = true;
  st.className = "status";
  st.textContent = "上传中（" + (imp.pth.size / 1048576).toFixed(1) + " MB，外加建档要跑一次 F0 提取，耐心等）…";
  const fd = new FormData();
  fd.append("model", imp.pth);
  if (imp.idx) fd.append("index", imp.idx);
  if (imp.ref) fd.append("ref_audio", imp.ref);
  fd.append("overwrite", imp.overwrite ? "on" : "off");
  try {
    const r = await fetchT("/api/rvc/models/import", { method: "POST", body: fd }, 600000);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      const msg = j.detail || j.error || ("HTTP " + r.status);
      if (r.status === 409 && !imp.overwrite) {
        st.className = "status err";
        st.textContent = "⚠ " + msg + "　点「确认替换并导入」会用新权重替换它（旧文件先备份，不静默覆盖）。";
        imp.overwrite = true; impRepaint();
        return;
      }
      throw new Error(msg);
    }
    const f0 = j.f0_reference || {};
    const bits = ["✓ 已导入 " + (j.name || "")];
    bits.push(j.index ? "索引 " + j.index : "无索引");
    if (j.overwritten) bits.push("（替换了同名旧文件）");
    if (f0.source === "training") bits.push("音域沿用训练实测");
    else if (f0.ok && f0.attempted) bits.push("音域已建档：中位 " + ((f0.stats && (f0.stats.median_hz || f0.stats.median)) || "?") + " Hz");
    else if (f0.ok) bits.push("音域沿用已有档案（" + (f0.note || "本次未带参考音频") + "）");
    else bits.push("⚠ 音域建档失败：" + (f0.error || "未知"));
    st.textContent = bits.join("　");
    st.className = "status" + (f0.ok ? " ok" : "");
    if (j.warning) { st.textContent += "　" + j.warning; }
    imp = { pth: null, idx: null, ref: null, overwrite: false };
    $("rvcImportPth").value = ""; $("rvcImportIndex").value = ""; $("rvcImportRef").value = "";
    impRepaint();
    renderRvcModels();
    if (typeof renderRvc === "function") renderRvc();
  } catch (e) {
    st.className = "status err";
    st.textContent = "✕ 导入失败：" + friendlyErr(e);
  } finally { $("rvcImportGo").disabled = false; }
}
$("rvcImportGo").onclick = () => {
  if (!imp.pth) { toast("先选一个 .pth 文件", "err"); $("rvcImportPth").click(); return; }
  if (!imp.ref && !imp.overwrite) {
    if (!confirm("没带「建档参考音频」。\n\n外部下载的音色必须有音域档案，否则 ⑨ 换声页给不出变调建议（页面会显示「无音域」而不是猜一个）。\n\n确定先不建档、只把 .pth 入库吗？（之后可在音色卡片上点「建档音域」补一次）")) return;
  }
  doImport();
};
// ---------- ⑩ → ⑨ / ⑦ 的音源通道 ----------
// 分离完直接换声、直接开练是最高频的两条路。file input 不能塞服务器路径，只能把产物
// 取回来做成 File 再塞进去——所以"导入"是真取音频，不是记个路径字符串。
// （原来的 pickSepSource + 「从 ⑩ 导入」两处：把 ⑩ 的产物塞进 ⑨/⑦ 的 file input，
//  还会顺手取消别的页的勾选——已全部删除。⑩ 产物的标识由下面 serverAudioOptions 供给
//  每页自己的下拉，提交时只传标识，文件由服务端取。）
// ================= v2.0 · ⑪ 轻量任务行 / ② 成片出口 / ⑥ 会话与回填 =================

// ---------- ⑪ 乐谱提取与分离两类：轻量行 ----------
// 它们没有音频产物也没有歌词，硬套生成任务那 12 个按钮只会出一排禁用态，
// 所以动作更少 —— 但**行骨架必须和别的行一模一样**，否则同一张表里两种行高。
//
// ★ 曾踩过的坑：这行最初自己搭了个"3 列 .h-col"的布局，4 个按钮全塞进最后一个 .h-col。
//   而 .h-col 默认是块级、按钮各自成行 ⇒ 这一行高 168px，而同列表里其它行是 72px。
//   实测（1920x1174，⑪ 共 61 行）：
//     行 0「如诗」(kind=generate) → h=72，动作在 .h-actions 里
//     行 1、2「🎼 乐谱提取」      → h=168 ← 破口
//   用户看到的是"表格里突然有两行比别的行高一倍多"，正是"乱"的一部分。
//   修法：**5 个格子，一个不少** —— 任务 / 状态 / 摘要(歌词列) / 元信息 / 操作，
//   动作一律进 .h-actions（那才有右对齐 + 定宽 + 按钮不撑宽的规矩）。
//   空列给一个 .h-col 占位（不放内容），列轨道才对得上。
function lightTaskRow(h) {
  const d = document.createElement("div");
  d.className = "hist-item";
  const isScore = h.kind === "score";
  // 聚合端点给分离条目带的是 assets（文件名），不是 parts（产物键名）：
  // 两边都认，否则任务管理里的分离行会一个下载按钮都不长
  const parts = (h.parts && h.parts.length) ? h.parts : Object.keys(h.assets || {});
  const title = isScore ? ("乐谱 " + (h.cot ? "[" + h.cot + "]" : " ") + (h.time || h.ts || h.id))
                        : (h.task_name || h.src_name || h.id);
  const stMap = { done: "✓ 成功", error: "✕ 失败", cancelled: "⏹ 已终止", pending: "⏳ 排队中" };
  const stTxt = stMap[h.status] || "⏳ 进行中";
  const stColor = h.status === "done" ? "var(--ok)" : (h.status === "error" || h.status === "cancelled") ? "var(--err)" : "var(--acc-text)";
  const preview = isScore ? String(h.abc_preview || "").replace(/\s+/g, " ").slice(0, 70)
    : (parts.length
        ? (parts.length + " 份产物：" + parts.map((x) => ((SEP_PARTS.find((sp) => sp.key === x) || {}).label || x)).join("、"))
        : (h.step || ""));
  // 第 1 格：任务名 + 时间/ID + 摘要（与 generate 行的任务格同一套内部结构）
  const col = document.createElement("div");
  col.className = "h-col";
  col.innerHTML = '<div class="h-title">' + escapeHtml((isScore ? "🎼 乐谱提取 · " : "✂ 分离 · ") + String(title)) + "</div>"
    + '<div class="h-sub">' + [h.ts || h.time ? timeAgo(h.ts || h.time) : "", idTag(h.id)].filter(Boolean).join(" ") + "</div>"
    + (h.error ? '<div class="h-sub" style="color:var(--err);">' + escapeHtml(String(h.error).slice(0, 160)) + "</div>" : "");
  // 第 2 格：状态
  const st = document.createElement("div");
  st.className = "h-col h-col-st"; st.style.color = stColor; st.textContent = stTxt;
  // 第 3 格：摘要（复用"歌词"那一列的位置）
  const sum = document.createElement("div");
  sum.className = "h-col";
  {
    const s = document.createElement("div");
    s.className = "h-sub"; s.style.marginTop = "0"; s.style.fontSize = "var(--fs-xs)";
    s.textContent = preview || "—";
    if (preview) s.title = preview;
    sum.appendChild(s);
  }
  // 第 4 格：元信息（乐谱/分离没有时长大小，占位保持列轨道）
  const meta = document.createElement("div");
  meta.className = "h-col h-col-meta";
  const acts = document.createElement("div");
  acts.className = "h-actions";
  const mk = (t, fn, cls, title) => {
    const b = document.createElement("button");
    b.className = cls || "ghost"; b.type = "button"; b.textContent = t;
    if (title) b.title = title;
    b.onclick = (e) => { e.stopPropagation(); fn(b); };
    acts.appendChild(b);
    return b;
  };
  if (isScore) {
    // 「↩ 载入到 ③」已删除：那是从任务列表把谱写进另一个页面的谱框。
    // 这份谱本来就在乐谱库里，③ 与 ⑤ 各自有库选择器，去那儿挑是同一页内的事。
    mk("MIDI", () => exportScoreById(h.id, "mid"), "ghost", "导出 MIDI");
    mk("ABC", () => exportScoreById(h.id, "abc"), "ghost", "导出 ABC 谱");
    mk("删除", async (b) => {
      if (!confirm("删除这份乐谱？\n\n转谱时归档的 MIDI 等中间文件会一起删除（原音频不受影响），本机不可恢复。")) return;
      b.disabled = true;
      try {
        const r = await fetch("/api/scores/" + encodeURIComponent(h.id), { method: "DELETE" });
        if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || "删除失败"); }
        toast("已删除（含归档目录）", "ok"); renderHistory();
      } catch (e) { toast(friendlyErr(e), "err"); b.disabled = false; }
    }, "danger", "删除这份乐谱");
  } else {
    parts.forEach((part) => {
      const lb = ((SEP_PARTS.find((x) => x.key === part) || {}).label) || part;
      mk(lb, () =>
        downloadRemoteAudio("/api/audio/separate/audio/" + encodeURIComponent(h.id) + "?part=" + encodeURIComponent(part), h.id + "_" + part + ".wav"),
        "ghost", "下载 " + lb);
    });
    // 「换声」「打开」两颗已删除：前者把产物塞进别的页的文件框，后者替另一页记住"当前记录"。
    // ⑨ 与 ⑦ 现在各自有"从服务器素材里选"的下拉，这份产物就在里面。
    mk("删除", async (b) => {
      if (!confirm("删除这条分离记录与它的产物文件？\n\n人声/伴奏的 wav 会一起从 output/ 清掉，本机不可恢复。")) return;
      b.disabled = true;
      try {
        const r = await fetch("/api/generate/" + encodeURIComponent(h.id), { method: "DELETE" });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(r.status === 409 ? (j.detail || "任务进行中，不能删除") : (j.detail || "删除失败"));
        toast("已删除（含产物）", "ok"); renderHistory();
      } catch (e) { toast(friendlyErr(e), "err"); b.disabled = false; }
    }, "danger", "删除这条分离记录");
  }
  // ★ 五个格子按列轨道顺序落位：任务 / 状态 / 摘要 / 元信息 / 操作。
  //   少放一个，后面所有格子就会整体左移一列（CSS grid 按顺序填轨道），
  //   于是"这张表的列还对不对得齐"取决于行类型 —— 那正是要消灭的东西。
  d.appendChild(col); d.appendChild(st); d.appendChild(sum);
  d.appendChild(meta); d.appendChild(acts);
  return d;
}
async function exportScoreById(id, fmt) {
  try {
    const r = await fetchT("/api/scores/" + encodeURIComponent(id) + "/export?format=" + encodeURIComponent(fmt));
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.detail || ("HTTP " + r.status)); }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "score-" + id + "." + (fmt === "mid" ? "mid" : "abc");
    a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast("已开始下载 " + a.download, "ok");
  } catch (e) { toast("导出失败：" + friendlyErr(e), "err"); }
}

// ---------- ② 成片出口 ----------
// 出片以后真正会做的事有五种：试听、拿 WAV、拿 LRC、拿逐字 eLRC、送去换声。
// 以前只有播放坞那一条，剩下四样都要绕去 ⑪ 把刚跑完的那条找回来。
function renderGenResult(job) {
  const box = $("genResult"), acts = $("genResultActs"), note = $("genResultNote"), idg = $("genResultId");
  if (!box || !job || !job.id) return;
  box.hidden = false;
  if (idg) idg.innerHTML = idTag(job.id);
  acts.innerHTML = "";
  if (note) note.textContent = "";
  const mk = (t, fn, cls) => {
    const b = document.createElement("button");
    b.className = cls || "ghost"; b.type = "button"; b.textContent = t;
    b.style.cssText = "padding:4px 11px;font-size:12px;";
    b.onclick = () => { Promise.resolve(fn(b)).catch((e) => toast(friendlyErr(e) || String(e), "err")); };
    acts.appendChild(b);
    return b;
  };
  mk("▶ 试听", async () => {
    const r = await fetchT("/api/generate/audio/" + job.id);
    if (!r.ok) throw new Error("音频文件取不到（HTTP " + r.status + "）");
    loadAudio(await r.blob(), job.id + ".wav", job.task_name || (job.style || "").split(",")[0]);
  });
  mk("⬇ WAV", () => {
    const a = document.createElement("a");
    a.href = "/api/generate/audio/" + encodeURIComponent(job.id);
    a.download = dlName(job.task_name || (job.style || "").split(",")[0] || job.id) + ".wav";
    document.body.appendChild(a); a.click(); a.remove();
  });
  mk("⬇ LRC", (b) => grabTimeline(job.id, "lrc", b, job.task_name || (job.style || "").split(",")[0]));
  mk("⬇ 逐字 eLRC", (b) => grabTimeline(job.id, "elrc", b, job.task_name || (job.style || "").split(",")[0]));
  mk("🎙 送去换声", () => convertServerSong(job.id,
    job.task_name || (job.style || "").split(",")[0] || job.id, acts));
  mk("↩ 改曲风重跑", () => switchTab("style"), "primary");
}
// 时间轴是后台异步构建的：没构建完就先提交构建再等，别把"还没有"说成"失败"
async function grabTimeline(rid, fmt, btn, nameForFile) {
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = "查询中…";
  try {
    let r = await fetch("/api/generate/lrc/" + encodeURIComponent(rid));
    let j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || "状态查询失败");
    if (j.status === "none") {
      btn.textContent = "对齐中…";
      const pr = await fetch("/api/generate/lrc/" + encodeURIComponent(rid), { method: "POST" });
      if (!pr.ok) { const pj = await pr.json().catch(() => ({})); throw new Error(pj.detail || "提交对齐失败"); }
      j = { status: "running" };
    }
    if (j.status !== "done") {
      btn.textContent = "对齐中…";
      for (let i = 0; i < 120; i++) {
        await new Promise((res) => setTimeout(res, 2000));
        const rr = await fetch("/api/generate/lrc/" + encodeURIComponent(rid));
        const jj = await rr.json().catch(() => ({}));
        if (jj.status === "done") { j = jj; break; }
        if (jj.status === "error") throw new Error(jj.error || "对齐失败");
      }
      if (j.status !== "done") throw new Error("对齐超时（4 分钟），稍后再点一次即可");
    }
    const text = fmt === "elrc" ? j.elrc : j.lrc;
    if (!text) throw new Error(fmt === "elrc"
      ? "这份对齐结果没有逐字版本（当前语言/引擎只产出行级时间轨），可下载 LRC"
      : "没有可用的行级歌词文本");
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = dlName(nameForFile || rid) + (fmt === "elrc" ? ".elrc" : ".lrc");
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    toast("已开始下载 " + (fmt === "elrc" ? "逐字 eLRC" : "LRC"), "ok");
  } finally { btn.disabled = false; btn.textContent = old; }
}
// 服务端直接从 output/ 取这份歌去换声，不需要用户重新上传一遍
// （forwardToConvert 已删：它把 ⑨ 页的十个滑杆当自己的参数源，
//  ② 页的按钮却去读另一个页面的控件状态。现在统一走 convertServerSong。）

// ---------- ⑥ 会话侧条与产出回填 ----------
// 会话里真正能无损接进创作链的是 feed 卡（后端 ai_tools.feed_card 产出的
// {type:'generate', style, lyrics, cot}）；对话正文是给人看的，抽词会抽错，
// 所以只做"取回给你确认"，不自动改写用户的歌词框。
async function loadAiSessions() {
  const box = $("aiSessList");
  if (!box) return;
  try {
    const j = await (await fetchT("/api/ai/sessions", {}, 20000)).json();
    const items = j.items || j.sessions || j || [];
    const cnt = $("aiSessCount");
    if (cnt) cnt.textContent = items.length ? items.length + " 条" : "";
    if (!items.length) { box.innerHTML = '<div class="empty">还没有会话：在右边工作台里聊一轮词曲，产出会出现在这里。</div>'; return; }
    box.innerHTML = "";
    items.slice(0, 30).forEach((it) => {
      const d = document.createElement("div");
      d.className = "ai-sess-item";
      d.textContent = (it.title || it.id || "会话").slice(0, 34);
      d.title = (it.title || "") + "\n" + (it.updated || it.ts || "") + "\n" + (it.id || "");
      d.onclick = () => { loadAiSession(it.id); chatShow(it.id); };
      box.appendChild(d);
    });
  } catch (e) {
    box.innerHTML = '<div class="empty" title="' + escapeHtml(e.message) +
                    '">会话读不到：AI 工作台还没起来，或网关（7863）没连上。</div>';
  }
}
// 从助手正文里抠 Style / 歌词。真机实测的写法是「**Style:**」+ ``` 代码块，
// 老正则只认行首裸 `Style:`，一条都抓不到——标签两侧可能各有 0~2 个星号，冒号可能在星号内也可能在外。
function aiExtractStyleLyrics(body) {
  const b = String(body || "");
  const fence = (label) => {
    const m = b.match(new RegExp("(?:^|\\n)[^\\n]*\\*{0,2}(?:" + label + ")[^\\n]{0,3}\\*{0,2}\\s*[:：][^\\n]*\\n+\\s*```[^\\n]*\\n([\\s\\S]*?)```", "i"));
    return m ? m[1].trim() : "";
  };
  const inline = (label) => {
    const m = b.match(new RegExp("^\\s*\\*{0,2}(?:" + label + ")\\s*\\*{0,2}\\s*[:：]\\s*\\*{0,2}([^\\n`]{12,})$", "im"));
    return m ? m[1].replace(/[*`]/g, "").trim() : "";
  };
  const style = (fence("Style|风格") || inline("Style|风格")).replace(/\s*\n\s*/g, " ").trim();
  let lyrics = fence("Lyrics|歌词");
  if (!lyrics) {
    const m = b.match(/\[[A-Z][a-z][^\]]*\][\s\S]+$/);
    if (m) {
      const lines = m[0].split("\n");
      const stop = lines.findIndex((l, i) => i > 0
        && (/^\s*-{3,}\s*$/.test(l) || /^\s*(?:\*\*|【|`{3})/.test(l)));
      lyrics = lines.slice(0, stop === -1 ? lines.length : stop).join("\n").trim();
    }
  }
  return { style, lyrics };
}
// 下拉列表和回填必须走同一份派生数据：以前一边按下标取全量 messages、另一边取过滤后的数组，
// 结果永远错位——选得到条目，填出来的却是空的。
function aiFeedList(j) {
  const feeds = (j.feeds || []).filter((f) => f && (f.style || f.lyrics)).map((f) => ({
    style: f.style || "", lyrics: f.lyrics || "",
    note: f.job_id ? "这条产出对应任务 " + f.job_id + (f.cot ? " · 档位 " + f.cot : "")
                   : (f.cot ? "档位 " + f.cot : ""),
    label: "产出卡：" + String(f.style || "").slice(0, 26),
  }));
  const msgs = (j.messages || [])
    .filter((m) => m && m.role === "assistant" && /\[[A-Z][a-z]+\]/.test(String(m.content || "")))
    .slice().reverse().map((m) => {
      const body = String(m.content || "");
      const ex = aiExtractStyleLyrics(body);
      return {
        style: ex.style, lyrics: ex.lyrics,
        note: (ex.style && ex.lyrics) ? "从对话正文里抓的（标签后的代码块优先）——送出前自己扫一眼"
                                      : "从对话正文里只抓到一半，请回右边让它按格式重交",
        label: "对话消息：" + body.replace(/\s+/g, " ").slice(0, 24) + "…",
      };
    });
  return feeds.concat(msgs);
}
async function loadAiSession(sid) {
  $("aiFeedPick").dataset.sid = sid;
  try {
    const j = await (await fetchT("/api/ai/session/" + encodeURIComponent(sid), {}, 25000)).json();
    const list = aiFeedList(j);
    const pick = $("aiFeedPick");
    if (!list.length) {
      pick.innerHTML = '<option value="">这条会话里没有可回填的产出</option>';
      $("aiFeedStyle").value = ""; $("aiFeedLyrics").value = "";
      $("aiFeedLint").textContent = "这条会话没交出结构化产出（Style / 歌词）。回到右边工作台让它把词曲交出来再来取。";
      return;
    }
    pick.innerHTML = list.map((it, i) =>
      '<option value="' + i + '">' + escapeHtml(it.label) + "</option>").join("");
    await fillAiFeed(pick.value);
  } catch (e) { toast("读会话失败：" + friendlyErr(e), "err"); }
}
async function fillAiFeed(key) {
  if (key === "" || key === null || key === undefined) return;
  const cur = $("aiFeedPick").dataset.sid;
  if (!cur) { $("aiFeedLint").textContent = "先从左边点一条会话。"; return; }
  const j = await (await fetchT("/api/ai/session/" + encodeURIComponent(cur), {}, 25000)).json();
  const it = aiFeedList(j)[Number(key)];
  if (!it) { $("aiFeedLint").textContent = "这条产出读不到了：会话可能已被删除，点「↻」刷新列表。"; return; }
  $("aiFeedStyle").value = it.style;
  $("aiFeedLyrics").value = it.lyrics;
  $("aiFeedLint").textContent = it.note;
}
$("aiSessReload").onclick = () => loadAiSessions();
$("aiFeedPick").onchange = () => fillAiFeed($("aiFeedPick").value);
$("aiFeedCheck").onclick = async () => {
  const ly = $("aiFeedLyrics").value;
  if (!ly.trim()) { toast("歌词框是空的", "err"); return; }
  const btn = $("aiFeedCheck");
  btn.disabled = true;
  try {
    const j = await (await fetchT("/api/lyrics/validate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lyrics: ly }),
    }, 20000)).json();
    const errs = j.errors || [], warns = j.warnings || [];
    $("aiFeedLint").textContent = errs.length
      ? "⚠ " + errs.slice(0, 4).join("；")
      : "✓ 结构合格" + (j.sections ? "（" + j.sections + " 段）" : "") + (warns.length ? "；提示：" + warns.slice(0, 2).join("；") : "");
  } catch (e) { $("aiFeedLint").textContent = "校验失败：" + friendlyErr(e); }
  finally { btn.disabled = false; }
};
$("aiFeedCopyStyle").onclick = () => copyOut($("aiFeedStyle").value, "Style");
$("aiFeedCopyLyrics").onclick = () => copyOut($("aiFeedLyrics").value, "歌词");
// 会话条目点击时记住 sid（fillAiFeed 要按它再取一次详情）
{
  const mo = new MutationObserver(() => {
    const box = $("aiSessList");
    if (!box) return;
    box.querySelectorAll(".ai-sess-item").forEach((el) => {
      el.addEventListener("click", () => {
        box.querySelectorAll(".ai-sess-item").forEach((x) => x.classList.remove("active"));
        el.classList.add("active");
        const t = el.title || "";
        const sid = (t.split("\n").pop() || "").trim();
        if (sid) $("aiFeedPick").dataset.sid = sid;
      });
    });
  });
  mo.observe($("aiSessList") || document.documentElement, { childList: true, subtree: true });
}

// ---------- boot ----------
// 创作页表单取值/回填：供下面的 localStorage 持久化调用。
// 字段清单必须与下方监听白名单一致，否则改了控件却悄悄不再保存。
const _FORM_IDS = ["taskName", "style", "lyrics", "cot", "abc", "gender", "seed", "steps", "cfg",
  "genCount", "bpm", "abc_temperature", "abc_top_p", "abc_top_k",
  "semantic_temperature", "semantic_top_p", "semantic_top_k"];
function currentConfig() {
  const c = {};
  _FORM_IDS.forEach((id) => { const el = $(id); if (el) c[id] = el.value; });
  const box = $("abcChords"); if (box) c.abcChords = !!box.checked;
  return c;
}
function applyConfig(c) {
  if (!c || typeof c !== "object") return;
  _FORM_IDS.forEach((id) => {
    const el = $(id); if (!el || c[id] == null) return;
    // 自动保存的那份可能是修复前留下的、带着机器补的 [Outro]；恢复时摘掉，
    // 否则刷新一次就把旧脏数据又端回用户面前，提交还会被结构校验拦下。
    el.value = id === "lyrics" ? stripMachineOutro(c[id]) : String(c[id]);
  });
  const box = $("abcChords"); if (box && c.abcChords != null) box.checked = !!c.abcChords;
  analyzeAbcDebounced();   // 回填的谱要重算「有没有和弦/走哪条档位」提示
}
// 创作页表单自动持久化：切 dsh 对话页再切回（iframe 重载）也不丢歌词/曲风/参数
(function () {
  const KEY = "yue2_compose_form";
  // 恢复上次保存的表单（localStorage 里未过期的一份）
  try {
    const saved = localStorage.getItem(KEY);
    if (saved) {
      const c = JSON.parse(saved);
      if (c && (c.style || c.lyrics)) applyConfig(c);
      // applyConfig 是直写 value、不派发事件的：档位高亮靠 cot/steps 的 change/input
      // 事件同步，不补这一句，恢复出 full/32 的表单高亮却还停在「草稿」上。
      syncGenPresetFromFields();
    }
  } catch (e) { console.warn("恢复上次表单失败：", e); }
  // 自动保存：内容变化即写 localStorage（防抖 400ms），iframe 销毁前兜底再存一次
  const save = () => {
    try { localStorage.setItem(KEY, JSON.stringify(currentConfig())); } catch (e) { console.warn("表单保存失败：", e); }
  };
  let timer = null;
  document.addEventListener("input", (ev) => {
    if (ev.target && ev.target.id && /^(taskName|style|lyrics|abc|abcChords|cot|seed|cfg|steps|gender|bpm|abc_|semantic_|genCount|model)$/.test(ev.target.id)) {
      clearTimeout(timer);
      timer = setTimeout(save, 400);
    }
  });
  document.addEventListener("change", (ev) => {
    if (ev.target && ev.target.id && /^(cot|seed|steps|cfg|gender|genCount|abcChords|model)$/.test(ev.target.id)) save();
  });
  window.addEventListener("beforeunload", save);
})();

// 关掉浏览器的滚动恢复：刷新时它会把上次的位置还回来，而 ① 以前底部有粘底产出条，
// 文档很长，恢复出来就是"停在页面最下面"。关掉之后首屏永远从顶部开始，落点可预期。
try { if ("scrollRestoration" in history) history.scrollRestoration = "manual"; } catch {}

// hash 直达页签（供 dsh 侧栏融合切换使用）：/lab/#rvc → 直开换声页
const _hashTab = (location.hash || "").replace("#", "");
// dsh 内嵌模式（?embed=1）：隐藏本页侧栏，功能由 dsh 侧栏提供
if (new URLSearchParams(location.search).has("embed")) {
  document.documentElement.classList.add("embed");
}
// 旧页签名兼容：v1.x 的 hash（#compose / #rvc / #rvcTrain / #voice）和 dsh 侧栏可能仍带着用，
// 直接喂给 switchTab 会找不到面板（页面停在 ①，hash 却写着旧值）。一次映射，不改历史链接。
const LEGACY_TAB = { compose: "sing", rvc: "convert", rvcTrain: "train", voice: "voices" };
function resolveTab(t) { return LEGACY_TAB[t] || t; }
if (_hashTab && document.querySelector(`[data-tab="${resolveTab(_hashTab)}"]`)) switchTab(resolveTab(_hashTab));
// dsh 侧栏页签切换时只改 hash，不重载页面——监听后续变化
window.addEventListener("hashchange", () => {
  const t = resolveTab((location.hash || "").replace("#", ""));
  if (t && document.querySelector(`[data-tab="${t}"]`)) switchTab(t);
});

// ① 右栏恢复上次选择的视图（silent：启动时不要碰滚动位置，滚动位置由下面的归零统一处理）
{
  let m = "preset";
  try { m = localStorage.getItem(LS_ASM_MODE) || "preset"; } catch {}
  if (typeof setAsmMode === "function") setAsmMode(m, { silent: true });
}

resumeRunningJob();
renderHistory();
renderRvc();
renderRvcModels();
// 音色名筛选：本地过滤上一次拉到的列表，不再打接口（模型多起来时逐个字筛要即时）
{ const f = $("rvcModelFilter"); if (f) f.addEventListener("input", paintRvcModels); }
restoreRvcActive();
restoreRvcTrainActive();
refreshHealth();
refreshModelVerify();
loadPresets();
loadModels();
setInterval(refreshHealth, 15000);
setInterval(refreshModelVerify, 60000);
// 首屏归零：默认落在 ①，且 hash 为空时上面不会走到 switchTab，这里补一次。
// 放在最后是因为渲染完成后文档高度才定下来，提前归零会被随后的内容撑高又带偏。
if (typeof scrollPageTop === "function") requestAnimationFrame(scrollPageTop);
