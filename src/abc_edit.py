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
    # 小调判定：只认真正的 m / min / minor 后缀。
    # 旧写法 `rest.startswith("m") and not rest.startswith("maj")` 会把
    # `K:Gmix`（混合利底亚，三级是大三度）判成 G 小调，调式转换于是错动 3/6/7 级；
    # `\b` 让 "m" 不能匹配 "mix"/"maj"，只匹配独立的 m。
    minor = bool(re.match(r"^(m|min|minor)\b", rest))
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
    """调号升降数（正=升、负=降）。小调的调号等于它关系大调的调号，所以要 +3 再查表。

    等音等价时取**升降号更少**的那个：C+1 是 Db（5 个降号）而不是 C#（7 个升号），
    G#/Ab、D#/Eb、A#/Bb 同理。旧实现按字典顺序取第一个，于是 +1 一律写成 C#，
    谱面 7 个升号，看着就像错调（同样的音高，读起来完全是另一回事）。"""
    ref = (tonic + 3) % 12 if minor else tonic % 12
    best: Optional[int] = None
    for _name, (pcv, sgv) in _KEY_SIG.items():
        if pcv == ref and (best is None or abs(sgv) < abs(best)):
            best = sgv
    return 0 if best is None else best


def _declared_sig(key_text: str, tonic: int, minor: bool) -> int:
    """按 K: 行**声明的写法**取调号（不做等音改选）。

    输入侧必须这样算：谱面写 K:C# 就是 7 个升号，不能因为"Db 更省事"就按 5 个降号
    去读，否则每个受调号影响的音都差半音——那正是这次要修的那类 bug。
    只有写不出升降倾向（如 K:C）时才退回取最省的那个。"""
    head = (key_text or "").strip()[:3]
    acc = "#" if head[:2].endswith("#") else ("b" if head[:2].endswith("b") else "")
    ref = (tonic + 3) % 12 if minor else tonic % 12
    cands = [v[1] for v in _KEY_SIG.values() if v[0] == ref]
    if not cands:
        return 0
    if acc == "#":
        return max(cands)              # 声明的升号调
    if acc == "b":
        return min(cands)              # 声明的降号调
    return min(cands, key=abs)          # 没写变音：取最省的那个


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


def _accidental_value(pre: str) -> Optional[int]:
    """ABC 前缀变音 → 半音偏移；空前缀返回 None（表示沿用当前状态）。"""
    if not pre:
        return None
    if pre == "=":
        return 0
    if pre in ("^", "^^"):
        return len(pre)
    if pre in ("_", "__"):
        return -len(pre)
    raise ValueError(f"不支持的 ABC 变音记号：{pre!r}")


def _actual_pc(letter_upper: str, pre: str,
               key_acc: dict[str, int]) -> int:
    """字母在调号下的实际半音值（含显式记号与调号自动升降）。
    ABC 变音记号只有前缀：^/^^ 升、_/__ 降、= 还原；显式前缀覆盖调号，
    无前缀才用调用方给出的当前变音状态。真正的逐音符加工还必须把上一音
    留下的“小节内变音”合进 key_acc，不能只传调号（见 transform）。
    """
    explicit = _accidental_value(pre)
    return _PC[letter_upper] + (key_acc.get(letter_upper, 0)
                                if explicit is None else explicit)


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
    r'|[_=^]{0,2}[A-Ga-g][\',]*'    # 音符（变音只认前缀 ^_=，支持 ^^/__ 双重）
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

def _shift_chord(tok: str, delta: int, pref: int) -> str:
    """和弦记号移调：根音与斜杠低音**一起**移，后缀（m7 / maj7 / sus4…）原样保留。

    低音不移会出现"旋律整体走了、和声低音还留在原调"的错位
    （"D7/C" +5 必须是 "G7/F"，旧实现只移根音产出 "G7/C"，生成必然打架）。
    """
    inner = tok[1:-1]
    m = re.match(r"^([A-G][#b]?)([^/]*)(?:/([A-G][#b]?))?$", inner)
    if not m:
        return tok
    root, qual, bass = m.groups()

    def _mv(s: str) -> str:
        pc = _PC[s[0]] + (1 if s.endswith("#") else -1 if s.endswith("b") else 0)
        l, a = _spell((pc + delta) % 12, pref)
        return l + a

    new = _mv(root) + (qual or "")
    if bass:
        new += "/" + _mv(bass)
    return '"' + new + '"'


# 平行大小调要动的音级：大调降 3/6/7 度 → 自然小调；小调升 3/6/7 度 → 自然大调
_MAJOR_DEGREES = (4, 9, 11)     # 大三 / 大六 / 大七
_MINOR_DEGREES = (3, 8, 10)     # 小三 / 小六 / 小七


def _walk_body(body: str, fn_note, fn_chord, strip_chords: bool,
               fn_key=None,
               measure: Optional[dict] = None) -> str:
    """正文变换。行级保护在 token 逻辑之前：inline 字段行（V:/K:/M:/L:/Q:/w: …）
    整行透传——`V: Vocal` 的 Vocal 是声部名不是音符，SheetSage2 每段都写，
    被移调成 `V: Vofd'l` 后声部路由全乱。

    谱中间的 `K:` 行（中途变调）走 fn_key：那一段必须按它**自己的调**算音高与调号。
    旧实现把每一处 inline K: 都改写成文件头的新调名，一段 K:G→K:D 的曲子移调后
    两个调号变成同一个，后半段整段跑调（真实语料里暂时没有中途变调，纯属兜底，
    但错了就是整段错，所以按正确语义实现）。不传 fn_key 就原样透传。

    measure：小节内变音状态 {"in":…, "out":…}，由调用方的闭包持有。
    ABC 规则：显式变音记号（^/_/=/^^/__）一直有效到本小节结束，其后
    **同字母同八度**的裸音全部继承它；小节线处清零。所以含 | 的 token（|、||、|]、
    :|、[| …）和 inline 字段行都要清零状态；注释行里的 | 不算小节线。
    漏掉这个状态是"逐音只看调号"的旧实现差半音的根因。"""
    def _reset():
        if measure is not None:
            measure["in"].clear()
            measure["out"].clear()

    out = []
    lines = body.split("\n")
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if _HEADER.match(stripped):
            _reset()
            m = re.match(r"^K:\s*(.*)$", stripped, re.S)
            if fn_key is not None and m:
                out.append("K: " + fn_key(m.group(1).strip()))
            else:
                out.append(line)
        else:
            for tok in _TOKEN.findall(line):
                if strip_chords and tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
                    continue
                if tok.startswith('"'):
                    out.append(fn_chord(tok))
                elif re.match(r"^\[[A-Za-z]:", tok):
                    # 行内字段（ABC 标准的 [K:G] 写法）：整块透传，只有 K: 要跟着变调。
                    # 不拦住它的话会被下面"连音块"逻辑当成音符块，把调名里的字母
                    # 当音符移调——K:C 升 1 半音本该写 [K:Db]，实际会写成一个非法调名。
                    mf = re.match(r"^\[([A-Za-z]):\s*([^\]]*)\]$", tok)
                    if mf and mf.group(1) in ("K", "k") and fn_key is not None:
                        out.append("[K:" + fn_key(mf.group(2).strip()) + "]")
                    else:
                        out.append(tok)
                elif re.match(r"^\[.*\]$", tok, re.S) and re.search(r"[A-Ga-g]", tok):
                    # 连音块：括号留着，里面的音逐个移（共享小节变音状态）
                    inner = tok[1:-1]
                    out.append("[" + _walk_body(inner, fn_note, fn_chord, False,
                                                None, measure) + "]")
                elif re.match(r"^([_=^]{0,2}[A-Ga-g][\',]*)$", tok):
                    out.append(fn_note(tok))
                else:
                    out.append(tok)
                    if "|" in tok and not tok.startswith("%"):
                        _reset()
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
    #    正文里的字段行（中途变调 K: F#、行内 [K:Bb]）必须先摘掉：
    #    调名里的 # 是合法的，不摘会把"谱子带了中途变调"误判成坏谱。
    music = "\n".join(l for l in body_lines if not _HEADER.match(l.strip()))
    music = re.sub(r"\[[A-Za-z]:[^\]]*\]", "", music)      # 行内字段 [K:G]
    bare = _QUOTED_STR.sub('""', re.sub(r"%[^\n]*", "", music))
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
    tonic, is_minor, _pref = _parse_key(key_raw)
    op = MODE_OPS.get(mode or "keep")
    if op is None:
        raise ValueError(f"未知的调式操作：{mode}")
    tonic_shift, target_minor = op
    if mode == "keep":
        target_minor = is_minor
    # 源调式已经等于目标调式 → 这次"转换"必须什么都不做。
    # 只改 K: 行、旋律不动，等于把同一串裸音放进了另一个调号里：
    # K:E（4 升）的 `f` 是 F#，写成 K:G（1 升）后同一个 `f` 变成 F，全曲低半音。
    # 用户听到的就是"调式转换之后很不正常"——所以这里直接按 no-op 处理。
    noop_mode = mode != "keep" and target_minor == is_minor
    if noop_mode:
        tonic_shift = 0
    want_minor = is_minor if noop_mode else target_minor
    modal = mode in ("major_to_minor", "minor_to_major") and not noop_mode

    # 旋律只跟着 transpose 走；关系大小调额外挪的是主音，不是旋律
    shift = int(transpose or 0)
    new_tonic = (tonic + shift + (0 if modal else tonic_shift)) % 12
    # 关系大小调**不换调号**：新调必须沿用源调的升降数。否则 C# 大调（7 升）的
    # 关系小调会被"少用升降号"优化成 Bbm（5 降）——同一个 `f` 从 F# 变成 F，
    # 整曲低半音，而这正是"关系调只改调号、旋律不动"最容易翻车的地方。
    relative_keeps_sig = (mode in ("major_to_relative_minor", "minor_to_relative_major")
                          and not noop_mode)
    src_sig = _declared_sig(key_raw, tonic, is_minor)
    new_sig = (src_sig if (relative_keeps_sig and not shift)
               else _sig_of(new_tonic, want_minor))
    # 拼写倾向由新调号决定：降号调里出现黑键必须写 b（Eb/Ab/Bb），
    # 否则会把 C 小调写成 D#/G#/A#——音高一样，谱面看着像错调。
    new_pref = 1 if new_sig >= 0 else -1

    # 调号字母偏移：算音高必须把 K: 行的自动升降叠进去（见 _actual_pc 注释）。
    # 旧测试全是 K:C（无升降）才漏到今天；SheetSage2 输出 K:E 等带号调必踩。
    key_acc = _key_accidentals(src_sig)
    new_key_acc = _key_accidentals(new_sig)

    # 小节内变音状态：输入侧读继承（显式 ^/_/= 之后同小节同字母同八度的裸音全继承，
    # 漏读会差半音）；输出侧管拼写——裸字母会继承输出小节里前一个显式变音，
    # 必要时必须写 = 还原号抵消，否则读谱的人/引擎会听到另一个音。
    measure = {"in": {}, "out": {}}
    _NOTE_TOK = re.compile(r"^([_=^]{0,2})([A-Ga-g])([\',]*)$")
    # "当前段落"的调性：文件头是一段，谱中间每遇到 K: 就换一段。
    # 中途变调后音高必须按新段自己的调号算，不能沿用文件头（否则整段跑调）。
    cur = {"in": key_acc, "out": new_key_acc, "pref": new_pref, "tonic": tonic}

    def fn_key(value):
        """谱中间的 K: 行：算出这一段的原调与新调，并切换当前段落状态。"""
        if not value.strip():
            return _key_name(cur["tonic"] + shift, want_minor, cur["pref"])
        t_i, m_i, _p = _parse_key(value)
        cur["in"] = _key_accidentals(_declared_sig(value, t_i, m_i))
        cur["tonic"] = t_i
        ni = (t_i + shift + (0 if modal else tonic_shift)) % 12
        nm = m_i if (mode == "keep" or noop_mode) else want_minor
        # 关系调保调号（见上面 relative_keeps_sig 的说明），本段按自己的调号算
        sig_i = (_declared_sig(value, t_i, m_i)
                 if (relative_keeps_sig and not shift) else _sig_of(ni, nm))
        cur["out"] = _key_accidentals(sig_i)
        cur["pref"] = 1 if sig_i >= 0 else -1
        return _key_name(ni, nm, cur["pref"])

    def fn_note(tok):
        if not shift and not modal:
            return tok  # 不动音高的加工对谱面必须是逐字节 no-op
        m = _NOTE_TOK.match(tok)
        if not m:
            return tok
        pre, letter, ticks = m.groups()
        lu = letter.upper()
        octave = (1 if letter.islower() else 0) + ticks.count("'") - ticks.count(",")
        try:
            expl = _accidental_value(pre)
        except ValueError:
            return tok  # 不认识的变音组合，原样透传
        # 输入实际音高：显式变音 > 小节内继承 > 调号自动升降。
        # 继承范围是「同字母 + 同八度」——ABC 2.1 与 abcjs 都这么算：
        # `^^C` 只影响本小节的其他 `C`，不影响 `c`/`c'`。按字母记状态会把
        # 另一个八度的同名音也改掉（真实谱里出现过 `^A`，这类错差一个半音）。
        slot = f"{lu}{octave}"
        if expl is not None:
            pc = _PC[lu] + expl
            measure["in"][slot] = pc % 12
        else:
            pc = measure["in"].get(slot, _PC[lu] + cur["in"].get(lu, 0))
        # 移调与调式偏移一次性累加——避免"先 shift 再 modal"时中间调号失配
        if shift:
            pc += shift
        if modal:
            # 音级相对本段主音判定（本段主音已含 shift）
            deg = (pc - (cur["tonic"] + shift)) % 12
            if want_minor and deg in _MAJOR_DEGREES:
                pc -= 1
            elif (not want_minor) and deg in _MINOR_DEGREES:
                pc += 1
        total = octave * 12 + pc
        o2, pc2 = divmod(total, 12)
        tp = pc2 % 12
        l2, _ = _spell_in_key(pc2, cur["out"], cur["pref"])
        # 输出拼写：不依赖"小节内继承"——需要变音的音一律重复写显式记号，
        # 这样无论下游解析器实不实现继承规则，读到的音高都一致
        # （继承写法 `=f' f'` 对不认继承的解析器就是 F# 错音）。
        key_default = (_PC[l2] + cur["out"].get(l2, 0)) % 12
        slot2 = f"{l2}{o2}"
        inherited = measure["out"].get(slot2, key_default)
        if key_default == tp and inherited == tp:
            prefix = ""
        else:
            alter = tp - _PC[l2]
            if alter not in (0, 1, -1):
                # 防御：极端双重变音走通用黑键拼写（理论不可达，_spell_in_key
                # 保证字母与目标距离 ≤1）
                l2, a2 = _spell(tp, cur["pref"])
                alter = {"#": 1, "b": -1}.get(a2, 0)
            prefix = {0: "=", 1: "^", -1: "_"}[alter]
        measure["out"][slot2] = tp
        head = l2.lower() if o2 >= 1 else l2.upper()
        tail = "'" * (o2 - 1) if o2 >= 1 else "," * (-o2)
        return prefix + head + tail

    # 调式转换与旧和弦必然冲突（如 C 大三和弦配 Cm 的 Eb 旋律），
    # 平行/关系转换没有可靠的和弦重配算法——移除和弦走纯旋律路线，
    # 明说而不是让引擎拿矛盾提示硬生成。
    body_text = "\n".join(body_lines)
    n_chords = len(re.findall(r'"[A-G][#b]?[^"]*"', body_text))
    eff_strip = strip_chords or (modal and n_chords > 0)

    def fn_chord(tok):
        return _shift_chord(tok, shift, cur["pref"]) if shift else tok

    # 什么都不改的请求（含"调式操作 no-op"）必须逐字节不动：连 K: 行的
    # 空白归一化都不做，否则"没变"却和源谱不相等，前端会误判成改过了。
    rewrite_keys = bool(shift) or modal or (mode != "keep" and not noop_mode)
    new_body = _walk_body(body_text, fn_note, fn_chord, eff_strip,
                          fn_key=fn_key if rewrite_keys else None,
                          measure=measure)

    if shift or (mode != "keep" and not noop_mode):
        headers = _set_header(headers, "K", _key_name(new_tonic, want_minor, new_pref))

    q = _get_header(headers, "Q")
    if tempo is not None or tempo_scale is not None:
        headers = _set_header(headers, "Q", _new_tempo_line(q or "", tempo, tempo_scale))

    changed = []
    if shift:
        changed.append(f"移调 {shift:+d} 半音")
    if noop_mode:
        changed.append(
            f"原谱已经是{'小调' if is_minor else '大调'}（K:{key_raw}），"
            "所选调式转换不改变任何音——谱面与调号原样保留")
    elif mode == "major_to_minor":
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
    elif modal and n_chords:
        changed.append(f"调式转换移除 {n_chords} 处和弦记号（原和声属于旧调式，"
                       "沿用会与新旋律打架；需和声请在新调式下重新配）")

    out = "\n".join(headers)
    if new_body.strip():
        out = out + ("\n" if out else "") + new_body
    return {"abc": out, "changed": changed,
            "key": _key_name(new_tonic, want_minor, new_pref),
            "mode_noop": bool(noop_mode),
            "note": "、".join(changed) or "谱面未改动"}
