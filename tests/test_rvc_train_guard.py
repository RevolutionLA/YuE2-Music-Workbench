"""回归用例：音色训练长跑的三道守卫（心跳判活 / 事故留痕 / 开训前让显存）。

来源是 2026-10-01 的 LA 音色训练事故，复盘见 docs/故障复盘-LA音色训练中断-20261001.md：
训练子进程在第 26 轮被显卡驱动故障钉死，既不退出也不报错，而当时的等待只有一句
communicate(timeout=4 小时 55 分)——于是任务挂着「训练中」白占 GPU 闸门两个半小时，
第二天点续训又把失败原因覆盖掉，根因只能靠 Windows 事件日志反推。

只碰临时目录与自起的子进程，绝不触发真实训练/生成（不发 /api/generate、/api/train），
CPU/GPU 正在跑歌时也可以放心执行：

    py312\\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

# 编译网关 main.cp312-win_amd64.pyd 不入库，测试需要可 import 的替身（与主测试文件同款）
if "main" not in sys.modules:
    from fastapi import FastAPI
    _stub = types.ModuleType("main")
    _stub.app = FastAPI()
    _stub.settings = types.SimpleNamespace(open_browser=True)
    sys.modules["main"] = _stub

for _name in ("voices", "asr", "denoise", "lrc"):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except Exception:
            sys.modules[_name] = types.ModuleType(_name)

import app  # noqa: E402

PY = sys.executable

# 让日志"停在几小时前"的子进程：模拟驱动故障后卡死不动的训练
HANG = "import time; time.sleep(120)"


class _SubProxy:
    """只替换 run()，其余属性（PIPE / Popen / CREATE_NO_WINDOW…）全部转给真的 subprocess。

    取证查询要 stub，但同一条代码路径里真的 Popen 必须还在——用 SimpleNamespace 造替身
    会把 PIPE 之类一起抹掉，测试就变成了"替身自己崩了"而不是"守卫崩了"。
    """

    def __init__(self, run):
        self._run = run

    def run(self, *a, **k):
        return self._run(*a, **k)

    def __getattr__(self, k):
        return getattr(subprocess, k)


class GuardBase(unittest.TestCase):
    """落盘目录指向临时目录，常量改小让心跳用例几秒内跑完，用完还原。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="yue2-train-guard-")
        self.tmp = Path(self._tmp.name)
        self._saved: dict[str, object] = {}
        # 进程内的任务表必须原样还原：续跑端点会往里写，下一条用例若看见上一条留下的
        # "running/pending"，就会被 409「已有任务在进行中」挡住（查起来像功能坏了）
        self._jobs_snapshot = dict(app.RVC_TRAIN_JOBS)
        self._pause_snapshot = app._RVC_TRAIN_PAUSE_REQ
        self._patch(RVC_DIR=self.tmp / "rvc", RVC_TRAIN_DIR=self.tmp / "trains")
        self._patch(_RVC_POLL_SEC=1, _RVC_RETRY_BACKOFF_SEC=0, _RVC_HB_INTERVAL_SEC=1)
        (self.tmp / "rvc").mkdir(parents=True, exist_ok=True)
        (self.tmp / "trains").mkdir(parents=True, exist_ok=True)
        self.toasts: list[tuple[str, str]] = []
        self._patch(_win_toast=lambda t, b: self.toasts.append((t, b)))

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            setattr(app, k, v)
        app.RVC_TRAIN_JOBS.clear()
        app.RVC_TRAIN_JOBS.update(self._jobs_snapshot)
        app._RVC_TRAIN_PAUSE_REQ = self._pause_snapshot
        self._tmp.cleanup()

    def _patch(self, **kw) -> None:
        for k, v in kw.items():
            self._saved.setdefault(k, getattr(app, k))
            setattr(app, k, v)

    def log_path(self, name: str) -> Path:
        d = self.tmp / "rvc" / "logs" / name
        d.mkdir(parents=True, exist_ok=True)
        return d / "train.log"

    def make_log(self, name: str, epochs: int, mtime_age: float | None = None) -> Path:
        """造一份 train.log：每个完成轮都是 "====> 轮次：N [时间戳]"（判活读的就是它）。"""
        p = self.log_path(name)
        base = time.time() - 5000
        lines = []
        for e in range(1, epochs + 1):
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(base + e * 120))
            lines.append(f"====> 轮次：{e} [{ts}] | (0:02:00.0)")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if mtime_age is not None:
            t = time.time() - mtime_age
            os.utime(p, (t, t))
        return p

    def popen(self, code: str, *argv: str):
        return subprocess.Popen([PY, "-c", code, *argv], cwd=str(self.tmp / "rvc"),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def wait(self, proc, est_sec=3600, name="", stall_rate=1.0, est_rate=0.0,
             measured_ok=False, job=None):
        return app._rvc_wait_step(proc, est_sec, "训练中", name, stall_rate,
                                  est_rate, measured_ok,
                                  job or {"id": "t", "name": name})


class TestHeartbeat(GuardBase):
    def test_stalled_child_is_killed_and_reported(self):
        """进程活着但不再产出 → 判死、杀树、给出可读原因（事故里这一步什么都没做）。"""
        self._patch(_RVC_STALL_MIN_SEC=3)
        self.make_log("STALL", epochs=26, mtime_age=9999)   # 日志上次动是 2.7 小时前
        proc = self.popen(HANG)
        _out, _err, stall = self.wait(proc, name="STALL", stall_rate=1.0)
        self.assertIsNotNone(stall, "卡死的孩子进程必须被判定为中断")
        self.assertIn("第 26 轮", stall)
        self.assertIn("显卡驱动故障", stall)
        self.assertIsNotNone(proc.poll(), "判死后要把整棵树收掉，不能留僵尸占着显存")

    def test_healthy_child_is_not_killed(self):
        """健康任务绝不该被误杀：日志一直在推进 → 正常退出、stall=None。"""
        self._patch(_RVC_STALL_MIN_SEC=3)
        log = self.log_path("OK")
        log.write_text("====> 轮次：1 [2026-10-01 01:00:00]\n", encoding="utf-8")
        code = ("import time,sys,os\n"
                "p=sys.argv[1]\n"
                "for i in range(2,7):\n"
                "    time.sleep(0.7)\n"
                "    open(p,'a',encoding='utf-8').write(f'====> 轮次：{i} [2026-10-01 01:10:00]\\n')\n"
                "    os.utime(p, None)\n")
        proc = self.popen(code, str(log))
        _out, _err, stall = self.wait(proc, name="OK", stall_rate=1.0)
        self.assertIsNone(stall, "日志一直在推进的健康任务不能被判死")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("轮次：6", log.read_text(encoding="utf-8"))

    def test_blanket_timeout_still_backstops(self):
        """总时长到点仍是兜底：没有心跳对象时（name=""）也要收掉并说清是超时。"""
        self._patch(_RVC_STALL_MIN_SEC=99999)
        proc = self.popen("import time; time.sleep(30)")
        _out, _err, stall = self.wait(proc, est_sec=2, name="")
        self.assertIn("超时", stall or "")
        self.assertIsNotNone(proc.poll())

    def test_big_stdout_is_drained_not_deadlocked(self):
        """管道必须由线程抽干：>64KB 输出没人读，孩子会卡在 write 上，"卡死"就是自造的。"""
        proc = self.popen("import sys; sys.stdout.write('x'*400000); "
                          "sys.stderr.write('e'*100000)")
        out, err, stall = self.wait(proc, name="")
        self.assertIsNone(stall)
        self.assertEqual(len(out), 400000, "stdout 要完整收回来")
        self.assertEqual(len(err), 100000)

    def test_slow_pace_leaves_a_warning(self):
        """比本机实测预估慢到 2 倍以上 → 留「显存被抢」警告（LA 那次正是 2.8 倍）。"""
        self.make_log("SLOW", epochs=6, mtime_age=9999)     # 每轮 120 秒的假日志
        self._patch(_RVC_STALL_MIN_SEC=99999)               # 本例只测警告，不测判死
        proc = self.popen("import time; time.sleep(4)")
        job = {"id": "t", "name": "SLOW"}
        # 预估 20 秒/轮、日志实测 120 秒/轮 → 必须报警
        self.wait(proc, est_sec=30, name="SLOW", stall_rate=999.0,
                  est_rate=20.0, measured_ok=True, job=job)
        self.assertIn("pace_warn", job)
        w = job["pace_warn"]
        self.assertGreater(w["epoch_sec"], 2 * w["expect_sec"])
        self.assertIn("显存", w["note"])

    def test_no_warning_when_estimate_is_conservative_fallback(self):
        """预估来自「240 秒/轮保守兜底」时不许报警：那是 CPU 首跑的口径，比了必误报。"""
        self.make_log("FALLBACK", epochs=4, mtime_age=9999)
        proc = self.popen("import time; time.sleep(4)")
        job = {"id": "t", "name": "FALLBACK"}
        self.wait(proc, est_sec=30, name="FALLBACK", stall_rate=999.0,
                  est_rate=20.0, measured_ok=False, job=job)
        self.assertNotIn("pace_warn", job)


    def test_taskkill_failure_falls_back_to_proc_kill(self):
        """taskkill 不生效时必须有兜底：僵尸训练进程会继续钉着整卡显存。"""
        self._patch(_RVC_STALL_MIN_SEC=2, _RVC_STALL_FACTOR=0)
        # 假的 subprocess 模块：run() 什么都不做，等于 taskkill 打不动
        self._patch(subprocess=types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(
            returncode=1, stdout=b"", stderr=b"")))
        self.make_log("STALL2", epochs=7, mtime_age=9999)
        proc = self.popen(HANG)
        _o, _e, stall = self.wait(proc, name="STALL2", stall_rate=1.0)
        self.assertIsNotNone(stall)
        self.assertIsNotNone(proc.poll(), "taskkill 失效时 proc.kill() 必须把进程收掉")


class TestForensics(GuardBase):
    def test_failure_leaves_durable_trail(self):
        """每次中断都要留下读得懂的痕迹：train_error.log + job.attempts，且不许影响任务。"""
        job = {"id": "LA", "name": "LA", "epochs": 100}
        self.make_log("LA", epochs=26)
        app._rvc_record_failure(job, "训练中", "显卡驱动故障或进程卡死", "boom-tail", exit_code=1)
        trail = self.tmp / "rvc" / "logs" / "LA" / "train_error.log"
        self.assertTrue(trail.is_file(), "必须落一份不会被续训覆盖的错误日志")
        text = trail.read_text(encoding="utf-8")
        self.assertIn("停在第 26 轮", text)
        self.assertIn("boom-tail", text)
        self.assertEqual(job["attempts"][0]["exit"], 1)
        persisted = json.loads((self.tmp / "trains" / "LA" / "job.json")
                               .read_text(encoding="utf-8"))
        self.assertEqual(persisted["attempts"][0]["reason"], "显卡驱动故障或进程卡死")

    def test_nonzero_exit_records_and_raises(self):
        """子进程非零退出：留痕 + 原样抛错 + 日志尾巴不许丢。"""
        job = {"id": "X", "name": "X", "epochs": 2, "step_secs": {}}
        with self.assertRaises(RuntimeError) as ctx:
            app._rvc_run_step([PY, "-c", "import sys; sys.stderr.write('cudaBoom\\n'); sys.exit(3)"],
                              job, "F0 提取")
        self.assertIn("exit 3", str(ctx.exception))
        self.assertEqual(job["attempts"][-1]["exit"], 3)
        self.assertIn("cudaBoom", job["log_tail"])

    def test_stalled_exit_raises_dedicated_signal(self):
        """心跳判死要抛专用信号，且留痕——这是「中断」不是「跑得不对」。

        注意 _wall 与 mtime 取较近者：进程刚起来的几十秒里，陈旧日志不算"停更"
        （否则每次自动续跑都会被上一次的旧时间戳立刻判死）。所以这里把倍数系数
        归零，让阈值只由下限决定，几秒钟就能触发。
        """
        self._patch(_RVC_STALL_MIN_SEC=2, _RVC_STALL_FACTOR=0)
        self.make_log("LA", epochs=26, mtime_age=9999)
        job = {"id": "LA", "name": "LA", "epochs": 100, "samples_used": 501, "step_secs": {}}
        with self.assertRaises(app._RvcTrainStalled) as ctx:
            app._rvc_run_step([PY, "-c", HANG], job, "训练中")
        self.assertIn("显卡驱动故障", str(ctx.exception))
        self.assertEqual(job["attempts"][-1]["epoch"], 26)

    def test_resume_archives_error_instead_of_wiping_it(self):
        """续训不许抹掉上一次的死因（事故就是这么丢证据的），要归档进 last_error。"""
        rid = "20261001_034300_deadbeef"
        d = self.tmp / "trains" / rid
        (d / "dataset").mkdir(parents=True)
        (d / "dataset" / "sample_000.wav").write_bytes(b"RIFFfake")
        job = {"id": rid, "name": "LA", "status": "error", "step": "训练中",
               "error": "步骤 训练中 失败（exit 1）", "epochs": 100}
        (d / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        app.RVC_TRAIN_JOBS[rid] = job
        started = []
        real = app._rvc_train_worker
        app._rvc_train_worker = lambda *a, **k: started.append(a)   # 别真起训练
        try:
            app.rvc_train_resume(rid)
        finally:
            app._rvc_train_worker = real
        # 第 7 个位置参数 retrain：不带 restart 的续跑必须是 False（True 会归档旧检查点、
        # 从底模重来，那是"换个轮数重训"的另一条路）
        self.assertEqual(started, [(rid, "LA", 100, False, True, "off", False)])
        after = json.loads((d / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(after.get("last_error"), "步骤 训练中 失败（exit 1）")
        self.assertIsNone(after.get("error"))


class TestAutoResume(GuardBase):
    def test_transient_failure_retries_from_checkpoint(self):
        """偶发中断 → 自动从检查点续跑；第二次成功就不该把任务判死。"""
        calls: list[str | None] = []
        # 先造一个检查点：有检查点时文案必须说"接第 N 轮"，没有才说"从底模重来"
        (self.tmp / "rvc" / "logs" / "LA").mkdir(parents=True, exist_ok=True)
        (self.tmp / "rvc" / "logs" / "LA" / "G_2333333.pth").write_bytes(b"ckpt")
        log = self.make_log("LA", epochs=25)

        def fake_step(cmd, job, step, label=None):
            calls.append(label)
            if len(calls) == 1:
                # 第一次跑到第 26 轮才中断：日志推进了，属"偶发故障"
                log.write_text(log.read_text(encoding="utf-8")
                               + "====> 轮次：26 [2026-10-01 03:42:59]\n", encoding="utf-8")
                raise app._RvcTrainStalled("训练在第 26 轮后停止产出：显卡驱动故障或进程卡死")

        real, app._rvc_run_step = app._rvc_run_step, fake_step
        try:
            job = {"id": "t", "name": "LA", "epochs": 100}
            self.assertIsNone(app._rvc_run_train_step(["x"], job, "LA"))
        finally:
            app._rvc_run_step = real
        self.assertEqual(len(calls), 2, "第一次中断后必须自动再来一次")
        self.assertIn("自动续跑第 2 次", calls[1])
        self.assertIn("接第 26 轮", calls[1])
        self.assertTrue(any("自动续跑" in t for t, _ in self.toasts), "要当场告诉用户")
        self.assertEqual(job["recovered_after"]["from_epoch"], 26)

    def test_no_progress_stops_retrying(self):
        """一启动就死、一轮都没推进 = 不是偶发故障，别磨三小时，把原因交回去。"""
        n = {"i": 0}
        self.make_log("LA", epochs=26)

        def fake_step(cmd, job, step, label=None):
            n["i"] += 1
            raise RuntimeError("底模损坏：CUDA error")

        real, app._rvc_run_step = app._rvc_run_step, fake_step
        try:
            with self.assertRaises(RuntimeError) as ctx:
                app._rvc_run_train_step(["x"], {"id": "t", "name": "LA", "epochs": 100}, "LA")
        finally:
            app._rvc_run_step = real
        self.assertIn("不像偶发故障", str(ctx.exception))
        self.assertEqual(n["i"], 1, "无推进时只试一次，不该接着磨")

    def test_retry_limit_is_honoured(self):
        """每次都有推进但反复中断 → 到上限才放弃，并把"重试过几次"说清楚。"""
        n = {"i": 0}
        log = self.make_log("LA", epochs=0)

        def fake_step(cmd, job, step, label=None):
            n["i"] += 1
            log.write_text(log.read_text(encoding="utf-8") + f"====> 轮次：{n['i']} [x]\n",
                           encoding="utf-8")
            raise app._RvcTrainStalled("显卡驱动故障")

        self._patch(_RVC_TRAIN_RETRY_LIMIT=3)
        real, app._rvc_run_step = app._rvc_run_step, fake_step
        try:
            with self.assertRaises(RuntimeError) as ctx:
                app._rvc_run_train_step(["x"], {"id": "t", "name": "LA", "epochs": 100}, "LA")
        finally:
            app._rvc_run_step = real
        self.assertEqual(n["i"], 3)
        self.assertIn("已自动重试 2 次", str(ctx.exception))

    def test_user_pause_is_not_treated_as_failure(self):
        """暂停是用户主动行为：原样上抛走 paused 收尾，不许被重试逻辑当成中断。"""
        def fake_step(cmd, job, step, label=None):
            raise app._RvcTrainPaused()

        real, app._rvc_run_step = app._rvc_run_step, fake_step
        try:
            with self.assertRaises(app._RvcTrainPaused):
                app._rvc_run_train_step(["x"], {"id": "t", "name": "LA"}, "LA")
        finally:
            app._rvc_run_step = real
        self.assertEqual(self.toasts, [], "暂停不该弹「训练中断」通知")


class TestGpuYield(GuardBase):
    def test_cpu_mode_does_not_touch_anything(self):
        self._patch(backend_mode=lambda: "cpu")
        killed: list[int] = []
        self._patch(_kill_audiocpp_now=lambda: killed.append(1))
        self.assertEqual(app._rvc_gpu_yield_before_train(4), (4, None))
        self.assertEqual(killed, [])

    def test_ample_vram_needs_no_yield(self):
        self._patch(backend_mode=lambda: "cuda", _gpu_total_mb=lambda: 6144,
                    _gpu_free_mb=lambda: 5600, _kill_audiocpp_now=lambda: None)
        bs, info = app._rvc_gpu_yield_before_train(3)
        self.assertEqual(bs, 3)
        self.assertEqual(info["action"], "空闲充足，无需让路")

    def test_contended_vram_unloads_engine(self):
        """引擎占着 3GB → 先卸引擎；腾开之后不必降 batch（lazy_load 下次生成自动重载）。"""
        st = {"free": 2400, "killed": 0}

        def kill():
            st["killed"] += 1
            st["free"] = 5500

        self._patch(backend_mode=lambda: "cuda", _gpu_total_mb=lambda: 6144,
                    _gpu_free_mb=lambda: st["free"], _kill_audiocpp_now=kill)
        self._patch(_RVC_YIELD_SETTLE_SEC=0)
        bs, info = app._rvc_gpu_yield_before_train(3)
        self.assertEqual(st["killed"], 1, "显存不够时必须先让引擎下场")
        self.assertEqual(bs, 3)
        self.assertEqual(info["free_after"], 5500)

    def test_still_short_after_yield_drops_batch(self):
        """让完路仍然不够 → 降一档 batch，别硬顶着跑（顶满最容易触发驱动故障）。"""
        st = {"free": 2400, "killed": 0}

        def kill():
            st["killed"] += 1

        self._patch(backend_mode=lambda: "cuda", _gpu_total_mb=lambda: 6144,
                    _gpu_free_mb=lambda: st["free"], _kill_audiocpp_now=kill,
                    _RVC_YIELD_SETTLE_SEC=0)
        bs, info = app._rvc_gpu_yield_before_train(3)
        self.assertEqual(bs, 2)
        self.assertIn("batch 降到 2", info["note"])

    def test_unreadable_vram_never_blocks_training(self):
        self._patch(backend_mode=lambda: "cuda", _gpu_total_mb=lambda: None,
                    _gpu_free_mb=lambda: None, _kill_audiocpp_now=lambda: None)
        self.assertEqual(app._rvc_gpu_yield_before_train(3), (3, None))


class TestSecondBatchGuards(GuardBase):
    """v1.6.0 四项：存盘间隔、半截检查点、防睡眠、驱动事件与 GPU 采样取证。"""

    def test_save_every_caps_loss_at_ten_epochs(self):
        """中断最多丢 10 轮：旧口径 epochs//4（100 轮=25）丢了 3.5 轮才存一次。"""
        self.assertEqual(app._rvc_save_every(100), 10)
        self.assertEqual(app._rvc_save_every(200), 10)
        self.assertEqual(app._rvc_save_every(1200), 10)
        self.assertEqual(app._rvc_save_every(40), 5)
        self.assertEqual(app._rvc_save_every(12), 5)
        self.assertEqual(app._rvc_save_every(6), 5)     # 小任务别每轮都存
        for e in (1, 2, 3, 4):
            self.assertGreaterEqual(app._rvc_save_every(e), 1)
        self.assertLessEqual(app._rvc_save_every(1200), 10)

    def test_broken_checkpoint_is_detected_without_torch(self):
        """判坏只看尾部 zip 目录记录：完整文件 False、截断副本 True、空文件 True。"""
        import zipfile
        good = self.tmp / "good.pth"
        with zipfile.ZipFile(good, "w") as z:
            z.writestr("x", b"y" * 5000)
        self.assertFalse(app._rvc_ckpt_broken(good), "合法 zip 不许判坏")
        raw = good.read_bytes()
        bad = self.tmp / "bad.pth"
        bad.write_bytes(raw[:-40])                  # 砍掉 EOCD = torch.save 被中途打断
        self.assertTrue(app._rvc_ckpt_broken(bad))
        tiny = self.tmp / "tiny.pth"
        tiny.write_bytes(b"PK")
        self.assertTrue(app._rvc_ckpt_broken(tiny))
        self.assertFalse(app._rvc_ckpt_broken(self.tmp / "不存在.pth"),
                         "读不到一律当没坏：取证工具不许变成新的失败源")

    def test_ckpt_health_quarantines_and_keeps(self):
        """半截的隔离、好的原地不动；查的是目录里全部 G/D 档，不只是旧制式那一份。

        本机 30 轮实测：新制式（-l 0）存出来叫 G_60/G_80/G_120.pth，写死的
        G_2333333.pth 只有 legacy 目录才有；而续跑取"数字最大"那份，正好是
        保存被打断时最可能残缺的那份——只盯 2333333 等于没查。
        """
        import zipfile
        d = self.tmp / "rvc" / "logs" / "LA"
        d.mkdir(parents=True)
        with zipfile.ZipFile(d / "G_2333333.pth", "w") as z:
            z.writestr("x", b"y" * 5000)
        for name in ("G_80.pth", "G_120.pth", "D_120.pth"):
            with zipfile.ZipFile(d / name, "w") as z:
                z.writestr("x", b"y" * 5000)
        (d / "D_2333333.pth").write_bytes((d / "G_2333333.pth").read_bytes()[:-40])
        (d / "D_80.pth").write_bytes((d / "G_80.pth").read_bytes()[:-40])
        out = app._rvc_ckpt_health("LA")
        self.assertEqual(out["G_2333333.pth"], "ok")
        self.assertEqual(out["G_120.pth"], "ok")
        self.assertIn("隔离", out["D_2333333.pth"])
        self.assertIn("隔离", out["D_80.pth"], "新制式的最高档也要查得到")
        self.assertTrue((d / "G_120.pth").is_file(), "好的检查点不许被搬走")
        self.assertFalse((d / "D_80.pth").is_file())
        self.assertEqual(len(list(d.glob("D_80.bad-*.pth"))), 1, "坏的要留一份可查")
        self.assertEqual(app._rvc_ckpt_health("没这个音色"), {}, "没检查点就空着，不报错")
        # 隔离过的文件带着 .bad- 名字仍在目录里，第二次体检不许再动它们
        self.assertEqual(app._rvc_ckpt_health("LA"),
                         {"D_120.pth": "ok", "G_2333333.pth": "ok",
                          "G_80.pth": "ok", "G_120.pth": "ok"})

    def test_retry_heals_checkpoint_before_relaunch(self):
        """重试前先体检：否则每次重试都在 load 半截检查点时崩，看起来像"续跑没用"。"""
        import zipfile
        d = self.tmp / "rvc" / "logs" / "LA"
        d.mkdir(parents=True)
        (d / "train.log").write_text("====> 轮次：1 [x]\n", encoding="utf-8")
        with zipfile.ZipFile(d / "G_2333333.pth", "w") as z:
            z.writestr("x", b"y" * 5000)
        (d / "D_2333333.pth").write_bytes(b"PK" + b"0" * 200)   # 无 EOCD = 半截
        n = {"i": 0}

        def fake_step(cmd, job, step, label=None):
            n["i"] += 1
            if n["i"] == 1:
                (d / "train.log").write_text("====> 轮次：5 [x]\n", encoding="utf-8")
                raise app._RvcTrainStalled("训练在第 5 轮后停止产出：显卡驱动故障")

        real, app._rvc_run_step = app._rvc_run_step, fake_step
        try:
            job = {"id": "t", "name": "LA", "epochs": 100}
            self.assertIsNone(app._rvc_run_train_step(["x"], job, "LA"))
        finally:
            app._rvc_run_step = real
        self.assertEqual(n["i"], 2)
        self.assertIn("隔离", json.dumps(job.get("ckpt_heal"), ensure_ascii=False))
        self.assertFalse((d / "D_2333333.pth").is_file(), "重试前坏检查点必须已经让位")

    def test_keep_awake_is_called_only_for_training(self):
        """训练中开、结束就关；别的步骤不开——不睡觉这件事只该在长跑时声明。"""
        calls: list[bool] = []
        self._patch(_rvc_keep_awake=lambda on: calls.append(on))
        job = {"id": "t", "name": "LA", "epochs": 2, "step_secs": {}}
        app._rvc_run_step([PY, "-c", "pass"], job, "F0 提取")
        self.assertEqual(calls, [], "非训练步骤不声明")
        job2 = {"id": "t", "name": "LA", "epochs": 2, "step_secs": {}}
        app._rvc_run_step([PY, "-c", "pass"], job2, "训练中")
        self.assertEqual(calls, [True, False], "训练步骤必须成对开关")

    def test_keep_awake_never_raises(self):
        """拿不到 windll（非 Windows / 被策略挡）只安静跳过。"""
        real = sys.modules.get("ctypes")
        boom = types.ModuleType("ctypes")
        boom.windll = types.SimpleNamespace(kernel32=types.SimpleNamespace(
            SetThreadExecutionState=lambda f: (_ for _ in ()).throw(OSError("blocked"))))
        sys.modules["ctypes"] = boom        # 函数内 import 走 sys.modules，这里换掉它
        try:
            self.assertIsNone(app._rvc_keep_awake(True))
        finally:
            if real is not None:
                sys.modules["ctypes"] = real

    def test_driver_events_are_parsed_and_never_break(self):
        """事件取证：能解析成结构化条目；查询炸了必须返回空表，不许把任务带崩。"""
        ok = types.SimpleNamespace(stdout="2026-10-01 03:43:34|153\n2026-10-01 03:44:00|153\n")
        self._patch(subprocess=types.SimpleNamespace(run=lambda *a, **k: ok))
        evs = app._rvc_gpu_events(5)
        self.assertEqual(len(evs), 2)
        self.assertEqual(evs[0], {"at": "2026-10-01 03:43:34", "id": "153"})

        class _Boom:
            def run(self, *a, **k):
                raise OSError("powershell 没了")

        self._patch(subprocess=_Boom())
        self.assertEqual(app._rvc_gpu_events(5), [])
        self._patch(subprocess=types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace()))
        self.assertEqual(app._rvc_gpu_events(5), [], "假对象缺字段时也不许抛")

    def test_stall_reason_carries_driver_events_into_trail(self):
        """抓到的中断要把"同期几条显卡驱动报错"写进原因、错误文件和 job.attempts。"""
        self._patch(_RVC_STALL_MIN_SEC=2, _RVC_STALL_FACTOR=0)
        self.make_log("LA", epochs=26, mtime_age=9999)
        fake = _SubProxy(lambda *a, **k: types.SimpleNamespace(
            stdout="2026-10-01 03:43:34|153\n"))
        self._patch(subprocess=fake)
        job = {"id": "LA", "name": "LA", "epochs": 100, "samples_used": 501, "step_secs": {}}
        with self.assertRaises(app._RvcTrainStalled) as ctx:
            app._rvc_run_step([PY, "-c", HANG], job, "训练中")
        self.assertIn("1 条显卡驱动报错", str(ctx.exception))
        self.assertIn("03:43:34", str(ctx.exception))
        att = job["attempts"][-1]
        self.assertEqual(att["driver_events"][0]["id"], "153")
        trail = self.tmp / "rvc" / "logs" / "LA" / "train_error.log"
        self.assertIn("同期显卡驱动报错", trail.read_text(encoding="utf-8"))

    def test_gpu_sample_parses_real_nvidia_smi_output(self):
        fake = types.SimpleNamespace(
            check_output=lambda *a, **k:
            "37, 5421, 6144, 68, 118.93, 1455, 0x0000000000000000\n")
        self._patch(subprocess=fake)
        s = app._rvc_gpu_sample()
        self.assertEqual(s["util_pct"], 37)
        self.assertEqual(s["mem_used_mb"], 5421)
        self.assertEqual(s["temp_c"], 68)
        self.assertEqual(s["power_w"], 118.9)
        self.assertEqual(s["sm_mhz"], 1455)

    def test_gpu_sample_survives_na_and_failure(self):
        self._patch(subprocess=types.SimpleNamespace(
            check_output=lambda *a, **k: "N/A, [N/A], 6144, 43, 12.5, 300, 0x1\n"))
        s = app._rvc_gpu_sample()
        self.assertEqual(s["util_pct"], "N/A", "拿不到的字段原样留着，不编数字")
        self.assertEqual(s["temp_c"], 43)

        class _Boom:
            def check_output(self, *a, **k):
                raise OSError("没有 nvidia-smi")

        self._patch(subprocess=_Boom())
        self.assertIsNone(app._rvc_gpu_sample())

    def test_gpu_watch_writes_jsonl_and_summary(self):
        """采样要落 jsonl（下次能不能定性全看这条时间线），并汇总峰值进 job。"""
        samples = iter([
            {"util_pct": 99, "mem_used_mb": 5000, "mem_total_mb": 6144, "temp_c": 71,
             "power_w": 120.0, "sm_mhz": 1500, "throttle": "0x0"},
            {"util_pct": 97, "mem_used_mb": 5400, "mem_total_mb": 6144, "temp_c": 83,
             "power_w": 118.0, "sm_mhz": 1200, "throttle": "0x0000000000000004"},
            # 本机空载实测 throttle=0x0000000000000001（GPU 空闲）。它是"没事干"的
            # 正常状态，把它算进"降频采样"就会在每次开训/收尾凭空多一条假警报。
            {"util_pct": 3, "mem_used_mb": 684, "mem_total_mb": 6144, "temp_c": 43,
             "power_w": 12.1, "sm_mhz": 300, "throttle": "0x0000000000000001"},
        ])
        self._patch(_rvc_gpu_sample=lambda: next(samples, None), _RVC_GPU_SAMPLE_SEC=0)
        job: dict = {"id": "t", "name": "LA"}
        st: dict = {}
        app._rvc_gpu_watch("LA", job, st)
        app._rvc_gpu_watch("LA", job, st)
        app._rvc_gpu_watch("LA", job, st)
        w = job["gpu_watch"]
        self.assertEqual(w["samples"], 3)
        self.assertEqual(w["max_temp_c"], 83, "温度要留峰值：散热是这台机器的老问题")
        self.assertEqual(w["max_mem_used_mb"], 5400)
        self.assertEqual(w["min_sm_mhz"], 300)
        self.assertEqual(w["throttle_samples"], 1, "空闲位 0x1 不算降频")
        self.assertEqual(w["last_mem_pct"], 11)
        rows = (self.tmp / "rvc" / "logs" / "LA" / "gpu_health.jsonl"
                ).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 3)
        self.assertIn("temp_c", rows[0])

    def test_gpu_watch_tolerates_dead_sampler(self):
        self._patch(_rvc_gpu_sample=lambda: None, _RVC_GPU_SAMPLE_SEC=0)
        job: dict = {"id": "t", "name": "LA"}
        st: dict = {}
        self.assertIsNone(app._rvc_gpu_watch("LA", job, st))
        self.assertEqual(job.get("gpu_watch"), None, "查不到就不许编出汇总")

    def test_checkpoints_survive_file_vanishing_mid_list(self):
        """glob 与 stat 之间档被剪掉（导出那一步在剪旧档，页面同时在轮询）不许 500。

        本机真跑到过一次：进度接口在"导出成品"那一秒整段抛 FileNotFoundError。
        """
        d = self.tmp / "rvc" / "logs" / "LA"
        d.mkdir(parents=True)
        for i, n in enumerate(("G_25.pth", "G_50.pth", "G_75.pth")):
            (d / n).write_bytes(b"x" * 100)
            t = time.time() - 100 + i          # 显式排好新旧，别赌文件系统的时间精度
            os.utime(d / n, (t, t))
        real_stat = Path.stat
        gone = d / "G_50.pth"

        def stat(self, *a, **k):
            if self == gone:
                raise OSError(2, "系统找不到指定的文件")
            return real_stat(self, *a, **k)

        Path.stat = stat
        try:
            got = app._rvc_checkpoints("LA")
        finally:
            Path.stat = real_stat
        self.assertEqual([p.name for p in got], ["G_75.pth", "G_25.pth"],
                         "丢掉那一份就好，不许把整个列表炸掉")

    def test_pace_warn_quotes_measured_vram(self):
        """慢速警告要把当时的显存/温度/时钟一起写进去：光说"慢 2.8 倍"没有说服力。"""
        self.make_log("SLOW", epochs=6, mtime_age=9999)
        self._patch(_RVC_STALL_MIN_SEC=99999, _RVC_GPU_SAMPLE_SEC=0,
                    _rvc_gpu_sample=lambda: {"util_pct": 99, "mem_used_mb": 5421,
                                             "mem_total_mb": 6144, "temp_c": 83,
                                             "power_w": 118.9, "sm_mhz": 1455,
                                             "throttle": "0x0"})
        proc = self.popen("import time; time.sleep(4)")
        job = {"id": "t", "name": "SLOW"}
        self.wait(proc, est_sec=30, name="SLOW", stall_rate=999.0,
                  est_rate=20.0, measured_ok=True, job=job)
        note = job["pace_warn"]["note"]
        self.assertIn("5421", note)
        self.assertIn("83", note)
        self.assertIn("vram_at_warn", job["pace_warn"])


class TestIncidentReplay(GuardBase):
    def test_la_incident_would_be_caught_in_minutes(self):
        """事故回放：拿 LA 那次的真实数字，验证新守卫几分钟内就能发现，而不是两小时半。"""
        est = 140.7                                  # 本机实测预估：140.7 秒/轮
        limit = max(600, 4.0 * est)                  # 新守卫的判活阈值：10 分钟
        old_deadline = 100 * est + 3600              # 旧方案：4 小时 54 分的一刀切
        self.assertLess(limit, 15 * 60, "判活阈值必须远小于旧的 17668 秒一刀切超时")
        self.assertGreater(old_deadline / limit, 25, "发现中断至少要快 25 倍")
        self.make_log("LA", epochs=26, mtime_age=3700)   # 真实事故：断在 03:43
        age, cur = app._rvc_train_progress("LA", time.time() - 90000)
        self.assertEqual(cur, 26)
        self.assertGreater(age, limit)
        proc = self.popen(HANG)
        # 实弹部分只验"能抓到并说清"，阈值缩到 2 秒；真实阈值用上面的算式核验
        self._patch(_RVC_STALL_MIN_SEC=2, _RVC_STALL_FACTOR=0)
        _o, _e, stall = self.wait(proc, name="LA", stall_rate=est)
        self.assertIn("显卡驱动故障", stall or "")
        self.assertIsNotNone(proc.poll())


class TestStartupRecovery(GuardBase):
    """网关重启把长跑 worker 一起带走时的自恢复（本机 10-03 一小时内死了两次 LA 重训）。"""

    def setUp(self) -> None:
        super().setUp()
        # 启动清理还会翻 output/ 归档并把批量队列复位——那些都是真机路径，测试里全部关到
        # 临时目录/空操作，否则跑一次用例会把本机正在跑的生成任务判成"服务重启，任务中断"
        self._patch(OUTPUT_DIR=self.tmp / "output",
                    _batch_snapshot=lambda: {"items": []},
                    _batch_store=lambda bs: None,
                    _batch_ensure_worker=lambda *a, **k: None,
                    _rvc_train_procs=lambda name: [],
                    _rvc_reap_orphan_trainers=lambda name: [],
                    _rvc_train_procs_alive=lambda name: False)
        self.started: list[tuple] = []
        real = app._rvc_train_worker
        app._rvc_train_worker = lambda *a, **k: self.started.append(a)
        self._real_worker = real

    def tearDown(self) -> None:
        app._rvc_train_worker = self._real_worker
        super().tearDown()

    def make_running(self, rid="r1", name="LA", step="训练中", mtime_age=60,
                     epochs=100, **extra) -> Path:
        d = self.tmp / "trains" / rid / "dataset"
        d.mkdir(parents=True, exist_ok=True)
        (d / "sample_000.wav").write_bytes(b"RIFFfake")
        job = {"id": rid, "name": name, "status": "running", "step": step,
               "epochs": epochs, "error": None, "ts": "2026-10-03T02:50:00", **extra}
        jp = d.parent / "job.json"
        jp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        t = time.time() - mtime_age
        os.utime(jp, (t, t))
        return jp

    def wait_started(self, n=1, seconds=2.0) -> bool:
        end = time.time() + seconds
        while time.time() < end and len(self.started) < n:
            time.sleep(0.05)
        return len(self.started) >= n

    def test_fresh_interruption_is_picked_back_up(self):
        jp = self.make_running()
        app._orphan_cleanup_on_startup()
        self.assertTrue(self.wait_started(), "刚断的任务重启后必须自己接上")
        # 走的是用户点「↻ 续跑」那条路：rid, name, epochs, 分离, 续跑=True, 净化档, 重训
        self.assertEqual(self.started[0][:6], ("r1", "LA", 100, False, True, "off"))
        rec = json.loads(jp.read_text(encoding="utf-8")) if jp.exists() else \
            json.loads((self.tmp / "trains" / "r1" / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["last_error"], "服务重启，任务中断", "死因不许被自动接上抹掉")
        self.assertEqual(rec["auto_resume_count"], 1)
        self.assertIn("resumed_at", rec["auto_resume"])

    def test_stale_zombie_is_left_alone(self):
        """三天前的僵尸记录不许在半夜复活——那是一整晚的 GPU。"""
        jp = self.make_running(mtime_age=3 * 24 * 3600)
        app._orphan_cleanup_on_startup()
        self.assertFalse(self.wait_started(1, 0.4))
        rec = json.loads((self.tmp / "trains" / "r1" / "job.json").read_text(encoding="utf-8"))
        self.assertIn("窗口", rec["auto_resume"]["skipped"])
        self.assertEqual(rec["status"], "error")

    def test_resumes_at_most_twice_then_needs_a_human(self):
        jp = self.make_running(auto_resume_count=2)
        app._orphan_cleanup_on_startup()
        self.assertFalse(self.wait_started(1, 0.4))
        rec = json.loads((self.tmp / "trains" / "r1" / "job.json").read_text(encoding="utf-8"))
        self.assertIn("不再自动接", rec["auto_resume"]["skipped"])

    def test_skips_when_previous_child_is_still_running(self):
        """网关死了而 train.py 还活着（本机 02:51 就是这么演的）：双训练会把检查点写成花。"""
        self._patch(_rvc_train_procs_alive=lambda name: True)
        self.make_running()
        app._orphan_cleanup_on_startup()
        self.assertFalse(self.wait_started(1, 0.4))
        rec = json.loads((self.tmp / "trains" / "r1" / "job.json").read_text(encoding="utf-8"))
        self.assertIn("双训练", rec["auto_resume"]["skipped"])

    def test_pause_is_not_resumed_by_restart(self):
        self.make_running(step="已暂停")
        app._orphan_cleanup_on_startup()
        self.assertFalse(self.wait_started(1, 0.4))
        rec = json.loads((self.tmp / "trains" / "r1" / "job.json").read_text(encoding="utf-8"))
        self.assertIn("用户主动暂停", rec["auto_resume"]["skipped"])

    def test_resume_failure_is_written_not_raised(self):
        """样本目录没了（续跑会 404）时，启动流程绝不能跟着崩——那是整台网关起不来。"""
        self.make_running()
        shutil.rmtree(self.tmp / "trains" / "r1" / "dataset")
        app._orphan_cleanup_on_startup()
        rec = json.loads((self.tmp / "trains" / "r1" / "job.json").read_text(encoding="utf-8"))
        self.assertIn("failed", rec["auto_resume"])
        self.assertFalse(self.started)

    def test_process_probe_never_breaks_the_gate(self):
        """查进程这件事本身不许变成新的失败源：查询炸了/退出码非零都返回 None（不知道
        就当不知道），查到 PID 才算 True。setUp 把探针 stub 掉了，这里换回真的那两个。"""
        real = self._saved["_rvc_train_procs"]
        self._patch(_rvc_train_procs=real,
                    _rvc_train_procs_alive=self._saved["_rvc_train_procs_alive"])
        boom = types.SimpleNamespace(run=lambda *a, **k: (_ for _ in ()).throw(OSError("WMI 挂了")))
        self._patch(subprocess=boom)
        self.assertIsNone(app._rvc_train_procs("LA"))
        self.assertIsNone(app._rvc_train_procs_alive("LA"))
        self._patch(subprocess=types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(stdout="1234 999 N\n5678 42 Y\n",
                                                      returncode=0)))
        self.assertEqual(app._rvc_train_procs("LA"), [(1234, False), (5678, True)])
        self.assertIs(app._rvc_train_procs_alive("LA"), True)
        self._patch(subprocess=types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(stdout="", returncode=0)))
        self.assertIs(app._rvc_train_procs_alive("LA"), False,
                      "查询成功且没匹配就是真没有，不许当成「不知道」")
        self._patch(subprocess=types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(stdout="", returncode=1)))
        self.assertIsNone(app._rvc_train_procs("LA"), "查询失败不许谎报「没有」")

    def test_orphan_child_is_reaped_then_job_resumes(self):
        """爹没了的训练子进程：先收掉（它卡在管道上还占着 5.9GB），再自动接上。

        本机 02:51 的真实形态——网关 02:49 被杀，preprocess 子进程 02:51 还在写日志。
        父进程活着的那种一个都不许碰（那是另一个网关实例正在跑的任务）。
        """
        killed = []
        self._patch(_rvc_train_procs=lambda name: [(2222, False), (3333, True)],
                    _rvc_reap_orphan_trainers=self._saved["_rvc_reap_orphan_trainers"],
                    subprocess=types.SimpleNamespace(
                        run=lambda cmd, *a, **k: killed.append(cmd) or
                        types.SimpleNamespace(returncode=0, stdout=""),
                        CREATE_NO_WINDOW=0x08000000))
        jp = self.make_running()
        app._orphan_cleanup_on_startup()
        self.assertTrue(self.wait_started(), "收完孤儿就该接上")
        self.assertEqual(killed[0][:4], ["taskkill", "/F", "/T", "/PID"], "要连子孙一起收")
        self.assertEqual(killed[0][4], "2222")
        self.assertEqual(len(killed), 1, "只该收那一个死了爹的")
        self.assertNotIn("3333", "".join(" ".join(c) for c in killed), "父进程活着的那个不许动")
        rec = json.loads((self.tmp / "trains" / "r1" / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["orphan_reaped"], [2222], "收了谁的进程要留痕")


if __name__ == "__main__":
    unittest.main(verbosity=2)
