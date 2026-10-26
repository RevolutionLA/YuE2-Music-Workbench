"""issue #9：RVC 换声队列落盘与重启恢复的回归锁定。

换声待跑队列（_RVC_QUEUE/_RVC_JOBS）以前是纯内存的——网关被看门狗杀掉或手动重启，
排队中的任务会无声消失：用户提交时明明看到"已加入队列（排在第 N 位）"，回来却发现
任务管理页/换声页都没有这一条，也没有任何报错。

现在落到 runtime/rvc/queue_state.json：pending 的任务重启后重新排队（会做轻量重校验：
音色模型/源文件还在才排回去），running 的任务如实写一份"服务重启，任务中断"的归档
（不自动重跑——推理子进程随网关一起死了，converted.wav 可能是半截，不可信），
cancelled 的任务永远不复活。

这些用例只测队列状态流转与落盘逻辑本身，把真正的 worker 换成一个"堵在 Event 上的
替身"（同 TestRvcSerialQueue 的做法）——绝不打到 GPU/子进程，可与真实任务并行跑。
"""
from __future__ import annotations

import json
import sys
import threading
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_review_fixes import LOCAL_BASE, Sandbox, app  # noqa: E402


class RvcQueuePersistBase(Sandbox):
    """把换声队列的落盘状态与内存队列都按到临时沙箱里，worker 换成可控替身。"""

    def setUp(self) -> None:
        super().setUp()
        self._saved_rvc = (app._rvc_convert_worker, app._rvc_models, app._win_toast,
                           list(app._RVC_QUEUE), app._RVC_WORKER, app._RVC_CURRENT[0],
                           dict(app._RVC_JOBS))
        app._rvc_models = lambda: ["孙燕姿.pth"]
        app._win_toast = lambda *a, **k: None

        self.worker_calls: list[str] = []
        self.release = threading.Event()      # 放行当前这条替身任务
        self.entered = threading.Event()      # 通知"替身任务已经进入 worker"

        def fake_worker(rid, job, *args, **kw):
            # 与真实 _rvc_convert_worker 的落盘行为保持一致：开算标 running、
            # 算完标 done 并从持久化队列摘除——替身不忠实于这一点，用例就是自欺欺人。
            self.worker_calls.append(rid)
            with app._RVC_LOCK:
                app._RVC_JOBS[rid] = {**app._RVC_JOBS.get(rid, {"id": rid}),
                                      "status": "running", "queue_pos": 0}
            self.entered.set()
            self.release.wait(10)
            with app._RVC_LOCK:
                app._RVC_JOBS[rid] = {**app._RVC_JOBS.get(rid, {}), "status": "done"}
            app._rvc_state_remove(rid)

        app._rvc_convert_worker = fake_worker
        app._RVC_QUEUE.clear()
        app._RVC_WORKER = None
        app._RVC_CURRENT[0] = None
        app._RVC_JOBS.clear()

    def tearDown(self) -> None:
        self.release.set()                    # 别让还堵着的替身任务把线程带走
        deadline = time.time() + 5
        while time.time() < deadline and app._RVC_WORKER is not None and app._RVC_WORKER.is_alive():
            time.sleep(0.02)
        worker, models, toast, queue, _thread, current, jobs = self._saved_rvc
        app._rvc_convert_worker = worker
        app._rvc_models = models
        app._win_toast = toast
        app._RVC_QUEUE[:] = queue
        app._RVC_WORKER = None
        app._RVC_CURRENT[0] = current
        app._RVC_JOBS.clear()
        app._RVC_JOBS.update(jobs)
        super().tearDown()

    # ---- 小工具 ---- #
    def _make_job(self, rid, model="孙燕姿.pth", src_file="src.wav", write_src=True, **overrides):
        in_dir = app.RVC_JOB_DIR / rid
        in_dir.mkdir(parents=True, exist_ok=True)
        if write_src:
            (in_dir / src_file).write_bytes(b"RIFFxxxx")
        job = {"id": rid, "status": "pending", "step": "排队中", "queue_pos": 0,
               "ts": "2026-01-01T00:00:00", "task_name": "持久化测试",
               "model": model, "pitch": 0, "f0_method": "rmvpe",
               "index_rate": 0.5, "protect": 0.33, "rms_mix_rate": 0.25,
               "filter_radius": 3, "resample_sr": 0,
               "src_name": "demo.wav", "src_size": 8, "src_duration": 0.0,
               "src_file": src_file,
               "separate_vocal": True, "gate": True, "strip_harmony": False}
        job.update(overrides)
        return job

    def _args_for(self, rid, job):
        in_dir = app.RVC_JOB_DIR / rid
        src = in_dir / job.get("src_file", "src.wav")
        return (rid, job, src, in_dir, job["model"], job["pitch"], job["f0_method"],
                job["index_rate"], job["protect"], job["rms_mix_rate"],
                job["separate_vocal"], job["gate"], job["strip_harmony"],
                job["filter_radius"], job["resample_sr"])

    def _register_pending(self, rid, **job_kw):
        """模拟"已经通过 /api/rvc/convert 提交、正在排队"的状态：内存 + 落盘 + 真实源文件。"""
        job = self._make_job(rid, **job_kw)
        with app._RVC_LOCK:
            app._RVC_JOBS[rid] = job
        app._rvc_state_upsert({"id": rid, "status": "pending", "job": job})
        return job

    def _state_items(self):
        try:
            return json.loads(app._RVC_QUEUE_STATE.read_text(encoding="utf-8")).get("items", [])
        except Exception:
            return []

    def _state_get(self, rid):
        return next((it for it in self._state_items() if it.get("id") == rid), None)

    def _output_meta(self, rid):
        try:
            return json.loads((app.OUTPUT_DIR / f"{rid}.json").read_text(encoding="utf-8"))
        except Exception:
            return None

    def _wait_until(self, pred, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pred():
                return True
            time.sleep(0.02)
        return False


class TestRvcQueueLifecyclePersisted(RvcQueuePersistBase):
    """提交/开算/取消/失败这四个状态转移，都必须如实反映到持久化队列文件里。"""

    def test_pending_and_running_are_persisted(self):
        rid = "20260101_100000_aaaaaaaa"
        job = self._register_pending(rid)
        self.assertEqual(self._state_get(rid)["status"], "pending")

        app._rvc_mark_running(rid, job)
        self.assertEqual(self._state_get(rid)["status"], "running",
                         "开算那一刻必须落盘：重启后要能分清「还没排到」和「算到一半被杀」")

    def test_done_via_worker_removes_from_state(self):
        """跑到终态（done）必须摘除：output/meta.json 才是这条结果的权威归档，
        queue_state.json 只负责"还没跑完的东西别在重启后丢了"。

        走的是真实队列线程 + 替身 worker（替身忠实镜像了真实 worker 的落盘行为，
        见 RvcQueuePersistBase.fake_worker 的注释），不直接调真实 _rvc_convert_worker：
        那条路要真起 RVC 推理子进程，测试沙箱里不该碰。
        """
        rid = "20260101_100000_aaaaaaaa"
        job = self._register_pending(rid)
        app._rvc_submit(self._args_for(rid, job))
        self.assertTrue(self.entered.wait(5), "队列线程没把任务交给替身 worker")
        self.assertIsNotNone(self._state_get(rid), "还没跑完时，它必须一直留在持久化队列里")
        self.release.set()
        self.assertTrue(self._wait_until(lambda: self._state_get(rid) is None),
                        "跑完（done）之后必须从持久化队列摘掉")
        self.assertEqual(self.worker_calls, [rid])

    def test_cancel_removes_from_state_file(self):
        """取消掉的任务同步从持久化队列摘除：网关下次重启不得把它排回去（issue #9）。"""
        rid = "20260101_100001_bbbbbbbb"
        job = self._register_pending(rid)
        with app._RVC_QUEUE_LOCK:
            app._RVC_QUEUE.append(self._args_for(rid, job))
        self.assertTrue(app._rvc_cancel(rid))
        self.assertIsNone(self._state_get(rid), "cancelled 是终态，不得留在持久化队列里")

    def test_worker_error_removes_from_state_file(self):
        """算失败（worker 内部抛异常）同样是终态：摘除，不当成待复活。"""
        rid = "20260101_100002_cccccccc"
        # separate_vocal=False：跳过人声分离（那步要真的加载 BS-Roformer 模型），
        # 直奔推理子进程那一步，用 stub 让它失败——这正是要测的"error 也要摘除"路径。
        job = self._register_pending(rid, separate_vocal=False)
        with app._RVC_LOCK:
            app._RVC_JOBS[rid] = {**job, "status": "running"}

        class _Failed:
            returncode = 1
            stdout = b""
            stderr = b"intentional failure"

        saved_subprocess = app.subprocess
        app.subprocess = types.SimpleNamespace(
            run=lambda *a, **kw: _Failed(),
            CREATE_NO_WINDOW=getattr(saved_subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.release.set()                    # 直接同步跑真实 worker，不走队列线程
        try:
            # 必须用 setUp 之前抓下来的那个真 worker：app._rvc_convert_worker 已被
            # 换成替身，调它只会得到一条假的 "done"，这条用例就变成了自欺欺人。
            self._saved_rvc[0](*self._args_for(rid, job))
        finally:
            app.subprocess = saved_subprocess
        with app._RVC_LOCK:
            self.assertEqual(app._RVC_JOBS[rid]["status"], "error")
        self.assertIsNone(self._state_get(rid), "error 也是终态，必须从持久化队列摘掉")


class TestRvcBootResume(RvcQueuePersistBase):
    """网关重启时 _rvc_boot_resume 的四种分支：接回 pending、如实标记 running 中断、
    重校验不过判死、崩溃循环保护判死。"""

    def _write_state(self, items):
        app._RVC_QUEUE_STATE.parent.mkdir(parents=True, exist_ok=True)
        app._RVC_QUEUE_STATE.write_text(json.dumps({"items": items}, ensure_ascii=False),
                                        encoding="utf-8")

    def test_pending_is_requeued_and_stays_in_state_file(self):
        rid = "20260101_110000_aaaaaaaa"
        job = self._make_job(rid)
        self._write_state([{"id": rid, "status": "pending", "job": job}])

        result = app._rvc_boot_resume()
        self.assertEqual(result, {"restored": 1, "judged_dead": 0, "interrupted": 0})
        # 恢复走的是 _rvc_submit，它会把队列线程拉起来——所以替身 worker 立刻被叫到并堵在
        # Event 上。断言"一次都没被调用"是写错了：那要等队列线程永远不启动才成立。
        self.assertTrue(self.entered.wait(5), "恢复的 pending 任务应马上被队列线程接手")
        self.assertEqual(self.worker_calls, [rid])
        # 重新排回去了：内存队列里能找到它（可能已从 _RVC_QUEUE 挪到 _RVC_CURRENT），
        # 持久化文件里也还留着（等它真跑完才摘除）
        with app._RVC_QUEUE_LOCK:
            queued_ids = [a[0] for a in app._RVC_QUEUE] + [app._RVC_CURRENT[0]]
        self.assertIn(rid, queued_ids, "pending 的任务必须真的被重新排回队列，不能只是嘴上说恢复了")
        self.assertIsNotNone(self._state_get(rid), "接回去之后仍要留在持久化队列里，直到它跑到终态")
        with app._RVC_LOCK:
            self.assertEqual(app._RVC_JOBS[rid]["status"], "running")

        self.release.set()
        self.assertTrue(self._wait_until(lambda: rid not in [a[0] for a in app._RVC_QUEUE]
                                         and self._state_get(rid) is None),
                        "放行之后它应该正常跑完，并从持久化队列里摘掉")

    def test_running_is_marked_interrupted_not_requeued(self):
        rid = "20260101_110001_bbbbbbbb"
        job = self._make_job(rid)
        self._write_state([{"id": rid, "status": "running", "job": job}])

        result = app._rvc_boot_resume()
        self.assertEqual(result, {"restored": 0, "judged_dead": 0, "interrupted": 1})
        with app._RVC_QUEUE_LOCK:
            self.assertEqual(app._RVC_QUEUE, [], "running 的任务不许自动重跑")
        self.assertIsNone(self._state_get(rid), "已判为终态，不该留在持久化队列里")
        meta = self._output_meta(rid)
        self.assertIsNotNone(meta, "中断要留痕：历史页要能看到这条记录，而不是凭空消失")
        self.assertEqual(meta["status"], "error")
        self.assertIn("服务重启，任务中断", meta["error"])

    def test_pending_with_missing_model_is_judged_dead(self):
        rid = "20260101_110002_cccccccc"
        job = self._make_job(rid, model="已删除的音色.pth")
        self._write_state([{"id": rid, "status": "pending", "job": job}])

        result = app._rvc_boot_resume()
        self.assertEqual(result, {"restored": 0, "judged_dead": 1, "interrupted": 0})
        meta = self._output_meta(rid)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["status"], "error")
        self.assertIn("已删除的音色.pth", meta["error"])
        self.assertIsNone(self._state_get(rid))

    def test_pending_with_missing_src_file_is_judged_dead(self):
        rid = "20260101_110003_dddddddd"
        job = self._make_job(rid, write_src=False)     # 建了目录但没有源文件
        self._write_state([{"id": rid, "status": "pending", "job": job}])

        result = app._rvc_boot_resume()
        self.assertEqual(result, {"restored": 0, "judged_dead": 1, "interrupted": 0})
        meta = self._output_meta(rid)
        self.assertIsNotNone(meta)
        self.assertIn("源音频已丢失", meta["error"])

    def test_crash_loop_protection_stops_reviving(self):
        """同一条目在窗口内被反复复活超过上限，就停在 error，不再陪着它一遍遍重启。"""
        rid = "20260101_110004_eeeeeeee"
        job = self._make_job(rid)
        item = {"id": rid, "status": "pending", "job": job,
                "revive_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "revive_n": app._REVIVE_MAX}
        self._write_state([item])

        result = app._rvc_boot_resume()
        self.assertEqual(result["judged_dead"], 1, "已经复活到上限，这一次必须判死而不是再接一遍")
        self.assertEqual(result["restored"], 0)
        meta = self._output_meta(rid)
        self.assertIsNotNone(meta)
        self.assertIn("停止自动重排", meta["error"])

    def test_cancelled_leftover_is_never_revived(self):
        """兜底：万一持久化文件里因为写入时序问题残留了一条 cancelled，也绝不复活。"""
        rid = "20260101_110005_ffffffff"
        job = self._make_job(rid)
        self._write_state([{"id": rid, "status": "cancelled", "job": job}])

        result = app._rvc_boot_resume()
        self.assertEqual(result, {"restored": 0, "judged_dead": 0, "interrupted": 0})
        with app._RVC_QUEUE_LOCK:
            self.assertEqual(app._RVC_QUEUE, [])
        self.assertIsNone(self._output_meta(rid), "cancelled 不是错误，不该凭空多出一条失败记录")


class TestRvcQueueStateResilience(RvcQueuePersistBase):
    """issue #9 验收第 3 条：损坏单条记录不影响其他任务读取，并且要留下可诊断的错误。"""

    def test_corrupt_file_is_moved_aside_with_warning(self):
        app._RVC_QUEUE_STATE.parent.mkdir(parents=True, exist_ok=True)
        app._RVC_QUEUE_STATE.write_text("{这不是 JSON", encoding="utf-8")

        with self.assertLogs("fireredtts3_gateway", level="WARNING") as logs:
            state = app._rvc_state_read()
        self.assertEqual(state, {"items": []})
        self.assertTrue(any("损坏" in m for m in logs.output), "整份文件损坏必须留下可诊断的日志")

        leftovers = list(app._RVC_QUEUE_STATE.parent.glob("queue_state.corrupt-*.json"))
        self.assertEqual(len(leftovers), 1, "损坏文件要另存留证，不能被下一次落盘悄悄覆盖")
        self.assertEqual(leftovers[0].read_text(encoding="utf-8"), "{这不是 JSON")

        # 清空之后照常能写：不能让一次损坏卡死后续所有提交
        app._rvc_state_upsert({"id": "20260101_120000_deadbeef", "status": "pending", "job": {}})
        self.assertIsNotNone(self._state_get("20260101_120000_deadbeef"))

    def test_malformed_items_are_skipped_others_survive(self):
        rid = "20260101_120001_aaaaaaaa"
        job = self._make_job(rid)
        app._RVC_QUEUE_STATE.parent.mkdir(parents=True, exist_ok=True)
        app._RVC_QUEUE_STATE.write_text(json.dumps({"items": [
            {"id": rid, "status": "pending", "job": job},
            "一条字符串",
            {"no_id": True},
            None,
        ]}, ensure_ascii=False), encoding="utf-8")

        with self.assertLogs("fireredtts3_gateway", level="WARNING") as logs:
            result = app._rvc_boot_resume()
        self.assertTrue(any("结构不合法" in m for m in logs.output))
        self.assertEqual(result["restored"], 1, "坏记录不许连坐同一文件里的好记录")
        self.assertTrue(self.entered.wait(5), "好记录必须真的被重新排回队列")
        self.assertEqual(self.worker_calls, [rid])
        # 恢复结束时会把"只有合法条目"的状态写回文件，畸形记录不会一直挂在里面
        self.assertEqual([it.get("id") for it in self._state_items()], [rid])


if __name__ == "__main__":
    unittest.main()
