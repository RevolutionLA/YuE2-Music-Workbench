"""ABC 乐谱加工：移调、大小调转换、速度调整、去和弦。

这一层只做**符号层面的确定性变换**，不做任何"听起来好不好"的判断：
    · 移调 = 每个音加固定半音，调号随之改；
    · 平行大小调 = 只动 3/6/7 音级（大调降、小调升），主音不变；
    · 关系大小调 = 主音挪小三度，调号不变；
    · 速度只改 Q: 行的数字；去和弦只删 "..." 记号。
变换不了的东西（装饰音、连音记号、文字注释）一律原样透传，绝不猜测、绝不静默丢内容。

为什么要有这一页：YuE2 的"按谱演唱"路线里，谱是硬输入——女声的谱拿给男声音色唱
就得往下移一个八度，大调的曲子想换成小调情绪就得改音级。以前这活儿只能手工在谱框里
一个音一个音改，或者回 ③ 重新转一次谱（转出来还是原调）。
"""
from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------- 音高基础设施

# 自然音的半音值（相对 C）
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_PC_NATURAL = {v: k for k, v in _PC.items()}
# 黑键的两种写法：升号倾向用 sharp，降号倾向用 flat
_SHARP_OF = {1: "C", 3: "D", 6: "F", 8: "G", 10: "A"}
_FLAT_OF = {1: "D", 3: "E", 6: "G", 8: "A", 10: "B"}


def _spell(pc: int, pref: int) -> tuple[str, str]:
    """半音值 → (字母, 变音记号)。pref=1 倾向升号，pref=-1 倾向降号。"""
    pc %= 12
    if pc in _PC_NATURAL:
        return _PC_NATURAL[pc], ""
    if pref >= 0:
        return _SHARP_OF[pc], "#"
    return _FLAT_OF[pc], "b"


def _render(letter: str, acc: str, octave: int) -> str:
    """字母 + 变音 + 八度序号 → ABC 写法。octave=0 → 大写无后缀，1 → 小写无后缀。"""
    if octave >= 1:
        return letter.lower() + acc + "'" * (octave - 1)
    return letter.upper() + acc + "," * (-octave)


# 调号表：(主音半音, 升号数) —— 降号调记为负数
# 只列常见调，够用；表外的一律按"未知调号"处理（不猜，保守用自然音拼写）
_KEY_SIG: dict[str, tuple[int, int]] = {
    # 大调：升号方向
    "C": (0, 0), "G": (7, 1), "D": (2, 2), "A": (9, 3), "E": (4, 4), "B": (11, 5),
    "F#": (6, 6), "C#": (1, 7),
    # 大调：降号方向
    "F": (5, -1), "Bb": (10, -2), "Eb": (3, -3), "Ab": (8, -4),
    "Db": (1, -5), "Gb": (6, -6), "Cb": (11, -7),
}
_MINOR_RELATIVE_SHARPS = {0: 9, 1: 4, 2: 11, 3: 6, 4: 1, 5: 8, 6: 3, 7: 10}  # 升号数→大调主音


def _parse_key(k: str) -> tuple[int, bool, int]:
    """解析 K: 的值 → (主音半音, 是否小调, 倾向 1=升/-1=降)。

    表外或写不出来的调（如 K: none、无调号、混合调式）返回 pref=0，
    这时变换仍照做，只是拼写退回"能省则省"的自然音/升号拼写。"""
    s = (k or "").strip()
    if not s:
        return 0, False, 1
    m = re.match(r"^([A-G])([#b]?)\s*(.*)$", s, re.I)
    if not m:
        return 0, False, 1
    letter = m.group(1).upper()
    acc = m.group(2)
    rest = (m.group(3) or "").strip().lower()
    pc = _PC[letter]
    if acc == "#":
        pc = (pc + 1) % 12
    elif acc == "b":
        pc = (pc - 1) % 12
    # 小调判定：m / min / minor（注意 "maj" 不能被当成 minor）
    minor = bool(re.match(r"^(m|min|minor)\b", rest)) or rest.startswith("m") and not rest.startswith("maj")
    base = f"{letter}{acc}"
    sig = _KEY_SIG.get(base)
    if sig is None:
        return pc, minor, 1 if acc == "#" else (-1 if acc == "b" else 1)
    pref = 1 if sig[1] >= 0 else -1
    return pc, minor, pref


def _key_name(pc: int, minor: bool, pref: int) -> str:
    """反向：主音 + 是否小调 → 调名字符串。"""
    letter, acc = _spell(pc, pref)
    return letter + acc + ("m" if minor else "")


def _sig_of(tonic: int, minor: bool) -> int:
    """调号升降数（正=升、负=降）。小调的调号等于它关系大调的调号，所以要 +3 再查表。"""
    ref = (tonic + 3) % 12 if minor else tonic % 12
    for _name, (pcv, sgv) in _KEY_SIG.items():
        if pcv == ref:
            return sgv
    return 0


def _pref_of(tonic: int, minor: bool) -> int:
    """拼写倾向：降号调写 b、其余写 #。决定 Eb 而不是 D#、Ab 而不是 G#。"""
    return -1 if _sig_of(tonic, minor) < 0 else 1


# ---------------------------------------------------------------- 谱面切分

# token 顺序很关键：和弦 / 注释 / 装饰 / 连音块 必须排在单音符之前，
# 否则 "C" 里的 C 会被当成音符移调，和弦标记就跟着跑了（那是歌词不是旋律）。
_TOKEN = re.compile(
    r'"[^"]*"'                      # 和弦记号 "Am7"
    r'|%[^\n]*'                     # 行注释
    r'|![^!]*!'                     # 装饰音 !trill!
    r'|\[[A-Za-z0-9#bn\' ,_^=|:]*\]'  # 连音块 [CEG] / [C E G]
    r'|[_=^]?[A-Ga-g][#bn]?[\',]*'  # 音符
    r'|z[0-9/]*'                    # 休止
    r'|.',                          # 其他一律原样
    re.S,
)
_HEADER = re.compile(r"^[A-Za-z]:")


def split_abc(text: str) -> tuple[list[str], list[str]]:
    """切成 (文件头行, 正文行)。

    ABC 的文件头必须是开头连续若干行；一旦遇到第一行不是 `X:` 形式的行就认为正文开始。
    空行不算终止（谱面里文件头中间常见空行）。"""
    lines = (text or "").split("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or _HEADER.match(lines[i].strip())):
        i += 1
    return lines[:i], lines[i:]


def _get_header(headers: list[str], tag: str) -> Optional[str]:
    for ln in headers:
        m = re.match(rf"^{tag}:\s*(.*)$", ln.strip())
        if m:
            return m.group(1).strip()
    return None


def _set_header(headers: list[str], tag: str, value: str) -> list[str]:
    out, hit = [], False
    for ln in headers:
        if re.match(rf"^{tag}:\s*", ln.strip()):
            out.append(f"{tag}: {value}")
            hit = True
        else:
            out.append(ln)
    if not hit:
        out.append(f"{tag}: {value}")
    return out


# ---------------------------------------------------------------- 变换内核

def _shift_note(tok: str, delta: int, pref: int) -> str:
    """单个音符 token 移调 delta 半音。"""
    m = re.match(r"^([_=^]?)([A-Ga-g])([#bn]?)([\',]*)$", tok)
    if not m:
        return tok
    pre, letter, acc, ticks = m.groups()
    up = letter.islower()
    pc = _PC[letter.upper()]
    if pre == "^":
        pc += 1
    elif pre == "_":
        pc -= 1
    if acc == "#":
        pc += 1
    elif acc == "b":
        pc -= 1
    # 八度序号：小写 = +1 个八度，' 每个 +1，, 每个 -1
    octave = (1 if up else 0) + ticks.count("'") - ticks.count(",")
    total = octave * 12 + pc + delta
    o2, pc2 = divmod(total, 12)
    l2, a2 = _spell(pc2, pref)
    return _render(l2, a2, o2)


def _shift_chord(tok: str, delta: int, pref: int) -> str:
    """和弦记号移调：只动根音字母，后缀（m7 / maj7 / sus4…）原样保留。"""
    inner = tok[1:-1]
    m = re.match(r"^([A-G])([#b]?)", inner)
    if not m:
        return tok
    letter, acc = m.groups()
    pc = _PC[letter]
    if acc == "#":
        pc += 1
    elif acc == "b":
        pc -= 1
    l2, a2 = _spell((pc + delta) % 12, pref)
    return '"' + l2 + a2 + inner[m.end():] + '"'


# 平行大小调要动的音级：大调降 3/6/7 度 → 自然小调；小调升 3/6/7 度 → 自然大调
_MAJOR_DEGREES = (4, 9, 11)     # 大三 / 大六 / 大七
_MINOR_DEGREES = (3, 8, 10)     # 小三 / 小六 / 小七


def _modal_note(tok: str, tonic: int, to_minor: bool, pref: int) -> str:
    """平行大小调转换：按相对主音的音级升降，保持八度位置不变。"""
    m = re.match(r"^([_=^]?)([A-Ga-g])([#bn]?)([\',]*)$", tok)
    if not m:
        return tok
    pre, letter, acc, ticks = m.groups()
    pc = _PC[letter.upper()]
    if pre == "^":
        pc += 1
    elif pre == "_":
        pc -= 1
    if acc == "#":
        pc += 1
    elif acc == "b":
        pc -= 1
    octave = (1 if letter.islower() else 0) + ticks.count("'") - ticks.count(",")
    deg = (pc - tonic) % 12
    if to_minor and deg in _MAJOR_DEGREES:
        pc -= 1
    elif not to_minor and deg in _MINOR_DEGREES:
        pc += 1
    total = octave * 12 + pc
    o2, pc2 = divmod(total, 12)
    l2, a2 = _spell(pc2, pref)
    return _render(l2, a2, o2)


def _walk_body(body: str, fn_note, fn_chord, strip_chords: bool) -> str:
    out = []
    for tok in _TOKEN.findall(body):
        if strip_chords and tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
            continue
        if tok.startswith('"'):
            out.append(fn_chord(tok))
        elif re.match(r"^\[.*\]$", tok, re.S) and re.search(r"[A-Ga-g]", tok):
            # 连音块：括号留着，里面的音逐个移
            inner = tok[1:-1]
            out.append("[" + _walk_body(inner, fn_note, fn_chord, False) + "]")
        elif re.match(r"^([_=^]?[A-Ga-g][#bn]?[\',]*)$", tok):
            out.append(fn_note(tok))
        else:
            out.append(tok)
    return "".join(out)


def _new_tempo_line(value: str, tempo: Optional[int], scale: Optional[float]) -> str:
    """改 Q: 行里的数字。既有 `Q: 1/4=88`，也有 `Q: 88`，还有带文字的 `Q: "Allegro" 1/4=120`。"""
    if tempo is None and scale is None:
        return value
    m = re.search(r"(\d+(?:\.\d+)?)\s*$", value)
    m2 = re.search(r"=\s*(\d+(?:\.\d+)?)", value)
    tgt = m2 or m
    if tgt:
        old = float(tgt.group(1))
        new = float(tempo) if tempo is not None else old * float(scale or 1.0)
        new = max(1.0, round(new, 1))
        new_s = str(int(new)) if new == int(new) else str(new)
        return value[:tgt.start(1)] + new_s + value[tgt.end(1):]
    # 原来没有数字：直接写成标准形式
    return f"1/4={int(tempo) if tempo else 88}"


# ---------------------------------------------------------------- 对外主入口

# (主音偏移半音, 目标是否小调)
#   同名（平行）大小调：主音不动，靠动 3/6/7 音级改色彩 —— 旋律必须改音；
#   关系大小调：共用同一组音，主音挪小三度 —— 旋律一个音都不动，只改调号。
#   关系调若也挪旋律，等于把 C 大调变成 A 大调（三个升号），那是另一种错误。
MODE_OPS = {
    "keep":                    (0,  False),  # 不动调式
    "major_to_minor":          (0,  True),   # 同名小调（旋律降 3/6/7 音级）
    "minor_to_major":          (0,  False),  # 同名大调（旋律升 3/6/7 音级）
    "major_to_relative_minor": (-3, True),   # 关系小调：只挪主音，旋律不动
    "minor_to_relative_major": (3,  False),  # 关系大调：只挪主音，旋律不动
}


def transform(abc_text: str,
              transpose: int = 0,
              mode: str = "keep",
              tempo: Optional[int] = None,
              tempo_scale: Optional[float] = None,
              strip_chords: bool = False) -> dict:
    """加工一份 ABC 谱，返回 {abc, changed[], note}。

    transpose：半音，可负；mode 见 MODE_OPS；tempo/tempo_scale 二选一；
    strip_chords：删掉所有 "Am7" 这类和弦记号（谱子就从 full 路线退回 melody 路线）。
    """
    text = abc_text or ""
    headers, body_lines = split_abc(text)
    key_raw = _get_header(headers, "K") or "C"
    tonic, is_minor, pref = _parse_key(key_raw)
    op = MODE_OPS.get(mode or "keep")
    if op is None:
        raise ValueError(f"未知的调式操作：{mode}")
    tonic_shift, want_minor = op
    modal = mode in ("major_to_minor", "minor_to_major")
    if mode == "keep":
        want_minor = is_minor

    # 旋律只跟着 transpose 走；关系大小调额外挪的是主音，不是旋律
    shift = int(transpose or 0)
    new_tonic = (tonic + shift + (0 if modal else tonic_shift)) % 12
    # 拼写倾向按新调的调号重新定：降号调里出现黑键必须写 b（Eb/Ab/Bb），
    # 否则会把 C 小调写成 D#/G#/A#——音高一样，谱面看着像错调。
    new_pref = _pref_of(new_tonic, want_minor)

    def fn_note(tok):
        out = tok
        if shift:
            out = _shift_note(out, shift, new_pref)
        if modal:
            out = _modal_note(out, new_tonic, want_minor, new_pref)
        return out

    def fn_chord(tok):
        return _shift_chord(tok, shift, new_pref) if shift else tok

    new_body = _walk_body("\n".join(body_lines), fn_note, fn_chord, strip_chords)

    if mode != "keep" or shift:
        headers = _set_header(headers, "K", _key_name(new_tonic, want_minor, new_pref))

    q = _get_header(headers, "Q")
    if tempo is not None or tempo_scale is not None:
        headers = _set_header(headers, "Q", _new_tempo_line(q or "", tempo, tempo_scale))

    changed = []
    if shift:
        changed.append(f"移调 {shift:+d} 半音")
    if mode == "major_to_minor":
        changed.append("大调 → 同名小调（3/6/7 音级降半音，旋律改音）")
    elif mode == "minor_to_major":
        changed.append("小调 → 同名大调（3/6/7 音级升半音，旋律改音）")
    elif mode == "major_to_relative_minor":
        changed.append("大调 → 关系小调（旋律一个音不动，只把调性中心挪到下方小三度）")
    elif mode == "minor_to_relative_major":
        changed.append("小调 → 关系大调（旋律一个音不动，只把调性中心挪到上方小三度）")
    if tempo is not None:
        changed.append(f"速度设为 {tempo} BPM")
    elif tempo_scale:
        changed.append(f"速度 ×{tempo_scale}")
    if strip_chords:
        changed.append("去掉和弦记号（谱子退回纯旋律路线）")

    out = "\n".join(headers)
    if new_body.strip():
        out = out + ("\n" if out else "") + new_body
    return {"abc": out, "changed": changed,
            "key": _key_name(new_tonic, want_minor if mode != "keep" else is_minor, new_pref),
            "note": "、".join(changed) or "谱面未改动"}
