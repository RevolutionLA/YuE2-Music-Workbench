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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import sys as _sys
# 端口一律取自唯一真源 ports.json：这里再写死 3081，换端口后 _kill_stale_listener
# 会去 netstat 里找 :3081 并 taskkill——那可能杀掉与本项目无关的进程。
_src_dir = str(Path(__file__).resolve().parent)
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)
from ports import get as _port  # noqa: E402

DSH_PORT = _port("dsh")

import ai_lab

router = APIRouter(prefix="/api/ai")

def _caller_authority(request: Request) -> str:
    """调用方浏览器正在用的 host[:port]，取不到就回 127.0.0.1:<dsh>。

    面板反代会把 Host 改写成 127.0.0.1:7863，所以 Host 不能当证据；能用的是
    浏览器自带的 Origin/Referer（页面从哪个地址打开，这两个头就是哪个地址）。
    """
    for h in ("origin", "referer"):
        v = (request.headers.get(h) or "").strip()
        if not v:
            continue
        m = re.match(r"^[a-zA-Z]+://([^/?#]+)", v)
        if m:
            return m.group(1)
    return f"127.0.0.1:{DSH_PORT}"

DSH_RUNNER = Path(__file__).parent.parent / "dsh-plugin" / "src" / "runner.mjs"


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
def ai_web(request: Request):
    """重定向到 dsh 原生 Web UI；未运行则自动后台拉起并等待就绪。

    这个端点返回的是带进程 token 的 dsh 地址，等于"门票"，所以它只能经由面板
    反代到达调用方，而面板路由自 2026-09-28 起加了鉴权闸门（ui-panel.mjs 的
    isAuthorized：拿调用方 Host+Cookie 回环问 dsh 自己，未鉴权一律 401）。
    网关口 7863 本身不对局域网放行（防火墙 + 面板是唯一入口），因此这里不再
    额外拦 token，只做一件事：把 URL 里的 127.0.0.1 换成调用方正在使用的那个
    地址 —— 否则局域网电脑点"AI 助手"会被指到 127.0.0.1，打开的是它自己那台
    机器上的端口，必然失败。
    """
    import subprocess
    import time as _t
    from fastapi.responses import RedirectResponse

    log = Path(__file__).parent.parent / "dsh-plugin" / "_dsh_web.log"

    def _read_token() -> str | None:
        if not log.is_file():
            return None
        # 取**最后**一条：日志可能被两条链路写（拉起脚本每次截断重写、本模块以 "ab"
        # 追加），旧 token 留在前面。返回第一个匹配会把用户引到上一次会话的地址上。
        hits = re.findall(rf"http://127\.0\.0\.1:{DSH_PORT}/\?token=[A-Za-z0-9_\-]+",
                          log.read_text(encoding="utf-8", errors="replace"))
        return hits[-1] if hits else None

    def _alive() -> bool:
        import httpx
        try:
            httpx.get(f"http://127.0.0.1:{DSH_PORT}/", timeout=1.5)
            return True
        except Exception:
            return False

    def _kill_stale_listener() -> None:
        """端口被占用但不响应（假死僵死进程）时击杀，否则新进程抢不到端口起不来。

        只杀确证的 dsh node 进程：端口号可能已被别的程序占用（ports.json 改过、
        或别人抢过这个口），盲杀 taskkill 会把与本项目无关的进程连子进程树一起干掉。
        光看镜像名不够——用户自己开的 dev server 也叫 node.exe，所以要再验命令行里
        是否含 dsh；验不了就当不是自己的（宁可起不来并报明确错误，也不误杀）。
        """
        try:
            out = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            for line in out.splitlines():
                if "LISTENING" in line and line.split()[1].rstrip().endswith(f":{DSH_PORT}"):
                    pid = int(line.split()[-1])
                    if pid == os.getpid():
                        continue
                    if not _is_dsh_listener(pid):
                        return
                    subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                                   capture_output=True, timeout=15,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    _t.sleep(1)
                    break
        except Exception:
            pass

    def _is_dsh_listener(pid: int) -> bool:
        img = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        if "node.exe" not in img.lower():
            print(f"[ai_router] :{DSH_PORT} 被 PID {pid} 占用但非 node.exe，跳过击杀", flush=True)
            return False
        ps = ("$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%d';"
              "if($p){[Console]::Out.Write($p.CommandLine)}" % pid)
        cmd = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        if "dsh" not in cmd.lower():
            print(f"[ai_router] :{DSH_PORT} 上是本机其它 node 服务（命令行不含 dsh），跳过击杀", flush=True)
            return False
        return True

    url = _read_token() if _alive() else None
    if not url:
        _kill_stale_listener()  # 假死自愈兜底：watchdog 之外，拉起前再清一次
        # 自动拉起（分离进程，DSH_HOME 隔离，端口取自 ports.json）。
        # 用 env 字典传密钥，不走 cmd/PowerShell 字符串拼接：
        # 避免特殊字符注入命令行，也避免密钥出现在进程命令行（WMI 可见）。
        root = Path(__file__).parent.parent
        env = {**os.environ,
               "DEEPSEEK_API_KEY": ai_lab.ai_key() or "",
               "DSH_HOME": str(root / "dsh-plugin" / "_dsh_home"),
               "DSH_NO_BROWSER": "1"}
        log_fd = open(log, "ab")
        # --trusted-host 与启动脚本/看门狗保持同一口径：局域网白名单从环境变量读，
        # 少了这个参数，局域网打开的页面能渲染但所有按钮没反应（dsh 的同源栅栏把
        # /api 拒了）。历史上这条链路就是漏了参数才出现"本机好用、别处点不动"。
        spawn_argv = [
            "node", str(root / "dsh-plugin" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"),
            "web", "--port", str(DSH_PORT), "--no-open",
        ]
        _lan_hosts = [h.strip() for h in (os.environ.get("YUE2_LAN_HOSTS") or "").split(",") if h.strip()]
        if _lan_hosts:
            spawn_argv += ["--trusted-host", *_lan_hosts]
        subprocess.Popen(
            spawn_argv,
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
        # 把 token 地址改写成调用方正在用的地址：否则局域网电脑点"AI 助手"
        # 会被指到它自己的 127.0.0.1:3081，必然打不开。
        url = url.replace(f"127.0.0.1:{DSH_PORT}", _caller_authority(request), 1)
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
