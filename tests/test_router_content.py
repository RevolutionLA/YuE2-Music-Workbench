"""内容工具路由：主应用契约 + 临时根目录下的模板往返。"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from routers.content import build_router  # noqa: E402


def _client(root: Path) -> TestClient:
    api = FastAPI()
    api.include_router(build_router(
        templates_file=lambda: root / "templates.json",
        limit_text=lambda _label, value, limit: value[:limit],
        max_style=10,
        max_lyrics=100,
        max_abc=100,
        voices_mod=SimpleNamespace(_SAFE=re.compile(r"[^a-zA-Z0-9]+")),
    ))
    return TestClient(api)


class MainAppContract(unittest.TestCase):
    def test_routes_are_registered(self):
        got = {(route.path, method)
               for route in app.app.routes
               for method in (getattr(route, "methods", None) or [])}
        expected = {
            ("/api/lyrics/validate", "POST"),
            ("/api/templates", "GET"),
            ("/api/templates", "POST"),
            ("/api/templates/{template_id}", "DELETE"),
        }
        self.assertTrue(expected <= got, f"缺少路由：{expected - got}")


class TemplatesWithTempRoot(unittest.TestCase):
    def test_roundtrip_and_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = _client(root)
            r = client.post("/templates", json={"name": "我的模板", "style": "x" * 50})
            self.assertEqual(r.status_code, 200)
            tpl = r.json()["template"]
            self.assertEqual(len(tpl["style"]), 10, "模板文本必须走与生成入口同一上限")
            self.assertTrue((root / "templates.json").is_file())

            items = client.get("/templates").json()["templates"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["id"], tpl["id"])

            self.assertEqual(client.delete(f"/templates/{tpl['id']}").status_code, 200)
            self.assertEqual(client.get("/templates").json()["templates"], [])
            self.assertEqual(client.delete("/templates/nope").status_code, 404)

    def test_missing_name_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                _client(Path(tmp)).post("/templates", json={"style": "x"}).status_code, 400)

    def test_lyrics_validate(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _client(Path(tmp)).post(
                "/lyrics/validate", json={"lyrics": "[Verse]\nhello world"})
            self.assertEqual(r.status_code, 200)
            self.assertIsInstance(r.json(), dict)


if __name__ == "__main__":
    unittest.main()
