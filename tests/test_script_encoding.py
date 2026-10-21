"""脚本编码规矩的挡回归用例。

这几条规矩全是踩出来的，写在这里是为了下次有人"顺手把文件另存为 UTF-8"时立刻红：

  * `.bat` 必须是 GBK（OEM cp936），且不能在文件中间 chcp。cmd.exe 用控制台自身的
    OEM 码页解析批处理，UTF-8 的 .bat 在 936 下会把一行中文按多字节切半，后果不是
    显示难看而是**命令少跑几条**（实测报「'xxx' 不是内部或外部命令」）；而 `chcp 65001`
    救不了——文件早已按 936 打开，中途换码页只会让字节偏移更错位。
  * `.bat` 里不能出现 GBK 编不出的字符（emoji），否则文件根本写不进去。
  * `.ps1` 必须 UTF-8 with BOM：Windows PowerShell 5.1 读无 BOM 的 UTF-8 会按 ANSI 解，
    而 开放局域网.ps1 的 $RuleName 是防火墙规则的匹配键，解错就"加了删不掉"。
  * `requirements.txt` 必须 UTF-8 with BOM：pip 的 auto_decode 没有 BOM 就按当前区域
    编码解，带中文注释的清单在中文 Windows 上直接 UnicodeDecodeError。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BATS = [
    "启动音乐工作台.bat",
    "停止音乐工作台.bat",
    "一键补齐运行件.bat",
    "scripts/启动音乐工作台.bat",
    "scripts/停止音乐工作台.bat",
    "scripts/启动dsh工作台.bat",
    "scripts/开放局域网.bat",
    "scripts/关闭局域网.bat",
    "scripts/register_protocol.bat",
    "secrets/local_env.example.bat",
    "dsh-plugin/启动AI工作台.bat",
]
PS1 = ["scripts/开放局域网.ps1"]
BOM = b"\xef\xbb\xbf"


def _is_utf8(b: bytes) -> bool:
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


class BatsAreGbk(unittest.TestCase):
    def test_each_bat_decodes_as_gbk(self):
        for rel in BATS:
            p = ROOT / rel
            self.assertTrue(p.is_file(), f"{rel} 不在")
            try:
                p.read_bytes().decode("gbk")
            except UnicodeDecodeError as e:  # noqa: PERF203
                self.fail(f"{rel} 不能按 GBK 解码（项目规矩是 .bat 存 GBK）：{e}")

    def test_each_bat_is_not_utf8(self):
        """GBK 与 UTF-8 只对含中文的文件互斥——纯 ASCII 的 .bat 两者都算对，不报错。"""
        for rel in BATS:
            b = (ROOT / rel).read_bytes()
            try:
                b.decode("gbk")
            except UnicodeDecodeError:
                continue
            if any(ord(c) > 127 for c in b.decode("gbk")):
                self.assertFalse(
                    _is_utf8(b),
                    f"{rel} 现在是 UTF-8：cmd 在 936 控制台下会把中文行切半、"
                    f"命令少跑几条。请存回 GBK（见 .gitattributes 与 README「编码规矩」）",
                )

    def test_no_mid_file_codepage_switch(self):
        """禁的是"换码页"那条命令本身；`for /f ... in ('chcp')` 只是**读**当前码页，合法。"""
        import re
        switch = re.compile(r"^\s*chcp\s+\d+", re.I)
        for rel in BATS:
            t = (ROOT / rel).read_bytes().decode("gbk")
            for i, line in enumerate(t.splitlines(), 1):
                s = line.strip()
                if s.lower().startswith("rem") or s.startswith("for ") or not s:
                    continue
                self.assertIsNone(
                    switch.match(s),
                    f"{rel}:{i} 在批处理中间换了码页——GBK 文件 chcp 65001 会让 cmd 的"
                    f"字节偏移更错位，UTF-8 文件加了也救不了（两条都实测过）",
                )

    def test_python_output_has_a_codepage_guard(self):
        """启动脚本要给 Python 设 PYTHONIOENCODING，否则 print 一个 emoji 就 UnicodeEncodeError。

        实测：download_models.py 成功分支打印「模型下载完成 ✅」，936 码页下直接崩，
        退出码非 0 → 启动脚本以为模型缺失 → 又去重下 2.9GB。
        """
        for rel in ("scripts/启动音乐工作台.bat", "一键补齐运行件.bat"):
            t = (ROOT / rel).read_bytes().decode("gbk")
            self.assertIn("PYTHONIOENCODING", t,
                          f"{rel} 缺 PYTHONIOENCODING 保险（cp<码页>:backslashreplace）")


class BatPortability(unittest.TestCase):
    """.bat 里不许出现 Unix 重定向：cmd 会把 /dev/null 当路径，命令直接不执行（评审 B2）。"""

    def test_no_unix_redirection(self):
        for rel in BATS:
            text = (ROOT / rel).read_bytes().decode("gbk", "replace")
            self.assertNotIn("/dev/null", text,
                             f"{rel} 里出现了 /dev/null：cmd 不认，改成 >nul 2>&1")

    def test_python_encoding_guard_is_validated(self):
        """PYTHONIOENCODING 的码页必须来自纯数字校验后的 OEMCP（评审 T7）。"""
        for rel in ("scripts/启动音乐工作台.bat", "一键补齐运行件.bat"):
            text = (ROOT / rel).read_bytes().decode("gbk", "replace")
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
