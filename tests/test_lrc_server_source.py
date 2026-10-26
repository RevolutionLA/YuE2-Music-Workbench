"""⑭ 直接选服务器歌曲做歌词对齐（不必重新上传）。"""

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
WAV = b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 64


class FakeAlign:
    @staticmethod
    def align(src, lyrics):
        return {"lrc": "[00:00.00]" + lyrics + "\n", "elrc": "", "method": "fake"}


class LrcServerSource(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app, base_url="http://127.0.0.1")
        self._saved = (app.LRC_STANDALONE_DIR, app._server_audio_path,
                       app._output_read_meta, app.lrc_align_mod)
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        app.LRC_STANDALONE_DIR = root / "lrc_jobs"
        self.song = root / "song.wav"
        self.song.write_bytes(WAV)
        app._server_audio_path = lambda rid: self.song
        app._output_read_meta = lambda rid: {"task_name": "服务器歌"}
        app.lrc_align_mod = FakeAlign

    def tearDown(self):
        (app.LRC_STANDALONE_DIR, app._server_audio_path,
         app._output_read_meta, app.lrc_align_mod) = self._saved
        self._tmp.cleanup()

    def test_server_song_roundtrip(self):
        r = self.client.post("/api/lrc/start",
                             data={"rid": RID, "lyrics": "第一行\n第二行"})
        self.assertEqual(r.status_code, 200, r.text)
        jid = r.json()["id"]
        deadline = time.time() + 5
        st = {}
        while time.time() < deadline:
            st = self.client.get(f"/api/lrc/status/{jid}").json()
            if st.get("status") in ("done", "error"):
                break
            time.sleep(0.05)
        self.assertEqual(st.get("status"), "done", st)
        self.assertIn("第一行", st.get("lrc", ""))
        self.assertEqual(st.get("name"), "服务器歌")
        self.assertEqual(st.get("file"), "song.wav")
        self.assertTrue((Path(app.LRC_STANDALONE_DIR) / jid / "src.wav").is_file())

    def test_neither_file_nor_rid_is_400(self):
        r = self.client.post("/api/lrc/start", data={"lyrics": "la"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("服务器歌曲", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
