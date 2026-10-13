"""v10.14：AI 对话搬回网关 + 启动入口不再依赖 dsh 门票。

为什么锁这两件事（都是 2026-10-14 真机上坏掉的）：
  1. 上游 openclaw/dsh 升级后把启动日志里的门票打成 `token=***redacted***`，
     双击启动的工作台脚本读不到真票 → 连续 24 轮 HTTP 401，用户完全进不去；
  2. AI 工作台页原来是 iframe 借 dsh 原生界面，票一失效这页跟着废。
现在：页面由网关自己直出（gateway_serve_ui=True），对话走网关内的 ai_lab.chat_turn。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import ai_lab  # noqa: E402
import ai_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def client():
    """挂进一个真 FastAPI 应用再测：裸 APIRouter 没有 HTTPException 处理栈，
    400/502 会被 TestClient 当未捕获异常抛出来，测不到我们真正想验的状态码。"""
    app = FastAPI()
    app.include_router(ai_router.router)
    return TestClient(app)

INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
LAUNCH_BAT = (ROOT / "scripts" / "启动音乐工作台.bat").read_text(encoding="utf-8")


class TestChatRunsInsideGateway(unittest.TestCase):
    def setUp(self):
        self._saved = (ai_lab.chat_turn, ai_lab.ai_ready, ai_router.subprocess.run)
        ai_lab.ai_ready = lambda: True

    def tearDown(self):
        ai_lab.chat_turn, ai_lab.ai_ready, ai_router.subprocess.run = self._saved

    def test_chat_turn_is_the_only_engine_and_no_node_is_spawned(self):
        seen = {}

        def fake_chat_turn(sid, msg, confirm=False):
            seen.update(sid=sid, msg=msg, confirm=confirm)
            return {"session_id": "sess-1", "reply": "写好了一版词",
                    "feeds": [{"type": "generate", "style": "S", "lyrics": "L"}],
                    "needs_confirm": False, "confirm_hint": ""}

        def boom_no_spawn(*a, **k):
            raise AssertionError("AI 对话不该再 spawn node/dsh 子进程")

        ai_lab.chat_turn = fake_chat_turn
        ai_router.subprocess.run = boom_no_spawn
        c = client()
        r = c.post("/api/ai/chat", json={"session_id": "", "message": "写一首歌", "confirm": False})
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j["reply"], "写好了一版词")
        self.assertEqual(j["session_id"], "sess-1")
        self.assertEqual(len(j["feeds"]), 1, "产出卡必须回给前端，界面才有「复制」可点")
        self.assertEqual(seen["msg"], "写一首歌")

    def test_confirm_may_carry_empty_message(self):
        """确认执行上一轮挂起的删除操作时，用户不需要再打一句话。"""
        ai_lab.chat_turn = lambda sid, msg, confirm=False: {
            "session_id": sid or "x", "reply": "已删除", "feeds": [],
            "needs_confirm": False, "confirm_hint": ""}
        c = client()
        r = c.post("/api/ai/chat", json={"session_id": "abc", "message": "  ", "confirm": True})
        self.assertEqual(r.status_code, 200, r.text)
        r2 = c.post("/api/ai/chat", json={"session_id": "abc", "message": "  ", "confirm": False})
        self.assertEqual(r2.status_code, 400, "空消息且不确认，必须拦下来")

    def test_failure_says_the_session_was_not_written(self):
        def blow(sid, msg, confirm=False):
            raise RuntimeError("网络断了")
        ai_lab.chat_turn = blow
        c = client()
        r = c.post("/api/ai/chat", json={"session_id": "abc", "message": "来一首"})
        self.assertEqual(r.status_code, 502)
        d = r.json()["detail"]
        self.assertIn("网络断了", d)
        self.assertIn("会话未写入本轮", d, "异常时要说清历史有没有被写坏，否则用户不敢重发")

    def test_status_reports_the_gateway_not_dsh(self):
        c = client()
        j = c.get("/api/ai/status").json()
        self.assertNotEqual(j["via"], "dsh", "对话已经跑在网关里，状态不能再报 dsh")
        self.assertIn("chat_turn", j["via"])


class TestUiHasNoDshDependency(unittest.TestCase):
    def test_ai_page_is_native(self):
        for gone in ("aiFrame", "/api/ai/web", "aiFrameLoading"):
            self.assertNotIn(gone, INDEX, "⑥ 还留着借外部界面渲染对话的残迹：%s" % gone)
        for need in ('id="chatLog"', 'id="chatInput"', 'id="chatSend"',
                     'id="chatConfirm"', "/api/ai/chat"):
            self.assertIn(need, INDEX, "⑥ 的原生对话缺部件：%s" % need)

    def test_launcher_opens_the_gateway_and_never_asks_for_a_ticket(self):
        for gone in ("read_dsh_url", "_dsh_web.log", "DSH_URL", "dshverify"):
            self.assertNotIn(gone, LAUNCH_BAT, "启动脚本还在依赖门票：%s" % gone)
        self.assertIn('set "LAB_URL=http://127.0.0.1:%GATEWAY_PORT%/"', LAUNCH_BAT,
                      "启动脚本应当打开网关直出的页面")
        self.assertIn('if not "%GW_CODE%"=="200"', LAUNCH_BAT,
                      "打开之前必须实测一次 200，否则又是「页面开了但是空的」")


if __name__ == "__main__":
    unittest.main()
