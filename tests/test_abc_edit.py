"""ABC 乐谱加工（⑯ 乐谱加工页 / src/abc_edit.py）契约测试。

这一页是纯符号运算：移调、平行/关系大小调、速度、去和弦。
它不跑模型、不依赖 GPU，所以完全可以用"输入 ABC 字符串 → 断言输出"来锁死行为。

为什么值得单独一个测试文件：谱是「按谱演唱」路线的硬输入，
移错一个半音，整首歌都会跑调；而且错得很安静——不会报错，只会难听。
所以每个音级规则都必须有明确的断言。
"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import abc_edit  # noqa: E402


BASE = """X:1
T:Test
M:4/4
L:1/4
Q:1/4=88
K:C
C D E F | G A B c |
"""


class Transpose(unittest.TestCase):
    def test_up_two_semitones_moves_melody_and_key(self):
        r = abc_edit.transform(BASE, transpose=2)
        self.assertIn("K: D", r["abc"], "C 大调升 2 半音应写成 D 大调")
        self.assertIn("D E", r["abc"])

    def test_down_two_semitones_writes_flats_not_sharps(self):
        """降调里必须写 Bb 而不是 A#——音高一样，但谱面写法要对。"""
        r = abc_edit.transform(BASE, transpose=-2)
        self.assertIn("K: Bb", r["abc"])
        self.assertIn("Bb,", r["abc"], "低音 Bb 应写成 Bb,")
        self.assertNotIn("A#", r["abc"], "降号调里不该出现升号写法")

    def test_octave_down_keeps_pitch_classes(self):
        """移 -12 半音：音名不变，八度标记下移。"""
        r = abc_edit.transform(BASE, transpose=-12)
        self.assertIn("K: C", r["abc"])
        # 原谱 C D E F 变成 C, D, E, F,
        self.assertIn("C,", r["abc"])

    def test_zero_transpose_is_noop_on_body(self):
        r = abc_edit.transform(BASE, transpose=0)
        self.assertEqual(r["note"], "谱面未改动")
        self.assertIn("C D E F | G A B c |", r["abc"])

    def test_change_report_lists_transpose(self):
        r = abc_edit.transform(BASE, transpose=3)
        self.assertTrue(any("移调" in c for c in r["changed"]),
                        "changed 里应说明移了几个半音，用户才知道发生了什么")


class ModalConversion(unittest.TestCase):
    """大调 → 同名小调：主音不动，只把 3/6/7 音级降半音。"""

    def test_major_to_minor_flattens_third_sixth_seventh(self):
        r = abc_edit.transform("X:1\nM:4/4\nL:1/4\nK:C\nC D E F G A B c |\n",
                               mode="major_to_minor")
        self.assertIn("K: Cm", r["abc"], "同名小调调号应为 Cm")
        line = r["abc"].split("K: Cm", 1)[1]
        # C D Eb F G Ab Bb c —— 模块统一用「字母+降号」（Eb 而非 _E），
        # 与真实五线谱写法一致；_E 那种前缀式只在明确需要时才会出现。
        self.assertIn("Eb", line, "3 音级（E）应降半音写成 Eb")
        self.assertIn("Ab", line, "6 音级（A）应降半音写成 Ab")
        self.assertIn("Bb", line, "7 音级（B）应降半音写成 Bb")
        self.assertTrue(line.strip().startswith("C"), "主音 C 不应移动")
        self.assertNotIn("E ", line.replace("Eb", ""), "三音不该还是还原 E")

    def test_minor_to_major_sharpens_third_sixth_seventh(self):
        r = abc_edit.transform("X:1\nM:4/4\nL:1/4\nK:Cm\nC D _E F G _A _B c |\n",
                               mode="minor_to_major")
        self.assertIn("K: C\n", r["abc"] + "\n")
        line = r["abc"].split("K: C", 1)[-1]
        self.assertNotIn("_E", line, "升回大调后不该还有降三音")

    def test_relative_minor_keeps_melody_moves_tonic(self):
        """关系小调：旋律一个音都不动，只把调号挪到下方小三度（C→Am）。"""
        src = "X:1\nM:4/4\nL:1/4\nK:C\nC D E F G A B c |\n"
        r = abc_edit.transform(src, mode="major_to_relative_minor")
        self.assertIn("K: Am", r["abc"])
        melody_src = src.split("K:C\n", 1)[1]
        melody_out = r["abc"].split("K: Am\n", 1)[1]
        self.assertEqual(melody_src.strip(), melody_out.strip(),
                         "关系大小调不该改动旋律音")

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            abc_edit.transform(BASE, mode="not_a_mode")


class Tempo(unittest.TestCase):
    def test_set_tempo_overwrites_q_line(self):
        r = abc_edit.transform(BASE, tempo=120)
        self.assertIn("Q: 1/4=120", r["abc"], "速度应写进 Q: 行")

    def test_tempo_scale_multiplies(self):
        r = abc_edit.transform(BASE, tempo_scale=0.5)
        self.assertIn("Q: 1/4=44", r["abc"], "88 × 0.5 = 44")

    def test_tempo_line_with_text_is_handled(self):
        """`Q: "Allegro" 1/4=120` 这种带文字的也改得动数字，且保留文字。"""
        src = 'X:1\nM:4/4\nL:1/4\nQ:"Allegro" 1/4=120\nK:C\nC D E F |\n'
        r = abc_edit.transform(src, tempo=90)
        self.assertIn("1/4=90", r["abc"])
        self.assertIn("Allegro", r["abc"], "改速度不该把文字标记吃掉")

    def test_tempo_never_goes_below_one(self):
        r = abc_edit.transform(BASE, tempo_scale=0.0001)
        m = __import__("re").search(r"Q: 1/4=(\d+)", r["abc"])
        self.assertIsNotNone(m)
        self.assertGreaterEqual(int(m.group(1)), 1, "速度不能被缩放到 0")


class ChordStripping(unittest.TestCase):
    def test_strip_chords_removes_quoted_symbols(self):
        src = 'X:1\nM:4/4\nL:1/4\nK:C\n"C" C D E F | "Am7" G A B c |\n'
        r = abc_edit.transform(src, strip_chords=True)
        self.assertNotIn('"C"', r["abc"])
        self.assertNotIn("Am7", r["abc"])
        self.assertIn("C D E F", r["abc"], "去和弦不该伤到旋律音")

    def test_strip_chords_reported_in_changed(self):
        src = 'X:1\nM:4/4\nL:1/4\nK:C\n"C" C D E F |\n'
        r = abc_edit.transform(src, strip_chords=True)
        self.assertTrue(any("和弦" in c for c in r["changed"]))

    def test_chords_follow_transpose(self):
        """和弦记号也得跟着移调，否则旋律升了 2 度、和弦还停在原地。"""
        src = 'X:1\nM:4/4\nL:1/4\nK:C\n"C" C E G |\n'
        r = abc_edit.transform(src, transpose=2)
        self.assertIn('"D"', r["abc"], "C 和弦升 2 半音应成为 D 和弦")


class Robustness(unittest.TestCase):
    def test_empty_input_does_not_crash(self):
        r = abc_edit.transform("", transpose=2)
        self.assertIn("abc", r)

    def test_unknown_tokens_pass_through_untouched(self):
        """装饰音/连音记号这类看不懂的东西必须原样透传，绝不静默删除。"""
        src = "X:1\nM:4/4\nL:1/4\nK:C\n!trill! C2 D2 |\n"
        r = abc_edit.transform(src, transpose=2)
        self.assertIn("!trill!", r["abc"])

    def test_tuplets_survive(self):
        src = "X:1\nM:4/4\nL:1/4\nK:C\n(3C D E F |\n"
        r = abc_edit.transform(src, transpose=2)
        self.assertIn("(3", r["abc"], "连音符记号不该被吃掉")

    def test_result_has_stable_shape(self):
        """返回结构固定为 {abc, changed, key, note}，前端依赖它。"""
        r = abc_edit.transform(BASE, transpose=1, tempo=100)
        for k in ("abc", "changed", "key", "note"):
            self.assertIn(k, r)
        self.assertIsInstance(r["changed"], list)
        self.assertIsInstance(r["abc"], str)

    def test_bars_and_header_order_preserved(self):
        r = abc_edit.transform(BASE, transpose=2)
        self.assertIn("|", r["abc"])
        self.assertIn("M:4/4", r["abc"])
        self.assertLess(r["abc"].index("X:"), r["abc"].index("K:"),
                        "头部顺序应保持 X 在前、K 在后")


if __name__ == "__main__":
    unittest.main()
