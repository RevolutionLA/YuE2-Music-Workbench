"""③ 参考歌曲也能直接选服务器上已有的歌（不必重新上传）。"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

RID = "20261026_000000_abcdef12"
ABC = "X:1\nM:4/4\nK:C\nC2 D2 E2 F2 |\n"
WAV = b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 64


class ScoreServerSource(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app, base_url="http://127.0.0.1")
        self._saved = (app.SCORES_DIR, app._server_audio_path)
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        app.SCORES_DIR = root / "scores"
        app.SCORES_DIR.mkdir(parents=True, exist_ok=True)
        self.song = root / "song.wav"
        self.song.write_bytes(WAV)
        app._server_audio_path = lambda rid: self.song
        app._SCORE_JOBS.clear()
        self._sheetsage = mock.patch(
            "sheetsage_pt.transcribe_abc",
            side_effect=lambda path, melody_only=True, archive_dir=None: ABC)
        self._sheetsage.start()

    def tearDown(self):
        self._sheetsage.stop()
        app.SCORES_DIR, app._server_audio_path = self._saved
        app._SCORE_JOBS.clear()
        self._tmp.cleanup()

    def test_server_song_roundtrip(self):
        r = self.client.post("/api/score", data={"rid": RID, "melody_only": "false"})
        self.assertEqual(r.status_code, 200, r.text)
        jid = r.json()["job_id"]
        deadline = time.time() + 5
        rec = {}
        while time.time() < deadline:
            rec = self.client.get(f"/api/score/result?job_id={jid}").json()
            if rec.get("done"):
                break
            time.sleep(0.05)
        self.assertTrue(rec.get("done"), rec)
        self.assertIsNone(rec.get("error"), rec)
        self.assertEqual(rec.get("abc"), ABC)
        sid = rec.get("score_id")
        self.assertTrue(sid)
        self.assertTrue((Path(app.SCORES_DIR) / f"{sid}.json").is_file())

    def test_neither_file_nor_rid_is_400(self):
        r = self.client.post("/api/score", data={"melody_only": "true"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("服务器歌曲", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
