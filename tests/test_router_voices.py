"""音色库路由拆分后的契约：路径、方法、只读列表口仍在。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class VoiceRoutes(unittest.TestCase):
    def test_routes_are_registered(self):
        got = {(route.path, method)
               for route in app.app.routes
               for method in (getattr(route, "methods", None) or [])}
        expected = {
            ("/api/voices", "GET"),
            ("/api/voices", "POST"),
            ("/api/voices/{voice_id}", "DELETE"),
            ("/api/voices/transcribe", "POST"),
            ("/api/voices/denoise", "POST"),
        }
        self.assertTrue(expected <= got, f"缺少路由：{expected - got}")

    def test_list_is_read_only_and_shaped(self):
        client = TestClient(app.app, base_url="http://127.0.0.1")
        r = client.get("/api/voices")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json().get("voices"), list)


if __name__ == "__main__":
    unittest.main()
