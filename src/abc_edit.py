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
    """字母 + 变音 + 八度序号 → ABC 写法。octave=0 → 大写无后缀，1 → 小写无后缀。

    变音记号必须是**前缀**（^ 升 / _ 降）——ABC 标准里没有后缀写法，
    `d#'` 这种 `#` 挂在字母后面的写法标准解析器不认（当噪音丢掉，音高错半音）。
    acc 沿用内部 #/b 编码（K: 行调名与和弦记号仍用 #/b，那是标准写法）。
    """
    pre = {"#": "^", "b": "_", "=": "="}.get(acc, "")
    if octave >= 1:
        return pre + letter.lower() + "'" * (octave - 1)
    return pre + letter.upper() + "," * (-octave)


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


# 升号 / 降号在五度圈上的字母顺序（调号按此累加）
_SHARP_LETTERS = ("F", "C", "G", "D", "A", "E", "B")
_FLAT_LETTERS = ("B", "E", "A", "D", "G", "C", "F")


def _key_accidentals(sig: int) -> dict[str, int]:
    """调号对每个字母的半音偏移。sig>0 升号数、<0 降号数。
    例：G 大调 sig=1 → {F: +1}；Eb 大调 sig=-3 → {B,E,A: -1}。
    SheetSage2 输出的谱几乎都带调号且不在音上写显式升降号，
    移调/调式转换必须先把调号叠进音高，否则被调号影响的音全偏半音。"""
    acc: dict[str, int] = {}
    if sig > 0:
        for L in _SHARP_LETTERS[:sig]:
            acc[L] = 1
    elif sig < 0:
        for L in _FLAT_LETTERS[:-sig]:
            acc[L] = -1
    return acc


def _actual_pc(letter_upper: str, pre: str,
               key_acc: dict[str, int]) -> int:
    """字母在调号下的实际半音值（含显式记号与调号自动升降）。
    ABC 变音记号只有前缀：^ 升 / _ 降 / = 还原；显式前缀覆盖调号，
    无前缀才用调号自动升降。
    （不存在"后缀 #b 变音"——`bb` 是 B B 两个音、`Bb` 是 B b 差八度两个音，
    旧实现把后跟的 b 音吞成降号记号，丢音又错音高，2026-10-10 修。）"""
    base = _PC[letter_upper]
    if pre == "^":
        return base + 1
    if pre == "_":
        return base - 1
    if pre == "=":
        return base
    return base + key_acc.get(letter_upper, 0)


def _spell_in_key(target_pc: int, key_acc: dict[str, int],
                   pref: int) -> tuple[str, str]:
    """在指定调号下拼写目标实际音高。优先级：
    1. `_PC[L] == target_pc % 12` 的自然音字母（必要时加显式 `=` 还原）；
    2. 调号自动升降到 target 的字母（谱面最干净）；
    3. 退回 pref 决定升/降号。

    为什么需要第 1 档：移调跨过八度边界时（B4 +1 = C5），调号自动升降的字母可能是
    反向八度的（K:C# 下 B 升 = B#=C6）。若选 B 写谱面 `b`，ABC 解析回 C6，跨了一个八度。
    退回 `_PC_NATURAL[tp]` 用 `C=` 显式还原，读回仍是 C5，与目标一致。
    极端调号（C#/Cb）才会触发；常规 K:A/K:Bb 等值都走第 2 档。
    """
    tp = target_pc % 12
    # 第 1 档：自然音字母（不加任何调号自动升降、仅靠显式还原号表达 target）
    if tp in _PC_NATURAL:
        L = _PC_NATURAL[tp]
        acc = key_acc.get(L, 0)
        if acc == 0:
            return L, ""
        return L, "="  # 显式还原号：抵消调号自动升降
    # 第 2 档：调号自然音字母（被自动升降到 target_pc）
    for L in ("C", "D", "E", "F", "G", "A", "B"):
        if (_PC[L] + key_acc.get(L, 0)) % 12 == tp:
            return L, ""
    # 第 3 档：pref 决定升/降号
    return _spell(tp, pref)


# ---------------------------------------------------------------- 谱面切分

# token 顺序很关键：和弦 / 注释 / 装饰 / 连音块 必须排在单音符之前，
# 否则 "C" 里的 C 会被当成音符移调，和弦标记就跟着跑了（那是歌词不是旋律）。
# 音符正则**不能带后缀变音记号**：ABC 的变音只有前缀（^_=），`bb` 是 B B 两个音、
# `Bb` 是 B b 两个音——带上 `[#bn]?` 会把紧跟的 b 音吞成降号，丢音+错音高+节奏塌缩。
_TOKEN = re.compile(
    r'"[^"]*"'                      # 和弦记号 "Am7"
    r'|%[^\n]*'                     # 行注释
    r'|![^!]*!'                     # 装饰音 !trill!
    r'|\[[A-Za-z0-9#bn\' ,_^=|:]*\]'  # 连音块 [CEG] / [C E G]
    r'|[_=^]?[A-Ga-g][\',]*'        # 音符（变音只认前缀 ^_=）
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

def _shift_note(tok: str, delta: int, pref: int,
                key_acc: dict[str, int] | None = None,
                new_key_acc: dict[str, int] | None = None) -> str:
    """单个音符 token 移调 delta 半音。

    key_acc：原调号字母偏移；不传则按 C 大调（旧行为，仅给无调号场景兜底）。
    new_key_acc：新调号字母偏移，移调后用它把音高写成调号自然记号（干净谱面）；
                 不传则退回纯 pref 拼写（旧测试兼容）。

    关键：算音高时必须把调号的自动升降叠进去——SheetSage2 输出的谱面不带显式升降号，
    全靠 K: 行决定 F/C/G/D 等是不是升半音。漏叠会让 K:E 的 f（实为 F#）被当成 F，
    移调后整条旋律偏低半音，听感上"调都变了"。
    """
    m = re.match(r"^([_=^]?)([A-Ga-g])([\',]*)$", tok)
    if not m:
        return tok
    pre, letter, ticks = m.groups()
    up = letter.islower()
    pc = _actual_pc(letter.upper(), pre, key_acc or {})
    octave = (1 if up else 0) + ticks.count("'") - ticks.count(",")
    total = octave * 12 + pc + delta
    o2, pc2 = divmod(total, 12)
    l2, a2 = (_spell_in_key(pc2, new_key_acc, pref)
              if new_key_acc is not None else _spell(pc2, pref))
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


def _modal_note(tok: str, tonic: int, to_minor: bool, pref: int,
                key_acc: dict[str, int] | None = None,
                new_key_acc: dict[str, int] | None = None) -> str:
    """平行大小调转换：按相对主音的音级升降，保持八度位置不变。

    tonic 为实际主音半音值（_parse_key 已含本身变音记号）。
    key_acc 原调号偏移、new_key_acc 新调号偏移：与 _shift_note 同理——
    不叠调号会算错音级（G# 在 E 大调是 3 级，不叠调号会被当成 G 的 2 级），
    并把目标音写回新调号的自然字母而不是显式升降号。
    """
    m = re.match(r"^([_=^]?)([A-Ga-g])([\',]*)$", tok)
    if not m:
        return tok
    pre, letter, ticks = m.groups()
    up = letter.islower()
    pc = _actual_pc(letter.upper(), pre, key_acc or {})
    octave = (1 if up else 0) + ticks.count("'") - ticks.count(",")
    deg = (pc - tonic) % 12
    if to_minor and deg in _MAJOR_DEGREES:
        pc -= 1
    elif not to_minor and deg in _MINOR_DEGREES:
        pc += 1
    total = octave * 12 + pc
    o2, pc2 = divmod(total, 12)
    l2, a2 = (_spell_in_key(pc2, new_key_acc, pref)
              if new_key_acc is not None else _spell(pc2, pref))
    return _render(l2, a2, o2)


def _walk_body(body: str, fn_note, fn_chord, strip_chords: bool,
               inline_key: Optional[str] = None) -> str:
    """正文变换。行级保护在 token 逻辑之前：inline 字段行（V:/K:/M:/L:/Q:/w: …）
    整行透传——`V: Vocal` 的 Vocal 是声部名不是音符，SheetSage2 每段都写，
    被移调成 `V: Vofd'l` 后声部路由全乱。inline K: 行（谱中间变调）按
    inline_key 重写为新调号；不给 inline_key 就原样透传（SheetSage2 不产
    中途变调，这里只是兜底不破坏）。"""
    out = []
    lines = body.split("\n")
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if _HEADER.match(stripped):
            out.append("K: " + inline_key
                       if (inline_key and stripped.startswith("K:")) else line)
        else:
            for tok in _TOKEN.findall(line):
                if strip_chords and tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
                    continue
                if tok.startswith('"'):
                    out.append(fn_chord(tok))
                elif re.match(r"^\[.*\]$", tok, re.S) and re.search(r"[A-Ga-g]", tok):
                    # 连音块：括号留着，里面的音逐个移
                    inner = tok[1:-1]
                    out.append("[" + _walk_body(inner, fn_note, fn_chord, False) + "]")
                elif re.match(r"^([_=^]?[A-Ga-g][\',]*)$", tok):
                    out.append(fn_note(tok))
                else:
                    out.append(tok)
        if idx < len(lines) - 1:
            out.append("\n")
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


# ---------------------------------------------------------------- 坏谱哨兵
# 2026-10-10 修复前的旧版 abc_edit 有两个会把谱面越改越坏的 bug：
#   ① 正文 V: 行的声部名被当音符移调（"V: Vocal" → "V: Vobg'l"）；
#   ② _render 用后缀 #/b 写变音记号（标准 ABC 只有前缀 _=^，后缀是另一个音符），
#     且旧正则把 bb/gb 这类"两个相邻音"并成一个音——吞掉的音再也回不来。
# 坏谱存在前端 localStorage（LabStore）里，用户每次"再调一次"都在坏谱上叠错。
# 实测案例：原始 K:D 谱被叠了三次 +5（D→G→C→F，累计 +15），每层都带声部名破坏
# 与非法记号。这里在入口把旧版指纹揪出来，拒绝加工并指出出路。

_QUOTED_STR = re.compile(r'"[^"]*"')


def _declared_voices(headers: list) -> set:
    """头部 V: 行声明的声部名：裸名 + name="/snm=" 里的每个词。"""
    names = set()
    for h in headers:
        if not h.startswith("V:"):
            continue
        rest = h[2:].strip()
        m = re.match(r"([A-Za-z][\w.]*)", rest)
        if m:
            names.add(m.group(1))
        for m2 in re.finditer(r'(?:name|snm|subname)\s*=\s*"([^"]*)"', rest):
            for w in m2.group(1).split():
                names.add(w)
    return names


def _legacy_corruption(abc_text: str) -> list:
    """返回旧版坏谱指纹列表（空 = 干净）。只报"确定是坏"的，不做猜测性告警。"""
    problems = []
    headers, body_lines = split_abc(abc_text or "")
    body = "\n".join(body_lines)
    declared = _declared_voices(headers)
    # ① 正文 V: 行声部名：带八度撇号/逗号 = 被旧版当音符移调过（确定指纹）；
    #    或与头部声明撞两字母前缀但不在声明里（Vobg'l vs Vocal）。
    for m in re.finditer(r"^V:\s*(\S+)", body, re.M):
        name = m.group(1)
        if name in declared:
            continue
        if "'" in name or "," in name:
            problems.append(f"声部名 {name!r} 带八度记号——旧版把声部名当音符移调的破坏痕迹")
        elif declared and any(len(n) >= 2 and n[:2].lower() == name[:2].lower() for n in declared):
            problems.append(f"声部名 {name!r} 不在头部声明的 {sorted(declared)} 里，疑似被旧版移调破坏")
    # ② 音符后缀 # 变音记号：标准 ABC 的 # 只出现在引号和弦名里（如 "F#7"），
    #    音符后紧跟 # 一定是旧版 _render 的后缀写法残留（如 c#'2）。合并成一条。
    bare = _QUOTED_STR.sub('""', re.sub(r"%[^\n]*", "", body))
    n_sharp = len(re.findall(r"[_=^]?[A-Ga-g][\',]*#", bare))
    if n_sharp:
        problems.append(f"音符后缀 # 变音记号 ×{n_sharp} 处（如 c#'2）不是合法 ABC——旧版残留")
    return problems


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
    bad = _legacy_corruption(text)
    if bad:
        # 同类指纹去重合并（坏谱里 V: 行有几十处，逐条列只会刷屏）
        counts: dict = {}
        for p in bad:
            counts[p] = counts.get(p, 0) + 1
        lines = [f"{p}（×{n} 处）" if n > 1 else p for p, n in counts.items()]
        raise ValueError(
            "这份谱带旧版加工的破坏痕迹，继续调只会越叠越错：\n- "
            + "\n- ".join(lines[:6])
            + ("\n…" if len(lines) > 6 else "")
            + "\n请从原始谱重新加工：『乐谱加工』页 → 谱库选原始谱（或重新转谱）→ 再设移调。"
        )
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

    # 调号字母偏移：算音高必须把 K: 行的自动升降叠进去（见 _actual_pc 注释）。
    # 旧测试全是 K:C（无升降）才漏到今天；SheetSage2 输出 K:E 等带号调必踩。
    key_acc = _key_accidentals(_sig_of(tonic, is_minor))
    new_key_acc = _key_accidentals(_sig_of(new_tonic, want_minor))

    def fn_note(tok):
        m = re.match(r"^([_=^]?)([A-Ga-g])([\',]*)$", tok)
        if not m:
            return tok
        pre, letter, ticks = m.groups()
        up = letter.islower()
        # 原实际音高（含原调号自动升降）
        pc = _actual_pc(letter.upper(), pre, key_acc)
        octave = (1 if up else 0) + ticks.count("'") - ticks.count(",")
        # 移调与调式偏移一次性累加——避免"先 shift 再 modal"时中间调号失配
        if shift:
            pc += shift
        if modal:
            # 音级相对新主音判定（new_tonic 已含 shift）
            deg = (pc - new_tonic) % 12
            if want_minor and deg in _MAJOR_DEGREES:
                pc -= 1
            elif (not want_minor) and deg in _MINOR_DEGREES:
                pc += 1
        total = octave * 12 + pc
        o2, pc2 = divmod(total, 12)
        l2, a2 = _spell_in_key(pc2, new_key_acc, new_pref)
        return _render(l2, a2, o2)

    def fn_chord(tok):
        return _shift_chord(tok, shift, new_pref) if shift else tok

    new_body = _walk_body("\n".join(body_lines), fn_note, fn_chord, strip_chords,
                          inline_key=_key_name(new_tonic, want_minor, new_pref))

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
