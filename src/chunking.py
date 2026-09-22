"""Gateway-side sentence split and WAV stitch."""

from __future__ import annotations

import io
import math
import os
import re
import subprocess
import wave
from array import array

DEFAULT_MAX_N = 100
DEFAULT_FADE_IN_MS = 320.0
DEFAULT_FADE_OUT_MS = 200.0
DEFAULT_JOIN_GAP_MS = 240.0
DEFAULT_TRIM_DB = -38.0
DEFAULT_PAD_HEAD_MS = 160.0
DEFAULT_PAD_TAIL_MS = 80.0
DEFAULT_HEAD_PEAK = 0.55

_SENTENCE_END = re.compile(r"(?<=[。！？；!?;])")
_CLAUSE_END = re.compile(r"(?<=[，,、])")
_SPACES = re.compile(r"[ \t\r\f\v]+")
_CJK_SPACE_CJK = re.compile(
    r"([\u3400-\u9fff\uF900-\uFAFF])[ \t\u3000]+([\u3400-\u9fff\uF900-\uFAFF])"
)


def glue_cjk_spaces(text: str) -> str:
    """Turn '第一章 借宿' into '第一章，借宿' so 借 is not utterance-initial."""
    if not text:
        return text
    out = text
    for _ in range(8):
        nxt = _CJK_SPACE_CJK.sub(r"\1，\2", out)
        if nxt == out:
            break
        out = nxt
    return out


def split_text(text: str, max_n: int = DEFAULT_MAX_N) -> list[str]:
    cleaned = glue_cjk_spaces(_SPACES.sub(" ", text)).strip()
    if not cleaned:
        return []
    limit = max(8, int(max_n))
    lines: list[str] = []
    for sent in _SENTENCE_END.split(cleaned):
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) <= limit:
            lines.append(sent)
            continue
        buf = ""
        for piece in _CLAUSE_END.split(sent):
            piece = piece.strip()
            if not piece:
                continue
            if buf and len(buf) + len(piece) > limit:
                lines.append(buf)
                buf = piece
            else:
                buf += piece
        if buf:
            lines.append(buf)
    if not lines:
        return [cleaned]

    packed: list[str] = []
    buf = lines[0]
    for part in lines[1:]:
        if len(buf) + len(part) <= limit:
            buf += part
        else:
            packed.append(buf)
            buf = part
    packed.append(buf)
    return packed


def change_audio_speed(audio_bytes: bytes, speed: float = 1.0) -> bytes:
    rate = float(speed)
    if abs(rate - 1.0) < 0.01:
        return audio_bytes
    if rate < 0.5 or rate > 2.0:
        raise ValueError("speed must be between 0.5 and 2.0")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-filter:a",
        f"atempo={rate:.4f}",
        "-f",
        "wav",
        "pipe:1",
    ]
    kwargs: dict = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(command, **kwargs)
    stdout_data, stderr_data = process.communicate(input=audio_bytes)
    if process.returncode != 0:
        err = stderr_data.decode("utf-8", errors="ignore")
        raise RuntimeError(f"FFmpeg error ({process.returncode}): {err}")
    if not stdout_data:
        raise RuntimeError("FFmpeg returned empty audio")
    return stdout_data


def merge_wavs(
    chunks: list[bytes],
    cross_fade_ms: float = DEFAULT_FADE_OUT_MS,
    fade_in_ms: float = DEFAULT_FADE_IN_MS,
    fade_out_ms: float | None = None,
    trim_db: float = DEFAULT_TRIM_DB,
    pad_ms: float = DEFAULT_PAD_HEAD_MS,
    pad_head_ms: float | None = None,
    pad_tail_ms: float = DEFAULT_PAD_TAIL_MS,
    join_gap_ms: float = DEFAULT_JOIN_GAP_MS,
    head_peak: float = DEFAULT_HEAD_PEAK,
) -> bytes:
    if not chunks:
        raise ValueError("no wav chunks")
    parsed = [_decode_wav(c) for c in chunks]
    rate, nch = parsed[0][0], parsed[0][1]
    for sr, ch, _ in parsed[1:]:
        if sr != rate or ch != nch:
            raise ValueError("wav chunks must share sample rate and channels")

    head_pad = pad_ms if pad_head_ms is None else pad_head_ms
    samples = [
        _trim_silence(pcm, rate, trim_db, head_pad, pad_tail_ms)
        for _, _, pcm in parsed
    ]
    samples = [s for s in samples if s]
    if not samples:
        raise ValueError("all wav chunks were silent")

    fade_out = cross_fade_ms if fade_out_ms is None else fade_out_ms
    fade_in_n = max(0, int(rate * max(0.0, fade_in_ms) / 1000.0))
    fade_out_n = max(0, int(rate * max(0.0, fade_out) / 1000.0))
    first_in_n = max(0, int(rate * 0.12))
    gap = max(0, int(rate * max(0.0, join_gap_ms) / 1000.0))
    silence = array("h", [0] * (gap * nch)) if gap else array("h")

    out = array("h")
    last = len(samples) - 1
    for i, pcm in enumerate(samples):
        fin = first_in_n if i == 0 else fade_in_n
        fout = fade_out_n if i < last else 0
        piece = _edge_fades(pcm, nch, fin, fout)
        piece = _soften_head(piece, nch, rate, head_peak)
        if i:
            out.extend(silence)
        out.extend(piece)
    return _encode_wav(rate, nch, out)


def _decode_wav(data: bytes) -> tuple[int, int, array]:
    with wave.open(io.BytesIO(data), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise ValueError("only 16-bit wav is supported")
        nch = wf.getnchannels()
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    pcm = array("h")
    pcm.frombytes(raw)
    return rate, nch, pcm


def _encode_wav(rate: int, nch: int, pcm: array) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(nch)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def _frame_rms(frame: array) -> float:
    if not frame:
        return 0.0
    acc = 0
    for x in frame:
        acc += int(x) * int(x)
    return (acc / len(frame)) ** 0.5


def _trim_silence(
    pcm: array,
    rate: int,
    trim_db: float,
    pad_head_ms: float,
    pad_tail_ms: float | None = None,
) -> array:
    if not pcm:
        return pcm
    if pad_tail_ms is None:
        pad_tail_ms = pad_head_ms
    thresh = 32768.0 * (10.0 ** (trim_db / 20.0))
    hop = max(1, rate // 100)
    n = len(pcm)
    first = 0
    last = n
    for i in range(0, n, hop):
        if _frame_rms(pcm[i : i + hop]) >= thresh:
            first = i
            break
    else:
        return array("h")
    for i in range(n, 0, -hop):
        start = max(0, i - hop)
        if _frame_rms(pcm[start:i]) >= thresh:
            last = i
            break
    first = max(0, first - int(rate * max(0.0, pad_head_ms) / 1000.0))
    last = min(n, last + int(rate * max(0.0, pad_tail_ms) / 1000.0))
    if last <= first:
        return array("h")
    return array("h", pcm[first:last])


def _soften_head(pcm: array, nch: int, rate: int, peak_target: float) -> array:
    if not pcm or peak_target <= 0:
        return pcm
    window = min(len(pcm), int(rate * 0.22) * nch)
    peak = 0
    for i in range(0, window, nch):
        for c in range(nch):
            peak = max(peak, abs(pcm[i + c]))
    limit = int(32767 * min(1.0, peak_target))
    if peak <= limit:
        return pcm
    scale = limit / peak
    out = array("h", pcm)
    frames = window // nch
    for i in range(frames):
        t = i / max(1, frames - 1)
        w = scale + (1.0 - scale) * (t * t * (3 - 2 * t))
        base = i * nch
        for c in range(nch):
            out[base + c] = int(out[base + c] * w)
    return out


def _edge_fades(pcm: array, nch: int, fade_in: int, fade_out: int) -> array:
    frames = len(pcm) // nch
    max_fade = max(0, frames // 3)
    fade_in = min(max(0, fade_in), max_fade)
    fade_out = min(max(0, fade_out), max_fade)
    out = array("h", pcm)
    if fade_in:
        for i in range(fade_in):
            w = math.sin(((i + 1) / (fade_in + 1)) * math.pi / 2)
            base = i * nch
            for c in range(nch):
                out[base + c] = int(out[base + c] * w)
    if fade_out:
        for i in range(fade_out):
            w = math.cos(((i + 1) / (fade_out + 1)) * math.pi / 2)
            base = (frames - fade_out + i) * nch
            for c in range(nch):
                out[base + c] = int(out[base + c] * w)
    return out
