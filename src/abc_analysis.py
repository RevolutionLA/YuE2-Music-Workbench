"""ABC 乐谱分析：调性 / 拍号 / BPM / 曲式 / 音域 / 和弦判定（纯函数，无 app 依赖）。

从 app.py 拆出；`_resolve_cot`、`/abc/analyze`、乐谱入库都调用这里。
"""

from __future__ import annotations

import re

_KEY_MAP = {"C": "C 大调", "G": "G 大调", "D": "D 大调", "A": "A 大调", "E": "E 大调", "B": "B 大调",
            "F#": "升 F 大调", "C#": "升 C 大调", "F": "F 大调", "Bb": "降 B 大调", "Eb": "降 E 大调",
            "Ab": "降 A 大调", "Db": "降 D 大调", "Gb": "降 G 大调",
            "Am": "A 小调", "Em": "E 小调", "Bm": "B 小调", "F#m": "升 F 小调", "C#m": "升 C 小调",
            "G#m": "升 G 小调", "Dm": "D 小调", "Gm": "G 小调", "Cm": "C 小调", "Fm": "F 小调",
            "Bbm": "降 B 小调", "Ebm": "降 E 小调"}
_PITCH_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_SECTION_RE = re.compile(r"^\[([^\]\r\n]{1,32})\]", re.M)
_VOICE_RE = re.compile(r"^V:\s*([A-Za-z0-9_\-]+)", re.M)
# YuE2 原生引号和弦词汇（大三和弦无后缀，故根音可单独成词）+ 可选斜杠低音。
_ABC_QUALITIES = ("maj7", "m(maj7)", "m7b5", "7sus4", "dim7", "sus4", "sus2",
                  "aug", "dim", "maj", "min", "m6", "m7", "m", "6", "7")
_CHORD_RE = re.compile(
    r'"[A-G](?:##|bb|[b#])?(?:'
    + "|".join(re.escape(q) for q in _ABC_QUALITIES)
    + r')?(?:/[A-G](?:##|bb|[b#])?)?"')
# 存在性判定用宽松式：拿闭集判存在会把 C9/Cadd9/G13/Fm9 判成无和弦，进而错路由到 melody。
_CHORD_RE_LOOSE = re.compile(
    r'"[A-G](?:##|bb|[b#])?([A-Za-z0-9()#\-]*)(?:/[A-G](?:##|bb|[b#])?)?"')
_CHORD_TAIL_CHARS = set("0123456789#()-+/.majinsdugMAJINSDG")
_CHORD_TAIL_WORDS = ("alt", "add", "dim", "aug", "sus", "maj", "min", "no", "A7", "b5")


def is_chord_token(text: str) -> bool:
    """宽松式命中后的一遍词形校验：排除"以 A–G 开头的普通英文单词"这类假阳性。"""
    rest = text
    for word in _CHORD_TAIL_WORDS:
        rest = rest.replace(word, "")
    return all(ch in _CHORD_TAIL_CHARS for ch in rest)


def chord_tokens(abc: str, loose: bool = True) -> list[str]:
    """正文小节里的和弦记号列表（loose=存在性判定，strict=记号表可改写范围）。"""
    body = "\n".join(l for l in abc.splitlines()
                     if not re.match(r"^[A-Za-z]:", l.strip())
                     and not l.lstrip().startswith("%"))
    if not loose:
        return _CHORD_RE.findall(body)
    out = []
    for tail in _CHORD_RE_LOOSE.findall(body):
        if is_chord_token(tail):
            out.append(tail)
    return out


def has_chords(abc: str) -> bool:
    """谱面是否带和弦声部/和弦记号（决定 YuE2 该走 full 还是 melody 路线）。"""
    if any("chord" in v for v in (name.lower() for name in _VOICE_RE.findall(abc))):
        return True
    return bool(chord_tokens(abc))


def analyze(abc: str) -> dict:
    """从 ABC 文本提取调性/拍号/默认速度/曲式段落/音域，供前端展示摘要。"""
    out: dict = {}
    m = re.search(r"^K:\s*(\S+)", abc, re.M)
    if m:
        raw = m.group(1).strip()
        out["key_raw"] = raw
        out["key"] = _KEY_MAP.get(raw, _KEY_MAP.get(raw.rstrip("m").strip() + "m", raw))
    m = re.search(r"^M:\s*(\S+)", abc, re.M)
    if m:
        out["meter"] = m.group(1)
    m = re.search(r"^Q:\s*(?:\"[^\"]*\"|'[^']*')?\s*(?:\d+\s*/\s*\d+)?\s*=\s*(\d+)", abc, re.M)
    if m:
        try:
            out["bpm"] = int(m.group(1))
        except ValueError:
            pass
    sections, seen = [], set()
    for sm in _SECTION_RE.finditer(abc):
        name = sm.group(1).strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            sections.append(name)
    if sections:
        out["sections"] = sections

    pitches: list[int] = []
    in_music = False
    for line in abc.splitlines():
        s = line.strip()
        if s.startswith(("K:", "M:", "Q:", "L:", "T:", "C:", "W:", "w:", "%%", "X:")):
            continue
        if s.upper().startswith("V:") or (s and not s[:1].isalpha()):
            in_music = True
        if s.upper().startswith("V:"):
            continue
        if in_music:
            for pm in re.finditer(r"(_|\^|=)?([A-Ga-g])([',]*)", re.sub(r'"[^"]*"', " ", s)):
                acc, letter, octs = pm.groups()
                semi = _PITCH_SEMITONE[letter.upper()] + ({"_": -1, "^": 1}.get(acc, 0))
                if letter.islower():
                    semi += 12
                for ch in octs:
                    semi += 12 if ch == "'" else -12
                pitches.append(semi)
    out["note_count"] = len(pitches)
    if pitches:
        names_sharp = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        lo, hi = min(pitches), max(pitches)

        def name(s: int) -> str:
            return names_sharp[s % 12] + str(s // 12 + 4)

        out["range"] = f"{name(lo)} → {name(hi)}（{hi - lo} 个半音）"
    voices = list(dict.fromkeys(_VOICE_RE.findall(abc)))
    if voices:
        out["voices"] = voices
    tokens = chord_tokens(abc)
    chords = bool(tokens) or has_chords(abc)
    out["has_chords"] = chords
    out["chord_count"] = len(tokens)
    out["cot_suggested"] = "full" if chords else "melody"
    return out
