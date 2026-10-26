"""统一音频入口：后缀归一、ffmpeg 转 WAV、解码能力边界。

`soundfile` 在本机支持 wav/flac/mp3/ogg(Vorbis)，但读不了 m4a/aac/opus/webm/wma。
音色降噪、ASR、LRC 对齐这些直接走 soundfile 的路径，必须先统一转成 WAV 再喂，
否则会出现“白名单里有这个格式、上传成功、处理时才炸”的假支持。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

AUDIO_EXTS = (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac", ".wma", ".webm")
# 本机 libsndfile 实测可直读的格式；其余先转 WAV
SOUNDFILE_SAFE = (".wav", ".flac", ".mp3", ".ogg")


class AudioFormatError(RuntimeError):
    """音频无法解析/转换。"""


def normalize_suffix(filename: str | None, default: str = ".wav",
                     allowed: tuple[str, ...] = AUDIO_EXTS) -> str:
    ext = Path(str(filename or "")).suffix.lower()
    return ext if ext in allowed else default


def needs_convert(path: str | Path) -> bool:
    return Path(path).suffix.lower() not in SOUNDFILE_SAFE


def ffmpeg_path() -> str | None:
    raw = (os.environ.get("FFMPEG_PATH") or "").strip()
    if raw:
        p = Path(raw)
        if p.is_dir():
            p = p / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if p.is_file():
            return str(p)
    bundled = ROOT / "py312" / "ffmpeg" / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if bundled.is_file():
        return str(bundled)
    return shutil.which("ffmpeg")


def to_wav(src: str | Path, dst: str | Path | None = None, *,
           sample_rate: int | None = None, channels: int | None = None,
           timeout: float = 180.0) -> Path:
    src = Path(src)
    dst = Path(dst) if dst is not None else src.with_suffix(".wav")
    if src.resolve() == dst.resolve():
        return src
    if not src.is_file():
        raise AudioFormatError(f"音频不存在：{src.name}")
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise AudioFormatError("找不到 ffmpeg，无法转换音频格式")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn"]
    if channels:
        cmd += ["-ac", str(int(channels))]
    if sample_rate:
        cmd += ["-ar", str(int(sample_rate))]
    cmd += ["-c:a", "pcm_s16le", str(dst)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        dst.unlink(missing_ok=True)
        raise AudioFormatError("音频解码超时，请换更短的片段") from exc
    if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size <= 44:
        tail = proc.stderr.decode("utf-8", "replace").strip()[-300:]
        dst.unlink(missing_ok=True)
        raise AudioFormatError(f"音频解码失败：{tail or '未知错误'}")
    return dst


def ensure_wav(src: str | Path, *, sample_rate: int | None = None,
               channels: int | None = None) -> Path:
    """需要时把 src 转成同目录 .wav；已是可直读格式则原样返回。"""
    src = Path(src)
    if not needs_convert(src):
        return src
    return to_wav(src, src.with_suffix(".wav"), sample_rate=sample_rate,
                  channels=channels)


def to_wav_bytes(data: bytes, suffix: str = ".m4a", *, sample_rate: int = 16000,
                 channels: int = 1) -> bytes:
    if not data:
        raise AudioFormatError("音频为空")
    ext = suffix if suffix.startswith(".") else f".{suffix}"
    with tempfile.TemporaryDirectory(prefix="yue2_audio_") as tmp:
        src = Path(tmp) / f"in{ext}"
        dst = Path(tmp) / "out.wav"
        src.write_bytes(data)
        to_wav(src, dst, sample_rate=sample_rate, channels=channels)
        return dst.read_bytes()
