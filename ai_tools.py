# -*- coding: utf-8 -*-
"""AI 工作台工具集：供 LLM tool-calling 调用的纯 Python 函数。

约定：
- 函数保持纯 Python、不依赖 HTTP 层（未来迁移 dsh 内核时直接平移）
- 删除类工具用 @needs_confirm("提示") 标记：首轮返回 needs_confirm，用户确认后执行
- feed_card 为生成类工具产出「投喂卡片」（style/lyrics 可一键填入创作页）
"""
from __future__ import annotations

import functools
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
PY = ROOT / "py312" / "python.exe"


def needs_confirm(hint: str):
    """删除类工具标记：首轮调用返回 needs_confirm，前端确认后带 confirm=True 重发。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if not kw.pop("_confirmed", False):
                return {"needs_confirm": True, "hint": hint, "args": {k: v for k, v in kw.items()}}
            return fn(*a, **kw)
        wrapper.needs_confirm = True
        wrapper.confirm_hint = hint
        return wrapper
    return deco


# --------------------------------------------------------------------------- #
# 工具实现
# --------------------------------------------------------------------------- #

def tool_list_models() -> dict:
    """列出可用音色模型与生成模型。"""
    import app as gw
    models = gw._rvc_models()
    hist = _read_history()
    return {
        "rvc_voices": [m.replace(".pth", "") for m in models],
        "engine_model": "yue2",
        "cot_modes": {"full": "可编辑旋律+和声（慢，出ABC谱）", "melody": "旋律规划+自由伴奏", "off": "无符号规划（最快）"},
    }


def tool_generate(style: str, lyrics: str, cot: str = "full",
                  steps: int = 32, seed: int = -1, count: int = 1) -> dict:
    """提交歌曲生成任务。返回任务 id，进度用 tool_get_progress 查询。"""
    import asyncio
    import app as gw
    payload = {"style": style, "lyrics": lyrics, "cot": cot,
               "num_inference_steps": int(steps), "seed": int(seed), "count": int(count)}
    cur = gw._gen_get_job()
    if cur and cur.get("status") == "running":
        return {"error": "已有生成任务进行中（id " + str(cur.get("id")) + "），请先等它完成"}
    r = asyncio.run(gw.generate_start(payload))
    return {"ok": True, "job_id": r["job"]["id"], "count": r.get("count", 1),
            "hint": "生成已提交，用 tool_get_progress 查询进度；full/32步 长词约 60-90 分钟"}


def tool_get_progress() -> dict:
    """查询当前生成任务进度与预估剩余时间。"""
    import app as gw
    j = gw._gen_get_job() or {}
    if not j:
        return {"status": "idle", "hint": "当前没有生成任务"}
    out = {"job_id": j.get("id"), "status": j.get("status"),
           "elapsed_sec": j.get("sec"), "error": (j.get("error") or "")[:200]}
    est = j.get("eta_sec")
    if j.get("status") == "running" and est:
        out["eta_min"] = round(est / 60)
    return out


def tool_rvc_convert(source: str, voice: str, pitch: int = 0) -> dict:
    """对一首已生成的歌曲做换声。source=历史任务 id 或 output 文件名；voice=音色名。"""
    import asyncio
    import app as gw
    rid = source.strip()
    models = [m.replace(".pth", "") for m in gw._rvc_models()]
    if voice.replace(".pth", "") not in models:
        return {"error": f"没有音色「{voice}」，可用：{models}"}
    # 历史 rid 直接转发；否则按 output 文件名匹配最近任务
    meta_path = gw._output_meta_path(rid)
    if not meta_path.is_file():
        found = None
        for p in sorted(gw.OUTPUT_DIR.glob("*.json")):
            m = json.loads(p.read_text(encoding="utf-8"))
            if m.get("kind") != "rvc" and m.get("status") == "done" and rid in (m.get("style") or "") + (m.get("lyrics") or ""):
                found = m["id"]
        if not found:
            return {"error": f"找不到歌曲「{source}」。用 tool_list_history 查看可用的任务 id"}
        rid = found
    r = asyncio.run(gw.rvc_convert_by_rid(rid, {"model": voice.replace(".pth", "") + ".pth", "pitch": int(pitch)}))
    return {"ok": True, "job_id": r["id"], "hint": "换声已提交，约 1 分钟内完成，结果会出现在历史页"}


def tool_list_history(limit: int = 10, kind: str = "") -> dict:
    """列出最近的生成/换声记录。kind=rvc 只看换声结果。"""
    hist = _read_history()
    if kind:
        hist = [h for h in hist if h.get("kind") == kind]
    out = []
    for h in hist[:max(1, min(30, int(limit)))]:
        out.append({
            "id": h.get("id"), "status": h.get("status"),
            "style": (h.get("style") or "")[:80], "lyrics_head": (h.get("lyrics") or "")[:60],
            "kind": h.get("kind", "gen"),
        })
    return {"items": out, "total": len(hist)}


def tool_get_song(rid: str) -> dict:
    """读取某条记录的完整参数（style/歌词/cot/seed 等），供修改后重新生成。"""
    import app as gw
    p = gw._output_meta_path(rid.strip())
    if not p.is_file():
        return {"error": f"找不到 {rid}"}
    m = json.loads(p.read_text(encoding="utf-8"))
    return {"id": m.get("id"), "status": m.get("status"), "style": m.get("style"),
            "lyrics": m.get("lyrics"), "cot": m.get("cot"),
            "params": m.get("params", {}), "error": (m.get("error") or "")[:200]}


@needs_confirm("将永久删除该条历史记录（含音频文件），不可恢复")
def tool_delete_history(rid: str) -> dict:
    import app as gw
    gw.generate_delete(rid.strip())
    return {"ok": True, "deleted": rid}


@needs_confirm("将永久删除该音色模型及其索引文件，不可恢复")
def tool_delete_voice(name: str) -> dict:
    import app as gw
    gw.rvc_model_delete(name.strip().replace(".pth", "") + ".pth")
    return {"ok": True, "deleted": name}


def tool_validate_lyrics(lyrics: str) -> dict:
    """校验歌词是否符合 YuE2 结构规范（段落标签/空行/行数/副歌完整）。"""
    return _validate_lyrics_impl(lyrics)


def _validate_lyrics_impl(lyrics: str) -> dict:
    errors, warns = [], []
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", lyrics.strip()) if b.strip()]
    if not blocks:
        return {"ok": False, "errors": ["歌词为空"]}
    tag_re = re.compile(r"^\[([A-Za-z][A-Za-z0-9 \-]*)\]\s*$")
    chorus_lines = None
    chorus_count = 0
    for i, b in enumerate(blocks):
        lines = [l for l in b.splitlines() if l.strip()]
        m = tag_re.match(lines[0].strip())
        if not m:
            errors.append(f"第{i+1}段首行不是段落标签（如 [Verse 1]）：{lines[0][:30]}")
            continue
        tag = m.group(1)
        body = lines[1:]
        if tag.lower().startswith(("intro", "interlude")):
            if body:
                warns.append(f"[{tag}] 段带歌词行——纯器乐段应只留标签+空行")
            continue
        if not (4 <= len(body) <= 8):
            errors.append(f"[{tag}] 行数 {len(body)} 不在 4-8 行范围")
        if tag.lower().startswith("chorus"):
            chorus_count += 1
            if chorus_lines is None:
                chorus_lines = tuple(body)
            elif tuple(body) != chorus_lines:
                warns.append(f"第{chorus_count}次副歌与首次内容不同（副歌重复应写全且一致，或刻意变化）")
    n_sec = len([b for b in blocks if tag_re.match(b.splitlines()[0].strip())])
    if n_sec < 5:
        warns.append(f"全曲仅 {n_sec} 段（建议 6-10 段约 3-4.5 分钟）")
    return {"ok": not errors, "errors": errors, "warnings": warns, "sections": n_sec}


# --------------------------------------------------------------------------- #
# 投喂卡片：生成类工具产出可一键填入创作页的数据
# --------------------------------------------------------------------------- #

def feed_card(tool_name: str, args: dict, result) -> dict | None:
    """生成类工具调用成功后，产出投喂卡片附加到结果里。"""
    if tool_name == "tool_generate" and isinstance(result, dict) and result.get("ok"):
        return {"feed": {"type": "generate", "job_id": result.get("job_id"),
                          "style": args.get("style", ""), "lyrics": args.get("lyrics", ""),
                          "cot": args.get("cot", "full")}}
    return None


# --------------------------------------------------------------------------- #
# OpenAI tool schemas（供 /chat/completions 的 tools 字段）
# --------------------------------------------------------------------------- #

TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "tool_list_models", "description": "列出可用音色模型与生成模式说明", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "tool_generate", "description": "提交 YuE2 歌曲生成任务。用户明确要求生成时调用。", "parameters": {"type": "object", "properties": {
        "style": {"type": "string", "description": "英文风格标签（六要素：语言,流派,情绪,人声,乐器,速度）"},
        "lyrics": {"type": "string", "description": "结构化歌词，[Verse]/[Chorus] 标签+空行分段"},
        "cot": {"type": "string", "enum": ["full", "melody", "off"]},
        "steps": {"type": "integer", "description": "推理步数，默认 32；16 更快"},
        "seed": {"type": "integer", "description": "-1 随机"},
        "count": {"type": "integer", "description": "连发数量 1-5"}},
        "required": ["style", "lyrics"]}}},
    {"type": "function", "function": {"name": "tool_get_progress", "description": "查询当前生成任务进度", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "tool_rvc_convert", "description": "对已生成的歌曲做 RVC 换声", "parameters": {"type": "object", "properties": {
        "source": {"type": "string", "description": "历史任务 id（tool_list_history 获取）"},
        "voice": {"type": "string", "description": "音色名（tool_list_models 获取）"},
        "pitch": {"type": "integer", "description": "变调半音：男转女+12 女转男-12 不变0"}}, "required": ["source", "voice"]}}},
    {"type": "function", "function": {"name": "tool_list_history", "description": "列出最近的生成/换声记录", "parameters": {"type": "object", "properties": {
        "limit": {"type": "integer"}, "kind": {"type": "string", "enum": ["", "rvc"]}}}}},
    {"type": "function", "function": {"name": "tool_get_song", "description": "读取某条记录完整参数（style/歌词/seed等）", "parameters": {"type": "object", "properties": {
        "rid": {"type": "string"}}, "required": ["rid"]}}},
    {"type": "function", "function": {"name": "tool_validate_lyrics", "description": "校验歌词结构是否符合 YuE2 规范", "parameters": {"type": "object", "properties": {
        "lyrics": {"type": "string"}}, "required": ["lyrics"]}}},
    {"type": "function", "function": {"name": "tool_delete_history", "description": "删除一条历史记录（需用户确认）", "parameters": {"type": "object", "properties": {
        "rid": {"type": "string"}}, "required": ["rid"]}}},
    {"type": "function", "function": {"name": "tool_delete_voice", "description": "删除一个音色模型（需用户确认）", "parameters": {"type": "object", "properties": {
        "name": {"type": "string"}}, "required": ["name"]}}},
]


def _read_history() -> list:
    import app as gw
    try:
        d = gw.history_list()
        return d.get("items") or d.get("jobs") or d
    except Exception:
        return []


TOOLS = {
    "tool_list_models": tool_list_models,
    "tool_generate": tool_generate,
    "tool_get_progress": tool_get_progress,
    "tool_rvc_convert": tool_rvc_convert,
    "tool_list_history": tool_list_history,
    "tool_get_song": tool_get_song,
    "tool_validate_lyrics": tool_validate_lyrics,
    "tool_delete_history": tool_delete_history,
    "tool_delete_voice": tool_delete_voice,
}
