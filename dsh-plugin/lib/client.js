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
      { id: "compose",   name: "创作",  glyph: "♪" },
      { id: "batch",     name: "批量",  glyph: "▦" },
      { id: "rvc",       name: "换声",  glyph: "⇄" },
      { id: "voice",     name: "音色库", glyph: "🎙" },
      { id: "templates", name: "模板",  glyph: "📁" },
      { id: "history",   name: "历史",  glyph: "🕘" },
    ];

    // ------------------------------------------------------------------ //
    // 状态卡：引擎模式 / 显存 / 主题
    // ------------------------------------------------------------------ //
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
          var root = document.documentElement;
          var dark = root.classList.contains("dark") || root.dataset.theme === "dark"
            || (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
          if (!stop) set(function (p) { return p.dark === dark ? p : Object.assign({}, p, { dark: dark }); });
        }
        tick();
        var t = setInterval(tick, 5000);
        return function () { stop = true; clearInterval(t); };
      }, []);
      return st[0];
    }

  // 状态胶囊：对齐原版实验室左下角样式（● GPU 模式 · 显存 X.XG 空闲），点击切 CPU/GPU
  function StatusCard() {
    var s = useLabStatus();
    var switching = react.useState(false), setSwitching = switching[1];
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
    return react.createElement("button", {
      onClick: switchMode, title: "点击切换 GPU/CPU 模式",
      "data-yue2-status": "1",
      style: {
        position: "absolute", left: 12, right: 12, bottom: 46, zIndex: 10,
        display: "flex", alignItems: "center", gap: 6, padding: "5px 10px",
        boxSizing: "border-box", justifyContent: "flex-start",
        border: "1px solid var(--dsw-specific-divider, rgba(127,127,127,.2))", borderRadius: 16,
        cursor: "pointer", fontSize: 11, background: "transparent",
        color: "var(--dsw-alias-label-secondary, #888)", whiteSpace: "nowrap", overflow: "hidden",
      },
    },
      react.createElement("span", {
        style: { width: 7, height: 7, borderRadius: 4, flexShrink: 0, display: "inline-block",
          background: s.alive ? "#1d9b57" : "#d64545" },
      }),
      react.createElement("span", null, modeText + vramText)
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
        if (!root) return;
        var sync = function () { setCollapsed(root.className.indexOf("collapsed") >= 0); };
        sync();
        var mo = new MutationObserver(sync);
        mo.observe(root, { attributes: true, attributeFilter: ["class"] });
        return function () { mo.disconnect(); };
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
      // 收起态（rail）：分组标题与状态卡隐藏，页签缩成图标列，对齐原生 iconButton 36px 规格
      if (collapsed) {
        return react.createElement("div", { style: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0, alignItems: "center", paddingTop: 8, borderTop: "1px solid var(--dsw-specific-divider, rgba(127,127,127,.15))" } },
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
                display: "flex", alignItems: "center", justifyContent: "center",
                width: 36, height: 36, margin: "2px 0", padding: 0,
                border: 0, borderRadius: 8, cursor: "pointer", fontSize: 15,
                background: on ? "var(--dsw-specific-secondary-fill, rgba(127,127,127,.12))" : "transparent",
                color: "var(--dsw-alias-label-primary, inherit)",
              },
            }, t.glyph);
          })
        );
      }
      // 对齐原生工作区风格：分组标题 + 素色行（14px/行高34px，与原生会话行一致）
      return react.createElement("div", { style: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0 } },
        react.createElement("div", {
          style: {
            fontSize: 14, fontWeight: 400, color: "var(--dsw-alias-label-secondary, #adb2b8)",
            padding: "0 0 0 4px", margin: "2px 0 4px", height: 36, display: "flex", alignItems: "center",
            borderTop: "1px solid var(--dsw-specific-divider, rgba(127,127,127,.15))", marginTop: 4,
          },
        }, "音乐工作台"),
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
      // 浏览器标签页：LA 音乐工作台 + 乐符 favicon
      react.useEffect(function () {
        document.title = "音乐工作台";
        var svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
          // 深紫→蓝渐变底、大圆角
          + "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
          + "<stop offset='0' stop-color='%237c5cff'/><stop offset='1' stop-color='%233b82f6'/>"
          + "</linearGradient></defs>"
          + "<rect width='64' height='64' rx='16' fill='url(%23g)'/>"
          // 柔光高光
          + "<circle cx='18' cy='14' r='22' fill='white' opacity='0.12'/>"
          // 双音符：斜置琴杆 + 双音头，带投影
          + "<path d='M27 46V16l20-5v29' fill='none' stroke='white' stroke-width='4.5' stroke-linecap='round' stroke-linejoin='round'/>"
          + "<ellipse cx='21' cy='46' rx='6.5' ry='5.5' fill='white'/>"
          + "<ellipse cx='41' cy='40' rx='6.5' ry='5.5' fill='white'/></svg>";
        var link = document.querySelector("link[rel*='icon']");
        if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
        link.type = "image/svg+xml";
        link.href = "data:image/svg+xml," + svg;
      }, []);
      react.useEffect(function () {
        if (!ref.current) return;
        // 装载即带 pending 页签（侧栏在 iframe 装载前点过页签也不丢）；
        // 无 pending 时默认「创作」（compose）。
        var tab = window.__yue2LabTab || "compose";
        ref.current.src = "/lab/?embed=1" + (tab ? "#" + tab : "");
      }, []);
      // 跟随 dsh 深浅色：写实验室页的 html[data-theme]（其已有完整深色样式体系）
      react.useEffect(function () {
        var win = ref.current && ref.current.contentWindow;
        if (win && win.document && win.document.documentElement) {
          win.document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
        }
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
        style: { width: "100%", height: "100%", border: "0", display: "block", background: "#f6f7f9" },
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
            return react.createElement(react.Fragment, null,
              react.createElement(LabNav, { layout: layout }),
              react.createElement(StatusCard, null)
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
      ].join("\n");
      document.head.appendChild(st0);
      disposers.push(function () { st0.remove(); });

      return function () { for (var i = 0; i < disposers.length; i++) try { disposers[i](); } catch (e) {} };
    }
    exports.apply = apply;

    return module.exports;
  }
});
