# -*- coding: utf-8 -*-
"""测试环境探针：这份导出树（这台机器）装了哪些可选运行件。

为什么需要（issue #4-3）：`cpp/`、`runtime/`、`py312/` 全在 `.gitignore` 里。
干净 clone / CI 上跑 `unittest discover` 会得到 24 红 + 5 错 —— **全部是环境缺件**，
一条逻辑错都没有。红绿混在一起时，新贡献者分不清"我改坏了"和"我没装运行件"，
于是要么瞎改代码，要么干脆不信测试。

判据一律"文件真的在"：环境变量能把机器说成装好了，文件不会。
探针在任何 `import` 之前算好，因为 `unittest.skipUnless` 需要在收集阶段就有结论。

用法（模块级；用例根本不收集，比在 setUp 里 skip 省时间）::

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _env import HAS_RVC_ENV

    @unittest.skipUnless(HAS_RVC_ENV, SKIP_RVC)
    class RvcConvert(unittest.TestCase):
        ...

跑全量时若看到一串 `skipped`，检查是不是缺运行件：双击「一键补齐运行件.bat」。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: RVC 换声/训练专用解释器（不入库，由「一键补齐运行件」建）
RVC_PY = ROOT / "py312" / "python.exe"
RVC_DIR = ROOT / "runtime" / "rvc"

#: PyMSS 人声分离的随包模型目录（随 runtime/rvc 一起下发）
PYMSS_CATALOG = RVC_DIR / "tools" / "pymss" / "resources" / "model_catalog.json"

#: 引擎二进制与它启动时读的配置（cpp/ 整目录不入库）
AUDIOCPP_BIN = ROOT / "cpp" / "audiocpp_server.exe"
SERVER_JSON = ROOT / "cpp" / "server.json"

#: 换声/训练端点会先看 `py312/python.exe` 在不在，缺了直接 503；缺它时这些用例
#: 断言 200/400/413 全会拿到 503，红得毫无意义。
HAS_RVC_ENV = RVC_PY.is_file() and (RVC_DIR / "infer" / "cli.py").is_file()

#: 只有 py312 在（有些用例只借它起子进程验证 transformers 缓存口径，不需要 rvc 代码）
HAS_PY312 = RVC_PY.is_file()

#: 随包的模型清单：`model_catalog.json` 缺失时"这些档位受支持"根本无从断言
HAS_PYMSS = PYMSS_CATALOG.is_file()

#: 引擎是否部署（main.py 的 shim 在缺 cpp/model_specs 时按设计拒绝加载，已有自己的 SkipTest）
HAS_ENGINE = AUDIOCPP_BIN.is_file()
HAS_SERVER_JSON = SERVER_JSON.is_file()

#: Key 来自环境变量时，任何"清除后应变为未配置"的断言都不成立（issue #4-2）。
#: 相关用例必须 monkeypatch.delenv 隔离，而不是跳过 —— 这条要测的正是文件路径。
HAS_DEEPSEEK_ENV = bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())


#: 统一的跳过理由（写清"缺什么"和"怎么补"，别让人猜）
SKIP_RVC = ("缺 runtime/rvc 或 py312/python.exe（都不入库）；"
            "双击「一键补齐运行件.bat」装好后再跑这一组")
SKIP_PYMSS = (f"缺 {PYMSS_CATALOG.relative_to(ROOT).as_posix()}（随 runtime/rvc 下发）")
SKIP_PY312 = "缺 py312/python.exe（不入库，由「一键补齐运行件」建）"
SKIP_ENGINE = "缺 cpp/audiocpp_server.exe（引擎未部署）"
SKIP_SERVER_JSON = "缺 cpp/server.json（引擎未部署/未初始化）"
