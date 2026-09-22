# -*- coding: utf-8 -*-
"""AI 工作台：对话框 + tool-calling 循环（DeepSeek 兼容 API）。

设计要点：
- Key 只从环境变量 DEEPSEEK_API_KEY 读取，不落盘、不进日志
- 会话持久化到 data/ai_sessions/<sid>.json（仅对话与投喂卡片，绝不含 Key）
- tool-calling 循环最多 8 轮，工具由 TOOLS 注册表提供（见 tools 定义区）
- 删除类工具返回 needs_confirm，由前端二次确认后带 confirm=True 重发
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import httpx
from fastapi import HTTPException
from pydantic import BaseModel

import settings

AI_DIR = Path(settings.DATA_DIR if hasattr(settings, "DATA_DIR") else "runtime/data")
AI_SESSION_DIR = AI_DIR / "ai_sessions"

BASE_URL = os.environ.get("AI_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("AI_MODEL", "deepseek-chat")
MAX_TOOL_ROUNDS = 8
TIMEOUT = 180.0


def ai_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def ai_ready() -> bool:
    return bool(ai_key())


# --------------------------------------------------------------------------- #
# 会话持久化
# --------------------------------------------------------------------------- #

def _session_path(sid: str) -> Path:
    return AI_SESSION_DIR / ("".join(c for c in sid if c.isalnum() or c in "-_")[:64] + ".json")


def session_load(sid: str) -> dict:
    p = _session_path(sid)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"id": sid, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "messages": [], "feeds": []}


def session_save(s: dict) -> None:
    AI_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    # 只保留最近 40 条消息，防止会话文件无限膨胀
    s["messages"] = s.get("messages", [])[-40:]
    _session_path(s["id"]).write_text(
        json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 请求/响应模型
# --------------------------------------------------------------------------- #

class ChatBody(BaseModel):
    session_id: str = ""
    message: str
    confirm: bool = False  # 删除类工具的二次确认


# --------------------------------------------------------------------------- #
# LLM 客户端（OpenAI 兼容 /chat/completions）
# --------------------------------------------------------------------------- #

def _llm_chat(messages: list, tools: list) -> dict:
    key = ai_key()
    if not key:
        raise HTTPException(status_code=400, detail="未配置 DEEPSEEK_API_KEY 环境变量，AI 对话不可用")
    body = {"model": MODEL, "messages": messages, "temperature": 0.8}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    try:
        r = httpx.post(
            BASE_URL.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json=body, timeout=TIMEOUT,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI 服务连接失败：{e}")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"AI 服务返回 {r.status_code}：{r.text[:200]}")
    return r.json()


# --------------------------------------------------------------------------- #
# tool-calling 主循环
# --------------------------------------------------------------------------- #

def chat_turn(session_id: str, user_message: str, confirm: bool = False) -> dict:
    """一轮对话：LLM ↔ 工具循环，直到产出最终回复。"""
    from ai_tools import TOOLS, TOOL_SCHEMAS, feed_card  # 延迟导入避免循环依赖

    s = session_load(session_id or uuid.uuid4().hex[:12])
    msgs = s.get("messages", [])
    pending = s.get("_pending_tool")  # 待确认的删除类调用
    if pending:
        # 上轮有未确认的删除操作：确认则执行，否则取消
        msgs.append({
            "role": "tool",
            "tool_call_id": pending["id"],
            "content": "用户已确认执行。" if confirm else "用户取消了该操作。",
        })
        s.pop("_pending_tool")
        if confirm:
            result = TOOLS[pending["name"]](**pending["args"])
            msgs[-1]["content"] = json.dumps(result, ensure_ascii=False)[:4000]
    elif user_message:
        msgs.append({"role": "user", "content": user_message})

    new_feeds = []
    reply_text = ""
    for _round in range(MAX_TOOL_ROUNDS):
        resp = _llm_chat(
            [{"role": "system", "content": _system_prompt()}] + msgs,
            TOOL_SCHEMAS,
        )
        choice = resp["choices"][0]["message"]
        tcalls = choice.get("tool_calls") or []
        if not tcalls:
            reply_text = choice.get("content") or ""
            msgs.append({"role": "assistant", "content": reply_text})
            break
        # 记录 assistant 的工具调用消息
        msgs.append({
            "role": "assistant",
            "content": choice.get("content") or "",
            "tool_calls": [
                {"id": t["id"], "type": "function",
                 "function": {"name": t["function"]["name"],
                              "arguments": t["function"]["arguments"]}}
                for t in tcalls
            ],
        })
        for t in tcalls:
            name = t["function"]["name"]
            try:
                args = json.loads(t["function"]["arguments"] or "{}")
            except Exception:
                args = {}
            fn = TOOLS.get(name)
            if fn is None:
                out = {"error": f"未知工具 {name}"}
            elif getattr(fn, "needs_confirm", False) and not confirm:
                # 删除类：挂起等确认
                s["_pending_tool"] = {"id": t["id"], "name": name, "args": args}
                msgs.append({"role": "tool", "tool_call_id": t["id"],
                             "content": json.dumps({"needs_confirm": True,
                                                    "hint": getattr(fn, "confirm_hint", "该操作不可恢复，请确认")},
                                                   ensure_ascii=False)})
                continue
            else:
                try:
                    out = fn(**args)
                except Exception as e:
                    out = {"error": f"{type(e).__name__}: {e}"}
            out = feed_card(name, args, out) or out
            if isinstance(out, dict) and out.get("feed"):
                new_feeds.append(out["feed"])
            msgs.append({"role": "tool", "tool_call_id": t["id"],
                         "content": json.dumps(out, ensure_ascii=False)[:4000]})
    else:
        reply_text = "（工具调用轮次达到上限，已停止。可以继续对话。）"
        msgs.append({"role": "assistant", "content": reply_text})

    s["messages"] = msgs
    if new_feeds:
        s["feeds"] = (s.get("feeds") or [])[-20:] + new_feeds
    session_save(s)
    return {
        "session_id": s["id"],
        "reply": reply_text,
        "feeds": new_feeds,
        "needs_confirm": bool(s.get("_pending_tool")),
        "confirm_hint": (s.get("_pending_tool") or {}).get("args", {}).get("name", "")
                        if s.get("_pending_tool") else "",
    }


def _system_prompt() -> str:
    from ai_prompt import SYSTEM_PROMPT  # 延迟导入
    return SYSTEM_PROMPT
