"""ABC 分析模块：纯函数契约。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import abc_analysis  # noqa: E402

CHORDED = 'X:1\nM:4/4\nL:1/16\nQ:1/4=95\nK:Em\n"Em"e2e2e2b2a2b2a2ge|\n'
MELODY = "X:1\nM:4/4\nK:C\nV: Vocal\nC2 D2 E2 F2 |\n"


class AbcAnalysis(unittest.TestCase):
    def test_chords_detected_and_extended_symbols_allowed(self):
        self.assertTrue(abc_analysis.has_chords(CHORDED))
        self.assertTrue(abc_analysis.has_chords('X:1\nK:C\n"C9" C2 "Fm9" D2|\n'))
        self.assertFalse(abc_analysis.has_chords(MELODY))
        # 引号里的英文单词不算和弦
        self.assertFalse(abc_analysis.has_chords('X:1\nK:C\n"Chorus" C2 D2|\n'))

    def test_analyze_summary(self):
        a = abc_analysis.analyze(CHORDED)
        self.assertEqual(a["key"], "E 小调")
        self.assertEqual(a["meter"], "4/4")
        self.assertEqual(a["bpm"], 95)
        self.assertEqual(a["note_count"], 9)
        self.assertTrue(a["has_chords"])
        self.assertEqual(a["chord_count"], 1)
        self.assertEqual(a["cot_suggested"], "full")


if __name__ == "__main__":
    unittest.main()
