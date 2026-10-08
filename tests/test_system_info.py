"""v2.0 §四-⑩ / §六 P1：系统信息端点的回归锁定（规划 §9.1 表第 5 行）。

覆盖：① 端口从 ports.json 读，与 src/ports.py 一致；② 不返回任何密钥。
外加：显存/磁盘查不到时不许编数、只读（探活绝不拉起进程）、路径字段不越界。

沿用 tests/test_review_fixes.py 的 Sandbox 基线；nvidia-smi 换成替身——
真探测一次要起子进程，且换机器（无 N 卡 / 无驱动）结果就不同。
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import ports  # noqa: E402
from test_review_fixes import LOCAL_BASE, Sandbox, app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FAKE_KEY = "sk-TEST-勿出现在响应里-9f3c2b1a"


class SystemInfoBase(Sandbox):
    def setUp(self) -> None:
        super().setUp()
        self._saved_sys = (app._gpu_total_mb, app._gpu_free_mb, ports._cache)
        app._gpu_total_mb = lambda: 4096
        app._gpu_free_mb = lambda: 1024
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def tearDown(self) -> None:
        app._gpu_total_mb, app._gpu_free_mb, ports._cache = self._saved_sys
        super().tearDown()

    def _get(self):
        r = self.client.get("/api/system/info")
        self.assertEqual(r.status_code, 200, r.text)
        return r


class TestSystemInfoPorts(SystemInfoBase):
    def test_ports_come_from_ports_module(self):
        """与 src/ports.py 同一口径：端点不许自己写死 7863/3081/8080。"""
        ports._cache = {"gateway": 7777, "dsh": 8888, "audiocpp": 9999}
        j = self._get().json()
        self.assertEqual(j["ports"]["gateway"], 7777)
        self.assertEqual(j["ports"]["dsh"], 8888)
        self.assertEqual(j["ports"]["audiocpp"], 9999)

    def test_ports_match_ports_json_file(self):
        ports._cache = None          # 强制重读真源文件
        j = self._get().json()
        raw = json.loads((ROOT / "ports.json").read_text(encoding="utf-8"))
        for k in ("gateway", "dsh", "audiocpp"):
            self.assertEqual(j["ports"][k], raw[k], f"{k} 必须等于 ports.json 里的值")
        self.assertEqual(j["ports"]["gateway"], app.settings.app_port,
                         "网关自报端口必须与实际监听端口同源")

    def test_ports_file_flag_reports_reality(self):
        j = self._get().json()
        self.assertTrue(j["ports"]["ports_file_exists"])
        self.assertEqual(Path(j["ports"]["ports_file"]), ports.PORTS_FILE)


class TestSystemInfoSecrets(SystemInfoBase):
    def test_api_key_never_leaves_the_gateway(self):
        os.environ["DEEPSEEK_API_KEY"] = FAKE_KEY
        try:
            r = self._get()
            self.assertNotIn(FAKE_KEY, r.text, "系统信息里出现密钥=把密钥发给每一次页面轮询")
            self.assertIn("key_configured", json.dumps(r.json(), ensure_ascii=False))
            self.assertTrue(r.json()["engines"]["ai_workbench"]["key_configured"])
        finally:
            del os.environ["DEEPSEEK_API_KEY"]

    def test_no_secret_shaped_field_names(self):
        j = self._get().json()
        blob = json.dumps(j, ensure_ascii=False).lower()
        for needle in ("api_key", "apikey", "authorization", "password", "token=", "secret"):
            self.assertNotIn(needle, blob, f"响应里出现了敏感字段样式：{needle}")

    def test_ai_block_is_booleans_plus_model_name(self):
        j = self._get().json()["engines"]["ai_workbench"]
        self.assertIsInstance(j["ready"], bool)
        self.assertIsInstance(j["key_configured"], bool)
        self.assertIsInstance(j["runner_exists"], bool)
        self.assertIsInstance(j["dsh_listening"], bool)
        self.assertEqual(j["model"], os.environ.get("AI_MODEL", "deepseek-chat"))

    def test_probe_is_read_only_and_does_not_spawn(self):
        """只读闸门：/api/ai/web 那套"没起来就拉起 dsh"的动作绝不能被系统信息触发。"""
        import ai_router
        self._saved_web = ai_router.ai_web
        called: list[str] = []
        ai_router.ai_web = lambda *a, **k: called.append("ai_web")
        try:
            self._get()
        finally:
            ai_router.ai_web = self._saved_web
        self.assertEqual(called, [])


class TestSystemInfoResources(SystemInfoBase):
    def test_gpu_numbers_are_derived_not_invented(self):
        j = self._get().json()["gpu"]
        self.assertEqual(j["total_mb"], 4096)
        self.assertEqual(j["free_mb"], 1024)
        self.assertEqual(j["used_mb"], 3072)
        self.assertFalse(j["query_failed"])

    def test_missing_nvidia_smi_is_declared_not_faked(self):
        app._gpu_total_mb = lambda: None
        app._gpu_free_mb = lambda: None
        j = self._get().json()["gpu"]
        self.assertIsNone(j["total_mb"])
        self.assertIsNone(j["used_mb"], "查不到就回 null，不许拿 0 冒充显存已用 0MB")
        self.assertTrue(j["query_failed"])

    def test_disk_and_paths_present(self):
        j = self._get().json()
        self.assertGreater(j["disk"]["output_free_mb"], 0)
        self.assertEqual(Path(j["paths"]["output_dir"]), Path(app.OUTPUT_DIR))
        self.assertEqual(Path(j["paths"]["scores_dir"]), Path(app.SCORES_DIR))
        self.assertEqual(Path(j["paths"]["rvc_weights_dir"]), Path(app.RVC_MODELS_DIR))
        # ⑩「复制日志目录」按钮只有这一个数据源，缺了它按钮就永远复制不出东西
        self.assertEqual(Path(j["paths"]["log_dir"]), Path(app.ROOT) / "runtime" / "data" / "logs")

    def test_readiness_flags_are_booleans(self):
        eng = self._get().json()["engines"]
        for k in ("audiocpp_alive", "vocal_separation_ready", "sheetsage2_ready"):
            self.assertIsInstance(eng[k], bool, k)

    def test_version_comes_from_changelog_top_entry(self):
        import re
        j = self._get().json()
        self.assertRegex(j["version"], r"^v\d+\.\d+\.\d+")
        # 真源缺失时回 unknown，而不是抛 500 把设置页打白
        saved_root = app.ROOT
        try:
            app.ROOT = self.tmp
            self.assertEqual(self.client.get("/api/system/info").json()["version"], "unknown")
        finally:
            app.ROOT = saved_root


class TestSystemInfoLabels(SystemInfoBase):
    """⑩ 把响应整棵树铺成行：后端加字段而前端标签表没跟上，页面就长出裸英文键。"""

    @staticmethod
    def _label_keys():
        import re
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        block = re.search(r"const SYS_LABEL = \{(.*?)\n\};", html, re.S)
        assert block, "SYS_LABEL 表被改名或删掉了"
        return set(re.findall(r'["\']?([A-Za-z0-9_.]+)["\']?\s*:', block.group(1)))

    @staticmethod
    def _paths(obj, prefix=""):
        out = []
        for k, v in (obj or {}).items():
            if v is None or v == "":
                continue
            if isinstance(v, dict):
                out += TestSystemInfoLabels._paths(v, prefix + k + ".")
            else:
                out.append(prefix + k)
        return out

    def test_every_field_has_a_chinese_label(self):
        keys = self._label_keys()
        missing = [p for p in self._paths(self._get().json())
                   if p not in keys and p.split(".")[-1] not in keys]
        self.assertEqual(missing, [], f"这些字段会在⑩显示成裸英文键：{missing}")


if __name__ == "__main__":
    unittest.main()
