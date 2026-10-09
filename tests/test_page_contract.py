"""v2.0 / v2.1 页面契约测试（规划 §9.1）。

只做静态解析，不需要浏览器、不提交 GPU 计算，可与真实任务并行跑。
覆盖这些事：
  1. 侧栏 13 个 data-tab 与 13 个 id="tab-X" 面板一一对应，无孤儿；
  2. 静态 HTML 里 id 不重复（重复 id 会让 $("x") 拿到错的那个，是最难查的一类前端 bug）；
  3. 所有请求 URL 以 /api/ 开头，源码里不得出现硬编码的 /lab/api/（前缀由 LAB_BASE 猴补丁统一加）；
  4. 拆页共用件（#genForm / #abcBlock / #styleBox）的搬家用具在位，且每个页面进入时该刷新的都有刷新钩子；
  5. v2.1 新增：页签带圈序号、曲风输入框三页共用、④ 乐谱加工、⑩ 输出目录、⑫ 预设色板与关于页。
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")
DASH = os.path.join(ROOT, "dsh-plugin", "lib", "client.js")

# v2.1 重排 ：data-tab 即 hash 与面板名。
# 序号按"创作链路"的实际顺序（③ 提取 → ④ 加工 → ⑤ 按谱生成），
# 所以 ④ 是乐谱加工、⑤ 才是乐谱生成歌曲 —— 与旧版把"乐谱生成歌曲"叫 ④ 不同。
PAGES = {
    "style": "① Style 设计",
    "sing": "② 歌词+style生成歌曲",
    "score": "③ 参考歌曲提取乐谱",
    "scoreEdit": "④ 乐谱加工",
    "scoreSing": "⑤ 歌词+style+乐谱生成歌曲",
    "ailab": "⑥ 歌词曲风 AI 工作台",
    "train": "⑦ 音色制作",
    "voices": "⑧ 音色库",
    "convert": "⑨ 歌曲换声",
    "sep": "⑩ 人声伴奏分离",
    "history": "⑪ 任务管理",
    "settings": "⑫ 设置",
    "about": "⑬ 关于",
}
# v1.x 的旧页签名必须还能路由（历史链接与 dsh 侧栏可能仍带着用）
LEGACY = {"compose": "sing", "rvc": "convert", "rvcTrain": "train", "voice": "voices"}

TAB_NUMBERS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩", "⑪", "⑫", "⑬"]


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


class PageContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = read(INDEX)
        # 静态 HTML = <script> 之前的部分；JS 字符串里拼出来的 id 不算
        cls.html = cls.src[: cls.src.index("<script>")]
        cls.script = cls.src[cls.src.index("<script>"):]

    def test_tabs_match_panels(self):
        tabs = set(re.findall(r'class="tab[^"]*" data-tab="([^"]+)"', self.html))
        panels = set(re.findall(r'<section class="panel-tab" id="tab-([^"]+)"', self.html))
        self.assertEqual(tabs, set(PAGES), "侧栏页签与 PAGES 不一致")
        self.assertEqual(panels, set(PAGES), "面板与 PAGES 不一致")
        self.assertEqual(tabs, panels, "有页签没有面板，或有面板没有页签")

    def test_sidebar_tabs_carry_numbers(self):
        """侧栏每个页签都要有带圈序号，且序号与 PAGES 的编号一致。

        加序号的目的是让页签正文里那些"去 ② 填歌词""见 ⑪ 任务管理"能一眼对上，
        所以序号必须是"页签自己的号"——这里逐个页签核一遍，防止手改漏掉一个。
        """
        found = re.findall(r'class="tno">([①-⑬])</span>', self.html)
        self.assertEqual(sorted(found), sorted(TAB_NUMBERS), "页签序号不是一份完整编号")
        # 序号后紧跟的页签名必须是该页的名字（名字包在 <span class="tlabel"> 里）
        seq = re.findall(r'class="tno">([①-⑬])</span><span class="tlabel">(.*?)</span>', self.html, re.S)
        self.assertEqual(len(seq), len(PAGES), "页签序号与页签数量对不上")
        labels = {n: re.sub(r"<[^>]+>", "", t).strip() for n, t in seq}
        for tab, label in PAGES.items():
            self.assertEqual(labels.get(label[0]), label[1:].strip(),
                             "%s 页签的序号/名字对不上：实际 %r" % (tab, labels.get(label[0])))

    def test_exactly_one_default_visible_panel(self):
        visible = re.findall(r'<section class="panel-tab" id="tab-([^"]+)"(?![^>]*display:none)', self.html)
        self.assertEqual(visible, ["style"], "默认只能有一个面板可见（应为 ① Style 设计），否则首屏两页叠着显示")
        self.assertIn('class="tab active" data-tab="style"', self.html, "侧栏高亮的页签必须就是默认可见的那一页")

    def test_ids_unique_in_static_html(self):
        ids = re.findall(r'\bid="([^"]+)"', self.html)
        dup = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(dup, [], "静态 HTML 出现重复 id：" + "、".join(dup))

    def test_grouped_sidebar(self):
        groups = re.findall(r'<div class="nav-group">([^<]+)</div>', self.html)
        self.assertEqual(groups, ["创作", "声音", "系统"], "侧栏必须按三组分层")

    def test_legacy_tab_names_still_resolve(self):
        self.assertIn("LEGACY_TAB", self.src)
        for old, new in LEGACY.items():
            self.assertRegex(self.src, r'%s:\s*"%s"' % (old, new), "旧页签名 %s 没有映射到 %s" % (old, new))
            self.assertNotIn('data-tab="%s"' % old, self.html, "旧页签名 %s 不应再作为面板存在" % old)

    def test_no_hardcoded_lab_prefix(self):
        self.assertNotIn("/lab/api/", self.src, "源码不得硬编码 /lab/api/ 前缀（LAB_BASE 会统一补）")
        urls = re.findall(r'(?:fetch|fetchT|jfetch)\(\s*"([^"]+)"', self.src)
        bad = [u for u in urls if not u.startswith("/api/") and not u.startswith("/lab/")]
        self.assertEqual(bad, [], "这些请求 URL 既不是 /api/ 也不是 /lab/ 首页：" + "、".join(bad))

    def test_shared_dom_pieces_present(self):
        # ②⑤ 共用生成表单、③⑤ 共用谱框、①②⑤ 共用曲风框：靠搬家实现，用具必须都在位
        for node in ('id="genForm"', 'id="genSlotSing"', 'id="genSlotScore"',
                     'id="abcBlock"', 'id="abcSlotScore"', 'id="abcSlotSing"',
                     'id="scoreSection"', 'id="flowBar"', 'id="flowBarSing"',
                     'id="styleBox"', 'id="styleSlotAsm"', 'id="styleSlotGen"'):
            self.assertIn(node, self.html, "缺 %s：共用实现被打断" % node)
        self.assertIn("moveInto(gen", self.src, "switchTab 里没有把 #genForm 搬进目标槽位")
        self.assertIn('moveInto($("abcBlock")', self.src, "switchTab 里没有把 #abcBlock 搬进目标槽位")
        self.assertIn('moveInto($("styleBox")', self.src, "switchTab 里没有把 #styleBox 搬进目标槽位")

    def test_style_box_exists_once(self):
        """曲风框只能有一份：① 装配出来的和 ②⑤ 手写的必须是同一个值。

        如果哪天有人图省事在生成页再抄一个 textarea id="style"，页面上就有两个"真值"，
        用户在 A 页改的词不会出现在 B 页 —— 这类分叉极难发现，所以这里钉死。
        """
        self.assertEqual(len(re.findall(r'id="styleBox"', self.html)), 1, "曲风框只能有一份")
        self.assertEqual(len(re.findall(r'<textarea id="style"', self.html)), 1, "#style 只能有一个")

    def test_generation_pages_have_inline_style_input(self):
        """②⑤ 的曲风必须是可编辑输入框，不是只读来源条。

        旧版这里是一条只读的"曲风来源"预览 + 两个跳转按钮，改一个词都得跳回 ①；
        用户明确要求改成直接可写，所以这条断言防它被改回去。
        """
        self.assertNotIn('id="stylePreview"', self.html, "只读曲风来源条应已移除")
        self.assertIn('id="styleSlotGen"', self.html, "②⑤ 缺曲风输入框的落位槽")

    def test_score_page_only_copy_button(self):
        """③ 只留「复制谱面」：MIDI / ABC 下载与「用这份谱生成歌曲」都已撤掉。"""
        for gone in ('id="abcExportMid"', 'id="abcExportAbc"', 'id="toScoreSing2"'):
            self.assertNotIn(gone, self.html, "%s 应已从 ③ 移除" % gone)
        self.assertIn('id="abcCopy"', self.html, "③ 必须保留复制按钮")

    def test_params_sections_not_collapsible(self):
        """模型与参数 / 批次必须常开：它们是每次生成前都要确认的，折叠只是藏起必看的东西。"""
        for node in ("paramFold", "batchFold"):
            self.assertRegex(self.html, r'<div class="fold flat" id="%s"' % node,
                             "%s 必须常开（不能是 <details>）" % node)
            self.assertNotRegex(self.html, r'<details[^>]*id="%s"' % node, "%s 不应可折叠" % node)
        self.assertIn(".fold.flat", self.html, "缺 .fold.flat 样式：常开小节会长得像能点却点不动")

    def test_cot_follows_score(self):
        """有谱时 CoT 档位必须由谱面自动定（有和弦→full / 无和弦→melody）。"""
        self.assertIn("cot_suggested", self.script)
        self.assertIn("cotSel.value = route", self.script,
                      "分析出结论后没有把档位自动拨过去")

    def test_score_edit_page_wired_to_backend(self):
        """④ 乐谱加工页：控件齐全，且真的打到 /api/abc/edit。"""
        for node in ('id="seSrc"', 'id="seOut"', 'id="seTrans"', 'id="seTransRange"',
                     'id="seMode"', 'id="seTempo"', 'id="seStrip"', 'id="seTransChips"',
                     'id="seSave"', 'id="seToGen"', 'id="seCopy"', 'id="seAnalysis"'):
            self.assertIn(node, self.html, "④ 乐谱加工缺控件 %s" % node)
        self.assertIn('"/api/abc/edit"', self.script, "④ 没有接上 /api/abc/edit")
        # 五个调式操作与服务端 _ABC_EDIT_MODES 必须一一对应，少一个就是死选项
        modes = set(re.findall(r'<option value="([a-z_]+)">', self.html))
        for m in ("keep", "major_to_minor", "minor_to_major",
                  "major_to_relative_minor", "minor_to_relative_major"):
            self.assertIn(m, modes, "④ 缺调式选项 %s" % m)

    def test_separation_output_dir_wired(self):
        """⑩ 分离页的输出目录要真的提交给服务端，不是摆设。"""
        self.assertIn('id="sepOutDir"', self.html, "⑩ 缺输出目录输入框")
        self.assertIn('f2.append("out_dir"', self.script, "输出目录没有随提交发给服务端")

    def test_accent_presets_replace_color_picker(self):
        """外观给预设色板；取色器保留在「自定义」位置。"""
        self.assertIn('id="accentSwatches"', self.html, "⑫ 缺预设色板容器")
        self.assertIn("ACCENT_PRESETS", self.script, "缺预设色定义")
        block = self.script[self.script.index("const ACCENT_PRESETS"):self.script.index("function paintAccentSwatches")]
        n = len(re.findall(r'\["#[0-9a-fA-F]{6}", "', block))
        self.assertGreaterEqual(n, 6, "预设色少于 6 个，不够挑")
        self.assertIn('id="accentPicker"', self.html, "取色器应保留（作为自定义入口）")

    def test_about_has_author_and_repos(self):
        """关于页要有作者、邮箱与两个仓库地址。"""
        for s in ("刘昂", "liuang0307@foxmail.com",
                  "https://github.com/RevolutionLA/YuE2-Music-Workbench",
                  "https://github.com/RevolutionLA/awesome-YuE"):
            self.assertIn(s, self.html, "关于页缺 %s" % s)

    def test_voice_library_is_rvc_only(self):
        """⑧ 只留 RVC：参考音色（YuE2 翻唱）那一栏与其子页签都应移除。

        理由：YuE2 引擎没有 ICL 参考音频参数，存下来的音频复制不了音色，是个假出口；
        它唯一有用的半截（音频 → 歌词）已搬到 ② 的歌词区。
        """
        for gone in ('id="voiceTabs"', 'id="voicePaneRef"', 'id="voiceGrid"',
                     'id="vSave"', 'id="vRefText"'):
            self.assertNotIn(gone, self.html, "%s 应已从 ⑧ 移除" % gone)
        self.assertIn('id="lyricFromAudio"', self.html, "「从参考音频识别歌词」应搬到 ② 的歌词区")

    def test_side_foot_only_health_and_theme(self):
        side = self.html[self.html.index('<div class="side-foot">'): self.html.index("</aside>")]
        for moved in ("modelVerifyRow", "modeRow"):
            self.assertNotIn('id="%s"' % moved, side, "%s 应该已经搬进 ⑫ 设置页" % moved)
        for kept in ("health", "themeBtn"):
            self.assertIn('id="%s"' % kept, side, "侧栏底部必须常驻 %s" % kept)
        for node in ('id="modelVerifyRow"', 'id="modeRow"', 'id="accentSwatches"'):
            self.assertIn(node, self.html, "⑫ 设置页缺 %s" % node)

    def test_moved_controls_kept_single_instance(self):
        settings = self.html[self.html.index('id="tab-settings"'):]
        self.assertIn('id="modelSel"', settings, "⑫ 设置页没有模型档位下拉")
        self.assertEqual(len(re.findall(r'id="modelSel"', self.html)), 1, "modelSel 只能有一份")
        self.assertIn('id="modelNow"', self.html, "②⑤ 缺当前模型摘要")

    def test_page_refresh_hooks(self):
        # 只取 switchTab 函数体（到第一个顶格 } 为止），免得跨函数的宽松匹配把测试变成摆设
        start = self.src.index("function switchTab(name)")
        end = self.src.index("\n}", start)
        body = self.src[start:end]
        for name, fn in (("history", "renderHistory"), ("convert", "renderRvc"), ("train", "renderRvcTrainList"),
                         ("voices", "renderRvcModels"), ("sep", "loadSepList"), ("score", "loadScoreList"),
                         ("scoreSing", "loadScorePicker"), ("scoreEdit", "seSyncModeOptions"),
                         ("settings", "loadSysInfo")):
            self.assertRegex(body, r'(?s)name === "%s".*?%s\(\)' % (name, fn),
                             "%s 页进入时应刷新 %s()" % (name, fn))
        self.assertLess(len(body), 6000, "switchTab 函数体异常长，检查是否漏了收尾大括号")

    def test_switch_tab_scrolls_to_top(self):
        """换页必须回到顶部：以前进 ① 会落在页面最下面，第一眼看到的是"下半页"。"""
        self.assertIn("scrollPageTop", self.src, "缺 scrollPageTop：换页不会回到顶部")
        self.assertIn('history.scrollRestoration = "manual"', self.src,
                      "没关掉浏览器的滚动恢复，刷新时会把上次的位置还回来")
        start = self.src.index("function switchTab(name)")
        end = self.src.index("\n}", start)
        self.assertIn("scrollPageTop()", self.src[start:end], "switchTab 结尾没有归零滚动位置")

    def test_style_page_binary_switch(self):
        """① 右栏的「配方库 / 装配台」是二选一，靠 setAsmMode 切换两个视图容器。"""
        for node in ('id="asmPresetView"', 'id="asmSixView"', 'id="segPreset"', 'id="segSix"'):
            self.assertIn(node, self.html, "① 缺 %s" % node)
        self.assertIn("function setAsmMode", self.script, "缺 setAsmMode")
        self.assertRegex(self.html, r'<div class="asmview" id="asmPresetView">')
        self.assertRegex(self.html, r'<div class="asmview" id="asmSixView" hidden>')
        # 两个视图都必须真的含内容，不能是空壳
        self.assertIn('id="famTabs"', self.html, "配方库视图缺大类页签")
        self.assertIn('id="aMore"', self.html, "装配台视图缺六要素最后一行")

    def test_style_page_is_two_column(self):
        """① 必须是左右两栏：左产出、右来源。整页单栏会让产出条沉到页面最下面。"""
        style = self.html[self.html.index('id="tab-style"'): self.html.index('<!-- ============ ②')]
        self.assertIn('class="grid compose"', style, "① 的栅格掉了 compose，两栏不封顶会整页滚")
        self.assertNotIn("styleout", style, "旧的粘底产出条应已移除")
        self.assertNotIn("out-sticky", style, "旧的粘底样式应已移除")

    def test_dsh_sidebar_tabs_match(self):
        if not os.path.exists(DASH):
            self.skipTest("dsh 插件不在仓库里")
        dash = read(DASH)
        block = dash[dash.index("var LAB_TABS = ["): dash.index("];", dash.index("var LAB_TABS = ["))]
        ids = re.findall(r'\{ id: "([^"]+)"', block)
        # ailab 只在独立访问时存在：dsh 壳本身就是 AI 工作台，侧栏再给入口会自嵌套
        self.assertEqual(ids, [p for p in PAGES if p != "ailab"],
                         "dsh 侧栏页签必须与 13 页去掉 ailab 后的 12 页一致")
        self.assertNotIn('"compose"', dash, "dsh 侧栏仍在用旧页签名 compose")

    def test_new_pages_have_landmark_ids(self):
        for node in ('id="styleCopy"', 'id="sepFile"', 'id="sepGo"', 'id="sepResults"',
                     'id="sysInfo"', 'id="abcDrop"', 'id="abcCopy"', 'id="scorePick"',
                     'id="batchFold"', 'id="lyricFromAudio"', 'id="seDownload"'):
            self.assertIn(node, self.html, "缺 %s：页面骨架控件不完整" % node)

    def test_tag_balance_inside_main(self):
        main = self.html[self.html.index("<main>"): self.html.index("</main>")]
        self.assertEqual(main.count("<div"), main.count("</div>"), "main 里 <div> 与 </div> 不配平")
        self.assertEqual(main.count("<section"), main.count("</section>"), "main 里 <section> 与 </section> 不配平")

    def test_form_persistence_ids_still_exist(self):
        # 表单自动持久化按这份清单读写，拆页时若把某个控件搬丢，会静默不再保存
        ids = re.findall(r'"([A-Za-z_]+)"', self.src[self.src.index("_FORM_IDS = ["): self.src.index("];", self.src.index("_FORM_IDS = ["))])
        for i in ids:
            self.assertIn('id="%s"' % i, self.html, "持久化清单里的 %s 在页面上已找不到" % i)


if __name__ == "__main__":
    unittest.main()
