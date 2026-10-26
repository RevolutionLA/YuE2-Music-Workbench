"""冒烟脚本的纯函数用例：不联网、不跑引擎。"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("smoke_e2e", ROOT / "scripts" / "smoke_e2e.py")
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


class SmokeHelpers(unittest.TestCase):
    def test_wav_header(self):
        self.assertTrue(smoke._wav_ok(b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 64))
        self.assertFalse(smoke._wav_ok(b"ID3" + b"\x00" * 128))
        self.assertFalse(smoke._wav_ok(b"RIFF"))

    def test_tiny_payload_is_cheap_and_deterministic(self):
        payload = smoke._tiny_payload(4, 7)
        self.assertEqual(payload["cot"], "off")
        self.assertEqual(payload["num_inference_steps"], 4)
        self.assertEqual(payload["seed"], 7)
        self.assertEqual(payload["model"], "yue2")
        self.assertTrue(payload["style"])
        self.assertEqual(smoke._tiny_payload(0, 7)["num_inference_steps"], 1)

    def test_base_url_defaults_to_port_table(self):
        self.assertTrue(smoke._base_url(None).startswith("http://127.0.0.1:"))
        self.assertEqual(smoke._base_url("http://x:1/"), "http://x:1")

    def test_pick_lrc_item_prefers_done_with_lyrics(self):
        items = [
            {"id": "a", "status": "error", "lyrics": "x"},
            {"id": "b", "status": "done", "kind": "separate", "lyrics": "x"},
            {"id": "c", "status": "done", "lyrics": ""},
            {"id": "d", "status": "done", "lyrics": "la", "task_name": "歌"},
        ]
        self.assertEqual(smoke._pick_lrc_item(items), ("d", "la", "歌"))
        self.assertIsNone(smoke._pick_lrc_item([]))
        self.assertIsNone(smoke._pick_lrc_item(None))

    def test_lrc_timeline_detection(self):
        self.assertTrue(smoke._lrc_has_timeline("[00:21.45]迟来的雨季"))
        self.assertTrue(smoke._lrc_has_timeline("[Verse 1]\n[01:02.03]歌词"))
        self.assertFalse(smoke._lrc_has_timeline("[Verse 1]\n只有段标"))
        self.assertFalse(smoke._lrc_has_timeline(""))


if __name__ == "__main__":
    unittest.main()
