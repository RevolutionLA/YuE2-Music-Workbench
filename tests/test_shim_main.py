"""开源兼容网关 `main.py` 的回归用例。

`main.py` 是本机部署时替代闭源 `main.cp312-win_amd64.pyd` 的兼容层（它做四件事：
提供 app/settings、`/api/health`、`/api/music/generate`、导入时拉起引擎）。这一层是
新写的代码，所以按项目规矩给它配"能失败"的检查：每条都注明**把它改坏会立刻红**的那一处。

只碰纯函数与 400 分支，不发真实生成请求，因此引擎正在跑歌时可以并行执行：

    py312\\python.exe -m unittest discover -s tests -p "test_shim_main.py" -v
"""
from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

# 导入 main 会拉起 audio.cpp 引擎（与内核同行为），测试里绝不允许
os.environ["YUE2_NO_ENGINE_AUTOSTART"] = "1"

# 按文件路径独立加载，不走 `import main`：test_review_fixes.py 会往 sys.modules["main"]
# 注入一个 FastAPI() 替身（那是为了在没有闭源 pyd 的情况下也能测 app.py），
# 全量 discover 时那个替身会污染本文件——实测症状是 main._pick_payload 之类全部
# AttributeError，而 400 断言收到 403（app.py 的本机接口守卫挂在替身 app 上）。
import importlib.util as _ilu  # noqa: E402

# 干净 clone（没有 cpp/model_specs/yue2.json）上 main.py 会在 import 期拒绝加载，
# 这是**有意的**设计（缺参数表就别假装能工作）。本文件改为显式跳过，让 clone 用户
# 仍能跑其余用例（三方评审 B7）。
if not (ROOT / "cpp" / "model_specs" / "yue2.json").is_file():
    import unittest as _ut

    raise _ut.SkipTest("缺 cpp/model_specs/yue2.json（引擎未部署）；main.py 按设计拒绝加载")

_spec = _ilu.spec_from_file_location("yue2_shim_under_test", ROOT / "main.py")
main = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(main)

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# 一段最小的合法 WAV 头（真 WAV 由引擎出，这里只验"认字节头不认字段名"这条判据）
_WAV = (b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
        + b"\x01\x00\x01\x00\x22\x56\x00\x00\x44\xac\x00\x00\x02\x00\x10\x00"
        + b"data" + (8).to_bytes(4, "little") + b"\x00" * 8)
_WAV_B64 = base64.b64encode(_WAV).decode()


class SpecSource(unittest.TestCase):
    """白名单必须来自引擎自带的参数表，不能是手抄常量。"""

    def test_options_come_from_the_engine_spec(self):
        # 把 _SPEC_SOURCE 改成硬编码列表 → 这条红
        self.assertTrue(main._ENGINE_OPTIONS, "参数表读出来是空的，网关无从筛选字段")
        self.assertTrue(main._SPEC_SOURCE.endswith("yue2.json"),
                        f"参数表来源不是引擎包自带的 spec：{main._SPEC_SOURCE}")
        for k in ("style", "lyrics", "cot", "seed", "num_inference_steps",
                  "guidance_scale", "abc", "stop_after"):
            self.assertIn(k, main._ENGINE_OPTIONS, f"引擎声明过的 {k} 不在白名单里")

    def test_alias_table_is_the_only_translation_place(self):
        self.assertEqual(main._FIELD_ALIAS.get("cfg_scale"), "guidance_scale",
                         "工作台的 cfg_scale 必须翻译成引擎的 guidance_scale，否则步长之外"
                         "这一维永远是引擎默认值")


class PickPayload(unittest.TestCase):
    def test_unknown_keys_are_dropped_and_named(self):
        opts, dropped = main._pick_payload({
            "style": "English, pop", "cot": "off", "seed": 7,
            "task_name": "多余", "count": 3, "model": "yue2",
            "nonsense_field": 1,
        })
        self.assertNotIn("task_name", opts)
        self.assertNotIn("count", opts)
        self.assertNotIn("nonsense_field", opts)
        # 丢掉的字段必须被点名，方便发现"界面填了但没送进去"
        self.assertIn("task_name", dropped)
        self.assertIn("nonsense_field", dropped)
        self.assertEqual(opts.get("seed"), 7)

    def test_empty_values_are_omitted_not_sent_as_blanks(self):
        # 把 `if v is None or v == ""` 改成只判 None → abc="" 会被当"带谱"送进引擎 → 这条红
        opts, _ = main._pick_payload({"style": "s", "abc": "", "lyrics": ""})
        self.assertNotIn("abc", opts, "空谱送进引擎会被判成 external ABC")

    def test_cfg_scale_renamed_on_the_way_out(self):
        opts, _ = main._pick_payload({"style": "s", "cfg_scale": 1.0})
        self.assertEqual(opts.get("guidance_scale"), 1.0)
        self.assertNotIn("cfg_scale", opts)


class EngineBodyShape(unittest.TestCase):
    def test_options_appear_at_both_levels(self):
        """实测：只写 request.style 时引擎回「Yue2 requires non-empty style」。"""
        body = main._engine_body("yue2", {"style": "English, pop", "cot": "off"})
        self.assertEqual(body["model"], "yue2")
        self.assertEqual(body["request"]["task"], "gen")
        self.assertEqual(body["request"]["style"], "English, pop")
        # 把 options 那一层删掉 → 这条红（这正是 00:34 踩过的坑）
        self.assertEqual(body["request"]["options"]["style"], "English, pop")

    def test_mutating_body_does_not_alias_opts(self):
        opts = {"style": "a"}
        body = main._engine_body("yue2", opts)
        body["request"]["options"]["style"] = "b"
        self.assertEqual(opts["style"], "a", "options 必须是副本，不能与调用方共享同一 dict")


class AudioFromResponse(unittest.TestCase):
    def test_top_level_audio_key_is_the_real_shape_on_this_engine(self):
        # 本机 v0.9.2 实测出片走顶层 audio（2026-10-21 00:43，982,875 字节那份）
        raw, meta = main._audio_from_response(
            {"audio": _WAV_B64, "sample_rate": 48000, "channels": 2})
        self.assertEqual(raw[:4], b"RIFF")
        self.assertEqual(meta["sample_rate"], 48000)

    def test_real_probe_response_from_this_machine_decodes(self):
        """回放本机引擎真回过的那一份（982,875 字节的 JSON，3.84 秒 CPU 出片）。

        这条不用我造的假 WAV：deploy/probe_resp.json 是 2026-10-21 00:43 引擎当面回的。
        文件不在就跳过——它属于部署现场的物证，不入库。
        """
        p = ROOT / "deploy" / "probe_resp.json"
        if not p.is_file():
            self.skipTest("现场物证 probe_resp.json 不在（跳过，不代表验证过）")
            return
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
        raw, meta = main._audio_from_response(data)
        self.assertEqual(raw[:4], b"RIFF")
        self.assertEqual(raw[8:12], b"WAVE")
        self.assertEqual(meta["sample_rate"], 48000)
        self.assertEqual(meta["channels"], 2)
        self.assertGreater(len(raw), 300_000, "这份成品实测约 736KB，缩水说明解码漏了字节")
        # WAV 头里的 data 段长度应与实际字节数吻合（差太多说明 base64 被截断）
        import struct
        declared = struct.unpack("<I", raw[40:44])[0]
        self.assertLessEqual(declared, len(raw) - 44 + 8,
                             f"data 段声明 {declared} 字节，但文件只有 {len(raw) - 44}")

    def test_data_uri_prefix_is_accepted(self):
        raw, _ = main._audio_from_response({"audio": "data:audio/wav;base64," + _WAV_B64})
        self.assertEqual(raw[:4], b"RIFF")

    def test_artifacts_path_still_works(self):
        raw, meta = main._audio_from_response(
            {"artifacts": [{"id": "score", "kind": "custom",
                            "payload": base64.b64encode(b"X:1\nK:C\n").decode(),
                            "meta": {"format": "abc"}},
                           {"id": "audio", "kind": "custom", "payload": _WAV_B64,
                            "meta": {"format": "wav"}}]})
        self.assertEqual(raw[:4], b"RIFF")

    def test_no_audio_is_a_loud_failure_not_an_empty_success(self):
        """引擎回了包但没有音频时，绝不返回 b""（那会在 output/ 里存下一个 0 字节"成品"）。"""
        with self.assertRaises(HTTPException) as ctx:
            main._audio_from_response({"artifacts": [{"id": "score", "payload": "YWJj",
                                                      "meta": {"format": "abc"}}]})
        self.assertEqual(ctx.exception.status_code, 502)
        detail = ctx.exception.detail
        self.assertIn("score", str(detail.get("artifact_ids")),
                      "要说出引擎到底回了什么，不然排查只能靠猜")

    def test_non_base64_payload_does_not_become_silence(self):
        with self.assertRaises(HTTPException):
            main._audio_from_response({"audio": "不是base64～～"})


class RouteValidation(unittest.TestCase):
    """400 分支都在碰引擎之前返回，因此这些用例不会触发任何计算。"""

    def setUp(self) -> None:
        self.client = TestClient(main.app)

    def test_missing_style_400(self):
        r = self.client.post("/api/music/generate", json={"lyrics": "[Verse]\na"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("style", str(r.json()["detail"]))

    def test_bad_cot_uses_the_kernels_wording(self):
        r = self.client.post("/api/music/generate", json={"style": "s", "cot": "maybe"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("cot must be one of: full, melody, off", str(r.json()["detail"]))

    def test_abc_with_off_is_rejected_here_not_by_the_engine(self):
        r = self.client.post("/api/music/generate",
                             json={"style": "s", "cot": "off", "abc": "X:1\nK:C\n"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("external ABC requires cot=melody or cot=full", str(r.json()["detail"]))

    def test_non_object_body_400(self):
        r = self.client.post("/api/music/generate", json=["not", "a", "dict"])
        self.assertEqual(r.status_code, 400)


class EngineBackendChoice(unittest.TestCase):
    def test_backend_chain_is_coherent(self):
        """三处后端口径要能自洽：state 是**当前**选择，server.json 是下次冷启动的默认。

        用户在 ⑫ 里切过 CPU 之后，state 与 server.json 允许不同（这正是"切换"的含义）；
        不允许的是：任一方写了构建不支持的后端、或 main 自启用的后端与 state 不一致。
        """
        import json
        cfg = json.loads((ROOT / "cpp" / "server.json").read_text(encoding="utf-8"))
        state_path = ROOT / "runtime" / "data" / "engine_state.json"
        self.assertTrue(state_path.is_file(), "缺 engine_state.json：app.py 会按 cuda 默认去启引擎")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        effective = state.get("backend")
        default = cfg.get("backend")
        self.assertIn(effective, ("cpu", "cuda", "vulkan"), "state 里的后端非法")
        self.assertIn(default, ("cpu", "cuda", "vulkan"), "server.json 里的后端非法")
        self.assertEqual(main._engine_backend(), effective,
                         "main.py 自启引擎时用的后端必须与当前 state 一致")
        # 数小时的推理不能被引擎自己的 5 分钟 busy 守卫掐断
        self.assertEqual(cfg.get("busy_timeout_ms"), 0,
                         "挂机长跑必须关掉引擎的 busy 守卫，否则第一条请求 5 分钟就 503")


class Checkpoints(unittest.TestCase):
    """AR 检查点：同输入同键、种子变了不复用、过期作废、用完即弃。"""

    BASE = {"style": "English, pop", "lyrics": "[Verse] a", "cot": "full", "seed": 7,
            "num_inference_steps": 8}

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = main._CHECKPOINT_DIR
        main._CHECKPOINT_DIR = Path(self._tmp.name)

    def tearDown(self):
        main._CHECKPOINT_DIR = self._saved
        self._tmp.cleanup()

    def test_key_is_stable_and_seed_sensitive(self):
        k1 = main._checkpoint_key(dict(self.BASE))
        k2 = main._checkpoint_key(dict(self.BASE))
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, main._checkpoint_key({**self.BASE, "seed": 8}),
                            "换种子必须换键，否则会复用别人的 token")
        self.assertNotEqual(k1, main._checkpoint_key({**self.BASE, "style": "other"}))

    def test_save_load_drop_roundtrip(self):
        main._save_checkpoint(self.BASE, [1, 2, 3], "X:1")
        ck = main._load_checkpoint(self.BASE)
        self.assertEqual(ck["tokens"], [1, 2, 3])
        self.assertEqual(ck["abc"], "X:1")
        main._drop_checkpoint(self.BASE)
        self.assertIsNone(main._load_checkpoint(self.BASE), "成功后必须删，否则同 seed 重提会换波形")

    def test_expired_checkpoint_is_discarded(self):
        main._save_checkpoint(self.BASE, [1], "")
        path = main._checkpoint_path(self.BASE)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["ts"] = 0
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(main._load_checkpoint(self.BASE))
        self.assertFalse(path.exists(), "过期检查点应当被删掉，不占磁盘")

    def test_two_phase_is_skipped_for_external_abc_and_explicit_stop(self):
        self.assertTrue(main._two_phase_enabled(dict(self.BASE)))
        self.assertFalse(main._two_phase_enabled({**self.BASE, "abc": "X:1"}),
                         "自带谱时 AR 的产物不再可复用，不能拆")
        self.assertFalse(main._two_phase_enabled({**self.BASE, "stop_after": "semantic"}))

    def test_phase2_forces_no_sampling_and_keeps_score(self):
        body = main._phase2_body("yue2", dict(self.BASE), [5, 6, 7, 8], "X:1 K:C")
        req = body["request"]
        self.assertEqual(req["semantic_min_tokens"], 4)
        self.assertEqual(req["semantic_max_tokens"], 4, "上下界必须相等，否则 AR 会继续采样")
        self.assertEqual(req["options"]["semantic_prefix"], [5, 6, 7, 8])
        self.assertNotIn("stop_after", req["options"])
        self.assertEqual(req["cot"], "full")
        self.assertEqual(req["abc"], "X:1 K:C")

    def test_phase2_without_score_falls_back_to_off(self):
        body = main._phase2_body("yue2", dict(self.BASE), [1], "")
        self.assertEqual(body["request"]["cot"], "off")


class HealthShape(unittest.TestCase):
    """页脚健康灯的取数口径。

    实测教训：推理期间引擎被 CPU 占满，只读的 /v1/models 也要等十几秒——原先
    health 串了两次 HTTP，/api/health 整段 19.7 秒才回，而启动脚本给的是 3 秒 × 20 次
    探测窗口，于是"网关明明活着、脚本说未就绪"。现在模型名改从 server.json 现读。
    """

    def expected_name(self):
        """期望值现读 server.json。

        原来这里写死 `yue2-3b-q4_k_m.gguf`——那锁的是"某一台机器当前用哪一档量化"，
        换一台机器（或本机在 ⑫ 切到 q8）就假红。这条测试要锁的是**取数来源**：
        名字必须来自 server.json，而不是去问引擎。所以期望值也从 server.json 取，
        再用"必须以 .gguf 结尾"守住"取不到就退回 id"那种退化。
        """
        cfg = json.loads((ROOT / "cpp" / "server.json").read_text(encoding="utf-8"))
        entry = (cfg.get("models") or [{}])[0]
        so = entry.get("session_options") or {}
        name = str(so.get("yue2.model_gguf") or "").strip()
        if not name:
            name = str(entry.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1]
        self.assertTrue(name.endswith(".gguf"), f"server.json 自己就该给出权重文件名：{name!r}")
        return name

    def test_model_name_comes_from_server_json_not_from_the_engine(self):
        names = main._server_json_models()
        self.assertEqual(names.get("yue2"), self.expected_name(),
                         "页脚显示的必须是 server.json 真正指向的那一档权重")

    def _health(self, engine, listening):
        real_get, real_listen = main._engine_get, main._engine_port_listening
        main._engine_get = lambda *a, **k: engine
        main._engine_port_listening = lambda *a, **k: listening
        try:
            r = TestClient(main.app).get("/api/health")
            return r.status_code, r.json()
        finally:
            main._engine_get, main._engine_port_listening = real_get, real_listen

    def test_engine_down_is_reported_as_degraded_not_ok(self):
        """引擎全死时必须 gateway="degraded"：页面只认 ok，绝不能再亮绿灯（评审 B1）。"""
        code, j = self._health(None, False)
        self.assertEqual(code, 200)
        self.assertEqual(j["gateway"], "degraded")
        self.assertEqual(j["audiocpp"]["status"], "down")
        self.assertIsNone(j["audiocpp"]["loaded"], "查不到就说查不到，不拿 False 冒充实测结论")
        # 引擎全死时页脚仍要报出配置里那一档权重（名字不靠引擎活着才拿得到）
        self.assertEqual(j["models"]["yue2"], self.expected_name())
        self.assertTrue(str(j["models"]["yue2"]).endswith(".gguf"))

    def test_busy_engine_is_not_mistaken_for_dead(self):
        """端口在监听、/health 超时 = 生成中被打满，报 busy，不能报 down（评审 T1）。"""
        code, j = self._health(None, True)
        self.assertEqual(code, 200)
        self.assertEqual(j["gateway"], "ok")
        self.assertEqual(j["audiocpp"]["status"], "busy")

    def test_health_never_returns_5xx(self):
        """看门狗把 5xx 当死；health 三个分支都必须 200（三方评审 C4/T5）。"""
        for engine, listening in ((None, False), (None, True),
                                  ({"status": "ok", "backend": "cpu", "models": 1}, True)):
            code, _ = self._health(engine, listening)
            self.assertEqual(code, 200, f"engine={engine} listening={listening} 回了 {code}")

    def test_kernels_wording_is_not_rewritten_by_us(self):
        """前端按 `status === "ok"` 判绿灯，所以引擎原话里的 status 不能被我改名。"""
        _, j = self._health({"status": "ok", "backend": "cpu", "models": 1}, True)
        self.assertEqual(j["audiocpp"]["status"], "ok")
        self.assertEqual(j["audiocpp"]["backend"], "cpu")


if __name__ == "__main__":
    unittest.main()
