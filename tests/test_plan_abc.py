"""/api/generate/plan_abc（引擎 stop_after=abc：只出谱不出音频）的契约测试。

这条出口的存在理由本身就值得锁住：闭源内核 main.pyd 的 GenerateJSON 没有 stop_after 字段，
从网关那层塞会被 pydantic 静默丢掉——所以后端必须直连引擎。测试用假 httpx.Client 抓住真实发出的
请求体，验的就是"直连时形状对不对"，不是"函数跑没跑通"。
"""
import base64
import contextlib
import inspect
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402

ABC_TEXT = "X:1\nM:4/4\nK:C\nV: Vocal\nC2 D2 E2 F2 |\n"


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body

    @property
    def text(self):
        return self._body if isinstance(self._body, str) else json.dumps(self._body)


class FakeClient:
    """替掉 app.httpx.Client：记下请求体，回放预设响应。"""
    last = None
    reply = None

    def __init__(self, *a, **kw):
        self.kw = kw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, path, json=None):
        FakeClient.last = {"path": path, "body": json}
        return FakeClient.reply


class Sandbox(unittest.TestCase):
    def setUp(self):
        self._httpx = app.httpx
        self._ensure = app._ensure_backend
        app.httpx = type("H", (), {"Client": FakeClient})
        app._ensure_backend = lambda *a, **kw: None
        FakeClient.last = None
        FakeClient.reply = FakeResponse({
            "artifacts": [{"id": "score", "kind": "custom",
                           "payload": base64.b64encode(ABC_TEXT.encode()).decode(),
                           "meta": {"format": "abc", "truncated": "false"}}],
            "timing": {"wall_ms": 7900.0},
        })

    def tearDown(self):
        app.httpx = self._httpx
        app._ensure_backend = self._ensure

    def opts(self):
        req = FakeClient.last["body"]["request"]
        return req["options"], req


class TestRequestShape(Sandbox):
    def test_hits_the_engine_directly_with_the_documented_envelope(self):
        app.generate_plan_abc({"style": "piano pop", "lyrics": "[Verse]\nhi\n"})
        self.assertEqual(FakeClient.last["path"], "/v1/tasks/run")
        body = FakeClient.last["body"]
        self.assertEqual(body["model"], "yue2")
        self.assertEqual(body["request"]["task"], "gen")
        o, _req = self.opts()
        self.assertEqual(o["stop_after"], "abc", "不带 stop_after 就是渲染整首歌，这条出口的意义全没")
        self.assertEqual(o["style"], "piano pop")

    def test_off_cot_is_rewritten_and_said_out_loud(self):
        r = app.generate_plan_abc({"style": "x", "cot": "off"})
        o, _ = self.opts()
        self.assertEqual(o["cot"], "full", "off 没有谱可停，必须改写才有意义")
        self.assertIn("改写", r["message"])

    def test_melody_cot_is_left_alone(self):
        app.generate_plan_abc({"style": "x", "cot": "melody"})
        o, _ = self.opts()
        self.assertEqual(o["cot"], "melody", "melody 本身合法，不许顺手改成 full")

    def test_random_seed_is_materialised_before_sending(self):
        r = app.generate_plan_abc({"style": "x", "seed": -1})
        o, _ = self.opts()
        self.assertIsInstance(o["seed"], int)
        self.assertGreaterEqual(o["seed"], 0, "把 -1 原样发给引擎＝这次结果无法复现")
        self.assertEqual(r["seed"], o["seed"], "回包说的 seed 必须等于真发出去的那个")

    def test_advanced_abc_params_pass_through_only_when_filled(self):
        app.generate_plan_abc({"style": "x", "abc_max_tokens": 96, "abc_top_p": "",
                               "abc_temperature": None})
        o, _ = self.opts()
        self.assertEqual(o["abc_max_tokens"], 96)
        self.assertNotIn("abc_top_p", o, "空字符串不该变成参数发出去")
        self.assertNotIn("abc_temperature", o)


class TestReplies(Sandbox):
    def test_truncated_score_is_reported_as_truncated(self):
        FakeClient.reply = FakeResponse({
            "artifacts": [{"id": "score",
                           "payload": base64.b64encode(ABC_TEXT.encode()).decode(),
                           "meta": {"format": "abc", "truncated": "true"}}],
            "timing": {"wall_ms": 1200.0}})
        r = app.generate_plan_abc({"style": "x"})
        self.assertTrue(r["truncated"])
        self.assertIn("截断", r["message"], "截断的谱尾部不完整，不说不等于没发生")
        self.assertEqual(r["abc"], ABC_TEXT)

    def test_missing_style_never_touches_the_engine(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            app.generate_plan_abc({"style": "   "})
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIsNone(FakeClient.last, "参数不全就不该去占 GPU")

    def test_engine_error_is_forwarded_not_swallowed(self):
        FakeClient.reply = FakeResponse(
            {"error": {"message": "Yue2 requires non-empty style"}}, status=500)
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            app.generate_plan_abc({"style": "x"})
        self.assertEqual(cm.exception.status_code, 502)
        self.assertIn("requires non-empty", str(cm.exception.detail))

    def test_reply_without_a_score_artifact_is_an_error(self):
        FakeClient.reply = FakeResponse({"artifacts": [], "timing": {"wall_ms": 10.0}})
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            app.generate_plan_abc({"style": "x"})
        self.assertIn("没有 ABC 谱", str(cm.exception.detail))

    def test_handler_is_sync_so_the_gateway_keeps_answering(self):
        self.assertFalse(inspect.iscoroutinefunction(app.generate_plan_abc),
                         "async def 里等 _GPU_SEM 会让整个网关假死（本项目已修过一次）")


class TestUiContract(unittest.TestCase):
    def setUp(self):
        index = Path(app.__file__).resolve().parent / "static" / "index.html"
        with open(index, encoding="utf-8") as fh:
            self.html = fh.read()

    def test_button_lives_inside_the_score_section(self):
        """这条出口只在 ⑤ 有意义（结果写进 ⑤ 自己的谱框）。放 ② 就等于凭空多一个跨页动作。"""
        i = self.html.index('id="scoreSection"')
        j = self.html.index("参数区改成常开", i)
        block = self.html[i:j]
        self.assertIn('id="planAbcBtn"', block)
        self.assertIn('id="planAbcStatus"', block)

    def test_handler_writes_the_line_next_to_the_button(self):
        script = self.html[self.html.index("<script>"):]
        body = script[script.index('$("planAbcBtn").onclick'):]
        self.assertIn('$("abc").value = j.abc', body[:1600], "结果没写回本页谱框，这条按钮就是摆设")
        self.assertIn("planAbcSay(j.message", body[:2200], "成功也要在按钮旁边留话")
        self.assertIn("planAbcSay(", body[:2400])


if __name__ == "__main__":
    unittest.main()
