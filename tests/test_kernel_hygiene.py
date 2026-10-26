"""P0 守门用例：内核唯一化 + 测试隔离。

这两件事的失败方式都很隐蔽：内核选错时功能看似正常但字段被静默丢弃；
测试不隔离时会把假模型名/临时 sidecar 写进开发机。这里用可失败的断言锁住。
"""

from __future__ import annotations

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

try:
    from tests import _sandbox
except ImportError:  # 以 tests/ 为顶层目录运行时
    import _sandbox  # type: ignore[no-redef]


class KernelUniqueness(unittest.TestCase):
    """仓库默认只跑开源 shim；pyd 只保留回滚，必须显式 YUE2_ALLOW_PYD=1 才放行。"""

    def test_gateway_runs_the_open_shim(self):
        if os.environ.get("YUE2_ALLOW_PYD") == "1":
            self.skipTest("YUE2_ALLOW_PYD=1：显式允许编译内核")
        if not getattr(app.main, "__file__", ""):
            self.skipTest("main 是测试替身（引擎参数表未部署）")
        kernel = app._gateway_kernel()
        self.assertEqual(
            kernel["kind"], "shim",
            "网关内核不是 shim；把 main.cp312-win_amd64.pyd 移出仓库根（回滚件放 runtime/backup/）",
        )
        self.assertTrue(kernel["path"].endswith("main.py"), kernel["path"])

    def test_no_compiled_kernel_in_repo_root(self):
        if os.environ.get("YUE2_ALLOW_PYD") == "1":
            self.skipTest("YUE2_ALLOW_PYD=1：显式允许编译内核")
        pyd = ROOT / "main.cp312-win_amd64.pyd"
        self.assertFalse(
            pyd.exists(),
            "仓库根存在编译内核：它会让生产与测试走两条不同的代码路径；"
            "回滚请放到 runtime/backup/ 并用 YUE2_ALLOW_PYD=1 显式启用",
        )


class TestIsolation(unittest.TestCase):
    """测试进程必须在引擎关闭、真实配置受保护的条件下运行。"""

    def test_shim_detects_test_runner(self):
        """即使有人用旧命令跑 discover（沙箱没装），内核也要自己认得出测试进程。"""
        self.assertTrue(app.main._in_test_runner())
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("not _in_test_runner()", src)

    def test_autostart_is_disabled(self):
        self.assertEqual(os.environ.get("YUE2_NO_ENGINE_AUTOSTART"), "1")
        self.assertEqual(os.environ.get("YUE2_TESTING"), "1")

    def test_toast_is_suppressed(self):
        with mock.patch("app.subprocess.Popen", side_effect=AssertionError("测试不许弹通知")):
            app._win_toast("测试标题", "测试正文")

    def test_real_engine_files_are_snapshotted(self):
        server = ROOT / "cpp" / "server.json"
        if not server.is_file():
            self.skipTest("引擎未部署，没有可保护的 server.json")
        self.assertIn(server, _sandbox._snapshot)
        self.assertEqual(server.read_bytes(), _sandbox._snapshot[server])

    def test_sidecars_are_snapshotted(self):
        sidecars = list((ROOT / "cpp" / "model").glob("**/sidecars/*.json"))
        if not sidecars:
            self.skipTest("引擎未部署，没有 sidecar")
        for path in sidecars:
            self.assertIn(path, _sandbox._snapshot)


if __name__ == "__main__":
    unittest.main()
