"""v2.0 §四-③ / §六 P1：乐谱导出的回归锁定（规划 §9.1 表第 3 行）。

覆盖：① 未归档 MIDI 时返回明确错误而非 200 空体；② format 白名单外的取值 400；
③ 删除乐谱时连带清理归档目录。外加转谱结束时真的把 sheetsage2-output/ 归档到
data/scores/<id>/（否则上面三条都是空的）。

沿用 tests/test_review_fixes.py 的 Sandbox 基线（SCORES_DIR 已重定向到临时目录），
不提交任何 SheetSage2/GPU 计算。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_review_fixes import LOCAL_BASE, Sandbox, app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ABC = "X:1\nT:t\nM:4/4\nK:C\nC D E F |\n"
RID = "20260101_000000_aaaaaaaa"


class ScoresExportBase(Sandbox):
    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def _rec(self, rid: str = RID, artifacts: list[str] | None = None,
             abc: str = ABC) -> Path:
        p = Path(app.SCORES_DIR) / f"{rid}.json"
        p.write_text(json.dumps({"id": rid, "time": "2026-01-01 00:00:00", "abc": abc,
                                 "analysis": app._abc_analyze(abc),
                                 "artifacts": artifacts or []}, ensure_ascii=False),
                     encoding="utf-8")
        return p

    def _archive(self, rid: str = RID, **files: bytes | str) -> Path:
        d = Path(app.SCORES_DIR) / rid
        d.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (d / name).write_bytes(content.encode("utf-8")
                                   if isinstance(content, str) else content)
        return d


class TestScoresExportFormatWhitelist(ScoresExportBase):
    def test_unknown_format_is_400(self):
        self._rec(RID, ["transcription.mid"])
        for bad in ("xml", "musicxml", "pdf", "MID", "", "mid;rm"):
            r = self.client.get(f"/api/scores/{RID}/export", params={"format": bad})
            self.assertEqual(r.status_code, 400, f"{bad!r}：{r.text}")
            self.assertIn("不支持的导出格式", r.json()["detail"])

    def test_musicxml_declared_out_of_scope_not_empty_200(self):
        self._rec(RID, ["transcription.mid"])
        r = self.client.get(f"/api/scores/{RID}/export", params={"format": "musicxml"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("MusicXML", r.json()["detail"], "白名单外要说清是本期不做，不是漏了")


class TestScoresExportMidi(ScoresExportBase):
    def test_unarchived_midi_is_clear_error_not_empty_body(self):
        self._rec(RID, [])          # 老记录：转谱时没有归档这一步
        r = self.client.get(f"/api/scores/{RID}/export", params={"format": "mid"})
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("没有归档 MIDI", r.json()["detail"])
        # 关键：绝不是 200 + 空文件（那是把"没有"伪装成"有但为空"）
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-type", "").split(";")[0], "application/json")

    def test_recorded_but_file_lost_is_clear_error(self):
        self._rec(RID, ["transcription.mid"])   # 登记了，但归档目录没建
        r = self.client.get(f"/api/scores/{RID}/export", params={"format": "mid"})
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("重新转谱", r.json()["detail"])

    def test_empty_archived_file_is_not_served(self):
        self._rec(RID, ["transcription.mid"])
        self._archive(RID, **{"transcription.mid": ""})    # 0 字节
        r = self.client.get(f"/api/scores/{RID}/export", params={"format": "mid"})
        self.assertEqual(r.status_code, 404, r.text)
        self.assertNotEqual(len(r.content), 0)

    def test_midi_download_prefers_full_transcription(self):
        names = ["melody.mid", "transcription.mid", "chords.mid"]
        self._rec(RID, names)
        d = self._archive(RID, **{n: f"MIDI-DATA-{n}".encode() for n in names})
        r = self.client.get(f"/api/scores/{RID}/export", params={"format": "mid"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.content, b"MIDI-DATA-transcription.mid")
        self.assertIn("transcription.mid", r.headers["content-disposition"])
        # 只留 melody.mid 时降级到旋律轨（不是报错）
        (d / "transcription.mid").unlink()
        self._rec(RID, ["melody.mid", "chords.mid"])
        r2 = self.client.get(f"/api/scores/{RID}/export", params={"format": "mid"})
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.content, b"MIDI-DATA-melody.mid")

    def test_midi_not_in_record_is_not_served_even_if_on_disk(self):
        """归档目录里有未登记的文件（同名目录被外部塞过东西）→ 不能顺着路径发出去。"""
        self._rec(RID, [])
        self._archive(RID, **{"transcription.mid": b"STRAY"})
        r = self.client.get(f"/api/scores/{RID}/export", params={"format": "mid"})
        self.assertEqual(r.status_code, 404, r.text)

    def test_export_404_for_unknown_score(self):
        r = self.client.get(f"/api/scores/{RID}/export", params={"format": "abc"})
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("乐谱不存在", r.json()["detail"])


class TestScoresExportAbc(ScoresExportBase):
    def test_abc_from_record_when_not_archived(self):
        self._rec(RID, [])
        r = self.client.get(f"/api/scores/{RID}/export", params={"format": "abc"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("X:1", r.text)

    def test_abc_prefers_archived_file(self):
        self._rec(RID, ["score.abc"])
        self._archive(RID, **{"score.abc": "X:2\nT:archived\n"})
        r = self.client.get(f"/api/scores/{RID}/export", params={"format": "abc"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("archived", r.text)

    def test_blank_abc_is_error_not_empty_200(self):
        self._rec(RID, [], abc="   ")
        r = self.client.get(f"/api/scores/{RID}/export", params={"format": "abc"})
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("没有 ABC", r.json()["detail"])


class TestScoresDeleteCleansArchive(ScoresExportBase):
    def test_delete_removes_archive_dir(self):
        self._rec(RID, ["transcription.mid", "events.json"])
        d = self._archive(RID, **{"transcription.mid": "M", "events.json": "{}"})
        r = self.client.delete(f"/api/scores/{RID}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse((Path(app.SCORES_DIR) / f"{RID}.json").exists())
        self.assertFalse(d.exists(), "删谱必须连归档目录一起删，否则 MIDI 成孤儿")
        self.assertIn(f"{RID}/", r.json()["removed"])

    def test_delete_leaves_neighbour_alone(self):
        other = "20260101_000001_bbbbbbbb"
        self._rec(RID, ["transcription.mid"])
        self._archive(RID, **{"transcription.mid": "M"})
        self._rec(other, ["melody.mid"])
        od = self._archive(other, **{"melody.mid": "NEIGHBOUR"})
        self.client.delete(f"/api/scores/{RID}")
        self.assertTrue(od.is_dir(), "删一条不许碰另一条的归档")
        self.assertEqual((od / "melody.mid").read_bytes(), b"NEIGHBOUR")

    def test_delete_is_idempotent_and_safe_on_traversal(self):
        r = self.client.delete("/api/scores/..")
        self.assertIn(r.status_code, (200, 400, 404, 405), r.text)
        # 目录本身不能被删掉
        self.assertTrue(Path(app.SCORES_DIR).is_dir())


class TestTranscriptionArchives(ScoresExportBase):
    """转谱结束时归档：规划 §六「转谱任务结束时把 sheetsage2-output/ 里的产物随 score_id 归档」。"""

    def setUp(self) -> None:
        super().setUp()
        import sheetsage_pt
        self._sheetsage = sheetsage_pt
        self._saved_fn = getattr(sheetsage_pt, "transcribe_abc")
        self._saved_run = app._win_toast
        app._win_toast = lambda *a, **k: None

    def tearDown(self) -> None:
        self._sheetsage.transcribe_abc = self._saved_fn
        app._win_toast = self._saved_run
        super().tearDown()

    def test_score_run_archives_into_scores_dir(self):
        seen: dict = {}

        def fake_transcribe(audio_path, *, melody_only=True, archive_dir=None):
            seen["archive_dir"] = archive_dir
            if archive_dir:
                # 模拟真实现：把共享目录里这一首歌的产物复制进 archive_dir
                d = Path(archive_dir)
                d.mkdir(parents=True, exist_ok=True)
                (d / "transcription.mid").write_bytes(b"MID")
                (d / "score.abc").write_text(ABC, encoding="utf-8")
            return ABC

        self._sheetsage.transcribe_abc = fake_transcribe
        src = Path(app.OUTPUT_DIR) / "in.wav"
        src.write_bytes(b"RIFF")
        app._SCORE_JOBS.clear()
        app._SCORE_JOBS[RID] = {"done": False, "ts": 1.0}
        app._score_run(RID, src, melody_only=False)

        self.assertEqual(seen["archive_dir"], str(Path(app.SCORES_DIR) / RID),
                         "归档目录必须随 score_id 落在 data/scores/<id>/")
        rec = json.loads((Path(app.SCORES_DIR) / f"{RID}.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["artifacts"], ["score.abc", "transcription.mid"])
        r = self.client.get(f"/api/scores/{RID}/export", params={"format": "mid"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.content, b"MID")

    def test_archive_outputs_only_whitelisted_top_level_files(self):
        d = self.tmp / "sheetsage2-output"
        d.mkdir(parents=True)
        (d / "transcription.mid").write_bytes(b"M")
        (d / "chord.lab").write_text("lab", encoding="utf-8")
        (d / "events.json").write_text("{}", encoding="utf-8")
        (d / "score.pdf").write_bytes(b"%PDF")           # 本期不做谱面 PDF
        sub = d / "notation"
        sub.mkdir()
        (sub / "nested.mid").write_bytes(b"N")           # 不递归
        dest = self.tmp / "arch"
        names = self._sheetsage.archive_outputs(d, dest)
        self.assertEqual(sorted(names), ["chord.lab", "events.json", "transcription.mid"])
        self.assertFalse((dest / "score.pdf").exists())
        self.assertFalse((dest / "nested.mid").exists())

    def test_archive_outputs_empty_source_returns_nothing(self):
        dest = self.tmp / "arch2"
        self.assertEqual(self._sheetsage.archive_outputs(self.tmp / "nope", dest), [])
        self.assertFalse((dest / "transcription.mid").exists())


class TestScoresListExposesArtifacts(ScoresExportBase):
    def test_list_flags_archived_midi(self):
        self._rec(RID, ["transcription.mid"])
        self._rec("20260101_000002_cccccccc", [])
        out = {r["id"]: r for r in self.client.get("/api/scores").json()}
        self.assertTrue(out[RID]["has_midi"])
        self.assertFalse(out["20260101_000002_cccccccc"]["has_midi"])
        self.assertIn("abc_preview", out[RID], "列表旧字段不能丢（前端乐谱摘要还在用）")


if __name__ == "__main__":
    unittest.main()
