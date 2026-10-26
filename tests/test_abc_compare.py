# -*- coding: utf-8 -*-
"""`abc_compare` 的回归用例。

这个模块量的是"像不像 / 高没高"，既不是算术也不是模型输出，最容易出的一类错是
**量具本身有偏差而没人发现**（本次就是先把它当尺子用，才发现旧口径里 `shift` 把
"调性"和"音区"混成一个数）。所以这里钉两类用例：

1. **已知真值必须精确**：`abc_edit` 移了多少，`report` 就得报多少、残差为 0。
   真源是加工函数自己的输出，不是手抄的期望值。
2. **盲区必须如实存在**：色度分不清 −5 与 +7、绝对偏移会带上八度——
   这些"量具的局限"写成断言，谁哪天为了方便把它们抹平，测试立刻红。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import abc_compare as ac  # noqa: E402
import abc_edit  # noqa: E402


#: 带调号 + 和弦 + 变音 + 休止的谱，尽量像真的：K:A 有 3 个升号，`g` 是 G#、`g♮` 要写 =g
REALISH = """X:1
T:realish
M:4/4
L:1/16
Q:1/4=82
K:A
"A"E2A2c2e2 "D"d2c2A2E2|"F#m7"F2A2c2f2 e2c2A2F2|"Bm7"B2d2f2b2 a2f2d2B2|
"E7"e2g2b2e'2 =g2b2d'2B2|"A"A2c2e2a2 z2A2c2e2|
"""

#: 另一首歌（不同调、不同旋律走向）——用来测"区分度"，不是"相似度"
OTHER = """X:1
T:other
M:4/4
L:1/8
K:Bb
"Bb"B2c2d2f2 "Gm"g2f2d2B2|"Eb"e2f2g2b2 "F"f2e2c2A2|
"""


class NotesParsing(unittest.TestCase):
    def test_key_signature_applied(self):
        """K:E 的 `f` 是 F#（6），不是 F（5）——漏叠调号会整条旋律差半音。"""
        src = "X:1\nM:4/4\nL:1/4\nK:E\nE F G A |\n"
        self.assertEqual([p for p, _ in ac.notes(src)], [4, 6, 8, 9])

    def test_measure_accidental_beats_key_signature(self):
        """显式还原号优先于调号：K:E 下 `=F` 是 F♮（5），后面的 `F` 继承，`f`（高八度）不继承。"""
        src = "X:1\nM:4/4\nL:1/4\nK:E\n=F F f |\n"
        self.assertEqual([p for p, _ in ac.notes(src)], [5, 5, 18])

    def test_inheritance_is_scoped_to_same_letter_and_octave(self):
        """`^^C` 只影响本小节的大写 C（八度 0），`c`/`c'` 不受影响——这是 ABC 2.1 的粒度。"""
        src = "X:1\nM:4/4\nL:1/4\nK:C\n^^C C c c' | C, |\n"
        self.assertEqual([p for p, _ in ac.notes(src)],
                         [2, 2, 12, 24, -12])   # 换小节后清零：C, 回到本位

    def test_duration_parsed_and_used_as_weight(self):
        """时值真的参与加权：四分 + 全音符时，中位音高应落在长音上。"""
        src = "X:1\nM:4/4\nL:1/4\nK:C\nC4 A |\n"
        self.assertEqual(ac.notes(src), [(0, 4.0), (9, 1.0)])
        self.assertEqual(ac.weighted_median(ac.notes(src)), 0)

    def test_voice_field_line_is_not_music(self):
        """`V: Vocal` 的 c/a 不是音符（曾把声部名当旋律移调）。"""
        src = "X:1\nM:4/4\nL:1/4\nK:C\nV: Vocal\nC D |\n"
        self.assertEqual([p for p, _ in ac.notes(src)], [0, 2])

    def test_bar_line_resets_accidental(self):
        """`^F` 只管本小节——过小节线还带着它，就会把下一小节的 F 读高半音。

        这是本模块自己的历史 bug（只在字段行清状态、忘了小节线），
        症状是"移调量算对了、相似度却永远差一点"，很难看出来。
        """
        src = "X:1\nM:4/4\nL:1/4\nK:C\n^F F | F F |\n"
        self.assertEqual([p for p, _ in ac.notes(src)], [6, 6, 5, 5])

    def test_inline_key_change_switches_signature(self):
        """谱中间变调（`K:G` 独占一行 / 行内 `[K:G]`）后要按新调号起算。"""
        line_key = "X:1\nM:4/4\nL:1/4\nK:C\nC D |\nK:G\nF G |\n"
        inline_key = "X:1\nM:4/4\nL:1/4\nK:C\nC D |[K:G] F G |\n"
        self.assertEqual([p for p, _ in ac.notes(line_key)], [0, 2, 6, 7])
        self.assertEqual([p for p, _ in ac.notes(inline_key)], [0, 2, 6, 7])


class KnownTransposition(unittest.TestCase):
    """加工函数的输出就是真值：移 d 个半音，量出来必须是 d，残差 0。"""

    def test_every_delta_round_trips(self):
        for d in (-12, -7, -5, -3, -1, 0, 1, 2, 5, 7, 12):
            out = abc_edit.transform(REALISH, transpose=d)["abc"]
            r = ac.report(REALISH, out)
            self.assertEqual(r["shift_abs"], d, f"{d:+d}: {r}")
            self.assertEqual(r["residual"], 0.0, f"{d:+d}: {r}")
            self.assertEqual(r["corr"], 1.0, f"{d:+d}: 同一段旋律，相关应为 1")
            self.assertEqual(r["ref_n"], r["cand_n"], f"{d:+d}: 音数不该变")

    def test_chroma_is_mod_twelve(self):
        """−5 与 +7 是同一个音级关系：色度只给等价类代表。"""
        for d in (-5, 7):
            r = ac.report(REALISH, abc_edit.transform(REALISH, transpose=d)["abc"])
            self.assertEqual(r["shift_chroma"], -5 if d == -5 else -5,
                             "同余 12 的两个数字，色度必须给出同一个代表")

    def test_octave_moves_absolute_not_pitch_classes(self):
        """±12 只动八度：绝对偏移 ±12，色度平移 0（这正是"移调≠换声区"的量具证据）。"""
        up = abc_edit.transform(REALISH, transpose=12)["abc"]
        r = ac.report(REALISH, up)
        self.assertEqual((r["shift_abs"], r["shift_chroma"], r["offset"]), (12, 0, 12.0))

    def test_modal_conversion_leaves_residual(self):
        """大转小不是平移：残差必然非 0（否则说明量具在骗自己）。"""
        minor = abc_edit.transform(REALISH, mode="major_to_minor")["abc"]
        r = ac.report(REALISH, minor)
        self.assertNotEqual(r["residual"], 0.0, r)


class BlindSpots(unittest.TestCase):
    """量具的局限要如实存在——抹平了就等于给假结论。"""

    def test_absolute_offset_carries_register(self):
        """`offset`/`shift_abs` 把八度算进去：同一段旋律抬高八度后，调性没变但偏移 12。"""
        hi = abc_edit.transform(REALISH, transpose=12)["abc"]
        self.assertEqual(ac.key_of(hi), ac.key_of(REALISH))
        self.assertEqual(ac.report(REALISH, hi)["offset"], 12.0)

    def test_melody_match_reports_register(self):
        """Q2 的 `shift` 是"实际高了多少"，±12 的谱就该报 ±12。"""
        for d in (-12, -5, 5, 12):
            out = abc_edit.transform(REALISH, transpose=d)["abc"]
            m = ac.melody_match(REALISH, out)
            self.assertEqual(m["shift"], d, f"{d:+d}")
            self.assertEqual(m["mean"], 0.0, f"{d:+d}")
            self.assertEqual(m["hit0"], 1.0, f"{d:+d}")

    def test_empty_side_does_not_crash(self):
        empty = "X:1\nM:4/4\nL:1/4\nK:C\nz8 |\n"
        r = ac.report(empty, REALISH)
        self.assertEqual(r["ref_n"], 0)
        m = ac.melody_match(empty, REALISH)
        self.assertEqual(m["n"], 0)
        self.assertEqual(ac.motif_contain(empty, REALISH), {"jaccard": 0.0, "contain": 0.0})


class MelodyTolerance(unittest.TestCase):
    def test_transposed_copy_is_perfect_hit(self):
        out = abc_edit.transform(REALISH, transpose=5)["abc"]
        m = ac.melody_match(REALISH, out)
        self.assertEqual((m["shift"], m["mean"], m["hit0"]), (5, 0.0, 1.0))

    def test_inserted_passing_notes_do_not_break_alignment(self):
        """转谱常多认音：按顺序硬配会一路错位，DTW 必须扛得住。"""
        out = abc_edit.transform(REALISH, transpose=2)["abc"]
        # 在第 4 个音后插两个相邻的过门音
        head, _, tail = out.partition("|")
        noisy = head + " c2d2 |" + tail
        m = ac.melody_match(REALISH, noisy)
        self.assertEqual(m["shift"], 2)
        self.assertGreaterEqual(m["hit0"], 0.9, m)

    def test_different_song_is_not_similar(self):
        """区分度：异歌的五音组包含率必须接近 0，否则"像同一首歌"没有信息量。"""
        same = ac.motif_contain(REALISH, abc_edit.transform(REALISH, transpose=7)["abc"])
        diff = ac.motif_contain(REALISH, OTHER)
        self.assertEqual(same["contain"], 1.0, "同一段旋律移调后必须完全重合")
        self.assertLess(diff["contain"], 0.35, diff)


class Summary(unittest.TestCase):
    def test_summary_reports_range_in_midi(self):
        s = ac.summary(REALISH)
        self.assertEqual(s["key"], "A")
        self.assertEqual(s["n"], len(ac.notes(REALISH)))
        self.assertLess(s["lo"], s["hi"])
        self.assertEqual(s["span"], s["hi"] - s["lo"])
        self.assertEqual(ac.to_midi(0), 60)

    def test_key_of_reads_declared_key(self):
        self.assertEqual(ac.key_of(REALISH), "A")
        self.assertEqual(ac.key_of("X:1\nK:Bb\nB c |\n"), "Bb")
        self.assertEqual(ac.key_of("X:1\nC D |\n"), "")


if __name__ == "__main__":
    unittest.main()
