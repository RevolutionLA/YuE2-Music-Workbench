"""引擎所有权：重启/终止只能动本网关自己那只，不能按镜像名连坐。"""

from __future__ import annotations

import inspect
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402


class FakeProc:
    def __init__(self, pid: int, alive: bool = True):
        self.pid = pid
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


class EngineOwnership(unittest.TestCase):
    def setUp(self):
        self._saved = (app._ENGINE_PROC, getattr(app.main, "_AUDIOCPP_PROC", None))
        app._ENGINE_PROC = None
        app.main._AUDIOCPP_PROC = None

    def tearDown(self):
        app._ENGINE_PROC, app.main._AUDIOCPP_PROC = self._saved

    def test_testing_never_touches_a_real_engine(self):
        """CI/回归进程没有自己的句柄时，即使端口上有真引擎也不许杀。"""
        with mock.patch.object(app, "_pid_listening_on", return_value=1234), \
             mock.patch("app.subprocess.run") as run:
            self.assertIsNone(app._stop_current_engine())
            run.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows 专用：taskkill 参数")
    def test_kills_tracked_pid_only(self):
        app._ENGINE_PROC = FakeProc(4321)
        with mock.patch("app.subprocess.run") as run:
            self.assertEqual(app._stop_current_engine(), 4321)
        args = run.call_args[0][0]
        self.assertEqual(args[:4], ["taskkill", "/F", "/T", "/PID"])
        self.assertEqual(args[4], "4321")
        self.assertNotIn("/IM", args)

    @unittest.skipUnless(os.name == "nt", "Windows 专用：taskkill 参数")
    def test_port_fallback_verifies_image_name(self):
        with mock.patch.dict(os.environ, {"YUE2_TESTING": "0"}), \
             mock.patch.object(app, "_pid_listening_on", return_value=777), \
             mock.patch.object(app, "_is_audiocpp_pid", return_value=False), \
             mock.patch("app.subprocess.run") as run:
            self.assertIsNone(app._stop_current_engine())
            run.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows 专用：taskkill 参数")
    def test_port_fallback_kills_verified_engine(self):
        with mock.patch.dict(os.environ, {"YUE2_TESTING": "0"}), \
             mock.patch.object(app, "_pid_listening_on", return_value=777), \
             mock.patch.object(app, "_is_audiocpp_pid", return_value=True), \
             mock.patch("app.subprocess.run") as run:
            self.assertEqual(app._stop_current_engine(), 777)
        args = run.call_args[0][0]
        self.assertIn("/PID", args)
        self.assertNotIn("/IM", args)

    def test_restart_uses_ownership_stop_and_tracks_proc(self):
        """源码级锁：重启路径必须走 _stop_current_engine，且保留 Popen 句柄。"""
        src = inspect.getsource(app._restart_audiocpp)
        self.assertIn("_stop_current_engine()", src)
        self.assertIn("_ENGINE_PROC = subprocess.Popen", src)
        self.assertNotIn('"/IM"', src)


if __name__ == "__main__":
    unittest.main()
