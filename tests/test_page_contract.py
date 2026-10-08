"""v2.0 页面契约测试（规划 §9.1）。

只做静态解析，不需要浏览器、不提交 GPU 计算，可与真实任务并行跑。
覆盖四件事：
  1. 侧栏 11 个 data-tab 与 11 个 id="tab-X" 面板一一对应，无孤儿；
  2. 静态 HTML 里 id 不重复（重复 id 会让 $("x") 拿到错的那个，是最难查的一类前端 bug）；
  3. 所有请求 URL 以 /api/ 开头，源码里不得出现硬编码的 /lab/api/（前缀由 LAB_BASE 猴补丁统一加）；
  4. 拆页共用件（#genForm / #abcBlock）的搬家用具在位，且每个页面进入时该刷新的都有刷新钩子。
"""
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")
DASH = os.path.join(ROOT, "dsh-plugin", "lib", "client.js")

# 规划 §3.1：三组 11 页，data-tab 即 hash 与面板名
PAGES = {
    "style": "① Style 组装",
    "sing": "② 歌词生成歌曲",
    "score": "③ 参考歌曲提取乐谱",
    "scoreSing": "④ 乐谱生成歌曲",
    "ailab": "⑧ 歌词曲风 AI 工作台",
    "train": "⑤ 音色制作",
    "voices": "⑥ 音色库",
    "convert": "⑪ 歌曲换声",
    "sep": "⑦ 人声伴奏分离",
    "history": "⑨ 任务管理",
    "settings": "⑩ 设置",
}
# v1.x 的旧页签名必须还能路由（历史链接与 dsh 侧栏可能仍带着用）
LEGACY = {"compose": "sing", "rvc": "convert", "rvcTrain": "train", "voice": "voices"}


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


class PageContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = read(INDEX)
        # 静态 HTML = <script> 之前的部分；JS 字符串里拼出来的 id 不算
        cls.html = cls.src[: cls.src.index("<script>")]

    def test_eleven_tabs_match_eleven_panels(self):
        tabs = set(re.findall(r'class="tab[^"]*" data-tab="([^"]+)"', self.html))
        panels = set(re.findall(r'<section class="panel-tab" id="tab-([^"]+)"', self.html))
        self.assertEqual(tabs, set(PAGES), "侧栏页签与规划 §3.1 的 11 页不一致")
        self.assertEqual(panels, set(PAGES), "面板与规划 §3.1 的 11 页不一致")
        self.assertEqual(tabs, panels, "有页签没有面板，或有面板没有页签")

    def test_exactly_one_default_visible_panel(self):
        visible = re.findall(r'<section class="panel-tab" id="tab-([^"]+)"(?![^>]*display:none)', self.html)
        self.assertEqual(visible, ["style"], "默认只能有一个面板可见（应为 ① Style 组装），否则首屏两页叠着显示")
        self.assertIn('class="tab active" data-tab="style"', self.html, "侧栏高亮的页签必须就是默认可见的那一页")

    def test_ids_unique_in_static_html(self):
        ids = re.findall(r'\bid="([^"]+)"', self.html)
        dup = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(dup, [], "静态 HTML 出现重复 id：" + "、".join(dup))

    def test_grouped_sidebar(self):
        groups = re.findall(r'<div class="nav-group">([^<]+)</div>', self.html)
        self.assertEqual(groups, ["创作", "声音", "系统"], "侧栏必须按三组分层（规划 §3.1）")

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
        # ②④ 共用生成表单、③④ 共用谱框：靠搬家实现，四件用具必须都在位
        for node in ('id="genForm"', 'id="genSlotSing"', 'id="genSlotScore"',
                     'id="abcBlock"', 'id="abcSlotScore"', 'id="abcSlotSing"',
                     'id="scoreSection"', 'id="flowBar"'):
            self.assertIn(node, self.html, "缺 %s：②④/③④ 的共用实现被打断" % node)
        self.assertIn("moveInto(gen", self.src, "switchTab 里没有把 #genForm 搬进目标槽位")
        self.assertIn('moveInto($("abcBlock")', self.src, "switchTab 里没有把 #abcBlock 搬进目标槽位")

    def test_side_foot_only_health_and_theme(self):
        side = self.html[self.html.index('<div class="side-foot">'): self.html.index("</aside>")]
        for moved in ("modelVerifyRow", "modeRow", "accentRow"):
            self.assertNotIn('id="%s"' % moved, side, "%s 应该已经搬进 ⑩ 设置页" % moved)
        for kept in ("health", "themeBtn"):
            self.assertIn('id="%s"' % kept, side, "侧栏底部必须常驻 %s" % kept)
        self.assertIn('id="modelVerifyRow"', self.html)
        self.assertIn('id="modeRow"', self.html)
        self.assertIn('id="accentRow"', self.html)

    def test_moved_controls_kept_single_instance(self):
        # 量化档切换搬到 ⑩；②④ 只显示当前模型摘要，不留第二份 select
        settings = self.html[self.html.index('id="tab-settings"'):]
        self.assertIn('id="modelSel"', settings, "⑩ 设置页没有模型档位下拉")
        self.assertEqual(len(re.findall(r'id="modelSel"', self.html)), 1, "modelSel 只能有一份")
        self.assertIn('id="modelNow"', self.html, "②④ 缺当前模型摘要")

    def test_page_refresh_hooks(self):
        # 只取 switchTab 函数体（到第一个顶格 } 为止），免得跨函数的宽松匹配把测试变成摆设
        start = self.src.index("function switchTab(name)")
        end = self.src.index("\n}", start)
        body = self.src[start:end]
        for name, fn in (("history", "renderHistory"), ("convert", "renderRvc"), ("train", "renderRvcTrainList"),
                         ("voices", "renderRvcModels"), ("sep", "loadSepList"), ("score", "loadScoreList"),
                         ("scoreSing", "loadScorePicker"), ("settings", "loadSysInfo")):
            self.assertRegex(body, r'(?s)name === "%s".*?%s\(\)' % (name, fn),
                             "%s 页进入时应刷新 %s()" % (name, fn))
        self.assertLess(len(body), 4000, "switchTab 函数体异常长，检查是否漏了收尾大括号")

    def test_dsh_sidebar_tabs_match(self):
        if not os.path.exists(DASH):
            self.skipTest("dsh 插件不在仓库里")
        dash = read(DASH)
        block = dash[dash.index("var LAB_TABS = ["): dash.index("];", dash.index("var LAB_TABS = ["))]
        ids = re.findall(r'\{ id: "([^"]+)"', block)
        # ailab 只在独立访问时存在：dsh 壳本身就是 AI 工作台，侧栏再给入口会自嵌套
        self.assertEqual(ids, [p for p in PAGES if p != "ailab"], "dsh 侧栏页签必须与 11 页去掉 ailab 后的 10 页一致")
        self.assertNotIn('"compose"', dash, "dsh 侧栏仍在用旧页签名 compose")

    def test_new_pages_have_landmark_ids(self):
        for node in ('id="styleCopy"', 'id="toSing"', 'id="toScoreSing"', 'id="stylePreview"',
                     'id="sepFile"', 'id="sepGo"', 'id="sepResults"', 'id="sysInfo"',
                     'id="abcDrop"', 'id="abcExportMid"', 'id="scorePick"', 'id="batchFold"'):
            self.assertIn(node, self.html, "缺 %s：新页面的骨架控件不完整" % node)

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
