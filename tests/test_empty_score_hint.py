"""③ 转谱"空结果"必须说不清（2026-10-17 实测：RMS≈0.012 的 4 秒片段，SheetSage2 只吐 Z3 休止）。

这条判据来自 `_abc_analyze` 新暴露的 note_count——它本来就在算音高列表，只是没说出口。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402

QUIET = ("X:1\nT:\nM:4/4\nL:1/16\nQ:1/4=124\n"
         'V: Vocal clef=treble name="Vocal Melody" snm="Vocal"\n'
         'V: Ins clef=treble name="Ins Melody" snm="Inst."\nK:C\n% intro\n'
         "V: Vocal\nZ3|\nV: Ins\nZ3|\n")
REAL = ("X:1\nM:4/4\nK:C\nV: Vocal\nC2 D2 E2 F2 |G2 A2 B2 c2 |\n")


class TestNoteCount(unittest.TestCase):
    def test_rests_only_scores_zero(self):
        a = app._abc_analyze(QUIET)
        self.assertEqual(a["note_count"], 0, "全是休止＝一个音都没有，这就是那份实测产物")
        self.assertNotIn("range", a, "没音就不该有音域")

    def test_real_melody_counts_notes(self):
        a = app._abc_analyze(REAL)
        self.assertGreater(a["note_count"], 0)
        self.assertIn("range", a)

    def test_chord_symbols_are_not_notes(self):
        # 和弦记号里的字母不是唱出来的音（Am7 不该被数成三个音）
        a = app._abc_analyze('X:1\nM:4/4\nK:C\nV: Vocal\n"Am7"z16|"D"z16|\n')
        self.assertEqual(a["note_count"], 0)


class TestPageTellsTheTruth(unittest.TestCase):
    def setUp(self):
        index = Path(app.__file__).resolve().parent / "static" / "index.html"
        with open(index, encoding="utf-8") as fh:
            self.html = fh.read()

    def test_transcribe_success_path_branches_on_note_count(self):
        script = self.html[self.html.index("<script>"):]
        body = script[script.index("if (!rj.abc) throw new Error"):]
        body = body[:2500]
        self.assertIn("note_count === 0", body,
                      "转谱回来只判「有没有 abc 文本」，一份全是休止的谱也算成功")
        self.assertIn("一个音都没有", body, "空谱要说清原因与下一步，不能只绿灯报保存")

    def test_analysis_line_warns_too(self):
        script = self.html[self.html.index("<script>"):]
        fn = script[script.index("function renderAbcAnalysis"):script.index("function renderAbcAnalysis") + 3000]
        self.assertIn("note_count === 0", fn, "手粘一份空谱时，谱面摘要也该指出没音")


if __name__ == "__main__":
    unittest.main()
