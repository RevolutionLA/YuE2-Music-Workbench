"""乐谱域拆分后的契约：路由、锁/任务表同一对象、动态 SCORES_DIR 仍生效。"""

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

RID = "20261026_000000_abcdef12"
ABC = "X:1\nM:4/4\nK:C\nC2 D2 E2 F2 |\n"


class ScoreRoutes(unittest.TestCase):
    def test_routes_are_registered(self):
        got = {(route.path, method)
               for route in app.app.routes
               for method in (getattr(route, "methods", None) or [])}
        expected = {
            ("/api/score/status", "GET"),
            ("/api/score", "POST"),
            ("/api/score/result", "GET"),
            ("/api/scores", "GET"),
            ("/api/scores/{score_id}", "GET"),
            ("/api/scores/{score_id}/export", "GET"),
            ("/api/scores/{score_id}", "DELETE"),
        }
        self.assertTrue(expected <= got, f"缺少路由：{expected - got}")

    def test_shared_state_aliases(self):
        self.assertIs(app._SCORE_JOBS, app._score_routes.SCORE_JOBS)
        self.assertIs(app._SCORE_LOCK, app._score_routes.SCORE_LOCK)
        self.assertIs(app._SCORE_ENGINE_LOCK, app._score_routes.SCORE_ENGINE_LOCK)
        self.assertTrue(callable(app._score_save))
        self.assertTrue(callable(app._score_run))

    def test_dynamic_scores_dir_still_works(self):
        saved = app.SCORES_DIR
        with tempfile.TemporaryDirectory() as tmp:
            app.SCORES_DIR = Path(tmp)
            try:
                rec = app._score_save(RID, ABC)
                self.assertEqual(rec["id"], RID)
                self.assertTrue((Path(tmp) / f"{RID}.json").is_file())
                client = TestClient(app.app, base_url="http://127.0.0.1")
                items = client.get("/api/scores").json()
                self.assertEqual([i["id"] for i in items], [RID])
                self.assertEqual(client.get(f"/api/scores/{RID}").json()["abc"], ABC)
            finally:
                app.SCORES_DIR = saved


if __name__ == "__main__":
    unittest.main()
