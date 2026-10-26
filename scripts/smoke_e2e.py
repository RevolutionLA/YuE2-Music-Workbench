#!/usr/bin/env python
"""真引擎冒烟：验证 网关 → 引擎 → 产物 这条链，补充单元测试够不到的假绿灯。

前提：工作台已经启动（本脚本不启动、不重启、不杀任何进程）。
默认只跑 kernel + gen；score 会加载 PyTorch 转谱模型，6GB 卡请单独跑。

    py312\\python.exe scripts\\smoke_e2e.py --kernel
    py312\\python.exe scripts\\smoke_e2e.py --gen --steps 4
    py312\\python.exe scripts\\smoke_e2e.py --score
    py312\\python.exe scripts\\smoke_e2e.py --lrc          # 用服务器上带歌词的歌跑真实对齐
    py312\\python.exe scripts\\smoke_e2e.py --all        # 仅建议 >=8GB 显存

退出码：0 通过；1 失败；2 服务不可达。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import ports  # noqa: E402

DEFAULT_TIMEOUT = 20 * 60
TINY_LYRICS = "[Verse]\nla la la, hold the light\nla la la, stay tonight"


def _wav_ok(data: bytes) -> bool:
    return len(data) > 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _tiny_payload(steps: int, seed: int) -> dict:
    return {
        "model": "yue2",
        "style": "English, simple warm piano, slow tempo, clear vocal",
        "lyrics": TINY_LYRICS,
        "cot": "off",
        "num_inference_steps": max(1, int(steps)),
        "seed": int(seed),
        "cfg_scale": 1.0,
    }


def _base_url(value: str | None) -> str:
    if value:
        return value.rstrip("/")
    return f"http://127.0.0.1:{ports.get('gateway')}"


def _get(client: httpx.Client, path: str, timeout: float = 30.0) -> dict:
    r = client.get(path, timeout=timeout)
    r.raise_for_status()
    return r.json()


def check_kernel(client: httpx.Client) -> None:
    info = _get(client, "/api/system/info")
    kernel = info.get("gateway_kernel") or {}
    kind = kernel.get("kind")
    print(f"[smoke] kernel={kind} path={kernel.get('path')}")
    if kind != "shim":
        raise AssertionError("网关内核不是 shim：把 main.cp312-win_amd64.pyd 移出仓库根")


def check_engine(client: httpx.Client) -> None:
    health = _get(client, "/api/health")
    engine = health.get("audiocpp") or {}
    print(f"[smoke] engine={engine.get('status')} backend={engine.get('backend')} models={engine.get('models')}")
    if engine.get("status") != "ok":
        raise AssertionError(f"引擎不可用：{health}")


def run_gen(client: httpx.Client, steps: int, seed: int, timeout: float) -> None:
    started = time.time()
    r = client.post("/api/music/generate", json=_tiny_payload(steps, seed), timeout=timeout)
    cost = time.time() - started
    if r.status_code != 200:
        raise AssertionError(f"生成失败 HTTP {r.status_code}: {r.text[:300]}")
    body = r.content
    if not _wav_ok(body):
        raise AssertionError(f"返回的不是 WAV：{r.headers.get('content-type')} {len(body)} 字节")
    print(f"[smoke] gen ok {len(body) / 1024 / 1024:.2f}MB in {cost:.1f}s "
          f"(steps={steps}, seed={seed})")


def _ffmpeg() -> str | None:
    for cand in (
        ROOT / "py312" / "ffmpeg" / "bin" / "ffmpeg.exe",
        ROOT / "py312" / "ffmpeg" / "bin" / "ffmpeg",
    ):
        if cand.is_file():
            return str(cand)
    import shutil
    return shutil.which("ffmpeg")


def _probe_source() -> Path | None:
    """优先用真实歌曲片段；没有才退回合成音（合成音可能转不出谱）。"""
    patterns = ("runtime/output/*.wav", "scripts/bak/*.wav", "scripts/*.wav")
    for pattern in patterns:
        for p in sorted(ROOT.glob(pattern)):
            if p.is_file() and p.stat().st_size > 64 * 1024:
                return p
    return None


def _make_mp3(ffmpeg: str, src: Path | None, dest: Path, seconds: int = 8) -> Path:
    if src is not None:
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-t", str(seconds), "-i", str(src), "-ac", "1", "-ar", "44100",
               "-b:a", "128k", str(dest)]
    else:
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
               "-ac", "1", "-ar", "44100", "-b:a", "128k", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def run_score(client: httpx.Client, timeout: float) -> None:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise AssertionError("找不到 ffmpeg，无法生成 mp3 探针")
    src = _probe_source()
    with tempfile.TemporaryDirectory(prefix="yue2_smoke_") as tmp:
        mp3 = _make_mp3(ffmpeg, src, Path(tmp) / "probe.mp3")
        with mp3.open("rb") as fh:
            r = client.post(
                "/api/score",
                files={"audio": ("probe.mp3", fh, "audio/mpeg")},
                data={"melody_only": "true"},
                timeout=60,
            )
        if r.status_code != 200:
            raise AssertionError(f"出谱提交失败 HTTP {r.status_code}: {r.text[:300]}")
        job_id = (r.json() or {}).get("job_id")
        if not job_id:
            raise AssertionError(f"出谱没有返回 job_id：{r.text[:300]}")
        print(f"[smoke] score job={job_id} src={src.name if src else 'sine(合成)'}")

        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            rec = _get(client, f"/api/score/result?job_id={job_id}")
            last = rec
            if rec.get("done"):
                if rec.get("error"):
                    raise AssertionError(f"出谱失败：{rec['error']}")
                abc = (rec.get("abc") or "").strip()
                if not abc.upper().startswith("X:"):
                    raise AssertionError(f"出谱结果不是 ABC：{abc[:120]!r}")
                print(f"[smoke] score ok {len(abc)} 字符")
                score_id = rec.get("score_id")
                if score_id:
                    client.delete(f"/api/scores/{score_id}", timeout=30)
                return
            time.sleep(2.0)
        raise AssertionError(f"出谱超时（{timeout:.0f}s）：{last}")


def _pick_lrc_item(items: list | None) -> tuple[str, str, str] | None:
    """挑一首“已完成且带歌词”的服务器歌曲；没有就返回 None。"""
    for it in items or []:
        if not isinstance(it, dict) or it.get("status") != "done":
            continue
        kind = it.get("kind") or "generate"
        if kind not in ("generate", "upload", "history"):
            continue
        rid = str(it.get("id") or "").strip()
        lyrics = str(it.get("lyrics") or "").strip()
        if rid and lyrics:
            name = str(it.get("task_name") or it.get("name") or rid)
            return rid, lyrics, name
    return None


def _lrc_has_timeline(text: str) -> bool:
    """LRC 的标准行形如 [00:21.45]歌词；不接受只有 [Verse] 段标。"""
    return bool(re.search(r"\[\d{1,3}:\d{2}(?:\.\d{1,3})?\]", str(text or "")))


def run_lrc(client: httpx.Client, timeout: float) -> None:
    """⑭：直接用服务器上已生成、且带歌词的歌跑一遍真实对齐。"""
    listing = _get(client, "/api/generate/list?limit=100")
    picked = _pick_lrc_item(listing.get("items") if isinstance(listing, dict) else None)
    if not picked:
        raise AssertionError("服务器上没有“已完成且带歌词”的歌，无法验证 LRC；先跑一首带歌词的歌")
    rid, lyrics, name = picked
    r = client.post("/api/lrc/start",
                    data={"rid": rid, "lyrics": lyrics, "task_name": name},
                    timeout=60)
    if r.status_code != 200:
        raise AssertionError(f"LRC 提交失败 HTTP {r.status_code}: {r.text[:300]}")
    job_id = (r.json() or {}).get("id")
    if not job_id:
        raise AssertionError(f"LRC 没有返回 id：{r.text[:300]}")
    print(f"[smoke] lrc job={job_id} song={rid}")
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        rec = _get(client, f"/api/lrc/status/{job_id}")
        last = rec
        if rec.get("status") == "error":
            raise AssertionError(f"LRC 失败：{rec.get('error')}")
        if rec.get("status") == "done":
            lrc_text = str(rec.get("lrc") or "")
            if not _lrc_has_timeline(lrc_text):
                raise AssertionError(f"LRC 没有时间轴：{lrc_text[:120]!r}")
            print(f"[smoke] lrc ok {len(lrc_text.splitlines())} 行"
                  f"{' · eLRC' if rec.get('has_elrc') else ''}")
            client.delete(f"/api/lrc/{job_id}", timeout=30)
            return
        time.sleep(2.0)
    raise AssertionError(f"LRC 超时（{timeout:.0f}s）：{last}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YuE2 音乐工作台真引擎冒烟")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--kernel", action="store_true", help="只检查内核与引擎状态")
    parser.add_argument("--gen", action="store_true", help="极小生成（4 步默认）")
    parser.add_argument("--score", action="store_true", help="mp3 出谱")
    parser.add_argument("--lrc", action="store_true", help="用服务器上带歌词的歌跑真实 LRC 对齐")
    parser.add_argument("--all", action="store_true", help="kernel+gen+score")
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)

    base = _base_url(args.base_url)
    do_kernel = args.kernel or args.all or not (args.gen or args.score or args.lrc)
    do_gen = args.gen or args.all
    do_score = args.score or args.all
    do_lrc = args.lrc

    try:
        with httpx.Client(base_url=base, trust_env=False) as client:
            _get(client, "/api/health")
            print(f"[smoke] target={base}")
            if do_kernel:
                check_kernel(client)
                check_engine(client)
            if do_gen:
                run_gen(client, args.steps, args.seed, args.timeout)
            if do_score:
                run_score(client, args.timeout)
            if do_lrc:
                run_lrc(client, args.timeout)
    except httpx.HTTPError as exc:
        print(f"[smoke] 服务不可达或请求失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except AssertionError as exc:
        print(f"[smoke] FAIL {exc}", file=sys.stderr)
        return 1
    print("[smoke] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
