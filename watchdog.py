# -*- coding: utf-8 -*-
"""
音乐工作台 · 服务看门狗（常驻分离进程）

职责：守护网关 :7863 与工作台 :3081 两个进程。两者都会偶发假死——
端口仍 LISTENING 但不再响应（asyncio MemoryError / 引擎请求挂起导致
事件循环停摆）。看门狗每 10 秒探测一次，连续 5 次失败先做一次 90 秒深探针确认；确认仍不回话
才判定假死，杀掉并重启（判"慢"不判"死"时只把失败计数清零，不动进程）。

2026-09-26 重构（原实现两个真实缺陷）：
  1. 探活把 401 当离线：dsh 工作台根路径需要 token，urlopen 直接抛
     HTTPError，probe() 返回 False → dsh_seen 永远为 False →
     "3081 守护分支"从上线起一次都没触发过。现在 5xx 以下一律视为存活。
  2. 3081 只杀不拉：原逻辑击杀后要等用户手动重开工作台。现在与 7863
     同等对待——杀掉后重新拉起（经 scripts\\启动dsh工作台.bat，
     与主启动脚本共用同一份启动参数，不会两处漂移）。

用法：python watchdog.py   （由 WMI / Start-Process 分离启动）
退出：仅当进程被杀时结束（无自动退出逻辑，守护常驻）。
"""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))
try:
    from ports import get as port_of
except Exception:  # ports.py 缺失也不许看门狗起不来
    def port_of(name: str) -> int:
        return {"gateway": 7863, "dsh": 3081, "audiocpp": 8080}[name]

PY = ROOT / "py312" / "python.exe"
APP = ROOT / "app.py"
DSH_BAT = ROOT / "scripts" / "启动dsh工作台.bat"

GW_PORT = port_of("gateway")
DSH_PORT = port_of("dsh")

CHECK_INTERVAL = 10     # 每轮探测间隔（秒）
# 探测超时曾经只有 5 秒：推理一开跑，整机（GIL + 显存换入 + 磁盘）会被压满，
# /api/health 完全可能几十秒不回话。09-26 的看门狗日志实证：21:40 与 21:50 两次
# "判定假死→击杀"都发生在批量任务正在算的时候（用户那两首的 sec 停在 471.2 和
# 15735.4，报错都是 audio.cpp 请求失败），也就是**看门狗把还在干活的用户任务砍了**。
# 所以这里把窗口放宽，并在动手前加一次深探针确认（见 main 里的 DEEP_PROBE_TIMEOUT）。
HEALTH_TIMEOUT = 15     # 单次探测超时（秒）
FAIL_THRESHOLD = 5      # 连续失败多少次判定假死
DEEP_PROBE_TIMEOUT = 90  # 达到阈值后、击杀前的最后一次确认超时；答了就是"慢"不是"死"
RESTART_COOLDOWN = 30   # 重启后的最短稳定观察期（秒），期间不计失败
# 工作台是唯一 UI 入口：网关健康但 3081 从未出现过时主动拉起一次。
# 只尝试一次，失败即放弃（避免把用户的"故意不开工作台"理解成故障并反复刷进程）。
DSH_AUTOSTART = True


def probe(url: str, timeout: float | None = None) -> bool:
    """探测存活。5xx 以下一律算活着——dsh 根路径需 token 会回 401/403，
    把它当离线会导致看门狗疯狂误杀。timeout 缺省用 HEALTH_TIMEOUT，
    击杀前的深探确认会传一个长得多的值。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout or HEALTH_TIMEOUT) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
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


SPAWN_LOG_DIR = ROOT / "runtime" / "data" / "logs"
SPAWN_LOG_MAX = 5 * 1024 * 1024   # 单个日志超过 5 MB 先轮转，防止跑几年把盘吃掉


def _spawn_log(name: str):
    """给被拉起的子进程开一个追加写的日志句柄；开不了就返回 None（调用方退回
    DEVNULL），绝不能因为日志打不开而让守护失去重启能力。"""
    try:
        SPAWN_LOG_DIR.mkdir(parents=True, exist_ok=True)
        p = SPAWN_LOG_DIR / f"{name}.log"
        if p.exists() and p.stat().st_size > SPAWN_LOG_MAX:
            p.replace(p.with_suffix(".log.old"))
        return open(p, "ab", buffering=0)
    except Exception:
        return None


def spawn_gateway() -> None:
    # stdout/stderr 曾经是 DEVNULL：看门狗每次自愈重启，网关这一生的痕迹就全没了
    # （_gateway.log 停在 09-20，之后再无任何网关输出）。改为落盘。
    out, err = _spawn_log("gateway.out"), _spawn_log("gateway.err")
    try:
        subprocess.Popen(
            [str(PY), "-s", str(APP)],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            stdout=out or subprocess.DEVNULL,
            stderr=err or subprocess.DEVNULL,
        )
    finally:
        # 子进程已继承句柄，父进程这边关掉，否则轮转/删除都困难。
        for fh in (out, err):
            if fh is not None:
                fh.close()


def spawn_dsh() -> None:
    """拉起 dsh 工作台。优先走 bat（与主启动脚本同一份参数：DSH_HOME/端口/
    DEEPSEEK_API_KEY 都在这里面），bat 缺失时回退直接拉 node。"""
    if DSH_BAT.is_file():
        subprocess.Popen(
            ["cmd", "/c", str(DSH_BAT)],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        subprocess.Popen(
            ["node", "node_modules/@deepseek-ai/dsh/lib/bin.js",
             "web", "--port", str(DSH_PORT), "--no-open"],
            cwd=str(ROOT / "dsh-plugin"),
            env={**os.environ, "DSH_HOME": str(ROOT / "dsh-plugin" / "_dsh_home"),
                 "DSH_NO_BROWSER": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def log(msg: str) -> None:
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        log_dir = ROOT / "runtime" / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "watchdog.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def kill_pid(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                   capture_output=True, timeout=15,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main() -> None:
    # 单实例互斥：pid 文件存在且进程存活则退出，防止双 watchdog 互相竞争误杀
    pid_file = ROOT / "runtime" / "_watchdog.pid"
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

    # 统一服务表：两个进程走同一套探测→击杀→重启逻辑，不再厚此薄彼
    services = [
        {
            "name": f"网关 :{GW_PORT}",
            "port": GW_PORT,
            "health": f"http://127.0.0.1:{GW_PORT}/api/health",
            "respawn": spawn_gateway,
            "autostart": False,   # 网关由启动时的三轮兜底检查负责（见 main），不走"用户可能没开"那条逻辑
            "fails": 0,
            "seen": False,
            "cooldown": 0.0,
            "tried_autostart": True,  # 网关无需"首次拉起"判断
        },
        {
            "name": f"工作台 :{DSH_PORT}",
            "port": DSH_PORT,
            "health": f"http://127.0.0.1:{DSH_PORT}/",
            "respawn": spawn_dsh,
            "autostart": DSH_AUTOSTART,
            "fails": 0,
            "seen": False,
            "cooldown": 0.0,
            "tried_autostart": False,
        },
    ]

    log(f"看门狗启动：统一守护 " + " + ".join(s["name"] for s in services)
        + f"（连续 {FAIL_THRESHOLD} 次探测失败即重启；端口源 ports.json）")

    # 网关兜底拉起：通用规则是"从未见过的服务不判定假死（可能用户就是没开）"，
    # 这条对工作台正确，对网关却是死锁——看门狗单独起来（或网关比它先死光）时，
    # seen 永远为 False，"立刻拉回"那句注释就是空话：既不会拉起，也不会击杀僵在
    # 端口上的那个。所以启动时先给网关三轮确认，再决定"拉起"还是"纳入监控"。
    gw = services[0]
    for _ in range(3):
        if probe(gw["health"]):
            gw["seen"] = True
            break
        time.sleep(CHECK_INTERVAL)
    else:
        stuck_pid = find_listener_pid(gw["port"])
        if stuck_pid:
            gw["seen"] = True
            log(f"网关 :{GW_PORT} 有进程（PID {stuck_pid}）占着端口却三轮不回话，"
                f"纳入假死监控，按常规连败→深探→击杀流程处理")
        else:
            log(f"网关 :{GW_PORT} 无人监听，看门狗主动拉起")
            gw["respawn"]()
            gw["cooldown"] = time.time() + RESTART_COOLDOWN

    healthy_rounds = 0
    while True:
        time.sleep(CHECK_INTERVAL)

        # 每轮先探网关，作为"工作台要不要主动拉起"的前置条件
        gw_alive = probe(services[0]["health"])
        healthy_rounds = healthy_rounds + 1 if gw_alive else 0

        for idx, svc in enumerate(services):
            alive = gw_alive if idx == 0 else probe(svc["health"])

            # 首次拉起：网关已连续健康 2 轮，工作台却从未出现过 → 主动补一次
            if svc["autostart"] and not svc["tried_autostart"] and not svc["seen"]:
                if alive:
                    svc["seen"] = True
                    svc["tried_autostart"] = True
                elif healthy_rounds >= 2:
                    svc["tried_autostart"] = True
                    log(f"{svc['name']} 从未响应且网关健康，尝试主动拉起一次")
                    svc["respawn"]()
                    svc["cooldown"] = time.time() + RESTART_COOLDOWN
                continue

            if alive:
                svc["seen"] = True
                svc["fails"] = 0
                continue

            # 从未见过的服务不判定假死（可能用户就是没开），只跳过
            if not svc["seen"]:
                continue
            if time.time() < svc["cooldown"]:
                continue  # 刚重启，给加载时间

            svc["fails"] += 1
            log(f"{svc['name']} 探测失败（{svc['fails']}/{FAIL_THRESHOLD}）")
            if svc["fails"] < FAIL_THRESHOLD:
                continue

            # 击杀前的最后一道确认：给一次长超时深探。推理把整台机器的 CPU/GIL 压满时，
            # /api/health 完全可能几十秒不回话，而进程本身是好的——09-26 的日志实证：
            # 21:40 与 21:50 两次"判定假死→击杀"都落在批量任务正在算的当口，
            # 用户两首歌分别被砍掉（471s 与 15735s 的那两条，报错都是 audio.cpp 请求失败）。
            # 宁可多等三分钟，也不再错杀一首正在算的歌。
            if probe(svc["health"], DEEP_PROBE_TIMEOUT):
                log(f"{svc['name']} 连续 {svc['fails']} 次短探失败，但 {DEEP_PROBE_TIMEOUT}s 深探回了话 —— "
                    f"判为高负载慢响应而非假死，本轮不击杀，失败计数清零")
                svc["fails"] = 0
                continue

            log(f"判定 {svc['name']} 假死（{DEEP_PROBE_TIMEOUT}s 深探也无响应），执行重启…")
            pid = find_listener_pid(svc["port"])
            if pid and pid != _current_pid():
                kill_pid(pid)
                log(f"已击杀假死进程 PID {pid}")
            time.sleep(2)
            svc["respawn"]()
            log(f"{svc['name']} 已重新拉起，进入 {RESTART_COOLDOWN}s 稳定观察期")
            svc["fails"] = 0
            svc["cooldown"] = time.time() + RESTART_COOLDOWN


def _current_pid() -> int:
    return os.getpid()


if __name__ == "__main__":
    main()
