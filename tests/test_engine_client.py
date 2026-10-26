"""engine_client 的单元测试：不联网，用假 httpx.Client。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import engine_client  # noqa: E402


class FakeResponse:
    def __init__(self, body=None, status=200):
        self._body = {} if body is None else body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class FakeClient:
    last = None
    reply = FakeResponse()
    boom = False

    def __init__(self, *a, **kw):
        self.kw = kw

    def __enter__(self):
        if FakeClient.boom:
            raise RuntimeError("connect failed")
        return self

    def __exit__(self, *a):
        return False

    def get(self, path):
        FakeClient.last = ("GET", path, None)
        return FakeClient.reply

    def post(self, path, json=None):
        FakeClient.last = ("POST", path, json)
        return FakeClient.reply


class EngineClient(unittest.TestCase):
    def setUp(self):
        self._patch = mock.patch.object(engine_client.httpx, "Client", FakeClient)
        self._patch.start()
        FakeClient.boom = False
        FakeClient.last = None
        FakeClient.reply = FakeResponse({"ok": True})

    def tearDown(self):
        self._patch.stop()

    def test_base_url_has_no_trailing_slash(self):
        self.assertFalse(engine_client.base_url().endswith("/"))

    def test_get_json(self):
        self.assertEqual(engine_client.get_json("/health"), {"ok": True})
        self.assertEqual(FakeClient.last[:2], ("GET", "/health"))

    def test_get_json_returns_none_on_failure(self):
        FakeClient.boom = True
        self.assertIsNone(engine_client.get_json("/health"))
        self.assertIsNone(engine_client.status_code("/health"))

    def test_run_task_uses_tasks_run(self):
        r = engine_client.run_task({"model": "yue2"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(FakeClient.last, ("POST", "/v1/tasks/run", {"model": "yue2"}))

    def test_unload_all_returns_list(self):
        FakeClient.reply = FakeResponse({"unloaded": ["yue2"]})
        self.assertEqual(engine_client.unload_all(), ["yue2"])
        self.assertEqual(FakeClient.last[:2], ("POST", "/v1/tasks/unload_all_models"))

    def test_unload_all_is_best_effort(self):
        FakeClient.boom = True
        self.assertEqual(engine_client.unload_all(), [])


if __name__ == "__main__":
    unittest.main()
