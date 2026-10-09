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

        亮色主按钮是【白字压主色】，深色主按钮是【墨字 #1d1003 压提亮端】。
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


if __name__ == "__main__":
    unittest.main()
