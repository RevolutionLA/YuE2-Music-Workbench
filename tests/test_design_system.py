"""v2.2 设计系统契约测试。

背景（用户原话）：
    "当前的 UI，感觉还是老气，不够现代，不够简约，不够高级。
     我希望的质量标准是：作为品质标准，目标是所有页面达到 awwwards、webby awards、
     FWA 能获奖的品质。"

评奖级 UI 的第一条共性不是"某处好看"，而是**系统**：同一套字号/间距/圆角/动效，
处处一致。旧版的问题正是"每处都合理，合起来没节奏"——字号散落 8 个值、圆角 6 个值、
三个不同的 ease、满屏拟物浮雕与颗粒噪点。

这个测试文件锁住那次品质改造的**结构性成果**，防止后续开发把旧语言改回来。
它只做静态文本断言，不启浏览器、不跑 GPU。
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


class DesignSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = read(INDEX)
        cls.head = cls.src[: cls.src.index("</style>")]

    # ---------- 令牌层 ----------

    def test_radius_uses_scale(self):
        literal = []
        for m in re.finditer(r'border-radius:\s*([^;]+);', self.head):
            v = m.group(1).strip()
            if "var(--r-" in v or "50%" in v or "inherit" in v:
                continue
            literal.append(v)
        # 只容忍那两处方向性圆角
        allowed = {"0 3px 3px 0", "0 0 2px 2px"}
        unexpected = [v for v in literal if v not in allowed]
        self.assertEqual(unexpected, [],
                         "这些圆角没走 --r-* 令牌：%s" % unexpected)

    def test_token_layer_exists(self):
        """四套比例尺必须成组定义：字号 / 间距 / 圆角 / 动效。"""
        for tok in ("--fs-base", "--sp-5", "--r-lg", "--dur-2", "--ease"):
            self.assertIn(tok + ":", self.head, "缺少设计令牌 %s" % tok)

    def test_type_scale_is_used_not_hardcoded(self):
        """正文与控件必须走令牌，不允许再散落 10.5/11.5/12.5/13.5 这类孤立字号。

        允许的例外：100% 缩放下必须存在的 9px 角标（.tno/问号圆点）、以及
        scrollbar 这类非文字尺寸。
        """
        body = self.head
        # 抓所有 font: / font-size: 里的 px 值
        sizes = set()
        for m in re.finditer(r'font-size:\s*([0-9.]+)px', body):
            sizes.add(m.group(1))
        for m in re.finditer(r'font:\s*(?:\d+\s+)?([0-9.]+)px', body):
            sizes.add(m.group(1))
        # 令牌化之后，仍然允许的"字面"字号只应该是极少数：
        #   9px  —— 纯装饰角标
        # 其余一律通过 var(--fs-*) 引用。这里断言"字面字号不超过 2 种"。
        literal = {s for s in sizes if s not in ("9",)}
        self.assertLessEqual(len(literal), 2,
                             "head 里仍有太多字面字号 %s；请改用 var(--fs-*)" % sorted(literal))

    def test_spacing_uses_scale(self):
        """padding/gap/margin 大量走 --sp-* 令牌（抽查核心容器）。"""
        for sel in (".grid {", ".grid > div {", ".actions {"):
            # 从该选择器**顶格**出现处起查（避开媒体查询里的缩进覆盖规则）
            m = re.search(r'(?m)^  ' + re.escape(sel), self.head)
            self.assertIsNotNone(m, "找不到选择器 %s" % sel)
            chunk = self.head[m.start():m.start() + 400]
            self.assertTrue("var(--sp-" in chunk,
                            "%s 的间距没有走 --sp-* 令牌" % sel)

    def test_unified_motion(self):
        """动效收敛到 --dur-* / --ease，不再散落 .12s/.18s/.25s + 裸 ease。"""
        self.assertNotIn("transition: all .15s", self.head)
        self.assertNotIn("transition: all .12s", self.head)
        # 至少有一批组件用了统一缓动
        self.assertGreaterEqual(self.head.count("var(--ease)"), 15,
                                "统一缓动 var(--ease) 使用面太窄")

    # ---------- 去拟物 ----------

    def test_no_grain_texture_on_surfaces(self):
        """颗粒噪点纹理不得再铺在面板/侧栏上（纸质拟物的残留，最重的"老气"信号）。"""
        # --grain 变量本身保留（供历史原因兼容），但不得出现在 background 里
        self.assertNotIn("var(--grain)", self.head,
                         "还有组件在用颗粒噪点铺底；当代 UI 不该有纸质纹理")

    def test_keyboard_edge_bevels_removed_from_interactive(self):
        """按钮/输入/胶囊不得再用 --key-edge 拟物倒角（实体键语言）。"""
        for sel in (".primary {", ".ghost {", ".chip {", ".pcard {"):
            i = self.head.index(sel)
            chunk = self.head[i:i + 420]
            self.assertNotIn("var(--key-edge)", chunk,
                             "%s 还在用拟物倒角 --key-edge" % sel)

    def test_inputs_are_flat_not_grooved(self):
        """输入框不再使用深凹槽内阴影（inset 2px 是拟物表单标志）。"""
        i = self.head.index("textarea, input, select {")
        chunk = self.head[i:i + 500]
        self.assertNotIn("var(--inset)", chunk, "输入框还在用凹槽内阴影")

    # ---------- 视觉层次 ----------

    def test_page_heads_present_on_all_pages(self):
        """每一页都有页面标题块——没有锚点的页面看起来"没有层次"。"""
        heads = re.findall(r'<div class="page-head">', self.src)
        panels = re.findall(r'<section class="panel-tab"', self.src)
        self.assertEqual(len(heads), len(panels),
                         "有 %d 个面板但只有 %d 个页面标题块" % (len(panels), len(heads)))
        self.assertEqual(len(heads), 13)
        self.assertGreaterEqual(len(re.findall(r'<h1 class="page-title">', self.src)), 13)

    def test_primary_output_column_is_marked(self):
        """主产出列（pane-out）有铜色顶线，把"结果栏"与"原料栏"分开。"""
        self.assertIn(".pane-out::before", self.head)
        self.assertIn("linear-gradient(90deg, var(--acc)", self.head)

    def test_reduced_motion_respected(self):
        """系统开启"减弱动态效果"时必须关掉位移动画（无障碍硬要求）。"""
        self.assertIn("prefers-reduced-motion", self.head)

    def test_focus_visible_ring_uses_accent(self):
        """键盘焦点环用主题色 —— 键盘用户才看得见自己在哪。"""
        self.assertIn(":focus-visible", self.head)
        i = self.head.index("button:focus-visible")
        chunk = self.head[i:i + 600]
        self.assertIn("outline: 2px solid var(--acc)", chunk)

    def test_every_outline_none_has_a_focus_visible_replacement(self):
        """`outline: none` 必须配一个 :focus-visible 替代环，否则键盘焦点就消失了。

        这是最容易被"顺手清掉浏览器默认描边"破坏的一处：
        清掉描边本身没错（默认描边丑），错的是清完不给替代。
        """
        self.assertIn("outline: none", self.head)
        self.assertGreaterEqual(
            self.head.count(":focus-visible"), 3,
            "focus-visible 覆盖太少，可能有人把某类控件的焦点环删了")
        # 输入框用的是 :focus（它不需要 focus-visible 区分，敲字时本来就该有反馈）
        self.assertIn("textarea:focus, input:focus, select:focus {", self.head)

    # ---------- 无障碍：屏幕阅读器播报 ----------

    def test_screen_reader_live_regions_exist(self):
        """必须有 polite / assertive 两个播报区。

        这个工具的任务动辄跑几分钟，进度只出现在屏幕上的某一行字里；
        没有 live region 的话，读屏用户完全不知道跑到哪了 —— 这是硬伤不是小事。
        """
        self.assertIn('id="srStatus"', self.src)
        self.assertIn('id="srAlert"', self.src)
        self.assertIn('role="status"', self.src)
        self.assertIn('role="alert"', self.src)
        self.assertIn('aria-live="polite"', self.src)
        self.assertIn('aria-live="assertive"', self.src)

    def test_toast_announces_to_screen_reader(self):
        """toast() 必须把消息也送进播报区，不能只画在屏幕上。"""
        i = self.src.index("function toast(")
        chunk = self.src[i:i + 600]
        self.assertIn("announce(", chunk, "toast 没有播报")
        self.assertIn('type === "err"', chunk,
                      "错误提示应当走 assertive，不能和普通提示一样懒洋洋地等")

    def test_status_panels_auto_announce(self):
        """状态面板的变更要自动播报，且播报的是"真正变化的那个面板"。"""
        self.assertIn("watchStatusAnnouncements", self.src)
        i = self.src.index("function watchStatusAnnouncements")
        chunk = self.src[i:i + 1800]
        # 必须用 closest 定位本次变更的面板，而不是 querySelector 猜第一个
        self.assertIn("closest(", chunk,
                      "又用 querySelector 猜面板了：页面上有多个 .status，会播报错的那一个")
        self.assertIn("MutationObserver", chunk)
        # 必须有节流，否则进度高频刷新会把读屏刷成噪音
        self.assertIn("DELAY", chunk)

    def test_announce_helper_is_hoisted_safe(self):
        """announce 必须是函数声明（会被提升），因为 toast 等调用点可能先于它求值。"""
        self.assertIn("function announce(", self.src)

    # ---------- 错误文案：不许把底层异常直接甩给用户 ----------

    def test_friendly_error_translator_exists(self):
        """必须有一个把底层异常翻译成人话的收口函数。"""
        self.assertIn("function friendlyErr(", self.src)
        i = self.src.index("function friendlyErr(")
        chunk = self.src[i:i + 1400]
        for pat in ("Failed to fetch", "is not valid JSON", "aborted", "50"):
            self.assertIn(pat, chunk, "friendlyErr 没覆盖 %r 这类错误" % pat)

    def test_no_raw_error_message_in_user_facing_text(self):
        """用户可见文案里不得再直接拼"异常对象"的 message。

        曾经的样子：设置页把 `Unexpected token '<', "<!DOCTYPE "... is not valid JSON`
        原样糊在页面上——用户既看不懂，也猜不到"哦是网关没起来"。

        注意区分两类 message：
        · `e.message` / `err.message` —— 捕获到的异常，内容不可控（可能是解析器黑话）→ 必须翻译；
        · `j.message` —— 服务器**成功响应**里主动给的状态文案，本来就写给人看 → 保持原样。
        所以只扫异常变量，别误伤服务器文案。
        """
        bad = []
        for m in re.finditer(
                r'''["'`][^"'`\n]{0,40}["'`]\s*\+\s*(?:String\(\s*)?(e2?|err)\.message''',
                self.src):
            line_no = self.src[:m.start()].count("\n") + 1
            bad.append("第 %d 行: %s" % (line_no, m.group(0).strip()))
        self.assertEqual(bad, [],
                         "这些地方还在直接展示原始异常，请改用 friendlyErr()：\n  " +
                         "\n  ".join(bad))
        # 反向确认：服务器主动给的 j.message 不该被误改成 friendlyErr
        self.assertIn("j.message", self.src,
                      "服务器状态文案不该被一并替换掉")

    def test_friendlyErr_is_idempotent_safe(self):
        """friendlyErr 可能被串联调用（onGiveUp 收到的是已经翻译过的 Error），
        必须对"已经是人话"的输入原样返回，不能二次改写或抛异常。"""
        i = self.src.index("function friendlyErr(")
        chunk = self.src[i:i + 1600]
        # 兜底分支必须是"原样返回（截断）"，而不是再套一层包装
        self.assertIn("return m.slice(0, 200);", chunk,
                      "兜底分支变了：对认不出的错误应当原样返回，不要编造原因")

    def test_friendlyErr_syntax_is_valid(self):
        """整份主脚本必须能通过 JS 语法检查（历史上被正则批量替换搞坏过一次）。"""
        import subprocess
        blocks = re.findall(r'<script>(.*?)</script>', self.src, re.S)
        self.assertTrue(blocks, "找不到主脚本")
        main = max(blocks, key=len)
        tmp = os.path.join(ROOT, ".tmp_test_syntax_check.js")
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write(main)
        try:
            node = None
            for cand in (
                os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "node",
                             "versions", "22.22.2-6", "node.exe"),
                r"C:\Program Files\nodejs\node.exe",
            ):
                if os.path.exists(cand):
                    node = cand
                    break
            if not node:
                self.skipTest("本机没有可用的 node，跳过语法检查")
            r = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0,
                             "主脚本 JS 语法错误：\n" + (r.stderr or "")[:800])
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ---------- 遗留清理 ----------

    def test_no_stale_duplicate_wording(self):
        """文案不得出现机械拼接的重复（曾出现"歌词+style+歌词+style+乐谱生成歌曲"）。"""
        self.assertNotIn("歌词+style+歌词+style", self.src)

    def test_dock_height_is_tokenised(self):
        """播放坞高度走 --dock-h 令牌，两栏可视高度靠它统一扣除，避免 magic number 错位。"""
        self.assertIn("--dock-h:", self.head)
        self.assertIn("var(--dock-h)", self.head)
        self.assertNotIn("calc(100vh - 104px)", self.head,
                         "两栏高度不该再用 104px 这种魔法数")

    def test_dock_file_input_is_themed(self):
        """原生文件控件不得裸露（浏览器默认"选择文件/未选择任何文件"是唯一没被主题接管的零件）。"""
        self.assertIn('class="visually-hidden"', self.src)
        self.assertIn(".dock-pick", self.head)
        self.assertIn(".visually-hidden", self.head)

    def test_parameter_grid_self_fits(self):
        """参数网格用 auto-fit，避免固定 3 列 + 第 4 个控件孤行的破窗。"""
        i = self.head.index(".pgrid {")
        chunk = self.head[i:i + 200]
        self.assertIn("auto-fit", chunk)

    def test_page_title_is_a_real_anchor(self):
        """页面标题必须明显跳出正文（26px vs 13px 正文）。

        16px 的"标题"只比 12px 副标题大一档，整页没有视觉锚点——
        这是"不够高级"最直接的来源之一，不能改回去。
        """
        self.assertIn("--fs-3xl:", self.head)
        m = re.search(r'--fs-3xl:\s*(\d+)px', self.head)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 24,
                                "页面标题应当 ≥24px 才能成为整页锚点")
        i = self.head.index(".page-title {")
        self.assertIn("var(--fs-3xl)", self.head[i:i + 200],
                      "页面标题没有用 --fs-3xl 这个展示级字号")

    # ---------- 无障碍：对比度（静态核算，不依赖浏览器） ----------

    @staticmethod
    def _lum(hexcolor):
        h = hexcolor.lstrip("#")
        parts = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
        parts = [(c / 12.92) if c <= 0.04045 else (((c + 0.055) / 1.055) ** 2.4)
                 for c in parts]
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]

    @classmethod
    def _contrast(cls, a, b):
        la, lb = cls._lum(a), cls._lum(b)
        if la < lb:
            la, lb = lb, la
        return (la + 0.05) / (lb + 0.05)

    @staticmethod
    def _mix(a, b, t):
        A = [int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        B = [int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        return "#%02x%02x%02x" % tuple(
            int(round(A[i] * (1 - t) + B[i] * t)) for i in range(3))

    def test_accent_presets_meet_aa_in_both_themes(self):
        """每个主题预设色在两套主题下都要过 AA(4.5:1)。

        亮色主按钮是【白字压主色】，深色是【白字压主色 + hover 压暗】。
        旧版两处都不达标：亮色 #b0651c 只有 4.45:1（刚好卡在线下），
        深色直接把用户选的原色当键面，选石墨/绛红这类暗色时只有 2.9:1。
        这条断言就是防止有人"顺手调个色"又把可读性调坏。
        """
        m = re.search(r"const ACCENT_PRESETS = \[(.*?)\n\];", self.src, re.S)
        self.assertIsNotNone(m, "找不到 ACCENT_PRESETS")
        presets = re.findall(r'\["(#[0-9a-fA-F]{6})",\s*"([^"]+)"\]', m.group(1))
        self.assertEqual(len(presets), 8, "预期 8 个预设色")

        failures = []
        for hexv, name in presets:
            light_btn = self._contrast("#ffffff", hexv)
            dark_btn = self._contrast("#1d1003", self._mix(hexv, "#ffffff", 0.42))
            if light_btn < 4.5:
                failures.append("%s %s 亮色主按钮 %.2f" % (name, hexv, light_btn))
            if dark_btn < 4.5:
                failures.append("%s %s 深色主按钮 %.2f" % (name, hexv, dark_btn))
        self.assertEqual(failures, [],
                         "这些预设色对比度不达标：\n  " + "\n  ".join(failures))

    def test_hover_states_meet_aa_in_both_themes(self):
        """★ hover 态也必须过 AA —— 上面那条只验"静止态"，漏掉的恰恰是最容易坏的地方。

        实测（2026-10-09）：深色主按钮的 hover 原来用 --acc-hi（往白混 10%），
        白字压在"变亮的底"上，8 个预设色里 5 个掉到 AA 线下
        （#9a6700 4.05、#8250df 4.20、#1a7f37 4.21、#0969da 4.37、#0f766e 4.49）。
        规律：**白字底往上提亮 = 对比度必然下降**，所以深色 hover 必须往黑走。
        这条断言把"hover 也不能掉线"钉住，两种主题各验一次。
        """
        m = re.search(r"const ACCENT_PRESETS = \[(.*?)\n\];", self.src, re.S)
        presets = re.findall(r'\["(#[0-9a-fA-F]{6})",\s*"([^"]+)"\]', m.group(1))
        self.assertEqual(len(presets), 8)

        failures = []
        for hexv, name in presets:
            # 静止态与 hover 态（hover = --acc-btn-lo = 往黑混 20%）都要过 AA
            for label, face in (("静止", hexv),
                                ("hover", self._mix(hexv, "#000000", 0.20))):
                r = self._contrast("#ffffff", face)
                if r < 4.5:
                    failures.append("%s %s %s %.2f" % (name, hexv, label, r))
        self.assertEqual(failures, [],
                         "这些 preset 的 hover 态对比度不达标：\n  " + "\n  ".join(failures))

    def test_primary_hover_darkens_not_lightens(self):
        """主按钮的 hover 必须把底面**压暗**，两套主题都不许提亮。

        只验数值不够 —— 有人可以把 hover 改回 var(--acc-hi) 同时把混色比例调小蒙混过去。
        这里直接盯住规则本身：两个 .primary:hover 的 background 都必须是 --acc-btn-lo。
        （实测：提亮版本让浅色 8/8 掉到 2.82～3.71:1、深色 5/8 掉线。）
        """
        self.assertIn(".primary:hover:not(:disabled) { background: var(--acc-btn-lo);",
                      self.head, "浅色主按钮 hover 又变回提亮端了（白字底提亮 = 对比度必降）")
        self.assertIn("html[data-theme=\"dark\"] .primary:hover:not(:disabled) "
                      "{ background: var(--acc-btn-lo); }", self.head,
                      "深色主按钮 hover 又变回提亮端了")

    def test_shell_is_the_same_layer_as_panels(self):
        """常驻外壳（侧栏/播放坞）必须与主区卡片同一层面，不能另开一套颜色。

        用户原话："深色模式我感觉三块是差不多的，但浅色模式，三块还是明显不统一。"
        查证结果：深色下 --shell 本来就等于 --panel，所以"差不多"；
        浅色下外壳被单独画成了近黑（先 #1c2128 再 #3c4149），
        相对明度 0.015/0.052 压在主区 0.843 旁边 —— 那不是"深一档"，是黑白同框。
        唯一稳的写法是让 --shell 直接指向 --panel：两套主题自动同构，
        以后改主题色外壳会跟着走，不会再出现"三块不统一"。
        """
        decls = re.findall(r"^\s{4}--shell:\s*([^;]+);", self.head, re.M)
        self.assertEqual(len(decls), 2, "预期明暗两套各有一条 --shell 声明，实际 %d" % len(decls))
        for d in decls:
            self.assertEqual(d.strip(), "var(--panel)",
                             "--shell 又写死成 %s 了：外壳必须与主区卡片同层" % d.strip())
        # 外壳内的强调色必须是主题感知的"可读档"，不是 applyAccent 写进内联样式的原色
        self.assertIn("--shell-acc: var(--acc-text);", self.head,
                      "外壳强调色不能直接用 --acc（它不跟主题翻，深色下只有 3.33:1）")

    def test_default_accent_meets_aa(self):
        """默认铜色（--acc）配白字必须过 AA；同时确认深色默认键面不再用原色。"""
        m = re.search(r'^\s{4}--acc:\s*(#[0-9a-fA-F]{6});', self.head, re.M)
        self.assertIsNotNone(m)
        ratio = self._contrast("#ffffff", m.group(1))
        self.assertGreaterEqual(ratio, 4.5,
                                "--acc %s 配白字只有 %.2f:1，低于 AA" % (m.group(1), ratio))

    def test_dark_primary_button_face_is_lightened(self):
        """深色主按钮的键面必须是"提亮端"，不能是用户选的原色。"""
        i = self.src.index("function applyAccent(hex)")
        chunk = self.src[i:i + 2200]
        self.assertIn('"--acc-chip-hi": dark ? mix(hex, "#ffffff", 0.42)', chunk,
                      "深色主按钮键面又变回原色了（暗色预设会掉到 2.9:1）")

    def test_legacy_accent_is_migrated(self):
        """老用户 localStorage 里的旧默认色必须自动搬到新默认色。

        不改的话，新代码对"已经打开过工作台的人"永远不生效——
        他们看到的仍是旧配色，会以为改版没做。允许有多个映射（默认色换过几次），
        但每一个"迁移目标"都必须过 AA，且旧值本身不达标（否则不该迁）。
        """
        self.assertIn("ACCENT_LEGACY", self.src)
        block = self.src[self.src.index("const ACCENT_LEGACY"):
                         self.src.index("}", self.src.index("const ACCENT_LEGACY"))]
        pairs = re.findall(r'"(#[0-9a-fA-F]{6})":\s*"(#[0-9a-fA-F]{6})"', block)
        self.assertTrue(pairs, "找不到 ACCENT_LEGACY 映射")
        # #b0651c 是历史上那个"配白字 4.45:1、刚好卡在 AA 线下"的坏默认值，
        # 它必须出现在迁移表里且确实不达标。
        olds = [p[0] for p in pairs]
        self.assertIn("#b0651c", [o.lower() for o in olds],
                      "那个不达标的旧默认色没有被迁移")
        self.assertLess(self._contrast("#ffffff", "#b0651c"), 4.5)
        # 每个迁移目标都必须过 AA（主按钮就是白字压强调色）
        for _, new in pairs:
            self.assertGreaterEqual(self._contrast("#ffffff", new), 4.5,
                                    "迁移目标 %s 配白字不达标" % new)
        # 迁移逻辑被真正接进初始化路径
        self.assertIn("applyAccent(loadAccent())", self.src,
                      "主题色初始化没有走 loadAccent()，旧值不会被迁移")

    # ---------- ⑪ 任务管理表 ----------

    def test_hist_table_columns_are_content_independent(self):
        """任务管理表的列宽不能随行内容变化。

        用户原话："「任务管理」的UI还是太过乱了，作为用户，不便于浏览、筛选。"

        实测证据（1920x1174，30 条真实形状数据）：旧实现
        `grid-template-columns: minmax(140px,1.4fr) 92px minmax(120px,2fr) 150px auto`
        的**最后一档是 auto** —— 它按内容（操作按钮）取宽，而每一行是**各自独立的 grid
        容器**。按钮文案不同 → 该行 auto 宽度不同 → 留给两个 fr 的剩余空间不同 →
        同一列在不同行解出不同宽度：状态列 x ∈ {649,718,751,784,816}（5 个值），
        元信息列 3 个值、操作列 3 个值。眼睛识别不了"哪一格是什么"，这就是"乱"。

        契约：列模板里**不允许出现内容驱动的尺寸**（auto / max-content / min-content
        / fit-content）——凡是按内容取宽的轨道，都会让列位置逐行漂移。
        fr 本身没问题（所有行同模板、同容器宽，fr 必然解出同值），
        前提是没有任何一档按内容取宽。
        """
        # 列模板只允许出现一次定义 + 一次窄屏覆盖，且必须走令牌
        tpls = re.findall(r"grid-template-columns:\s*(var\(--hcol[^;]+);", self.head)
        self.assertTrue(tpls, "任务管理表的列模板没有走 var(--hcol-*) 令牌")
        for t in tpls:
            for tok in re.findall(r"var\((--hcol-[a-z]+)\)", t):
                self.assertIn(tok, self.head, "列令牌 %s 没有定义" % tok)
        # 令牌不得含内容驱动尺寸——这是"每行列位置不同"的根因
        for name, val in re.findall(r"^\s{4}(--hcol-[a-z]+):\s*([^;]+);", self.head, re.M):
            for bad in ("auto", "max-content", "min-content", "fit-content"):
                self.assertNotIn(bad, val,
                                 "%s=%s 含内容驱动尺寸 %s：列宽会随行内容变、列位置对不齐"
                                 % (name, val, bad))
        # 网格只作用于 #histList 内的行：.hist-item 是共享类（换声/训练/分离列表都用），
        # 写在共享类上会把那几个"内容纵向铺满"的列表也扭成五列表
        i = self.head.index("grid-template-columns: var(--hcol-task)")
        sel = self.head[max(0, i - 300):i]
        self.assertIn("#histList .hist-item", sel,
                      "五列网格写到了共享的 .hist-item 上，会污染换声/训练/分离列表")

    def test_hist_table_has_visible_column_header(self):
        """表必须有列头，且列头与行共用同一份列模板。

        实测：旧实现 hasColumnHeader=false —— 五列各是什么只能靠猜。
        """
        self.assertIn(".h-head", self.head, "任务管理表没有列头样式")
        # 列头必须和行同模板（否则"任务"两字压不到任务列上）
        m = re.search(r"#histList \.hist-item, #histList \.h-head\s*\{([^}]*)\}", self.head)
        self.assertIsNotNone(m, "列头与行没有共用列模板选择器")
        self.assertIn("--hcol-task", m.group(1))
        # 渲染代码里真的 appendChild 了列头
        self.assertIn('head.className = "h-head"', self.src,
                      "renderHistory 没有渲染列头")

    def test_hist_row_height_is_uniform(self):
        """行高必须唯一——失败行不能因为多一行错误说明撑高。

        实测：旧实现行高有 72px 和 90px 两个值（失败行多一行 inline 错误文本），
        一列里两种行高交替，扫读节奏全乱。
        """
        # 错误行被移出文档流（absolute），因此不参与行高计算
        i = self.head.index(".hist-item .h-errline")
        chunk = self.head[i:i + 320]
        self.assertIn("position: absolute", chunk,
                      "错误行又回到文档流里了：失败行会比其它行高")
        self.assertIn("overflow: hidden", self.head[self.head.index(".hist-item .h-sub"):
                                                   self.head.index(".hist-item .h-sub") + 200])

    def test_hist_all_row_kinds_share_the_five_column_skeleton(self):
        """⑪ 里**每一种行**（生成 / 换声 / 训练 / 乐谱 / 分离）都必须落 5 个格子。

        用户原话："「任务管理」的UI还是太过乱了，作为用户，不便于浏览、筛选。"

        实测（1920x1174，⑪ 共 61 行）：
          · 生成行「如诗」       → h=72，动作在 .h-actions →
          · 乐谱行「🎼 乐谱提取」 → h=168 ← 比别的行高一倍多
          · 列表尾巴「加载更多」 → h=67 ← 第三种行高
        根因有两个，都不在 CSS：
          ① lightTaskRow()（乐谱/分离）自己搭了"3 列 .h-col"布局，4 颗按钮全塞进最后一个
             .h-col（块级，按钮各自成行）⇒ 行高 4×36+padding ≈ 168。
             CSS grid 是**按顺序填轨道**的，少一个格子后面全部左移一列 ——
             这张表的列还对不对得齐，居然取决于行的类型。
          ② 分页尾巴穿了 class="hist-item" 的衣服，于是继承了 padding/边框/列网格，
             看起来像第 61 条任务，还多出 67px 这一档行高。
        契约：渲染出的每个 .hist-item 都必须恰好 append 5 个子元素，且动作在 .h-actions 里；
              列表尾巴必须用 .hist-more，不许复用 .hist-item。
        """
        # ① lightTaskRow 必须 5 格 + 动作进 .h-actions
        #    （取"函数开头 → 下一个顶层函数"之间的整段；按字数切片会在函数变长时截断，
        #      于是断言看着在、其实量的是空气）
        i = self.src.index("function lightTaskRow(h)")
        j = self.src.index("async function exportScoreById", i)
        body = self.src[i:j]
        self.assertIn("d.appendChild(col); d.appendChild(st); d.appendChild(sum);", body,
                      "lightTaskRow 没有铺满 5 个格子（后面几列会整体左移）")
        self.assertIn("d.appendChild(meta); d.appendChild(acts);", body,
                      "lightTaskRow 少了元信息格或操作格")
        self.assertIn('acts.className = "h-actions"', body,
                      "lightTaskRow 的按钮没有进 .h-actions（会失去右对齐/定宽/不撑宽三条规矩）")
        # 不许再出现"把按钮塞进 .h-col"的老写法
        self.assertNotIn('acts.className = "h-col"', body,
                         "lightTaskRow 又用 .h-col 当操作容器了（块级里按钮会各自成行）")
        # ② 列表尾巴必须有自己的类
        self.assertIn('more.className = "hist-more"', self.src,
                      "「加载更多」没有用 .hist-more（会继承 .hist-item，多出一种行高）")
        self.assertNotIn('more.className = "hist-item"', self.src,
                         "「加载更多」又穿 .hist-item 的衣服了")
        m = re.search(r"\.hist-more\s*\{([^}]*)\}", self.head)
        self.assertIsNotNone(m, "找不到 .hist-more 规则")
        self.assertIn("justify-content: center", m.group(1),
                      "列表尾巴没有居中（它不是一行数据）")
        # ③ 操作簇要容得下 4 颗按钮：236 − 3×gap(6) = 218 ⇒ 每颗上限 54.5
        mb = re.search(r"\.hist-item \.h-actions > button\s*\{([^}]*)\}", self.head)
        self.assertIsNotNone(mb, "找不到操作按钮规则")
        minw = int(re.search(r"min-width:\s*(\d+)px", mb.group(1)).group(1))
        self.assertLessEqual(minw, 54,
                             "操作按钮 min-width=%dpx，4 颗放不进 236px 的簇"
                             "（4×%d+3×6=%d > 236，nowrap 会溢出到下一列）"
                             % (minw, minw, minw * 4 + 18))

    def test_hist_action_cluster_is_right_pinned(self):
        """操作列必须定宽右对齐，按钮不得随文案撑宽整簇。

        实测：旧实现操作簇右边界在第 1 行 x=1852、第 8 行 x=1445（差 407px），
        "▶ 播放"(77px) 与 "▶ 成品（带伴奏）"(142px) 让整簇宽度在 78~239 之间跳，
        鼠标要追着按钮跑。改后：每行右边界恒为 1852。
        """
        m = re.search(r"\.hist-item \.h-actions\s*\{([^}]*)\}", self.head)
        self.assertIsNotNone(m, "找不到 .h-actions 规则")
        rule = m.group(1)
        self.assertIn("justify-content: flex-end", rule, "操作簇没有右对齐")
        self.assertIn("flex-wrap: nowrap", rule, "操作簇会换行，行高随之变化")
        # 按钮按内容自然宽（flex-grow:0），不能 flex:1 均分（会把"▶ 播放"拉成横条）。
        # shrink 允许 1：一簇要容下 4 颗（乐谱行）时，差几像素靠一起收，
        # 而不是让整簇溢出到下一列（min-width 有下限兜底，不会塌成看不见）。
        bm = re.search(r"\.hist-item \.h-actions > button\s*\{([^}]*)\}", self.head)
        self.assertIsNotNone(bm, "找不到操作按钮规则")
        br = bm.group(1)
        self.assertRegex(br, r"flex:\s*0\s+[01]\s+auto",
                         "按钮被拉宽了（flex-grow:1 均分会把短标签拉成横条）")
        self.assertIn("text-overflow: ellipsis", br, "长标签没有 ellipsis 收口")

    def test_control_height_decoupled_from_font_size(self):
        """控件高度必须由 height 给定，不能靠"字号 + 上下 padding"累加。

        用户原话："整个3081端口的页面你再好好打磨，主要是布局，元素间的对齐和间隔。"

        实测（1920x1174，跨 9 个页面统计）：旧实现
          · .ghost 高度 ∈ {26,36,37,38}（应为两档：次级 / 主要）
          · input ∈ {34,37}、primary ∈ {37,38}、select = 39
        根因是 `min-height:36px; padding:9px 17px`：内容盒高 = padding + 行盒，
        而行盒跟着 font-size 走 —— 13px 的 37px、12px 的 36px、带 svg 的又不同。
        同一排"标签 + 输入 + 按钮"三个控件三种高度，一眼就是没对齐。

        契约：主要档 36 / 次级档 28（btn-sm 与 btn-xs 同为次级；"微级 26" 已废除
        —— 26 与 28 差 2px 谁也看不出，却让"数档位"这件事多出一个值），一律用 height 钉死。
        """
        # 基础按钮
        m = re.search(r"\.primary, \.ghost, \.danger\s*\{([^}]*)\}", self.head)
        self.assertIsNotNone(m, "找不到按钮基础规则")
        base = m.group(1)
        self.assertIn("height: 36px", base, "按钮主要档没有用 height 钉死（会随字号变高）")
        self.assertNotIn("padding: 9px", base, "按钮又回到 padding 累加高度的写法了")
        # 次级/微级也必须用 height（min-height 小于基础 height 时不生效）
        self.assertIn(".btn-sm { padding: 0 11px !important; height: 28px !important;", self.head,
                      ".btn-sm 必须用 height 覆盖（min-height 盖不住 height:36px）")
        self.assertIn(".btn-xs { padding: 0 10px !important; height: 28px !important;", self.head,
                      ".btn-xs 必须用 height 覆盖（并与 .btn-sm 同为次级档 28）")
        # 单行输入控件
        m2 = re.search(r'input:not\(\[type="checkbox"\]\)[^{]*\{([^}]*)\}', self.head)
        self.assertIsNotNone(m2, "找不到单行 input 的高度规则")
        self.assertIn("height: 36px", m2.group(1),
                      "单行 input 没有钉死高度（select 会被 UA 内边距撑到 39px）")
        self.assertIn("select { cursor: pointer; height: 36px;", self.head,
                      "select 没有钉死高度")
        # textarea 是唯一允许"高度表达内容量"的控件，必须保留 padding 累加
        m3 = re.search(r"textarea \{ min-height: 84px;([^}]*)\}", self.head)
        self.assertIsNotNone(m3, "textarea 基础规则不见了")
        self.assertNotIn("height: 36px", m3.group(1),
                         "textarea 被钉死成单行高了——它是多行控件，高度就是'这里写多少'")

    def test_button_height_has_exactly_two_tiers(self):
        """按钮高度必须恰好两档：主要 36 / 次级 28。

        实测（1920x1174，跨 9 个页面把所有 button.ghost 量一遍）：
        改造前 ∈ {26,36,37,38}，把 .btn-xs 从 26 收到 28、并修掉
        `.model-now .ghost{height:26px}` 之后 ∈ {28,36}，两档。

        ★ 这条规矩的意义就是"一眼能数清"。26 和 28 差 2px 谁也看不出，
          但工具量得出来、后人得重新判断哪个才对 —— 多出来的第 3 档把规矩破了。
        """
        hs = set()
        for m in re.finditer(r"(?:^|[,\s])\.?[\w-]*[^{}]*\{[^{}]*height:\s*(\d+)px[^{}]*\}", self.head):
            pass  # 宽匹配不用，下面精确取
        # 精确取三个按钮档位
        base = re.search(r"\.primary, \.ghost, \.danger\s*\{[^}]*height:\s*(\d+)px", self.head)
        sm = re.search(r"\.btn-sm\s*\{[^}]*height:\s*(\d+)px", self.head)
        xs = re.search(r"\.btn-xs\s*\{[^}]*height:\s*(\d+)px", self.head)
        self.assertIsNotNone(base, "按钮基础档没有 height")
        self.assertIsNotNone(sm, ".btn-sm 没有 height")
        self.assertIsNotNone(xs, ".btn-xs 没有 height")
        tiers = {int(base.group(1)), int(sm.group(1)), int(xs.group(1))}
        self.assertEqual(tiers, {28, 36},
                         "按钮高度档位应恰好是 {28, 36}（次级/主要），实际 %s" % sorted(tiers))
        # 不允许再有第三档的按钮高度散落在别处
        #
        # ★ 关键：选择器必须"整体都是按钮"，不能只看含不含 ghost/btn-
        #   —— `.primary svg, .ghost svg, .danger svg { height:14px }` 里的 14px 是
        #   **图标尺寸**（svg 的行高不是按钮的行高）。只要选择器里有一个 svg/icon 子选择器，
        #   这条规则管的就是图标而不是按钮本体，整条跳过。
        #   同一个坑的另一种写法：`.m-actions button svg`。判定口径统一为"扫选择器的每一段
        #   复合选择器，只要某一段的末位是 svg/img/i，就认为该段是图标尺寸"。
        ICON_TAIL = re.compile(r"(?:^|[\s>+~])(svg|img)\s*$")
        stray = []
        for m in re.finditer(r"([.:#][\w.#>-]*[^{}]*)\{([^{}]*)\}", self.head):
            sel, body = m.group(1), m.group(2)
            if "ghost" not in sel and "btn-" not in sel:
                continue
            # 逐段（按逗号）判断：任何一段落在图标上，该段就不算按钮高度
            for part in sel.split(","):
                part = part.strip()
                if not part or ICON_TAIL.search(part):
                    continue
                # 该段必须真的是按钮本体（最后一段含 .ghost/.btn-*），否则不算
                tail = part.split()[-1].split(">")[-1].split("+")[-1].split("~")[-1].strip()
                if "ghost" not in tail and "btn-" not in tail:
                    continue
                for hm in re.finditer(r"(?<!min-)height:\s*(\d+)px", body):
                    v = int(hm.group(1))
                    if v not in (28, 36):
                        stray.append("%s → height:%dpx" % (part[:50], v))
        self.assertEqual(stray, [], "按钮出现了第三档高度：" + "、".join(stray))

    def test_short_pages_fill_the_viewport(self):
        """内容偏短的页面（⑫设置/⑬关于/⑦音色制作/⑧音色库）必须把版面撑满。

        用户原话："整个3081端口的页面你再好好打磨，主要是布局，元素间的对齐和间隔。"

        实测（1920x1174）：⑬ 内容底 439px、视口 1174px —— **底下 685px 全空**；
        ⑫ 空 547px、⑧ 空 417px、⑦ 空 385px。留白在框内是设计，在框外是"没做完"。

        契约：这四个页面的主网格必须有一份 min-height（用纵向令牌算，不许魔法数）。
        """
        self.assertIn(".grid.settings:not(.compose), .grid.page-fill:not(.compose)", self.head,
                      "短页撑满的规则不见了")
        i = self.head.index(".grid.settings:not(.compose)")
        chunk = self.head[i:i + 320]
        self.assertIn("min-height:", chunk + self.head[i - 200:i + 320],
                      "短页没有 min-height")
        self.assertIn("var(--pagehead-h)", chunk + self.head[i - 200:i + 320],
                      "短页高度没有用纵向令牌算（又写魔法数了）")
        # 这四个页面真的挂了类
        for cls in ['class="grid page-fill" id="voicePaneRvc"', 'class="grid page-fill"']:
            self.assertIn(cls, self.src, "⑦/⑧ 没有挂 page-fill 类：%s" % cls)
        # ★ 例外：关于页内容天然偏短且两列悬殊（646 / 317px），撑满会把两张卡片一起
        #   拉到 1000px，卡片内空出 210 / 540px。它改为"贴合内容 + 整块居中"。
        self.assertIn(".grid.grid-about:not(.compose)", self.head,
                      "关于页的撑满例外不见了（会被重新拉到卡片内一大片空）")
        self.assertIn("align-items: start", self.head,
                      "关于页的列没有改成贴合内容")

    def test_vertical_budget_is_a_single_source(self):
        """纵向预算必须只有一份：main 的内边距 = compose 栏高公式减掉的那些。

        用户原话："整个3081端口的页面你再好好打磨，主要是布局，元素间的对齐和间隔。"

        实测（1920x1174）：改造前 main 底部 padding = dock-h + sp-8 = 82px，
        而 .grid.compose .pane 的高度按 `100vh - sp-6 - pagehead - dock-h - pane-gap` 算 ——
        两侧对"底部留多少"各持一套口径，于是 ①②③④⑤ 每页都高出视口 11px，
        逼出一条纵向滚动条；滚动条宽 10px，把可用宽度从 1920 压到 1910，
        而居中块(max-width:1680;margin:auto)因此左右各收 5px
        → ⑥ 的标题 x=115、其余页 x=120。翻页时标题**横向跳 5px**，这就是"没对齐"的真身。

        契约：① main 的下内边距 与 pane 高度公式里的下边距项必须一致；
              ② html 上必须有 scrollbar-gutter: stable（滚动条的"有无"由内容决定，
                 布局绝不能依赖它 —— 预留通道，宽度才恒定）。
        """
        # ① main 的下内边距（注意文件里有多个 main 规则：动画/侧栏/窄屏都各有一条，
        #    要取"带 calc(--dock-h ...)"那条，即主区那条）
        m = re.search(r"\n  main \{[^}]*padding:[^;]*calc\(var\(--dock-h\) \+ var\((--sp-\d)\)\)[^}]*\}", self.head)
        self.assertIsNotNone(m, "找不到主区 main 的 padding 规则（应由「坞高 + 间距令牌」构成）")
        main_pad = m.group(0)
        tok = m.group(1)
        # ② pane 高度公式里的下边距项必须引用同一个令牌
        #    （calc 里嵌 var()，正则数括号太脆；直接取 height: calc( 之后到 "));" 的那一段）
        p = re.search(r"\.grid\.compose \.pane \{[^}]*height:\s*calc\((.*?)\);\s*\}", self.head, re.S)
        self.assertIsNotNone(p, "找不到 compose 栏高公式")
        formula = p.group(1)
        self.assertIn("var(--dock-h)", formula, "栏高公式没有减掉坞高")
        self.assertIn("var(%s)" % tok, formula,
                      "栏高公式的下边距项(%s)与 main 的下内边距不一致 —— "
                      "两侧口径一分家，页面就会比视口高/矮几像素，凭空长滚动条" % tok)
        self.assertIn("var(--pagehead-h)", formula, "栏高公式没有减掉页头高")
        # ③ 滚动条通道必须固定预留
        self.assertRegex(self.head, r"html \{[^}]*scrollbar-gutter:\s*stable",
                         "html 上没有 scrollbar-gutter: stable —— "
                         "滚动条的有无会改写可用宽度，居中块随之左右跳")

    def test_history_list_scrolls_internally(self):
        """任务管理列表必须自身滚动，让筛选栏永远钉在顶部。

        用户原话："「任务管理」的UI还是太过乱了，作为用户，不便于浏览、筛选。"

        实测：旧实现列表自然生长（30 条 = 2430px），页面整体滚 —— 筛选栏一滚就没了，
        换筛选条件要先滚回顶部。现在列表封顶到剩余视口高、自己滚：
        clientH=822 而 scrollH=2428，滚到底筛选栏仍在 y=151（filterStayed=true）。
        """
        m = re.search(r"\.row-list\.page-fill-list\s*\{([^}]*)\}", self.head)
        self.assertIsNotNone(m, "找不到 .page-fill-list 规则")
        r = m.group(1)
        self.assertIn("max-height:", r, "列表没有封顶，会撑长整页")
        self.assertIn("overflow-y: auto", r, "列表没有自身滚动")
        self.assertIn("var(--pagehead-h)", r, "列表上限没有用纵向令牌算")
        self.assertIn("max(240px,", r, "列表上限没有兜底，矮窗口会压成一条缝")
        self.assertIn('class="row-list page-fill-list" id="histList"', self.src,
                      "⑪ 的列表没有挂 page-fill-list 类")

    def test_preset_row_label_shares_the_chip_line_box(self):
        """「快捷预设行」的行首小标题必须和后缀芯片同一条行盒/同一中线。

        用户原话："整个3081端口的页面你再好好打磨，主要是布局，元素间的对齐和间隔。"

        实测（1920x1174，⑦ 变调快捷行）：`.preset-row{align-items:center}` 里放一个
        19px 的行内 <span> 和四颗 28px 的 .btn-sm，结果是"各自居中" ——
        两段文字中线差 4~5px，`sameRowY` 量出 2 个不同的 y。肉眼一眼就是没对齐。

        契约：行首维度标签一律 `.row-label`（inline-flex + 固定 28px 行盒 + 文字垂直居中），
        于是它与 .btn-sm 是同一档高度，中线天然重合，不靠猜 padding。
        """
        m = re.search(r"\.preset-row > \.row-label\s*\{([^}]*)\}", self.head)
        self.assertIsNotNone(m, "找不到 .preset-row > .row-label 规则")
        r = m.group(1)
        self.assertIn("display: inline-flex", r, "行首标签不是行盒，会和芯片各居其位")
        self.assertIn("align-items: center", r, "行首标签内的文字没有垂直居中")
        self.assertIn("height: 28px", r,
                      "行首标签的高度不等于次级档 28px，中线对不上 .btn-sm")
        self.assertIn("flex: none", r, "行首标签会被压缩换行")
        # 预设行里不许再留裸的 .t-sm 行首标签（会退回 19px 行盒）
        rows = re.findall(r'<div class="preset-row"[^>]*>(.*?)</div>', self.src, re.S)
        self.assertTrue(rows, "找不到任何 preset-row")
        for body in rows:
            for lm in re.finditer(r'<span class="([^"]*)"', body):
                cls = lm.group(1)
                self.assertNotIn("t-sm", cls.split(),
                                 "预设行里还有裸 .t-sm 行首标签，高度会对不齐：%s" % cls)
                self.assertIn("row-label", cls.split() or [],
                              "预设行行首标签没有挂 .row-label：%s" % cls)

    def test_ailab_height_is_layout_derived_not_a_magic_number(self):
        """⑬-AI 工作台的 100vh 扣减不许再写魔法数，必须由 flex 列自己算出来。

        实测（1920x1174，#ailab）：`.ai-frame-wrap{height:calc(100vh - 190px)}` 算出 984px，
        但这一页的真实 chrome 开销是 216px（main 顶距 20 + 页头外框 + 上方 .sec 的 24/12 上下 margin
        + 播放坞让位 62）—— 写 190 少算了 26px。后果：整页 1200px > 视口 1174px，
        13 个页签里**只有这一页**长出滚动条；又因为全站加了 `scrollbar-gutter: stable`，
        别的页不跳、只有它跳，反而更显眼。

        契约：#tab-ailab 是 flex 列 + 定高（只扣 main 的上下 padding 与播放坞让位），
        .ailab-grid 用 flex:1 吃掉剩余高度，具体数值交给浏览器。加减任何一段 chrome
        都不需要再回来改这个算式。
        """
        m = re.search(r"#tab-ailab\s*\{([^}]*)\}", self.head)
        self.assertIsNotNone(m, "找不到 #tab-ailab 规则")
        r = m.group(1)
        self.assertIn("display: flex", r,
                      "#tab-ailab 不是 flex 列，.ailab-grid 无法吃掉剩余高度")
        self.assertIn("flex-direction: column", r, "#tab-ailab 的 flex 方向不是列")
        self.assertIn("height: calc(100vh", r, "#tab-ailab 没有按视口定高")
        # 只拦"真的拿来当 100vh 扣减"的魔法数 —— 那是这个 bug 的形状。
        # 不能裸查 "190px"：注释里会引用旧值说明来由，别处也可能有无关的 max-width:190px。
        self.assertNotRegex(
            re.sub(r"/\*[\s\S]*?\*/", "", self.head),
            r"100vh\s*-\s*\d+px",
            "还有 `calc(100vh - <字面数>px)` 残留 —— 裸数字迟早算错，"
            "必须改用 --dock-h / --pagehead-h / --sp-* 令牌或 flex 列",
        )
        # 显示态必须是 flex —— switchTab 用内联 display 切换，写 none 这页就永远出不来
        self.assertNotIn("display: none", r,
                         "#tab-ailab 写成 display:none 会让该页永远显示不出来"
                         "（switchTab 显示时把内联 display 置空，会回落到这条规则）")
        g = re.search(r"#tab-ailab \.ailab-grid\s*\{([^}]*)\}", self.head)
        self.assertIsNotNone(g, "找不到 #tab-ailab .ailab-grid 规则")
        self.assertIn("flex: 1", g.group(1), ".ailab-grid 没有吃掉剩余高度")

    def test_footer_hint_truncation_leaves_a_trace(self):
        """页脚说明被截断时必须留下痕迹（省略号 + title），不许无声丢字。

        实测（1920x1174，① Style 装配栏）：`.pane-foot .hint-xs` 原来写
        `-webkit-line-clamp:1; overflow:hidden` —— 既没有省略号、也没有 title。
        那条 38px 的文案被压进 19px 的盒子，后半句（"那两页也各自留了曲风输入框…"）
        **整段消失**，用户只看到半句话。这正是设计系统禁止的"元素莫名消失"。

        契约：单行 + text-overflow:ellipsis（可见的截断痕迹），
        并由 syncFootHintTitles() 在真的被截断时把完整文案挂到 title 上。
        """
        m = re.search(r"\.pane-foot \.hint-xs\s*\{([^}]*)\}", self.head)
        self.assertIsNotNone(m, "找不到 .pane-foot .hint-xs 规则")
        r = m.group(1)
        self.assertIn("text-overflow: ellipsis", r, "截断没有省略号，看不出被截了")
        self.assertIn("white-space: nowrap", r, "不是单行截断，行高会随文本换行膨胀")
        self.assertIn("overflow: hidden", r, "没有裁剪掉溢出部分")
        self.assertNotIn("-webkit-line-clamp", r,
                         "还在用 line-clamp —— 它不带省略号也不带 title，是无声截断")
        # 必须有自动挂 title 的兜底，并且真的做了"赋值 title"这件事 ——
        # 只查函数名存在是不够的：把函数体掏空、调用点留着，名字照样在，功能却没了。
        fm = re.search(r"function syncFootHintTitles\(\)\s*\{([\s\S]*?)\n\}", self.src)
        self.assertIsNotNone(fm, "没有 syncFootHintTitles()，被截断的文案无法通过悬停读全")
        body = fm.group(1)
        self.assertIn(".pane-foot .hint-xs", body, "syncFootHintTitles 没在管页脚 hint")
        self.assertRegex(body, r"\.title\s*=", "syncFootHintTitles 没有真的挂 title")
        self.assertIn("scrollWidth", body, "syncFootHintTitles 没有判定是否真的被截断")
        self.assertRegex(self.src, r"syncFootHintTitles\(\)[\s\S]{0,400}addEventListener\(\"resize\"",
                         "syncFootHintTitles 没有跟随 resize 重算（栏宽变化会改变截断与否）")


if __name__ == "__main__":
    unittest.main()
