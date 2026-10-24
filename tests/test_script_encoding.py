"""脚本编码规矩的挡回归用例。

这几条规矩全是踩出来的，写在这里是为了下次有人"顺手改一下"时立刻红：

  * **`.bat` 是 UTF-8（无 BOM）+ CRLF，且它 `call` 的每个 `.bat` 文件名必须是纯 ASCII。**
    这是 2026-10-24 那次"双击启动脚本毫无反应"的根因：本机开了 Windows 的
    「Beta: 使用 Unicode UTF-8 提供全球语言支持」，OEMCP=65001，而脚本当时是 GBK 字节。
    cmd 按 UTF-8 解析 GBK → 中文文件名变成 `锟斤拷` → `call "...\\scripts\\启动音乐工作台.bat"`
    直接报 "is not recognized as an internal or external command" → 窗口一闪而过，
    网关/dsh/引擎一个都没起。**改成 UTF-8 内容 + ASCII 调用目标后，65001 与 936 下都能跑**
    （936 下只是文案花，路径不受影响）。
  * `.bat` 里不能有 BOM：cmd 会把 BOM 当命令的一部分执行。
  * `.bat` 里不能在文件中间 chcp：字节偏移会错位（两条都实测过）。
  * `.ps1` 必须 UTF-8 with BOM：Windows PowerShell 5.1 读无 BOM 的 UTF-8 会按 ANSI 解，
    而 开放局域网.ps1 的 $RuleName 是防火墙规则的匹配键，解错就"加了删不掉"。
  * `requirements.txt` 必须 UTF-8 with BOM：pip 的 auto_decode 没有 BOM 就按当前区域
    编码解，带中文注释的清单在中文 Windows 上直接 UnicodeDecodeError。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BATS = [
    # 入口（中文名，用户双击的就是它们；内容 UTF-8，调用目标是 ASCII）
    "启动音乐工作台.bat",
    "停止音乐工作台.bat",
    "一键补齐运行件.bat",
    # 逻辑脚本（ASCII 名，码页无关）
    "scripts/start_lab.bat",
    "scripts/start_dsh.bat",
    "scripts/stop_lab.bat",
    # 兼容壳（中文名，内容只有 ASCII 的一行 call）
    "scripts/启动音乐工作台.bat",
    "scripts/停止音乐工作台.bat",
    "scripts/启动dsh工作台.bat",
    # 旁路脚本
    "scripts/开放局域网.bat",
    "scripts/关闭局域网.bat",
    "scripts/register_protocol.bat",
    "secrets/local_env.example.bat",
    "dsh-plugin/启动AI工作台.bat",
]

#: 码页无关的调用目标：这些文件被 call 时不会因为代码页而找不到
ASCII_TARGETS = {
    "scripts/start_lab.bat",
    "scripts/start_dsh.bat",
    "scripts/stop_lab.bat",
}

PS1 = ["scripts/开放局域网.ps1"]
BOM = b"\xef\xbb\xbf"
NON_ASCII = re.compile(r"[^\x00-\x7f]")


def _read(rel: str) -> str:
    return (ROOT / rel).read_bytes().decode("utf-8", "replace")


class BatsAreUtf8AndCodepageSafe(unittest.TestCase):
    def test_each_bat_decodes_as_utf8(self):
        for rel in BATS:
            p = ROOT / rel
            self.assertTrue(p.is_file(), f"{rel} 不在")
            try:
                p.read_bytes().decode("utf-8")
            except UnicodeDecodeError as e:  # noqa: PERF203
                self.fail(f"{rel} 不是 UTF-8：本机 OEMCP=65001，GBK 字节会被解成乱码，"
                          f"中文文件名随之失效（见文件头注释）：{e}")

    def test_no_bom(self):
        for rel in BATS:
            b = (ROOT / rel).read_bytes()
            self.assertFalse(b.startswith(BOM),
                             f"{rel} 带 UTF-8 BOM：cmd 会把 BOM 当成命令内容，"
                             f"首行 `@echo off` 失效并打印怪字符")

    def test_crlf(self):
        for rel in BATS:
            b = (ROOT / rel).read_bytes()
            self.assertIn(b"\r\n", b, f"{rel} 必须是 CRLF 行尾（cmd 对 LF 容错但不稳）")

    def test_call_targets_are_ascii(self):
        """所有 `call "xxx.bat"` 的目标文件名必须是纯 ASCII —— 这是本次事故的正解。

        `%~dp0` / 环境变量展开前先剥掉，只看字面量部分；带中文的 `.bat` 目标一律判红。
        """
        for rel in BATS:
            for i, line in enumerate(_read(rel).splitlines(), 1):
                s = line.strip()
                if not s or s.lower().startswith("rem"):
                    continue
                for target in re.findall(r'call\s+"([^"]+)"', s, re.I):
                    literal = re.sub(r"%~dp0|%~f0|%[^%]+%", "", target)
                    if not re.search(r"\.bat\s*$", literal, re.I):
                        continue
                    self.assertIsNone(
                        NON_ASCII.search(literal),
                        f"{rel}:{i} 用中文文件名 `{literal}` 做 call 目标。"
                        f"OEMCP=65001 时它会被解成乱码、报「不是内部或外部命令」，"
                        f"双击脚本一闪而过（2026-10-24 实测）。请改成 scripts\\start_lab.bat "
                        f"这类 ASCII 名。",
                    )
                for target in re.findall(r'call\s+([^\s"]+\.bat)', s, re.I):
                    literal = re.sub(r"%~dp0|%~f0|%[^%]+%", "", target)
                    self.assertIsNone(NON_ASCII.search(literal),
                                      f"{rel}:{i} call 目标 `{literal}` 含非 ASCII 字符")

    def test_entry_points_point_at_ascii_scripts(self):
        """两个用户双击的入口必须 call ASCII 逻辑脚本（中文壳不能被它们直接调）。"""
        for rel, want in (("启动音乐工作台.bat", "scripts\\start_lab.bat"),
                          ("停止音乐工作台.bat", "scripts\\stop_lab.bat")):
            text = _read(rel)
            self.assertIn("call \"%~dp0" + want + "\"", text,
                          f"{rel} 没有 call {want}：入口链又回到中文文件名了")

    def test_compat_wrappers_only_call_ascii(self):
        """中文名兼容壳本身必须只剩 ASCII 内容（它就是在乱码环境下被 call 的）。"""
        for rel, target in (("scripts/启动音乐工作台.bat", "start_lab.bat"),
                            ("scripts/启动dsh工作台.bat", "start_dsh.bat"),
                            ("scripts/停止音乐工作台.bat", "stop_lab.bat")):
            text = _read(rel)
            body = [ln for ln in text.splitlines()
                    if ln.strip() and not ln.strip().lower().startswith("rem")]
            self.assertEqual(len(body), 2, f"{rel} 兼容壳里不该有别的命令：{body}")
            self.assertIn(f'call "%~dp0{target}"', body[-1],
                          f"{rel} 必须 call ASCII 逻辑脚本 {target}")
            for ln in body:
                self.assertIsNone(NON_ASCII.search(ln),
                                  f"{rel} 兼容壳含非 ASCII 命令：{ln!r}")

    def test_no_mid_file_codepage_switch(self):
        """禁的是"换码页"那条命令本身；`for /f ... in ('chcp')` 只是**读**当前码页，合法。"""
        switch = re.compile(r"^\s*chcp\s+\d+", re.I)
        for rel in BATS:
            for i, line in enumerate(_read(rel).splitlines(), 1):
                s = line.strip()
                if s.lower().startswith("rem") or s.startswith("for ") or not s:
                    continue
                self.assertIsNone(
                    switch.match(s),
                    f"{rel}:{i} 在批处理中间换了码页——cmd 已按打开时的码页解完整行，"
                    f"中途切换只会让后续字节错位（实测过）",
                )

    def test_python_output_has_a_codepage_guard(self):
        """启动脚本要给 Python 设 PYTHONIOENCODING，否则 print 一个 emoji 就 UnicodeEncodeError。

        实测：download_models.py 成功分支打印「模型下载完成 ✅」，936 码页下直接崩，
        退出码非 0 → 启动脚本以为模型缺失 → 又去重下 2.9GB。
        """
        for rel in ("scripts/start_lab.bat", "一键补齐运行件.bat"):
            self.assertIn("PYTHONIOENCODING", _read(rel),
                          f"{rel} 缺 PYTHONIOENCODING 保险（cp<码页>:backslashreplace）")


class BatPortability(unittest.TestCase):
    """.bat 里不许出现 Unix 重定向：cmd 会把 /dev/null 当路径，命令直接不执行（评审 B2）。"""

    def test_no_unix_redirection(self):
        for rel in BATS:
            self.assertNotIn("/dev/null", _read(rel),
                             f"{rel} 里出现了 /dev/null：cmd 不认，改成 >nul 2>&1")

    def test_python_encoding_guard_is_validated(self):
        """PYTHONIOENCODING 的码页必须来自纯数字校验后的 OEMCP（评审 T7）。"""
        for rel in ("scripts/start_lab.bat", "一键补齐运行件.bat"):
            text = _read(rel)
            if "PYTHONIOENCODING" not in text:
                continue
            self.assertIn("findstr /r \"^[0-9][0-9]*$\"", text,
                          f"{rel} 的 OEMCP 没做纯数字校验：畸形值会让 Python 初始化 Fatal")


class Ps1HasBom(unittest.TestCase):
    def test_ps1_files_start_with_utf8_bom(self):
        for rel in PS1:
            b = (ROOT / rel).read_bytes()
            self.assertTrue(b.startswith(BOM),
                            f"{rel} 没有 UTF-8 BOM：Windows PowerShell 5.1 会按 ANSI 解它，"
                            f"防火墙规则名里的中文会解错，出现「加了删不掉」")


class RequirementsHasBom(unittest.TestCase):
    def test_requirements_txt_starts_with_bom(self):
        b = (ROOT / "requirements.txt").read_bytes()
        self.assertTrue(b.startswith(BOM),
                        "requirements.txt 没有 BOM：pip 的 auto_decode 在中文 Windows 上"
                        "按 cp936 解这份带中文注释的清单，会直接 UnicodeDecodeError")
        text = b.decode("utf-8-sig")
        self.assertIn("fastapi==", text, "清单得真的钉住版本")
        self.assertIn("starlette==", text,
                      "fastapi 与 starlette 必须成对钉：只升一个会出现"
                      "「空 filename 的 file part 被当成合法上传」那种行为漂移")


class GatewayKernelIsImportable(unittest.TestCase):
    """闭源内核缺席时，仓库自带的开源 main.py 必须能顶上——这是「clone 就能跑」的前提。"""

    def test_open_gateway_module_exists(self):
        self.assertTrue((ROOT / "main.py").is_file(),
                        "缺 main.py：app.py 的 `import main` 会直接失败，"
                        "整个工作台在没有 main.cp312-win_amd64.pyd 的机器上起不来")

    def test_app_still_imports_main(self):
        # 只确认这层耦合还在（改天有人把 app.py 的 import main 删了，这个用例该提醒他）
        src = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("import main", src)
        self.assertIn("app = main.app", src)


if __name__ == "__main__":
    sys.exit(unittest.main())
