"""页面清单唯一真源：HTML 侧栏 / dsh 侧栏 / README 表必须与 static/pages.json 一致。

加页或改序号时只改 pages.json 与三个展示面；本用例负责抓漏改的那一处。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "static" / "pages.json").read_text(encoding="utf-8"))
PAGES = MANIFEST["pages"]
NUMBERS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭"


class ManifestSelf(unittest.TestCase):
    def test_ids_and_numbers_are_unique_and_ordered(self):
        ids = [p["id"] for p in PAGES]
        self.assertEqual(len(ids), len(set(ids)), "页面 id 重复")
        self.assertEqual([p["no"] for p in PAGES], list(NUMBERS), "序号必须从 ① 连续到 ⑭")
        self.assertTrue(set(p["group"] for p in PAGES) <= set(MANIFEST["groups"]))

    def test_every_page_declares_dsh(self):
        self.assertTrue(all(isinstance(p.get("dsh"), bool) for p in PAGES))


class HtmlSidebar(unittest.TestCase):
    def test_buttons_match_manifest_in_order(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        html = html[: html.index("<script>")]
        html = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
        found = re.findall(
            r'data-tab="([^"]+)"[^>]*>.*?class="tno">([①-⑭])</span>'
            r'<span class="tlabel">(.*?)</span>',
            html, re.S)
        want = [(p["id"], p["no"], p["label"]) for p in PAGES]
        self.assertEqual(found, want, "侧栏页签与 pages.json 不一致（顺序/编号/名字）")


class DshSidebar(unittest.TestCase):
    def test_dsh_tabs_match_manifest(self):
        dash = (ROOT / "dsh-plugin" / "lib" / "client.js").read_text(encoding="utf-8")
        block = dash[dash.index("var LAB_TABS = ["): dash.index("];", dash.index("var LAB_TABS = ["))]
        found = re.findall(r'\{ id: "([^"]+)",\s*name: "([^"]+)"', block)
        want = [(p["id"], f'{p["no"]} {p["label"]}') for p in PAGES if p["dsh"]]
        self.assertEqual(found, want, "dsh 侧栏与 pages.json 的 dsh=true 页不一致")


class ReadmeTable(unittest.TestCase):
    def test_readme_rows_match_manifest(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        start = text.index("## 🧭 工作台有哪 14 个页面")
        section = text[start: text.index("###", start)]
        rows = re.findall(
            r'^\|\s*(?:\*\*(?:创作|声音|系统|工具)\*\*)?\s*\|\s*([①-⑭])\s*([^|]+?)\s*\|',
            section, re.M)
        want = [(p["no"], p["label"]) for p in PAGES]
        self.assertEqual(rows, want, "README 页面表与 pages.json 不一致")


if __name__ == "__main__":
    unittest.main()
