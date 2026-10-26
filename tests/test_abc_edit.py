"""ABC 乐谱加工（⑯ 乐谱加工页 / src/abc_edit.py）契约测试。

这一页是纯符号运算：移调、平行/关系大小调、速度、去和弦。
它不跑模型、不依赖 GPU，所以完全可以用"输入 ABC 字符串 → 断言输出"来锁死行为。

为什么值得单独一个测试文件：谱是「按谱演唱」路线的硬输入，
移错一个半音，整首歌都会跑调；而且错得很安静——不会报错，只会难听。
所以每个音级规则都必须有明确的断言。
"""
import io
import os
import re
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


_NOTE_RE = re.compile(r"^([_=^]{0,2})([A-Ga-g])([\',]*)$")
_INLINE_KEY_RE = re.compile(r"^\[K:\s*([^\]]*)\]$")


def actual_pitches(abc: str) -> list[int]:
    """解析一份 ABC 谱里正文音符的实际 MIDI 半音值（C4=0 基准）。
    遵守完整 ABC 语义：
    · 调号自动升降——K:E 的 f 实为 F#，漏叠会整条旋律偏半音；
    · 小节内变音继承——显式 ^/_/= 之后同小节同字母裸音全继承，| 清零；
    · 中途变调——正文里的 K: 行（或行内 [K:G]）之后按新调号算；
    · inline 字段行（V:/w:…）整行跳过——Vocal 里的 c/a 不是音符。
    读不懂继承的旧版辅助正是"输出全对、测试误报差半音"的原因。"""
    headers, body_lines = abc_edit.split_abc(abc)

    def _key_acc_of(text):
        tonic, is_minor, _ = abc_edit._parse_key(text)
        # 输入侧按声明的调名算（K:C# 就是 7 个升号，不能被等音改选成 Db）
        return abc_edit._key_accidentals(
            abc_edit._declared_sig(text, tonic, is_minor))

    key_raw = abc_edit._get_header(headers, "K") or "C"
    key_acc = _key_acc_of(key_raw)
    in_acc: dict[str, int] = {}
    out: list[int] = []
    for line in body_lines:
        stripped = line.strip()
        if abc_edit._HEADER.match(stripped):
            in_acc.clear()
            if stripped.startswith("K:"):
                key_acc = _key_acc_of(stripped[2:].strip())
            continue
        for tok in abc_edit._TOKEN.findall(line):
            m = _NOTE_RE.match(tok)
            mf = _INLINE_KEY_RE.match(tok)
            if mf:
                in_acc.clear()
                key_acc = _key_acc_of(mf.group(1).strip())
            elif m:
                pre, letter, ticks = m.groups()
                lu = letter.upper()
                octave = (1 if letter.islower() else 0) + ticks.count("'") - ticks.count(",")
                slot = f"{lu}{octave}"   # 继承只认同字母 + 同八度
                expl = abc_edit._accidental_value(pre)
                if expl is not None:
                    pc = abc_edit._PC[lu] + expl
                    in_acc[slot] = pc % 12
                else:
                    pc = in_acc.get(slot, abc_edit._PC[lu] + key_acc.get(lu, 0))
                out.append(octave * 12 + pc)  # C4(大写C无后缀)=0
            elif "|" in tok and not tok.startswith("%"):
                in_acc.clear()
    return out


class Transpose(unittest.TestCase):
    def test_up_two_semitones_moves_melody_and_key(self):
        r = abc_edit.transform(BASE, transpose=2)
        self.assertIn("K: D", r["abc"], "C 大调升 2 半音应写成 D 大调")
        self.assertIn("D E", r["abc"])

    def test_down_two_semitones_writes_flats_not_sharps(self):
        """降调里必须用 b、且能复用新调号的自动降号——音高一样，谱面写法要对。
        K:C 的 B 在 K:Bb 下正好是调号自然音，写 B（不带显式记号）即演奏 Bb。"""
        r = abc_edit.transform(BASE, transpose=-2)
        self.assertIn("K: Bb", r["abc"])
        self.assertNotIn("A#", r["abc"], "降号调里不该出现升号写法")
        # 用解析辅助断言实际音高，绑定语义而非谱面写法
        pc = actual_pitches(r["abc"])
        self.assertEqual(pc[:4], [-2, 0, 2, 3], "C D E F -2 半音 → Bb3 C4 D4 Eb4")

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
        # 不强行断言显式降号写法（K:Cm 可复用调号自然字母），
        # 改为断言实际音高：3/6/7 级降半音 → C D Eb F G Ab Bb c
        pc = actual_pitches(r["abc"])
        self.assertEqual(pc, [0, 2, 3, 5, 7, 8, 10, 12],
                         "3/6/7 音级各降半音，其余不变")

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


class KeySignatureAwareTranspose(unittest.TestCase):
    """带调号的谱移调：被调号自动升降的音必须先叠进实际音高再 +delta。
    这是 2026-10 修的根因 bug——旧逻辑只看字母，K:E 的 f（实为 F#）
    被当 F，移 +5 算成 A 而非 B，整条旋律偏半音，听感上"调都变了"。
    SheetSage2 输出几乎都带调号且不在音上写显式记号，所以这组测试才是
    真正抓得住回归的。"""

    def test_sharp_key_transpose_keeps_intervals(self):
        """K:E（4 升号）下 f 实为 F#；移 +5 应为 B（不是 A）。
        旧实现会算成 A#——少半音，旋律被破坏。"""
        src = "X:1\nM:4/4\nL:1/4\nK:E\nf2|]\n"
        r = abc_edit.transform(src, transpose=5)
        self.assertIn("K: A", r["abc"], "E +5 = A 大调")
        pc = actual_pitches(r["abc"])
        self.assertEqual(pc, [23], "F#5(18) + 5 半音 = B5(23)")

    def test_sharp_key_full_scale_transpose_preserves_intervals(self):
        """K:E 全音阶移调 +2 后相邻音的半音差不变（音程关系是移调的定义）。"""
        src = "X:1\nM:4/4\nL:1/4\nK:E\nc d e f g a b c' |]\n"
        orig = actual_pitches(src)
        r = abc_edit.transform(src, transpose=2)
        moved = actual_pitches(r["abc"])
        self.assertEqual(len(orig), len(moved), "音数不变")
        for a, b in zip(orig, moved):
            self.assertEqual(b - a, 2, f"每个音应升 2 半音：{orig} → {moved}")

    def test_sheetsage2_real_output_transpose_preserves_intervals(self):
        """真实 SheetSage2 输出样例（K:E，含 bb 后缀降号写法）移调后每个音严格 +5。
        这是用户实际会踩的路径：SheetSage2 转谱 → 乐谱加工页移调 → 生成歌曲。"""
        src = ("X:1\nM:2/4\nL:1/16\nQ:1/4=73\nK:E\n"
               "c'2bb3gf|g2z6|c'2g'2g'2c'2|f'2g'2z2e'd'|\n")
        orig = actual_pitches(src)
        r = abc_edit.transform(src, transpose=5)
        moved = actual_pitches(r["abc"])
        self.assertEqual(len(orig), len(moved), "音数不变")
        diffs = set(b - a for a, b in zip(orig, moved))
        self.assertEqual(diffs, {5}, f"所有音差应恒为 5：{orig} → {moved}")
        self.assertIn("K: A", r["abc"], "K:E +5 → K:A")

    def test_consecutive_same_letter_notes_are_two_notes(self):
        """`bb` 是 B B 两个音，不是 Bb 一个音。
        铁证是小节时值：M:2/4 L:1/16 每小节 8 个 1/16——
        c'2 b b3 g f = 2+1+3+1+1 = 8 凑满；若 bb 是一个 Bb3 则 2+3+1+1=7 缺拍。
        旧 _TOKEN 带 `[#bn]?` 后缀，把紧跟的 b 音吞成降号 → 丢音+节奏塌缩。"""
        src = "X:1\nM:2/4\nL:1/16\nK:E\nc'2bb3gf|\n"
        self.assertEqual(actual_pitches(src),
                         [25, 23, 23, 20, 18],
                         "c' b b g f = C#5 B4 B4 G#4 F#4（bb 是两个 B）")
        r = abc_edit.transform(src, transpose=5)
        moved = actual_pitches(r["abc"])
        self.assertEqual(len(moved), 5, "移调后必须还是 5 个音")
        self.assertEqual([b - a for a, b in zip([25, 23, 23, 20, 18], moved)],
                         [5, 5, 5, 5, 5], "每个音严格 +5")

    def test_note_followed_by_b_note_is_not_merged(self):
        """任何音后紧跟 b 音都不能被吞：`gb` = G B 两个音，不是 Gb 一个音。
        SheetSage2 真实输出第二行就有 `z2gb`——旧正则会把 gb 并成一个 Gb。"""
        src = "X:1\nM:2/4\nL:1/16\nK:E\nc'2g2z2gb|\n"
        self.assertEqual(actual_pitches(src), [25, 20, 20, 23],
                         "c' g (z) g b：C#5 G#4 G#4 B4（gb 是 G B 两个音，z 不算音）")
        r = abc_edit.transform(src, transpose=5)
        moved = actual_pitches(r["abc"])
        self.assertEqual(len(moved), 4, "4 个音一个不能少")
        self.assertEqual([b - a for a, b in zip([25, 20, 20, 23], moved)],
                         [5, 5, 5, 5])

    def test_output_uses_prefix_accidentals_only(self):
        """产出的谱面变音记号必须是前缀 ^_（ABC 标准），不能写后缀 #b——
        标准解析器（audiocpp）会把后缀 # 当噪音丢掉，音高错半音。"""
        src = "X:1\nM:2/4\nL:1/16\nK:E\nf'2g'2z2e'd'|\n"
        r = abc_edit.transform(src, transpose=1)
        headers, body = abc_edit.split_abc(r["abc"])
        for tok in abc_edit._TOKEN.findall("\n".join(body)):
            m = _NOTE_RE.match(tok)
            if not m:
                continue
            pre, letter, ticks = m.groups()
            self.assertNotIn(letter[0] + "#", tok,
                             f"音符 {tok} 不能用后缀 # 写变音（应为前缀 ^）")
            self.assertNotIn(letter[0] + "b", tok,
                             f"音符 {tok} 不能用后缀 b 写变音（应为前缀 _）")

    def test_bar_duration_unchanged_after_transpose(self):
        """移调后每小节的时值总量必须不变（节奏不能塌缩）。
        用 token 序列对照：非音符 token 原样、音符 token 一一同构。"""
        src = ("X:1\nM:2/4\nL:1/16\nK:E\n"
               "c'2bb3gf|g2z6|c'2b2b3c'-|c'2g2z2gb|\n")
        r = abc_edit.transform(src, transpose=5)
        _, orig_body = abc_edit.split_abc(src)
        _, new_body = abc_edit.split_abc(r["abc"])
        orig_toks = abc_edit._TOKEN.findall("\n".join(orig_body))
        new_toks = abc_edit._TOKEN.findall("\n".join(new_body))
        self.assertEqual(len(orig_toks), len(new_toks),
                         f"token 数必须守恒：{orig_toks} → {new_toks}")
        for a, b in zip(orig_toks, new_toks):
            is_note_a = bool(_NOTE_RE.match(a))
            is_note_b = bool(_NOTE_RE.match(b))
            self.assertEqual(is_note_a, is_note_b,
                             f"token 结构错位：{a} → {b}")
            if not is_note_a:
                self.assertEqual(a, b, f"非音符 token 必须原样：{a} → {b}")

    def test_inline_voice_lines_are_untouched(self):
        """`V: Vocal` / `V: Ins` 是声部切换行，不是音符——SheetSage2 每段都写。
        旧实现把 Vocal 里的 c/a 当音移调成 `V: Vofd'l`，声部路由全乱。
        所有 inline 字段行（V:/K:/M:/w: …）必须整行原样透传。"""
        src = ("X:1\nM:2/4\nL:1/16\nK:E\nV: Vocal\nc'2bb3gf|\n"
               "V: Ins\nZ4|\nV: Vocal\nc'2g2z2gb|\n")
        r = abc_edit.transform(src, transpose=5)
        self.assertEqual(r["abc"].count("V: Vocal"), 2)
        self.assertEqual(r["abc"].count("V: Ins"), 1)
        self.assertNotIn("Vofd", r["abc"], "声部名绝不能被移调")
        # 声部行必须独占一行（不能跟音符行粘连）
        for ln in r["abc"].splitlines():
            if ln.strip().startswith("V:"):
                self.assertTrue(__import__("re").fullmatch(
                    r"V:\s*\w+", ln.strip()), f"声部行被粘连：{ln!r}")

    def test_real_sheetsage2_score_full_conservation(self):
        """真实 SheetSage2 全谱（多声部、V: 行、Z 休止、bb 连写）移调守恒：
        每个声部段音数不变、每个音严格 +5。内嵌样例取自实际输出结构。"""
        src = ("X:1\nM:2/4\nL:1/16\nQ:1/4=73\n"
               "V: Vocal clef=treble name=\"Vocal Melody\"\n"
               "V: Ins clef=treble name=\"Ins Melody\"\nK:E\n"
               "V: Vocal\nc'2bb3gf|g2z6|c'2g'2g'2c'2|f'2g'2z2e'd'|\n"
               "V: Ins\nZ4|\n"
               "V: Vocal\nc'2b2b3c'-|c'2g2z2gb|c'c'z4c'e'|f'4z2c'b|\n"
               "V: Ins\nZ4|\n")
        r = abc_edit.transform(src, transpose=5)
        self.assertIn("K: A", r["abc"])
        # 逐段音高守恒（跳过纯休止的 Ins 段）
        headers, body_lines = abc_edit.split_abc(src)
        key_a = abc_edit._get_header(headers, "K")
        headers2, body2 = abc_edit.split_abc(r["abc"])
        key_b = abc_edit._get_header(headers2, "K")
        ka = abc_edit._key_accidentals(abc_edit._sig_of(*abc_edit._parse_key(key_a)[:2]))
        kb = abc_edit._key_accidentals(abc_edit._sig_of(*abc_edit._parse_key(key_b)[:2]))

        def segs(lines):
            out, cur, buf = [], None, []
            for line in "\n".join(lines).split("\n"):
                s = line.strip()
                m = __import__("re").match(r"^V:\s*(\w+)", s)
                if m:
                    if cur is not None:
                        out.append((cur, buf))
                    cur, buf = m.group(1), []
                elif __import__("re").match(r"^[A-Za-z]:", s) or s.startswith("%"):
                    continue
                elif s:
                    buf.append(s)
            if cur is not None:
                out.append((cur, buf))
            return out

        def pitches(lines, kacc):
            res = []
            for line in lines:
                for tok in abc_edit._TOKEN.findall(line):
                    m = _NOTE_RE.match(tok)
                    if not m:
                        continue
                    pre, letter, ticks = m.groups()
                    pc = abc_edit._actual_pc(letter.upper(), pre, kacc)
                    oct_ = (1 if letter.islower() else 0) + ticks.count("'") - ticks.count(",")
                    res.append(oct_ * 12 + pc)
            return res

        for (va, la), (vb, lb) in zip(segs(body_lines), segs(body2)):
            self.assertEqual(va, vb)
            pa, pb = pitches(la, ka), pitches(lb, kb)
            self.assertEqual(len(pa), len(pb), f"[{va}] 音数变了")
            if pa:
                self.assertEqual(set(b - a for a, b in zip(pa, pb)), {5},
                                 f"[{va}] 每个音必须严格 +5")

    def test_flat_key_transpose_keeps_intervals(self):
        """K:Bb（2 降号）下 B 实为 Bb；移 +2 应为 C（12）。"""
        src = "X:1\nM:4/4\nL:1/4\nK:Bb\nB2|]\n"
        r = abc_edit.transform(src, transpose=2)
        pc = actual_pitches(r["abc"])
        self.assertEqual(pc, [12], "Bb3(-2) + 2 半音 = C4(0)... 实为 Bb4(10)+2=C5(12)")

    def test_explicit_sharp_overrides_key_signature(self):
        """显式前缀 ^ 覆盖调号：K:E 下 ^c 是 C##（实为 D）。移 +1 → D#+1 = E... 不深究多重升降，
        只验证显式记号不被调号覆盖。"""
        src = "X:1\nM:4/4\nL:1/4\nK:E\n^c2|]\n"
        r = abc_edit.transform(src, transpose=1)
        pc = actual_pitches(r["abc"])
        # ^c: K:E 调号本就升 C（C#=1）；
        # 显式 ^ 覆盖调号后也是 C+1=1。移 +1 → 2(D)，octave=1 → 14
        self.assertEqual(pc, [14])

    def test_zero_transpose_on_keyed_score_is_noop(self):
        """SheetSage2 输出 K:E 不带显式记号，移调 0 必须完全不变。"""
        src = "X:1\nM:4/4\nL:1/4\nK:E\nc'2bb3gf|g2z6|c'2g'2g'2c'2|f'2g'2z2e'd'|\n"
        r = abc_edit.transform(src, transpose=0)
        self.assertEqual(actual_pitches(src), actual_pitches(r["abc"]),
                         "移调 0 不应改任何音高关系")
        self.assertEqual(r["note"], "谱面未改动")


class LegacyCorruptionGuard(unittest.TestCase):
    """坏谱哨兵：2026-10-10 修复前的旧版会把声部名当音符移调（Vocal→Vobg'l）、
    用后缀 # 写变音（c#'2）。坏谱存在前端 localStorage 里被反复叠加加工
    （实测 K:D 原谱被叠三次 +5 到 K:F），必须在入口拒绝，不能默默继续。"""

    def _bad(self, **kw):
        """带声部声明的完整谱（模拟 SheetSage2 真实头部结构）。"""
        return ("X:1\nM:4/4\nL:1/16\nQ:1/4=82\n"
                'V: Vocal clef=treble name="Vocal Melody" snm="Vocal"\n'
                'V: Ins clef=treble name="Ins Melody" snm="Inst."\n'
                "K:C\n% verse\n" + kw.get("body", ""))

    def test_mangled_voice_name_rejected(self):
        """声部名带八度记号（Vobg'l）→ 拒绝加工。"""
        src = self._bad(body="V: Vobg'l\n\"C\"c2d2e2f2|\nV: Ins\nZ4|\n")
        with self.assertRaises(ValueError) as cm:
            abc_edit.transform(src, transpose=5)
        self.assertIn("声部名", str(cm.exception))

    def test_undeclared_voice_prefix_collision_rejected(self):
        """声部名不在声明里且撞两字母前缀（Vofdl vs Vocal）→ 拒绝。"""
        src = self._bad(body="V: Vofdl\n\"C\"c2d2e2f2|\n")
        with self.assertRaises(ValueError):
            abc_edit.transform(src, transpose=5)

    def test_suffix_sharp_rejected(self):
        """音符后缀 #（c#'2）是旧版 _render 残留 → 拒绝加工。"""
        src = self._bad(body="V: Vocal\n\"C\"c#'2d2e2f2|\n")
        with self.assertRaises(ValueError) as cm:
            abc_edit.transform(src, transpose=5)
        self.assertIn("#", str(cm.exception))

    def test_chord_name_sharp_not_flagged(self):
        """引号里的和弦名（"F#7/A#"）是合法的，不能误报。"""
        src = self._bad(body="V: Vocal\n\"F#7/A#\"z16|c2d2e2f2|\n")
        r = abc_edit.transform(src, transpose=5)  # 不抛错即通过
        self.assertTrue(r["abc"])

    def test_comment_sharp_not_flagged(self):
        """注释行里的 # （% bridge#1）不误报。"""
        src = self._bad(body="% bridge#1\nV: Vocal\nc2d2e2f2|\n")
        r = abc_edit.transform(src, transpose=5)
        self.assertTrue(r["abc"])

    def test_clean_score_passes(self):
        """干净谱（声明过的声部名 + 前缀变音）正常加工。"""
        src = self._bad(body="V: Vocal\n\"C\"^c2_d2e2f2|\nV: Ins\nZ4|\n")
        r = abc_edit.transform(src, transpose=5)
        self.assertIn("K: F", r["abc"])

    def test_undeclared_unrelated_voice_allowed(self):
        """没撞前缀的自定义声部名（如 V: Drums 且头部未声明）不拦——
        手写谱可能这么写，哨兵只拦"确定是坏"的。"""
        src = ("X:1\nM:4/4\nL:1/16\nK:C\n"
               "% verse\nV: Drums\n\"C\"c2d2e2f2|\n")
        r = abc_edit.transform(src, transpose=5)
        self.assertIn("V: Drums", r["abc"])


class OctaveWrapTranspose(unittest.TestCase):
    """跨八度移调（B4+1=C5）不能让字母选错导致差 13 半音。

    根因：极端调号（K:C# 7 升号、K:Cb 7 降号）下，多个字母都能 mod 12 匹配
    同一目标 pc。旧版 `_spell_in_key` 按字母序优先选，会把 B4+1 选成 B#
    （K:C# 下 B 升=C6）而不是 C5——实际音高跨了一个八度（差 13 不是 1）。
    修：`_spell_in_key` 加第 1 档——优先用 `_PC[L]==tp` 的自然音字母，
    必要时加显式 `=` 还原号抵消调号自动升降。"""

    SCALE_C = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:C\nC D E F | G A B c' |\n"

    def _trans_diff(self, src, n):
        orig = actual_pitches(src)
        r = abc_edit.transform(src, transpose=n)
        moved = actual_pitches(r["abc"])
        return orig, moved

    def test_kc_up_one_b_to_c(self):
        """K:C 全音阶 +1：每个音严格 +1 半音，跨八度无错位。"""
        orig, moved = self._trans_diff(self.SCALE_C, 1)
        self.assertEqual(len(orig), len(moved))
        self.assertEqual(set(b - a for a, b in zip(orig, moved)), {1})

    def test_kc_up_two_three(self):
        for n in (2, 3, 5, 7, 12):
            orig, moved = self._trans_diff(self.SCALE_C, n)
            self.assertEqual(set(b - a for a, b in zip(orig, moved)), {n},
                             f"K:C +{n} 差集异常")

    def test_kc_down_all(self):
        for n in (-1, -2, -3, -5, -7, -12):
            orig, moved = self._trans_diff(self.SCALE_C, n)
            self.assertEqual(set(b - a for a, b in zip(orig, moved)), {n})

    def test_ke_all_transpose_values(self):
        """K:E（4 升号）下移 ±1/2/3/5/7/12 全部精确守恒。"""
        src = ("X:1\nM:2/4\nL:1/16\nQ:1/4=73\nK:E\n"
               "V: Vocal\nc'2bb3gf|g2z6|c'2g'2g'2c'2|f'2g'2z2e'd'|\n"
               "V: Ins\nz4z4|z4z4|z4z4|z4z4|\n")
        for n in (1, 2, 3, 5, 7, 12, -1, -2, -3, -5, -7, -12):
            orig, moved = self._trans_diff(src, n)
            self.assertEqual(len(orig), len(moved),
                             f"K:E +{n} 音数变 {len(orig)}→{len(moved)}")
            self.assertEqual(set(b - a for a, b in zip(orig, moved)), {n},
                             f"K:E +{n} 差集异常")

    def test_kbb_all_transpose_values(self):
        """K:Bb（2 降号方向）下移 ±1/2/3/5/7/12 全部精确守恒。"""
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:Bb\nB c d e | f g a b |\n"
        for n in (1, 2, 3, 5, 7, 12, -1, -2, -3, -5, -7, -12):
            orig, moved = self._trans_diff(src, n)
            self.assertEqual(len(orig), len(moved))
            self.assertEqual(set(b - a for a, b in zip(orig, moved)), {n},
                             f"K:Bb +{n} 差集异常")

    def test_kgm_minor_key_all_transpose_values(self):
        """K:Gm（1 升号，小调）下移调全部精确守恒——
        小调调号 sig_of 已含 +3 偏移。"""
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:Gm\nG A B c | d e f g |\n"
        for n in (1, 2, 3, 5, 7, 12, -1, -2, -3, -5, -7, -12):
            orig, moved = self._trans_diff(src, n)
            self.assertEqual(len(orig), len(moved))
            self.assertEqual(set(b - a for a, b in zip(orig, moved)), {n},
                             f"K:Gm +{n} 差集异常")


class ParallelModalConversion(unittest.TestCase):
    """平行大小调：3/6/7 级音升降半音，主音不变、八度不动。
    大调→小调只允许 0/-1 差；小调→大调只允许 0/+1 差。"""

    def test_kc_major_to_minor_step_by_step(self):
        """K:C → K:Cm：C D E F G A B c' → C D Eb F G Ab Bb c'（仅 3/6/7 降半音）。"""
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:C\nC D E F G A B c'|\n"
        r = abc_edit.transform(src, mode="major_to_minor")
        out = actual_pitches(r["abc"])
        # C D E F G A B c'(=C6=24)
        expected = [0, 2, 3, 5, 7, 8, 10, 24]
        self.assertEqual(out, expected, f"应 {expected} 实 {out}")
        self.assertIn("K: Cm", r["abc"])

    def test_ke_major_to_minor_only_0_or_minus1(self):
        """K:E → K:Em：每个音差只能是 0（不动）或 -1（3/6/7 级降半音）。"""
        src = ("X:1\nM:2/4\nL:1/16\nQ:1/4=73\nK:E\n"
               "V: Vocal\nc'2bb3gf|g2z6|c'2g'2g'2c'2|f'2g'2z2e'd'|\n"
               "V: Ins\nz4z4|z4z4|z4z4|z4z4|\n")
        orig = actual_pitches(src)
        r = abc_edit.transform(src, mode="major_to_minor")
        moved = actual_pitches(r["abc"])
        self.assertEqual(len(orig), len(moved))
        diffs = set(b - a for a, b in zip(orig, moved))
        self.assertTrue(diffs.issubset({0, -1}),
                        f"差集应 ⊆ {{0, -1}}，实际 {diffs}")
        self.assertIn("K: Em", r["abc"])
        self.assertEqual(r["abc"].count("V: Vocal"), src.count("V: Vocal"))
        self.assertEqual(r["abc"].count("V: Ins"), src.count("V: Ins"))

    def test_kgm_minor_to_major_step_by_step(self):
        """K:Gm → K:G：3级(_B→B)、6级(_e→e)、7级(F→F#) 各升半音。"""
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:Gm\nG A _B c' d' _e f' g'|\n"
        r = abc_edit.transform(src, mode="minor_to_major")
        out = actual_pitches(r["abc"])
        # G A _B c' d' _e f' g' = [7,9,10,24,26,15,29,31]
        # 升 3级: 10→11, 6级: 15→16, 7级: 29→30
        expected = [7, 9, 11, 24, 26, 16, 30, 31]
        self.assertEqual(out, expected, f"应 {expected} 实 {out}")
        self.assertIn("K: G\n", r["abc"])

    def test_kem_minor_to_major_step_by_step(self):
        """K:Em → K:E：3级(G→G#)、6级(C→C#)、7级(D→D#) 各升半音。"""
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:Em\nE ^F G A | B c' d' e'|\n"
        r = abc_edit.transform(src, mode="minor_to_major")
        out = actual_pitches(r["abc"])
        # E ^F G A B c' d' e' = [4,6,7,9,11,24,26,28]
        # 升 3级: 7→8, 6级: 24→25, 7级: 26→27
        expected = [4, 6, 8, 9, 11, 25, 27, 28]
        self.assertEqual(out, expected, f"应 {expected} 实 {out}")
        self.assertIn("K: E\n", r["abc"])


class RelativeModalConversion(unittest.TestCase):
    """关系大小调：旋律一个音不动，只改调号（K:C↔K:Am、K:Em↔K:G）。"""

    def test_kc_to_relative_minor_melody_unchanged(self):
        """K:C → K:Am：旋律不动（共用调号 sig=0），仅调号变。"""
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:C\nC D E F | G A B c' |\n"
        orig = actual_pitches(src)
        r = abc_edit.transform(src, mode="major_to_relative_minor")
        moved = actual_pitches(r["abc"])
        self.assertEqual(moved, orig, f"旋律应完全不动 {orig} vs {moved}")
        self.assertIn("K: Am", r["abc"])

    def test_kem_to_relative_major_melody_unchanged(self):
        """K:Em → K:G：旋律不动（共用 sig=1），仅调号变。"""
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:Em\nE F G A | B c d e |\n"
        orig = actual_pitches(src)
        r = abc_edit.transform(src, mode="minor_to_relative_major")
        moved = actual_pitches(r["abc"])
        self.assertEqual(moved, orig, f"旋律应完全不动 {orig} vs {moved}")
        self.assertIn("K: G\n", r["abc"])

    def test_relative_minor_keeps_seven_sharp_signature(self):
        """K:C#（7 升）→ 关系小调必须是 A#m（同为 7 升），不能"省事"写成 Bbm（5 降）。

        写成 Bbm 会让同一个 `f` 从 F# 变 F，整曲低半音——关系调的全部意义就是
        "调号一个记号都不动，只换调性中心"，调号一改就等于偷偷移了调。
        """
        src = "X:1\nM:4/4\nL:1/4\nK:C#\nC D E F | G A B c |\n"
        orig = actual_pitches(src)
        r = abc_edit.transform(src, mode="major_to_relative_minor")
        self.assertIn("K: A#m", r["abc"])
        self.assertEqual(actual_pitches(r["abc"]), orig,
                         "关系小调旋律必须一个音不动")

    def test_relative_minor_keeps_seven_flat_signature(self):
        """K:Cb（7 降）→ 关系小调 Abm（同为 7 降）。"""
        src = "X:1\nM:4/4\nL:1/4\nK:Cb\nC D E F | G A B c |\n"
        orig = actual_pitches(src)
        r = abc_edit.transform(src, mode="major_to_relative_minor")
        self.assertIn("K: Abm", r["abc"])
        self.assertEqual(actual_pitches(r["abc"]), orig)


class CombinedOperations(unittest.TestCase):
    """组合操作：移调 + 模式 + tempo + tempo_scale + strip_chords。"""

    def test_transpose_plus_parallel_modal(self):
        """K:E +5 → K:Am 同名小调：每音差 ⊆ {4, 5}（5 减去 modal 降的 1 半音）。"""
        src = ("X:1\nM:2/4\nL:1/16\nQ:1/4=73\nK:E\n"
               "V: Vocal\nc'2bb3gf|g2z6|c'2g'2g'2c'2|f'2g'2z2e'd'|\n"
               "V: Ins\nz4z4|z4z4|z4z4|z4z4|\n")
        orig = actual_pitches(src)
        r = abc_edit.transform(src, transpose=5, mode="major_to_minor")
        moved = actual_pitches(r["abc"])
        self.assertEqual(len(orig), len(moved))
        diffs = set(b - a for a, b in zip(orig, moved))
        self.assertTrue(diffs.issubset({4, 5}),
                        f"组合差集应 ⊆ {{4,5}}，实际 {diffs}")
        self.assertIn("K: Am", r["abc"])

    def test_minor_key_transpose_keeps_mode(self):
        """K:Gm +5 → K:Cm（小调移调仍是小调）：每音 +5。"""
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:Gm\nG A B c | d e f g |\n"
        orig = actual_pitches(src)
        r = abc_edit.transform(src, transpose=5, mode="keep")
        moved = actual_pitches(r["abc"])
        self.assertEqual(set(b - a for a, b in zip(orig, moved)), {5})
        self.assertIn("K: Cm", r["abc"])

    def test_tempo_set_to_120(self):
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:C\nC D E F |\n"
        r = abc_edit.transform(src, tempo=120)
        self.assertIn("120", next(l for l in r["abc"].split("\n") if l.startswith("Q:")))

    def test_tempo_scale_1_5(self):
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:C\nC D E F |\n"
        r = abc_edit.transform(src, tempo_scale=1.5)
        self.assertIn("132", next(l for l in r["abc"].split("\n") if l.startswith("Q:")))

    def test_tempo_scale_pure_number_q(self):
        """Q: 不带 = 形式也能缩放。"""
        src = "X:1\nM:4/4\nL:1/4\nQ:88\nK:C\nC D E F |\n"
        r = abc_edit.transform(src, tempo_scale=2.0)
        self.assertIn("176", next(l for l in r["abc"].split("\n") if l.startswith("Q:")))

    def test_strip_chords_removes_but_keeps_notes(self):
        src = 'X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:C\n"C"C D |"G"E G |\n'
        r = abc_edit.transform(src, strip_chords=True)
        self.assertNotIn('"C"', r["abc"])
        self.assertNotIn('"G"', r["abc"])
        self.assertIn("C", r["abc"])
        self.assertIn("D", r["abc"])


class MeasureAccidentalInheritance(unittest.TestCase):
    """小节内变音继承：显式 ^/_/=/^^/__ 一直有效到小节线，其后同字母裸音
    全部继承。旧实现逐音只看调号，继承音全被当回调号音——真实整谱移调
    -1/-2/-3/+2/+3/+7 时大量音符差半音的根因（abcjs 独立解析器实测复现）。"""

    def test_explicit_accidental_carries_within_measure(self):
        src = "X:1\nM:4/4\nL:1/4\nK:C\n^F F G A | F |]\n"
        self.assertEqual(actual_pitches(src), [6, 6, 7, 9, 5],
                         "^F 后同小节的裸 F 是 F#；小节线后的 F 回到自然音")
        r = abc_edit.transform(src, transpose=2)
        moved = actual_pitches(r["abc"])
        self.assertEqual([b - a for a, b in zip(actual_pitches(src), moved)],
                         [2, 2, 2, 2, 2], "每个音精确 +2（含继承音）")

    def test_bar_line_resets_state(self):
        src = "X:1\nM:4/4\nL:1/4\nK:C\n^F | F |]\n"
        self.assertEqual(actual_pitches(src), [6, 5])
        r = abc_edit.transform(src, transpose=2)
        self.assertEqual(actual_pitches(r["abc"]), [8, 7])

    def test_natural_sign_carries_within_measure(self):
        """K:E（F# 在调号）里 =F F 是两个 F 自然音——还原号同样继承。"""
        src = "X:1\nM:4/4\nL:1/4\nK:E\n=F F |]\n"
        self.assertEqual(actual_pitches(src), [5, 5])
        r = abc_edit.transform(src, transpose=1)
        self.assertEqual(actual_pitches(r["abc"]), [6, 6])

    def test_output_repeats_explicit_accidentals(self):
        """输出不依赖继承：需要变音的音一律重复写显式记号，
        不认继承规则的简易解析器也不会读错。"""
        src = "X:1\nM:4/4\nL:1/4\nK:C\n^F F |]\n"
        r = abc_edit.transform(src, transpose=2)
        body = [l for l in r["abc"].split("\n") if l.strip()][-1]
        # 目标 G# G#：必须两个都带 ^，不能只写 ^G G（裸 G 靠继承）
        self.assertTrue(body.strip().startswith("^G ^G"),
                        f"输出应重复显式记号：{body!r}")

    def test_output_naturalizes_inherited_letter(self):
        """小节里先有显式变音、后跟同字母自然音：输出必须写 = 还原。"""
        src = "X:1\nM:4/4\nL:1/4\nK:C\n^G =G |]\n"
        self.assertEqual(actual_pitches(src), [8, 7])
        r = abc_edit.transform(src, transpose=1)
        self.assertEqual(actual_pitches(r["abc"]), [9, 8], "^G(8)→A(9)，=G(7)→G#(8)")

    def test_double_accidentals_parse_and_carry(self):
        src = "X:1\nM:4/4\nL:1/4\nK:C\n^^F F | __B B |]\n"
        self.assertEqual(actual_pitches(src), [7, 7, 9, 9])
        r = abc_edit.transform(src, transpose=1)
        self.assertEqual([b - a for a, b in zip(actual_pitches(src),
                                                actual_pitches(r["abc"]))],
                         [1, 1, 1, 1])

    def test_inheritance_is_scoped_to_same_octave(self):
        """变音继承只认「同字母 + 同八度」。`^^C` 不影响 `c`（C5）、也不影响 `c'`。

        ABC 2.1 与 abcjs 都是这个语义；按字母记状态会把另一个八度的同名音
        也升/降半音——真实谱里出现过的 `^A` 就是这类错，听感是"某个音莫名高半音"。
        """
        src = "X:1\nM:4/4\nL:1/4\nK:C\n^^C C c c' |]\n"
        self.assertEqual(actual_pitches(src), [2, 2, 12, 24])
        r = abc_edit.transform(src, transpose=1)
        self.assertEqual(actual_pitches(r["abc"]), [3, 3, 13, 25])

    def test_real_score_like_measure_transpose(self):
        """真实 SheetSage2 谱样式的段落（K:D，含显式变音）整段守恒。"""
        src = ("X:1\nM:4/4\nL:1/8\nK:D\n"
               "V: Vocal\n^c'2 b2 a2 g2 | f2 e2 d2 ^c2 |]\n")
        orig = actual_pitches(src)
        for n in (-3, -2, -1, 1, 2, 3, 5, 7):
            moved = actual_pitches(abc_edit.transform(src, transpose=n)["abc"])
            self.assertEqual(len(orig), len(moved), f"{n:+d} 音数变了")
            self.assertEqual(set(b - a for a, b in zip(orig, moved)), {n},
                             f"{n:+d} 差集异常")


class ChordSymbolTranspose(unittest.TestCase):
    """和弦记号跟着旋律一起移：根音与斜杠低音都要动。
    旧实现只移根音——"D7/C" +5 产出 "G7/C"，旋律走了低音还在原调，必打架。"""

    def test_slash_bass_moves_with_root(self):
        src = 'X:1\nM:4/4\nL:1/4\nK:D\n"D7/C"c2 d2 e2 f2 |\n'
        r = abc_edit.transform(src, transpose=5)
        self.assertIn('"G7/F"', r["abc"])
        self.assertNotIn('"G7/C"', r["abc"])

    def test_root_and_bass_with_accidentals(self):
        src = 'X:1\nM:4/4\nL:1/4\nK:D\n"F#m7/A#"d2 e2 f2 g2 |\n'
        r = abc_edit.transform(src, transpose=5)
        self.assertIn('"Bm7/D#"', r["abc"])

    def test_flat_key_chord_spelling(self):
        """降号调里和弦拼写用 b 不用 #（Ab 而非 G#）。K:C -4 → Ab 大调。"""
        src = 'X:1\nM:4/4\nL:1/4\nK:C\n"C7"C2 E2 G2 B2 |\n'
        r = abc_edit.transform(src, transpose=-4)
        self.assertIn('"Ab7"', r["abc"])
        self.assertNotIn('"G#7"', r["abc"])

    def test_modal_conversion_strips_stale_chords(self):
        """调式转换与旧和弦必然冲突（C 大三和弦配 Cm 的 Eb 旋律）——
        移除和弦走纯旋律路线，并在 changed 里明说，不让引擎拿矛盾提示生成。"""
        src = 'X:1\nM:4/4\nL:1/4\nK:C\n"C"C E G c | "G"G B d g |\n'
        r = abc_edit.transform(src, mode="major_to_minor")
        self.assertNotIn('"C"', r["abc"])
        self.assertNotIn('"G"', r["abc"])
        self.assertTrue(any("和弦" in c for c in r["changed"]),
                        f"changed 应说明移除和弦：{r['changed']}")
        self.assertIn("K: Cm", r["abc"])
        # 旋律本身照常转换（3/6/7 级降半音）
        self.assertEqual(actual_pitches(r["abc"]), [0, 3, 7, 12, 7, 10, 14, 19])

    def test_relative_modal_keeps_chords(self):
        """关系大小调旋律不动、调号不变——和弦不冲突，保留不动。"""
        src = 'X:1\nM:4/4\nL:1/4\nK:C\n"C"C E G c |\n'
        r = abc_edit.transform(src, mode="major_to_relative_minor")
        self.assertIn('"C"', r["abc"])
        self.assertIn("K: Am", r["abc"])

    def test_zero_shift_leaves_chords_untouched(self):
        src = 'X:1\nM:4/4\nL:1/4\nK:D\n"D7/C"d2 e2 f2 g2 |\n'
        r = abc_edit.transform(src, transpose=0)
        self.assertIn('"D7/C"', r["abc"])


class MismatchedModeOpIsNoop(unittest.TestCase):
    """所选调式操作与源调式不匹配时，必须是逐字节 no-op。

    踩过的坑：对已是小调的谱选「→关系大调」，旧实现把 K:Em 改成 K:G——
    两者调号相同所以没事；但选「→关系小调」会把 K:Em 改成 K:C#m（1 升 → 4 升），
    旋律一个音没动、谱面同一个 `f` 的含义从 F 变成 F#，整曲凭空高半音。
    用户听到的就是「转完调之后非常不正常」。同理 K:E 上选「→关系大调」会从
    4 升变 1 升，整曲低半音。目标调式已达成时，正确做法是什么都不做并如实说明。
    """

    def test_relative_minor_op_on_minor_score_is_noop(self):
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:Em\nE F G A | B c d e |\n"
        r = abc_edit.transform(src, mode="major_to_relative_minor")
        self.assertEqual(r["abc"], src, "源调式已是小调，谱面必须原样不动")
        self.assertTrue(r["mode_noop"])
        self.assertIn("已经是小调", r["note"])

    def test_relative_major_op_on_major_score_is_noop(self):
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:E\nE F G A | B c d e |\n"
        r = abc_edit.transform(src, mode="minor_to_relative_major")
        self.assertEqual(r["abc"], src, "源调式已是大调，谱面必须原样不动")
        self.assertTrue(r["mode_noop"])
        self.assertIn("已经是大调", r["note"])

    def test_parallel_minor_op_on_minor_score_is_noop(self):
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:Am\nA B c d | e f g a |\n"
        r = abc_edit.transform(src, mode="major_to_minor")
        self.assertEqual(r["abc"], src)
        self.assertTrue(r["mode_noop"])

    def test_parallel_major_op_on_major_score_is_noop(self):
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:G\nG A B c | d e f g |\n"
        r = abc_edit.transform(src, mode="minor_to_major")
        self.assertEqual(r["abc"], src)
        self.assertTrue(r["mode_noop"])

    def test_noop_mode_still_transposes(self):
        """调式操作 no-op 不能吞掉同一请求里的移调：+5 该走半音照样走。"""
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:Am\nA B c d |\n"
        r = abc_edit.transform(src, transpose=5, mode="major_to_minor")
        self.assertTrue(r["mode_noop"])
        self.assertIn("K: Dm", r["abc"])
        self.assertEqual(actual_pitches(r["abc"]),
                         [p + 5 for p in actual_pitches(src)])

    def test_matching_relative_op_reports_no_noop(self):
        src = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:C\nC E G c |\n"
        r = abc_edit.transform(src, mode="major_to_relative_minor")
        self.assertFalse(r["mode_noop"])
        self.assertIn("K: Am", r["abc"])


class ModalKeyParsing(unittest.TestCase):
    """K: 行调式判定：`m` 不能被当成任何以 m 开头的词。"""

    def test_mixolydian_is_not_minor(self):
        """K:Gmix 的三级是 B（大三度），旧判定 rest.startswith("m") 会把它当小调。"""
        src = "X:1\nM:4/4\nL:1/4\nK:Gmix\nG A B c | d e f g |\n"
        r = abc_edit.transform(src, mode="major_to_minor")
        # 按大调处理 → 三级 B 降半音成 Bb、六度 E 降成 Eb、七度 F# 降成 F
        self.assertFalse(r["mode_noop"])
        self.assertEqual(actual_pitches(r["abc"]),
                         [7, 9, 10, 12, 14, 15, 17, 19])
        self.assertIn("K: Gm", r["abc"])

    def test_plain_minor_still_detected(self):
        for key in ("Am", "Em", "Cm", "F#m", "Bbm", "Amin", "Aminor"):
            src = f"X:1\nM:4/4\nL:1/4\nK:{key}\nA B c d |\n"
            r = abc_edit.transform(src, mode="major_to_minor")
            self.assertTrue(r["mode_noop"], f"{key} 应被识别为小调")


class InlineKeyChange(unittest.TestCase):
    """谱中间变调：K: 行/行内 [K:G] 之后必须按**那一段自己的调**算音高与调号。

    旧实现把每一处 inline K: 都改写成文件头的新调名，一段 K:C→K:G 的曲子移调后
    两个调号变成同一个，后半段整段跑调。真实语料里暂时没有中途变调，
    但错了就是整段错，所以按正确语义实现并钉死。
    """

    SRC = "X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:C\nC D E F |\nK:G\nG A B c |\n"

    def test_transpose_moves_each_section_key(self):
        r = abc_edit.transform(self.SRC, transpose=2)
        keys = [l for l in r["abc"].split("\n") if l.startswith("K:")]
        self.assertEqual(keys, ["K: D", "K: A"])
        # C D E F → D E F# G（K:D 的 F 自带升号）；G A B c → A B C# d（K:A）
        self.assertEqual(actual_pitches(r["abc"]), [2, 4, 6, 7, 9, 11, 13, 14])

    def test_modal_conversion_applies_to_every_section(self):
        r = abc_edit.transform(self.SRC, mode="major_to_minor")
        self.assertIn("K: Cm", r["abc"])
        self.assertIn("K: Gm", r["abc"])
        # 每段的三级都降半音：E→Eb、B→Bb
        self.assertEqual(actual_pitches(r["abc"]), [0, 2, 3, 5, 7, 9, 10, 12])

    def test_bracket_inline_field_moves_key(self):
        src = "X:1\nM:4/4\nL:1/4\nK:C\nC D E F |[K:G] G A B c |\n"
        r = abc_edit.transform(src, transpose=2)
        self.assertIn("[K:A]", r["abc"])
        self.assertEqual(actual_pitches(r["abc"]), [2, 4, 6, 7, 9, 11, 13, 14])

    def test_bracket_inline_key_uses_correct_enharmonic_name(self):
        """K:C 升 1 半音：[K:C] 段应写成 [K:Db]，不是非法的 [K:_d] 或 7 个升号的 [K:C#]。"""
        src = "X:1\nM:4/4\nL:1/4\nK:C\nC D E F |[K:G] G A B c |\n"
        r = abc_edit.transform(src, transpose=1)
        self.assertIn("[K:Ab]", r["abc"])

    def test_noop_leaves_inline_key_text_alone(self):
        """什么都不改的请求必须逐字节相同（包括 K: 行的空白写法）。"""
        self.assertEqual(abc_edit.transform(self.SRC)["abc"], self.SRC)


class EnharmonicKeySpelling(unittest.TestCase):
    """等音等价时写升降号更少的调：+1 是 Db（5 降），不是 C#（7 升）。"""

    def _key_line(self, abc):
        return next(l for l in abc.split("\n") if l.startswith("K:"))

    def test_up_one_is_dflat_not_csharp(self):
        src = "X:1\nM:4/4\nL:1/4\nK:C\nC D E F |\n"
        r = abc_edit.transform(src, transpose=1)
        self.assertEqual(self._key_line(r["abc"]), "K: Db")
        self.assertEqual(r["key"], "Db")
        self.assertEqual(actual_pitches(r["abc"]), [1, 3, 5, 6])

    def test_flat_side_keys(self):
        src = "X:1\nM:4/4\nL:1/4\nK:C\nC D E F |\n"
        for delta, want in ((3, "Eb"), (8, "Ab"), (10, "Bb"), (-1, "B")):
            r = abc_edit.transform(src, transpose=delta)
            self.assertEqual(self._key_line(r["abc"]), f"K: {want}", f"{delta:+d}")

    def test_declared_csharp_still_read_as_seven_sharps(self):
        """输入侧不能被等音改选影响：K:C# 的 f 是 F#（7 升），不是 Db 调号下的 F。"""
        src = "X:1\nM:4/4\nL:1/4\nK:C#\nC D E F |\n"
        # K:C# 里四个音都在升号上：C# D# E# F#
        self.assertEqual(actual_pitches(src), [1, 3, 5, 6])
        # 若被误读成 Db 调号，C 会当自然 C、F 会当自然 F，下面两个断言都会挂
        r = abc_edit.transform(src, transpose=1)
        self.assertEqual(self._key_line(r["abc"]), "K: D")
        self.assertEqual(actual_pitches(r["abc"]), [2, 4, 6, 7])


if __name__ == "__main__":
    unittest.main()
