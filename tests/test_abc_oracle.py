"""用第三方解析器（abcjs）复核 ABC 加工：真跑，不是自证。

为什么不写在 test_abc_edit.py 里：那边是"按我们理解的规则"断言输出，属于自证。
移调最怕的就是自洽地错——2026-10 那批 bug（漏调号、漏小节内继承、后缀变音记号）
当时单测全绿，真唱出来整曲跑调。所以这里换一把尺子：把加工前后的 ABC 都交给
abcjs，用它的 MIDI 结果逐音对比。

本机没有 node 或没有 abcjs 资源时自动跳过（CI 里不装 node 也能跑全套）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import abc_edit  # noqa: E402

ABCJS = os.path.join(ROOT, "checkpoints", "render_assets", "abcjs-basic-min.js")
RUNNER = os.path.join(ROOT, "tests", "abc_oracle.js")

# 素材取自真实 SheetSage2 输出的形状（带 V: 声部行、引号和弦、斜杠低音、Z| 多小节休止），
# 另外补上手写谱才会出现的写法：中途变调、连音块、连字符、双变音、小节内继承。
FIXTURES = [
    dict(
        name="K:E 声部+和弦",
        allow=("transpose", "mode", "combo", "strip"),
        abc=('X:1\nT:\nM:4/4\nL:1/16\nQ:1/4=95\n'
             'V: Vocal clef=treble name="Vocal Melody" snm="Vocal"\n'
             'V: Ins clef=treble name="Ins Melody" snm="Inst."\n'
             'K:E\n% intro\nV: Vocal\n'
             'Z|"E"c\'2bb3gf|"B"g2z6|"C#m"c\'2g\'2g\'2c\'2|"A"f\'2g\'2z2e\'d\'|\n'
             'V: Ins\n"C#m"z4z4|"A"z4z4|"E"z4z4|"B7/D#"z4z4|\n'),
    ),
    dict(
        name="K:Eb 三个降号",
        allow=("transpose", "mode", "combo", "strip"),
        abc=('X:1\nM:4/4\nL:1/8\nQ:1/4=80\nK:Eb\n'
             'E F G A | B c d e | "Eb"e2 d2 | "Bb/D"d2 B2 |\n'),
    ),
    dict(
        name="K:Gm 小调",
        allow=("transpose", "mode", "combo", "strip"),
        abc=('X:1\nM:6/8\nL:1/8\nQ:1/4=76\nK:Gm\n'
             'G A B c | d e f g | "Gm"g2 f2 d2 | "D7"^f2 a2 |\n'),
    ),
    dict(
        name="K:C# 七个升号",
        allow=("transpose", "mode", "combo", "strip"),
        abc=('X:1\nM:4/4\nL:1/4\nK:C#\nC D E F | G A B c | "C#"C2 "G#/B#"B,2 |\n'),
    ),
    dict(
        name="小节内继承与双变音",
        allow=("transpose", "mode", "combo"),
        abc=('X:1\nM:4/4\nL:1/4\nK:G\n'
             '^F F G A | =F F G A | ^^C C c c | __B B d d |\n'),
    ),
    dict(
        name="连音块/连字符/三连音",
        allow=("transpose", "mode", "combo", "strip"),
        abc=('X:1\nM:4/4\nL:1/8\nK:D\n'
             '(3A B c d2- d2 |[CEG]2 z2 "A7"c2 B2 | "D/F#"F2 A2 d2 f2 |\n'),
    ),
    dict(
        name="中途变调（K: 独占一行）",
        allow=("transpose",),
        abc='X:1\nM:4/4\nL:1/4\nQ:1/4=88\nK:C\nC D E F |\nK:G\nG A B c |\n',
    ),
    dict(
        name="中途变调（行内 [K:G]）",
        allow=("transpose",),
        abc='X:1\nM:4/4\nL:1/4\nK:C\nC D E F |[K:G] G A B c |\n',
    ),
]

DELTAS = [-12, -7, -5, -3, -2, -1, 1, 2, 3, 5, 7, 11, 12]
MODES = ["major_to_minor", "minor_to_major",
         "major_to_relative_minor", "minor_to_relative_major"]
COMBOS = [(-5, "major_to_minor"), (1, "minor_to_major"),
          (5, "major_to_relative_minor"), (-2, "minor_to_relative_major")]


def _node() -> str | None:
    for cand in (
        os.path.join(os.path.expanduser("~"), ".workbuddy", "binaries", "node",
                     "versions", "22.22.2-6", "node.exe"),
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files\nodejs\node",
        "/usr/local/bin/node",
        "/usr/bin/node",
    ):
        if os.path.exists(cand):
            return cand
    return shutil.which("node")


def build_cases() -> list[dict]:
    cases = []
    for fx in FIXTURES:
        allow = fx["allow"]
        base = dict(name=fx["name"], orig=fx["abc"])
        if "transpose" in allow:
            for d in DELTAS:
                r = abc_edit.transform(fx["abc"], transpose=d)
                cases.append(dict(base, kind="transpose", param=d, trans=r["abc"]))
        if "mode" in allow:
            for m in MODES:
                r = abc_edit.transform(fx["abc"], mode=m)
                cases.append(dict(base, kind="mode", param=m, trans=r["abc"]))
        if "combo" in allow:
            for t, m in COMBOS:
                r = abc_edit.transform(fx["abc"], transpose=t, mode=m)
                cases.append(dict(base, kind="combo", param=f"{m}{t:+d}",
                                  transpose=t, mode=m, trans=r["abc"]))
        if "strip" in allow:
            r = abc_edit.transform(fx["abc"], strip_chords=True)
            cases.append(dict(base, kind="strip", param="strip", trans=r["abc"]))
    return cases


class AbcOracle(unittest.TestCase):
    """加工后的谱必须与 abcjs 算出的音高/节奏/和弦完全一致。"""

    def setUp(self):
        self.node = _node()
        if not self.node:
            self.skipTest("本机没有 node，跳过 abcjs 独立复核")
        if not os.path.exists(ABCJS):
            self.skipTest("缺少 checkpoints/render_assets/abcjs-basic-min.js")

    def test_transforms_match_third_party_parser(self):
        cases = build_cases()
        self.assertGreater(len(cases), 100)
        # 先自检：加工结果不能带旧版坏谱指纹，也不能出现双重变音记号
        for c in cases:
            self.assertEqual(abc_edit._legacy_corruption(c["trans"]), [],
                             f"{c['name']} {c['kind']} {c['param']} 产出了坏谱")
            # 双重变音只能是源谱本来就有的（如 ^^C），加工不能凭空造出来
            for mark in ("^^", "__"):
                self.assertLessEqual(c["trans"].count(mark), c["orig"].count(mark),
                                     f"{c['name']} {c['kind']} {c['param']} 凭空多了 {mark}")
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cases, fh, ensure_ascii=False)
            r = subprocess.run([self.node, RUNNER, tmp, ABCJS],
                               capture_output=True, text=True, timeout=120)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        self.assertEqual(r.returncode, 0,
                         "abcjs 复核未通过：\n" + (r.stdout or "") + (r.stderr or "")[:800])

    def test_noop_transform_is_byte_identical(self):
        """零加工的请求必须逐字节相同——"没变"不能显示成"改过了"。"""
        for fx in FIXTURES:
            self.assertEqual(abc_edit.transform(fx["abc"])["abc"], fx["abc"], fx["name"])


if __name__ == "__main__":
    unittest.main()
