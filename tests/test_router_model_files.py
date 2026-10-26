"""模型校验/修复域拆分后的契约。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402


class ModelFilesRoutes(unittest.TestCase):
    def test_routes_are_registered(self):
        got = {(route.path, method)
               for route in app.app.routes
               for method in (getattr(route, "methods", None) or [])}
        expected = {
            ("/api/models/verify", "GET"),
            ("/api/models/repair", "POST"),
            ("/api/models/repair/status", "GET"),
        }
        self.assertTrue(expected <= got, f"缺少路由：{expected - got}")

    def test_verify_uses_active_manifest(self):
        j = app.models_verify()
        self.assertIn(j.get("source"), ("active", "fallback-q4"))
        self.assertEqual(len(j.get("files") or []), 7)
        self.assertIn("repair_running", j)


if __name__ == "__main__":
    unittest.main()
