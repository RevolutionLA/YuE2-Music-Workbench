# -*- coding: utf-8 -*-
"""
音乐工作台 · 网关看门狗（常驻分离进程）

职责：网关 app.py（:7863）偶发假死（asyncio MemoryError / 引擎请求挂起
导致事件循环停摆），端口仍 LISTENING 但不再响应。看门狗每 10 秒探测
/api/health（5 秒超时），连续 3 次失败即判定假死，杀掉并重启网关。

用法：python watchdog.py   （由 WMI 分离进程启动，或 bat / 计划任务）
退出：仅当进程被杀时结束（无自动退出逻辑，守护常驻）。
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
PY = ROOT / "py312" / "python.exe"
APP = ROOT / "app.py"
HEALTH_URL = "http://127.0.0.1:7863/api/health"
# dsh web 工作台（:3081）：无守护时假死只能等用户手动重开；
# 这里只自愈"曾响应后失联"的情况，绝不主动拉起（它由 /api/ai/web 按需启动）。
DSH_URL = "http://127.0.0.1:3081/"
DSH_PORT = 3081
DSH_ROOT = ROOT / "dsh-plugin"
CHECK_INTERVAL = 10     # 每轮探测间隔（秒）
HEALTH_TIMEOUT = 5      # 单次探测超时（秒）
FAIL_THRESHOLD = 3      # 连续失败多少次判定假死
RESTART_COOLDOWN = 30   # 重启后的最短稳定观察期（秒），期间不计失败


def probe(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=HEALTH_TIMEOUT) as r:
            return r.status == 200
    except Exception:
        return False


def find_listener_pid(port: int) -> int | None:
    """返回占用指定端口的进程 PID（用于精准击杀假死进程）。匹配行尾端口，避免 :78630 误命中。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        for line in out.splitlines():
            if "LISTENING" not in line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1].rstrip().endswith(f":{port}"):
                return int(parts[-1])
    except Exception:
        pass
    return None


def spawn_gateway() -> None:
    subprocess.Popen(
        [str(PY), "-s", str(APP)],
        cwd=str(ROOT),
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def log(msg: str) -> None:
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(ROOT / "watchdog.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def kill_pid(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                   capture_output=True, timeout=15,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main() -> None:
    # 单实例互斥：pid 文件存在且进程存活则退出，防止双 watchdog 互相竞争误杀
    pid_file = ROOT / "_watchdog.pid"
    try:
        if pid_file.is_file():
            old = int(pid_file.read_text(encoding="utf-8").strip() or 0)
            if old and old != _current_pid():
                # tasklist 找不到该 PID 时输出不含对应行，以此判断存活
                tl = subprocess.run(["tasklist", "/FI", f"PID eq {old}"],
                                    capture_output=True, text=True, timeout=15,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
                if f" {old} " in " ".join(tl.split()) and "python" in tl:
                    print(f"看门狗已在运行（PID {old}），本实例退出。", flush=True)
                    return
        pid_file.write_text(str(_current_pid()), encoding="utf-8")
    except Exception:
        pass

    fails = 0
    cooldown_until = 0.0
    dsh_seen = False       # dsh web 曾响应过（只自愈"曾经活着后来失联"，不主动拉起）
    dsh_fails = 0
    log("看门狗启动：监测 :7863 网关 + :3081 工作台 假死（连续 %d 次探测失败即重启）" % FAIL_THRESHOLD)
    while True:
        time.sleep(CHECK_INTERVAL)

        # ---- :3081 dsh web 工作台：曾响应后失联 = 假死，击杀即可（下次点入口自动拉起）----
        if probe(DSH_URL):
            dsh_seen = True
            dsh_fails = 0
        elif dsh_seen:
            dsh_fails += 1
            log(f"dsh web :3081 探测失败（{dsh_fails}/{FAIL_THRESHOLD}）")
            if dsh_fails >= FAIL_THRESHOLD:
                pid = find_listener_pid(DSH_PORT)
                if pid:
                    kill_pid(pid)
                    log(f"已击杀假死的 dsh web 进程 PID {pid}（下次打开工作台入口会自动重新拉起）")
                dsh_fails = 0

        # ---- :7863 网关：假死则击杀并重新拉起 ----
        if time.time() < cooldown_until:
            continue  # 刚重启，给网关加载模型的时间
        if probe(HEALTH_URL):
            fails = 0
            continue
        fails += 1
        log(f"health 探测失败（{fails}/{FAIL_THRESHOLD}）")
        if fails < FAIL_THRESHOLD:
            continue
        log("判定网关假死，执行重启…")
        pid = find_listener_pid(7863)
        if pid and pid != _current_pid():
            kill_pid(pid)
            log(f"已击杀假死进程 PID {pid}")
        time.sleep(2)
        spawn_gateway()
        log("网关已重新拉起，进入 %ds 稳定观察期" % RESTART_COOLDOWN)
        fails = 0
        cooldown_until = time.time() + RESTART_COOLDOWN


def _current_pid() -> int:
    return os.getpid()


if __name__ == "__main__":
    main()
