"""前端拆分守门：web/ 源文件必须能逐字节重建 static/index.html。"""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_ui", ROOT / "scripts" / "build_ui.py")
build_ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_ui)


class UiBuild(unittest.TestCase):
    def test_generated_file_matches_sources(self):
        self.assertEqual(build_ui.render(), build_ui.OUT.read_bytes(),
                         "static/index.html 与 web/ 源文件不同步；先跑 scripts/build_ui.py")

    def test_check_mode_passes(self):
        self.assertEqual(build_ui.main(["--check"]), 0)

    def test_sources_are_non_trivial(self):
        css = (ROOT / "web" / "app.css").read_text(encoding="utf-8")
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn(".preset-row button.on", css)
        self.assertIn("GEN_PRESETS", js)
        self.assertIn("switchTab", js)

    def test_template_has_markers_and_output_does_not(self):
        tpl = (ROOT / "web" / "index.html").read_bytes()
        out = build_ui.OUT.read_bytes()
        self.assertIn(build_ui.CSS_MARK, tpl)
        self.assertIn(build_ui.JS_MARK, tpl)
        self.assertNotIn(build_ui.CSS_MARK, out)
        self.assertNotIn(build_ui.JS_MARK, out)

    def test_runtime_stays_self_contained(self):
        """拆源文件不能把运行时变成外链 CSS/JS（网关没有 StaticFiles 挂载）。"""
        out = build_ui.OUT.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r'<script[^>]+src=', out))
        self.assertIsNone(re.search(r'<link[^>]+rel=["\']stylesheet', out))


if __name__ == "__main__":
    unittest.main()
