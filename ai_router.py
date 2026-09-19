# -*- coding: utf-8 -*-
"""AI 工作台路由：/api/ai/*，dsh 内核桥接版。

契约（与前端约定，切换内核不变形）：
  POST /api/ai/chat  {session_id, message, confirm} ->
    {session_id, reply, feeds[], needs_confirm, confirm_hint}
  GET  /api/ai/status -> {ready, via, model}
  GET  /api/ai/session/{sid} -> 会话消息与投喂卡片
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import ai_lab

router = APIRouter(prefix="/api/ai")

DSH_RUNNER = Path(__file__).parent / "dsh-plugin" / "src" / "runner.mjs"


class ChatBody(BaseModel):
    session_id: str = ""
    message: str
    confirm: bool = False


@router.get("/status")
def ai_status():
    return {
        "ready": ai_lab.ai_ready(),
        "via": "dsh",
        "model": ai_lab.MODEL,
        "runner_exists": DSH_RUNNER.is_file(),
    }


@router.get("/session/{sid}")
def ai_session(sid: str):
    s = ai_lab.session_load(sid)
    return {"session_id": s["id"], "messages": s.get("messages", [])[-40:],
            "feeds": s.get("feeds", [])[-10:]}


@router.get("/web")
def ai_web():
    """重定向到 dsh 原生 Web UI；未运行则自动后台拉起并等待就绪。"""
    import subprocess
    import time as _t
    from fastapi.responses import RedirectResponse

    log = Path(__file__).parent / "dsh-plugin" / "_dsh_web.log"

    def _read_token() -> str | None:
        if not log.is_file():
            return None
        m = re.search(r"http://127\.0\.0\.1:3081/\?token=[A-Za-z0-9_\-]+",
                      log.read_text(encoding="utf-8", errors="replace"))
        return m.group(0) if m else None

    def _alive() -> bool:
        import httpx
        try:
            httpx.get("http://127.0.0.1:3081/", timeout=1.5)
            return True
        except Exception:
            return False

    url = _read_token() if _alive() else None
    if not url:
        # 自动拉起（分离进程，DSH_HOME 隔离，端口 3081）。
        # 用 env 字典传密钥，不走 cmd/PowerShell 字符串拼接：
        # 避免特殊字符注入命令行，也避免密钥出现在进程命令行（WMI 可见）。
        root = Path(__file__).parent
        env = {**os.environ,
               "DEEPSEEK_API_KEY": ai_lab.ai_key() or "",
               "DSH_HOME": str(root / "dsh-plugin" / "_dsh_home"),
               "DSH_NO_BROWSER": "1"}
        log_fd = open(log, "ab")
        subprocess.Popen(
            ["node", str(root / "dsh-plugin" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"),
             "web", "--port", "3081", "--no-open"],
            cwd=str(root / "dsh-plugin"), env=env,
            stdout=log_fd, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        # 等待就绪（最多 60s：Node 启动 + profile 装配）
        for _ in range(30):
            if _alive() and _read_token():
                break
            _t.sleep(2.0)
        url = _read_token()
    if url:
        return {"ok": True, "url": url}
    raise HTTPException(status_code=503, detail="dsh 原生界面启动失败（查看 dsh-plugin/_dsh_web.log）")


@router.get("/sessions")
def ai_sessions():
    """列出全部会话（按最近消息时间倒序），供前端侧栏。"""
    items = []
    if ai_lab.AI_SESSION_DIR.is_dir():
        for p in ai_lab.AI_SESSION_DIR.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            msgs = [m for m in d.get("messages", []) if m.get("role") in ("user", "assistant")]
            first_user = next((m["content"] for m in msgs if m["role"] == "user"), "")
            items.append({
                "id": d.get("id") or p.stem,
                "title": (first_user or "(空会话)")[:30],
                "turns": len(msgs) // 2,
                "mtime": p.stat().st_mtime,
                "created": d.get("created", ""),
            })
    items.sort(key=lambda x: -x["mtime"])
    return {"sessions": items[:50]}


@router.delete("/session/{sid}")
def ai_session_delete(sid: str):
    p = ai_lab._session_path(sid)
    if p.is_file():
        p.unlink()
        return {"ok": True}
    raise HTTPException(status_code=404, detail="会话不存在")


def _task_from_session(sid: str, user_message: str, confirm: bool) -> str:
    """把系统提示词（两个 skill 全文）+ 会话历史注入 task（dsh headless 是一次性任务）。"""
    import ai_prompt
    s = ai_lab.session_load(sid)
    msgs = [m for m in s.get("messages", []) if m.get("role") in ("user", "assistant")]
    recent = msgs[-8:]  # 最近 4 轮
    parts = [f"[系统指令]\n{ai_prompt.SYSTEM_PROMPT}\n[/系统指令]"]
    if recent:
        hist = "\n".join(f"{'用户' if m['role'] == 'user' else 'AI'}: {str(m.get('content', ''))[:400]}"
                         for m in recent)
        parts.append(f"[对话历史]\n{hist}\n[/对话历史]")
    if confirm:
        parts.append("[用户已确认刚才的删除操作，直接执行对应工具]")
    parts.append(f"用户：{user_message}")
    return "\n\n".join(parts)


@router.post("/chat")
def ai_chat(body: ChatBody):
    sid = re.sub(r"[^0-9A-Za-z_-]", "", body.session_id)[:64] or ai_lab.uuid.uuid4().hex[:12]
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")
    if not ai_lab.ai_ready():
        raise HTTPException(status_code=400, detail="未配置 DEEPSEEK_API_KEY 环境变量，AI 工作台不可用")
    if not DSH_RUNNER.is_file():
        raise HTTPException(status_code=500, detail="dsh runner 不存在（dsh-plugin/src/runner.mjs）")

    task = _task_from_session(sid, body.message.strip(), body.confirm)
    try:
        r = subprocess.run(
            ["node", str(DSH_RUNNER), task],
            cwd=str(DSH_RUNNER.parent.parent), capture_output=True, timeout=300,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="AI 回复超时（300s），请重试或缩短问题")
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace")[-300:]
        raise HTTPException(status_code=502, detail=f"dsh 内核失败：{err}")

    out = r.stdout.decode("utf-8", "replace")
    payload = {}
    m = re.search(r"@@JSON@@(.+)", out)
    if m:
        try:
            payload = json.loads(m.group(1))
        except Exception:
            payload = {}
    reply = (payload.get("reply") or "").strip() or "（AI 没有返回内容，请重试）"

    # 会话记录：保存用户消息与 AI 回复（与 dsh 内核无关，网关侧持久化）
    s = ai_lab.session_load(sid)
    s["messages"].append({"role": "user", "content": body.message.strip()})
    s["messages"].append({"role": "assistant", "content": reply})
    ai_lab.session_save(s)

    return {"session_id": sid, "reply": reply,
            "feeds": payload.get("feeds", []),
            "needs_confirm": False, "confirm_hint": ""}
