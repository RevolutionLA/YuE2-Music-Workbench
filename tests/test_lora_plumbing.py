# -*- coding: utf-8 -*-
"""LoRA 接线契约：audio.cpp v0.8.1+ 的 YuE2 AR/NAR 适配器在工作台这边的落点。

这些用例全部跑在临时目录里——`models_lora_set` 会改 server.json 并重启引擎，
不重定向就等于把用户机器上的引擎配置改了（本项目已经因 engine_state 被测试写脏栽过一次）。
不启动引擎、不提交 GPU 任务。
"""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")


def _read_index():
    with io.open(INDEX, encoding="utf-8") as fh:
        return fh.read()


class LoraSandbox(unittest.TestCase):
    """把 LORA_DIR / server.json / engine_state / 引擎重启四件事全接管。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="yue2-lora-"))
        self.lora_dir = self.tmp / "lora"
        self.lora_dir.mkdir(parents=True)
        (self.lora_dir / "ar_lora_demo.bf16.safetensors").write_bytes(b"A")
        (self.lora_dir / "nar_lora_demo.bf16.safetensors").write_bytes(b"N")
        (self.lora_dir / "notes.txt").write_text("x", encoding="utf-8")
        self.state = {}
        self.cfg = {"models": [{"path": "model/yue2-q4_k_m/yue2-3b-q4_k_m.gguf",
                               "session_options": {"yue2.model_gguf": "yue2-3b-q4_k_m.gguf",
                                                   "yue2.vae_gguf": "yue2-vae-f16.gguf"}}]}
        self.restarts = []
        self._saved = {}
        for name, value in (
                ("LORA_DIR", self.lora_dir),
                ("_read_server_json", lambda: json.loads(json.dumps(self.cfg))),
                ("_write_server_json", lambda cfg: (self.cfg.clear(), self.cfg.update(cfg))),
                ("_restart_audiocpp", lambda *a, **k: self.restarts.append(a)),
                ("_engine_state_read", lambda: dict(self.state)),
                ("_engine_state_write", lambda upd: self.state.update(upd))):
            self._saved[name] = getattr(app, name)
            setattr(app, name, value)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(app, name, value)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestLoraListing(LoraSandbox):
    def test_only_safetensors_are_listed(self):
        files = [i["file"] for i in app.available_loras()]
        self.assertEqual(sorted(files),
                         ["ar_lora_demo.bf16.safetensors", "nar_lora_demo.bf16.safetensors"],
                         "目录扫描混进了非适配器文件，或者漏了适配器")

    def test_role_guess_from_name(self):
        roles = {i["file"]: i["role"] for i in app.available_loras()}
        self.assertEqual(roles["ar_lora_demo.bf16.safetensors"], "ar")
        self.assertEqual(roles["nar_lora_demo.bf16.safetensors"], "nar",
                         "NAR 里有 'ar' 子串，必须先判 nar，否则界面会把两段标反")

    def test_endpoint_reports_active_from_config(self):
        # 当前挂着什么，只能从 server.json 推，不看 engine_state 的自述（B-1 的教训）
        app.models_lora_set({"ar": "ar_lora_demo.bf16.safetensors"})
        j = app.models_loras()
        self.assertEqual(j["ar"], "ar_lora_demo.bf16.safetensors")
        self.assertEqual(j["nar"], "")
        self.assertIn("lora", j["dir"].lower(), "报错/提示里要说清目录在哪")


class TestLoraAttach(LoraSandbox):
    def so(self):
        return self.cfg["models"][0]["session_options"]

    def test_attach_writes_session_options_and_restarts(self):
        r = app.models_lora_set({"ar": "ar_lora_demo.bf16.safetensors",
                                 "nar": "nar_lora_demo.bf16.safetensors"})
        so = self.so()
        self.assertTrue(so["yue2.ar_lora"].endswith("ar_lora_demo.bf16.safetensors"))
        self.assertTrue(so["yue2.nar_lora"].endswith("nar_lora_demo.bf16.safetensors"))
        self.assertEqual(so["yue2.ar_lora_scale"], 1.0)
        self.assertEqual(len(self.restarts), 1, "改了 session_options 不重启引擎是不生效的")
        self.assertIn("每一次生成", r["message"], "适配器恒生效这件事必须在回包里说清")
        self.assertEqual(self.state.get("loaded_ar_lora"), "ar_lora_demo.bf16.safetensors")

    def test_clear_removes_the_keys_rather_than_writing_empty(self):
        app.models_lora_set({"ar": "ar_lora_demo.bf16.safetensors"})
        app.models_lora_set({"ar": "", "nar": ""})
        for k in ("yue2.ar_lora", "yue2.nar_lora", "yue2.ar_lora_scale", "yue2.nar_lora_scale"):
            self.assertNotIn(k, self.so(),
                             "卸下要删键：留一个空串会让引擎去找名为 \"\" 的文件")
        self.assertEqual(app._loaded_lora_label(), "未挂载")

    def test_scale_clamped_and_zero_kept(self):
        app.models_lora_set({"ar": "ar_lora_demo.bf16.safetensors", "ar_scale": 9})
        self.assertEqual(self.so()["yue2.ar_lora_scale"], 2.0)
        app.models_lora_set({"ar": "ar_lora_demo.bf16.safetensors", "ar_scale": 0})
        self.assertEqual(self.so()["yue2.ar_lora_scale"], 0.0,
                         "0 是「关掉适配器」的合法值，不能被当成没填而丢掉")

    def test_traversal_and_bogus_names_rejected(self):
        from fastapi import HTTPException
        # 防线是"只取文件名"：带 .. 或绝对路径进不来 LORA_DIR 之外，
        # 剩下的文件名若不是适配器就照样得拒。
        for bad in ("../../../Windows/win.ini", "C:/Windows/win.ini", "/etc/passwd"):
            with self.assertRaises(HTTPException) as cm:
                app.models_lora_set({"ar": bad})
            self.assertEqual(cm.exception.status_code, 400,
                             "%r 消解成文件名后不是 .safetensors，必须 400" % bad)
        with self.assertRaises(HTTPException) as cm:
            app.models_lora_set({"ar": "ar_lora_demo.txt"})
        self.assertEqual(cm.exception.status_code, 400, "非 .safetensors 必须直接拒")
        with self.assertRaises(HTTPException) as cm:
            app.models_lora_set({"ar": "not_here.safetensors"})
        self.assertEqual(cm.exception.status_code, 404)
        self.assertEqual(self.restarts, [], "被拒的写操作不能已经把引擎重启了")

    def test_nested_path_is_neutralised_to_a_name_inside_the_dir(self):
        app.models_lora_set({"ar": "sub/../ar_lora_demo.bf16.safetensors"})
        written = self.so()["yue2.ar_lora"]
        self.assertNotIn("..", written, "写进配置的适配器路径不许带 ..")
        self.assertEqual(Path(written).parent, self.lora_dir.resolve(),
                         "解析后必须仍落在 LORA_DIR 内")

    def test_missing_file_message_tells_where_to_put_it(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            app.models_lora_set({"nar": "x.safetensors"})
        self.assertIn("cpp/lora", str(cm.exception.detail),
                      "用户下载的适配器放错目录是最常见的失败，报错必须说放哪儿")


class TestLoraDisplay(LoraSandbox):
    def test_label_lists_both_segments(self):
        app.models_lora_set({"ar": "ar_lora_demo.bf16.safetensors",
                             "nar": "nar_lora_demo.bf16.safetensors"})
        label = app._loaded_lora_label()
        self.assertIn("AR ar_lora_demo.bf16.safetensors", label)
        self.assertIn("NAR nar_lora_demo.bf16.safetensors", label)

    def test_stale_engine_state_does_not_make_the_label_lie(self):
        # 状态文件里留着旧适配器名，而配置已卸下：必须报"未挂载"，不能报状态文件的自述
        self.state["loaded_ar_lora"] = "ghost.safetensors"
        self.assertEqual(app._loaded_lora_label(), "未挂载")


class TestLoraUiContract(unittest.TestCase):
    def test_controls_live_on_settings_page(self):
        html = _read_index()
        i = html.index('id="tab-settings"')
        j = html.index('<section class="panel-tab"', i + 10)
        block = html[i:j]
        for el in ("loraAr", "loraNar", "loraArScale", "loraNarScale",
                   "loraApply", "loraClear", "loraScan"):
            self.assertIn('id="%s"' % el, block, "⑫ 缺了 LoRA 控件：%s" % el)

    def test_generation_pages_never_touch_lora(self):
        """LoRA 是引擎级配置，不许混进 ②⑤ 的生成表单——那是本项目「页面独立性」的硬规矩。"""
        html = _read_index()
        i = html.index("<script>")
        script = html[i:]
        for marker in ('$("go").onclick', '$("queue").onclick'):
            body = script[script.index(marker):script.index(marker) + 1400]
            self.assertNotIn("lora", body.lower(),
                             "%s 里出现了 lora：提交口又开始读引擎级配置了" % marker)

    def test_about_page_labels_the_line(self):
        html = _read_index()
        self.assertIn('"engines.loaded_lora": "当前 LoRA"', html,
                      "⑬ 会多出一行 engines.loaded_lora，没中文标签就成了裸键名")


if __name__ == "__main__":
    unittest.main()
