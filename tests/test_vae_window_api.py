# -*- coding: utf-8 -*-
"""设置页"VAE 显存优化"开关的接口契约：GET/POST /api/vae/window。

2026-10-23 实测背景：这个开关上线后一直是坏的——static/index.html 从早期版本
起就在调 /api/vae/window，但后端从没注册过这对路由，点下去浏览器控制台是
404，页面弹「切换失败：Not Found」（上游 master 同样缺，不是本地同步漏的）。
本测试锁死这些事，防止再被改回去：
  * GET 报的 frames 就是 sidecar 里的 decode_core_frames（开关初始状态来源）；
  * POST 512/1024 改得动文件、会留 .bak-vae-window 备份、并重启引擎一次；
  * 同值再提交不重启引擎、不重复备份；
  * 非法 frames（300 / "abc" / 缺失）一律 400；
  * 没有 sidecar 时 GET/POST 都是 404 且说清原因；
  * 路由真的注册了（这是本次 404 的直接回归点）；
  * 页面还在调这对路由（前后端契约）。

    py312\\python.exe -m unittest discover -s tests -p "test_vae_window_api.py" -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

if "main" not in sys.modules:
    from fastapi import FastAPI

    _stub = types.ModuleType("main")
    _stub.app = FastAPI()
    _stub.settings = types.SimpleNamespace(open_browser=True)
    sys.modules["main"] = _stub

import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

LOCAL_BASE = f"http://127.0.0.1:{app.settings.app_port}"
BACKUP_SUFFIX = ".bak-vae-window"
CFG_REL = "cpp/model/yue2-q4_k_m/sidecars/yue2-vae-config.json"


class VaeWindowApi(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.cfg = root / "cpp" / "model" / "yue2-q4_k_m" / "sidecars" / "yue2-vae-config.json"
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text(
            json.dumps({"decode_core_frames": 1024, "decode_halo_frames": 16,
                        "sample_rate": 48000}),
            encoding="utf-8")
        self._saved = (app.ROOT, app._restart_audiocpp)
        app.ROOT = root
        self.restarts = 0

        def _fake_restart(*a, **k):     # 只数次数，绝不真动引擎进程
            self.restarts += 1

        app._restart_audiocpp = _fake_restart
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def tearDown(self):
        app.ROOT, app._restart_audiocpp = self._saved
        self._tmp.cleanup()

    # ---- GET ---------------------------------------------------------- #
    def test_get_reports_current_window(self):
        j = self.client.get("/api/vae/window").json()
        self.assertEqual(j["frames"], 1024)
        self.assertEqual(j["choices"], [512, 1024])
        self.assertEqual(j["configs"][0]["path"], CFG_REL)

    def test_get_after_manual_patch_reports_512(self):
        # 本机现状：scripts/set_vae_window.py 已把窗口打成 512，GET 必须照实说
        app._vae_window_patch(self.cfg, 512)
        j = self.client.get("/api/vae/window").json()
        self.assertEqual(j["frames"], 512)

    def test_get_404_when_sidecar_missing(self):
        self.cfg.unlink()
        r = self.client.get("/api/vae/window")
        self.assertEqual(r.status_code, 404)
        self.assertIn("一键补齐运行件", r.json()["detail"])

    # ---- POST --------------------------------------------------------- #
    def test_post_switches_to_512_backups_and_restarts(self):
        r = self.client.post("/api/vae/window", json={"frames": 512})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["restarted"])
        self.assertEqual(body["frames"], 512)
        self.assertIn("重启", body["message"])
        self.assertEqual(self.restarts, 1, "改窗口必须重启引擎才生效")
        cfg = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(cfg["decode_core_frames"], 512)
        self.assertEqual(cfg["decode_halo_frames"], 16, "只许改窗口，别动别的键")
        self.assertEqual(cfg["sample_rate"], 48000)
        bak = self.cfg.with_name(self.cfg.name + BACKUP_SUFFIX)
        self.assertTrue(bak.is_file(), "第一次改动前必须留备份")
        self.assertEqual(
            json.loads(bak.read_text(encoding="utf-8"))["decode_core_frames"], 1024)

    def test_post_same_value_is_noop_without_restart(self):
        self.assertEqual(
            self.client.post("/api/vae/window", json={"frames": 512}).status_code, 200)
        bak = self.cfg.with_name(self.cfg.name + BACKUP_SUFFIX)
        first = bak.read_bytes()
        r = self.client.post("/api/vae/window", json={"frames": 512})
        body = r.json()
        self.assertFalse(body["restarted"])
        self.assertEqual(self.restarts, 1, "同值不许再重启引擎")
        self.assertIn("已是 512 帧", body["message"])
        self.assertEqual(bak.read_bytes(), first, "同值不许覆盖备份")

    def test_post_switch_back_to_1024(self):
        self.client.post("/api/vae/window", json={"frames": 512})
        r = self.client.post("/api/vae/window", json={"frames": 1024})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["frames"], 1024)
        self.assertEqual(self.restarts, 2)
        self.assertEqual(
            json.loads(self.cfg.read_text(encoding="utf-8"))["decode_core_frames"], 1024)

    def test_post_accepts_numeric_string(self):
        # 前端发的是数字；容忍 "512" 只是防御，别把它当契约
        r = self.client.post("/api/vae/window", json={"frames": "512"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_post_rejects_unknown_frames(self):
        for bad in (300, 8, 4096, "abc", None, True, -1):
            r = self.client.post("/api/vae/window", json={"frames": bad})
            self.assertEqual(r.status_code, 400, f"frames={bad!r} 必须被拒")
            self.assertIn("512", r.json()["detail"])
        self.assertEqual(self.restarts, 0, "非法值不许碰引擎")
        self.assertEqual(
            json.loads(self.cfg.read_text(encoding="utf-8"))["decode_core_frames"], 1024,
            "非法值不许改文件")

    def test_post_404_when_sidecar_missing(self):
        self.cfg.unlink()
        r = self.client.post("/api/vae/window", json={"frames": 512})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.restarts, 0)


class RouteRegistration(unittest.TestCase):
    """本次 404 的直接回归点：路由必须真的挂在 /api/vae/window 上。"""

    def test_both_methods_registered(self):
        pairs = set()
        for route in app.app.routes:
            for method in (getattr(route, "methods", None) or ()):
                pairs.add((route.path, method))
        self.assertIn(("/api/vae/window", "GET"), pairs)
        self.assertIn(("/api/vae/window", "POST"), pairs)

    def test_frontend_toggle_hits_the_route(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8", errors="ignore")
        self.assertIn('fetch("/api/vae/window")', html,
                      "设置页读开关状态的调用不能改路径")
        self.assertIn('jfetch("/api/vae/window"', html,
                      "切换开关的调用不能改路径")
        self.assertIn('id="vaeWindowToggle"', html)


if __name__ == "__main__":
    unittest.main()
