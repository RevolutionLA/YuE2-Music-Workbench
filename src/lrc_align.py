# -*- coding: utf-8 -*-
"""歌词强制对齐（forced alignment）—— 已知歌词文本 → 精确时间轴。

与旧实现（src/lrc.py）的本质区别：
  旧实现走的是「ASR 转写 → 模糊文本匹配 → 插值」，属于 *识别* 路线：
  歌声上 ASR 漏识率 20-30%，再加字符集合交集打分（丢序），误差逐级累积。
  本模块走的是 *强制对齐* 路线：歌词文本是已知的，只需要把它"压"到音频上，
  不做识别，因此不受漏识影响，字/词级时间戳误差在百毫秒量级。

分层策略（按语种自动选路，全程无 ASR 介入）：
  1. 中文（含中英混排）：FunASR fa-zh 时间戳预测模型（151MB，本机已缓存）
     —— 输入 (音频, 歌词字序列)，直接输出每个字的 [起, 止] 毫秒。
  2. 英文/其它：torchaudio CTC 强制对齐（wav2vec2-base-960h）
     —— 把歌词按字母拼成目标序列，用 CTC 把音频强制对齐到该序列，
        输出逐词时间轴。这是英文歌声目前最稳的方案，无需任何识别模型。
  3. 任一路径完成后，用 fsmn-vad 的人声起音点做锚点吸附校正：
     VAD 的起音检测是毫秒级的，fa-zh / wav2vec2 在歌声上有约 0.5s 的集体漂移，
     吸附可消除该漂移。
  4. 两条路都不可用时，末级回退为「VAD 段锚定 + 按行内音节/词数加权分布」：
     这是确定性铺排，不假装精确（诊断里标注 method="vad-distribute"），
     比旧 ASR 兜底更诚实——毕竟 ASR 在歌声上的错识本身就不具参考价值。

已弃用：旧实现的 SenseVoice / faster-whisper ASR 兜底。ASR 在歌唱旋律、
转音、和声上漏识严重（实测 "摆了一本又一本厚砖"→"百你一半又一半"），
用它反推时间轴只会放大误差，已无保留价值。

产出：
  * lrc  —— 标准 LRC（[mm:ss.xx]歌词）
  * elrc —— 增强 LRC（行内逐字/逐词 <mm:ss.xx>，用于卡拉OK 逐字高亮）
  * diagnostics —— 对齐方法、命中率、偏差，供前端展示与排障
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger("fireredtts3_gateway")

_fa_lock = threading.Lock()
_fa_model = None
_vad_lock = threading.Lock()
_vad_model = None
_ctc_lock = threading.Lock()
_ctc_model = None

# 模型**推理**必须串行：FunASR AutoModel 与 torch 模型实例都不是线程安全的。
# 实测同一段音频串行 4 次稳定得到 30 个 VAD 段，4 线程并发则随机抛
# "Sizes of tensors must match ... Expected size 2 but got size 1" → 被 except 吞掉
# → 静默返回 0 段 → VAD 吸附失效（诊断里表现为 vad_segments=0，但整首仍能出 LRC，
# 所以这个 bug 很难被察觉）。三首并发下发时实测 3 首里 2 首中招。
# 用单个可重入锁把所有推理（fa-zh / VAD / CTC）串行化；对齐本身只需 1~10s，
# 串行的代价远小于静默降级。
_infer_lock = threading.RLock()

_STRUCT_RE = re.compile(
    r"^\s*[\[【（(]?(intro|verse|chorus|bridge|pre-chorus|outro|hook|"
    r"前奏|主歌|副歌|桥段|间奏|尾声|预副歌)[^\]】）]*[\]】）]?\s*$", re.I)
_PARAM_RE = re.compile(r"^\s*--")
# fa-zh 词表覆盖：汉字 + 常用 ASCII 字母数字（英文歌词按单词会被拆字，故英文走 CTC 路径）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


# --------------------------------------------------------------------------- #
# 歌词解析
# --------------------------------------------------------------------------- #
def _is_struct(line: str) -> bool:
    return bool(_STRUCT_RE.match(line)) or (_PARAM_RE.match(line) is not None)


def parse_lyrics(lyrics: str) -> tuple[list[str], list[int]]:
    """拆成 (行文本列表, 每行的对齐单位数)。结构行返回 0，不参与对齐。

    中文/中英混排行按「中文字数」对齐；纯英文/其它行按「单词数」对齐
    （供 CTC 路径的权重与行级判定使用）。
    """
    lines: list[str] = []
    counts: list[int] = []
    for ln in str(lyrics or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if _is_struct(s):
            lines.append(s)
            counts.append(0)
            continue
        n_cjk = len(_CJK_RE.findall(s))
        n = n_cjk if n_cjk > 0 else len(_word_split(s))
        lines.append(s)
        counts.append(n)
    return lines, counts


def _cjk_ratio(lines: list[str]) -> float:
    all_chars = "".join(lines)
    if not all_chars:
        return 0.0
    return len(_CJK_RE.findall(all_chars)) / len(all_chars)


def _word_split(line: str) -> list[str]:
    """英文/其它：按单词切分（保留缩写撇号）。"""
    return re.findall(r"[A-Za-z']+", line)


# --------------------------------------------------------------------------- #
# 模型加载
# --------------------------------------------------------------------------- #
def _load_fa_zh():
    """FunASR fa-zh 强制对齐模型（中文）。"""
    global _fa_model
    if _fa_model is not None:
        return _fa_model
    with _fa_lock:
        if _fa_model is not None:
            return _fa_model
        from funasr import AutoModel
        _fa_model = AutoModel(model="fa-zh", disable_update=True)
        logger.info("fa-zh 强制对齐模型已加载")
        return _fa_model


def _load_vad():
    """fsmn-vad：人声活动区间（毫秒）。"""
    global _vad_model
    if _vad_model is not None:
        return _vad_model
    with _vad_lock:
        if _vad_model is not None:
            return _vad_model
        from funasr import AutoModel
        hub = Path.home() / ".cache" / "modelscope" / "hub" / "iic"
        vad_dir = hub / "speech_fsmn_vad_zh-cn-16k-common-pytorch"
        if not vad_dir.exists():
            return None
        _vad_model = AutoModel(model=str(vad_dir), disable_update=True)
        return _vad_model


def _load_ctc():
    """torchaudio wav2vec2-base-960h：英文 CTC 强制对齐。"""
    global _ctc_model
    if _ctc_model is not None:
        return _ctc_model
    with _ctc_lock:
        if _ctc_model is not None:
            return _ctc_model
        try:
            import torchaudio
            from torchaudio.pipelines import WAV2VEC2_ASR_BASE_960H as B
        except Exception as exc:
            logger.warning("torchaudio 不可用，英文 CTC 对齐不可用：%s", exc)
            return None
        bundle = B
        model = bundle.get_model()
        model.eval()
        labels = list(bundle.get_labels())
        _ctc_model = (model, labels)
        logger.info("wav2vec2 CTC 对齐模型已加载")
        return _ctc_model


# --------------------------------------------------------------------------- #
# 音频加载
# --------------------------------------------------------------------------- #
def _load_16k(wav_path: Path) -> tuple[np.ndarray, float]:
    import soundfile as sf
    data, sr = sf.read(str(wav_path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != 16000:
        import librosa
        data = librosa.resample(data, orig_sr=sr, target_sr=16000)
    return data, len(data) / 16000.0


def _vad_intervals(data16k: np.ndarray) -> list[list[float]]:
    """返回 [[start_sec, end_sec], ...]。

    注意 fsmn-vad 在整段 numpy 输入下返回
    [{'key':.., 'value': [[beg,-1],[-1,end],...]}]，单位毫秒、成对哨兵。
    （旧实现 lrc.py 用 isinstance(c, (list,tuple)) 过滤，dict 永不匹配 → VAD 段
    实际从未生效，整轨兜底成 1 段，再触发 whisper 回退——这是旧链路失准的根因之一。）
    """
    with _infer_lock:  # 加载 + 推理一并串行，避免并发初始化 FunASR
        vad = _load_vad()
        if vad is None:
            return []
        try:
            out = vad.generate(input=data16k, cache={}, chunk_size=200,
                               max_single_segment_time=30000)
        except Exception as exc:
            logger.warning("VAD 失败：%s", exc)
            return []
    raw: list = []
    for item in out:
        if isinstance(item, dict):
            raw.extend(item.get("value") or [])
    ivs, pend = [], None
    for a, b in raw:
        if a >= 0 and b < 0:
            pend = a
        elif a < 0 and b >= 0 and pend is not None:
            ivs.append([pend / 1000.0, b / 1000.0])
            pend = None
    return ivs


# --------------------------------------------------------------------------- #
# 主路径 A：fa-zh 强制对齐（中文）
# --------------------------------------------------------------------------- #
def _align_fa_zh(data16k: np.ndarray, text: str) -> list[list[float]] | None:
    """返回每个字的 [start_ms, end_ms]；失败返回 None。"""
    try:
        with _infer_lock:  # 加载 + 推理一并串行（见 _infer_lock 注释）
            model = _load_fa_zh()
            res = model.generate(input=(data16k, text),
                                 data_type=("sound", "text"))
    except Exception as exc:
        logger.warning("fa-zh 对齐失败：%s", exc)
        return None
    item = res[0] if isinstance(res, list) and res else res
    if not isinstance(item, dict):
        return None
    ts = item.get("timestamp")
    if not ts:
        return None
    return [[float(t[0]), float(t[1])] for t in ts]


# --------------------------------------------------------------------------- #
# 主路径 B：torchaudio CTC 强制对齐（英文/其它）
# --------------------------------------------------------------------------- #
def _align_ctc_en(data16k: np.ndarray, lines: list[str], counts: list[int]):
    """英文/其它歌词逐词强制对齐。

    返回 (times, line_subtokens)：
      times         —— list[float|None]，每行起始秒（结构行恒为 None）
      line_subtokens—— dict[行下标] -> [(start_sec, "WORD"), ...]，供 eLRC 逐词高亮

    失败返回 (None, None)。
    """
    with _infer_lock:  # 加载 + 推理一并串行（见 _infer_lock 注释）
        ctc = _load_ctc()
    if ctc is None:
        return None, None
    import torch
    import torchaudio
    model, labels = ctc
    DICT = {c: i for i, c in enumerate(labels)}
    if DICT.get("|") != 1 or DICT.get("-") != 0:
        logger.warning("wav2vec2 词表顺序异常，放弃 CTC 对齐")
        return None, None

    # 组装逐行单词与全局目标序列（| 为词分隔符）
    line_word_start: list[int] = []
    all_words: list[str] = []
    line_words: list[list[str]] = []
    for li in (i for i, c in enumerate(counts) if c > 0):
        toks = _word_split(lines[li])
        if not toks:
            continue
        line_word_start.append(len(all_words))
        as_list = [w.upper() for w in toks]
        line_words.append(as_list)
        all_words.extend(as_list)
    if not all_words:
        return None, None
    transcript = "|".join(all_words)
    if any(c not in DICT for c in transcript):
        logger.warning("歌词含 wav2vec2 词表外字符，放弃 CTC 对齐")
        return None, None

    targets = [DICT[c] for c in transcript]
    try:
        with _infer_lock, torch.inference_mode():  # torch 推理同样需串行
            wav_t = torch.from_numpy(np.ascontiguousarray(data16k)).unsqueeze(0)
            emission, _ = model(wav_t)
            emission = torch.log_softmax(emission, dim=-1)
            input_lengths = torch.tensor([emission.shape[1]])
            target_lengths = torch.tensor([len(targets)])
            paths, scores = torchaudio.functional.forced_align(
                emission, torch.tensor([targets]), input_lengths, target_lengths)
            path = paths[0]
            spans = torchaudio.functional.merge_tokens(
                path, scores[0] if scores.dim() > 1 else scores)
    except Exception as exc:
        logger.warning("CTC 对齐失败：%s", exc)
        return None, None

    # 字符级 span → 词级 span（按 | 切分）
    FR = 50.0  # wav2vec2-base 总 stride=320 @16k → 50 fps
    word_spans: list[tuple[float, float, str]] = []
    cur: list = []
    for sp in spans:
        tok = sp.token if isinstance(sp.token, str) else labels[sp.token]
        if tok in ("|", "-"):
            if cur:
                s = cur[0].start / FR
                e = cur[-1].end / FR
                txt = "".join(c.token if isinstance(c.token, str) else labels[c.token] for c in cur)
                word_spans.append((s, e, txt))
                cur = []
            continue
        cur.append(sp)
    if cur:
        s = cur[0].start / FR
        e = cur[-1].end / FR
        txt = "".join(c.token if isinstance(c.token, str) else labels[c.token] for c in cur)
        word_spans.append((s, e, txt))

    if not word_spans:
        return None, None

    # 词数漂移保护：模型偶尔会吞/增词。按比例把行首对齐到最近的 span。
    n_exp, n_got = len(all_words), len(word_spans)
    times: list[float | None] = [None] * len(lines)
    line_subtokens: dict[int, list] = {}
    if n_got == n_exp:
        idx_map = list(range(n_got))
    else:
        logger.info("CTC 词数漂移 exp=%d got=%d，按比例适配", n_exp, n_got)
        idx_map = [min(n_got - 1, round(k * n_got / n_exp)) for k in range(n_exp)]

    for li, lw in zip((i for i in range(len(counts)) if counts[i] > 0), line_words):
        start = line_word_start[len(line_subtokens)]
        si = idx_map[start] if start < len(idx_map) else -1
        if si < 0 or si >= len(word_spans):
            continue
        times[li] = word_spans[si][0]
        ws = []
        for k in range(len(lw)):
            gk = start + k
            gk = idx_map[gk] if gk < len(idx_map) else -1
            if 0 <= gk < len(word_spans):
                ws.append((word_spans[gk][0], lw[k]))
        line_subtokens[li] = ws
    if all(t is None for t in times):
        return None, None
    return times, line_subtokens


# --------------------------------------------------------------------------- #
# 末级回退：VAD 段锚定 + 加权分布（确定性，不假装精确）
# --------------------------------------------------------------------------- #
def _distribute_vad(lines: list[str], counts: list[int],
                    ivs: list[list[float]], dur: float) -> tuple[list[float], str]:
    """无强制对齐能力时的末级铺排：把每行按词/字数权重铺到人声段或全曲上。"""
    sing = [i for i, c in enumerate(counts) if c > 0]
    if not sing:
        return [0.0] * len(counts), "empty"
    if ivs:
        anchors = [a for a, _ in ivs] + [ivs[-1][1]]
    else:
        anchors = [dur * (k + 0.5) / len(sing) for k in range(len(sing))]
    out = [0.0] * len(counts)
    for j, i in enumerate(sing):
        out[i] = anchors[min(j, len(anchors) - 1)]
    for i in sing:
        out[i] = min(out[i], max(dur - 0.5, 0.0))
    for k in range(1, len(sing)):
        if out[sing[k]] < out[sing[k - 1]] + 0.05:
            out[sing[k]] = min(out[sing[k - 1]] + 0.05, dur)
    return out, "vad-distribute"


# --------------------------------------------------------------------------- #
# 后处理：VAD 锚点吸附 + 单调化
# --------------------------------------------------------------------------- #
def _snap_to_vad(times: list[float | None], ivs: list[list[float]],
                 window: float = 1.2) -> tuple[list[float | None], int]:
    """把落在人声起音点附近的歌词行吸附到该起音点。

    VAD 的起音检测是能量突变级的（毫秒精度），fa-zh / wav2vec2 在歌声上整体偏晚
    约 0.5s；吸附既消除漂移，又保留模型给出的「哪一行属于哪一段」的映射。
    每个 VAD 段只服务一行，避免多行挤在同一段。
    """
    if not ivs:
        return times, 0
    onsets = [a for a, _ in ivs]
    used: set[int] = set()
    snapped = 0
    out = list(times)
    for i, t in enumerate(out):
        if t is None:
            continue
        cands = [k for k, o in enumerate(onsets)
                 if abs(t - o) <= window and k not in used]
        if not cands:
            continue
        k = min(cands, key=lambda k: abs(t - onsets[k]))
        if i > 0 and out[i - 1] is not None and onsets[k] <= out[i - 1]:
            continue
        used.add(k)
        out[i] = onsets[k]
        snapped += 1
    return out, snapped


def _monotonic_fill(times: list[float | None], counts: list[int],
                    total_dur: float, min_gap: float = 0.8) -> list[float]:
    """缺失行在前后锚点间按字数加权插值，再做单调钳制。"""
    idx = [i for i, c in enumerate(counts) if c > 0]
    vals = {i: times[i] for i in idx if times[i] is not None}
    if not vals:
        tot = sum(counts[i] for i in idx) or 1
        acc, out = 0.0, [0.0] * len(counts)
        for i in idx:
            out[i] = total_dur * acc / tot
            acc += counts[i]
        return out
    first, last = min(vals), max(vals)
    li = 0
    while li < len(idx):
        i = idx[li]
        if i in vals:
            li += 1
            continue
        rj = li
        while rj < len(idx) and idx[rj] not in vals:
            rj += 1
        prev_t = vals.get(idx[li - 1]) if li > 0 else None
        nxt_i = idx[rj] if rj < len(idx) else None
        lo = prev_t if prev_t is not None else max(first - 2.0, 0.0)
        hi = vals.get(nxt_i) if nxt_i is not None else min(last + 2.0, total_dur)
        if hi <= lo:
            hi = min(lo + 1.0, total_dur)
        m = rj - li
        wsum = sum(counts[idx[li + k]] for k in range(m)) or m
        acc = 0.0
        for k in range(m):
            out_i = idx[li + k]
            acc += counts[out_i]
            vals[out_i] = lo + (hi - lo) * acc / wsum
        li = rj
    out = [0.0] * len(counts)
    for i in idx:
        out[i] = vals[i]
    for i in idx:
        if out[i] > total_dur:
            out[i] = max(total_dur - 0.5, 0.0)
    for k in range(1, len(idx)):
        i, j = idx[k - 1], idx[k]
        if out[j] < out[i] + 0.05:
            out[j] = min(out[i] + 0.05, total_dur)
    return out


# --------------------------------------------------------------------------- #
# 对外主入口
# --------------------------------------------------------------------------- #
def _fmt(sec: float) -> str:
    sec = max(0.0, sec)
    return f"{int(sec // 60):02d}:{sec % 60:05.2f}"


def align(wav_path: Path, lyrics: str, snap_window: float = 1.2) -> dict:
    """把已知歌词强制对齐到音频，返回 LRC / eLRC / 诊断信息。

    选路：cjk_ratio >= 0.3 → fa-zh（中文字级）；否则 → CTC（英文词级）；
    两者皆不可用时回退 VAD 加权分布。全程无 ASR 介入。
    """
    lines, counts = parse_lyrics(lyrics)
    sing = [i for i, c in enumerate(counts) if c > 0]
    if not sing:
        return {"lrc": "", "elrc": "", "method": "empty", "diagnostics": {}}

    data16k, dur = _load_16k(wav_path)
    ivs = _vad_intervals(data16k)
    ratio = _cjk_ratio([lines[i] for i in sing])
    method, char_ts, line_subtokens = "unknown", None, {}

    # 1) 中文主路径：fa-zh 强制对齐
    if ratio >= 0.3:
        text = "".join(_CJK_RE.findall("".join(lines[i] for i in sing)))
        ts = _align_fa_zh(data16k, text)
        if ts:
            method = "fa-zh"
            char_ts = ts
    # 2) 英文/其它：CTC 强制对齐
    else:
        times_b, line_subtokens = _align_ctc_en(data16k, lines, counts)
        if times_b is not None:
            method = "ctc-wav2vec2"
        else:
            times_b, _ = _distribute_vad(lines, counts, ivs, dur)
            method = "vad-distribute"

    # 由 char_ts 构造行起始时间（中文路径）
    if char_ts is not None:
        times: list[float | None] = [None] * len(lines)
        pos = 0
        for i in sing:
            n = counts[i]
            if pos < len(char_ts):
                times[i] = char_ts[pos][0] / 1000.0
                pos += n
            else:
                break
    else:
        # 英文/其它路径：times_b 已就绪
        times = times_b

    # 3) VAD 锚点吸附
    times, snapped = _snap_to_vad(times, ivs, window=snap_window)
    filled = sum(1 for i in sing if times[i] is not None)

    # 4) 补缺与单调化
    final = _monotonic_fill(times, counts, dur)

    # 5) 输出
    lrc_rows: list[str] = []
    elrc_rows: list[str] = []
    pos = 0
    for i, ln in enumerate(lines):
        if counts[i] == 0:
            tag = ln if ln.startswith("[") else f"[{ln.strip('[]()（）【】 ')}]"
            lrc_rows.append(tag)
            elrc_rows.append(tag)
            continue
        t = final[i]
        lrc_rows.append(f"[{_fmt(t)}]{ln}")
        # eLRC：逐字（中文）/ 逐词（英文）高亮
        if char_ts is not None:
            parts = []
            for k in range(counts[i]):
                if pos + k < len(char_ts):
                    parts.append(f"<{_fmt(char_ts[pos + k][0] / 1000.0)}>")
                    ch = _CJK_RE.findall(ln)[k] if k < len(_CJK_RE.findall(ln)) else ""
                    parts.append(ch)
            elrc_rows.append(f"[{_fmt(t)}]{''.join(parts) if parts else ln}")
        elif i in line_subtokens:
            parts = []
            for st, w in line_subtokens[i]:
                parts.append(f"<{_fmt(st)}>{w}")
            elrc_rows.append(f"[{_fmt(t)}]{' '.join(parts)}")
        else:
            elrc_rows.append(f"[{_fmt(t)}]{ln}")
        pos += counts[i]

    diag = {
        "method": method,
        "duration_sec": round(dur, 1),
        "lines": len(sing),
        "matched": filled,
        "match_rate": round(filled / max(len(sing), 1), 2),
        "vad_segments": len(ivs),
        "vad_snapped": snapped,
        "cjk_ratio": round(ratio, 2),
        "first_line_sec": round(min(final[i] for i in sing), 2),
        "last_line_sec": round(max(final[i] for i in sing), 2),
    }
    return {"lrc": "\n".join(lrc_rows) + "\n",
            "elrc": "\n".join(elrc_rows) + "\n",
            "method": method,
            "diagnostics": diag}
