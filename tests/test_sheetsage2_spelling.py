# -*- coding: utf-8 -*-
"""SheetSage2 升库后的和弦拼写：本机 checkpoints 是随仓库入库的上游代码，必须锁住来源与行为。

2026-10-16 从上游 m-a-p/SheetSage2（10-12 版）换回 4 个模块 + 1 个新模块。上游 10-12 那次
提交修的就是"和弦拼写模块本地加载失败"，而本机原来停在 9-16，连 chord_spelling 这个文件都没有。
这些用例不加载权重、不碰 GPU，只验模块存在、被真正接进导出路径、以及拼写判据本身。
"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CKPT = os.path.join(ROOT, "checkpoints")
PKG = os.path.join(ROOT, "checkpoints")


def _read(name):
    with io.open(os.path.join(CKPT, name), encoding="utf-8") as fh:
        return fh.read()


class TestChordSpellingWired(unittest.TestCase):
    def test_module_present(self):
        self.assertTrue(os.path.isfile(os.path.join(CKPT, "chord_spelling_sheetsage2.py")),
                        "和弦拼写模块又丢了：转谱会退回一律用升号拼，同音异名跟着调走不了")

    def test_export_path_uses_it(self):
        src = _read("exports_sheetsage2.py")
        self.assertIn("from .chord_spelling_sheetsage2 import correct_chord_rows", src,
                      "导出路径没接上和弦拼写，LAB/MIDI 里的和弦还是原始解码拼写")
        self.assertIn("correct_chord_rows(intervals[\"chord\"]", src,
                      "接了 import 但没真的按局部调校正")

    def test_tokenizer_keys_are_normalized(self):
        src = _read("tokenization_sheetsage2.py")
        self.assertIn("normalize_key_name", src,
                      "调名标签不再归一，谱面调号与和弦会各说各话")


class TestSpellingBehaviour(unittest.TestCase):
    """判据本身：同音异名要跟着调走，无调信息时不许乱改。"""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        p = os.path.join(PKG, "chord_spelling_sheetsage2.py")
        spec = importlib.util.spec_from_file_location("yue2_chord_spelling", p)
        cls.cs = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(cls.cs)
        except ImportError as exc:                       # mir_eval 缺失属环境问题，不是代码问题
            raise unittest.SkipTest("mir_eval 未安装：%s" % exc)

    def test_key_names_use_canonical_spelling(self):
        self.assertEqual(self.cs.normalize_key_name("C#:major"), "Db:major")
        self.assertEqual(self.cs.normalize_key_name("Ab:minor"), "G#:minor")
        self.assertEqual(self.cs.normalize_key_name("B:major"), "B:major")

    def test_chord_spelling_follows_the_key(self):
        # B 大调里降五级不存在，Db 三级和应写成 C#
        self.assertEqual(self.cs.correct_chord_spelling("Db:maj", "B:major"), "C#:maj")
        # 无调信息时原样保留，不许猜
        rows = [[0.0, 2.0, "Db:maj"]]
        self.assertEqual(self.cs.correct_chord_rows(rows, []), [[0.0, 2.0, "Db:maj"]])

    def test_rest_and_unknown_labels_survive(self):
        for lab in ("N", "X"):
            self.assertEqual(self.cs.correct_chord_spelling(lab, "B:major"), lab)


if __name__ == "__main__":
    unittest.main()
