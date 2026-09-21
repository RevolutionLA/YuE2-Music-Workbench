"""LRC 歌词生成：用 faster-whisper 词级时间戳把用户歌词对齐到已生成的音频。

产物为标准 .lrc（每行 [mm:ss.xx]歌词），可直接导入音乐 App 做滚动歌词。
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from fastapi import HTTPException

from settings import settings

logger = logging.getLogger("fireredtts3_gateway")

_lock = threading.Lock()
_model = None

# 项目内自带的 faster-whisper CT2 模型（整体分享项目文件夹即可用，无需外部依赖）
_WHISPER_DIR = Path(__file__).resolve().parent / "models" / "faster-whisper-large-v3-turbo"


def _snapshot_dir() -> Path:
    if (_WHISPER_DIR / "model.bin").is_file():
        return _WHISPER_DIR
    snaps = _WHISPER_DIR / "snapshots"
    if snaps.is_dir():
        for d in sorted(snaps.iterdir()):
            if (d / "model.bin").is_file():
                return d
    raise HTTPException(status_code=503, detail=f"whisper 模型不存在: {_WHISPER_DIR}")


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="faster-whisper 未安装") from exc
        d = _snapshot_dir()
        device = "cuda" if settings.sensevoice_device.startswith("cuda") else "cpu"
        logger.info("loading faster-whisper from %s device=%s", d, device)
        try:
            _model = WhisperModel(
                str(d), device=device,
                compute_type="float16" if device == "cuda" else "int8",
            )
        except Exception:
            # GPU 加载失败（显存被生成任务占满等）退回 CPU
            _model = WhisperModel(str(d), device="cpu", compute_type="int8")
        return _model


_STRUCT_RE = re.compile(r"^\s*[\[【（(]?(intro|verse|chorus|bridge|pre-chorus|outro|hook|"
                        r"前奏|主歌|副歌|桥段|间奏|尾声|预副歌)[^\]】）]*[\]】）]?\s*$", re.I)
_PARAM_RE = re.compile(r"^\s*--")


def _is_struct(line: str) -> bool:
    return bool(_STRUCT_RE.match(line)) or (_PARAM_RE.match(line) is not None)


def _norm(s: str) -> str:
    """对齐用文本归一化：去空白/标点，转小写，便于 whisper 词与歌词行匹配。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", s).lower()


def _fmt_ts(sec: float) -> str:
    sec = max(0.0, sec)
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:05.2f}"


def _transcribe_words(wav_path: Path) -> list[dict]:
    """返回 [{start, end, word}]（词级时间戳）。"""
    model = _load_model()
    segments, _info = model.transcribe(
        str(wav_path), word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
    )
    words = []
    for seg in segments:
        for w in (seg.words or []):
            token = (w.word or "").strip()
            if token:
                words.append({"start": w.start, "end": w.end, "word": token})
    if not words:
        raise HTTPException(status_code=500, detail="未能从音频识别出任何人声，无法生成 LRC")
    return words


def generate_lrc(wav_path: Path, lyrics: str, title: str = "") -> str:
    """把歌词逐行对齐到音频时间轴，返回 LRC 文本。

    策略：whisper 词流 + 歌词行（非结构行）按顺序贪心匹配——
    逐行累计匹配到的归一化字符数，达到该行字符数的一定比例即认为该行唱完，
    行首时间取该行第一个匹配词的 start。识别与歌词不完全一致时按比例推进，保证不卡死。
    """
    words = _transcribe_words(wav_path)
    lines = [ln.strip() for ln in str(lyrics or "").splitlines() if ln.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="任务没有歌词内容")

    wi = 0
    n_words = len(words)
    out: list[str] = []
    fallback = 0.0  # 词流耗尽后剩余行的顺延起点（保持时间戳单调）
    for ln in lines:
        if _is_struct(ln):
            out.append(ln if ln.startswith("[") else f"[{ln.strip('[]()（）【】 ')}]")
            continue
        target = _norm(ln)
        if not target:
            out.append(ln)
            continue
        # 贪心吞词：累计归一化长度，覆盖该行所需字符后本行结束
        need = len(target)
        got = 0
        line_start = None
        consumed = 0
        j = wi
        while j < n_words and got < need * 0.8:
            wj = _norm(words[j]["word"])
            if wj:
                if line_start is None:
                    line_start = words[j]["start"]
                got += len(wj)
            consumed = j + 1 - wi
            j += 1
        if line_start is None:
            # 词已用尽：剩余行按顺延时间推进，保持 LRC 单调
            fallback = max(fallback, words[-1]["end"] if words else 0.0) + 5.0
            line_start = fallback
        else:
            fallback = max(fallback, words[min(j, n_words - 1)]["end"])
            wi += max(consumed, 1)
        out.append(f"[{_fmt_ts(line_start)}]{ln}")

    header = []
    if title:
        header.append(f"[ti:{title}]")
    header.append("[re:YuE2 音乐工作台]")
    return "\n".join(header + out) + "\n"
