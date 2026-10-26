"""v2.0 / v2.1 页面契约测试（规划 §9.1）。

只做静态解析，不需要浏览器、不提交 GPU 计算，可与真实任务并行跑。
覆盖这些事：
  1. 侧栏 14 个 data-tab 与 14 个 id="tab-X" 面板一一对应，无孤儿；
  2. 静态 HTML 里 id 不重复（重复 id 会让 $("x") 拿到错的那个，是最难查的一类前端 bug）；
  3. 所有请求 URL 以 /api/ 开头，源码里不得出现硬编码的 /lab/api/（前缀由 LAB_BASE 猴补丁统一加）；
  4. 拆页共用件（#genForm / #abcBlock / #styleBox）的搬家用具在位，且每个页面进入时该刷新的都有刷新钩子；
  5. v2.1 新增：页签带圈序号、曲风输入框三页共用、④ 乐谱加工、⑩ 输出目录、⑫ 预设色板与关于页。
"""
import io
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")
DASH = os.path.join(ROOT, "dsh-plugin", "lib", "client.js")
PAGES_MANIFEST = os.path.join(ROOT, "static", "pages.json")

# 页面清单唯一真源：static/pages.json。HTML 侧栏、本测试、dsh 侧栏、README 表
# 都由它派生；加页/改号只改这一处，再由 tests/test_pages_manifest.py 交叉核对。
with io.open(PAGES_MANIFEST, encoding="utf-8") as _fh:
    _MANIFEST = json.load(_fh)

PAGES = {p["id"]: f'{p["no"]} {p["label"]}' for p in _MANIFEST["pages"]}
LEGACY = dict(_MANIFEST["legacy"])
TAB_NUMBERS = [p["no"] for p in _MANIFEST["pages"]]


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
        found = re.findall(r'class="tno">([①-⑭])</span>', self.html)
        self.assertEqual(sorted(found), sorted(TAB_NUMBERS), "页签序号不是一份完整编号")
        # 序号后紧跟的页签名必须是该页的名字（名字包在 <span class="tlabel"> 里）
        seq = re.findall(r'class="tno">([①-⑭])</span><span class="tlabel">(.*?)</span>', self.html, re.S)
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
        self.assertEqual(groups, ["创作", "声音", "系统", "工具"], "侧栏必须按四组分层")

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
        # ②⑤ 共用生成表单、③⑤ 共用谱框：靠搬家实现，用具必须都在位。
        # ★ 曲风框不在这一列里了 —— 它以前也靠搬家实现"①②⑤ 同一份 DOM"，
        #   结果就是"在 ① 写了风格，点别的页面它还在那儿"。用户两次要求页面之间独立、
        #   不要自动带内容，所以 ① 现在有自己的 #styleAsm，②⑤ 有自己的 #style。
        for node in ('id="genForm"', 'id="genSlotSing"', 'id="genSlotScore"',
                     'id="abcBlock"', 'id="abcSlotScore"', 'id="abcSlotSing"',
                     'id="scoreSection"', 'id="flowBar"', 'id="flowBarSing"',
                     'id="styleBox"', 'id="styleBoxAsm"', 'id="styleSlotAsm"', 'id="styleSlotGen"'):
            self.assertIn(node, self.html, "缺 %s：共用实现被打断" % node)
        self.assertIn("moveInto(gen", self.src, "switchTab 里没有把 #genForm 搬进目标槽位")
        self.assertIn('moveInto($("abcBlock")', self.src, "switchTab 里没有把 #abcBlock 搬进目标槽位")
        self.assertNotIn('moveInto($("styleBox")', self.src,
                         "#styleBox 又被搬回家了：曲风框必须 ① 与 ②⑤ 各一份，不随切页流动")

    def test_style_box_exists_once(self):
        """曲风框恰好两份：① 一份 #styleAsm，②⑤ 一份 #style，谁也不流向谁。

        以前是"一份 DOM 在三个页面之间搬家"，理由是"不存在哪边才是真的"。
        实测到的却是用户理解的"页面独立"被破坏：在 ① 写了风格，切到 ② 它还在，
        用户认为这就是被自动带过去的脏内容（他明确要求过两次）。
        现在钉死成两份，并且 ① 的那份不得出现在 ②⑤ 的槽里。
        """
        self.assertEqual(len(re.findall(r'id="styleBox"', self.html)), 1, "#styleBox 应当只有 ②⑤ 那一份")
        self.assertEqual(len(re.findall(r'<textarea id="style"', self.html)), 1, "#style 只能有一个")
        self.assertEqual(len(re.findall(r'<textarea id="styleAsm"', self.html)), 1,
                         "① 应当有且只有自己的那一个 #styleAsm")
        asm = self.html[self.html.index('id="styleSlotAsm"'):][:240]
        gen = self.html[self.html.index('id="styleSlotGen"'):][:240]
        self.assertIn('id="styleAsm"', asm, "① 的槽里没有 ① 自己的曲风框")
        self.assertIn('id="styleBox"', gen, "②⑤ 的槽里没有自己的曲风框")
        self.assertNotIn('<textarea id="style"', asm, "① 的槽里不该再放 ②⑤ 那个 #style")

    def test_generation_pages_have_inline_style_input(self):
        """②⑤ 的曲风必须是可编辑输入框，不是只读来源条。

        旧版这里是一条只读的"曲风来源"预览 + 两个跳转按钮，改一个词都得跳回 ①；
        用户明确要求改成直接可写，所以这条断言防它被改回去。
        """
        self.assertNotIn('id="stylePreview"', self.html, "只读曲风来源条应已移除")
        self.assertIn('id="styleSlotGen"', self.html, "②⑤ 缺曲风输入框的落位槽")

    def test_score_page_only_copy_button(self):
        """③ 只留「复制谱面」＋两个显式接力按钮；MIDI/ABC 下载与旧的隐式「用这份谱生成歌曲」都已撤掉。

        接力按钮（abcToEdit/abcToScoreSing）与旧的 toScoreSing2 的关键区别：toScoreSing2 会
        在切页时自动把内容写进目标页；接力按钮只在用户点击、且经确认后才写入，目标为空或内容
        相同不会打扰，不同才会 confirm。旧的隐式出口仍然禁止。
        """
        for gone in ('id="abcExportMid"', 'id="abcExportAbc"', 'id="toScoreSing2"'):
            self.assertNotIn(gone, self.html, "%s 应已从 ③ 移除" % gone)
        self.assertIn('id="abcCopy"', self.html, "③ 必须保留复制按钮")
        self.assertIn('id="abcToEdit"', self.html, "③ 缺「送去④加工」显式接力按钮")
        self.assertIn('id="abcToScoreSing"', self.html, "③ 缺「去⑤按谱生成」显式接力按钮")

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
                     'id="seSave"', 'id="seFromLib"', 'id="seCopy"', 'id="seAnalysis"'):
            self.assertIn(node, self.html, "④ 乐谱加工缺控件 %s" % node)
        self.assertIn('"/api/abc/edit"', self.script, "④ 没有接上 /api/abc/edit")
        # 五个调式操作与服务端 _ABC_EDIT_MODES 必须一一对应，少一个就是死选项
        modes = set(re.findall(r'<option value="([a-z_]+)">', self.html))
        for m in ("keep", "major_to_minor", "minor_to_major",
                  "major_to_relative_minor", "minor_to_relative_major"):
            self.assertIn(m, modes, "④ 缺调式选项 %s" % m)

    def test_score_edit_has_no_carry_to_other_pages(self):
        """④ 不许再读 ③ 的谱框、也不许把结果自动写进 ⑤ 的谱框：出路是存库/复制/下载，
        外加一个「显式接力」按钮（seToScoreSing）——它只在用户点击、且经确认后才写入 ⑤，
        与旧的隐式跨页搬运（seFromScore/seToGen/LabStore.）性质不同，仍被禁止。
        """
        for gone in ('id="seFromScore"', 'id="seToGen"', "LabStore."):
            self.assertNotIn(gone, self.html, "④ 还留着跨页搬运的控件/写入：%s" % gone)
        self.assertIn("存入乐谱库", self.html, "④ 的结果应当存库，由 ⑤ 自己从库里挑")
        self.assertIn('id="seToScoreSing"', self.html, "④ 缺「用于⑤生成」显式接力按钮")

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

    def test_rvc_auto_reco_is_silent(self):
        """切页/刷新时自动套用推荐参数必须静默，只有用户主动换音色才提示「已填入」。

        renderRvc() 会在进入 ⑧ 换声页时被 switchTab 调用；如果这里也弹「已按推荐参数
        填入旋钮」，用户明明没有点任何按钮、只是切了一页，就会看到一条像是"操作成功"的
        反馈——反馈与动作对不上，比不反馈更糟（历史 issue #6）。
        """
        # renderRvc 内部的自动调用必须显式传 announce=false（静默）
        render_block_start = self.script.index("function renderRvc(")
        render_block = self.script[render_block_start:render_block_start + 3000]
        self.assertRegex(render_block, r'loadRvcReco\(true,\s*false\)',
                         "renderRvc() 里自动套用推荐参数没有静默：切页会误弹「已填入」toast")
        # 只有用户主动 onchange 换音色时才允许 announce=true
        self.assertRegex(self.script, r'\$\("rvcModel"\)\.onchange[^\n]*loadRvcReco\(true,\s*true\)',
                         "用户主动换音色时应当给出「已填入推荐参数」的反馈")
        # 一键套用按钮不该被这次静默改动误伤：它本来就是用户主动点击，理应有反馈
        self.assertIn('$("rvcApplyReco").onclick = () => applyRvcReco(false);', self.script,
                      "「一键套用推荐参数」按钮的反馈被误改成静默了")

    def test_side_foot_only_health_and_theme(self):
        side = self.html[self.html.index('<div class="side-foot">'): self.html.index("</aside>")]
        for moved in ("modelVerifyRow", "modeRow"):
            self.assertNotIn('id="%s"' % moved, side, "%s 应该已经搬进 ⑫ 设置页" % moved)
        self.assertIn('id="health"', side, "侧栏底部必须常驻 health")
        self.assertNotIn('id="themeBtn"', side, "明暗模式已搬到 ⑫ 设置页")
        for node in ('id="modelVerifyRow"', 'id="modeRow"', 'id="accentSwatches"', 'id="themeBtn"'):
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
        # ailab 只在独立访问时存在：dsh 壳本身就是 AI 工作台，侧栏再给入口会自嵌套。
        # 哪些页进 dsh 由 pages.json 的 dsh 字段决定，不再在测试里写死。
        want = [p["id"] for p in _MANIFEST["pages"] if p["dsh"]]
        self.assertEqual(ids, want,
                         "dsh 侧栏页签必须与 pages.json 里 dsh=true 的页一致")
        self.assertNotIn('"compose"', dash, "dsh 侧栏仍在用旧页签名 compose")

    def test_generation_presets_are_wired(self):
        """三档预设只改 cot/steps，不自动提交；手改参数后高亮要跟着变。"""
        for token in ('data-gen-preset="draft"', 'data-gen-preset="standard"',
                      'data-gen-preset="final"', 'GEN_PRESETS', 'applyGenPreset',
                      'syncGenPresetFromFields'):
            self.assertIn(token, self.src)
        self.assertRegex(self.script, r'draft:\s*\{[^}]*cot:\s*"off"[^}]*steps:\s*8')
        self.assertRegex(self.script, r'standard:\s*\{[^}]*steps:\s*16')
        self.assertRegex(self.script, r'final:\s*\{[^}]*steps:\s*32')
        self.assertIn(".preset-row button.on", self.html)

    def test_fresh_defaults_are_draft_preset(self):
        """新用户第一首歌的默认档位必须是「草稿档」（cot=off / steps=8），不是成曲档。

        成曲档（cot=full / steps=32）在 6GB 显卡上要跑 60-90 分钟，第一次玩的人不该被
        默认值直接推进这个坑；已经调过参数并保存进 localStorage 的老用户不受影响，
        因为 applyConfig() 会用保存值覆盖这里的静态默认值，这条测试只锁静态 HTML 那一层。
        """
        cot_block = self.html[self.html.index('id="cot"'):]
        cot_block = cot_block[:cot_block.index("</select>")]
        self.assertRegex(cot_block, r'<option value="off" selected>',
                         "cot 的默认选中项不是 off（草稿档），新用户会被默认推进 full/成曲档")
        self.assertNotRegex(cot_block, r'<option value="full" selected>',
                            "cot 不该默认选中 full")
        self.assertRegex(self.html, r'id="steps"[^>]*value="8"',
                         "steps 的默认值不是 8（草稿档），新用户会被默认推进 32/成曲档")

    def test_saved_form_restore_syncs_preset_highlight(self):
        """老用户恢复存档表单后，档位高亮必须跟着实际 cot/steps 走。

        applyConfig 是直写 value、不派发事件的，而高亮靠 cot/steps 的 change/input
        事件同步——不补 syncGenPresetFromFields()，恢复出 full/32 的表单高亮却停在
        「草稿」上（2026-10-26 有窗口浏览器截图实测抓到的）。
        """
        i = self.script.index('const KEY = "yue2_compose_form"')
        block = self.script[i:i + 700]
        self.assertIn("applyConfig(c)", block)
        self.assertIn("syncGenPresetFromFields()", block,
                      "恢复存档后没同步档位高亮：值与高亮会各说各话")

    def test_rvc_queue_copy_matches_persisted_queue(self):
        """⑨ 页的诚实说明必须与 #9 之后的真实行为一致：队列已落盘、重启会接回。

        旧文案「网关重启后尚未开跑的排队条目会丢」在 queue_state.json 落盘之后就是
        骗用户的反话——用户看到这句就不会指望重启后任务还在，而它其实还在。
        """
        self.assertNotIn("网关重启后尚未开跑的排队条目会丢", self.html,
                         "⑨ 页还在用落盘之前的旧文案")
        self.assertIn("重新校验再排回", self.html)
        self.assertIn("服务重启，任务中断", self.html,
                      "⑨ 页要如实告诉用户：重启时正在转换的任务不自动重跑")

    def test_new_pages_have_landmark_ids(self):
        for node in ('id="styleCopy"', 'id="sepFile"', 'id="sepGo"', 'id="sepResults"',
                     'id="sysInfo"', 'id="abcDrop"', 'id="abcCopy"', 'id="scorePick"',
                     'id="batchFold"', 'id="lyricFromAudio"', 'id="seDownload"', 'id="lrcGo"',
                     'id="lrcPick"', 'id="abcPick"',
                     'id="styleToSing"', 'id="styleToScoreSing"', 'id="abcToEdit"',
                     'id="abcToScoreSing"', 'id="seToScoreSing"'):
            self.assertIn(node, self.html, "缺 %s：页面骨架控件不完整" % node)

    def test_lrc_can_pick_server_song(self):
        """⑭ 除了上传，还能直接点名服务器上已有的歌（页内自己取库，不跨页代填）。"""
        self.assertIn('id="lrcPick"', self.html)
        self.assertIn("loadLrcPicks", self.script)
        self.assertIn('fd.append("rid"', self.script)

    def test_score_can_pick_server_song(self):
        """③ 除了拖文件，也能直接点名服务器上已有的歌（本页自己取库）。"""
        self.assertIn('id="abcPick"', self.html)
        self.assertIn("loadScorePicks", self.script)
        self.assertIn('form.append("rid"', self.script)

    def test_tag_balance_inside_main(self):
        main = self.html[self.html.index("<main>"): self.html.index("</main>")]
        self.assertEqual(main.count("<div"), main.count("</div>"), "main 里 <div> 与 </div> 不配平")
        self.assertEqual(main.count("<section"), main.count("</section>"), "main 里 <section> 与 </section> 不配平")

    def test_form_persistence_ids_still_exist(self):
        # 表单自动持久化按这份清单读写，拆页时若把某个控件搬丢，会静默不再保存
        ids = re.findall(r'"([A-Za-z_]+)"', self.src[self.src.index("_FORM_IDS = ["): self.src.index("];", self.src.index("_FORM_IDS = ["))])
        for i in ids:
            self.assertIn('id="%s"' % i, self.html, "持久化清单里的 %s 在页面上已找不到" % i)


class PageIndependence(unittest.TestCase):
    """v10.12 的页面独立性契约：每个页面只从"用户自己填的 + 服务器自己的库"取输入。

    判据只有一条：**没有任何页面的代码可以"隐式"写另一个页面的控件**（切页、刷新、
    进入钩子里顺手代填，用户没点任何按钮就发生）。
    v10.26 起允许一种例外：用户主动点击「→ 用于X页」按钮时，代码可以把当前页的内容
    写进目标页——但必须同时满足：①只有点击才触发，切页/刷新不会；②目标页已有不同
    内容时必须 confirm 询问，不能静默覆盖；③目标为空或内容相同则不打扰。这类按钮
    集中在 sendTextToTab()/gotoSharedScore()（见 test_score_edit_has_no_carry_to_other_pages、
    test_score_page_only_copy_button），不在本类的"隐式搬运"黑名单里。
    带注释的历史说明会提到被删掉的名字，所以断言前先剥掉注释。
    """

    @classmethod
    def setUpClass(cls):
        cls.src = read(INDEX)
        cls.html = cls.src[: cls.src.index("<script>")]
        script = cls.src[cls.src.index("<script>"):]
        # 只剥行注释：块注释正则会贪吃跨行，把中间的代码一起吞掉。
        # 被删掉的名字只出现在行注释里，块注释里写的是解释性短语。
        cls.code = re.sub(r"(?<!:)//[^\n]*", "", script)

    def test_no_cross_page_fillers_left(self):
        """这些名字一旦回到代码里，就是"替你填表"复活了。"""
        gone = ["LabStore", "handoffAudio", "pullServerAudioInto", "pickSepSource",
                "sepSourceOptions", "forwardToConvert", "seFromScore", "seToGen",
                "voiceInfoGoConvert", "rvcFromSep", "trainFromSep",
                "aiFeedTo1", "aiFeedTo2", "data-restore", "playAndRestore"]
        for name in gone:
            self.assertNotIn(name, self.code, "跨页搬运又回来了：%s" % name)
        # restore() 曾经把历史记录六个控件写进 ② 还不切页，是最隐蔽的一处
        self.assertNotIn("restore(", self.code, "⑪ 的 restore() 又回来了")

    def test_outlets_are_copy_or_server(self):
        """替代方案必须在位：要么复制走，要么本页自己从服务器取。"""
        for need in ("function copyOut(", 'data-copy-style', 'data-copy-lyrics',
                     'id="aiFeedCopyStyle"', 'id="aiFeedCopyLyrics"', 'id="voiceInfoCopy"',
                     "function serverAudioOptions(", "function paintSrcPick(",
                     'id="rvcSrcPick"', 'id="trainSrcPick"',
                     "function convertServerSong("):
            self.assertIn(need, self.src, "缺少独立性所需的部件：%s" % need)

    def test_explicit_relay_is_click_gated_and_confirm_gated(self):
        """「显式接力」按钮必须真的是显式的：只在点击时触发，且目标已有不同内容要先问。

        这是页面独立性契约（见类文档）为 issue #8 开的唯一口子，口子的边界必须钉死：
          1) sendTextToTab 内部必须先 confirm()（目标非空且内容不同）才写入；
          2) switchTab() 本身绝不能调用 sendTextToTab/gotoSharedScore——否则就成了
             "切页时代填"，与 seFromScore/seToGen 那批黑名单没有区别；
          3) 每个接力按钮都必须绑定在 onclick 上，而不是在页面初始化/渲染函数里被调用。
        """
        i = self.code.index("function sendTextToTab(")
        body = self.code[i:self.code.index("\nfunction ", i + 1)]
        self.assertIn("confirm(", body, "sendTextToTab 缺确认步骤：目标已有不同内容会被静默覆盖")
        self.assertIn("old !== text", body, "确认应当只在内容真的不同时才触发")

        switch_start = self.code.index("function switchTab(name)")
        switch_body = self.code[switch_start:self.code.index("\nfunction ", switch_start + 1)]
        for banned in ("sendTextToTab(", "gotoSharedScore("):
            self.assertNotIn(banned, switch_body,
                             "switchTab() 内部调用了 %s：这就成了隐式跨页代填" % banned)

        for btn in ("styleToSing", "styleToScoreSing", "abcToEdit", "abcToScoreSing", "seToScoreSing"):
            self.assertRegex(self.code, r'\$\("%s"\)\.onclick' % btn,
                             "%s 没有绑定在 onclick 上：不是用户点击触发就不是显式接力" % btn)

    def test_score_page_never_writes_generation_cot(self):
        """③ 转谱/回填不许写 ②⑤ 的「规划 CoT」下拉框。

        与 abcForSubmit 同一个 bug 家族：#cot 在 #genForm 里（②⑤ 共用），
        人在 ③ 时写它，用户看不见；等他切到 ②，才发现"我选的 full 自己变成 melody"。
        ⑤ 的进入钩子（switchTab 里 replay renderAbcAnalysis）本来就会按当前共用谱
        重放一次锁档，所以 ③ 这两处写入既多余又有害。
        """
        for site in ('if (curTab === "scoreSing") $("cot").value = want;',
                     'if (sug && curTab === "scoreSing") $("cot").value = sug;'):
            self.assertIn(site, self.code, "③ 又无条件写 ②⑤ 的档位了：%s" % site)
        # renderAbcAnalysis 自己也有同一道守卫，别被人删掉
        i = self.code.index("function renderAbcAnalysis")
        self.assertIn('curTab === "scoreSing"', self.code[i:i + 2600],
                      "renderAbcAnalysis 的按页守卫不见了")

    def test_random_seed_does_not_become_a_standing_choice(self):
        """服务端把 -1/随机 具体化出来的那个种子，不许写回 #seed 输入框。

        app.py:2520 故意在提交时把随机落成一个真实数，好让历史记录能复现 —— 那是
        **这一首**的种子。以前 resumeRunningJob 把它 `$("seed").value = p.seed` 回写，
        表单自动保存再存进 localStorage：用户下一首以为是随机，其实沿用上一首那个数，
        而且参数摘要会显示"固定 xxx"，界面向用户撒了两次谎。
        现在只记进 runningSeedActual，由摘要行如实说"本次实际种子 N"，输入框不动。
        """
        i = self.code.index("async function resumeRunningJob")
        body = self.code[i:self.code.index("\n}", i)]
        self.assertNotIn('$("seed").value', body,
                         "resumeRunningJob 又把服务端具体化的种子写回输入框了")
        self.assertIn("runningSeedActual = p.seed", body, "实际种子没被记下来")
        j = self.code.index("function updateParamSummary")
        self.assertIn("本次实际种子", self.code[j:j + 2400],
                      "摘要行不再展示本次实际种子：用户无从知道自己那首用了什么种子")
        self.assertIn("runningSeedActual = null", self.code[self.code.index("function setGenerating"):][:220],
                      "任务结束后没清掉 runningSeedActual，摘要会继续挂着上一首的种子")

    def test_generate_page_never_carries_a_score(self):
        """②「歌词 + Style 生成歌曲」提交时必须交空乐谱，档位不许被服务端改写。

        用户实测投诉：在 ② 只给曲风和歌词、选了 full，点生成后档位自己变成 melody，
        而且成品听起来是《七里香》的旋律。

        根因是两处叠在一起：
          · #abcBlock 是 ③⑤ 共用的同一份 DOM，_FORM_IDS 又把 abc 存进 localStorage，
            于是 ② 上看不见谱框，$("abc").value 却读得到上一次 ③ 转出来的纯旋律谱；
          · 服务端 _resolve_cot（app.py:880）看到 abc 非空且谱里没有和弦记号，
            就把 full 纠正成 melody，并且**按那份谱的旋律**去生成 —— 别的歌的调子就是这么来的。
        修法是按"这一页是谁"决定带不带谱，而不是看 DOM 里有没有残留。
        """
        self.assertIn("function abcForSubmit()", self.code,
                      "abcForSubmit() 不见了：② 会又开始无条件读 ③⑤ 那份残留的谱")
        i = self.code.index("function abcForSubmit()")
        body = self.code[i:self.code.index("\n}", i)]
        self.assertIn('"scoreSing"', body, "abcForSubmit 不再按页判定")
        # 两条提交路径都必须走它，不许再直接读 $("abc").value
        self.assertEqual(self.code.count("abc: abcForSubmit()"), 2,
                         "②/⑤ 的单发与批量提交应当各用一次 abcForSubmit()")
        for bad in ('abc: $("abc").value', 'abc: $("abc").value,'):
            self.assertNotIn(bad, self.code,
                             "提交 payload 又直接读 #abc 了：② 会把 ③⑤ 的残留谱带进请求")

    def test_per_page_task_strip_does_not_come_back(self):
        """「本页任务」条已按用户要求整体收回，只留 ⑪ 一处总任务管理。

        v10.13.0 给每页配了一条任务条，用户 2026-10-16 的判定是「都太丑了，去掉，
        只留一个总的任务管理的页面」——实测它在 1680×1000 下也要滚出屏幕才看得见。
        这条断言补在 v10.16.1：v10.16.0 的日志里写了这个防护，其实当时删组件把测试一起删了。
        """
        for gone in ("modEnter(", "modStopTimer(", 'id="modTasks"', "mod-timer"):
            self.assertNotIn(gone, self.code, "每页任务条的零件又回来了：" + gone)
        # 收回的是每页那一条，⑪ 这个总入口必须还在（面板在 HTML 里，不在脚本里）
        self.assertIn('id="tab-history"', self.html, "⑪ 总任务管理页被跟着一起删了")

    def test_server_material_submit_never_touches_a_file_object(self):
        """⑨ 走「服务器素材」时没有 File 对象，提交成功后也不许读 f.name（评审 F-1，P0）。

        旧写法 `src_name: f.name` 在 fetch 已经成功之后抛 TypeError，被 catch 报成
        「提交失败」——请求其实已经入队、GPU 已经开始烧，用户于是再点一次。
        """
        self.assertNotIn("src_name: f.name", self.code,
                         "⑨ 又在提交成功后读前端那个不存在的 File 了")
        i = self.code.index('$("rvcGo").onclick')
        body = self.code[i:i + 3600]
        self.assertIn("...(d.job || {})", body, "状态与 src_name 应一律以服务端回包为准")

    def test_server_gives_back_a_src_name_for_both_inputs(self):
        with io.open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('"服务器素材 {spec}"', src,
                      "走服务器素材时后端要自己把 src_name 填好，前端才不必猜")

    def test_per_page_task_strip_does_not_come_back(self):
        """「本页任务」条整体收回，只留 ⑪ 一处总任务管理。

        v10.16.0 删组件时把对应的三条测试一起删了，日志却写着"任务条组件不得复活"
        ——三方评审复核时在 tests/ 里 0 命中。这条断言是那句承诺的正身。
        """
        for gone in ("modEnter", "modStopTimer", "modTasks", "modTimer", "MOD_TYPES"):
            self.assertNotIn(gone, self.code, "每页任务条又回来了：%s" % gone)
        self.assertNotIn("mod-strip", self.src, "样式层又加回了任务条")

    def test_server_material_submit_never_touches_a_file_object(self):
        """⑨ 走「服务器素材」时没有 File 对象，提交成功后也不许再读 f.name（评审 F-1，P0）。

        旧写法 `src_name: f.name` 在 fetch **已经成功之后**抛 TypeError，被 catch 报成
        「提交失败」——请求其实早已入队、GPU 已经开始烧，用户于是再点一次。
        """
        self.assertNotIn("src_name: f.name", self.code,
                         "⑨ 又在提交成功后读前端那个并不存在的 File 了")
        i = self.code.index('$("rvcGo").onclick')
        self.assertIn("...(d.job || {})", self.code[i:i + 3600],
                      "换声任务的状态与文件名应一律以服务端回包为准")
        with io.open(os.path.join(ROOT, "app.py"), encoding="utf-8") as fh:
            backend = fh.read()
        self.assertIn("服务器素材 {spec}", backend,
                      "走服务器素材时后端要自己把 src_name 填好，前端才不必猜")

    def test_server_source_uses_ids_not_dragged_files(self):
        """⑨/⑦ 提交服务器素材时只传标识：文件由服务端取，浏览器不绕一圈。"""
        self.assertIn('fd.append("source_id", srcId)', self.code, "⑨ 没有把 source_id 传给服务端")
        self.assertIn('fd.append("source_ids", srcIds.join(","))', self.code,
                      "⑦ 训练/体检没有把 source_ids 传给服务端")
        self.assertIn("v: m.id + \"|\" + p", self.code, "分离产物的标识格式不是 <rid>|<part>")

    def test_pages_refresh_their_own_pickers(self):
        st = self.code[self.code.index("function switchTab"):]
        st = st[: st.index("\n}\n")]
        self.assertIn('paintSrcPick("rvcSrcPick"', st, "进 ⑨ 不刷新自己的音源列表，会拿着过期的一批提交")
        self.assertIn('paintSrcPick("trainSrcPick"', st, "进 ⑦ 不刷新自己的素材列表，同上")

    def test_sep_products_are_play_and_download_only(self):
        """⑩ 的产物只给试听与下载：去向按钮就是"把 A 页的东西塞进 B 页"。"""
        for gone in ("→⑨ 换声", "→⑦ 训练素材", "→③ 转谱"):
            self.assertNotIn(gone, self.code, "⑩ 又长出去向按钮：%s" % gone)
        self.assertIn('mk("▶ 试听"', self.code, "⑩ 的产物至少要能试听")
        self.assertIn('mk("⬇ 下载"', self.code, "⑩ 的产物至少要能下载")

    def test_convert_from_other_pages_does_not_read_convert_dom(self):
        """⑪/② 发起换声时不许读 ⑨ 的滑杆——那等于把 ⑨ 的界面当隐式参数源。"""
        body = self.code[self.code.index("async function convertServerSong"):]
        body = body[: body.index("\n}\n")]
        for knob in ("rvcIndexRate", "rvcProtect", "rvcRms", "rvcFilter", "rvcSep", "rvcGate"):
            self.assertNotIn(knob, body, "convertServerSong 又在读 ⑨ 的 %s" % knob)
        self.assertIn("/api/rvc/models", body, "官方推荐档应当来自服务端")



class ScoreEditKeyMode(unittest.TestCase):
    """④ 乐谱加工的调式识别必须与后端一致。

    前端按 K: 行禁用不匹配的调式选项（对已是小调的谱就不该让人选"→同名大调"），
    判错的后果是"能选的被藏起来 / 不能选的点了没反应"。旧实现
    `/m(\\b|in|aj|$)/` 把 K:Gmaj 判成小调（m+aj 命中），K:Gmix 只是侥幸对了。
    这里直接把页面里的 seKeyMode 抠出来丢进 node 跑，避免"改了 JS 没人管"。
    """

    CASES = {
        # 调号 → 期望（None = 认不出，调用方不限制选项）
        "Em": "minor", "Am": "minor", "Cm": "minor", "F#m": "minor",
        "Bbm": "minor", "Amin": "minor", "Gmin": "minor", "Eminor": "minor",
        "Am clef=bass": "minor",
        "G": "major", "Bb": "major", "C": "major", "Eb": "major",
        "Gmaj": "major", "Gmix": "major", "Ddor": "major",
        "none": None, "": None,
    }

    def _node(self):
        for cand in (
            os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "node",
                         "versions", "22.22.2-6", "node.exe"),
            r"C:\Program Files\nodejs\node.exe",
        ):
            if os.path.exists(cand):
                return cand
        return None

    def test_se_key_mode_matches_backend_rule(self):
        with io.open(INDEX, encoding="utf-8") as fh:
            html = fh.read()
        m = re.search(r"function seKeyMode\(abc\) \{.*?\n\}", html, re.S)
        self.assertIsNotNone(m, "页面里找不到 seKeyMode（改名了就要同步改本测试）")
        node = self._node()
        if not node:
            self.skipTest("本机没有可用的 node，跳过前端行为校验")
        script = (m.group(0)
                  + "\nconst CASES = " + json.dumps(self.CASES) + ";\n"
                  + "let bad = 0;\n"
                  + "for (const k of Object.keys(CASES)) {\n"
                  + "  const got = seKeyMode('X:1\\nK:' + k + '\\nC D E');\n"
                  + "  if (got !== CASES[k]) { console.log('FAIL K:' + k + ' → ' + got"
                  + " + '（应 ' + CASES[k] + '）'); bad++; }\n"
                  + "}\n"
                  + "process.exit(bad ? 1 : 0);\n")
        import subprocess
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".js")
        os.close(fd)
        try:
            with io.open(tmp, "w", encoding="utf-8") as fh:
                fh.write(script)
            r = subprocess.run([node, tmp], capture_output=True, text=True)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        self.assertEqual(r.returncode, 0, "调式识别与后端不一致：\n" + (r.stdout or ""))


if __name__ == "__main__":
    unittest.main()
