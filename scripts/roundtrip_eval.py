# -*- coding: utf-8 -*-
"""往返回归实验：识别 → 加工 → 生成 → 再识别 → 比对。

为什么需要它（这不是"再测一遍移调"）：移调算术由 `tests/test_abc_edit.py` 和
`tests/abc_oracle.js`（第三方 abcjs 解析）逐音证明，秒级可复现。这里要回答的是
**只有跑真模型才能回答**的三个问题：

  1. 引擎到底按不按我们写进谱里的调唱？（它是编译好的 GGUF，仓库里没有它的解析源码）
  2. 移调 / 换调式之后，唱出来的东西还对不对？
  3. 人声的绝对音区，是不是能靠移调控制？

成本：引擎支持 `semantic_max_tokens` 限长，36 秒片段一首约 3–4 分钟（生成 3.5 分 + 转谱 1.5 分），
全曲要 60 分钟以上——所以本脚本默认只切**前 N 小节**当实验材料，不做全曲往返。

用法（工作台在跑才有效；引擎没起会自动跳过，返回 0）::

    py312\\python.exe scripts\\roundtrip_eval.py --list
    py312\\python.exe scripts\\roundtrip_eval.py                    # 跑全部组，已完成自动跳过
    py312\\python.exe scripts\\roundtrip_eval.py base p5            # 只跑指定组
    py312\\python.exe scripts\\roundtrip_eval.py base --force       # 重跑（覆盖旧结果）
    py312\\python.exe scripts\\roundtrip_eval.py --report           # 只重算报告表
    py312\\python.exe scripts\\roundtrip_eval.py --score a.abc --bars 8 --lyrics l.txt

产物都在 `runtime/roundtrip/`（不入库）：`scores/*.abc`、`audio/*.wav`、`manifest.json`。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(ROOT, "src"), ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import abc_compare  # noqa: E402
import abc_edit  # noqa: E402

ENGINE = os.environ.get("YUE2_ENGINE", "http://127.0.0.1:8080")
GATEWAY = os.environ.get("YUE2_GATEWAY", "http://127.0.0.1:7863/api")
WORKDIR = os.path.join(ROOT, "runtime", "roundtrip")
SEED = 20261026

#: 实验组：名字 → abc_edit.transform 的参数。
#: `seed_offset` 不是加工参数，是"在这一轮基准种子上加几"——`rep` 用它做噪声地线。
GROUPS = {
    "base":   {},                                   # 原调基准
    "m3":     {"transpose": -3},
    "m5":     {"transpose": -5},
    "p5":     {"transpose": 5},
    "p7":     {"transpose": 7},
    "p12":    {"transpose": 12},                    # 只换八度：验证"移调能不能当换声区用"
    "modal":  {"mode": "major_to_minor"},
    "rep":    {"seed_offset": 1},                   # 噪声地线：同一份谱、换个种子
}

GROUP_NOTES = {
    "rep": "同一份谱只换种子。没有它，分不清'声区/贴合度的差异'是引擎按移调走的还是随机抖动",
    "p12": "谱面真高一个八度，但引擎会折回自己的舒声区——这条就是'移调当不了换声区'的证据",
    "modal": "同名大小调：旋律本身被改（3/6/7 级），不是平移",
}

PLACEHOLDER_LYRICS = "[Verse]\n啦 啦 啦 啦\n"


# ---------------------------------------------------------------- 环境探测

def _get(url: str, timeout: float = 3.0) -> bytes:
    """本机回环不走系统代理——否则 httpx/urllib 会把 127.0.0.1 也塞进代理，全 502。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as r:
        return r.read()


def probe() -> tuple[bool, bool, str]:
    """返回 (引擎在线, 网关在线, 说明)。探测失败不抛异常——缺件是常态，不是错误。

    探测用的必须是**真的存在**的只读端点：曾经探 `/api/version`，而网关根本没有这个
    路由（返回 404），于是脚本每次都"网关没起"直接跳过，看起来像环境问题、
    其实是探测自己写错了。`/api/scores` 是列表接口，只读、无副作用。
    """
    engine = gateway = False
    reasons = []
    try:
        _get(f"{ENGINE}/health")
        engine = True
    except (urllib.error.URLError, OSError, ValueError) as e:
        reasons.append(f"引擎 {ENGINE} 不可用：{e}")
    try:
        _get(f"{GATEWAY}/scores")
        gateway = True
    except (urllib.error.URLError, OSError, ValueError) as e:
        reasons.append(f"网关 {GATEWAY} 不可用：{e}")
    return engine, gateway, "；".join(reasons)


# ---------------------------------------------------------------- 生成 / 转谱

def generate(abc: str, lyrics: str, out_wav: str, *, seed: int = SEED,
             max_tok: int = 900, steps: int = 32, cot: str = "full",
             style: str = "Mandarin pop ballad, female vocal, warm acoustic, 82 BPM") -> dict:
    """直接打引擎的 `POST /v1/tasks/run`（不经网关，避免排队与超时策略干扰实验）。"""
    request = {"task": "gen", "style": style, "lyrics": lyrics, "cot": cot,
               "abc": abc, "seed": seed, "num_inference_steps": steps,
               "semantic_max_tokens": max_tok}
    body = {"model": "yue2", "request": dict(request, options=dict(request))}
    t0 = time.time()
    req = urllib.request.Request(f"{ENGINE}/v1/tasks/run",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=7200) as r:
        data = json.loads(r.read())
    used = time.time() - t0
    payload = data.get("audio") or ""
    if not payload:
        for art in data.get("artifacts") or []:
            if (art.get("meta") or {}).get("format") == "wav":
                payload = art.get("payload") or ""
                break
    if not payload:
        raise RuntimeError(f"引擎没回音频（返回字段：{sorted(data)}）")
    raw = base64.b64decode(payload)
    os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
    with open(out_wav, "wb") as f:
        f.write(raw)
    timing = data.get("timing") or {}
    return {"wav": os.path.basename(out_wav), "sec": round(used, 1), "bytes": len(raw),
            "audio_ms": timing.get("audio_duration_ms"), "rtf": timing.get("rtf"),
            "seed": seed, "md5": hashlib.md5(raw).hexdigest()[:12]}


def transcribe(wav: str, *, melody_only: bool = True, timeout: float = 1800) -> str:
    """调网关的转谱接口（和工作台 ③ 用的是同一条路径、同一个模型）。"""
    boundary = "----roundtrip%d" % int(time.time() * 1000)
    with open(wav, "rb") as f:
        audio = f.read()
    parts = []
    for name, value in (("melody_only", "true" if melody_only else "false"),):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                     f"{value}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; "
                 f"filename=\"{os.path.basename(wav)}\"\r\n"
                 f"Content-Type: audio/wav\r\n\r\n".encode() + audio + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"{GATEWAY}/score", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=180) as r:
        job = json.loads(r.read())["job_id"]
    t0 = time.time()
    while True:
        time.sleep(2)
        with opener.open(f"{GATEWAY}/score/result?job_id={job}", timeout=60) as r:
            d = json.loads(r.read())
        if d.get("done"):
            if d.get("error"):
                raise RuntimeError(d["error"])
            return d.get("abc") or ""
        if time.time() - t0 > timeout:
            raise TimeoutError("转谱超时")


# ---------------------------------------------------------------- 切实验片段

_BAR_TOK = re.compile(r":\|+\]?|\[\||\|\]|\|\||\|:|\|")


def cut_excerpt(text: str, bars: int = 8, title: str = "roundtrip excerpt") -> str:
    """从整谱里切前 `bars` 小节的人声旋律，保留原调号/拍号/时值单位。

    只取 Vocal 声部：SheetSage2 的整谱里还有和弦与伴奏声部，混进去就不是"唱这一条旋律"了。
    不手抄谱、不改任何音——实验的输入必须是工作台真识别出来的东西，否则验不到链路。

    按**小节线**截断而不是按行：真实转谱的一段 8 小节常常挤在同一行里，
    按行切会让 `--bars` 形同虚设（这也是生成成本的唯一闸门，虚设就没有实验预算了）。
    截断点若正好落在连音线上（`E2-`），把悬空的 `-` 去掉——留着就是一根接不上东西的线。
    """
    headers, body = abc_edit.split_abc(text)
    key = abc_edit._get_header(headers, "K") or "C"
    meter = abc_edit._get_header(headers, "M") or "4/4"
    unit = abc_edit._get_header(headers, "L") or "1/8"
    tempo = abc_edit._get_header(headers, "Q") or "1/4=90"
    voice, seen_voice, keep, count = None, False, [], 0
    for line in body:
        s = line.strip()
        if not s or s.startswith("%"):
            continue
        if abc_edit._HEADER.match(s):
            head, _, value = s.partition(":")
            if head.strip().upper() == "V":
                seen_voice = True
                voice = value.strip()
            continue
        if s.startswith("w:"):
            continue                                    # 歌词行与切出来的片段不再对齐，丢掉
        if seen_voice and not (voice or "").lower().startswith("vocal"):
            continue
        # 跳过整行没音高内容的（前奏的空小节 `z16`、多小节休止 `Z|`）。
        # 不跳的话切片会从"歌词还没进"的地方开始，生成出来是一段无人声的伴奏，
        # 后面所有指标都变成噪声——踩过一次，别省这一步。
        if not re.search(r"[A-Ga-g]", re.sub(r'"[^"]*"', "", s)):
            continue
        ends = [m.end() for m in _BAR_TOK.finditer(s)]
        need = bars - count
        if len(ends) >= need:
            piece = s[:ends[need - 1]].rstrip()
            if piece.endswith("-"):
                piece = piece[:-1].rstrip()
            keep.append(piece)
            count = bars
            break
        keep.append(s)
        count += len(ends)
        if count >= bars:
            break
    if not keep:
        raise ValueError("这份谱里没找到可用的旋律行（是不是只有和弦、没有音符？）")
    return "\n".join([f"X:1", f"T:{title}", f"M:{meter}", f"L:{unit}",
                      f"Q:{tempo}", f"K:{key}"] + keep) + "\n"


def lyrics_from(abc: str) -> str:
    """谱里带 `w:` 歌词行就直接用；否则返回空串由调用方决定。"""
    words = [l.split(":", 1)[1].strip() for l in abc.splitlines()
             if l.strip().lower().startswith("w:")]
    words = [w for w in words if w]
    return "[Verse]\n" + "\n".join(words) + "\n" if words else ""


# ---------------------------------------------------------------- 主流程

def _latest_score() -> str:
    base = os.path.join(ROOT, "runtime", "data", "scores")
    cands = []
    for d in os.listdir(base) if os.path.isdir(base) else []:
        p = os.path.join(base, d, "score.abc")
        if os.path.isfile(p):
            cands.append((os.path.getmtime(p), p))
    return max(cands)[1] if cands else ""


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def chroma_rotation(base: list[float], cand: list[float]) -> tuple[int, float]:
    """12 维色度的整体旋转量：`cand` 比 `base` 高几（模 12）个半音，附带相关。

    纯 Python 实现（不碰 numpy），因为这段符号最容易搞反——搞反了所有"音频 Δ"
    的结论都会指反方向。约定与 `abc_compare` 一致：**正数 = cand 更高**。

    `rot[i] = cand[i + k]` 是"把 cand 降 k 格"，降到与 base 对齐时 k 就是
    cand 高出的半音数（不是 −k）。
    """
    best_k, best_c = 0, -2.0
    for k in range(12):
        rot = [cand[(i + k) % 12] for i in range(12)]
        c = abc_compare.corr(base, rot)
        if c > best_c:
            best_k, best_c = k, c
    return (best_k if best_k <= 6 else best_k - 12), round(best_c, 3)


def audio_shift_vs(base_wav: str, wav: str) -> tuple[int, float] | tuple[None, None]:
    """绕过转谱的第三把尺子：直接在混音上算色度再求整体旋转。

    转谱模型自己可能听错，所以"转录谱说了什么"不能当唯一证据；这一路只依赖音频。
    混音里贝斯/低音弦乐会把重心往下拽，所以它只能当旁证，不能单独下结论。
    """
    try:
        import librosa
        import numpy as np
    except ImportError:
        return None, None
    try:
        a, sr = librosa.load(base_wav, sr=22050, mono=True)
        b, _ = librosa.load(wav, sr=22050, mono=True)
        ca = np.mean(librosa.feature.chroma_cqt(y=a, sr=sr), axis=1).tolist()
        cb = np.mean(librosa.feature.chroma_cqt(y=b, sr=sr), axis=1).tolist()
        return chroma_rotation(ca, cb)
    except Exception:  # noqa: BLE001  # 缺 librosa / 音频坏了都不该让整个实验崩
        return None, None


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="往返回归实验（识别→加工→生成→再识别→比对）")
    ap.add_argument("groups", nargs="*", help="只跑这些组（默认全部）：" + "、".join(GROUPS))
    ap.add_argument("--score", default="", help="用这份 ABC 当实验材料（默认取最新一次转谱）")
    ap.add_argument("--bars", type=int, default=8, help="切前几小节（默认 8，约 20 秒）")
    ap.add_argument("--lyrics", default="", help="歌词文件（默认用谱里的 w: 行，没有就用占位词）")
    ap.add_argument("--workdir", default=WORKDIR)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--max-tok", type=int, default=900, help="semantic_max_tokens，越小越短越快")
    ap.add_argument("--steps", type=int, default=32)
    ap.add_argument("--style", default="Mandarin pop ballad, female vocal, warm acoustic, 82 BPM")
    ap.add_argument("--force", action="store_true", help="已有结果也重跑")
    ap.add_argument("--report", action="store_true", help="只重算报告表，不生成")
    ap.add_argument("--list", action="store_true", help="列出实验组后退出")
    args = ap.parse_args(argv)

    if args.list:
        for name, kw in GROUPS.items():
            note = GROUP_NOTES.get(name)
            print(f"{name:6} {kw}" + (f"\n       ↳ {note}" if note else ""))
        print(f"\n基准种子 --seed {args.seed}（`rep` 用基准种子 +1）。"
              "同谱同种子 → 音频逐字节相同，所以组间差异只来自谱。")
        return 0

    work = os.path.abspath(args.workdir)
    man_path = os.path.join(work, "manifest.json")
    man: dict = {}
    if os.path.isfile(man_path):
        man = json.loads(_read(man_path))
    if args.report:
        return _report(man, work)

    wanted = args.groups or list(GROUPS)
    unknown = [g for g in wanted if g not in GROUPS]
    if unknown:                                     # 先校验再探测：点错名字要当场说清楚
        print(f"[err] 没有这些组：{unknown}；可选 {list(GROUPS)}")
        return 2

    engine_ok, gate_ok, why = probe()
    if not (engine_ok and gate_ok):
        print(f"[skip] 工作台没起全，这次不做往返实验：{why}")
        print("       这不是失败——生成要真模型，缺件时跳过比伪装通过好。")
        return 0

    src_path = args.score or _latest_score()
    if not src_path or not os.path.isfile(src_path):
        print("[skip] 找不到可用的谱：用 --score 指定一份 ABC，或先在 ③ 里提取一份乐谱。")
        return 0
    excerpt = cut_excerpt(_read(src_path), args.bars)
    _write(os.path.join(work, "scores", "_excerpt.abc"), excerpt)
    lyrics = _read(args.lyrics) if args.lyrics else lyrics_from(excerpt)
    if not lyrics:
        lyrics = PLACEHOLDER_LYRICS
        print("[warn] 没给 --lyrics 也没找到 w: 歌词行，用占位词；这一轮只能看音高，别当成品听。")
    print(f"[src] {src_path}")
    print(f"[src] 片段 {len(excerpt)} 字节，调号 {abc_compare.key_of(excerpt)}，"
          f"音数 {len(abc_compare.notes(excerpt))}")

    failed = 0
    for name in wanted:
        kw = dict(GROUPS[name])
        seed = args.seed + kw.pop("seed_offset", 0)
        sp = os.path.join(work, "scores", f"{name}.abc")
        wav = os.path.join(work, "audio", f"{name}.wav")
        rec = man.get(name) or {}
        if rec.get("trans_file") and rec.get("wav") and not args.force:
            print(f"[skip] {name} 已完成（--force 可重跑）")
            continue
        try:
            out = abc_edit.transform(excerpt, **kw)["abc"]
        except ValueError as e:
            print(f"[err ] {name} 加工失败：{e}")
            man[name] = {"error": str(e)[:300], "transform": kw, "seed": seed}
            failed += 1
            continue
        _write(sp, out)
        key = abc_compare.key_of(out)
        print(f"[gen ] {name} 调号={key} 字节={len(out)} seed={seed}", flush=True)
        try:
            info = generate(out, lyrics, wav, seed=seed, max_tok=args.max_tok,
                            steps=args.steps, style=args.style)
            print(f"[gen ] {name} 完成 {info['sec']}s 音频 {info['bytes']} 字节")
        except Exception as e:  # noqa: BLE001
            print(f"[err ] {name} 生成失败：{e}")
            man[name] = {"error": str(e)[:300], "transform": kw, "seed": seed,
                         "abc_file": sp, "key": key}
            failed += 1
            json.dump(man, open(man_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            continue
        man[name] = {"transform": kw, "seed": seed, "key": key,
                     "abc_file": sp, "wav": wav, "gen": info}
        json.dump(man, open(man_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        try:
            tabc = transcribe(wav)
            tp = os.path.join(work, "scores", f"{name}.trans.abc")
            _write(tp, tabc)
            man[name].update({"trans_file": tp,
                              "trans_md5": hashlib.md5(tabc.encode()).hexdigest()[:12]})
            print(f"[tr  ] {name} 转谱 {len(tabc)} 字节 调号={abc_compare.key_of(tabc)}")
        except Exception as e:  # noqa: BLE001
            print(f"[err ] {name} 转谱失败：{e}")
            man[name]["trans_error"] = str(e)[:300]
            failed += 1
        json.dump(man, open(man_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    _report(man, work)
    return 1 if failed else 0


def _report(man: dict, work: str) -> int:
    """三把尺子一起报：调性（色度）、音区（DTW）、像不像（五音组）。"""
    done = {k: v for k, v in man.items() if v.get("trans_file") and v.get("abc_file")}
    if not done:
        print("[report] 还没有完成到「转谱」这一步的组，先跑一轮。")
        return 1
    expect = {"base": 0, "rep": 0, "m3": -3, "m5": -5, "p5": 5, "p7": 7, "p12": 12, "modal": 0}
    head = (f"{'组':5}{'请求':>5} {'输入调':6}{'转录调':6}{'调平移':>6}{'绝对偏移':>8}"
            f"{'残差':>6}{'音区差':>6}{'逐音均':>6}{'±0音':>6}{'五音组':>7}")
    print("\n" + head)
    print("-" * (len(head) + 6))
    rows = []
    for name, m in done.items():
        try:
            src, trans = _read(m["abc_file"]), _read(m["trans_file"])
        except OSError:
            continue
        r = abc_compare.report(src, trans)
        d = abc_compare.melody_match(src, trans)
        keep = abc_compare.motif_contain(src, trans, 5)["contain"]
        print(f"{name:5}{expect.get(name, 0):>+5d} {r['ref_key']:6}{r['cand_key']:6}"
              f"{r['shift_chroma']:>+6d}{r['offset']:>+8.1f}{r['residual']:>+6.1f}"
              f"{d['shift']:>+6d}{d['mean']:>6.2f}{d['hit0']:>6.2f}{keep:>7.2f}")
        rows.append({"name": name, "expect": expect.get(name), "key_in": r["ref_key"],
                     "key_out": r["cand_key"], "key_shift": r["shift_chroma"],
                     "abs_offset": r["offset"], "residual": r["residual"],
                     "register": d["shift"], "dtw_mean": d["mean"], "hit0": d["hit0"],
                     "hit1": d["hit1"], "motif5": keep,
                     "in_n": r["ref_n"], "out_n": r["cand_n"],
                     "gen_sec": (m.get("gen") or {}).get("sec")})

    base = man.get("base") or {}
    if base.get("wav"):
        for row in rows:
            w = (man.get(row["name"]) or {}).get("wav")
            if not w:
                continue
            shift, corr = audio_shift_vs(base["wav"], w)
            row["audio_vs_base"], row["audio_corr"] = shift, corr
        print(f"\n{'组':5}{'音频Δ':>6}{'音频相关':>9}   （绕过转谱，直接在混音色度上量）")
        for row in rows:
            s, c = row.get("audio_vs_base"), row.get("audio_corr")
            print(f"{row['name']:5}{(f'{s:+d}' if s is not None else 'n/a'):>6}"
                  f"{(f'{c:.3f}' if c is not None else 'n/a'):>9}")

    with open(os.path.join(work, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    print("\n列说明：")
    print("  调平移 = 转录谱相对输入谱的音级平移（模 12，0 表示唱在同一个调）")
    print("  绝对偏移/音区差 = 把八度也计进去的实际音高差（引擎会自己挑声区，所以它不等于移调请求）")
    print("  残差 = 扣掉调平移后还剩多少（理想 0；明显不为 0 说明不只是移调）")
    print("  逐音均 / ±0音 = DTW 对齐后的平均偏差与完全对上比例（照谱程度）")
    print("  五音组 = 5 音组包含率（同一段旋律 ≈1，不同旋律 ≈0）")
    print(f"\n明细已写入 {os.path.join(work, 'report.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
