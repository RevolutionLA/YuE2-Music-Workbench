// 音乐工作台 · 一体化音乐工作台 — Client 半
// 侧栏分上下两段：上=DSH 原生（新会话+会话列表，不动），下=实验室六页签；
// 左下角状态卡：引擎模式(GPU/CPU)、显存空闲、主题（深/浅色）。
// 主面板为全尺寸 iframe 复用原版实验室页面（/lab 反代，同一后端）。
window.__ModuleLoader__.load({
  id: "yue2-lab-plugin/client",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    var react = require("react");

    // ------------------------------------------------------------------ //
    // 实验室页签（与原版 data-tab 一致，点击经 hash 直达对应页）
    // ------------------------------------------------------------------ //
    var LAB_TABS = [
      { id: "compose",   name: "创作",  glyph: "♪", short: "创作" },
      { id: "rvc",       name: "歌曲换声",  glyph: "⇄", short: "换声" },
      { id: "rvcTrain",  name: "音色制作",  glyph: "🎨", short: "制作" },
      { id: "voice",     name: "音色库", glyph: "🎙", short: "音色" },
      { id: "history",   name: "任务管理",  glyph: "🕘", short: "任务" },
    ];

    // ------------------------------------------------------------------ //
    // 状态卡：引擎模式 / 显存 / 主题
    // ------------------------------------------------------------------ //
    // 读取 dsh 当前深/浅色：只认 dsh 自己的主题标记（body[data-ds-dark-theme]、
    // html class/data-theme）。不读 prefers-color-scheme——OS 深色 ≠ dsh 浅色设置，
    // 混入会让 iframe 在 dsh 浅色时被误判成深色。
    function readDark() {
      var root = document.documentElement;
      var body = document.body;
      return root.classList.contains("dark") || root.dataset.theme === "dark"
        || (body && body.hasAttribute("data-ds-dark-theme"));
    }
    // 把一个主题同步到实验室 iframe（写 data-theme + postMessage 通知其重算派生色）
    function pushThemeToIframe(win, dark) {
      if (!win) return;
      try {
        var d = win.document;
        if (d && d.documentElement) d.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
        try { win.postMessage({ type: "yue2-theme", theme: dark ? "dark" : "light" }, "*"); } catch (e) {}
      } catch (e) {}
    }
    function useLabStatus() {
      var st = react.useState({ mode: "…", vram: "", alive: false, theme: "" });
      var set = st[1];
      react.useEffect(function () {
        var stop = false;
        // 瞬时抖动（网关忙/代理超时/网络抖动）不应立刻显示「离线」：
        // 连续 3 次探测失败才判定离线，任一次成功立即恢复。
        var failStreak = 0;
        function tick() {
          // /lab-status 是插件侧 1.5s 超时的轻量存活探测，比经 60s 超时代理的
          // /lab-api/backend/mode 更能反映"网关在不在"，也不会被慢请求拖挂。
          fetch("/lab-status").then(function (r) { return r.json(); }).then(function (alive) {
            if (stop) return;
            if (!alive || !alive.alive) throw new Error("gateway down");
            failStreak = 0;
            // 存活后再取模式/显存；这个请求慢或失败不影响在线状态
            fetch("/lab-api/backend/mode").then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }).then(function (j) {
              if (stop) return;
              set(function (p) {
                return Object.assign({}, p, {
                  mode: j ? (j.mode === "cuda" ? "GPU" : j.mode === "cpu" ? "CPU" : String(j.mode || "…")) : p.mode,
                  vram: j && j.vram_free_mb != null ? (j.vram_free_mb / 1024).toFixed(1) + "G 空闲" : p.vram,
                  alive: true,
                  tick: (p.tick || 0) + 1,   // 轮询计数：供依赖 s.tick 的下游（模型校验）定时刷新
                });
              });
            });
          }).catch(function () {
            if (stop) return;
            failStreak++;
            if (failStreak >= 3) {
              set(function (p) { return p.alive ? Object.assign({}, p, { alive: false, mode: "离线", vram: "" }) : p; });
            }
          });
          // 检测 dsh 当前深/浅色（不再展示，仅用于 iframe 跟随变色）
          var dark = readDark();
          if (!stop) set(function (p) { return p.dark === dark ? p : Object.assign({}, p, { dark: dark }); });
        }
        tick();
        var t = setInterval(tick, 5000);
        // 即时联动：dsh 切换主题时 body[data-ds-dark-theme] / html data-theme 属性会变，
        // MutationObserver 立即捕获并更新 dark，无需等下一次 5s 轮询。
        var mo = new MutationObserver(function () {
          var dark = readDark();
          set(function (p) { return p.dark === dark ? p : Object.assign({}, p, { dark: dark }); });
        });
        if (document.body) mo.observe(document.body, { attributes: true, attributeFilter: ["data-ds-dark-theme", "data-theme", "class"] });
        else document.addEventListener("DOMContentLoaded", function () { mo.observe(document.body, { attributes: true, attributeFilter: ["data-ds-dark-theme", "data-theme", "class"] }); });
        // 系统跟随变化也即时响应
        var mq = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
        var onMq = function () { set(function (p) { var d = readDark(); return p.dark === d ? p : Object.assign({}, p, { dark: d }); }); };
        if (mq && mq.addEventListener) mq.addEventListener("change", onMq); else if (mq && mq.addListener) mq.addListener(onMq);
        return function () { stop = true; clearInterval(t); if (mo) mo.disconnect(); if (mq && mq.removeEventListener) mq.removeEventListener("change", onMq); else if (mq && mq.removeListener) mq.removeListener(onMq); };
      }, []);
      return st[0];
    }

  // 状态胶囊：对齐原版实验室左下角样式（● GPU 模式 · 显存 X.XG 空闲），点击切 CPU/GPU
  function StatusCard() {
    var s = useLabStatus();
    var switching = react.useState(false), setSwitching = switching[1];
    // 收起态（rail）：侧栏变窄，绝对定位的状态卡会被裁切——切紧凑竖排固定在底部
    var rail = document.documentElement.className.indexOf("collapsed") >= 0
      || (document.querySelector('[class*="hHd-Xa_root"]') || {}).className
      && String(document.querySelector('[class*="hHd-Xa_root"]').className).indexOf("collapsed") >= 0;
    // rail 收起态：状态卡直接不渲染（窄栏放不下，且用户要求隐藏；折叠按钮独占顶部）
    if (rail) return null;
    function switchMode(ev) {
      ev.stopPropagation();
      if (switching[0] || !s.alive) return;
      var target = s.mode === "GPU" ? "cpu" : "cuda";
      if (!window.confirm("切换到 " + (target === "cpu" ? "CPU" : "GPU") + " 模式？引擎会重启加载，需等待片刻。")) return;
      setSwitching(true);
      fetch("/lab-api/backend/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: target }),
      }).catch(function () {}).then(function () { setSwitching(false); });
    }
    var modeText = switching[0] ? "切换中…" : s.alive ? s.mode + " 模式" : "离线";
    var vramText = s.alive && s.vram ? " · 显存 " + s.vram.replace(" 空闲", "") : "";
    // 模型完整性：校验失败时状态点转红并提示（点击修复走 iframe 内创作页/侧栏修复按钮）
    var vBad = react.useState(null), setVBad = vBad[1];
    react.useEffect(function () {
      if (!s.alive) return;
      fetch("/lab-api/models/verify").then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) { if (j) setVBad(j.ok ? false : (j.files || []).filter(function (f) { return !f.ok; }).length); })
        .catch(function () {});
    }, [s.alive, s.tick]);
    var badText = vBad[0] ? " · 模型缺件 " + vBad[0] + " 项" : "";
    return react.createElement("button", {
      onClick: switchMode, title: "点击切换 GPU/CPU 模式" + (vBad[0] ? "；模型缺件请到工作台侧栏点「修复」" : ""),
      "data-yue2-status": "1",
      style: {
        // 文档流内联（不再绝对定位）：footer 槽里的正常块级元素，
        // 展开/收起都跟随布局，物理上不可能遮挡折叠按钮或设置按钮。
        // 与侧栏其他条目同圆角/同内边距，视觉对齐；rail 窄栏时文字居中
        display: "flex", alignItems: "center", gap: 6,
        width: "100%", margin: "10px 0 4px", padding: "6px 10px",
        boxSizing: "border-box", justifyContent: rail ? "center" : "flex-start",
        border: "1px solid var(--dsw-specific-divider, rgba(127,127,127,.2))", borderRadius: 12,
        cursor: "pointer", fontSize: 11, background: "transparent",
        color: "var(--dsw-alias-label-secondary, #888)", whiteSpace: "nowrap", overflow: "hidden",
      },
    },
      react.createElement("span", {
        style: { width: 7, height: 7, borderRadius: 4, flexShrink: 0, display: "inline-block",
          background: !s.alive ? "#d64545" : vBad[0] ? "#e0705f" : "#1d9b57" },
      }),
      // 单行内联文本：模式 + 显存（收起态窄栏自动省略号截断，悬停 title 看全）
      react.createElement("span", null, (s.alive ? s.mode : "离线") + " " + (s.vram || "") + badText)
    );
  }

    // ------------------------------------------------------------------ //
    // 侧栏下半段：实验室页签列表 + 左下角状态卡
    // ------------------------------------------------------------------ //
    function LabNav(props) {
      var st = react.useState(null), set = st[1];
      // iframe 内实验室页程序化切页（如历史页「回填」跳创作）时，同步点亮本侧栏页签
      react.useEffect(function () {
        function onMsg(e) {
          // origin 校验：只接受同源 iframe（本插件自身）的页签同步消息
          if (e.origin && e.origin !== location.origin) return;
          var d = e.data || {};
          if (d.type === "yue2-lab-tab" && d.tab) {
            var ok = LAB_TABS.some(function (t) { return t.id === d.tab; });
            if (ok) set(d.tab);
          }
        }
        window.addEventListener("message", onMsg);
        return function () { window.removeEventListener("message", onMsg); };
      }, []);
      // 跟随原生侧栏收起态（rail 模式）：收起时页签缩为图标列、隐藏状态卡
      var col = react.useState(false), collapsed = col[0], setCollapsed = col[1];
      react.useEffect(function () {
        var root = document.querySelector('[class*="hHd-Xa_root"]');
        // 「工作区」→「歌词和曲风工作台」：dsh 原生渲染的分组标题文本，
        // 用运行时替换 + MutationObserver 守卫（React 重渲染会改回去）
        var rename = function () {
          var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
          var n;
          while ((n = walker.nextNode())) {
            if (n.nodeValue === "工作区") n.nodeValue = "歌词和曲风工作台";
          }
        };
        rename();
        var rmo = new MutationObserver(rename);
        if (document.body) rmo.observe(document.body, { childList: true, subtree: true });
        if (!root) return function () { rmo.disconnect(); };
        var sync = function () { setCollapsed(root.className.indexOf("collapsed") >= 0); };
        sync();
        var mo = new MutationObserver(sync);
        mo.observe(root, { attributes: true, attributeFilter: ["class"] });
        return function () { mo.disconnect(); rmo.disconnect(); };
      }, []);
      // 切到音乐工作台主面板：走官方机制——点击 panellist 里隐藏的
      // 「音乐工作台」入口按钮（面板可达性由它保证；selectPanel 受白名单校验不可靠）。
      function gotoStudio() {
        var btn = document.querySelector('button[aria-label="音乐工作台"], [aria-label="音乐工作台"]');
        if (btn) { btn.click(); return; }
        try {
          if (props.layout && typeof props.layout.selectPanel === "function") {
            props.layout.selectPanel("music-studio");
            return;
          }
        } catch (e) {}
      }
      var layout = props.layout;
      // 收起态（rail）：页签缩成「图标+短字」竖排列（仅 glyph 用户认不出含义）；
      // 状态卡切紧凑竖排完整显示（原绝对定位在窄栏会被裁切）
      if (collapsed) {
        return react.createElement("div", { style: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0, alignItems: "center", paddingTop: 48, borderTop: "1px solid var(--dsw-specific-divider, rgba(127,127,127,.15))" } },
          LAB_TABS.map(function (t) {
            var on = st[0] === t.id;
            return react.createElement("button", {
              key: t.id,
              title: "音乐工作台 · " + t.name,
              onClick: function () {
                set(t.id);
                gotoStudio();
                window.__yue2LabTab = t.id;
                window.dispatchEvent(new CustomEvent("yue2-lab-tab", { detail: t.id }));
              },
              style: {
                display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
                gap: 1, width: 44, height: 46, margin: "3px 0", padding: 0,
                border: 0, borderRadius: 8, cursor: "pointer",
                background: on ? "var(--dsw-specific-secondary-fill, rgba(127,127,127,.12))" : "transparent",
                color: "var(--dsw-alias-label-primary, inherit)",
              },
            },
              react.createElement("span", { style: { fontSize: 14, lineHeight: "16px" } }, t.glyph),
              react.createElement("span", { style: { fontSize: 10, lineHeight: "12px", color: "var(--dsw-alias-label-secondary, #999)" } }, t.short)
            );
          })
        );
      }
      // 对齐原生工作区风格：分组标题 + 素色行（14px/行高34px，与原生会话行一致）；
      // 容器底部 margin 与「工作区」分隔开
      return react.createElement("div", { style: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0, marginBottom: 18 } },
        react.createElement("div", {
          style: {
            fontSize: 14, fontWeight: 400, color: "var(--dsw-alias-label-secondary, #adb2b8)",
            padding: "0 0 0 4px", margin: "2px 0 4px", height: 36, display: "flex", alignItems: "center",
            borderTop: "1px solid var(--dsw-specific-divider, rgba(127,127,127,.15))", marginTop: 4,
          },
        }, "声音创作工作台"),
        LAB_TABS.map(function (t) {
          var on = st[0] === t.id;
          return react.createElement("button", {
            key: t.id,
            onClick: function () {
              set(t.id);
              gotoStudio();
              window.__yue2LabTab = t.id;
              window.dispatchEvent(new CustomEvent("yue2-lab-tab", { detail: t.id }));
            },
            style: {
              display: "flex", alignItems: "center", margin: "1px 8px", padding: "0 12px",
              height: 34, border: 0, borderRadius: 8, cursor: "pointer", fontSize: 14, textAlign: "left",
              width: "calc(100% - 16px)", boxSizing: "border-box", flexShrink: 0,
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              background: on ? "var(--dsw-specific-secondary-fill, rgba(127,127,127,.12))" : "transparent",
              color: "var(--dsw-alias-label-primary, inherit)",
              fontWeight: on ? 600 : 400,
            },
            onMouseEnter: function (e) { if (!on) e.currentTarget.style.background = "var(--dsw-alias-interactive-bg-hover, rgba(127,127,127,.09))"; },
            onMouseLeave: function (e) { if (!on) e.currentTarget.style.background = "transparent"; },
          }, t.name);
        }),
        react.createElement("div", { style: { flex: 1, minHeight: 0 } })
      );
    }

    // ------------------------------------------------------------------ //
    // 主面板：全尺寸 iframe 复用原版实验室页面
    // ------------------------------------------------------------------ //
    function MusicStudio() {
      var ref = react.useRef(null);
      var dark = useLabStatus().dark;
      // 浏览器标签页：音乐工作台 + 琥珀铜 favicon（与主界面主题色一致，避免 AI 味蓝紫）
      react.useEffect(function () {
        // 标签栏文字固定为「音乐工作台」：MutationObserver 守卫，
        // dsh 框架或其他脚本改动 title 时立即改回
        var fixTitle = function () { if (document.title !== "音乐工作台") document.title = "音乐工作台"; };
        fixTitle();
        var titleMo = new MutationObserver(fixTitle);
        titleMo.observe(document.querySelector("title") || document.head, { childList: true, characterData: true, subtree: true });
        var svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
          + "<defs><linearGradient id='g' x1='0' y1='0' x2='0' y2='1'>"
          + "<stop offset='0' stop-color='%23c4762a'/><stop offset='1' stop-color='%2396501a'/>"
          + "</linearGradient></defs>"
          + "<rect width='64' height='64' rx='14' fill='url(%23g)'/>"
          // 顶缘高光：拟物质感
          + "<rect x='8' y='4' width='48' height='3' rx='1.5' fill='white' opacity='0.35'/>"
          // 双音符
          + "<path d='M27 46V16l20-5v29' fill='none' stroke='white' stroke-width='4.5' stroke-linecap='round' stroke-linejoin='round'/>"
          + "<ellipse cx='21' cy='46' rx='6.5' ry='5.5' fill='white'/>"
          + "<ellipse cx='41' cy='40' rx='6.5' ry='5.5' fill='white'/></svg>";
        var link = document.querySelector("link[rel*='icon']");
        if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
        link.type = "image/svg+xml";
        link.href = "data:image/svg+xml," + svg;
        // favicon 任务状态：空闲=琥珀铜音符；有任务进行中=绿色圆点角标（右上）。
        // iframe 内的 favicon 在 dsh 环境不可见（标签页图标取顶层文档），故在顶层轮询切换。
        var busySvg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
          + "<defs><linearGradient id='g' x1='0' y1='0' x2='0' y2='1'>"
          + "<stop offset='0' stop-color='%23c4762a'/><stop offset='1' stop-color='%2396501a'/>"
          + "</linearGradient></defs>"
          + "<rect width='64' height='64' rx='14' fill='url(%23g)'/>"
          + "<path d='M27 46V16l20-5v29' fill='none' stroke='white' stroke-width='4.5' stroke-linecap='round' stroke-linejoin='round'/>"
          + "<ellipse cx='21' cy='46' rx='6.5' ry='5.5' fill='white'/>"
          + "<ellipse cx='41' cy='40' rx='6.5' ry='5.5' fill='white'/>"
          // 忙碌角标：右上大号琥珀橙圆点（一眼与空闲铜色音符区分）
          + "<circle cx='49' cy='15' r='13' fill='%23ff9d2e' stroke='white' stroke-width='4'/>"
          + "<circle cx='49' cy='15' r='5' fill='white'/></svg>";
        var idleHref = link.href;
        var busyHref = "data:image/svg+xml," + busySvg;
        var favBusy = false;
        setInterval(function () {
          var running = false;
          try {
            var x1 = new XMLHttpRequest();
            x1.open("GET", "/lab-api/batch/status", false); x1.send();
            if (x1.ok) { var j = JSON.parse(x1.responseText); running = !!j.running; }
            if (!running) {
              var x2 = new XMLHttpRequest();
              x2.open("GET", "/lab-api/history/active", false); x2.send();
              if (x2.ok) { running = (JSON.parse(x2.responseText).items || []).length > 0; }
            }
          } catch (e) {}
          if (running !== favBusy) {
            favBusy = running;
            link.href = running ? busyHref : idleHref;
          }
        }, 8000);
      }, []);
      react.useEffect(function () {
        if (!ref.current) return;
        // 装载即带 pending 页签（侧栏在 iframe 装载前点过页签也不丢）；
        // 无 pending 时默认「创作」（compose）。
        var tab = window.__yue2LabTab || "compose";
        ref.current.src = "/lab/?embed=1" + (tab ? "#" + tab : "");
      }, []);
      // 跟随 dsh 深浅色：写实验室页的 html[data-theme] + postMessage 通知其重算派生色
      react.useEffect(function () {
        pushThemeToIframe(ref.current && ref.current.contentWindow, dark);
      }, [dark]);
      // iframe 加载/重载完成后再推一次主题：useEffect([dark]) 只在 dark 变化时跑，
      // 若加载时 dark 已固定则写入会丢——onLoad 补上这一发。
      react.useEffect(function () {
        var fr = ref.current;
        if (!fr) return;
        var onload = function () { pushThemeToIframe(fr.contentWindow, dark); };
        fr.addEventListener("load", onload);
        return function () { fr.removeEventListener("load", onload); };
      }, [dark]);
      react.useEffect(function () {
        function onTab(e) {
          if (ref.current && ref.current.contentWindow) {
            try { ref.current.contentWindow.location.hash = e.detail; } catch (err) { ref.current.src = "/lab/#" + e.detail; }
          }
        }
        window.addEventListener("yue2-lab-tab", onTab);
        return function () { window.removeEventListener("yue2-lab-tab", onTab); };
      }, []);
      // iframe 看门狗：长任务（如换声）执行中反代请求可能超时/断连，导致 iframe 内会话
      // 失效呈整页空白。每 10s 探测一次实验室页健康状态，连续失败则自动重载 iframe 恢复，
      // 不再需要用户手动刷新整页。探测走主文档（dsh 同源），不受 iframe 内状态影响。
      react.useEffect(function () {
        var fails = 0;
        var timer = setInterval(function () {
          fetch("/lab/?embed=1&ping=" + Date.now(), { cache: "no-store" })
            .then(function (r) {
              // 任意 HTTP 响应（含 502/504）都说明 dsh 反代层活着；只有网络层失败才计数
              fails = 0;
            })
            .catch(function () {
              fails++;
              if (fails >= 3 && ref.current) {
                fails = 0;
                var tab = window.__yue2LabTab || "compose";
                ref.current.src = "/lab/?embed=1&reloaded=" + Date.now() + (tab ? "#" + tab : "");
              }
            });
        }, 10000);
        return function () { clearInterval(timer); };
      }, []);
      return react.createElement("iframe", {
        ref: ref,
        title: "音乐工作台",
        style: { width: "100%", height: "100%", border: "0", display: "block", background: "#e8e7e4" },
      });
    }

    function StudioIcon(props) {
      return react.createElement("span", {
        style: { display: "flex", alignItems: "center", justifyContent: "center", width: "100%", height: "100%" },
        title: "音乐工作台",
      },
        react.createElement("svg", { viewBox: "0 0 20 20", width: props.size || 20, height: props.size || 20, fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinejoin: "round", strokeLinecap: "round" },
          react.createElement("path", { d: "M7 15.5V4l9-2v11.5" }),
          react.createElement("circle", { cx: 5.2, cy: 15.5, r: 1.8 }),
          react.createElement("circle", { cx: 14.2, cy: 13.5, r: 1.8 })
        )
      );
    }

    function apply(ctx) {
      // 运行标记：证明 client 半的 apply 真的被执行了（排查用）
      try { document.documentElement.setAttribute("data-yue2-ran", "1"); } catch (e) {}
      var slots = ctx.get("slots");
      var layout = ctx.get("layout");
      var disposers = [];
      // 默认展示「音乐工作台」（而非对话）：等 panellist 入口按钮渲染出来后
      // 点击它切换主面板。放在 apply 入口执行，不依赖 MusicStudio 是否已挂载。
      // 若用户手动切回对话，则记住选择、本会话内不再抢焦点。
      (function () {
        var tried = 0;
        var timer = setInterval(function () {
          if (window.__yue2LabTabUserPicked || tried++ > 40) { clearInterval(timer); return; }
          var btn = document.querySelector('button[aria-label="音乐工作台"], [aria-label="音乐工作台"]');
          if (btn) {
            clearInterval(timer);
            btn.click();
          }
        }, 250);
        document.addEventListener("click", function onPick(ev) {
          // 用户点了任何「对话/工作台」切换入口后，标记为用户主动选择
          var t = ev.target;
          if (t && t.closest && (t.closest('[aria-label="音乐工作台"]')
            || t.closest('[class*="newSession"]') || t.closest("button"))) {
            window.__yue2LabTabUserPicked = true;
            document.removeEventListener("click", onPick);
          }
        });
      })();
      if (slots) {
        // 主面板：音乐工作台（iframe 复用原版页面）
        disposers.push(slots.inject("main", function () {
          return slots.register({ name: "main", key: "music-studio" }, function () {
            return react.createElement(MusicStudio, null);
          });
        }));
        // panellist 入口：面板可达性的官方机制（CDP 实测：仅注册 main 槽时
        // selectPanel 白名单不含本面板、主区永不渲染 iframe）。保留注册但视觉隐藏，
        // 由 LabNav 页签经 gotoStudio 点击它完成切换。
        disposers.push(slots.inject("sidebar.panellist", function () {
          return slots.register({ name: "sidebar.panellist", id: "music-studio", label: "音乐工作台" }, function (props) {
            return react.createElement("span", { style: { position: "absolute", width: 1, height: 1, overflow: "hidden", opacity: 0, pointerEvents: "none" } });
          });
        }));
        // 侧栏下半段：实验室页签 + 状态卡（footer 槽在列表之后、原生底栏之前）
        disposers.push(slots.inject("sidebar.footer.action", function () {
          return slots.register({ name: "sidebar.footer.action", id: "yue2-lab-nav" }, function () {
            // 关键：footer 槽容器本身是横排 flex，LabNav 与 StatusCard 直接并排会
            // 互相挤压（页签被挤成竖排文字、状态卡撑成大方块）。包一层纵向 flex
            // 容器强制上下堆叠，两者各占整行宽度。
            return react.createElement("div", {
              style: { display: "flex", flexDirection: "column", width: "100%", minWidth: 0 },
            },
              react.createElement(StatusCard, null),
              react.createElement(LabNav, { layout: layout })
            );
          });
        }));
      }
      // 隐藏原生「新对话」按钮（侧栏顶部，对本工作台无用）；并隐藏顶部
      // 「音乐工作台」panellist 入口（保留注册以保证面板可达性，仅视觉消失）。
      var st0 = document.createElement("style");
      st0.textContent = [
        ".hHd-Xa_newSession{display:none !important;}",
        '[aria-label="音乐工作台"]{display:none !important;}',
        // 布局调换：音乐工作台（footArea）移到上方，聊天工作区在其下
        ".hHd-Xa_logoRow{order:-3 !important;}",
        ".hHd-Xa_footArea{order:-2 !important;}",
        // 设置区（sidebar.settings 槽容器）沉到侧栏最底部（页面左下角）：
        // footArea 整体被提到上方承载导航，故用绝对定位把 settingsArea 锚在根容器底部
        ".hHd-Xa_root{position:relative !important;}",
        ".hHd-Xa_settingsArea{position:absolute !important;left:12px;right:12px;bottom:6px;z-index:10;}",
        ".hHd-Xa_collapsed .hHd-Xa_settingsArea{left:auto;right:auto;}",
        // 底部留白，避免会话列表滚动到设置区之下被遮挡
        ".hHd-Xa_root{padding-bottom:64px !important;}",

        // 左上角品牌标识（DeepSeek Harness 文字/图形）隐藏，保留折叠按钮；
        // 原位置由「GPU模式·显存」状态按钮接管（StatusCard 绝对定位 top:12）
        ".hHd-Xa_logoRow > *:first-child{display:none !important;}",
        // 彻底禁止收起侧栏：隐藏折叠按钮，只保留展开态（rail 布局问题不再出现）
        ".hHd-Xa_logoRow button{display:none !important;}",
        // logoRow 内品牌/按钮均已移除，整行只剩空白占位——直接隐藏，
        // 「声音创作工作台」模块随之顶到侧栏最上方
        ".hHd-Xa_logoRow{display:none !important;}",

        // ---- 侧栏拟物材质对齐音乐工作台 UI：中性暖灰 + 键缘高光/内阴影，明暗跟随 dsh ----
        // 浅色：暖灰基材 #e8e7e4；深色：#17171a 系（与工作台 --bg 同源）
        ".hHd-Xa_root{background:linear-gradient(180deg,#eceae7 0%,#e8e7e4 100%) !important;}",
        "body[data-ds-dark-theme] .hHd-Xa_root{background:linear-gradient(180deg,#1c1b1e 0%,#17171a 100%) !important;}",
        // 原生按钮/页签行：拟物键缘（顶缘高光+底部投影），hover 轻浮起
        ".hHd-Xa_root button{border-radius:9px;transition:box-shadow .15s,background .15s;}",
        ".hHd-Xa_root button:not([data-yue2-status]){box-shadow:inset 0 1px 0 rgba(255,255,255,.55),0 1px 2px rgba(0,0,0,.06);}",
        "body[data-ds-dark-theme] .hHd-Xa_root button:not([data-yue2-status]){box-shadow:inset 0 1px 0 rgba(255,255,255,.06),0 1px 2px rgba(0,0,0,.35);}",
        ".hHd-Xa_root button:hover{box-shadow:inset 0 1px 0 rgba(255,255,255,.6),0 2px 5px rgba(0,0,0,.1);}",
        "body[data-ds-dark-theme] .hHd-Xa_root button:hover{box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 2px 6px rgba(0,0,0,.45);}",
        // 选中态页签（创作等 active 行）：下沉内嵌感，与工作台 chip 选中一致
        ".hHd-Xa_root button[aria-current='true'],.hHd-Xa_root button[class*='active']{box-shadow:var(--dsw-specific-inset-shadow,inset 0 2px 4px rgba(0,0,0,.14)) !important;}",
        // 输入/滚动区隔线淡化，让材质统一
        ".hHd-Xa_root hr,.hHd-Xa_root [class*='divider']{border-color:rgba(127,127,127,.14) !important;}",
      ].join("\n");
      document.head.appendChild(st0);
      disposers.push(function () { st0.remove(); });

      return function () { for (var i = 0; i < disposers.length; i++) try { disposers[i](); } catch (e) {} };
    }
    exports.apply = apply;

    return module.exports;
  }
});
