#!/usr/bin/env python
"""生成质量基准运行器（issue #12）。

固定样本集在 bench/prompts.yaml（写死 style/歌词/seed/档位，永不改）；
本脚本把它们提交到工作台的现有批量队列，轮询到终态，把产物按版本归档。

    py312\\python.exe bench\\run_bench.py --dry-run     # 只打印计划，不联网、不占卡
    py312\\python.exe bench\\run_bench.py               # 提交 → 轮询 → 归档（占卡约 10 分钟）
    py312\\python.exe bench\\run_bench.py --only zh_pop,en_pop
    py312\\python.exe bench\\run_bench.py --list

产物：bench/output/<版本>/<组>.wav + .json（音频，已被 .gitignore 排除）
      bench/results/<版本>.json（清单摘要，入 git，供历史对比）
退出码：0 全部成功；1 有组失败；2 网关不可达；3 配置/用法错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = ROOT / "bench"
PROMPTS = BENCH_DIR / "prompts.yaml"
OUTPUT_ROOT = BENCH_DIR / "output"
RESULTS_DIR = BENCH_DIR / "results"
LIB_OUTPUT = ROOT / "runtime" / "output"
DEFAULT_BASE = "http://127.0.0.1:7863"
TERMINAL = {"done", "error", "cancelled"}


def _die(msg: str, code: int = 3) -> int:
    print(f"[bench] {msg}", file=sys.stderr)
    return code


def load_set(path: Path = PROMPTS) -> dict:
    import yaml  # 只在真正跑时依赖；--help 不触发
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("groups"), list):
        raise ValueError(f"{path} 缺少 groups 列表")
    return doc


def enabled_groups(doc: dict) -> list[dict]:
    return [g for g in doc["groups"] if g.get("enabled")]


def build_tasks(groups: list[dict]) -> list[dict]:
    tasks = []
    for g in groups:
        if g.get("kind") == "cover":
            raise ValueError(f"组 {g['id']} 是翻唱组（参考音频→乐谱→按谱生成），"
                             "文本到歌曲的运行器还不支持它；先把 enabled 关掉")
        tasks.append({
            "name": f"bench-{g['id']}",
            "style": g["style"],
            "lyrics": g["lyrics"],
            "cot": g["cot"],
            "seed": int(g["seed"]),
            "num_inference_steps": int(g["steps"]),
            "cfg_scale": float(g["cfg_scale"]),
            "model": "yue2",
        })
    return tasks


def api(base: str, method: str, path: str, payload: dict | None = None,
        timeout: float = 30.0) -> dict:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        method=method,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body.decode("utf-8") or "{}") if body else {}


def _git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=False)
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_group(gid: str, rid: str, label_dir: Path,
                  src_dir: Path | None = None) -> dict:
    """把 runtime/output/<rid>.{wav,json} 复制进版本目录，返回清单条目。"""
    src_dir = src_dir or LIB_OUTPUT
    label_dir.mkdir(parents=True, exist_ok=True)
    wav = src_dir / f"{rid}.wav"
    meta = src_dir / f"{rid}.json"
    if not wav.is_file():
        raise FileNotFoundError(f"找不到产物 {wav}")
    dest_wav = label_dir / f"{gid}.wav"
    dest_meta = label_dir / f"{gid}.json"
    if wav.resolve() != dest_wav.resolve():
        import shutil
        shutil.copy2(wav, dest_wav)
    if meta.is_file():
        import shutil
        shutil.copy2(meta, dest_meta)
    try:
        shown = str(dest_wav.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        shown = str(dest_wav).replace("\\", "/")
    return {
        "id": gid,
        "rid": rid,
        "output": shown,
        "bytes": dest_wav.stat().st_size,
        "sha256": _sha256(dest_wav),
    }


def write_manifest(label: str, doc: dict, entries: list[dict], gateway: dict,
                   base: str, results_dir: Path | None = None) -> Path:
    results_dir = results_dir or RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "label": label,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "commit": _git_commit(),
        "gateway_version": gateway.get("version"),
        "gateway_kernel": (gateway.get("gateway_kernel") or {}).get("kind"),
        "base_url": base,
        "set": doc.get("set"),
        "quality": "draft（off/8）",
        "groups": entries,
    }
    path = results_dir / f"{label}.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def _print_plan(groups: list[dict], skipped: list[dict]) -> None:
    print(f"{'组':<14}{'档位':<10}{'seed':>9}  标题")
    for g in groups:
        print(f"{g['id']:<14}{g['cot'] + '/' + str(g['steps']):<10}{g['seed']:>9}  {g.get('title', '')}")
    for g in skipped:
        print(f"[跳过] {g['id']}：{g.get('reason') or 'enabled=false'}")


def run(args) -> int:
    try:
        doc = load_set(Path(args.prompts) if args.prompts else PROMPTS)
    except Exception as exc:
        return _die(f"样例集读取失败：{exc}")

    groups = enabled_groups(doc)
    skipped = [g for g in doc["groups"] if not g.get("enabled")]
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        unknown = want - {g["id"] for g in doc["groups"]}
        if unknown:
            return _die(f"未知组：{', '.join(sorted(unknown))}")
        groups = [g for g in groups if g["id"] in want]

    if args.list or args.dry_run:
        _print_plan(groups if args.list else groups, [] if args.list else skipped)
        if args.dry_run:
            print(f"\n计划：{len(groups)} 组 × draft（off/8）≈ {len(groups) * 10}–{len(groups) * 15} 分钟"
                  f"（实测每首 4–20 分钟，AR 长度取决于歌词）；产物归档到 bench/output/<版本>/")
        return 0

    if not groups and not args.archive_only:
        return _die("没有可跑的组（列出的组都 enabled=false？）")

    base = args.base or DEFAULT_BASE
    try:
        gateway = api(base, "GET", "/api/system/info", timeout=10)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _die(f"网关不可达 {base}：{exc}", 2)
    label = args.label or str(gateway.get("version") or "unknown").lstrip("v")
    label = label if label.startswith("v") else f"v{label}"

    if args.archive_only:
        # 不提交，只把队列里最近一次运行的 bench-* 条目归档（轮询被打断/超时后用）
        try:
            st = api(base, "GET", "/api/batch/status", timeout=30)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return _die(f"读队列失败：{exc}", 2)
        latest: dict[str, dict] = {}
        for it in st.get("items") or []:
            name = str(it.get("name") or "")
            if name.startswith("bench-"):
                latest[name[len("bench-"):]] = it       # 同名的后来者覆盖先前的
        by_name = {gid: str(it.get("id") or "") for gid, it in latest.items()
                   if gid in {g["id"] for g in groups}}
        status = {gid: latest[gid] for gid in by_name}
        if not by_name:
            return _die("队列里没有可归档的 bench-* 条目")
        print(f"[bench] 只归档：{', '.join(sorted(by_name))}")
    else:
        tasks = build_tasks(groups)
        print(f"[bench] 版本 {label} · {len(tasks)} 组 · 提交到 {base}")
        try:
            resp = api(base, "POST", "/api/batch/start",
                       {"name": f"bench {label}", "tasks": tasks}, timeout=60)
        except urllib.error.HTTPError as exc:
            return _die(f"提交失败 HTTP {exc.code}：{exc.read()[:300]!r}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return _die(f"提交失败：{exc}", 2)

        by_name = {}
        for it in resp.get("items") or []:
            name = str(it.get("name") or "")
            if name.startswith("bench-"):
                by_name[name[len("bench-"):]] = str(it.get("id") or it.get("qid") or "")
        if len(by_name) != len(groups):
            return _die(f"提交返回的条目对不上：期望 {len(groups)} 组，拿到 {len(by_name)}")

        deadline = time.time() + args.timeout * 60
        status = {}
        last_note = ""
        timed_out = False
        while time.time() < deadline:
            try:
                st = api(base, "GET", "/api/batch/status", timeout=30)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(f"[bench] 轮询失败（继续等）：{exc}")
                time.sleep(args.poll)
                continue
            items = {str(it.get("id")): it for it in st.get("items") or []}
            pending = []
            for gid, rid in by_name.items():
                it = items.get(rid)
                if it is None:
                    pending.append(gid)
                    continue
                status[gid] = it
                if it.get("status") not in TERMINAL:
                    pending.append(gid)
            note = f"{len(groups) - len(pending)}/{len(groups)} 组到终态"
            if note != last_note:
                print(f"[bench] {note}")
                last_note = note
            if not pending:
                break
            time.sleep(args.poll)
        else:
            timed_out = True
            print(f"[bench] 超时（{args.timeout} 分钟）：已完成/失败的组照常归档，"
                  "其余留在工作台队列里继续跑；之后可用 --archive-only 补归档", file=sys.stderr)


    label_dir = OUTPUT_ROOT / label
    entries = []
    failed = 0
    for g in groups:
        gid = g["id"]
        it = status.get(gid) or {}
        rid = by_name.get(gid)
        if not rid:
            failed += 1
            entries.append({"id": gid, "status": "missing", "error": "队列里没有这一组"})
            print(f"[bench] {gid} 不在队列里")
            continue
        entry = {"id": gid, "title": g.get("title"), "rid": rid,
                 "seed": g["seed"], "cot": g["cot"], "steps": g["steps"],
                 "cfg_scale": g["cfg_scale"], "status": it.get("status"),
                 "seconds": it.get("seconds") or it.get("sec"),
                 "error": it.get("error")}
        if it.get("status") == "done":
            try:
                entry.update(archive_group(gid, rid, label_dir))
                print(f"[bench] {gid} -> {entry['output']} "
                      f"({entry['bytes'] / 1024 / 1024:.1f}MB)")
            except Exception as exc:
                entry["status"] = "archive_error"
                entry["error"] = str(exc)
                failed += 1
                print(f"[bench] {gid} 归档失败：{exc}")
        else:
            failed += 1
            print(f"[bench] {gid} 未完成：{it.get('status')} {it.get('error') or ''}")
        entries.append(entry)

    path = write_manifest(label, doc, entries, gateway, base)
    try:
        shown = path.relative_to(ROOT)
    except ValueError:
        shown = path
    print(f"[bench] 清单：{shown}")
    if not args.keep_library:
        for gid, rid in by_name.items():
            try:
                api(base, "DELETE", f"/api/generate/{rid}", timeout=20)
            except Exception:
                pass
        print("[bench] 已清理工作台历史里的本次条目（产物保留在 bench/output/）")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成质量基准（固定样本集 + 固定 seed）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不联网")
    ap.add_argument("--list", action="store_true", help="列出样例集")
    ap.add_argument("--only", default="", help="只跑指定组，逗号分隔")
    ap.add_argument("--base", default="", help=f"网关地址（默认 {DEFAULT_BASE}）")
    ap.add_argument("--label", default="", help="归档版本名（默认取网关版本号）")
    ap.add_argument("--prompts", default="", help="样例集路径（默认 bench/prompts.yaml）")
    ap.add_argument("--timeout", type=float, default=90.0, help="总超时分钟（默认 90）")
    ap.add_argument("--poll", type=float, default=5.0, help="轮询间隔秒（默认 5）")
    ap.add_argument("--archive-only", action="store_true",
                    help="不提交，只把队列里最近一次 bench-* 运行归档（轮询被打断时用）")
    ap.add_argument("--keep-library", action="store_true",
                    help="归档后不删工作台历史里的本次条目")
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
