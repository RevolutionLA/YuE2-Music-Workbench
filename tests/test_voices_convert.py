"""音色 ASR/降噪入口：m4a 必须先转 WAV 再交给只认 soundfile 的模块。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402
import audio_io  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FFMPEG = audio_io.ffmpeg_path()


def _m4a_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "probe.m4a"
        subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             "-ac", "1", "-ar", "44100", "-c:a", "aac", str(dst)],
            check=True, capture_output=True)
        return dst.read_bytes()


@unittest.skipUnless(FFMPEG, "缺 ffmpeg")
class VoicesConvertBeforeDecode(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app, base_url="http://127.0.0.1")
        self.m4a = _m4a_bytes()

    def test_denoise_receives_wav(self):
        seen = {}

        def fake(data, filename):
            seen["data"], seen["name"] = data, filename
            return b"RIFF" + b"\0" * 64

        with mock.patch.object(app.denoise, "denoise_wav_bytes", fake):
            r = self.client.post(
                "/api/voices/denoise",
                files={"audio": ("ref.m4a", self.m4a, "audio/mp4")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(seen["data"].startswith(b"RIFF"), seen.get("name"))
        self.assertTrue(seen["name"].endswith(".wav"), seen["name"])

    def test_transcribe_receives_wav(self):
        seen = {}

        def fake(data, filename):
            seen["data"], seen["name"] = data, filename
            return "la la"

        with mock.patch.object(app.asr, "recognize_wav_bytes", fake):
            r = self.client.post(
                "/api/voices/transcribe",
                files={"audio": ("ref.m4a", self.m4a, "audio/mp4")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(seen["data"].startswith(b"RIFF"), seen.get("name"))
        self.assertTrue(seen["name"].endswith(".wav"), seen["name"])
        self.assertEqual(r.json()["text"], "la la")


if __name__ == "__main__":
    unittest.main()
