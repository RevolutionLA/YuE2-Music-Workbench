# -*- coding: utf-8 -*-
"""server.json 的"长跑必需项"迁移，以及网关内核的可辨识性（issue #4-5 / #4-7）。

这两条都属于同一类病：**同一版本，两台机器行为不同，而谁也看不出来。**

* #4-5 升级用户的旧 `cpp/server.json` 没有 `busy_timeout_ms`。引擎于是沿用自带的
  5 分钟 busy 守卫：一首跑几小时的歌会在中途被 503 掐断，日志里没有一条指向配置。
  修法是启动时幂等补齐（先备份）+ WARN，安装脚本同样幂等补键。
* #4-7 `main.cp312-win_amd64.pyd` 会遮蔽 `main.py`（CPython 扩展模块优先于源码），
  而它在 `.gitignore` 里、老安装机器上普遍存在。于是老机器走编译内核（不做字段
  白名单、不写丢键日志）、干净 clone 走 shim，能力不同却没有任何地方显示。

    py312\\python.exe -m unittest discover -s tests -p "test_server_json_hygiene.py" -v
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

if "main" not in sys.modules:
    from fastapi import FastAPI

    _stub = types.ModuleType("main")
    _stub.app = FastAPI()
    _stub.settings = types.SimpleNamespace(open_browser=True)
    sys.modules["main"] = _stub

import app  # noqa: E402

BAK_SUFFIX = ".bak-busy-timeout"


class BusyTimeoutMigration(unittest.TestCase):
    """缺键要补、补要留备份、非 0 只警告不擅改。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.cfg = self.dir / "server.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, cfg: dict) -> None:
        self.cfg.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read(self) -> dict:
        return json.loads(self.cfg.read_text(encoding="utf-8"))

    def _old_install(self) -> dict:
        """模拟升级用户：老配置没有 busy_timeout_ms。"""
        return {"backend": "cuda", "threads": 8,
                "models": [{"id": "yue2", "path": "model/yue2-3b-q8_0.gguf",
                            "session_options": {"yue2.model_gguf": "yue2-3b-q8_0.gguf"}}]}

    # ---- 补键 ---------------------------------------------------------- #
    def test_missing_key_is_added_zero_and_backed_up(self):
        self._write(self._old_install())
        note = app._ensure_server_json_busy_timeout(self.cfg)
        self.assertEqual(self._read()["busy_timeout_ms"], 0,
                         "必须补 0：引擎默认的 5 分钟 busy 守卫会掐断长歌")
        self.assertIn("busy_timeout_ms", note)
        bak = self.cfg.with_name(self.cfg.name + BAK_SUFFIX)
        self.assertTrue(bak.is_file(), "改配置前必须留备份")
        self.assertNotIn("busy_timeout_ms", json.loads(bak.read_text(encoding="utf-8")),
                         "备份必须是改之前那份")
        # 其它键一个都不许动
        now = self._read()
        self.assertEqual(now["backend"], "cuda")
        self.assertEqual(now["threads"], 8)
        self.assertEqual(now["models"][0]["path"], "model/yue2-3b-q8_0.gguf")

    def test_migration_is_idempotent(self):
        self._write(self._old_install())
        app._ensure_server_json_busy_timeout(self.cfg)
        bak = self.cfg.with_name(self.cfg.name + BAK_SUFFIX)
        first = bak.read_bytes()
        self.assertEqual(app._ensure_server_json_busy_timeout(self.cfg), "",
                         "已经补过就不该再报一次")
        self.assertEqual(bak.read_bytes(), first, "不许覆盖备份")

    def test_existing_zero_is_left_alone(self):
        cfg = self._old_install()
        cfg["busy_timeout_ms"] = 0
        self._write(cfg)
        before = self.cfg.read_bytes()
        self.assertEqual(app._ensure_server_json_busy_timeout(self.cfg), "")
        self.assertEqual(self.cfg.read_bytes(), before, "同值不许重写文件")
        self.assertFalse(self.cfg.with_name(self.cfg.name + BAK_SUFFIX).exists())

    def test_nonzero_top_level_warns_without_overwriting(self):
        cfg = self._old_install()
        cfg["busy_timeout_ms"] = 300000
        self._write(cfg)
        before = self.cfg.read_bytes()
        note = app._ensure_server_json_busy_timeout(self.cfg)
        self.assertIn("busy_timeout_ms", note)
        self.assertIn("503", note, "要说清后果，而不是只说'值不对'")
        self.assertEqual(self.cfg.read_bytes(), before,
                         "用户显式设的值不许擅自改，只警告")

    def test_model_level_key_is_called_out(self):
        """模型级 busy_timeout_ms 可能覆盖顶层：本机就是顶层 0、模型级 600000。"""
        cfg = self._old_install()
        cfg["busy_timeout_ms"] = 0
        cfg["models"][0]["busy_timeout_ms"] = 600000
        self._write(cfg)
        note = app._ensure_server_json_busy_timeout(self.cfg)
        self.assertIn("models[0]", note)
        self.assertIn("10 分钟", note, "要把毫秒翻成人能读的时长")

    def test_missing_or_broken_file_is_not_fatal(self):
        self.assertEqual(app._ensure_server_json_busy_timeout(self.cfg), "",
                         "没有配置不该在这里炸：_ensure_backend 会给更准的错")
        self.cfg.write_text("{not json", encoding="utf-8")
        self.assertEqual(app._ensure_server_json_busy_timeout(self.cfg), "")


class SetupScriptMigratesOldInstalls(unittest.TestCase):
    """`scripts/setup_runtime.py` 对已存在的 server.json 也要幂等补键（issue #4-5）。

    以前它只在"文件不存在"时写模板；老安装再跑一遍脚本，缺的键永远不会被补上。
    """

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "setup_runtime_under_test", ROOT / "scripts" / "setup_runtime.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "cpp").mkdir()
        shutil.copy2(ROOT / "server.json.example", self.root / "server.json.example")
        self._saved = self.mod.ROOT
        self.mod.ROOT = self.root

    def tearDown(self):
        self.mod.ROOT = self._saved
        self._tmp.cleanup()

    def _cfg_path(self) -> Path:
        return self.root / "cpp" / "server.json"

    def test_old_server_json_gets_busy_timeout(self):
        old = {"backend": "cpu", "threads": 4,
               "models": [{"id": "yue2", "path": "model/yue2-q4_k_m/yue2-3b-q4_k_m.gguf"}]}
        self._cfg_path().write_text(json.dumps(old), encoding="utf-8")
        self.assertTrue(self.mod.ensure_server_json("cpu", "q4km"))
        now = json.loads(self._cfg_path().read_text(encoding="utf-8"))
        self.assertEqual(now["busy_timeout_ms"], 0)
        self.assertEqual(now["threads"], 4, "老配置的其它选择不许被覆盖")
        self.assertEqual(now["models"][0]["path"],
                         "model/yue2-q4_k_m/yue2-3b-q4_k_m.gguf")
        self.assertTrue(self._cfg_path().with_name("server.json" + BAK_SUFFIX).is_file())

    def test_second_run_changes_nothing(self):
        old = {"backend": "cpu", "threads": 4, "models": [{"id": "yue2", "path": "x.gguf"}]}
        self._cfg_path().write_text(json.dumps(old), encoding="utf-8")
        self.mod.ensure_server_json("cpu", "q4km")
        first = self._cfg_path().read_bytes()
        self.mod.ensure_server_json("cpu", "q4km")
        self.assertEqual(self._cfg_path().read_bytes(), first, "幂等：第二次不许再改")

    def test_fresh_install_already_has_the_key(self):
        """模板里本来就有 0；新建时不该再走迁移分支。"""
        self.assertTrue(self.mod.ensure_server_json("cpu", "q4km"))
        now = json.loads(self._cfg_path().read_text(encoding="utf-8"))
        self.assertEqual(now["busy_timeout_ms"], 0)
        self.assertFalse(self._cfg_path().with_name("server.json" + BAK_SUFFIX).exists(),
                         "新建时就该是对的，不该产生备份")


class GatewayKernelIsIdentifiable(unittest.TestCase):
    """走的是编译内核还是开源 shim，必须能查出来（issue #4-7）。"""

    def test_kind_follows_the_loaded_module_suffix(self):
        real = app.main
        try:
            app.main = types.SimpleNamespace(__file__=r"C:\x\main.cp312-win_amd64.pyd")
            k = app._gateway_kernel()
            self.assertEqual(k["kind"], "pyd")
            self.assertIn(".pyd", k["path"])
            self.assertIn("pyd", k["note"])

            app.main = types.SimpleNamespace(__file__=r"C:\x\main.py")
            k = app._gateway_kernel()
            self.assertEqual(k["kind"], "shim")
            self.assertIn("main.py", k["path"])
            self.assertIn("白名单", k["note"])
        finally:
            app.main = real

    def test_pyd_note_tells_you_how_to_switch(self):
        real = app.main
        try:
            app.main = types.SimpleNamespace(__file__=r"C:\x\main.cp312-win_amd64.pyd")
            note = app._gateway_kernel()["note"]
            self.assertIn("移走", note,
                          "既然 pyd 遮蔽 main.py，就得说清怎么切回开源网关")
        finally:
            app.main = real

    def test_system_info_exposes_the_kernel(self):
        """⑬ 必须能看到——只在启动日志打一行，用户依然分不清两台机器。"""
        src = (ROOT / "src" / "routers" / "system.py").read_text(
            encoding="utf-8", errors="replace")
        i = src.index("def system_info(")
        body = src[i:i + 1500]
        self.assertIn('"gateway_kernel": host._gateway_kernel()', body)

    def test_startup_prints_the_kernel(self):
        src = Path(app.__file__).read_text(encoding="utf-8", errors="replace")
        i = src.index('if __name__ == "__main__":')
        body = src[i:i + 1200]
        self.assertIn("网关内核", body, "启动日志要明确打印内核来源")


if __name__ == "__main__":
    unittest.main()
