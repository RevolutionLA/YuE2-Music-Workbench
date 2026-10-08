"""v2.0 §四-⑦ / §六 P0：人声伴奏分离四端点的回归锁定（规划 §9.1 表第 2 行）。

覆盖：① 参数校验（空文件/超限/格式白名单）；② 任务名与路径穿越拒绝；
③ strip_harmony 开关影响产物清单；④ 产物命名规范；
⑤ 静音守卫触发时必须报错而不是返回空文件；⑥ 与 _GPU_SEM 串行（并发峰值=1）。

沿用 tests/test_review_fixes.py 的 Sandbox 基线：OUTPUT_DIR / RVC_JOB_DIR / HIST_* 全部
重定向到临时目录，且把 _run_vocal_separation 换成假实现——**不提交任何 GPU 计算**，
可以在真跑歌的同时执行。
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_review_fixes import LOCAL_BASE, Sandbox, app  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ABC = "X:1\nT:t\nM:4/4\nK:C\nC D E F |\n"


def _wav(path: Path, peak: float, sec: float = 0.30, sr: int = 8000) -> Path:
    """写一个真实可读的小 wav（峰值可控），让静音守卫走的是真 soundfile 而不是替身。"""
    import numpy as np
    import soundfile as sf
    n = int(sr * sec)
    t = np.arange(n) / sr
    data = (peak * np.sin(2 * np.pi * 220.0 * t)).astype("float32") if peak > 0 \
        else np.zeros(n, dtype="float32")
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, sr)
    return path


class SeparateBase(Sandbox):
    """公共替身：假分离实现（可注入成功/静音/失败）、假能力探测、假系统通知。"""

    def setUp(self) -> None:
        super().setUp()
        # 注意：Sandbox 已经用了 self._saved（那是它自己的 dict），这里必须另起名字
        self._saved_sep = (app._win_toast, app._sep_available, app._run_vocal_separation,
                           app._SEP_MAX_BYTES, app._DISK_FLOOR_BYTES, dict(app._SEP_JOBS))
        app._win_toast = lambda *a, **k: None
        app._sep_available = lambda: (True, "")
        app._SEP_MAX_BYTES = 10 * 1024 * 1024
        # 沙箱临时目录所在分区剩余空间不足 2GB 时，_stream_upload_to 的磁盘闸门会
        # 把用例挡成 507——那是环境而不是行为，这里显式放行（真实上限另有其用例）。
        app._DISK_FLOOR_BYTES = 0
        app._SEP_JOBS.clear()
        self.calls: list[str] = []
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def tearDown(self) -> None:
        self._wait_all()
        app._SEP_JOBS.clear()
        (app._win_toast, app._sep_available, app._run_vocal_separation,
         app._SEP_MAX_BYTES, app._DISK_FLOOR_BYTES, jobs) = self._saved_sep
        app._SEP_JOBS.update(jobs)
        super().tearDown()

    # ---- 小工具 ---- #
    def _fake_sep(self, *, noharmony: bool = False, silent: str | None = None,
                  boom: str | None = None, delay: float = 0.0):
        """替身：签名与 _run_vocal_separation 一致，产物也落在 in_dir/"sep"。"""

        def run(src, in_dir, job, strip_harmony):
            sep = Path(in_dir) / "sep"
            sep.mkdir(parents=True, exist_ok=True)
            self.calls.append(Path(src).name)
            if boom:
                raise RuntimeError(boom)
            if delay:
                time.sleep(delay)
            job["sep_stage"] = "人声分离（PyMSS BS-Roformer-Resurrection）"
            vocals = _wav(sep / f"{Path(src).stem}_vocals.wav", 0.0 if silent == "vocals" else 0.4)
            acc = _wav(sep / f"{Path(src).stem}_other.wav", 0.0 if silent == "accompaniment" else 0.3)
            artifacts = {"vocals_raw": vocals.name, "accompaniment": acc.name}
            if noharmony and strip_harmony:
                job["sep_stage"] = "去除和声（HP5，只留主唱）"
                return _wav(sep / "vocal_main.wav", 0.35), artifacts
            return vocals, artifacts

        app._run_vocal_separation = run

    def _submit(self, filename="song.wav", data=None, content=None):
        return self.client.post("/api/audio/separate",
                                files={"audio": (filename,
                                                 content if content is not None else b"RIFFfake",
                                                 "audio/wav")},
                                data=data or {})

    def _wait(self, rid: str, timeout: float = 15.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.client.get(f"/api/audio/separate/status/{rid}")
            if r.status_code == 200 and r.json().get("status") in ("done", "error"):
                return r.json()
            time.sleep(0.02)
        self.fail(f"分离任务 {rid} 没有在规定时间内结束")

    def _wait_all(self) -> None:
        for rid in list(app._SEP_JOBS.keys()):
            if app._SEP_JOBS.get(rid, {}).get("status") in ("pending", "running"):
                try:
                    self._wait(rid, timeout=15.0)
                except Exception:
                    pass

    def _rid_of(self, r) -> str:
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]


class TestSeparateValidation(SeparateBase):
    def test_rejects_bad_extension(self):
        for fn in ("lyrics.txt", "score.abc", "noext"):
            r = self._submit(fn)
            self.assertEqual(r.status_code, 400, r.text)
            self.assertIn("不支持的音频格式", r.json()["detail"])

    def test_rejects_empty_upload(self):
        r = self._submit(content=b"")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("空", r.json()["detail"])

    def test_rejects_oversize_upload(self):
        app._SEP_MAX_BYTES = 16
        r = self._submit(content=b"x" * 4096)
        self.assertEqual(r.status_code, 413, r.text)
        self.assertIn("上限", r.json()["detail"])
        # 被限额挡下的提交不许留下工作目录，也不许留下任务记录
        self.assertEqual(list(Path(app.RVC_JOB_DIR).glob("sep_*")), [])
        self.assertEqual(self.client.get("/api/audio/separate/list").json()["total"], 0)

    def test_rejects_when_no_engine(self):
        app._sep_available = lambda: (False, "PyMSS 未安装且未配置 YUE2_GSV_ROOT")
        r = self._submit()
        self.assertEqual(r.status_code, 503, r.text)
        self.assertIn("YUE2_GSV_ROOT", r.json()["detail"])

    def test_task_name_rejects_traversal_and_absolute(self):
        for bad in ("../etc/passwd", "..\\..\\x", "a/b", "a\\b", "C:\\Windows\\x.wav",
                    "/tmp/x", "含\0控制字符\x01"):
            r = self._submit(data={"task_name": bad})
            self.assertEqual(r.status_code, 400, f"{bad!r} 应被拒绝：{r.text}")

    def test_task_name_keeps_legal_and_truncates(self):
        self._fake_sep()
        rid = self._rid_of(self._submit(data={"task_name": "我的歌 2026:最佳"}))
        job = self._wait(rid)
        self.assertEqual(job["task_name"], "我的歌 2026_最佳")  # 冒号是保留字符，换成 _
        long = self._rid_of(self._submit(data={"task_name": "长" * 160}))
        self.assertEqual(len(self._wait(long)["task_name"]), 100)

    def test_rid_and_part_are_validated(self):
        # '..' 会被 Starlette 先做路径归一化（挡在路由层就 405），能进到 handler 的
        # 是 1234 / 大写十六进制这类形状不对的 id —— 两种都算"没被当成合法 ID"
        for bad in ("", "..", "20260101_000000_ZZZZ", "1234"):
            r = self.client.get(f"/api/audio/separate/status/{bad}")
            self.assertGreaterEqual(r.status_code, 400, f"{bad!r}：{r.text}")
            if r.status_code == 400:
                self.assertIn("非法任务 ID", r.json()["detail"])
        self.assertEqual(app._sep_rid("20260101_000000_deadbeef"), "20260101_000000_deadbeef")
        for bad in ("../x", "..\\..\\x", "/etc/passwd", "20260101_000000_DEADBEEF", ""):
            with self.assertRaises(HTTPException) as ctx:
                app._sep_rid(bad)
            self.assertIn("非法任务 ID", str(ctx.exception.detail))
        self._fake_sep()
        rid = self._rid_of(self._submit())
        self._wait(rid)
        r2 = self.client.get(f"/api/audio/separate/audio/{rid}", params={"part": "nope"})
        self.assertEqual(r2.status_code, 400, r2.text)
        self.assertIn("非法产物名", r2.json()["detail"])
        r3 = self.client.get(f"/api/audio/separate/audio/{rid}", params={"part": "../x"})
        self.assertEqual(r3.status_code, 400, r3.text)


class TestSeparateArtifacts(SeparateBase):
    def test_strip_harmony_switch_changes_artifact_list(self):
        self._fake_sep(noharmony=True)
        off = self._rid_of(self._submit(data={"strip_harmony": "off"}))
        on = self._rid_of(self._submit(data={"strip_harmony": "on"}))
        a, b = self._wait(off)["assets"], self._wait(on)["assets"]
        self.assertEqual(sorted(a), ["accompaniment", "vocals"],
                         "关去和声只该有人声与伴奏两份")
        self.assertEqual(sorted(b), ["accompaniment", "vocals", "vocals_noharmony"],
                         "开去和声必须多出第三份主唱")

    def test_harmony_strip_failure_is_reported_not_silent(self):
        # HP5 没产出主唱时 _run_vocal_separation 返回含和声那份：产物只有两份，
        # 但必须在任务上留下说明（不许静默少给一个文件）
        def run(src, in_dir, job, strip_harmony):
            sep = Path(in_dir) / "sep"
            vocals = _wav(sep / f"{Path(src).stem}_vocals.wav", 0.4)
            acc = _wav(sep / f"{Path(src).stem}_other.wav", 0.3)
            return vocals, {"vocals_raw": vocals.name, "accompaniment": acc.name}

        app._run_vocal_separation = run
        rid = self._rid_of(self._submit(data={"strip_harmony": "on"}))
        job = self._wait(rid)
        self.assertEqual(sorted(job["assets"]), ["accompaniment", "vocals"])
        self.assertIn("HP5", job.get("harmony_note", ""), "缺产物必须写明原因")
        self.assertEqual(job["status"], "done", "少了可选产物不该把整条任务判死")

    def test_artifact_names_and_download(self):
        self._fake_sep(noharmony=True)
        rid = self._rid_of(self._submit(data={"strip_harmony": "on", "task_name": "测试曲"}))
        job = self._wait(rid)
        self.assertEqual(job["assets"], {
            "vocals": f"{rid}_vocals.wav",
            "accompaniment": f"{rid}_accompaniment.wav",
            "vocals_noharmony": f"{rid}_vocals_noharmony.wav"})
        for part, fname in job["assets"].items():
            self.assertTrue((Path(app.OUTPUT_DIR) / fname).is_file(), fname)
            r = self.client.get(f"/api/audio/separate/audio/{rid}", params={"part": part})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertGreater(len(r.content), 100, "产物不能是空文件")
            self.assertIn(".wav", r.headers.get("content-disposition", ""))

    def test_absent_part_returns_clear_error(self):
        self._fake_sep()
        rid = self._rid_of(self._submit(data={"strip_harmony": "off"}))
        self._wait(rid)
        r = self.client.get(f"/api/audio/separate/audio/{rid}",
                            params={"part": "vocals_noharmony"})
        self.assertEqual(r.status_code, 404, r.text)
        self.assertIn("没有这份产物", r.json()["detail"])

    def test_list_and_unified_visibility(self):
        self._fake_sep()
        rid = self._rid_of(self._submit(data={"task_name": "登记检查"}))
        self._wait(rid)
        lst = self.client.get("/api/audio/separate/list").json()
        self.assertEqual([i["id"] for i in lst["items"]], [rid])
        self.assertEqual(lst["items"][0]["kind"], "separate")
        # 任务管理页的主源：output/*.json 里必须带 kind=separate，且统一接口能筛出来
        meta = json.loads((Path(app.OUTPUT_DIR) / f"{rid}.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["kind"], "separate")
        uni = self.client.get("/api/tasks/unified", params={"kind": "separate"}).json()
        self.assertEqual([i["id"] for i in uni["items"]], [rid])
        self.assertEqual(uni["items"][0]["name"], "登记检查")
        self.assertEqual(uni["items"][0]["progress"], 100)

    def test_work_dir_is_cleaned_after_done(self):
        self._fake_sep()
        rid = self._rid_of(self._submit())
        self._wait(rid)
        self.assertFalse((Path(app.RVC_JOB_DIR) / f"sep_{rid}").exists(),
                         "上传原曲与中间产物（几十 MB）必须随任务结束清掉")


class TestSeparateFailure(SeparateBase):
    def test_silence_guard_fails_instead_of_returning_empty_file(self):
        self._fake_sep(silent="vocals")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rid = self._rid_of(self._submit())
            job = self._wait(rid)
        self.assertEqual(job["status"], "error")
        self.assertIn("静音守卫", job["error"])
        self.assertIn("数字静音", job["error"])
        # 关键：守卫触发时绝不往 output/ 落空文件
        self.assertEqual(list(Path(app.OUTPUT_DIR).glob(f"{rid}_*.wav")), [])
        r = self.client.get(f"/api/audio/separate/audio/{rid}", params={"part": "vocals"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("静音守卫", r.json()["detail"])
        self.assertIn("[separate]", buf.getvalue(), "静默退化必须留日志")

    def test_silence_guard_also_catches_silent_accompaniment(self):
        self._fake_sep(silent="accompaniment")
        rid = self._rid_of(self._submit())
        job = self._wait(rid)
        self.assertEqual(job["status"], "error")
        self.assertIn("accompaniment", job["error"])

    def test_pymss_failure_reason_is_passed_through(self):
        self._fake_sep(boom="人声分离（PyMSS）未产出完整产物：CUDA out of memory")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rid = self._rid_of(self._submit())
            job = self._wait(rid)
        self.assertEqual(job["status"], "error")
        self.assertIn("out of memory", job["error"], "原始错误文本必须回传，不许换成一句未知错误")
        self.assertIn("人声分离失败", job["error"])
        self.assertIn("[separate]", buf.getvalue())

    def test_fallback_chain_failure_reason_is_passed_through(self):
        self._fake_sep(boom="人声分离（伴奏分离）失败：sep_roformer.py 退出码 1")
        rid = self._rid_of(self._submit())
        job = self._wait(rid)
        self.assertEqual(job["status"], "error")
        self.assertIn("伴奏分离", job["error"])

    def test_missing_artifact_is_an_error(self):
        def run(src, in_dir, job, strip_harmony):
            return Path(src), {}      # 什么都不登记
        app._run_vocal_separation = run
        rid = self._rid_of(self._submit())
        job = self._wait(rid)
        self.assertEqual(job["status"], "error")
        self.assertIn("没有产出完整产物", job["error"])

    def test_status_404_for_unknown_task(self):
        r = self.client.get("/api/audio/separate/status/20260101_000000_deadbeef")
        self.assertEqual(r.status_code, 404, r.text)


class TestSeparateGpuSerial(SeparateBase):
    """照抄 TestRvcSerialQueue 的写法：并发提交，观测 GPU 段内的同时在跑数与峰值。"""

    def setUp(self) -> None:
        super().setUp()
        self.live = 0
        self.peak = 0
        self._lock = threading.Lock()

    def _counting_sep(self, src, in_dir, job, strip_harmony):
        with self._lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
        try:
            time.sleep(0.12)          # 制造重叠窗口：没有串行闸门时峰值一定 >1
            sep = Path(in_dir) / "sep"
            vocals = _wav(sep / f"{Path(src).stem}_vocals.wav", 0.4)
            acc = _wav(sep / f"{Path(src).stem}_other.wav", 0.3)
            return vocals, {"vocals_raw": vocals.name, "accompaniment": acc.name}
        finally:
            with self._lock:
                self.live -= 1

    def test_gpu_section_never_overlaps(self):
        app._run_vocal_separation = self._counting_sep
        ids = [self._rid_of(self._submit(f"song{i}.wav")) for i in range(4)]
        for rid in ids:
            self.assertEqual(self._wait(rid)["status"], "done", rid)
        self.assertEqual(self.peak, 1, "分离必须走 _GPU_SEM，与生成/换声/训练互斥")

    def test_semaphore_not_leaked_by_submit(self):
        """提交接口的"看一眼 GPU 是否空闲"绝不能把全局闸门的一个许可吃掉。"""
        self._fake_sep()
        rids = [self._rid_of(self._submit(f"s{i}.wav")) for i in range(3)]
        for rid in rids:
            self._wait(rid)
        self.assertTrue(app._GPU_SEM.acquire(blocking=False),
                        "提交后 _GPU_SEM 必须还能拿到——吃掉许可会让整机 GPU 永久饿死")
        app._GPU_SEM.release()


class TestTasksUnified(SeparateBase):
    """§六 P0 第二项：GET /api/tasks/unified 一次回五类任务，并按已裁定的历史口径去重。

    裁定（规划 §十一-2）：runtime/output/*.json 是主源，records/history.json 只作
    外部导入记录的补充，去重时主源优先。"""

    def _meta(self, rid: str, **fields) -> None:
        body = {"id": rid, "ts": "2026-01-01T00:00:00", "status": "done", "kind": "generate"}
        body.update(fields)
        (Path(app.OUTPUT_DIR) / f"{rid}.json").write_text(
            json.dumps(body, ensure_ascii=False), encoding="utf-8")

    def test_five_kinds_all_show_up(self):
        self._meta("20260101_000000_aaaaaaaa", task_name="生成的歌")
        self._meta("20260101_000001_bbbbbbbb", kind="rvc", model="蛋卷.pth")
        self._meta("20260101_000002_cccccccc", kind="train", name="LA")
        self._meta("20260101_000003_dddddddd", kind="separate", task_name="分离曲",
                   assets={"vocals": "x.wav"})
        app._score_save("20260101_000004_eeeeeeee", ABC)
        j = self.client.get("/api/tasks/unified").json()
        got = {i["id"]: i["kind"] for i in j["items"]}
        self.assertEqual(got["20260101_000000_aaaaaaaa"], "generate")
        self.assertEqual(got["20260101_000001_bbbbbbbb"], "rvc")
        self.assertEqual(got["20260101_000002_cccccccc"], "train")
        self.assertEqual(got["20260101_000003_dddddddd"], "separate")
        self.assertEqual(got["20260101_000004_eeeeeeee"], "score")
        for i in j["items"]:
            self.assertEqual(set(i) >= {"kind", "id", "name", "status", "ts", "progress"},
                             True, f"统一项缺字段：{i}")

    def test_history_json_is_only_a_supplement(self):
        """同一个 id 两边都有：主源（output meta）赢，不许再冒出一条 external。"""
        self._meta("20260101_000005_ffffffff", task_name="主源名字")
        Path(app.HIST_JSON).write_text(json.dumps([
            {"id": "20260101_000005_ffffffff", "ts": "2026-01-01T00:00:00",
             "file": "x.wav", "style": "外部导入的同一条"},
            {"id": "20260101_000006_11111111", "ts": "2026-01-01T00:00:00",
             "file": "y.wav", "style": "只在外部源里的记录"},
        ], ensure_ascii=False), encoding="utf-8")
        items = self.client.get("/api/tasks/unified").json()["items"]
        by_id = {}
        for i in items:
            self.assertNotIn(i["id"], by_id, f"同一 id 出现两次（{i['id']}）= 去重没做")
            by_id[i["id"]] = i
        self.assertEqual(by_id["20260101_000005_ffffffff"]["kind"], "generate")
        self.assertEqual(by_id["20260101_000005_ffffffff"]["name"], "主源名字")
        self.assertEqual(by_id["20260101_000006_11111111"]["kind"], "external")

    def test_batch_pending_entries_are_visible_as_generate(self):
        Path(app._BATCH_STATE).write_text(json.dumps({
            "running": True, "name": "批量A",
            "items": [{"id": "20260101_000007_22222222", "qid": "q1", "name": "排队的第一首",
                       "status": "pending"}]}, ensure_ascii=False), encoding="utf-8")
        items = self.client.get("/api/tasks/unified", params={"kind": "generate"}).json()["items"]
        self.assertEqual([i["id"] for i in items], ["20260101_000007_22222222"])
        self.assertEqual(items[0]["status"], "pending")
        self.assertEqual(items[0]["progress"], 0)

    def test_kind_filter_and_bad_kind(self):
        self._meta("20260101_000008_33333333", kind="separate")
        self.assertEqual([i["id"] for i in
                          self.client.get("/api/tasks/unified",
                                          params={"kind": "separate"}).json()["items"]],
                         ["20260101_000008_33333333"])
        r = self.client.get("/api/tasks/unified", params={"kind": "everything"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("未知任务类型", r.json()["detail"])

    def test_failed_task_carries_its_reason(self):
        self._meta("20260101_000009_44444444", kind="separate", status="error",
                   error="静音守卫触发：vocals 峰值仅 0.0e+00")
        i = self.client.get("/api/tasks/unified",
                            params={"kind": "separate"}).json()["items"][0]
        self.assertEqual(i["status"], "error")
        self.assertIn("静音守卫", i["error"])
        self.assertIsNone(i["progress"], "失败任务不该被画成某个百分比")


if __name__ == "__main__":
    unittest.main()
