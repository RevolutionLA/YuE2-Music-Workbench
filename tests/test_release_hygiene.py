"""发布卫生检查器的用例：用临时目录模拟干净包与各种污染。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_release_hygiene", ROOT / "scripts" / "check_release_hygiene.py")
hygiene = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hygiene)


def _root(tmp: str) -> Path:
    root = Path(tmp)
    (root / "requirements.txt").write_bytes(
        b"\xef\xbb\xbffastapi==1.0\nhttpx==1.0\n")
    (root / "ports.json").write_text(
        json.dumps({"gateway": 7863, "dsh": 3081, "audiocpp": 8080}), encoding="utf-8")
    (root / "CHANGELOG.md").write_text(
        "# 更新日志\n\n## [v10.26.0] - 2026-10-26\n", encoding="utf-8")
    (root / "static").mkdir()
    (root / "static" / "index.html").write_bytes(b"x" * hygiene.MIN_UI_BYTES)
    (root / "web").mkdir()
    for name in ("index.html", "app.css", "app.js"):
        (root / "web" / name).write_text("x", encoding="utf-8")
    (root / "README.md").write_text("# x", encoding="utf-8")
    (root / "LICENSE").write_text("x", encoding="utf-8")
    (root / "secrets").mkdir()
    (root / "secrets" / "local_env.example.bat").write_text(
        "rem set DEEPSEEK_API_KEY=sk-xxxxxxxx\n", encoding="utf-8")
    return root


class Hygiene(unittest.TestCase):
    def test_clean_tree_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            failures, _ = hygiene.check(_root(tmp), tracked=[])
            self.assertEqual(failures, [])

    def test_example_env_is_allowed(self):
        """example 文件是给用户看的模板，必须能进仓库；真实 local_env.bat 才判红。"""
        with tempfile.TemporaryDirectory() as tmp:
            failures, _ = hygiene.check(
                _root(tmp), tracked=["secrets/local_env.example.bat"])
            self.assertEqual(failures, [])

    def test_compiled_kernel_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            (root / "main.cp312-win_amd64.pyd").write_bytes(b"x")
            failures, _ = hygiene.check(root, tracked=[])
            self.assertTrue(any("编译内核" in f for f in failures), failures)

    def test_allow_pyd_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            (root / "main.cp312-win_amd64.pyd").write_bytes(b"x")
            failures, warnings = hygiene.check(root, tracked=[], allow_pyd=True)
            self.assertEqual(failures, [])
            self.assertTrue(warnings)

    def test_tracked_secret_and_runtime_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            failures, _ = hygiene.check(
                root, tracked=["secrets/local_env.bat", "cpp/server.json",
                               "runtime/data/x.json", "__pycache__/a.pyc"])
            text = " | ".join(failures)
            self.assertIn("secrets/local_env.bat", text)
            self.assertIn("cpp/server.json", text)
            self.assertIn("runtime/data/x.json", text)
            self.assertIn("__pycache__/a.pyc", text)

    def test_tracked_weights_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            failures, _ = hygiene.check(_root(tmp), tracked=["model/a.gguf", "model/b.pth"])
            text = " | ".join(failures)
            self.assertIn("a.gguf", text)
            self.assertIn("b.pth", text)

    def test_real_secret_in_tracked_file_fails_without_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            leak = root / "secrets" / "leak.bat"
            leak.write_text("set DEEPSEEK_API_KEY=sk-" + "a" * 30 + "\n", encoding="utf-8")
            failures, _ = hygiene.check(root, tracked=["secrets/leak.bat"])
            self.assertTrue(any("疑似真实密钥" in f for f in failures), failures)
            self.assertNotIn("a" * 30, " | ".join(failures))

    def test_access_token_in_tracked_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            leak = root / "secrets" / "tok.bat"
            leak.write_text("set YUE2_ACCESS_TOKEN=" + "b" * 20 + "\n", encoding="utf-8")
            failures, _ = hygiene.check(root, tracked=["secrets/tok.bat"])
            self.assertTrue(any("疑似真实密钥" in f for f in failures), failures)

    def test_unpinned_or_missing_bom_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            failures, _ = hygiene.check(root, tracked=[])
            text = " | ".join(failures)
            self.assertIn("UTF-8 BOM", text)
            self.assertIn("未钉版本", text)

    def test_bad_ports_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            (root / "ports.json").write_text(
                json.dumps({"gateway": 7863, "dsh": 7863, "audiocpp": 99999}),
                encoding="utf-8")
            failures, _ = hygiene.check(root, tracked=[])
            self.assertTrue(any("端口" in f for f in failures), failures)

    def test_missing_changelog_header_and_small_ui_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            (root / "CHANGELOG.md").write_text("# 没有版本\n", encoding="utf-8")
            (root / "static" / "index.html").write_bytes(b"x")
            failures, _ = hygiene.check(root, tracked=[])
            text = " | ".join(failures)
            self.assertIn("版本标题", text)
            self.assertIn("过小", text)

    def test_release_rejects_local_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            (root / "secrets" / "local_env.bat").write_text("set X=1\n", encoding="utf-8")
            failures, _ = hygiene.check(root, tracked=[], release=True)
            self.assertTrue(any("本机环境文件" in f for f in failures), failures)

    def test_large_tracked_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _root(tmp)
            (root / "big.txt").write_bytes(b"x" * 100)
            failures, _ = hygiene.check(root, tracked=["big.txt"], max_tracked_bytes=10)
            self.assertTrue(any("超过" in f for f in failures), failures)


if __name__ == "__main__":
    unittest.main()
