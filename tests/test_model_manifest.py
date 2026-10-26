"""模型校验/修复清单必须跟随当前 server.json，而不是写死 q4。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402


def _cfg(rel: str, main: str, vae: str = "yue2-vae-f16.gguf") -> dict:
    return {"models": [{"path": rel,
                        "session_options": {"yue2.model_gguf": main,
                                            "yue2.vae_gguf": vae}}]}


class ActiveManifest(unittest.TestCase):
    def test_q8_follows_current_config(self):
        cfg = _cfg("model/yue2-3b-q8_0.gguf", "yue2-3b-q8_0.gguf")
        conf = {"vae": "yue2-vae-f16.gguf", "vae_from": "同目录",
                "main": "yue2-3b-q8_0.gguf", "main_exists": True}
        with mock.patch.object(app, "_read_server_json", return_value=cfg), \
             mock.patch.object(app, "_engine_config_report", return_value=conf):
            items, source = app._active_model_manifest()
        self.assertEqual(source, "active")
        repo, remote, rel, min_size, is_gguf = items[0]
        self.assertIn("q8_0", remote)
        self.assertTrue(rel.endswith("yue2-3b-q8_0.gguf"))
        self.assertTrue(is_gguf and min_size >= 2_000_000_000)
        self.assertTrue(any("yue2-vae-f16.gguf" in i[2] for i in items))
        self.assertTrue(any(i[1] == "sidecars/yue2-model-config.json" for i in items))
        self.assertTrue(any(i[0] == "m-a-p/SheetSage2" for i in items))

    def test_q4_follows_current_config(self):
        cfg = _cfg("model/yue2-q4_k_m/yue2-3b-q4_k_m.gguf",
                   "yue2-3b-q4_k_m.gguf")
        conf = {"vae": "yue2-vae-f16.gguf", "vae_from": "同目录",
                "main": "yue2-3b-q4_k_m.gguf", "main_exists": True}
        with mock.patch.object(app, "_read_server_json", return_value=cfg), \
             mock.patch.object(app, "_engine_config_report", return_value=conf):
            items, source = app._active_model_manifest()
        self.assertEqual(source, "active")
        self.assertEqual(items[0][1], "yue2-3b-q4_k_m.gguf")
        self.assertIn("yue2-q4_k_m", items[0][2])

    def test_unknown_quant_has_no_repair_source(self):
        cfg = _cfg("model/yue2-q6_k.gguf", "yue2-q6_k.gguf")
        conf = {"vae": "yue2-vae-f16.gguf", "vae_from": "同目录",
                "main": "yue2-q6_k.gguf", "main_exists": True}
        with mock.patch.object(app, "_read_server_json", return_value=cfg), \
             mock.patch.object(app, "_engine_config_report", return_value=conf):
            items, _ = app._active_model_manifest()
        self.assertEqual(items[0][0], "")
        self.assertEqual(items[0][1], "")

    def test_fallback_to_q4_without_config(self):
        with mock.patch.object(app, "_read_server_json",
                               side_effect=FileNotFoundError("no server.json")):
            items, source = app._active_model_manifest()
        self.assertEqual(source, "fallback-q4")
        self.assertEqual(items, list(app._MODEL_MANIFEST_MAIN))

    def test_real_config_matches_engine_report(self):
        if not app.SERVER_JSON.is_file():
            self.skipTest("本机没有 server.json")
        conf = app._engine_config_report()
        if not conf.get("main") or not conf.get("main_exists"):
            self.skipTest("当前配置没有可校验的主模型")
        items, source = app._active_model_manifest()
        self.assertEqual(source, "active")
        self.assertEqual(items[0][2], conf["main_path"])


class VerifyRoute(unittest.TestCase):
    def test_verify_reports_source(self):
        items = [("repo", "remote.gguf", "checkpoints/model.safetensors", 1, False)]
        with mock.patch.object(app, "_active_model_manifest", return_value=(items, "active")):
            j = app.models_verify()
        self.assertEqual(j["source"], "active")
        self.assertEqual(len(j["files"]), 1)


if __name__ == "__main__":
    unittest.main()
