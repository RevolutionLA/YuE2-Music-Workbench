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
import os
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


class ActiveConfigResolution(unittest.TestCase):
    """issue #4-1：开关必须改"当前加载模型"那份 sidecar。

    老代码只 glob `*/sidecars/yue2-vae-config.json`，只认"每个量化档一个子目录"的布局。
    扁平布局（权重摊在 `cpp/model/` 下、sidecar 在 `cpp/model/sidecars/`）一份都匹配不到
    → ⑫ 点 512 改的是没在用的那份，页面却显示 512；预检也按别的量化档估算。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.root = root
        # 扁平布局：当前加载的 q8 权重直接躺在 cpp/model/，sidecar 在同级 sidecars/
        self.flat = root / "cpp" / "model" / "sidecars" / "yue2-vae-config.json"
        self.flat.parent.mkdir(parents=True)
        self.flat.write_text(json.dumps({"decode_core_frames": 1024}), encoding="utf-8")
        # 分子目录布局：另一个量化档，故意设成 512，用来证明"没被改"
        self.sub = (root / "cpp" / "model" / "yue2-q4_k_m" / "sidecars"
                    / "yue2-vae-config.json")
        self.sub.parent.mkdir(parents=True)
        self.sub.write_text(json.dumps({"decode_core_frames": 512}), encoding="utf-8")
        # server.json 指向扁平那份（path 相对 cpp/）
        self._write_server("model/yue2-3b-q8_0.gguf", "yue2-3b-q8_0.gguf")
        self._saved = (app.ROOT, app._restart_audiocpp)
        app.ROOT = root
        self.restarts = 0
        app._restart_audiocpp = lambda *a, **k: setattr(self, "restarts", self.restarts + 1)
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def tearDown(self):
        app.ROOT, app._restart_audiocpp = self._saved
        self._tmp.cleanup()

    def _write_server(self, path: str, model_gguf: str | None) -> None:
        entry = {"id": "yue2", "family": "yue2", "path": path, "task": "gen"}
        if model_gguf is not None:
            entry["session_options"] = {"yue2.model_gguf": model_gguf}
        cfg = {"backend": "cpu", "busy_timeout_ms": 0, "models": [entry]}
        p = self.root / "cpp" / "server.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg), encoding="utf-8")

    # ---- 发现 ---------------------------------------------------------- #
    def test_flat_layout_sidecar_is_discovered(self):
        """扁平布局那份必须出现在清单里——老 glob 会把它整个漏掉。"""
        j = self.client.get("/api/vae/window").json()
        paths = [c["path"] for c in j["configs"]]
        self.assertIn("cpp/model/sidecars/yue2-vae-config.json", paths)
        self.assertIn("cpp/model/yue2-q4_k_m/sidecars/yue2-vae-config.json", paths)

    def test_get_reports_the_active_one_not_the_first_sorted(self):
        j = self.client.get("/api/vae/window").json()
        self.assertEqual(j["active"], "cpp/model/sidecars/yue2-vae-config.json")
        self.assertEqual(j["frames"], 1024, "报的必须是在用那份（q8=1024），不是排序第一的 q4=512")
        self.assertEqual(j["model"], "yue2-3b-q8_0.gguf")
        actives = [c for c in j["configs"] if c["active"]]
        self.assertEqual([c["path"] for c in actives],
                         ["cpp/model/sidecars/yue2-vae-config.json"])
        self.assertEqual(j["configs"][0]["active"], True, "在用那份要排第一")
        self.assertEqual(j["mismatch"], ["cpp/model/yue2-q4_k_m/sidecars/yue2-vae-config.json"],
                         "多档不一致时要挑明，别让用户以为都一样")

    def test_session_options_only_falls_back_to_path_basename(self):
        """session_options 缺失的老配置也要能定位（退回 path 的 basename）。"""
        self._write_server("model/yue2-3b-q8_0.gguf", None)
        j = self.client.get("/api/vae/window").json()
        self.assertEqual(j["active"], "cpp/model/sidecars/yue2-vae-config.json")

    def test_unresolvable_model_says_so_instead_of_guessing(self):
        (self.root / "cpp" / "server.json").unlink()
        j = self.client.get("/api/vae/window").json()
        self.assertIsNone(j["active"])
        self.assertIn("定位不到", j["note"], "定位不到就要说定位不到，不许拿别的档顶替")

    # ---- 写入 ---------------------------------------------------------- #
    def test_post_only_touches_the_active_config(self):
        r = self.client.post("/api/vae/window", json={"frames": 1024})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["restarted"], "同值不该重启引擎")

        r = self.client.post("/api/vae/window", json={"frames": 512})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["restarted"])
        self.assertEqual(self.restarts, 1)
        self.assertEqual([c["path"] for c in body["changed"]],
                         ["cpp/model/sidecars/yue2-vae-config.json"],
                         "默认只改在用那份，别动别的量化档")
        self.assertEqual(json.loads(self.flat.read_text(encoding="utf-8"))["decode_core_frames"],
                         512)
        self.assertEqual(json.loads(self.sub.read_text(encoding="utf-8"))["decode_core_frames"],
                         512, "另一档原本就是 512，不该被碰（这里顺势验证没被改成别的）")

    def test_post_does_not_touch_other_quant_sidecar(self):
        """核心回归：q8 设 512、q4 设 1024 后，GET 必须报 512 且标 active。"""
        r = self.client.post("/api/vae/window", json={"frames": 512})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(json.loads(self.sub.read_text(encoding="utf-8"))["decode_core_frames"],
                         512, "q4 原来就是 512；改 q8 不该顺带动它")
        # 把 q4 设回 1024（模拟用户两份故意设不同），再用 all=true 统一
        self.sub.write_text(json.dumps({"decode_core_frames": 1024}), encoding="utf-8")
        j = self.client.get("/api/vae/window").json()
        self.assertEqual(j["frames"], 512, "在用那份是 512")
        self.assertEqual(j["mismatch"], ["cpp/model/yue2-q4_k_m/sidecars/yue2-vae-config.json"])

    def test_post_all_true_still_batches(self):
        self.sub.write_text(json.dumps({"decode_core_frames": 1024}), encoding="utf-8")
        r = self.client.post("/api/vae/window", json={"frames": 512, "all": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["changed"]), 2, "all=true 时两份都改")
        self.assertEqual(json.loads(self.sub.read_text(encoding="utf-8"))["decode_core_frames"],
                         512)

    def test_post_without_resolvable_active_changes_everything_and_says_so(self):
        (self.root / "cpp" / "server.json").unlink()
        self.sub.write_text(json.dumps({"decode_core_frames": 1024}), encoding="utf-8")
        r = self.client.post("/api/vae/window", json={"frames": 512})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("定位不到", r.json()["message"], "全改就必须说明是在全改")
        self.assertEqual(len(r.json()["changed"]), 2)

    def test_preflight_reads_the_active_window(self):
        """预检必须按在用那份估：1024 与 512 的结论差一个量级（issue #4-1 第二条影响）。"""
        saved = (app.backend_mode, app._vulkan_heap_budget_gb, app._cpu_fallback_allowed)
        app.backend_mode = lambda: "vulkan"
        app._vulkan_heap_budget_gb = lambda: 7.26
        app._cpu_fallback_allowed = lambda: False
        try:
            long_song = {"lyrics": "字" * 600}
            big = app._preflight_memory_estimate(long_song)
            self.assertEqual(big["frames"], 7200)
            self.assertFalse(big["ok"], f"1024 窗口下 7200 帧必超预算：{big}")
            # 把**在用那份**改成 512：分块解码，缓冲不随帧数涨 → 放行
            self.flat.write_text(json.dumps({"decode_core_frames": 512}), encoding="utf-8")
            small = app._preflight_memory_estimate(long_song)
            self.assertTrue(small["ok"], f"512 窗口下同一首歌应放行：{small}")
            self.assertLess(small["peak_gb"], big["peak_gb"])
        finally:
            app.backend_mode, app._vulkan_heap_budget_gb, app._cpu_fallback_allowed = saved

    def test_preflight_ignores_other_quants_window(self):
        """别的档设成 512 不能把在用的 1024 估小——那是会 OOM 的那种错。"""
        saved = (app.backend_mode, app._vulkan_heap_budget_gb, app._cpu_fallback_allowed)
        app.backend_mode = lambda: "vulkan"
        app._vulkan_heap_budget_gb = lambda: 7.26
        app._cpu_fallback_allowed = lambda: False
        try:
            self.sub.write_text(json.dumps({"decode_core_frames": 512}), encoding="utf-8")
            self.assertEqual(json.loads(self.flat.read_text(encoding="utf-8"))["decode_core_frames"],
                             1024)
            out = app._preflight_memory_estimate({"lyrics": "字" * 600})
            self.assertFalse(out["ok"], "在用那份是 1024，就该按 1024 拦下")
        finally:
            app.backend_mode, app._vulkan_heap_budget_gb, app._cpu_fallback_allowed = saved


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
