"""后端"能力解析"：页面上的「GPU」必须落到这份引擎构建真能跑的后端。

2026-10-21 实测背景：本机装的是 `backends: cpu,vulkan` 的构建（Iris Xe），而页面
「切GPU」原来硬编码提交 `cuda` —— 按下去引擎会因没有 CUDA 后端直接起不来，界面却
还会弹一句"已切回 GPU（CUDA）"。所以：
  * 后端按 `audiocpp_server.exe --version` 里的 `backends:` 解析；
  * 前端按 `/api/backend/mode` 返回的 `available` 决定「切GPU」提交 cuda 还是 vulkan。

    py312\\python.exe -m unittest discover -s tests -p "test_backend_mode.py" -v
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
IGPU_BUILD = ["cpu", "vulkan"]      # 本机：Vulkan 构建
CUDA_BUILD = ["cpu", "cuda"]        # 典型 N 卡机器
CPU_ONLY = ["cpu"]                  # 纯 CPU 包


class Base(unittest.TestCase):
    available = IGPU_BUILD

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = (app._ENGINE_STATE, app._restart_audiocpp, app.engine_backends)
        app._ENGINE_STATE = Path(self._tmp.name) / "engine_state.json"
        app._restart_audiocpp = lambda *a, **k: None   # 只验解析，绝不真动进程
        app.engine_backends = lambda: list(self.available)
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def tearDown(self) -> None:
        app._ENGINE_STATE, app._restart_audiocpp, app.engine_backends = self._saved
        self._tmp.cleanup()

    def _state(self, backend: str) -> None:
        app._ENGINE_STATE.parent.mkdir(parents=True, exist_ok=True)
        app._ENGINE_STATE.write_text(json.dumps({"backend": backend}), encoding="utf-8")


class ResolveOnIgpu(Base):
    def test_gpu_means_vulkan_when_the_build_has_no_cuda(self):
        # 把 resolve_backend 里的 vulkan 分支删掉 → 这条红（会落到 cpu 或原样 cuda）
        mode, note = app.resolve_backend("cuda")
        self.assertEqual(mode, "vulkan")
        self.assertIn("没有 CUDA", note)
        self.assertIn("vulkan", note)

    def test_explicit_vulkan_stays_vulkan(self):
        self.assertEqual(app.resolve_backend("vulkan"), ("vulkan", ""))

    def test_cpu_is_always_available(self):
        self.assertEqual(app.resolve_backend("cpu"), ("cpu", ""))

    def test_setter_turns_gpu_click_into_vulkan(self):
        self._state("cpu")
        r = self.client.post("/api/backend/mode", json={"mode": "cuda"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["mode"], "vulkan", "点了「切GPU」就该真的落在 GPU 上")
        self.assertIn("Vulkan", body["message"])
        self.assertEqual(body["available"], IGPU_BUILD)
        self.assertEqual(app.backend_mode(), "vulkan")

    def test_getter_reports_available_backends_for_the_ui(self):
        self._state("vulkan")
        j = self.client.get("/api/backend/mode").json()
        self.assertEqual(j["mode"], "vulkan")
        self.assertEqual(j["available"], IGPU_BUILD, "前端靠它决定「切GPU」提交哪个后端")


class ResolveOnCudaMachine(Base):
    available = CUDA_BUILD

    def test_gpu_means_cuda_when_cuda_exists(self):
        self.assertEqual(app.resolve_backend("cuda"), ("cuda", ""))

    def test_vulkan_on_a_cuda_only_build_falls_back_to_cpu_with_a_reason(self):
        mode, note = app.resolve_backend("vulkan")
        self.assertEqual(mode, "cpu")
        self.assertIn("没有 Vulkan", note)


class ResolveOnCpuOnly(Base):
    available = CPU_ONLY

    def test_cuda_falls_back_to_cpu_and_says_so(self):
        mode, note = app.resolve_backend("cuda")
        self.assertEqual(mode, "cpu")
        self.assertIn("只能用 CPU", note)


class InvalidRequest(Base):
    def test_unknown_mode_lists_what_this_build_supports(self):
        r = self.client.post("/api/backend/mode", json={"mode": "metal"})
        self.assertEqual(r.status_code, 400)
        detail = str(r.json()["detail"])
        self.assertIn("vulkan", detail)
        self.assertIn("cpu", detail)


class BackendDefaults(unittest.TestCase):
    """缺 state 时的缺省必须是 server.json 的那一份，最后才是 cpu（评审 B17）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = (app._ENGINE_STATE, app.SERVER_JSON)
        app._ENGINE_STATE = Path(self._tmp.name) / "engine_state.json"
        app.SERVER_JSON = Path(self._tmp.name) / "server.json"

    def tearDown(self) -> None:
        app._ENGINE_STATE, app.SERVER_JSON = self._saved
        self._tmp.cleanup()

    def test_missing_state_uses_server_json(self):
        app.SERVER_JSON.write_text(json.dumps({"backend": "vulkan"}), encoding="utf-8")
        self.assertEqual(app.backend_mode(), "vulkan")

    def test_missing_everything_defaults_to_cpu(self):
        # 把 return ... else "cpu" 改回 "cuda"，这条立刻红
        self.assertEqual(app.backend_mode(), "cpu")

    def test_state_wins_over_config(self):
        app._ENGINE_STATE.write_text(json.dumps({"backend": "cpu"}), encoding="utf-8")
        app.SERVER_JSON.write_text(json.dumps({"backend": "vulkan"}), encoding="utf-8")
        self.assertEqual(app.backend_mode(), "cpu")


class BackendProbe(unittest.TestCase):
    """探测失败不能被永久缓存成"只有 cpu"（评审 T2）。"""

    def setUp(self) -> None:
        self._saved = (app._ENGINE_BACKENDS_CACHE, app.subprocess.run)

    def tearDown(self) -> None:
        app._ENGINE_BACKENDS_CACHE, app.subprocess.run = self._saved

    def test_failure_is_not_cached(self):
        app._ENGINE_BACKENDS_CACHE = None

        class _Boom:
            returncode = 1
            stdout = ""

        app.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
        self.assertEqual(app.engine_backends(), [])
        self.assertIsNone(app._ENGINE_BACKENDS_CACHE, "失败不许落缓存")

        class _Ok:
            returncode = 0
            stdout = "audio.cpp 0.9.2\nbackends: cpu,vulkan\n"

        app.subprocess.run = lambda *a, **k: _Ok()
        self.assertEqual(app.engine_backends(), ["cpu", "vulkan"])

    def test_unknown_backends_pass_the_request_through(self):
        app.engine_backends = lambda: []          # type: ignore[assignment]
        try:
            self.assertEqual(app.resolve_backend("cuda"), ("cuda", ""))
        finally:
            pass


class CpuFallbackGate(unittest.TestCase):
    """核显机器默认不许"分配失败就偷偷整首重算成 CPU"（用户侧开关）。"""

    def setUp(self):
        self._saved = os.environ.get("YUE2_DISABLE_CPU_FALLBACK")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("YUE2_DISABLE_CPU_FALLBACK", None)
        else:
            os.environ["YUE2_DISABLE_CPU_FALLBACK"] = self._saved

    def test_default_allows(self):
        os.environ.pop("YUE2_DISABLE_CPU_FALLBACK", None)
        self.assertTrue(app._cpu_fallback_allowed())

    def test_flag_disables(self):
        for v in ("1", "true", "YES", "on"):
            os.environ["YUE2_DISABLE_CPU_FALLBACK"] = v
            self.assertFalse(app._cpu_fallback_allowed(), v)

    def test_both_fallback_branches_consult_the_gate(self):
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(src.count("_cpu_fallback_allowed()"), 3,
                                "预检分支与错误分支都必须过这道闸（含函数定义一行）")
        self.assertIn("已禁用 CPU 回退", src, "失败时必须把原因说给用户看")


class EngineRestartOnAlloc(unittest.TestCase):
    """分配失败要重启引擎再试一次，但只一次（防死循环）。"""

    def setUp(self):
        self._saved = app.backend_mode
        app.backend_mode = lambda: "vulkan"

    def tearDown(self):
        app.backend_mode = self._saved

    def test_alloc_failure_triggers_one_restart(self):
        self.assertTrue(app._should_restart_engine_after_alloc(
            500, "failed to allocate Vulkan0 buffer of size 541200064", False))
        self.assertFalse(app._should_restart_engine_after_alloc(
            500, "failed to allocate Vulkan0 buffer", True), "只许重启一次")

    def test_non_alloc_errors_do_not_restart(self):
        self.assertFalse(app._should_restart_engine_after_alloc(400, "style is required", False))
        self.assertFalse(app._should_restart_engine_after_alloc(200, "", False))

    def test_cpu_backend_does_not_get_restarted(self):
        app.backend_mode = lambda: "cpu"
        self.assertFalse(app._should_restart_engine_after_alloc(
            500, "out of memory", False))


class SeedHeader(unittest.TestCase):
    """X-Seed 的大小写契约（评审 B13）：httpx 头转 dict 后键是小写。"""

    def test_lowercase_header_is_read(self):
        self.assertEqual(app._seed_from_headers({"x-seed": "777"}, {}), "777")

    def test_mixed_case_header_still_works(self):
        self.assertEqual(app._seed_from_headers({"X-Seed": "9"}, {}), "9")

    def test_falls_back_to_payload(self):
        self.assertEqual(app._seed_from_headers({}, {"seed": 5}), 5)


class FrontendUsesAvailable(unittest.TestCase):
    """页面不许再把「切GPU」写死成 cuda。"""

    def test_switch_target_comes_from_the_api(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8", errors="ignore")
        self.assertIn('engineMode === "cpu" ? engineGpu : "cpu"', html,
                      "「切GPU」的目标必须来自 /api/backend/mode 的 available")
        self.assertNotIn('engineMode === "cpu" ? "cuda" : "cpu"', html,
                         "硬编码 cuda 会在核显机器上让引擎起不来")
        self.assertIn("engineGpu = avail.includes", html,
                      "没从 available 推导引擎的 GPU 后端")

    def test_vulkan_mode_has_its_own_label(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8", errors="ignore")
        self.assertIn("Vulkan（集显，共享内存）", html,
                      "vulkan 模式不能显示成「显存 状态未知」，那是 CUDA 口径的文案")


if __name__ == "__main__":
    unittest.main()
