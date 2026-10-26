"""LRC 域拆分后的契约：路由 + 动态 LRC_STANDALONE_DIR。"""

from __future__ import annotations

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


class LrcRoutes(unittest.TestCase):
    def test_routes_are_registered(self):
        got = {(route.path, method)
               for route in app.app.routes
               for method in (getattr(route, "methods", None) or [])}
        expected = {
            ("/api/lrc/start", "POST"),
            ("/api/lrc/list", "GET"),
            ("/api/lrc/status/{rid}", "GET"),
            ("/api/lrc/download/{rid}", "GET"),
            ("/api/lrc/{rid}", "DELETE"),
        }
        self.assertTrue(expected <= got, f"缺少路由：{expected - got}")

    def test_dynamic_standalone_dir(self):
        saved = app.LRC_STANDALONE_DIR
        with tempfile.TemporaryDirectory() as tmp:
            app.LRC_STANDALONE_DIR = Path(tmp)
            try:
                app._lrc_sa_write("20261026_000000_abcd1234",
                                  {"status": "done", "name": "测试", "ts": "2026-10-26"})
                client = TestClient(app.app, base_url="http://127.0.0.1")
                items = client.get("/api/lrc/list").json()["items"]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["name"], "测试")
            finally:
                app.LRC_STANDALONE_DIR = saved


if __name__ == "__main__":
    unittest.main()
