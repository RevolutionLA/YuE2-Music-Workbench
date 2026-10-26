"""P1：生成调用统一为进程内，不再反向 HTTP 打自己。"""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.responses import Response  # noqa: E402


class InProcessGenerate(unittest.TestCase):
    def test_calls_kernel_function_without_http(self):
        async def fake(payload):
            return Response(
                content=b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 64,
                media_type="audio/wav",
                headers={"X-Seed": "7"},
            )

        with mock.patch.object(app.main, "generate_response", fake), \
             mock.patch.object(app.httpx, "Client",
                               side_effect=AssertionError("不应反向 HTTP")), \
             mock.patch.object(app, "_rvc_keep_awake") as awake:
            code, detail, content, headers = app._gen_run_once({"style": "x"})

        self.assertEqual(code, 200)
        self.assertEqual(detail, "")
        self.assertTrue(content.startswith(b"RIFF"))
        self.assertEqual(app._seed_from_headers(headers, {}), "7")
        self.assertEqual([c.args[0] for c in awake.call_args_list], [True, False])

    def test_http_exception_maps_to_tuple(self):
        async def fake(payload):
            raise HTTPException(status_code=400, detail="style is required")

        with mock.patch.object(app.main, "generate_response", fake):
            code, detail, content, headers = app._gen_run_once({})

        self.assertEqual((code, detail, content, headers),
                         (400, "style is required", b"", {}))

    def test_source_has_no_reverse_self_call(self):
        src = inspect.getsource(app._gen_run_once)
        self.assertIn("main.generate_response", src)
        self.assertNotIn('client.post("/api/music/generate"', src)
        self.assertNotIn("settings.app_port", src)


class KernelSplit(unittest.TestCase):
    def test_generate_response_is_reusable(self):
        self.assertTrue(callable(getattr(app.main, "generate_response", None)))

    def test_route_delegates_to_generate_response(self):
        src = inspect.getsource(app.main.music_generate)
        self.assertIn("await generate_response(payload)", src)

    def test_real_generation_is_blocked_in_tests(self):
        import asyncio

        with self.assertRaises(HTTPException) as caught:
            asyncio.run(app.main.generate_response({"style": "s", "lyrics": "la"}))
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("禁止真生成", str(caught.exception.detail))


if __name__ == "__main__":
    unittest.main()
