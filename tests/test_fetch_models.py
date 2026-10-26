"""scripts/fetch_models.py 的完整性判据。

为什么单拎出来测：这套判据存在的理由就是"目录存在 / 大小达标 ≠ 模型可用"。
本机 2026-10-21 00:12 那份 228MB 的 SheetSage2 权重就是靠**头部结构**验出来的
（头部声明 228,738,564 字节，实际也正好 228,738,564），只看"超过 100MB 就算齐"
的话，一个被掐断的下载会一路装进推理、在加载时才炸。

    py312\\python.exe -m unittest discover -s tests -p "test_fetch_models.py" -v
"""
from __future__ import annotations

import http.server
import json
import re
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_models  # noqa: E402


def make_safetensors(path: Path, truncate: int = 0) -> int:
    header = {"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
    h = json.dumps(header).encode("utf-8")
    blob = struct.pack("<Q", len(h)) + h + b"\x00\x00\x80?"  # 4 字节 float
    if truncate:
        blob = blob[:-truncate]
    path.write_bytes(blob)
    return len(blob)


class SafetensorsIntegrity(unittest.TestCase):
    def test_complete_file_passes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "model.safetensors"
            n = make_safetensors(p)
            ok, why = fetch_models.verify_one(p, 1, False)
            self.assertTrue(ok, why)
            self.assertEqual(n, p.stat().st_size)

    def test_truncated_file_is_rejected_even_when_it_clears_min_size(self):
        """把"只看 min_size"改回来，这条立刻红——这正是它要防的假绿灯。"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "model.safetensors"
            make_safetensors(p, truncate=2)
            ok, why = fetch_models.verify_one(p, 1, False)  # min_size 故意设成 1
            self.assertFalse(ok, "截断的文件必须被拦下，不能因为超过 min_size 就放行")
            self.assertIn("截断", why)

    def test_broken_header_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "model.safetensors"
            p.write_bytes(b"\xff" * 32)
            ok, why = fetch_models.verify_one(p, 1, False)
            self.assertFalse(ok)


class GgufAndSize(unittest.TestCase):
    def test_gguf_magic_is_checked(self):
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "a.gguf"
            good.write_bytes(b"GGUF" + b"\x00" * 60)
            bad = Path(d) / "b.gguf"
            bad.write_bytes(b"XXXX" + b"\x00" * 60)
            self.assertTrue(fetch_models.verify_one(good, 8, True)[0])
            ok, why = fetch_models.verify_one(bad, 8, True)
            self.assertFalse(ok)
            self.assertIn("GGUF", why)

    def test_min_size_still_guards(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.gguf"
            p.write_bytes(b"GGUF")
            ok, why = fetch_models.verify_one(p, 1_000_000, True)
            self.assertFalse(ok)
            self.assertIn("不完整", why)

    def test_missing_file(self):
        ok, why = fetch_models.verify_one(Path("C:/definitely/not/here.gguf"), 1, True)
        self.assertFalse(ok)
        self.assertEqual(why, "缺失")


class ManifestMatchesApp(unittest.TestCase):
    """下载清单的落点与最小字节数必须和 model_files.py 的完整性清单一致，否则两边会各说各话。"""

    def test_transcription_weight_matches_app_manifest(self):
        repo, rel, dest, min_size, is_gguf = fetch_models.SHEETSAGE
        self.assertEqual(repo, "m-a-p/SheetSage2")
        self.assertEqual(dest, "checkpoints/model.safetensors")
        app_src = (ROOT / "src" / "routers" / "model_files.py").read_text(encoding="utf-8")
        self.assertIn('"checkpoints/model.safetensors", 100_000_000', app_src,
                      "model_files.py 的 MODEL_MANIFEST_MAIN 改了就要同步改这里")

    def test_main_quant_lands_where_server_json_points(self):
        _, dest_dir, _ = fetch_models.QUANTS["q4km"]
        cfg = json.loads((ROOT / "server.json.example").read_text(encoding="utf-8"))
        path = cfg["models"][0]["path"]
        self.assertIn(dest_dir, path, "server.json 指向的目录必须与下载器的落地目录一致")


class ContentRange(unittest.TestCase):
    def test_parses_total(self):
        self.assertEqual(fetch_models._parse_content_range("bytes 0-31/100"), (0, 31, 100))

    def test_parses_star_total(self):
        self.assertEqual(fetch_models._parse_content_range("bytes 5-9/*"), (5, 9, None))

    def test_rejects_garbage(self):
        for bad in ("", "bytes", "bytes x-y/1", "items 0-1/2"):
            self.assertIsNone(fetch_models._parse_content_range(bad), bad)


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """mode=good 按请求窗口回；mode=shifted 故意回错位窗口；mode=ignore 忽略 Range。"""

    mode = "good"
    data = bytes(range(64))

    def do_GET(self):  # noqa: N802
        m = re.match(r"bytes=(\d+)-(\d+)", self.headers.get("Range") or "")
        if not m:
            self.send_response(400)
            self.end_headers()
            return
        a, b = int(m.group(1)), int(m.group(2))
        if self.mode == "ignore":
            body = self.data
            self.send_response(200)
        else:
            a2, b2 = (a + 1, b + 1) if self.mode == "shifted" else (a, b)
            body = self.data[a2:b2 + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {a2}-{b2}/{len(self.data)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静音
        pass


class ChunkWindow(unittest.TestCase):
    """分块下载必须核对 Content-Range 窗口（评审 B4）。"""

    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}/f.bin"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.part = Path(self._tmp.name) / "chunk.part"
        _RangeHandler.mode = "good"

    def tearDown(self):
        self._tmp.cleanup()

    def test_good_window_downloads_exact_length(self):
        n = fetch_models.fetch_chunk(self.url, self.part, 0, 8)
        self.assertEqual(n, 8)
        self.assertEqual(self.part.stat().st_size, 8)
        self.assertEqual(self.part.read_bytes(), _RangeHandler.data[:8])

    def test_shifted_window_is_rejected(self):
        """镜像回错位窗口时必须报错，而不是把垃圾拼进去（这条就是 B4 的复现点）。"""
        _RangeHandler.mode = "shifted"
        with self.assertRaises(urllib.error.URLError) as ctx:
            fetch_models.fetch_chunk(self.url, self.part, 0, 8)
        self.assertIn("窗口错位", str(ctx.exception))

    def test_server_ignoring_range_is_rejected(self):
        _RangeHandler.mode = "ignore"
        with self.assertRaises(urllib.error.URLError) as ctx:
            fetch_models.fetch_chunk(self.url, self.part, 0, 8)
        self.assertIn("长度不符", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
