"""app.py 拆分止损门（issue #10 / #11）。

规矩：app.py 只许变小——新路由进 src/routers/，新业务逻辑进 src/services/。
确实必须在 app.py 里长大（进程级队列、引擎句柄这类），就在同一笔提交里
调高 BUDGETS 并写明理由；这是"变大要在 diff 里留理由"的决定，不是禁止。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
sys.path.insert(0, str(ROOT / "scripts"))

import app_size_report  # noqa: E402

# 冻结值取精确字节数、不取整：取整的余量会被无声吃掉（issue #11）。
# 514,459 = 2026-10-27 工作树实际内容（含当天换声训练改进的未提交改动）；
# 那笔改动落地后，应把这里收紧到提交后的实际值。
BUDGETS = {"app_py_bytes": 514459}
# "静默零即失败"：路径写错/读空文件时，门不许空转地判绿（issue #11）。
FLOOR_BYTES = 400_000


class AppPyBudget(unittest.TestCase):
    def test_file_is_the_real_gateway(self):
        self.assertTrue(APP.is_file(), "找不到 app.py，止损门不能空转")
        self.assertGreater(app_size_report.current_size(APP), FLOOR_BYTES,
                           "app.py 读出来太小，量具的路径大概率错了")
        text = APP.read_text(encoding="utf-8", errors="replace")
        self.assertIn('router = APIRouter(prefix="/api")', text,
                      "app.py 不像网关主文件（/api 路由前缀缺失），预算门的测量对象错了")

    def test_app_py_within_budget(self):
        size = app_size_report.current_size(APP)
        over = app_size_report.over_budget(size, BUDGETS["app_py_bytes"])
        self.assertEqual(
            over, 0,
            f"app.py 长了 {over} 字节（当前 {size:,}，预算 {BUDGETS['app_py_bytes']:,}）。"
            "新代码请进 src/（路由 src/routers/、业务 src/services/）；确需在 app.py 里长大，"
            "请在同一笔提交里调高 BUDGETS 并写明理由。",
        )

    def test_mutation_over_budget_turns_red(self):
        """门本身要能红：造一个超预算的假 app.py，检查器必须报出增长（issue #11）。"""
        budget = BUDGETS["app_py_bytes"]
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "app.py"
            fake.write_bytes(b"x" * (budget + 7))
            self.assertEqual(app_size_report.over_budget(app_size_report.current_size(fake), budget), 7)
            fake.write_bytes(b"x" * (budget - 1))
            self.assertEqual(app_size_report.over_budget(app_size_report.current_size(fake), budget), 0)

    def test_crlf_does_not_inflate_size(self):
        """Windows 检出是 CRLF：同一份内容必须与 Linux/LF 算出同一个字节数。"""
        lf = b"a\nb\nc\n"
        self.assertEqual(app_size_report.normalized_size(lf), 6)
        self.assertEqual(app_size_report.normalized_size(lf.replace(b"\n", b"\r\n")), 6)


if __name__ == "__main__":
    unittest.main()
