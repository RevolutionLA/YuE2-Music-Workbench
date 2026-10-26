"""局域网直连网关的可选令牌：单元 + 中间件集成测试（不联网、不跑引擎）。"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx  # noqa: E402

import app  # noqa: E402
import lan_auth  # noqa: E402

TOKEN = "s3cret-token"


def _request(method: str, path: str, *, client=("192.168.1.50", 50000),
             host="192.168.1.6", headers=None, cookies=None):
    async def run():
        transport = httpx.ASGITransport(app=app.app, client=client)
        async with httpx.AsyncClient(
                transport=transport, base_url=f"http://{host}:7863",
                cookies=cookies, follow_redirects=False) as c:
            return await c.request(method, path, headers=headers)
    return asyncio.run(run())


class LanAuthHelpers(unittest.TestCase):
    def test_loopback_detection(self):
        for h in ("127.0.0.1", "127.0.0.2", "::1", "localhost", "testclient"):
            self.assertTrue(lan_auth.client_is_loopback(h), h)
        for h in ("192.168.1.50", "10.0.0.1", "", None):
            self.assertFalse(lan_auth.client_is_loopback(h), h)

    def test_token_compare(self):
        self.assertTrue(lan_auth.token_matches("abc", "abc"))
        self.assertFalse(lan_auth.token_matches("abc", "abd"))
        self.assertFalse(lan_auth.token_matches("", "abc"))
        self.assertFalse(lan_auth.token_matches("abc", ""))

    def test_strip_token_keeps_other_params(self):
        self.assertEqual(
            lan_auth.strip_token("http://h:7863/?token=x&a=1&b=2#frag"),
            "http://h:7863/?a=1&b=2#frag")


class Middleware(unittest.TestCase):
    def setUp(self):
        self._saved = (app.LAN_MODE, app.LAN_HOSTS, app.ACCESS_TOKEN)
        app.LAN_MODE = True
        app.LAN_HOSTS = {"192.168.1.6"}
        app.ACCESS_TOKEN = TOKEN

    def tearDown(self):
        app.LAN_MODE, app.LAN_HOSTS, app.ACCESS_TOKEN = self._saved

    def test_lan_without_token_is_401(self):
        r = _request("GET", "/api/voices")
        self.assertEqual(r.status_code, 401)
        self.assertNotIn(TOKEN, r.text)
        self.assertIn("WWW-Authenticate", r.headers)

    def test_wrong_token_is_401(self):
        self.assertEqual(_request("GET", "/api/voices", headers={"X-Yue2-Token": "nope"}).status_code, 401)

    def test_header_token_is_ok(self):
        self.assertEqual(_request("GET", "/api/voices", headers={"X-Yue2-Token": TOKEN}).status_code, 200)

    def test_bearer_token_is_ok(self):
        r = _request("GET", "/api/voices", headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(r.status_code, 200)

    def test_cookie_token_is_ok(self):
        r = _request("GET", "/api/voices", cookies={lan_auth.COOKIE_NAME: TOKEN})
        self.assertEqual(r.status_code, 200)

    def test_query_token_on_api_sets_cookie(self):
        r = _request("GET", f"/api/voices?token={TOKEN}")
        self.assertEqual(r.status_code, 200)
        self.assertIn(lan_auth.COOKIE_NAME, r.headers.get("set-cookie", ""))

    def test_query_token_on_html_redirects_and_sets_cookie(self):
        r = _request("GET", f"/?token={TOKEN}")
        self.assertEqual(r.status_code, 303)
        self.assertNotIn("token=", r.headers.get("location", ""))
        self.assertIn(lan_auth.COOKIE_NAME, r.headers.get("set-cookie", ""))

    def test_loopback_is_exempt(self):
        r = _request("GET", "/api/voices", client=("127.0.0.1", 50000), host="127.0.0.1")
        self.assertEqual(r.status_code, 200)

    def test_no_token_configured_keeps_old_behavior(self):
        app.ACCESS_TOKEN = ""
        self.assertEqual(_request("GET", "/api/voices").status_code, 200)

    def test_system_info_reports_token_without_value(self):
        info = app.system_info()
        self.assertTrue(info["security"]["direct_access_token"])
        self.assertNotIn(TOKEN, str(info))


if __name__ == "__main__":
    unittest.main()
