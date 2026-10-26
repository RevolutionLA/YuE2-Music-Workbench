"""锁定「出歌期间按住别睡」这条接线（2026-10-21 凌晨的实测教训）。

现场：系统在新式待机里把在跑的 AR 解码冻住过多次（Kernel-Power 506/507/566 在
02:27、03:12、04:52 各成对出现），一首歌被拖长，引擎没崩纯属运气。RVC 训练早就
调用 _rvc_keep_awake，出歌这条路径原来一条都没声明。

这些用例只碰 wiring，不碰网络与 GPU（P1 起生成走进程内，直接 mock 内核函数）：
    py312\\python.exe -m unittest discover -s tests -p "test_gen_keep_awake.py" -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402
from fastapi.responses import Response  # noqa: E402


class GenHoldsKeepAwake(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_awake = app._rvc_keep_awake
        self._saved_gen = getattr(app.main, "generate_response", None)
        self.calls: list[bool] = []
        app._rvc_keep_awake = lambda on: self.calls.append(bool(on))

    def tearDown(self) -> None:
        app._rvc_keep_awake = self._saved_awake
        if self._saved_gen is not None:
            app.main.generate_response = self._saved_gen

    def test_hold_during_generation_and_release_after(self):
        async def fake(payload):
            return Response(content=b"RIFFfake", media_type="audio/wav")

        app.main.generate_response = fake
        code, detail, content, headers = app._gen_run_once({"style": "s"})
        self.assertEqual(code, 200)
        self.assertEqual(content, b"RIFFfake")
        # 顺序必须是"先按住、再放"：把 finally 里的 False 删掉，这条立刻红
        self.assertEqual(self.calls, [True, False],
                         "出歌期间必须声明系统别睡，结束后必须撤销（按线程声明，不会误伤训练）")

    def test_hold_is_released_even_when_the_call_explodes(self):
        """超时/连接失败一样要撤销声明，否则这台机器从此再也不睡了。"""
        async def boom(payload):
            raise RuntimeError("connection reset")

        app.main.generate_response = boom
        code, detail, content, headers = app._gen_run_once({"style": "s"})
        self.assertEqual(code, 0)
        self.assertIn("connection reset", detail)
        self.assertEqual(self.calls, [True, False])


if __name__ == "__main__":
    unittest.main()
