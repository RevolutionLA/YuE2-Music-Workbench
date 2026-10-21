"""锁定「出歌期间按住别睡」这条接线（2026-10-21 凌晨的实测教训）。

现场：系统在新式待机里把在跑的 AR 解码冻住过多次（Kernel-Power 506/507/566 在
02:27、03:12、04:52 各成对出现），一首歌被拖长，引擎没崩纯属运气。RVC 训练早就
调用 _rvc_keep_awake，出歌这条路径原来一条都没声明。

这些用例只碰 wiring，不碰网络与 GPU：
    py312\\python.exe -m unittest discover -s tests -p "test_gen_keep_awake.py" -v
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

# app.py 无条件 `import main`（编译网关）。测试要一个可 import 的替身，
# 与 tests/test_review_fixes.py 同一口径；谁先跑谁注入，后者直接复用。
if "main" not in sys.modules:
    from fastapi import FastAPI

    _stub = types.ModuleType("main")
    _stub.app = FastAPI()
    _stub.settings = types.SimpleNamespace(open_browser=True)
    sys.modules["main"] = _stub

import app  # noqa: E402


class _FakeResp:
    status_code = 200
    content = b"RIFFfake"
    text = "ok"
    headers: dict = {}


class _FakeClient:
    """替掉 httpx.Client：只负责记录「有没有被调用」并返回一个成功响应。"""

    def __init__(self, *a, **k):
        self.posted = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None):
        self.posted = (url, json)
        return _FakeResp()


class GenHoldsKeepAwake(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (app._rvc_keep_awake, app.httpx.Client)
        self.calls: list[bool] = []
        app._rvc_keep_awake = lambda on: self.calls.append(bool(on))
        self.fake = _FakeClient()
        app.httpx.Client = lambda *a, **k: self.fake

    def tearDown(self) -> None:
        app._rvc_keep_awake, app.httpx.Client = self._saved

    def test_hold_during_generation_and_release_after(self):
        code, detail, content, headers = app._gen_run_once({"style": "s"})
        self.assertEqual(code, 200)
        self.assertEqual(content, b"RIFFfake")
        # 顺序必须是"先按住、再放"：把 finally 里的 False 删掉，这条立刻红
        self.assertEqual(self.calls, [True, False],
                         "出歌期间必须声明系统别睡，结束后必须撤销（按线程声明，不会误伤训练）")
        self.assertEqual(self.fake.posted[0], "/api/music/generate")

    def test_hold_is_released_even_when_the_call_explodes(self):
        """超时/连接失败一样要撤销声明，否则这台机器从此再也不睡了。"""
        def boom(*a, **k):
            raise RuntimeError("connection reset")
        app.httpx.Client = lambda *a, **k: boom()
        with self.assertRaises(RuntimeError):
            app._gen_run_once({"style": "s"})
        self.assertEqual(self.calls, [True, False])


if __name__ == "__main__":
    unittest.main()
