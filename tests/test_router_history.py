"""历史 CRUD 拆分后的契约：5 条路由 + 动态 HIST_DIR 往返。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

WAV = b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 64


class HistoryRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app, base_url="http://127.0.0.1")
        self._saved = (app.HIST_DIR, app.HIST_JSON, app._output_purge)
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        app.HIST_DIR = root
        app.HIST_JSON = root / "history.json"
        app._output_purge = lambda rid: None

    def tearDown(self):
        app.HIST_DIR, app.HIST_JSON, app._output_purge = self._saved
        self._tmp.cleanup()

    def _add(self, lyrics="la"):
        return self.client.post(
            "/api/history",
            files={"audio": ("a.wav", WAV, "audio/wav")},
            data={"meta": json.dumps({"style": "piano", "lyrics": lyrics})},
        )

    def test_routes_are_registered(self):
        got = {(route.path, method)
               for route in app.app.routes
               for method in (getattr(route, "methods", None) or [])}
        expected = {
            ("/api/history", "GET"),
            ("/api/history", "POST"),
            ("/api/history/{rid}/audio", "GET"),
            ("/api/history/clear", "DELETE"),
            ("/api/history/{rid}", "DELETE"),
        }
        self.assertTrue(expected <= got, f"缺少路由：{expected - got}")

    def test_roundtrip_and_clear(self):
        r = self._add("第一首")
        self.assertEqual(r.status_code, 200, r.text)
        item = r.json()["item"]
        rid, fn = item["id"], item["file"]
        self.assertTrue((Path(app.HIST_DIR) / fn).is_file())

        items = self.client.get("/api/history").json()["items"]
        self.assertEqual([i["id"] for i in items], [rid])
        self.assertEqual(items[0]["lyrics"], "第一首")

        audio = self.client.get(f"/api/history/{rid}/audio")
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.content, WAV)

        self.assertEqual(self.client.delete(f"/api/history/{rid}").status_code, 200)
        self.assertEqual(self.client.get("/api/history").json()["items"], [])
        self.assertFalse((Path(app.HIST_DIR) / fn).exists())

        self.assertEqual(self._add().status_code, 200)
        self.assertEqual(self._add().status_code, 200)
        self.assertEqual(self.client.delete("/api/history/clear").status_code, 200)
        self.assertEqual(self.client.get("/api/history").json()["items"], [])
        self.assertEqual(list(Path(app.HIST_DIR).glob("*.wav")), [])


if __name__ == "__main__":
    unittest.main()
