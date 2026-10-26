# -*- coding: utf-8 -*-
"""`scripts/roundtrip_eval.py` 的守门用例（全部离线，不需要引擎）。

往返实验本身跑一轮要几十分钟，但它**会烂**：接口变了、谱里多了空小节、
色度旋转的符号写反了——这些都不会让脚本报错，只会让结论悄悄指反方向
（本次就抓到一次：`音频Δ` 把 +5 报成 −5）。所以把不依赖模型的那几段拉出来钉住：

* 片段切取：跳过前奏空小节、只取 Vocal 声部、小节数受控；
* 色度旋转：已知真值 ±5 / ±12 的方向必须对，等价类（±7 与 ∓5）如实重合；
* 缺件行为：没引擎就 skip 返回 0，没结果时报告返回 1，都不抛异常。
"""
from __future__ import annotations

import importlib.util
import os
import random
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "src"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_spec = importlib.util.spec_from_file_location(
    "roundtrip_eval", os.path.join(ROOT, "scripts", "roundtrip_eval.py"))
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)


SCORE = """X:1
T:two voices
M:4/4
L:1/8
Q:1/4=90
K:G
% intro
V:Vocal
z8 | z8 | z8 |
G2 B2 d2 g2 | a2 g2 d2 B2 | c2 B2 A2 G2 | G8 |
V:Piano
C2 E2 G2 c2 | d2 c2 G2 E2 | C8 |
"""


class ChromaRotation(unittest.TestCase):
    """符号方向必须直白：正数 = 候选更高。"""

    @staticmethod
    def _up(vec, n):
        return [vec[(i - n) % 12] for i in range(12)]

    def test_direction_is_positive_when_higher(self):
        base = [10, 0, 0, 5, 0, 0, 20, 0, 0, 3, 0, 0]
        for n in (5, -5, 3, -3, 0):
            shift, corr = rt.chroma_rotation(base, self._up(base, n))
            self.assertEqual(shift, n, f"真值 {n:+d}")
            self.assertEqual(corr, 1.0)

    def test_tritone_and_octave_live_in_the_same_blind_spot(self):
        """±7 与 ∓5 同余 12，色度只能给同一个等价类代表；±12 直接看不见。"""
        base = [10, 0, 0, 5, 0, 0, 20, 0, 0, 3, 0, 0]
        self.assertEqual(rt.chroma_rotation(base, self._up(base, 7))[0], -5)
        self.assertEqual(rt.chroma_rotation(base, self._up(base, -7))[0], 5)
        self.assertEqual(rt.chroma_rotation(base, self._up(base, 12))[0], 0)

    def test_unrelated_chroma_never_matches_fully(self):
        """随机两段色度在"12 种旋转里挑最好"之后仍到不了高相关。

        之所以只敢断言"不到 0.7"而不是"接近 0"：任何移调后形状相同的音级分布
        都会被旋转对上（三和弦就是这个形状），所以色度相关只能当旁证，
        判"像不像同一首歌"要用 `abc_compare.motif_contain`。
        """
        rng = random.Random(7)
        a = [rng.random() for _ in range(12)]
        b = [rng.random() for _ in range(12)]
        _, corr = rt.chroma_rotation(a, b)
        self.assertLess(corr, 0.7)


class Excerpt(unittest.TestCase):
    def test_skips_leading_rests_and_other_voices(self):
        ex = rt.cut_excerpt(SCORE, bars=2)
        self.assertIn("G2 B2 d2 g2", ex)
        self.assertNotIn("C2 E2 G2 c2", ex, "Piano 声部不能混进人声片段")
        self.assertNotIn("z8", ex, "前奏空小节不该当实验材料")
        self.assertEqual([l for l in ex.splitlines() if l.startswith("K:")], ["K:G"])

    def test_bar_count_is_respected(self):
        one = rt.cut_excerpt(SCORE, bars=1)
        two = rt.cut_excerpt(SCORE, bars=2)
        self.assertLess(one.count("|"), two.count("|"))
        self.assertGreaterEqual(two.count("|"), 2)

    def test_headers_come_from_source(self):
        ex = rt.cut_excerpt(SCORE, bars=2)
        self.assertIn("M:4/4", ex)
        self.assertIn("L:1/8", ex)
        self.assertIn("Q:1/4=90", ex)

    def test_score_without_notes_is_rejected(self):
        with self.assertRaises(ValueError):
            rt.cut_excerpt("X:1\nM:4/4\nL:1/8\nK:C\nz8 | z8 |\n", bars=2)

    def test_lyrics_from_w_lines(self):
        self.assertEqual(rt.lyrics_from("X:1\nK:C\nC D |\nw: 你 好\nw: 世 界\n"),
                         "[Verse]\n你 好\n世 界\n")
        self.assertEqual(rt.lyrics_from("X:1\nK:C\nC D |\n"), "")


class OfflineBehaviour(unittest.TestCase):
    def test_list_runs_without_engine(self):
        self.assertEqual(rt.run(["--list"]), 0)

    def test_report_without_results_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(rt.run(["--report", "--workdir", tmp]), 1)

    def test_probe_never_raises(self):
        engine, gateway, why = rt.probe()
        self.assertIsInstance(engine, bool)
        self.assertIsInstance(gateway, bool)
        self.assertIsInstance(why, str)

    def test_unknown_group_is_rejected(self):
        """点错组名要当场说清楚，别闷头跑一轮再报错。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = rt.run(["nosuchgroup", "--workdir", tmp])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
