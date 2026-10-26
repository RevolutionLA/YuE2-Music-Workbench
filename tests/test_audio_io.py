"""统一音频入口：后缀归一 + 真实 ffmpeg 转 WAV（无 ffmpeg 则跳过转换用例）。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import audio_io  # noqa: E402

FFMPEG = audio_io.ffmpeg_path()


def _make(ext: str, dst: Path, codec: str) -> None:
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-ac", "1", "-ar", "44100", "-c:a", codec, str(dst)],
        check=True, capture_output=True)


class Suffixes(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(audio_io.normalize_suffix("A.MP3"), ".mp3")
        self.assertEqual(audio_io.normalize_suffix("a.m4a"), ".m4a")
        self.assertEqual(audio_io.normalize_suffix("a.xyz"), ".wav")
        self.assertEqual(audio_io.normalize_suffix(None), ".wav")
        self.assertEqual(audio_io.normalize_suffix("a.webm"), ".webm")
        self.assertIn(".webm", audio_io.AUDIO_EXTS)
        self.assertIn(".ogg", audio_io.SOUNDFILE_SAFE)

    def test_needs_convert(self):
        self.assertFalse(audio_io.needs_convert("a.wav"))
        self.assertFalse(audio_io.needs_convert("a.mp3"))
        self.assertTrue(audio_io.needs_convert("a.m4a"))
        self.assertTrue(audio_io.needs_convert("a.webm"))


@unittest.skipUnless(FFMPEG, "缺 ffmpeg")
class Conversion(unittest.TestCase):
    def test_m4a_to_16k_mono_wav(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.m4a"
            _make("m4a", src, "aac")
            dst = audio_io.to_wav(src, Path(tmp) / "a.wav", sample_rate=16000, channels=1)
            import soundfile as sf
            info = sf.info(str(dst))
            self.assertEqual(info.samplerate, 16000)
            self.assertEqual(info.channels, 1)
            self.assertGreater(info.frames, 1000)

    def test_ensure_wav_skips_safe_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.wav"
            src.write_bytes(b"RIFF" + b"\0" * 100)
            self.assertEqual(audio_io.ensure_wav(src), src)

    def test_ensure_wav_converts_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.m4a"
            _make("m4a", src, "aac")
            dst = audio_io.ensure_wav(src)
            self.assertEqual(dst.suffix, ".wav")
            self.assertTrue(src.is_file())
            self.assertTrue(dst.is_file() and dst.stat().st_size > 44)

    def test_to_wav_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a.m4a"
            _make("m4a", src, "aac")
            wav = audio_io.to_wav_bytes(src.read_bytes(), ".m4a", sample_rate=16000, channels=1)
            self.assertTrue(wav.startswith(b"RIFF"))
            self.assertEqual(wav[8:12], b"WAVE")

    def test_invalid_audio_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "bad.m4a"
            src.write_bytes(b"not audio")
            with self.assertRaises(audio_io.AudioFormatError):
                audio_io.to_wav(src, Path(tmp) / "bad.wav")


if __name__ == "__main__":
    unittest.main()
