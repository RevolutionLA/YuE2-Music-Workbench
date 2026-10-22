"""生成阶段进度（AR / NAR 第几步 / VAE）的解析回归。

引擎没有 HTTP 进度回调，app.py 从引擎日志的 TRACE/TIMING 行判断阶段；
NAR 每步不写日志，步数是估算值，UI 必须标“约”。这里锁住解析与估算的边界。

    py312\\python.exe -m unittest discover -s tests -p "test_engine_progress.py" -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
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

for _name in ("voices", "asr", "denoise", "lrc"):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except Exception:
            sys.modules[_name] = types.ModuleType(_name)

import app  # noqa: E402


def _ts(seconds_ago: int) -> str:
    return (datetime.now() - timedelta(seconds=seconds_ago)).strftime("%Y%m%d-%H%M%S")


class EngineProgress(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="yue2-progress-")
        self.path = Path(self._tmp.name) / "audiocpp_server.log"
        app._PROGRESS_CACHE["key"] = None   # 不跨用例命中缓存

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, *lines: str) -> None:
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def req(seed=123, steps=8, frames=800, ago=120) -> str:
        return (f"[TRACE ts={_ts(ago)}] yue2.request seed={seed} cot=full stop_after=audio "
                f"guidance_scale=1 num_inference_steps={steps} context=24576 abc=provided "
                f"nar_noise=generated semantic_prefix_frames={frames}")

    def test_ar_stage(self):
        self.write(self.req(), f"[TIMING ts={_ts(100)}] yue2.ar.weights.buffer_mb 1579.0")
        p = app._engine_progress(self.path, seed=123)
        self.assertEqual(p["stage"], "ar")
        self.assertIn("AR", p["label"])

    def test_nar_step_is_estimated_and_labelled(self):
        # 800 帧 × 8 步、每帧步 65ms → 每步约 52 秒；120 秒前开始 → 约第 3 步
        self.write(self.req(), f"[TIMING ts={_ts(120)}] yue2.nar.graph.build_ms 3.0",
                   "[TIMING ts=20261022-100002] yue2.nar.synthesize.codec_tokens 800")
        p = app._engine_progress(self.path, seed=123)
        self.assertEqual(p["stage"], "nar")
        self.assertEqual(p["steps"], 8)
        self.assertGreaterEqual(p["step"], 2)
        self.assertLessEqual(p["step"], 4)
        self.assertIn("约", p["label"], "估出来的步数必须标约，不能冒充精确进度")

    def test_vae_wins_over_nar(self):
        self.write(self.req(), f"[TIMING ts={_ts(60)}] yue2.nar.graph.build_ms 3.0",
                   f"[TIMING ts={_ts(10)}] framework.oobleck_audio_vae.decode.total_ms 8414")
        p = app._engine_progress(self.path, seed=123)
        self.assertEqual(p["stage"], "vae")
        self.assertIn("VAE", p["label"])

    def test_seed_mismatch_returns_none(self):
        self.write(self.req(seed=123))
        self.assertIsNone(app._engine_progress(self.path, seed=999),
                          "日志里是上一首的 seed：不能把旧阶段画到新任务头上")

    def test_missing_log_is_none(self):
        self.assertIsNone(app._engine_progress(Path(self._tmp.name) / "nope.log", seed=1))

    def test_nar_done_before_vae(self):
        self.write(self.req(), f"[TIMING ts={_ts(20)}] yue2.nar.graph.build_ms 3.0",
                   f"[TIMING ts={_ts(5)}] yue2.nar.synthesize.total_ms 1000")
        p = app._engine_progress(self.path, seed=123)
        self.assertEqual(p["stage"], "nar_done")
        self.assertIn("准备 VAE", p["label"])


if __name__ == "__main__":
    unittest.main()
