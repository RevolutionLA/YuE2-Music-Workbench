# -*- coding: utf-8 -*-
"""谱面比对：把两份 ABC 变成可比较的音高序列，用来**事后**回答"改对了没 / 唱对了没"。

为什么不并进 `abc_edit`：`abc_edit` 是"改"谱，它的正确性能靠算术证明（见 tests/abc_oracle.js
用第三方解析器逐音核对）；本模块是"量"结果，衡量的对象包括模型唱出来的东西，只能靠实验。
两者混在一起会让人误以为"量出来的数字"也是确定性的。

三个问题、三把尺子——**每把都有自己的盲区，任何一把单独都构不成结论**：

Q1「这份谱是不是整体挪了 k 个半音」  → `report()`
   色度旋转给出模 12 的平移，绝对音高中位数（按时值加权）再定方向与八度。
Q2「引擎到底有没有照谱唱」          → `melody_match()`
   两串音高做 DTW 单调对齐，报平均偏差与"完全对上"的比例。
Q3「听起来还是不是同一首歌」        → `motif_contain()`
   相邻音高差切成 n 元组取包含率，对移调、换八度天然免疫。

盲区（测试里逐条钉住了，别删）：

* **色度只有 12 个格子，只说得出等价类**。`-5` 与 `+7` 在色度上完全一样（同余 12），
  所以 `chroma_shift()` 只保证模 12 正确，八度与方向要靠中位偏移来定。
* **"调性变了"和"音区变了"是两个独立量，混在一起就全错**。实测 `base` 那组：
  转录谱与输入谱**同一个调**（色度平移 0），但绝对音高差 **+12**（引擎自己唱高了一个八度）。
  所以 `report()` 里 `shift_chroma` 回答"调挪了没"，`offset` 回答"整体高了多少（含八度）"，
  两个数各自成立、互不替代；把 `shift_abs` 当"移调量"用就会把八度差读成移调。
* **转录谱通常比输入谱长**（引擎会加过门、转谱会多认装饰音），逐下标对齐会从多出来的
  那个音起一路错位，所以 Q2 用 DTW 允许跳着对，不按顺序硬配。
* **加权中位数同时被"移调"和"换八度"影响**，别拿它单独区分调性或音区。
* **时值取自谱面 L: 的默认值**。本模块不追 `M:`/`L:` 变化，只做相对加权，
  所以跨 L: 变化的谱内部比值会略粗——测"整体平移"够用，别拿它当节奏分析器。

音高口径：内部一律用 `C4 = 0` 的半音值（大写 `C` 无后缀即 0），`to_midi()` 加 60
换成常见 MIDI 编号。
"""
from __future__ import annotations

import math
import re

import abc_edit

__all__ = [
    "notes", "to_midi", "chroma", "corr", "chroma_shift", "weighted_median",
    "key_of", "summary", "resolve_shift", "median_offset", "report",
    "motif_contain", "align", "melody_match",
]

#: 音符 + 时值。和弦记号/注释/装饰音/连音块整体跳过——比对的是旋律骨架，
#: 装饰音和和弦名会把音数搞乱（转录谱里尤其多）。
#:
#: **小节线必须单独成一项且排在 `[...]` 之前**：ABC 的变音记号只在本小节有效，
#: 不清状态就会把上一条 `=f` 带到下一小节（实测在这份谱上差了整整一个半音）。
#: 顺序也不能反：`\[[^\]]*\]` 写在前面会把 `[|` 到下一个 `]` 之间的音符整段吞掉。
_TOKEN = re.compile(
    r'"[^"]*"'                                  # 和弦记号
    r'|%[^\n]*'                                 # 注释
    r'|![^!]*!'                                 # 装饰音
    r'|:\|+\]?|\[\||\|\]|\|\||\|:|\|'           # 小节线/反复记号 → 清变音状态
    r'|\[[^\]]*\]'                              # 连音块、和弦、行内字段
    r'|([_=^]{0,2})([A-Ga-g])([\',]*)(\d*(?:/\d*)?)'   # (变音)(字母)(八度)(时值)
    r'|z\d*(?:/\d*)?',                          # 休止符
    re.S)
_BAR = re.compile(r":\|+\]?|\[\||\|\]|\|\||\|:|\|")
_INLINE_KEY = re.compile(r"^\[K:[ \t]*([^\]]*)\]$", re.I)


def _dur(text: str) -> float:
    """ABC 时值 → 相对长度。空 = 1（等于 L: 的默认值），`3/2` = 1.5。"""
    if not text:
        return 1.0
    if "/" in text:
        a, b = text.split("/", 1)
        return (float(a) if a else 1.0) / (float(b) if b else 2.0)
    return float(text)


def notes(abc: str) -> list[tuple[int, float]]:
    """返回 [(音高, 时值)]，音高是 `C4 = 0` 的半音值。

    遵守与 `abc_edit.transform` **同一套**语义，否则"加工对不对"就没法用本模块量：
    · 调号自动升降（K:E 的 `f` 是 F#）；
    · 小节内变音继承，粒度是「同字母 **+ 同八度**」，**小节线处清零**；
    · 正文 inline 字段行（`V: Vocal`）整行跳过——Vocal 里的 c/a 不是音符；
    · 谱中间变调（独占一行的 `K:X` 或行内 `[K:X]`）按新调号重新起算。
    """
    headers, body = abc_edit.split_abc(abc)
    key_raw = abc_edit._get_header(headers, "K") or "C"
    tonic, minor, _ = abc_edit._parse_key(key_raw)
    key_acc = abc_edit._key_accidentals(abc_edit._declared_sig(key_raw, tonic, minor))
    state: dict[str, int] = {}
    out: list[tuple[int, float]] = []

    def _rekey(value: str) -> dict[str, int]:
        t, m_, _p = abc_edit._parse_key(value)
        return abc_edit._key_accidentals(abc_edit._declared_sig(value, t, m_))

    for line in body:
        if abc_edit._HEADER.match(line.strip()):
            state.clear()                       # 字段行：小节状态与旧调号一起作废
            head, _, value = line.strip().partition(":")
            if head.strip().upper() == "K":
                key_acc = _rekey(value)
            continue
        for m in _TOKEN.finditer(line):
            if not m.group(2):
                tok = m.group(0)
                if _BAR.fullmatch(tok):
                    state.clear()
                    continue
                mk = _INLINE_KEY.match(tok)
                if mk:
                    state.clear()
                    key_acc = _rekey(mk.group(1))
                continue
            pre = m.group(1) or ""
            letter = m.group(2)
            ticks = m.group(3) or ""
            lu = letter.upper()
            try:
                expl = abc_edit._accidental_value(pre)
            except ValueError:
                continue                        # 认不出的变音组合：跳过而不是瞎猜
            octave = (1 if letter.islower() else 0) + ticks.count("'") - ticks.count(",")
            k2 = f"{lu}{octave}"
            if expl is not None:
                pc = abc_edit._PC[lu] + expl
                state[k2] = pc % 12
            else:
                pc = state.get(k2, abc_edit._PC[lu] + key_acc.get(lu, 0))
            out.append((octave * 12 + pc, _dur(m.group(4) or "")))
    return out


def to_midi(value: float) -> float:
    """`C4 = 0` → 常见 MIDI 编号（C4 = 60）。"""
    return value + 60


def key_of(abc: str) -> str:
    """谱面声明的调名（`K:` 行的值），读不出来给空串。"""
    headers, _ = abc_edit.split_abc(abc)
    return (abc_edit._get_header(headers, "K") or "").strip()


def chroma(ns: list[tuple[int, float]]) -> list[float]:
    """按时值加权的 12 维音级分布（归一化到和为 1）。"""
    hist = [0.0] * 12
    for pitch, dur in ns:
        hist[pitch % 12] += dur
    total = sum(hist) or 1.0
    return [x / total for x in hist]


def corr(a, b) -> float:
    """皮尔逊相关。对整体缩放不敏感，正是比较两个归一化分布要的性质。"""
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da and db else 0.0


def chroma_shift(ref: list[tuple[int, float]], cand: list[tuple[int, float]]) -> tuple[int, float]:
    """`cand` 比 `ref` 整体高几（模 12）个半音最像，附带相似度。

    符号约定：**正数 = cand 更高**，与 `report()["shift"]` 一致；
    只保证模 12，八度与方向要靠 `median_offset()` 定（见模块文档的色度盲区）。
    """
    ca, cb = chroma(ref), chroma(cand)
    best_k, best_c = 0, -2.0
    for k in range(12):
        rot = [cb[(i - k) % 12] for i in range(12)]
        c = corr(ca, rot)
        if c > best_c:
            best_k, best_c = k, c
    if best_k > 6:                                  # 取 -6..+6 的等价类代表，别报 +11
        best_k -= 12
    return -best_k, round(best_c, 4)


def weighted_median(ns: list[tuple[int, float]]) -> float:
    """按时值加权的音高中位数：长音决定听感，短音只是过门。"""
    if not ns:
        return float("nan")
    pairs = sorted(ns)
    total = sum(d for _, d in pairs)
    acc = 0.0
    for pitch, dur in pairs:
        acc += dur
        if acc >= total / 2:
            return float(pitch)
    return float(pairs[-1][0])


def median_offset(ref: list[tuple[int, float]], cand: list[tuple[int, float]]) -> float:
    """绝对音高差（cand − ref），用加权中位数。对增删音、顺序变化不敏感。"""
    return round(weighted_median(cand) - weighted_median(ref), 1)


def resolve_shift(chroma_k: int, offset: float) -> int:
    """把「模 12 的旋转」和「实测中位偏移」合成一个直观的半音数。

    在所有与 `chroma_k` 同余的整数里取离 `offset` 最近的那个：
    `-5` 与 `+7` 在色度上等价，真实的八度与方向只能由中位偏移定。
    """
    if offset != offset:                            # NaN：空谱，无从校正
        return chroma_k
    cands = [chroma_k + 12 * d for d in (-2, -1, 0, 1, 2)]
    return min(cands, key=lambda base: abs(base - offset))


def summary(abc: str) -> dict:
    """一份谱的概览：调、音数、音域、加权中位。"""
    ns = notes(abc)
    pitches = [p for p, _ in ns]
    return {"key": key_of(abc), "n": len(ns),
            "lo": to_midi(min(pitches)) if pitches else None,
            "hi": to_midi(max(pitches)) if pitches else None,
            "span": (max(pitches) - min(pitches)) if pitches else None,
            "wmed": to_midi(weighted_median(ns)) if pitches else None}


def report(ref_abc: str, cand_abc: str) -> dict:
    """Q1：两份谱的关系。

    返回里三件事必须分开读，**谁也不能代替谁**：

    · `shift_chroma`——调性平移，模 12。`0` 就是同一个调；`-5` 与 `+7` 等价。
      问"转录有没有唱到我们移过去的那个调"看这个。
    · `offset`——绝对音高差（按时值加权的中位数），把八度差也算进去。
      两份纯 ABC 谱互比时它就等于移调量；一旦掺进"唱出来的录音"，它还会带上音区差。
    · `shift_abs`——把上面两个合成一个直观半音数（取与色度同余、离 `offset` 最近的那个）。
      只在"两份谱都是符号"时才等于移调量；拿去读录音必踩八度。

    `residual` 是扣掉 `shift_abs` 后还剩多少差，理想为 0；明显不为 0 说明不只是平移
    （换了调式，或者根本不是同一段旋律）。
    """
    ref, cand = notes(ref_abc), notes(cand_abc)
    k12, co = chroma_shift(ref, cand)
    off = median_offset(ref, cand)
    k = resolve_shift(k12, off)
    return {"ref_key": key_of(ref_abc), "cand_key": key_of(cand_abc),
            "ref_n": len(ref), "cand_n": len(cand),
            "ref_med": to_midi(weighted_median(ref)),
            "cand_med": to_midi(weighted_median(cand)),
            "shift_chroma": k12, "shift_abs": k, "corr": co, "offset": off,
            "residual": None if off != off else round(off - k, 1)}


def _grams(pitches: list[int], n: int) -> set[tuple[int, ...]]:
    steps = tuple(b - a for a, b in zip(pitches, pitches[1:]))
    return {steps[i:i + n] for i in range(len(steps) - n + 1)}


def motif_contain(ref_abc: str, cand_abc: str, n: int = 5) -> dict:
    """Q3：旋律 n 音组重合度，返回 Jaccard 与包含率。

    只看**相邻音高差**，所以移调、换八度都免疫，适合回答"还是同一首歌吗"。
    两个数各有用途：Jaccard 会被长度差压得很低（转录谱总是更长），
    包含率 = 交集 / 较短一方，才是"像不像"的主指标（实测同歌 1.00 / 异歌 0.00–0.02，
    用 n=5；n=3 时异歌也能到 0.2 以上，区分度不够）。
    """
    a = _grams([p for p, _ in notes(ref_abc)], n)
    b = _grams([p for p, _ in notes(cand_abc)], n)
    if not a or not b:
        return {"jaccard": 0.0, "contain": 0.0}
    inter = len(a & b)
    return {"jaccard": round(inter / len(a | b), 4),
            "contain": round(inter / min(len(a), len(b)), 4)}


def align(a: list[int], b: list[int], shift: int) -> tuple[float, list[float]]:
    """把 `b` 整体平移 `shift` 后与 `a` 做 DTW 单调对齐。

    返回 (平均绝对音高差, 每一个对齐格的绝对差)。允许"跳过一个音"，所以转谱漏音/
    多音不会让后面全部错位——按顺序硬配就会（实测过：多一个装饰音，命中率从 0.9 掉到 0.2）。
    """
    n, m = len(a), len(b)
    if not n or not m:
        return float("inf"), []
    inf = float("inf")
    prev = [inf] * (m + 1)
    prev[0] = 0.0
    back = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cur = [inf] * (m + 1)
        ai = a[i - 1]
        for j in range(1, m + 1):
            d = abs(ai - (b[j - 1] + shift))
            opts = (prev[j - 1], prev[j], cur[j - 1])
            k = 0 if opts[0] <= opts[1] and opts[0] <= opts[2] else (1 if opts[1] <= opts[2] else 2)
            back[i][j] = k
            cur[j] = d + opts[k]
        prev = cur
    diffs: list[float] = []
    i, j = n, m
    while i > 0 and j > 0:
        diffs.append(abs(a[i - 1] - (b[j - 1] + shift)))
        k = back[i][j]
        if k == 0:
            i, j = i - 1, j - 1
        elif k == 1:
            i -= 1
        else:
            j -= 1
    return (sum(diffs) / len(diffs) if diffs else inf), diffs


def melody_match(ref_abc: str, cand_abc: str, *, span: int = 24) -> dict:
    """Q2：逐音贴合度——找一个整体平移让两串音高最贴合，再报偏差与命中率。

    `shift` 是那个平移量（正数 = cand 更高），也就是两段录音的**实际音区差**。
    扫描范围 ±`span` 半音；覆盖率不足一半的路径会被加罚，否则"只对上一个音"
    也能拿平均偏差 0，报出来的数字好看但没意义。
    """
    a = [p for p, _ in notes(ref_abc)]
    b = [p for p, _ in notes(cand_abc)]
    if not a or not b:
        return {"mean": float("inf"), "shift": 0, "n": 0, "hit0": 0.0, "hit1": 0.0}
    floor = 0.5 * min(len(a), len(b))
    best = (float("inf"), 0, [])
    for s in range(-span, span + 1):
        mean, diffs = align(a, b, s)
        score = mean + (6 if len(diffs) < floor else 0)
        if score < best[0]:
            best = (score, s, diffs)
    _, s, diffs = best
    # `align` 内部是 "b + s ≈ a"，所以 s = −(cand 比 ref 高多少)；对外统一成
    # "正数 = cand 更高"，与 report()/chroma_shift() 同口径，免得两个数字看着像互相矛盾。
    shift = -s
    if not diffs:
        return {"mean": float("inf"), "shift": shift, "n": 0, "hit0": 0.0, "hit1": 0.0}
    return {"mean": round(sum(diffs) / len(diffs), 2), "shift": shift, "n": len(diffs),
            "hit0": round(sum(1 for d in diffs if d <= 0.5) / len(diffs), 3),
            "hit1": round(sum(1 for d in diffs if d <= 1.5) / len(diffs), 3)}
