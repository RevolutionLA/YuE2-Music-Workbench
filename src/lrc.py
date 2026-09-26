"""LRC 歌词生成（legacy whisper 兜底）：

仅当 src/lrc_align.py 的强制对齐（fa-zh / ctc-wav2vec2 / vad-distribute）完全失败时，
由 app.py 回退调用。ASR 在歌声上漏识严重（转音/和声/旋律），时间轴精度远低于
强制对齐，因此仅作 last-resort，不建议直接依赖。SenseVoice 分支已移除。

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
_WHISPER_DIR = Path(__file__).resolve().parent.parent / "runtime" / "models" / "faster-whisper-large-v3-turbo"


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
        # 末级兜底链路：强制走 CPU int8，避免与常驻的 audiocpp 推理引擎抢显存
        device = "cpu"
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


_CC = None  # opencc 繁→简转换器（懒加载；未安装时跳过归一化）


def _norm(s: str) -> str:
    """对齐用文本归一化：繁→简、去空白/标点，转小写。

    whisper 对中文歌常输出繁体（場/燈/燙），与简体歌词判不上——
    行首定位退化为邻近弱命中，造成多行挤压在同一秒附近。
    """
    global _CC
    if _CC is None:
        try:
            from opencc import OpenCC
            _CC = OpenCC("t2s")
        except Exception:
            class _Pass:
                def convert(self, t):
                    return t
            _CC = _Pass()
    s = _CC.convert(s)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", s).lower()


def _hit(wj: str, target: str) -> bool:
    """词-行命中判定：公共子串 ≥2 字（或低频单字）才算可信。

    只看单字交集太弱——「我/你/不/的」等常用字让任意中文词都能'命中'，
    相邻行的首个命中词会挤在同一段词流里（行首差 0.2s 的挤压就是它造成的）。
    """
    if not wj:
        return False
    if len(wj) == 1:
        return wj in target and target.count(wj) <= 2
    for i in range(len(wj) - 1):
        if wj[i:i + 2] in target:
            return True
    return False


def _fmt_ts(sec: float) -> str:
    sec = max(0.0, sec)
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:05.2f}"



# ---- whisper 段级兜底（legacy；仅当强制对齐完全失败时由 app.py 调用）----
# 注：ASR 在歌声上漏识严重（转音/和声/旋律导致），时间轴精度远低于强制对齐，
# 仅作为 last-resort 兜底，不建议直接依赖。SenseVoice 分支已移除。
def _transcribe_segments(wav_path: Path) -> list[dict]:
    """返回 whisper 段级列表 [{start, end, text}]（按 VAD 开→关两遍尝试）。

    段级是地面真相：segment 自带真实演唱区间（含间奏空档），且整段文本
    一起匹配，漏识几个字不影响锚点定位——词级贪心会被漏识带得越唱越超前。
    """
    model = _load_model()
    for vad in (True, False):
        segments, _info = model.transcribe(
            str(wav_path), word_timestamps=True, vad_filter=vad,
            vad_parameters={"min_silence_duration_ms": 300},
            language="zh", beam_size=1,
        )
        segs = []
        for seg in segments:
            t = (seg.text or "").strip()
            if not t:
                continue
            ws = [{"start": float(w.start), "end": float(w.end), "word": (w.word or "").strip()}
                  for w in (seg.words or []) if (w.word or "").strip()]
            segs.append({"start": float(seg.start), "end": float(seg.end), "text": t, "words": ws})
        if segs:
            return segs
    raise HTTPException(status_code=500, detail="未能从音频识别出任何人声，无法生成 LRC")


def generate_lrc(wav_path: Path, lyrics: str, title: str = "") -> str:
    """把歌词逐行对齐到音频时间轴，返回 LRC 文本。

    段级锚点 + 插值策略（v2）：
    1. whisper 段级结果作为地面真相——segment 自带真实演唱区间（含间奏空档），
       整段文本匹配对漏识鲁棒（词级贪心会被 ~25% 漏识带得越唱越超前）。
    2. 歌词行按顺序与段做单调匹配（字符重合度打分），命中段即锚点。
    3. 同段多行按字符占比在该段时间跨度内线性分布。
    4. 未命中行在前后锚点之间按剩余字数比例插值；无后锚点时顺延 +2.5s/行，
       全程钳制在 [首锚, 末识别时间] 内。
    """
    # whisper 段级兜底（legacy）：强制对齐失败时才走到这里
    segs = _transcribe_segments(wav_path)
    if not segs:
        raise HTTPException(status_code=500, detail="未能从音频识别出任何人声，无法生成 LRC")
    all_lines = [ln.strip() for ln in str(lyrics or "").splitlines() if ln.strip()]
    if not all_lines:
        raise HTTPException(status_code=400, detail="任务没有歌词内容")

    # 只对非结构行做对齐，结构段标记原样输出
    sing_idx = [i for i, ln in enumerate(all_lines) if not _is_struct(ln)]
    targets = [_norm(all_lines[i]) for i in sing_idx]
    seg_norms = [_norm(x["text"]) for x in segs]
    n_s, n_g = len(sing_idx), len(segs)

    # 单调匹配：每行在前一行命中段之后找重合度最高的段（阈值内才算锚点）
    anchors: dict[int, int] = {}  # 行序(在 sing_idx 中) -> 段序
    si = 0
    for li in range(n_s):
        t = targets[li]
        if not t:
            continue
        best, best_sc = -1, 0.0
        for gi in range(si, min(si + 8, n_g)):
            g = seg_norms[gi]
            if not g:
                continue
            sc = sum(1 for ch in set(t) if ch in g) / max(len(set(t)), 1)
            if sc > best_sc:
                best, best_sc = gi, sc
        if best >= 0 and best_sc >= 0.22:
            anchors[li] = best
            si = best  # 允许同段多行；下一行从同段继续找

    # 行时间轴
    times: list[float | None] = [None] * n_s
    # 同段多行：按字符占比在该段跨度内分布
    by_seg: dict[int, list[int]] = {}
    for li, gi in anchors.items():
        by_seg.setdefault(gi, []).append(li)
    for gi, lis in by_seg.items():
        seg = segs[gi]
        dur = max(seg["end"] - seg["start"], 0.8)
        # whisper 可能把前奏并进第一段（段 start=0 但 13s 才起唱）——
        # 段内用词级时间戳校正：每行取段内首个命中该行文字的词的 start
        def _seg_line_start(li):
            seg_ws = seg.get("words") or []
            for w in seg_ws:
                wt = _norm(w["word"])
                if wt and _hit(wt, targets[li]):
                    return w["start"]
            return None
        if len(lis) == 1:
            times[lis[0]] = _seg_line_start(lis[0]) or seg["start"]
            continue
        weights = [max(len(targets[x]), 1) for x in lis]
        total = sum(weights)
        acc = 0.0
        for idx, li in enumerate(lis):
            times[li] = _seg_line_start(li) or (seg["start"] + dur * (acc / total))
            acc += weights[idx]

    # 未命中行：按「未定行游程」在前后锚点区间内均分插值；
    # 最后做一次全序列单调钳制（间隔 <1s 的行顺延），保证 LRC 合法且不挤压
    li = 0
    while li < n_s:
        if times[li] is not None:
            li += 1
            continue
        # 找出连续未定行游程 [li, rj)
        rj = li
        while rj < n_s and times[rj] is None:
            rj += 1
        prev_t = max((times[k] for k in range(li) if times[k] is not None), default=None)
        nxt_t = next((times[k] for k in range(rj, n_s) if times[k] is not None), None)
        lo = prev_t if prev_t is not None else (segs[0]["start"] if segs else 0.0)
        hi = nxt_t if nxt_t is not None else min(lo + 2.5 * (rj - li + 1), segs[-1]["end"] if segs else lo)
        if hi <= lo + 0.5:
            hi = lo + 2.5 * (rj - li + 1)
        m = rj - li
        for idx in range(m):
            times[li + idx] = lo + (hi - lo) * (idx + 1) / (m + 1)
        li = rj
    # 全序列单调钳制
    for li in range(1, n_s):
        if times[li] < times[li - 1] + 1.0:
            times[li] = times[li - 1] + 1.0
    try:
        import soundfile as _sf
        _dur = _sf.info(str(wav_path)).duration
    except Exception:
        _dur = segs[-1]["end"] if segs else 0.0
    # cap 取音频实际时长与最后识别时间的较大者——fsmn-vad 偶发漏检
    # 结尾人声（实测 154.9s 的歌只检到 147s），只按 VAD 末端钳会压扁尾行
    cap = max(segs[-1]["end"] if segs else 0.0, min(_dur, (segs[-1]["end"] if segs else 0.0) + 30.0))
    # 贴 cap 的行不能全部压在同一时刻——把尾部游程在
    # 「前一个未压行」到 cap 之间反向均匀展开（行距仍 ≥1s 尽量保住）
    over = [li for li in range(n_s) if times[li] > cap]
    if over:
        first = over[0]
        base = times[first - 1] if first > 0 else 0.0
        m = len(over)
        for idx, li in enumerate(over):
            times[li] = base + (cap - base) * (idx + 1) / (m + 1) if cap > base + 1.0 else cap - (m - idx) * 0.01
    for li in range(1, n_s):
        if times[li] < times[li - 1] + 0.01:
            times[li] = times[li - 1] + 0.01

    # 输出：结构行原样，歌词行带时间戳
    out: list[str] = []
    k = 0
    for i, ln in enumerate(all_lines):
        if _is_struct(ln):
            out.append(ln if ln.startswith("[") else f"[{ln.strip('[]()（）【】 ')}]")
        else:
            out.append(f"[{_fmt_ts(times[k])}]{ln}")
            k += 1

    # 纯净输出：只保留 [时间]歌词 行与结构段标记，不写标题/作者/制作等冗余头，
    # 便于直接导入播放器
    return "\n".join(out) + "\n"
