"""P2 守门：async 处理器里不许直接做大文件同步 IO。"""

from __future__ import annotations

import ast
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402


class FakeUpload:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    async def read(self, _size: int = -1) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class UploadUsesAiofiles(unittest.TestCase):
    def test_stream_upload_writes_bytes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "x.wav"
            n = asyncio.run(app._stream_upload_to(
                FakeUpload([b"abc", b"defg", b""]), dest, 1024, "测试"))
            self.assertEqual(n, 7)
            self.assertEqual(dest.read_bytes(), b"abcdefg")

    def test_source_uses_aiofiles_not_builtin_open(self):
        import inspect
        src = inspect.getsource(app._stream_upload_to)
        self.assertIn("aiofiles.open", src)
        self.assertNotIn("with open(tmp", src)


class NoBlockingOpenInAsync(unittest.TestCase):
    def test_no_builtin_open_inside_async_functions(self):
        """大文件写盘必须走 aiofiles/线程池，避免一个上传冻住整个网关。"""
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                        and sub.func.id == "open":
                    offenders.append(f"{node.name}:{sub.lineno}")
        self.assertEqual(offenders, [], f"async def 里还有同步 open()：{offenders}")


if __name__ == "__main__":
    unittest.main()
