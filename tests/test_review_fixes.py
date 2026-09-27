"""回归用例：adversarial-review 308f833 一轮整改的锁定测试。

只碰临时目录与只读端点，绝不触发任何计算（不发 /api/generate/*、/api/batch/start、
/rvc/*、/train/* 的真实任务），因此 CPU/GPU 正在跑歌时也可以放心执行：

    py312\\python.exe -m unittest discover -s tests -v

每个用例注释里标了对应的蓝军编号（B/D/S/Y），便于评审报告逐条回溯。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

# 编译网关 main.cp312-win_amd64.pyd 不入库（见 README「首次运行准备」），
# 测试需要一个可 import 的替身：app.py 只用 main.app 与 main.settings 两处。
if "main" not in sys.modules:
    from fastapi import FastAPI

    _stub = types.ModuleType("main")
    _stub.app = FastAPI()
    _stub.settings = types.SimpleNamespace(open_browser=True)
    sys.modules["main"] = _stub

# 这些兄弟模块会拉起重依赖（torch / funasr），测试路径上不调用它们
for _name in ("voices", "asr", "denoise", "lrc"):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except Exception:
            sys.modules[_name] = types.ModuleType(_name)

import app  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

LOCAL_BASE = f"http://127.0.0.1:{app.settings.app_port}"


class Sandbox(unittest.TestCase):
    """把 app 模块里的落盘目录全部指向临时目录，用完还原。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="yue2-test-")
        self.tmp = Path(self._tmp.name)
        self._saved: dict[str, object] = {}
        for name in ("OUTPUT_DIR", "SCORES_DIR", "HIST_DIR", "HIST_JSON",
                     "RVC_JOB_DIR", "RVC_TRAIN_DIR", "_BATCH_STATE"):
            self._saved[name] = getattr(app, name)
            setattr(app, name, self.tmp / name.lower())
        for d in (app.OUTPUT_DIR, app.SCORES_DIR, app.HIST_DIR,
                  app.RVC_JOB_DIR, app.RVC_TRAIN_DIR):
            Path(d).mkdir(parents=True, exist_ok=True)
        # 索引目录也一并挪走：网关找配套索引看的是本机 assets/indices 与 logs，
        # 不重定向的话"提交换声"这类用例就只在作者机上通过（作者恰好装过孙燕姿的索引），
        # 换台机器就凭空多出一堆 400。
        self._saved["_rvc_index_dirs"] = app._rvc_index_dirs
        self.rvc_indices = self.tmp / "indices"
        self.rvc_logs = self.tmp / "logs"
        for d in (self.rvc_indices, self.rvc_logs):
            d.mkdir(parents=True, exist_ok=True)
        # CLI 能力探测默认模拟"打过补丁的 runtime"。不 stub 的话 fcpe 用例会真起子进程
        # 探本机 runtime——作者机装了 torchfcpe 就绿、换台没装的机器红得莫名其妙，
        # 这正是索引目录那条评论过的"本机假通过"。个别用例再点名改成缺能力场景。
        self._saved["_rvc_cli_caps"] = app._rvc_cli_caps
        self._saved["_rvc_fcpe_ok"] = app._rvc_fcpe_ok
        app._rvc_cli_caps = lambda: {"filter_radius": True, "fcpe": True}
        app._rvc_fcpe_ok = lambda: True
        app._rvc_index_dirs = lambda: (self.rvc_indices, self.rvc_logs)
        app._ID_SEEN.clear()

    def tearDown(self) -> None:
        for name, value in self._saved.items():
            setattr(app, name, value)
        app._ID_SEEN.clear()
        self._tmp.cleanup()


class TestTaskId(Sandbox):
    def test_ids_are_unique_and_well_formed(self):  # 需求③：ID 唯一
        ids = {app._new_id() for _ in range(300)}
        self.assertEqual(len(ids), 300)
        import re
        for rid in ids:
            self.assertRegex(rid, r"^\d{8}_\d{6}_[0-9a-f]{8}$")

    def test_id_busy_blocks_reuse_of_existing_artifact(self):  # 撞号=覆盖别人产物
        (app.OUTPUT_DIR / "20260101_000000_deadbeef.json").write_text("{}", encoding="utf-8")
        self.assertTrue(app._id_busy("20260101_000000_deadbeef"))
        self.assertFalse(app._id_busy("20260101_000000_cafebabe"))


class TestPurge(Sandbox):
    def test_output_purge_covers_every_extension(self):  # D13/B 系列：以前漏 .elrc
        rid = "20260101_000000_aaaaaaaa"
        for ext in app._OUTPUT_EXTS:
            (app.OUTPUT_DIR / f"{rid}{ext}").write_text("x", encoding="utf-8")
        neighbor = app.OUTPUT_DIR / "20260101_000001_bbbbbbbb.wav"
        neighbor.write_text("x", encoding="utf-8")
        self.assertEqual(app._output_purge(rid), len(app._OUTPUT_EXTS))
        self.assertFalse(any(app.OUTPUT_DIR.glob(f"{rid}.*")))
        self.assertTrue(neighbor.is_file())

    def test_output_purge_refuses_wildcard_and_traversal(self):  # 绝不 glob：'*' 会清空整目录
        keep = app.OUTPUT_DIR / "20260101_000000_aaaaaaaa.wav"
        keep.write_text("x", encoding="utf-8")
        for bad in ("*", "", ".", "..", "*/.."):
            self.assertEqual(app._output_purge(bad), 0)
        self.assertTrue(keep.is_file())

    def test_output_purge_takes_meta_recorded_multi_artifacts(self):
        # 换声多产物叫 `<id>_full_song.wav` 这种名字，后缀白名单命不中：
        # 删一条换声记录曾留下三个几十 MB 的孤儿（本团队自查发现）
        import json
        rid = "20260101_000000_aaaaaaaa"
        (app.OUTPUT_DIR / f"{rid}.wav").write_text("x", encoding="utf-8")
        for part in ("vocals_original", "accompaniment", "full_song"):
            (app.OUTPUT_DIR / f"{rid}_{part}.wav").write_text("x", encoding="utf-8")
        (app.OUTPUT_DIR / f"{rid}.json").write_text(json.dumps(
            {"assets": {"vocals_raw": f"{rid}_vocals_original.wav",
                        "accompaniment": f"{rid}_accompaniment.wav",
                        "full_song": f"{rid}_full_song.wav"}}), encoding="utf-8")
        neighbor = app.OUTPUT_DIR / "20260101_000000_aaaaaaaa_other.wav"
        neighbor.write_text("x", encoding="utf-8")
        self.assertEqual(app._output_purge(rid), 5)  # 3 多产物 + .wav + .json
        self.assertTrue(neighbor.is_file(), "同前缀但 meta 没登记的，不许顺手删")

    def test_output_purge_ignores_assets_that_are_not_this_tasks(self):
        # meta 里的 assets 只认 `<rid>_*.wav` 这个形状，别的（穿越、别人的产物、
        # 奇怪后缀）一概不碰——这是 URL 里的 rid 加上可被改的 meta 两个入口叠出来的面
        import json
        rid = "20260101_000000_aaaaaaaa"
        keep = app.OUTPUT_DIR / "20260101_000001_bbbbbbbb.wav"
        keep.write_text("x", encoding="utf-8")
        (app.OUTPUT_DIR / f"{rid}.json").write_text(json.dumps(
            {"assets": {"a": keep.name, "b": "../../app.py", "c": f"{rid}x.wav",
                        "d": f"{rid}_evil.txt", "e": ""}}), encoding="utf-8")
        self.assertEqual(app._output_extra_assets(rid), [])
        self.assertEqual(app._output_purge(rid), 1)  # 只删掉那份 .json
        self.assertTrue(keep.is_file())

    def test_rvc_job_purge_removes_uploaded_source(self):  # S7：原曲 + 分离 stem 不能变孤儿
        rid = "20260101_000000_aaaaaaaa"
        job = app.RVC_JOB_DIR / rid
        job.mkdir(parents=True)
        (job / "src.wav").write_text("x", encoding="utf-8")
        self.assertTrue(app._rvc_job_purge(rid))
        self.assertFalse(job.exists())

    def test_rvc_job_purge_cannot_escape_jobs_dir(self):  # 只删 jobs/<id>，不接受穿越
        other = self.tmp / "elsewhere"
        other.mkdir()
        (other / "keep.me").write_text("x", encoding="utf-8")
        self.assertFalse(app._rvc_job_purge("../elsewhere"))
        self.assertFalse(app._rvc_job_purge(""))
        self.assertTrue((other / "keep.me").is_file())


class TestInputLimits(Sandbox):
    def test_over_limit_raises_400_instead_of_truncating(self):  # D 系列：静默截断改判错
        from fastapi import HTTPException
        self.assertEqual(app._limit_text("歌词", "a" * 10, 20), "a" * 10)
        with self.assertRaises(HTTPException) as ctx:
            app._limit_text("歌词", "a" * (app._MAX_LYRICS + 1), app._MAX_LYRICS)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("上限", ctx.exception.detail)

    def test_style_and_abc_caps_defined(self):
        self.assertGreater(app._MAX_STYLE, 0)
        self.assertGreaterEqual(app._MAX_ABC, 1000)


class TestChordRouting(Sandbox):
    MELODY_ONLY = "X:1\nK:G\nM:4/4\n|: d2 B2 G2 B2 :|\n"
    WITH_JAZZ_CHORDS = 'X:1\nK:G\nM:4/4\n|: "C9" C,2 "Fm9" D2 "G13" E2 "Csus" F2 :|\n'
    WITH_PLAIN_CHORDS = 'X:1\nK:G\nM:4/4\n|: "C" C,2 "Am7" D2 "G/B" E2 :|\n'

    def test_extended_chord_symbols_count_as_chords(self):  # D7：曾被判成"无和弦"→ 走错路线
        self.assertTrue(app._abc_has_chords(self.WITH_JAZZ_CHORDS))
        self.assertTrue(app._abc_has_chords(self.WITH_PLAIN_CHORDS))
        self.assertFalse(app._abc_has_chords(self.MELODY_ONLY))

    def test_melody_score_forces_melody_route(self):  # 需求②：谱形态与档位双向配对
        cot, note = app._resolve_cot("full", self.MELODY_ONLY)
        self.assertEqual(cot, "melody")
        self.assertIn("melody", note)

    def test_chord_score_forces_full_route(self):
        cot, note = app._resolve_cot("melody", self.WITH_PLAIN_CHORDS)
        self.assertEqual(cot, "full")
        self.assertIn("full", note)

    def test_off_with_score_is_corrected(self):  # off 带谱 = 引擎 400
        cot, _ = app._resolve_cot("off", self.MELODY_ONLY)
        self.assertEqual(cot, "melody")
        cot2, _ = app._resolve_cot("off", self.WITH_JAZZ_CHORDS)
        self.assertEqual(cot2, "full")

    def test_empty_score_leaves_user_choice(self):
        for mode in ("full", "melody", "off"):
            self.assertEqual(app._resolve_cot(mode, "  "), (mode, ""))


class TestQueueMigration(Sandbox):
    def test_legacy_items_get_a_queue_id(self):  # D6/B8：58 条无 qid 曾塌成一个空桶
        app._BATCH_STATE.parent.mkdir(parents=True, exist_ok=True)
        legacy = {"name": "寻兰", "running": False, "items": [
            {"id": f"202609{i:02d}_120000_0000000{i}", "name": "寻兰", "status": "done"}
            for i in range(58)]}
        Path(app._BATCH_STATE).write_text(json.dumps(legacy), encoding="utf-8")
        st = app._batch_read()
        self.assertTrue(all(it.get("qid") for it in st["items"]))
        self.assertEqual(len({it["qid"] for it in st["items"]}), 1)
        self.assertTrue(st["items"][0]["qid"].startswith("legacy:"))

    def test_missing_state_file_reads_as_empty(self):
        self.assertEqual(app._batch_read()["items"], [])

    def test_write_is_atomic_and_leaves_no_temp(self):  # 蓝军 N-2：写一半被杀曾清空整个队列
        app._batch_write({"name": "q", "running": False, "current": None,
                          "items": [{"id": "20260101_000000_abcdabcd", "qid": "q1",
                                     "status": "pending"}]})
        state = app._batch_read()
        self.assertEqual(state["items"][0]["id"], "20260101_000000_abcdabcd")
        leftovers = [p.name for p in Path(app._BATCH_STATE).parent.glob("*.tmp")]
        self.assertEqual(leftovers, [])

    def test_store_only_replaces_listed_ids(self):
        app._batch_write({"name": "q", "running": True, "current": None,
                          "items": [{"id": "20260101_000000_00000001", "qid": "q1",
                                     "status": "pending", "payload": {}}]})
        # 并发追加（另一条已经写进盘）
        with app._BATCH_LOCK:
            st = app._batch_read()
            st["items"].append({"id": "20260101_000000_00000002", "qid": "q1",
                                "status": "pending", "payload": {}})
            app._batch_write(st)
        # worker 侧的正确写法：锁内重读 → 只把自己那条翻成 running → 回写
        with app._BATCH_LOCK:
            st = app._batch_read()
            item = next(i for i in st["items"] if i["id"] == "20260101_000000_00000001")
            item["status"] = "running"
            st["current"] = item["id"]
            app._batch_write(st)
        # 若像旧代码那样把 worker 早先的过期快照整体写回，第二条就消失了
        after = app._batch_read()
        self.assertEqual(len(after["items"]), 2)
        self.assertEqual([i["status"] for i in after["items"]], ["running", "pending"])
        self.assertTrue(any(i["id"] == "20260101_000000_00000002" for i in after["items"]))

    def test_crash_loop_revive_is_bounded(self):
        """第三方第三节-3：条目自己是崩溃诱因时，自愈+看门狗可以无限复活它。"""
        from datetime import datetime
        item = {"id": "20260101_000000_00000009", "qid": "q1", "status": "running",
                "payload": {}, "revive_n": app._REVIVE_MAX,
                "revive_ts": datetime.now().isoformat(timespec="seconds")}
        app._batch_write({"name": "q", "running": True, "current": item["id"],
                          "items": [item]})
        started = []
        saved = app._batch_ensure_worker
        app._batch_ensure_worker = lambda: started.append(1)
        try:
            app.batch_status()
        finally:
            app._batch_ensure_worker = saved
        st = app._batch_read()
        self.assertEqual(st["items"][0]["status"], "error")
        self.assertIn("崩溃诱因", st["items"][0]["error"])
        self.assertFalse(st["running"])
        self.assertEqual(started, [], "判死的条目不能再拉起 worker")

    def test_normal_restart_still_resumes(self):
        """同一判据的反面：隔得够久的两次中断是用户正常重启，不该被误杀。"""
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(seconds=app._REVIVE_WINDOW_SEC + 60))
        item = {"id": "20260101_000000_00000010", "qid": "q1", "status": "running",
                "payload": {}, "revive_n": app._REVIVE_MAX,
                "revive_ts": old.isoformat(timespec="seconds")}
        app._batch_write({"name": "q", "running": True, "current": item["id"],
                          "items": [item]})
        started = []
        saved = app._batch_ensure_worker
        app._batch_ensure_worker = lambda: started.append(1)
        try:
            app.batch_status()
        finally:
            app._batch_ensure_worker = saved
        st = app._batch_read()
        self.assertEqual(st["items"][0]["status"], "pending")
        self.assertEqual(st["items"][0]["revive_n"], 1, "超出窗口的旧计数要清零")
        self.assertEqual(started, [1], "正常中断要照常续跑")


class TestSweepHonesty(Sandbox):
    """冒烟实测（09-26 22:17）：用户点"停止"后，正在算的那一首十秒内就被轮询写成
    "服务重启，任务中断"，而网关 PID 从头到尾没变过——这句指控没有证据。
    状态清扫必须说得出现场能核实的话。"""

    class _Worker:
        def __init__(self, alive):
            self._alive = alive

        def is_alive(self):
            return self._alive

    def _run(self, item, *, worker_alive, current=None):
        from datetime import datetime
        item.setdefault("qid", "q1")
        item.setdefault("status", "running")
        app._batch_write({"name": "q", "running": False, "current": current,
                          "items": [item]})
        saved = app._BATCH_WORKER
        app._BATCH_WORKER = self._Worker(worker_alive)
        try:
            app.batch_status()
        finally:
            app._BATCH_WORKER = saved
        return app._batch_read()["items"][0]

    def test_live_worker_current_item_is_left_alone(self):
        it = self._run({"id": "20260101_000000_000000a1",
                        "started_ts": "2020-01-01T00:00:00"},
                       worker_alive=True, current="20260101_000000_000000a1")
        self.assertEqual(it["status"], "running", "线程还在算的这一条不能被人代写结局")

    def test_only_pre_start_residue_may_say_service_restarted(self):
        it = self._run({"id": "20260101_000000_000000a2",
                        "started_ts": "2020-01-01T00:00:00"}, worker_alive=False)
        self.assertEqual(it["status"], "error")
        self.assertIn("服务重启", it["error"])

    def test_in_process_death_says_worker_did_not_report(self):
        from datetime import datetime
        after = datetime.fromtimestamp(app._PROC_START_TS + 30).isoformat(timespec="seconds")
        it = self._run({"id": "20260101_000000_000000a3", "started_ts": after},
                       worker_alive=False)
        self.assertNotIn("服务重启", it["error"], "本进程内起的线程，进程没重启过")
        it2 = self._run({"id": "20260101_000000_000000a4"}, worker_alive=False)
        self.assertNotIn("服务重启", it2["error"], "缺 started_ts 的旧条目不能凭猜定罪")


class TestLocalGuard(Sandbox):
    """蓝军 S3：CORS 挡不住跨站简单请求，守卫中间件补 Host + Origin 两道。"""

    def setUp(self):
        super().setUp()
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def test_loopback_host_parsing(self):
        for ok in ("127.0.0.1:7863", "localhost", "LOCALHOST:3081", "::1"):
            self.assertTrue(app._host_is_loop(ok), ok)
        for bad in ("evil.example", "evil.example:7863", "192.168.1.7:7863", ""):
            self.assertFalse(app._host_is_loop(bad), bad)

    def test_origin_parsing(self):
        self.assertTrue(app._origin_is_loop("http://127.0.0.1:3081"))
        for bad in ("http://evil.example", "null", "http://evil.example/@127.0.0.1"):
            self.assertFalse(app._origin_is_loop(bad), bad)

    def test_dns_rebinding_host_is_rejected(self):
        r = self.client.get("/api/batch/status", headers={"host": "evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_cross_site_post_is_rejected_before_handler(self):
        # /api/batch/status 只接受 GET：命中守卫返回 403，越过守卫才是 405
        evil = {"origin": "http://evil.example"}
        self.assertEqual(self.client.post("/api/batch/status", headers=evil).status_code, 403)
        self.assertEqual(self.client.post("/api/batch/status", headers={"referer": "http://evil.example/x"}).status_code, 403)
        self.assertEqual(self.client.post("/api/batch/status", headers=evil).status_code, 403)

    def test_local_calls_still_work(self):  # 不能把自家前端与 curl 挡在门外
        self.assertEqual(self.client.get("/api/batch/status").status_code, 200)
        self.assertEqual(self.client.post("/api/batch/status",
                                          headers={"origin": LOCAL_BASE}).status_code, 405)

    def test_security_headers_on_every_response(self):  # 蓝军 Y4
        h = self.client.get("/api/batch/status").headers
        self.assertEqual(h.get("x-content-type-options"), "nosniff")
        self.assertEqual(h.get("referrer-policy"), "no-referrer")
        csp = h.get("content-security-policy") or ""
        self.assertIn("frame-ancestors", csp)                       # dsh(3081) 要内嵌，不能用 X-Frame-Options
        self.assertIn(f"127.0.0.1:{app.settings.app_port}", csp)
        self.assertNotIn("*", csp)                                  # 但绝不能放行任意来源

    def test_lan_mode_allows_only_whitelisted_host_and_port(self):  # 蓝军 N-3：放宽不等于不设防
        lan_base = "http://192.168.1.7:7863"
        client = TestClient(app.app, base_url=lan_base)
        saved = (app.LAN_MODE, app.LAN_HOSTS)
        app.LAN_MODE, app.LAN_HOSTS = True, {"192.168.1.7"}
        try:
            # 白名单内的主机名放行，否则局域网用户自己也被挡在门外
            self.assertEqual(client.get("/api/batch/status").status_code, 200)
            self.assertEqual(client.post("/api/batch/status",
                                         headers={"origin": lan_base}).status_code, 405)
            self.assertEqual(client.post("/api/batch/status",
                                         headers={"origin": "http://evil.example"}).status_code, 403)
            # 同主机名、别的端口（同机上另一个 node 服务/路由器后台）不算同源
            self.assertEqual(client.post("/api/batch/status",
                                         headers={"origin": "http://192.168.1.7:3999"}).status_code, 403)
            # DNS 重绑定：Host 与 Origin 两个字符串都由攻击者域名决定、天然相等，
            # 只要 evil.example 不在白名单里就必须拒——这正是旧实现漏掉的那一支
            evil = TestClient(app.app, base_url="http://evil.example:7863")
            self.assertEqual(evil.get("/api/batch/status").status_code, 403)
            self.assertEqual(evil.post("/api/batch/status",
                                       headers={"origin": "http://evil.example:7863"}).status_code, 403)
        finally:
            app.LAN_MODE, app.LAN_HOSTS = saved

    def test_lan_mode_works_through_dsh_proxy(self):  # 评审 P0：真实链路 Host 被代理改写
        """上一用例把 base_url 设成 192.168.1.7:7863 是"直连网关"的假路径。
        真实路径是 dsh(ui-panel.mjs proxy())强制 host:127.0.0.1:<gw> 转发、
        原样带浏览器 Origin(:3081)。这里按代理真实发出的请求头复现。"""
        client = TestClient(app.app, base_url="http://192.168.1.7:7863")
        saved = (app.LAN_MODE, app.LAN_HOSTS)
        app.LAN_MODE, app.LAN_HOSTS = True, {"192.168.1.7"}
        proxy_host = {"host": f"127.0.0.1:{app.settings.app_port}"}
        ui_origin = "http://192.168.1.7:3081"
        try:
            # 代理内部跳变：回环 Host 不能再按白名单拒，否则整个网关经代理不可达
            self.assertEqual(client.get("/api/batch/status", headers=proxy_host).status_code, 200)
            # UI 挂在 dsh 端口，写请求 Origin 是 :3081 —— 必须过守卫（405=到了路由层）
            self.assertEqual(client.post("/api/batch/status",
                                         headers={**proxy_host, "origin": ui_origin}).status_code, 405)
            # 放行回环 Host ≠ 不设防：借道代理、Origin 不在白名单仍要被拒
            self.assertEqual(client.post("/api/batch/status",
                                         headers={**proxy_host, "origin": "http://evil.example:3081"}).status_code, 403)
            # 借道代理也换不来别的端口上的服务：同源判定仍是"白名单主机+白名单端口"
            self.assertEqual(client.post("/api/batch/status",
                                         headers={**proxy_host, "origin": "http://192.168.1.7:3999"}).status_code, 403)
        finally:
            app.LAN_MODE, app.LAN_HOSTS = saved


class TestLanBind(unittest.TestCase):
    """蓝军 Y1：把 app_host 改成 0.0.0.0 却"以为开放了"（实际 Host 检查全 403）比启动即失败更难查。"""

    def setUp(self):
        import os
        self.saved = {k: os.environ.get(k) for k in ("YUE2_ALLOW_LAN", "YUE2_LAN_HOSTS")}
        os.environ.pop("YUE2_ALLOW_LAN", None)
        os.environ.pop("YUE2_LAN_HOSTS", None)

    def tearDown(self):
        import os
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_loopback_bind_is_not_lan_mode(self):
        for ok in ("127.0.0.1", "localhost", "::1"):
            self.assertFalse(app._resolve_lan_mode(ok), ok)

    def test_open_bind_refuses_startup_without_opt_out(self):
        with self.assertRaises(RuntimeError) as cm:
            app._resolve_lan_mode("0.0.0.0")
        self.assertIn("YUE2_ALLOW_LAN", str(cm.exception))

    def test_opt_out_still_needs_a_hostname_whitelist(self):  # 蓝军 N-3：通配绑定必须点名
        import os
        os.environ["YUE2_ALLOW_LAN"] = "1"
        with self.assertRaises(RuntimeError) as cm:
            app._resolve_lan_mode("0.0.0.0")
        self.assertIn("YUE2_LAN_HOSTS", str(cm.exception))
        os.environ["YUE2_LAN_HOSTS"] = "192.168.1.7, music.local"
        self.assertTrue(app._resolve_lan_mode("0.0.0.0"))
        self.assertEqual(app._lan_host_allowlist("0.0.0.0"), {"192.168.1.7", "music.local"})

    def test_concrete_bind_falls_back_to_itself(self):
        import os
        os.environ["YUE2_ALLOW_LAN"] = "1"
        self.assertTrue(app._resolve_lan_mode("192.168.1.7"))
        self.assertEqual(app._lan_host_allowlist("192.168.1.7"), {"192.168.1.7"})


class TestModelSwitchPath(Sandbox):
    """蓝军 S11：校验用的变量与写进 server.json 的变量必须是同一个。"""

    def setUp(self):
        super().setUp()
        self.model_dir = self.tmp / "model"
        self.model_dir.mkdir()
        (self.model_dir / "yue2-q4.gguf").write_bytes(b"G")
        (self.model_dir / "yue2-vae-f16.gguf").write_bytes(b"V")
        self.saved = {}
        for name, value in (("MODEL_DIR", self.model_dir),
                            ("_read_server_json", lambda: {"models": [{}]}),
                            ("_restart_audiocpp", lambda *a, **k: None)):
            self.saved[name] = getattr(app, name)
            setattr(app, name, value)
        self.written = {}
        self.saved["_write_server_json"] = app._write_server_json
        app._write_server_json = lambda cfg: self.written.update(cfg)

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(app, name, value)
        super().tearDown()

    def test_traversal_is_rejected_before_any_write(self):
        for bad in ("../../Windows/win.ini", "model/../../../etc/passwd", "sub/dir/x.gguf"):
            with self.assertRaises(app.HTTPException, msg=bad):
                app.models_switch({"path": bad})
        self.assertEqual(self.written, {})          # 拒绝必须发生在落盘之前

    def test_written_path_is_the_validated_filename(self):
        app.models_switch({"path": "model/yue2-q4.gguf"})   # 前端回传的带前缀写法
        self.assertEqual(self.written["models"][0]["path"], "model/yue2-q4.gguf")  # 不再叠成 model/model/
        self.assertEqual(self.written["models"][0]["session_options"]["yue2.model_gguf"], "yue2-q4.gguf")


class TestRvcCheckCache(Sandbox):
    """蓝军 Y5：体检挂在 GET 上，却要另起子进程 torch.load 整个 pth（最长 300 秒）。"""

    def test_repeated_check_reuses_cache_until_file_changes(self):
        from unittest import mock
        models = self.tmp / "models"
        models.mkdir(parents=True)
        pth = models / "voice.pth"
        pth.write_bytes(b"x")
        calls = []

        def fake_run(cmd, *a, **kw):
            calls.append(cmd)
            return types.SimpleNamespace(
                returncode=0, stdout=json.dumps(
                    {"sr": "40000", "f0": True, "version": "v2", "epoch": "10",
                     "struct": True, "n": 99}).encode(), stderr=b"")

        fake_sub = types.SimpleNamespace(run=fake_run, CREATE_NO_WINDOW=0)
        with mock.patch.object(app, "RVC_MODELS_DIR", models), \
             mock.patch.object(app, "RVC_DIR", self.tmp), \
             mock.patch.object(app, "RVC_PY", "python"), \
             mock.patch.object(app, "subprocess", fake_sub):
            first = app.rvc_model_check("voice.pth")
            second = app.rvc_model_check("voice.pth")
            self.assertEqual(len(calls), 1, "同一个文件不该被反复 torch.load")
            self.assertFalse(first.get("cached"))
            self.assertTrue(second.get("cached"))
            self.assertEqual(second["summary"], first["summary"])
            # 重训覆盖了文件 → 指纹变了，缓存必须自动失效，不能拿旧结论糊弄
            pth.write_bytes(b"xy")
            app.rvc_model_check("voice.pth")
            self.assertEqual(len(calls), 2)
            # ?force=1 用于明知有问题时强刷
            app.rvc_model_check("voice.pth", force=True)
            self.assertEqual(len(calls), 3)

    def test_broken_model_is_not_cached(self):  # 一次性 OOM/超时不该永久钉死结论
        from unittest import mock
        models = self.tmp / "models"
        models.mkdir(parents=True)
        (models / "bad.pth").write_bytes(b"x")
        fake_sub = types.SimpleNamespace(
            run=lambda *a, **kw: types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom"),
            CREATE_NO_WINDOW=0)
        with mock.patch.object(app, "RVC_MODELS_DIR", models), \
             mock.patch.object(app, "RVC_DIR", self.tmp), \
             mock.patch.object(app, "RVC_PY", "python"), \
             mock.patch.object(app, "subprocess", fake_sub):
            for _ in range(2):
                with self.assertRaises(app.HTTPException) as cm:
                    app.rvc_model_check("bad.pth")
                self.assertEqual(cm.exception.status_code, 422)


class TestDeleteEndpoints(Sandbox):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def test_generate_delete_clears_output_and_rvc_workspace(self):  # D13 + S7 端到端
        rid = "20260101_000000_cccccccc"
        for ext in app._OUTPUT_EXTS:
            (app.OUTPUT_DIR / f"{rid}{ext}").write_text("x", encoding="utf-8")
        job = app.RVC_JOB_DIR / rid
        job.mkdir()
        (job / "src.wav").write_text("x", encoding="utf-8")
        other = app.OUTPUT_DIR / "20260101_000001_dddddddd.wav"
        other.write_text("x", encoding="utf-8")
        r = self.client.delete(f"/api/generate/{rid}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(any(app.OUTPUT_DIR.glob(f"{rid}.*")))
        self.assertFalse(job.exists())
        self.assertTrue(other.is_file())

    def test_history_delete_clears_record_and_artifacts(self):
        rid = "20260101_000000_eeeeeeee"
        app.HIST_DIR.mkdir(parents=True, exist_ok=True)
        (app.HIST_DIR / f"{rid}.wav").write_text("x", encoding="utf-8")
        (app.OUTPUT_DIR / f"{rid}.lrc").write_text("x", encoding="utf-8")
        Path(app.HIST_JSON).write_text(
            json.dumps([{"id": rid, "file": f"{rid}.wav"}]), encoding="utf-8")
        r = self.client.delete(f"/api/history/{rid}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse((app.HIST_DIR / f"{rid}.wav").exists())
        self.assertFalse((app.OUTPUT_DIR / f"{rid}.lrc").exists())
        self.assertEqual(app._hist_read(), [])

    def test_running_generate_cannot_be_deleted(self):  # 删了在跑的任务=丢产物
        app._gen_set_job({"id": "20260101_000000_aaaaaaaa", "status": "running"})
        try:
            r = self.client.delete("/api/generate/20260101_000000_aaaaaaaa")
            self.assertEqual(r.status_code, 409)
        finally:
            app._gen_set_job(None)


class TestGsvChainConfig(Sandbox):
    def test_private_default_path_is_env_overridable(self):  # D2：不再只能跑在作者机器上
        import os
        self.assertTrue(hasattr(app, "GSV_ROOT"))
        saved = os.environ.get("YUE2_GSV_ROOT")
        os.environ["YUE2_GSV_ROOT"] = r"D:\Elsewhere\GPT-SoVITS"
        try:
            # 真调一次解析函数（旧用例只 grep 了源码里的变量名，不算验证行为）
            self.assertEqual(str(app._gsv_root()), r"D:\Elsewhere\GPT-SoVITS")
        finally:
            if saved is None:
                os.environ.pop("YUE2_GSV_ROOT", None)
            else:
                os.environ["YUE2_GSV_ROOT"] = saved
        # 没配 env 时回落到仓库内默认值：路径可以不存在，但必须是绝对路径
        os.environ.pop("YUE2_GSV_ROOT", None)
        self.assertTrue(app._gsv_root().is_absolute())

    def test_chain_probe_is_callable_and_boolean(self):
        self.assertIn(app._gsv_chain_available(), (True, False))


class TestRvcGateAndMix(Sandbox):
    """换声三投诉的锁定用例：①没人声段的底噪 ②成品丢了立体声 ③去和声开关要真存在。"""

    SR = 8000

    def _tone(self, n, amp=0.3, f=279.0):
        t = np.arange(n) / self.SR
        return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)[:, None]

    def test_gate_mutes_hum_over_silent_reference(self):  # 症状①：模型在静音处自己哼音
        ref = np.zeros((self.SR * 3, 2), dtype=np.float32)     # 送 RVC 的人声 = 静音
        voc = self._tone(self.SR * 3)                          # RVC 却输出了恒定蜂鸣
        out, info = app._apply_silence_gate(voc, ref, self.SR)
        rms = float(np.sqrt((out.astype(np.float64) ** 2).mean()))
        self.assertLess(20 * np.log10(max(rms, 1e-12)), -55.0, "静音段没被压下去")
        self.assertGreaterEqual(info["gate_closed_ratio"], 0.9)

    def test_gate_keeps_vocal_untouched_over_active_reference(self):  # 不能误切人声
        voc = self._tone(self.SR * 3)
        ref = np.repeat(self._tone(self.SR * 3, amp=0.4), 2, axis=1)
        out, info = app._apply_silence_gate(voc, ref, self.SR)
        self.assertAlmostEqual(float(np.sqrt((out ** 2).mean())),
                               float(np.sqrt((voc ** 2).mean())), delta=1e-4)
        self.assertEqual(info["gate_closed_ratio"], 0.0)
        self.assertTrue(info["gate_open_frames"].all())

    def test_gate_reference_length_mismatch_is_rescaled(self):  # 换声输出采样数与输入不同
        out, info = app._apply_silence_gate(self._tone(self.SR * 2),
                                            np.zeros((self.SR * 3, 1), dtype=np.float32),
                                            self.SR)
        self.assertEqual(out.shape[0], self.SR * 2)

    def test_mix_never_collapses_stereo_accompaniment(self):  # 症状②：成品曾被压成单声道
        voc = self._tone(self.SR * 2)                          # 单声道人声
        acc = np.stack([self._tone(self.SR * 2, 0.2)[:, 0],
                        self._tone(self.SR * 2, 0.2, 281.0)[:, 0]], axis=1)  # 立体声伴奏
        mixed = app._mix_vocal_accompaniment(voc, acc)
        self.assertEqual(mixed.shape[1], 2, "伴奏声道数不能被压掉")
        self.assertFalse(np.allclose(mixed[:, 0], mixed[:, 1]), "两声道必须不同")

    def test_mix_pads_shorter_side(self):
        mixed = app._mix_vocal_accompaniment(self._tone(self.SR), self._tone(self.SR * 2))
        self.assertEqual(mixed.shape[0], self.SR * 2)

    def test_mask_to_signal_covers_request_length(self):
        m = app._mask_to_signal(np.array([True, False, True]), 9)
        self.assertEqual(m.shape, (9,))
        self.assertEqual(app._mask_to_signal(None, 4).all(), True)

    def test_convert_worker_exposes_gate_and_strip_harmony(self):  # 症状③开关真的接进了流水线
        import inspect
        worker = inspect.signature(app._rvc_convert_worker)
        for name in ("separate_vocal", "gate", "strip_harmony",
                     "filter_radius", "resample_sr"):
            self.assertIn(name, worker.parameters)
        sep_sig = inspect.signature(app._run_vocal_separation)
        self.assertIn("strip_harmony", sep_sig.parameters)
        # 端点的默认值不是裸值而是 Form 实例（app.py 开了 future annotations）
        endpoint = inspect.signature(app.rvc_convert).parameters
        self.assertIn("gate", endpoint)
        self.assertIn("strip_harmony", endpoint)
        self.assertEqual(endpoint["gate"].default.default, "on")
        self.assertEqual(endpoint["filter_radius"].default.default, 3)
        self.assertEqual(endpoint["resample_sr"].default.default, 0)

    def test_gate_envelope_matches_reference_implementation(self):  # 评审 F1：分块≠改结果
        """分块重构后的阈值必须与旧的全展开 float64 参考实现一致。"""
        rng = np.random.default_rng(7)
        ref = np.zeros(self.SR * 3, dtype=np.float32)
        ref[self.SR:self.SR * 2] = rng.standard_normal(self.SR).astype(np.float32) * 0.3
        gain, thr, open_ = app._gate_envelope(ref, self.SR)
        # 参考实现：当年那份 np.stack 全展开（就是被 F1 点名的写法）
        n, hop = int(self.SR * 0.02), int(self.SR * 0.01)
        starts = np.arange(0, max(1, len(ref) - n + 1), hop)
        frames = np.stack([ref[s:s + n] for s in starts]).astype(np.float64)
        db = 20 * np.log10(np.clip(np.sqrt((frames ** 2).mean(axis=1)), 1e-12, None))
        thr_ref = float(np.clip(np.percentile(db, 90) - 38.0, -75.0, -45.0))
        self.assertAlmostEqual(thr, thr_ref, places=3)
        self.assertEqual(len(gain), len(ref))
        self.assertEqual(gain.dtype, np.float32)

    def test_gate_peak_memory_does_not_scale_with_length(self):  # 评审 F1：1.06GB→分块水位
        """600s / 44.1kHz 输入下，_gate_envelope 期间的进程峰值内存增量必须远小于
        旧全展开实现（评审实测旧版仅帧矩阵就 +1061MB；新版只随输出线性 +~110MB）。
        判据取 300MB：旧代码必红、新代码有余量。"""
        if sys.platform != "win32":
            self.skipTest("Windows 专属：ctypes 读 ProcessMemoryCounters")
        import ctypes
        import ctypes.wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        k32 = ctypes.windll.kernel32
        try:                                   # Win10 起该函数在 psapi.dll，个别构建挂在 kernel32
            fn = ctypes.windll.psapi.GetProcessMemoryInfo
        except AttributeError:
            fn = k32.GetProcessMemoryInfo
        # 必须显式声明签名：默认 int 返回会把 -1 伪句柄截断成无效值
        k32.GetCurrentProcess.restype = wt.HANDLE
        fn.restype = wt.BOOL
        fn.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
        buf = PMC()
        buf.cb = ctypes.sizeof(PMC)

        def peak_mb():
            assert fn(k32.GetCurrentProcess(), ctypes.byref(buf), buf.cb), "GetProcessMemoryInfo failed"
            return buf.PeakWorkingSetSize / 1e6

        sr = 44100
        ref = np.zeros(sr * 600, dtype=np.float32)          # 零页不占物理内存
        ref[sr * 100:sr * 200] = 0.2                         # 一段"人声"
        p0 = peak_mb()
        gain, thr, open_ = app._gate_envelope(ref, sr)
        p1 = peak_mb()
        del gain, ref
        self.assertLess(p1 - p0, 300.0,
                        f"_gate_envelope 峰值内存增量 {p1 - p0:.0f}MB，旧全展开是 ~1061MB（评审 F1）")


class TestStorageCaps(Sandbox):
    """蓝军 N-11：存储入口必须和生成入口同一套口径——原样存下，或者报错。

    以前 POST /history 与 POST /templates 各自砍到 600/4000 字符，比 _MAX_LYRICS
    (_MAX_ABC=20000) 还短：模板回填出来的谱比原稿少一截且不留痕迹。
    """

    def setUp(self):
        super().setUp()
        self._saved_tpl = app._TEMPLATES_FILE
        app._TEMPLATES_FILE = self.tmp / "templates.json"
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def tearDown(self):
        app._TEMPLATES_FILE = self._saved_tpl
        super().tearDown()

    def test_template_keeps_long_abc_intact(self):
        abc = "X:1\nK:G\n" + ('|: "C9" C,2 "Fm9" D2 :|\n' * 300)   # > 4000 字符
        self.assertGreater(len(abc), 4000)
        r = self.client.post("/api/templates", json={"name": "长谱模板", "abc": abc})
        self.assertEqual(r.status_code, 200, r.text)
        stored = json.loads(app._TEMPLATES_FILE.read_text(encoding="utf-8"))
        self.assertEqual(stored[0]["abc"], abc)

    def test_template_rejects_oversize_instead_of_chopping(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            app.save_template({"name": "超限", "abc": "a" * (app._MAX_ABC + 1)})
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("上限", str(ctx.exception.detail))

    def test_history_rejects_oversize_lyrics_and_leaves_no_orphan(self):
        # 校验发生在落盘之前：判错的这次上传不能在 HIST_DIR 里留下没人认领的 wav
        files = {"audio": ("a.wav", b"RIFF" + b"\0" * 1024, "audio/wav")}
        data = {"meta": json.dumps({"lyrics": "啊" * (app._MAX_LYRICS + 1)})}
        r = self.client.post("/api/history", files=files, data=data)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(list(Path(app.HIST_DIR).glob("*")), [])

    def test_history_keeps_full_lyrics_within_cap(self):
        lyrics = "啊" * 5000
        files = {"audio": ("a.wav", b"RIFF" + b"\0" * 1024, "audio/wav")}
        r = self.client.post("/api/history",
                             files=files,
                             data={"meta": json.dumps({"lyrics": lyrics})})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["item"]["lyrics"], lyrics)


class TestUploadBudget(Sandbox):
    """蓝军 N-7：逐文件 200MB 挡不住"一次传一沓"，要再看总量与磁盘剩余。"""

    class _Up:
        def __init__(self, data: bytes):
            self._data, self._sent = data, False

        async def read(self, n: int) -> bytes:
            if self._sent:
                return b""
            self._sent = True
            return self._data

    def _run(self, dest_name: str, limit: int, budget=None):
        import asyncio
        dest = Path(app.RVC_TRAIN_DIR) / dest_name
        return asyncio.run(app._stream_upload_to(
            self._Up(b"x" * 4096), dest, limit, "样本", budget=budget))

    def test_budget_accumulates_across_files(self):
        budget = {"used": 0}
        for i in range(3):
            self._run(f"s{i}.wav", 200 * 1024 * 1024, budget)
        self.assertEqual(budget["used"], 3 * 4096)

    def test_budget_cap_rejects_next_file(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            self._run("over.wav", 200 * 1024 * 1024,
                      {"used": app._MAX_UPLOAD_TOTAL})
        self.assertEqual(ctx.exception.status_code, 413)

    def test_low_disk_refuses_before_writing(self):
        from fastapi import HTTPException
        saved = app._free_bytes
        app._free_bytes = lambda p: 0
        try:
            with self.assertRaises(HTTPException) as ctx:
                self._run("nodisk.wav", 200 * 1024 * 1024, {"used": 0})
            self.assertEqual(ctx.exception.status_code, 507)
        finally:
            app._free_bytes = saved
        self.assertFalse((Path(app.RVC_TRAIN_DIR) / "nodisk.wav").exists())


class TestChordProseGuard(Sandbox):
    """蓝军 N-8：宽松式把引号住的普通英文单词当和弦，会把纯旋律谱推去 full。"""

    def test_prose_in_quotes_is_not_a_chord(self):
        score = 'X:1\nT:"Chorus"\nK:G\n% "Chorus" 标注\n|: d2 B2 | "Dog" e2 :|\n'
        self.assertFalse(app._abc_has_chords(score))

    def test_real_symbols_still_detected(self):
        for sym in ('"C"', '"Am7"', '"C9"', '"Fm9"', '"G13"', '"Csus"', '"Cmaj9"',
                    '"A7alt"', '"Cadd9"', '"Bbm"', '"F#m7b5"', '"G/B"', '"D7/F#"'):
            self.assertTrue(app._abc_has_chords(f'X:1\nK:G\n|: {sym} C,2 D,2 :|\n'), sym)

    def test_abc_direction_marker_is_not_a_chord(self):
        self.assertFalse(app._abc_has_chords('X:1\nK:G\n|: C,2 D,2 :| "D.C."\n'))


class TestWatchdogEarlyDeath(unittest.TestCase):
    """评审 P1-3：配置错（开了 YUE2_ALLOW_LAN 却没给 hosts）让 `python -s app.py` 在
    绑定端口之前就抛 RuntimeError。看门狗常规路径要 ~140s 才转一圈，会把日志刷满
    却永远救不活——"启动即死"和"跑一阵后假死"是两种病，必须分开处理。"""

    import watchdog as _wd

    def setUp(self):
        self.wd = self._wd
        self.msgs = []
        self._saved_log = self.wd.log
        self.wd.log = lambda m: self.msgs.append(m)

    def tearDown(self):
        self.wd.log = self._saved_log

    class _Proc:
        def __init__(self, rc):
            self._rc = rc

        def poll(self):
            return self._rc

    def _new(self, track=True):
        spawns = []
        svc = {"name": "网关 :7863", "track": track, "proc": None, "proc_start": 0.0,
               "early_deaths": 0, "given_up": False,
               "respawn": lambda: (spawns.append(1), self._Proc(None))[1]}
        svc["_spawns"] = spawns
        return svc

    def test_repeated_early_death_trips_the_breaker(self):
        svc = self._new()
        for _ in range(self.wd.EARLY_DEATH_LIMIT):
            self.assertTrue(self.wd._respawn(svc))
            svc["proc"] = self._Proc(1)                    # 秒退
            svc["proc_start"] = time.time() - 5            # 窗口内
            self.wd._reap_early_death(svc)
        self.assertTrue(svc["given_up"])
        spawned = len(svc["_spawns"])
        self.assertFalse(self.wd._respawn(svc))
        self.assertEqual(svc["_spawns"], [1] * spawned, "熔断后不能再拉起一次")
        self.assertTrue(any("不再自动重启" in m for m in self.msgs))

    def test_long_lived_child_is_not_an_early_death(self):
        """跑满窗口后才死的是"假死"，归连败→深探→击杀管，不该被熔断计数吃掉。"""
        svc = self._new()
        self.wd._respawn(svc)
        svc["proc"] = self._Proc(1)
        svc["proc_start"] = time.time() - self.wd.EARLY_DEATH_WINDOW - 1
        self.wd._reap_early_death(svc)
        self.assertEqual(svc["early_deaths"], 0)
        self.assertFalse(svc["given_up"])

    def test_dsh_via_bat_is_not_counted(self):
        """bat 路径里 cmd 转完参数就退出，其退出时间不代表服务死了——不能计入。"""
        svc = self._new(track=False)
        self.wd._respawn(svc)
        svc["proc"] = self._Proc(0)
        svc["proc_start"] = time.time()
        self.wd._reap_early_death(svc)
        self.assertEqual(svc["early_deaths"], 0)
        self.assertFalse(svc["given_up"])

    def test_respawn_reports_truth(self):
        """自愈文案不许说谎（fb24917 同一条原则）：只有真拉起了才返回 True。"""
        svc = self._new()
        self.assertTrue(self.wd._respawn(svc))
        self.assertEqual(len(svc["_spawns"]), 1)
        svc["given_up"] = True
        self.assertFalse(self.wd._respawn(svc))
        self.assertEqual(len(svc["_spawns"]), 1)


class TestRvcSerialQueue(Sandbox):
    """投诉④：连点「换声」不能并行。一次只跑一个，后面的排队、看得见、没开跑的能取消。

    全部用例都把真正的 worker 换成假任务（不碰 GPU/不落盘），所以可以放心在跑歌时执行。
    """

    def setUp(self) -> None:
        super().setUp()
        self._saved_rvc = (app._rvc_convert_worker, dict(app._RVC_JOBS),
                       list(app._RVC_QUEUE), app._RVC_WORKER, app._RVC_CURRENT[0])
        self.calls: list[str] = []          # 假任务的实际执行顺序
        self.live = 0                       # 当前同时在跑的几个
        self.peak = 0                       # 观测到的并发峰值
        self._lock = threading.Lock()
        self.release = threading.Event()    # 放行当前这条假任务
        self.entered = threading.Event()    # 通知"第一条已经进 worker"

        def fake_worker(rid, job, *args, **kw):
            with app._RVC_LOCK:
                app._RVC_JOBS[rid] = {**app._RVC_JOBS.get(rid, {"id": rid}),
                                      "status": "running", "queue_pos": 0,
                                      "started_ts": "2026-01-01T00:00:00"}
            with self._lock:
                self.live += 1
                self.peak = max(self.peak, self.live)
                self.calls.append(rid)
            self.entered.set()
            self.release.wait(10)
            with self._lock:
                self.live -= 1
            with app._RVC_LOCK:
                app._RVC_JOBS[rid] = {**app._RVC_JOBS.get(rid, {}), "status": "done"}

        app._rvc_convert_worker = fake_worker
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def tearDown(self) -> None:
        self.release.set()                  # 别让还堵着的假任务把线程带走
        self._wait_until(lambda: not self._queue_len()
                         and (app._RVC_WORKER is None or not app._RVC_WORKER.is_alive()))
        worker, jobs, queue, _, current = self._saved_rvc
        app._rvc_convert_worker = worker
        app._RVC_QUEUE[:] = queue
        app._RVC_CURRENT[0] = current
        app._RVC_JOBS.clear()
        app._RVC_JOBS.update(jobs)
        app._RVC_WORKER = None              # 真实 worker 从未被起过（假任务不起新线程）
        super().tearDown()                  # 最后再收临时目录（前面还要用它的落盘路径）

    # ---- 小工具 ---- #
    def _queue_len(self) -> int:
        with app._RVC_QUEUE_LOCK:
            return len(app._RVC_QUEUE)

    def _wait_until(self, pred, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pred():
                return True
            time.sleep(0.02)
        return False

    def _register(self, rid: str, pos: int) -> None:
        with app._RVC_LOCK:
            app._RVC_JOBS[rid] = {"id": rid, "status": "pending", "queue_pos": pos,
                                  "model": "孙燕姿.pth", "src_duration": 30}

    def _status(self, rid: str) -> str:
        with app._RVC_LOCK:
            return (app._RVC_JOBS.get(rid) or {}).get("status", "missing")

    # ---- 用例 ---- #
    def test_submits_are_queued_fifo_and_never_concurrent(self):
        """三条一起提交：位次 1/2/3，同时最多一条在算，执行顺序=提交顺序。"""
        rids = [f"20260101_00000{i}_0000000{i}" for i in (1, 2, 3)]
        for i, rid in enumerate(rids, start=1):
            self._register(rid, i)
            self.assertEqual(app._rvc_submit((rid, app._RVC_JOBS[rid])), i)
        self.assertTrue(self.entered.wait(10), "队列线程没把第一条跑起来")
        # 第一条在跑的时候，后两条必须仍是 pending —— 并行就是这次投诉的根因
        self.assertTrue(self._wait_until(
            lambda: len(self.calls) == 1 and self.live == 1 and self._status(rids[1]) == "pending"
            and self._status(rids[2]) == "pending"))
        self.release.set()
        self.assertTrue(self._wait_until(lambda: len(self.calls) == 3), "后两条没轮到")
        self.assertEqual(self.calls, rids, "必须按提交顺序执行")
        self.assertEqual(self.peak, 1, f"换声并发峰值 {self.peak}，只允许 1")

    def test_queue_position_is_recomputed_live(self):
        """入队时写下的位次会过期：正在转换的那条占第 1 位，后面的依次前移。"""
        a, b, c = ("20260101_000001_aaaaaaaa", "20260101_000002_bbbbbbbb",
                   "20260101_000003_cccccccc")
        for i, rid in enumerate((a, b, c), start=1):
            self._register(rid, i)
            app._rvc_submit((rid, app._RVC_JOBS[rid]))
        self.assertTrue(self.entered.wait(10))
        with app._RVC_LOCK:
            lb = app._rvc_live(dict(app._RVC_JOBS[b]))
            lc = app._rvc_live(dict(app._RVC_JOBS[c]))
        self.assertEqual(lb["queue_pos"], 2, "a 正在转换 = 第 1 位，b 紧随其后")
        self.assertEqual(lc["queue_pos"], 3)
        self.release.set()

    def test_cancel_only_touches_tasks_that_have_not_started(self):
        """排队中的能取消；已经在转换的不给取消（推理进程不可半途终止），并如实回 409。"""
        a, b = "20260101_000001_dddddddd", "20260101_000002_eeeeeeee"
        for i, rid in enumerate((a, b), start=1):
            self._register(rid, i)
            for r in (a, b):                       # 两条都已落地上传的源文件
                (app.RVC_JOB_DIR / r).mkdir(parents=True, exist_ok=True)
                (app.RVC_JOB_DIR / r / "src.wav").write_bytes(b"RIFFxxxx")
            app._rvc_submit((rid, app._RVC_JOBS[rid]))
        self.assertTrue(self.entered.wait(10))
        busy = self.client.post(f"/api/rvc/cancel/{a}")
        self.assertEqual(busy.status_code, 409)
        self.assertIn("已在转换中", busy.json()["detail"])
        r = self.client.post(f"/api/rvc/cancel/{b}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["job"]["status"], "cancelled")
        self.assertEqual(self._status(b), "cancelled")
        self.assertFalse((app.RVC_JOB_DIR / b).exists(), "取消掉的任务那份上传必须回收，它永远轮不到执行")
        self.assertTrue((app.RVC_JOB_DIR / a).exists(), "正在转换的目录不能动")
        self.release.set()
        self._wait_until(lambda: not self._queue_len())
        self.assertEqual(self.calls, [a], "取消掉的条目永远不该被跑到")
        self.assertEqual(self.client.post("/api/rvc/cancel/20260101_090909_deadbeef").status_code, 404)

    def test_convert_endpoint_enqueues_instead_of_spawning_threads(self):
        """上传入口必须入队（起线程 = 并行的来源），回包如实给 pending + 位次。"""
        seen: list[tuple] = []
        saved = (app._rvc_submit, app._rvc_models)
        app._rvc_models = lambda: ["孙燕姿.pth"]
        # 检索强度默认 0.75（>0），提交闸门要求配套索引在位——这里给一份，
        # 专门测"缺索引"的用例见 TestRvcSubmitGate。
        (self.rvc_logs / "added_IVF348_Flat_nprobe_1_孙燕姿_v2.index").write_bytes(b"x")
        app._rvc_submit = lambda args: (seen.append(args), 4)[1]   # 不起线程，位次固定 4
        try:
            r = self.client.post("/api/rvc/convert",
                                 files={"file": ("demo.wav", b"RIFFxxxx", "audio/wav")},
                                 data={"model": "孙燕姿.pth", "task_name": "队列测试"})
        finally:
            app._rvc_submit, app._rvc_models = saved
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["position"], 4)
        self.assertEqual(body["job"]["status"], "pending", "不能再写死 running")
        self.assertEqual(body["job"]["queue_pos"], 4)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], body["id"], "入队的就是这条任务")
        self.assertEqual(app._RVC_JOBS[body["id"]]["queue_pos"], 4)

    def test_active_lists_include_queued_tasks(self):
        """页面刷新后要能还原整条队列；只列 running 的话排队条目会凭空消失。"""
        run, wait = "20260101_000003_aaaaaaaa", "20260101_000004_bbbbbbbb"
        self._register(run, 0)
        self._register(wait, 1)
        with app._RVC_LOCK:
            app._RVC_JOBS[run]["status"] = "running"
        ids = [j["id"] for j in self.client.get("/api/rvc/active").json()["items"]]
        self.assertEqual(ids, [run, wait], "正在跑的在前，排队的在后")
        act = self.client.get("/api/history/active").json()["items"]
        self.assertIn(wait, [a["id"] for a in act if a.get("model")])
        self.assertEqual(app._rvc_model_in_use("孙燕姿.pth"), True, "排队中的音色也算被占用")

    def test_delete_queued_rvc_dequeues_instead_of_resurrecting(self):  # 评审 F3
        """删掉排队中的换声任务：必须先把参数元组从队列摘掉。只删文件不摘队列，
        轮到它时 src 已没 → 子进程失败 → 任务"删了又复活"成 error。"""
        first, doomed = "20260101_000005_aaaaaaaa", "20260101_000006_bbbbbbbb"
        for i, rid in enumerate((first, doomed), start=1):
            self._register(rid, i)
            (app.RVC_JOB_DIR / rid).mkdir(parents=True, exist_ok=True)
            (app.RVC_JOB_DIR / rid / "src.wav").write_bytes(b"RIFFxxxx")
            app._rvc_submit((rid, app._RVC_JOBS[rid]))
        self.assertTrue(self.entered.wait(10))               # first 占住队列（假任务堵住）
        r = self.client.delete(f"/api/generate/{doomed}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._status(doomed), "cancelled", "删除后要落在 cancelled，不是复活成 error")
        self.assertFalse((app.RVC_JOB_DIR / doomed).exists(), "工作目录必须一起回收")
        self.release.set()
        self.assertTrue(self._wait_until(lambda: self._status(first) == "done"))
        self._wait_until(lambda: not self._queue_len())
        self.assertEqual(self._status(doomed), "cancelled", "前一条跑完后，被删的不得被跑起来")
        self.assertNotIn(doomed, self.calls, "被删除的条目永远不该被跑到")

    def test_delete_running_rvc_is_refused(self):  # 评审 F3 配套：正在转换的不许删
        rid = "20260101_000007_cccccccc"
        self._register(rid, 1)
        app._rvc_submit((rid, app._RVC_JOBS[rid]))
        self.assertTrue(self.entered.wait(10))
        r = self.client.delete(f"/api/generate/{rid}")
        self.assertEqual(r.status_code, 409)
        self.assertIn("转换", r.json()["detail"])
        self.assertTrue((app._RVC_JOBS[rid]["status"] == "running"))
        self.release.set()


class TestRvcWaitsForGpu(Sandbox):
    """评审 MEDIUM：出队 ≠ 开算。_GPU_SEM 还被生成/批量/训练占着时，
    换声条目必须老老实实待在 pending，running 与起表时刻只能写在拿到锁之后，
    否则等锁的几十分钟会被画成"转换进度"——正是这一轮要修的第二个谎。"""

    RID = "20260101_000006_gggggggg"

    def setUp(self) -> None:
        super().setUp()
        self._saved_rvc_gpu = (app._GPU_SEM, app.subprocess, app._win_toast)
        self.toasts: list[str] = []
        app._win_toast = lambda title, body: self.toasts.append(title)
        with app._RVC_LOCK:
            app._RVC_JOBS.pop(self.RID, None)

    def tearDown(self) -> None:
        app._GPU_SEM, app.subprocess, app._win_toast = self._saved_rvc_gpu
        with app._RVC_LOCK:
            app._RVC_JOBS.pop(self.RID, None)
        super().tearDown()

    def test_running_and_start_time_are_stamped_after_the_gpu_lock(self):
        in_dir = app.RVC_JOB_DIR / self.RID
        in_dir.mkdir(parents=True, exist_ok=True)
        job = {"id": self.RID, "status": "pending", "step": "排队中", "queue_pos": 1,
               "model": "孙燕姿.pth", "src_name": "x.wav", "src_duration": 30,
               "ts": "2026-01-01T00:00:00"}
        with app._RVC_LOCK:
            app._RVC_JOBS[self.RID] = job
        seen: dict = {}

        class _Failed:
            returncode = 1
            stdout = b""
            stderr = b"intentional failure"   # 故意失败：本例只验证登记时机

        def fake_run(cmd, **kw):
            # 真起推理进程的那一刻，任务必须已经登记为 running 且起了表
            with app._RVC_LOCK:
                seen.update(app._RVC_JOBS[self.RID])
            return _Failed()

        sem = threading.Semaphore(1)
        sem.acquire()                       # 假装一首歌正在生成，GPU 不给
        app._GPU_SEM = sem
        app.subprocess = types.SimpleNamespace(run=fake_run)
        th = threading.Thread(target=app._rvc_convert_worker,
                              args=(self.RID, job, in_dir / "src.wav", in_dir,
                                    "孙燕姿.pth", 0, "rmvpe", 0.75, 0.33, 0.25))
        th.start()
        try:
            deadline = time.time() + 10
            while time.time() < deadline and not app._RVC_JOBS[self.RID].get("step"):
                time.sleep(0.02)
            snap = dict(app._RVC_JOBS[self.RID])
            self.assertIn("等待本机空闲", snap.get("step", ""), "没写出在等什么，页面只能说谎")
            self.assertEqual(snap["status"], "pending", "没拿到 GPU 之前不许说在转换")
            self.assertNotIn("started_ts", snap, "起表只能等到 GPU 真到手")
            self.assertEqual(seen, {}, "等待期间不许起推理进程")
            sem.release()
            th.join(15)
            self.assertEqual(seen.get("status"), "running", "拿到锁的那一刻才登记在算")
            self.assertTrue(seen.get("started_ts"))
            self.assertEqual(app._RVC_JOBS[self.RID]["status"], "error")
            self.assertLess(app._RVC_JOBS[self.RID]["sec"], 10, "耗时不含等锁时间")
        finally:
            if sem._value == 0:
                sem.release()               # 断言中途失败时别把锁留在测试手里
            th.join(5)


class TestRvcIndexFiles(Sandbox):
    """P0：配套索引的查找必须与推理侧同一套规则，且覆盖 assets/indices 与 logs 两处。

    两条踩过的坑：①网关只 glob logs/ 下的 added_*_名字_v2.index，自己练出来的音色
    （外链名前缀是音色名，train_index.py:52-76）一律被体检报成"缺索引"；
    ②删音色时另一处的同名索引没人清，留在盘上成孤儿。"""

    def test_training_link_counts_as_matching_index(self):
        p = self.rvc_indices / "兔裹_added_IVF500_Flat_nprobe_1_兔裹_v2.index"
        p.write_bytes(b"x")
        self.assertEqual([q.name for q in app._rvc_index_files("兔裹")], [p.name],
                         "刚训练完的音色必须被认成有索引，否则体检与提交都是冤枉")

    def test_downloaded_index_in_logs_root_still_found(self):
        p = self.rvc_logs / "added_IVF348_Flat_nprobe_1_孙燕姿_v2.index"
        p.write_bytes(b"x")
        self.assertEqual([q.name for q in app._rvc_index_files("孙燕姿")], [p.name])

    def test_foreign_trained_and_multispeaker_indexes_are_not_matched(self):
        (self.rvc_indices / "trained_IVF500_Flat_nprobe_1_王菲_v2.index").write_bytes(b"x")
        (self.rvc_indices / "added_IVF500_Flat_nprobe_1_王菲_v2_spkid1.index").write_bytes(b"x")
        (self.rvc_indices / "added_IVF500_Flat_nprobe_1_孙燕姿_v2.index").write_bytes(b"x")
        keep = self.rvc_indices / "added_IVF500_Flat_nprobe_1_王菲_v2.index"
        keep.write_bytes(b"x")
        self.assertEqual([q.name for q in app._rvc_index_files("王菲")], [keep.name],
                         "trained_ 是索引训练自己的中间文件；多说话人索引在换声不指定说话人时"
                         "也不会被选中——两者都不算「有这个音色的索引」")
        self.assertEqual(app._rvc_index_files("不存在的音色"), [])

    def test_delete_removes_both_copies(self):
        saved = app.RVC_MODELS_DIR
        models = self.tmp / "weights"
        models.mkdir()
        app.RVC_MODELS_DIR = models
        client = TestClient(app.app, base_url=LOCAL_BASE)
        try:
            (models / "王菲.pth").write_bytes(b"x")
            a = self.rvc_indices / "王菲_added_IVF9_Flat_nprobe_1_王菲_v2.index"
            b = self.rvc_logs / "added_IVF9_Flat_nprobe_1_王菲_v2.index"
            a.write_bytes(b"x")
            b.write_bytes(b"x")
            self.assertEqual(client.delete("/api/rvc/models/王菲.pth").status_code, 200)
            self.assertFalse(a.exists(), "assets/indices 里那份不能留成孤儿")
            self.assertFalse(b.exists())
        finally:
            app.RVC_MODELS_DIR = saved

    def test_delete_never_touches_a_neighbors_index(self):
        """查找侧允许子串匹配（推理选索引时 王菲 会把 王菲V6 的索引也当候选），
        但删除必须只认这个音色自己的——否则删一个音色会废掉另一个音色的检索。"""
        saved = app.RVC_MODELS_DIR
        models = self.tmp / "weights2"
        models.mkdir()
        app.RVC_MODELS_DIR = models
        client = TestClient(app.app, base_url=LOCAL_BASE)
        try:
            (models / "王菲.pth").write_bytes(b"x")
            mine = self.rvc_logs / "added_IVF9_Flat_nprobe_1_王菲_v2.index"
            neighbor = self.rvc_logs / "added_IVF9_Flat_nprobe_1_王菲V6_v2.index"
            mine.write_bytes(b"x")
            neighbor.write_bytes(b"x")
            self.assertTrue(app._rvc_index_files("王菲"), "查找侧要能看见候选（与推理一致）")
            self.assertEqual(client.delete("/api/rvc/models/王菲.pth").status_code, 200)
            self.assertFalse(mine.exists())
            self.assertTrue(neighbor.exists(), "邻居的索引不是它的文件")
        finally:
            app.RVC_MODELS_DIR = saved


class TestRvcSubmitGate(Sandbox):
    """P0：两个换声入口在入队前把注定失败的参数拦下来（失败点原本在十几分钟队列之后）。"""

    def setUp(self) -> None:
        super().setUp()
        # 注意别覆盖 Sandbox._saved（那是临时目录还原用的字典）
        self._saved_gate = (app._rvc_models, app._rvc_submit,
                            dict(app._RVC_JOBS), list(app._RVC_QUEUE))
        self.seen: list[tuple] = []
        app._rvc_models = lambda: ["孙燕姿.pth"]
        app._rvc_submit = lambda args: (self.seen.append(args), 1)[1]
        app._RVC_JOBS.clear()
        app._RVC_QUEUE.clear()
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def tearDown(self) -> None:
        app._rvc_models, app._rvc_submit = self._saved_gate[0], self._saved_gate[1]
        app._RVC_QUEUE[:] = self._saved_gate[3]
        app._RVC_JOBS.clear()
        app._RVC_JOBS.update(self._saved_gate[2])
        super().tearDown()

    def _post_upload(self, **data):
        form = {"model": "孙燕姿.pth"}
        form.update({k: str(v) for k, v in data.items()})
        return self.client.post("/api/rvc/convert",
                                files={"file": ("demo.wav", b"RIFFxxxx", "audio/wav")},
                                data=form)

    def test_missing_index_is_refused_before_upload_is_queued(self):
        r = self._post_upload(index_rate=0.75)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("没有配套的检索索引", r.json()["detail"])
        self.assertIn("检索强度", r.json()["detail"], "要给出路：调 0 或补索引，而不是只说失败")
        self.assertEqual(self.seen, [], "缺索引的一条都不该进队列")

    def test_index_rate_zero_needs_no_index(self):
        (self.rvc_indices / "孙燕姿_added_IVF9_Flat_nprobe_1_孙燕姿_v2.index").write_bytes(b"x")
        self.assertEqual(self._post_upload(index_rate=0).status_code, 200)
        self.assertEqual(len(self.seen), 1)

    def test_unknown_f0_method_is_refused(self):
        """变调算法的白名单与 CLI choices 同步（infer/cli.py --f0-method）；
        写错的那串要当场说，不能进 argparse。"""
        r = self._post_upload(f0_method="harvest", index_rate=0)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("未知变调算法", r.json()["detail"])

    def test_fcpe_accepted_and_new_knob_gates(self):
        """评审 C1/C2/C3：fcpe 放开（pipeline 与 CLI 都支持，torchfcpe 已装并真机跑通）；
        平滑半径与输出采样率各有闸门。"""
        self.assertEqual(self._post_upload(f0_method="fcpe", index_rate=0).status_code, 200)
        args = self.seen[-1]   # worker 参数元组：[..., strip_harmony, filter_radius, resample_sr]
        self.assertEqual(args[13], 3, "不填 = 官方 WebUI 同值默认 3")
        self.assertEqual(args[14], 0, "默认跟随模型原生采样率（40k 音色不再被无谓上采样）")
        self.assertEqual(self._post_upload(filter_radius=9, index_rate=0).status_code, 400)
        self.assertEqual(self._post_upload(resample_sr=8000, index_rate=0).status_code, 400)
        self.assertEqual(self._post_upload(resample_sr=44100, index_rate=0).status_code, 200)
        self.assertEqual(self.seen[-1][14], 44100)
        with self.assertRaises(HTTPException) as cm:
            app._rvc_convert_params("孙燕姿.pth", "rmvpe", 0, 0.33, 1, "大", 0)
        self.assertEqual(cm.exception.status_code, 400)

    def test_filter_radius_even_is_clamped_to_odd(self):
        """评审 H1：scipy.signal.medfilt 只认奇数核，页面 number 输入 step=1，
        4 和 6 随手能填且能过旧的 0-7 闸门——原值传到推理里整条换声崩在 get_f0。
        网关必须钳到下一个奇数（4→5、6→7）；≤2 本就不触发滤波，不许被"顺手修正"。"""
        self.assertEqual(self._post_upload(filter_radius=4, index_rate=0).status_code, 200)
        self.assertEqual(self.seen[-1][13], 5)
        self.assertEqual(self._post_upload(filter_radius=6, index_rate=0).status_code, 200)
        self.assertEqual(self.seen[-1][13], 7)
        self.assertEqual(self._post_upload(filter_radius=2, index_rate=0).status_code, 200)
        self.assertEqual(self.seen[-1][13], 2, "2 = 关闭档，不该被改成 3（那等于偷偷开启滤波）")
        self.assertEqual(app._rvc_convert_params("孙燕姿.pth", "rmvpe", 0, 0.33, 1, 7, 0)[4], 7)

    def test_fcpe_refused_when_runtime_or_dependency_missing(self):
        """评审 H2/H3：fcpe 缺一样都必须在提交时 400——悄悄降级成 rmvpe 是"点 A 得 B"，
        放进队列则十几分钟后才崩。缺 CLI 支持（runtime 被重装成上游原版）与
        缺 torchfcpe 依赖是两条不同的路，都要说清出路。"""
        app._rvc_cli_caps = lambda: {"filter_radius": True, "fcpe": False}
        r = self._post_upload(f0_method="fcpe", index_rate=0)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("不支持 fcpe", r.json()["detail"])
        self.assertIn("rmvpe", r.json()["detail"], "要给出路：换算法或恢复补丁")
        self.assertEqual(self.seen, [])
        app._rvc_cli_caps = lambda: {"filter_radius": True, "fcpe": True}
        app._rvc_fcpe_ok = lambda: False
        r = self._post_upload(f0_method="fcpe", index_rate=0)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("torchfcpe", r.json()["detail"])
        self.assertEqual(self.seen, [])
        self.assertEqual(self._post_upload(f0_method="rmvpe", index_rate=0).status_code, 200,
                         "rmvpe 是永远可用的路")

    def test_out_of_range_ratios_are_refused(self):
        r = self._post_upload(protect=0.9, index_rate=0)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("protect", r.json()["detail"])
        self.assertEqual(self._post_upload(index_rate=1.5).status_code, 400)
        self.assertEqual(self._post_upload(pitch=90).status_code, 400)

    def test_by_rid_entry_keeps_index_rate_zero(self):
        """送去换声（历史页转发）以前写 `float(payload.get('index_rate') or 0.75)`，
        用户主动设的 0 会被当成"没填"又放回 0.75。"""
        rid = "20260101_000000_abcdabcd"
        (app.OUTPUT_DIR / f"{rid}.wav").write_bytes(b"RIFFxxxx")
        (app.OUTPUT_DIR / f"{rid}.json").write_text(
            json.dumps({"id": rid, "kind": "generate"}), encoding="utf-8")
        r = self.client.post(f"/api/rvc/convert/{rid}",
                             json={"model": "孙燕姿.pth", "index_rate": 0, "protect": 0})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.seen[0][7], 0.0, "入队参数就是用户要的 0")
        # 缺索引 + 检索强度>0 的组合在历史入口同样要拦住
        self.assertEqual(self.client.post(
            f"/api/rvc/convert/{rid}", json={"model": "孙燕姿.pth"}).status_code, 400)

    def test_by_rid_rejects_before_touching_the_disk(self):
        rid = "20260101_000000_abcdefff"
        (app.OUTPUT_DIR / f"{rid}.wav").write_bytes(b"RIFFxxxx")
        (app.OUTPUT_DIR / f"{rid}.json").write_text(
            json.dumps({"id": rid, "kind": "generate"}), encoding="utf-8")
        r = self.client.post(f"/api/rvc/convert/{rid}",
                             json={"model": "孙燕姿.pth", "index_rate": 2})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(list(app.RVC_JOB_DIR.iterdir()), [],
                         "参数不合法时不该在本机留下半成品工作目录")


class TestRvcTrainCheck(Sandbox):
    """P1：训练前先给素材做体检（时长/静音/爆音/响度 + 建议轮数），确认后再开练不重传。"""

    SR = 40000

    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(app.app, base_url=LOCAL_BASE)
        self._saved_train = (app._rvc_train_worker, dict(app.RVC_TRAIN_JOBS))
        self.starts: list[tuple] = []

        def fake_worker(rid, name, epochs, *a, **kw):
            self.starts.append((rid, name, epochs, a, kw))

        app._rvc_train_worker = fake_worker

    def tearDown(self) -> None:
        app._rvc_train_worker = self._saved_train[0]
        app.RVC_TRAIN_JOBS.clear()
        app.RVC_TRAIN_JOBS.update(self._saved_train[1])
        super().tearDown()

    def _wav(self, seconds: float, amp: float = 0.2, silence_tail: float = 0.0,
             clip: bool = False):
        import io
        import soundfile as sf
        n = int(self.SR * seconds)
        t = np.arange(n) / self.SR
        x = amp * np.sin(2 * np.pi * 220 * t)
        if silence_tail:
            k = int(self.SR * silence_tail)
            x[-k:] = 0.0
            x = np.concatenate([x, np.zeros(int(self.SR * silence_tail))])
        if clip:
            x[::7] = 1.0
        buf = io.BytesIO()
        sf.write(buf, x.astype("float32"), self.SR, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    def test_scan_reports_duration_silence_and_clip(self):
        ds = self.tmp / "ds"
        ds.mkdir()
        (ds / "a.wav").write_bytes(self._wav(30))
        (ds / "b.wav").write_bytes(self._wav(30, silence_tail=8))
        rep = app._rvc_dataset_scan(ds)
        self.assertEqual(rep["files"], 2)
        self.assertAlmostEqual(rep["total_sec"], 60 + 8, delta=1.0)
        self.assertTrue(rep["longest_silence_sec"] >= 8, "8 秒纯静音必须量出来")
        self.assertGreater(rep["silence_runs_over_5s"], 0)
        self.assertIn("5 秒的静音段", " ".join(rep["warnings"]))
        self.assertEqual(rep["clipped_ratio"], 0.0)
        self.assertEqual(rep["suggest_epochs"], 100, "68 秒素材：不足 8 分钟走常用档")
        self.assertTrue(rep["advice"])

    def test_scan_flags_clipping_and_tiny_dataset(self):
        ds = self.tmp / "ds2"
        ds.mkdir()
        (ds / "hot.wav").write_bytes(self._wav(10, clip=True))
        rep = app._rvc_dataset_scan(ds)
        self.assertGreater(rep["clipped_ratio"], 0.05)
        self.assertIn("爆音", " ".join(rep["warnings"]))
        self.assertEqual(rep["suggest_epochs"], 30, "10 秒素材只够试听档")
        self.assertIn("不足 1 分钟", rep["advice"])

    def test_check_endpoint_then_train_reuses_the_same_dataset(self):
        r = self.client.post("/api/rvc/train/check",
                             files=[("files", ("a.wav", self._wav(30), "audio/wav")),
                                    ("files", ("b.wav", self._wav(30), "audio/wav"))])
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        rid = body["id"]
        self.assertEqual(body["report"]["files"], 2)
        self.assertTrue((app.RVC_TRAIN_DIR / rid / "dataset" / "sample_000.wav").is_file())
        self.assertEqual((app.RVC_TRAIN_DIR / rid / "job.json").is_file(), True,
                         "体检结果要落盘：刷新页面不能丢")
        only = list(app.RVC_TRAIN_DIR.iterdir())
        q = self.client.post("/api/rvc/train",
                             data={"name": "voiceA", "epochs": "100", "dataset_id": rid})
        self.assertEqual(q.status_code, 200, q.text)
        self.assertEqual(self.starts[0][0], rid, "开练用的就是体检那份素材目录（rid 不变）")
        self.assertEqual(list(app.RVC_TRAIN_DIR.iterdir()), only,
                         "复用素材不得再建一个目录（几十分钟素材不传第二遍）")

    def test_train_without_files_and_without_dataset_id_is_refused(self):
        r = self.client.post("/api/rvc/train", data={"name": "voiceB", "epochs": "100"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("dataset_id", r.json()["detail"])
        bad = self.client.post("/api/rvc/train",
                               data={"name": "voiceB", "epochs": "100",
                                     "dataset_id": "20260101_000000_deadbeef"})
        self.assertEqual(bad.status_code, 404, r.text)

    def test_epoch_tiers_reach_below_the_old_floor(self):
        """旧闸门写死 150-400，"30 轮试听"这种档位根本提交不了；现在按官方口径 1-1200。"""
        r = self.client.post("/api/rvc/train/check",
                             files=[("files", ("a.wav", self._wav(30), "audio/wav"))])
        rid = r.json()["id"]
        self.assertEqual(self.client.post("/api/rvc/train",
                                          data={"name": "voiceC", "epochs": "30",
                                                "dataset_id": rid}).status_code, 200)
        self.assertEqual(self.client.post("/api/rvc/train",
                                          data={"name": "voiceD", "epochs": "0",
                                                "dataset_id": rid}).status_code, 400)


class TestRvcTrainBudget(Sandbox):
    """P1-2/P1-3：训练耗时与 batch_size 都要有出处——实测数字 + 整卡显存，不再拍脑袋。"""

    def setUp(self) -> None:
        super().setUp()
        self._saved_budget = (app.backend_mode, app._gpu_total_mb, dict(app.RVC_TRAIN_JOBS))

    def tearDown(self) -> None:
        app.backend_mode, app._gpu_total_mb = self._saved_budget[0], self._saved_budget[1]
        app.RVC_TRAIN_JOBS.clear()
        app.RVC_TRAIN_JOBS.update(self._saved_budget[2])
        super().tearDown()

    def _done_job(self, rid: str, train_sec: float, epochs: int, samples: int):
        d = app.RVC_TRAIN_DIR / rid
        d.mkdir(parents=True, exist_ok=True)
        (d / "job.json").write_text(json.dumps({
            "id": rid, "status": "done", "train_sec": train_sec,
            "epochs": epochs, "samples_used": samples, "backend": "cuda",
        }), encoding="utf-8")

    def test_batch_size_follows_vram_not_a_hardcoded_4(self):
        # 官方 WebUI 同一口径（webui.py:180 batch_size = VRAM_GB // 2），上限 8 防炸显存
        for total_mb, want in ((6144, 3), (16384, 8), (24576, 8), (2048, 1)):
            app.backend_mode = lambda: "cuda"
            app._gpu_total_mb = lambda t=total_mb: t
            self.assertEqual(app._rvc_train_batch_size()[0], want, f"{total_mb}MB 卡")
        # CPU 模式和"查不到显存"都不能冒进
        app.backend_mode = lambda: "cpu"
        app._gpu_total_mb = lambda: 0
        self.assertEqual(app._rvc_train_batch_size()[0], 4)
        app.backend_mode = lambda: "cuda"
        self.assertEqual(app._rvc_train_batch_size()[0], 4)
        self.assertIn("显存未知", app._rvc_train_batch_size()[1])

    def test_epoch_rate_uses_measured_pace_and_says_so(self):
        # 1182.7 秒 / 200 轮 / 334 切片 = 0.0177 秒每切片每轮
        self._done_job("20260927_000000_measured", 1182.7, 200, 334)
        rate, note = app._rvc_epoch_rate({"samples_used": 400})
        self.assertAlmostEqual(rate, 0.0177 * 400, delta=1.0)
        self.assertIn("实测", note)
        self.assertIn("measured", note)

    def test_epoch_rate_without_any_measurement_is_marked_conservative(self):
        """没有实测记录时不许装作知道：宁可保守，也绝不写成一个看起来像结论的公式。"""
        rate, note = app._rvc_epoch_rate({"samples_used": 400})
        self.assertEqual(rate, 240.0)
        self.assertIn("保守", note)
        # 素材数不明（还没预处理）同样不许按实测夸口
        self._done_job("20260927_000001_other", 600.0, 100, 200)
        self.assertEqual(app._rvc_epoch_rate({})[0], 240.0)

    def test_failed_jobs_do_not_set_the_pace(self):
        """只采信跑完的训练：中途失败/暂停的任务耗时是残缺的，拿它预估会误杀下一单。"""
        d = app.RVC_TRAIN_DIR / "20260927_000002_dead"
        d.mkdir(parents=True, exist_ok=True)
        (d / "job.json").write_text(json.dumps({
            "id": d.name, "status": "error", "train_sec": 12.0,
            "epochs": 200, "samples_used": 400}), encoding="utf-8")
        self.assertIsNone(app._rvc_train_pace())


class TestRvcCheckpoints(Sandbox):
    """P1-3/P1-4：检查点保留策略、逐点试听缓存、选点定稿、训练后自动自检。"""

    NAME = "试听音色"

    def setUp(self) -> None:
        super().setUp()
        self._saved_ck = (app.RVC_DIR, app.RVC_MODELS_DIR,
                          app._require_headroom_for_preview, app._rvc_small_model,
                          app._rvc_preview_infer)
        self.rvc = self.tmp / "rvc"
        app.RVC_DIR = self.rvc
        app.RVC_MODELS_DIR = self.rvc / "assets" / "weights"
        app.RVC_MODELS_DIR.mkdir(parents=True)
        self.logs = self.rvc / "logs" / self.NAME
        self.logs.mkdir(parents=True)
        self.client = TestClient(app.app, base_url=LOCAL_BASE)
        self.rid = "20260927_100000abcdef"
        (app.RVC_TRAIN_DIR / self.rid).mkdir(parents=True)
        (app.RVC_TRAIN_DIR / self.rid / "dataset").mkdir()
        (app.RVC_TRAIN_DIR / self.rid / "dataset" / "s0.wav").write_bytes(b"RIFF")
        self.job = {"id": self.rid, "name": self.NAME, "status": "done",
                    "epochs": 200, "samples_used": 100}
        self._write_job()

    def tearDown(self) -> None:
        (app.RVC_DIR, app.RVC_MODELS_DIR, app._require_headroom_for_preview,
         app._rvc_small_model, app._rvc_preview_infer) = self._saved_ck
        super().tearDown()

    def _write_job(self):
        (app.RVC_TRAIN_DIR / self.rid / "job.json").write_text(
            json.dumps(self.job), encoding="utf-8")

    def _ckpt(self, step: str, age_min: float = 0.0) -> Path:
        p = self.logs / f"G_{step}.pth"
        p.write_bytes(b"x")
        t = time.time() - age_min * 60
        os.utime(p, (t, t))
        d = self.logs / f"D_{step}.pth"
        d.write_bytes(b"x")
        os.utime(d, (t, t))
        return p

    def test_prune_keeps_newest_pairs_and_the_final_export(self):
        for i, age in enumerate((120, 90, 60, 30, 0)):
            self._ckpt(str(1000 * (i + 1)), age_min=age)
        removed = app._rvc_prune_checkpoints(self.NAME)
        left = sorted(p.name for p in self.logs.glob("[GD]_*.pth"))
        self.assertEqual(removed, 6, "5 对里最旧的 3 对应删除（G+D 各 3）")
        self.assertEqual(left, ["D_4000.pth", "D_5000.pth", "G_4000.pth", "G_5000.pth"],
                         "留的是最近 2 对，续跑和换点定稿都还有得用")

    def test_loss_summary_reports_head_and_tail(self):
        lines = []
        for i in range(120):
            lines.append("2026-09-27 10:00:00,000\tx\tINFO\t"
                         f"loss_disc={8 - i * 0.01:.3f}, loss_gen={5 - i * 0.01:.3f}, "
                         "loss_fm=3.000,loss_mel=40.000, loss_kl=9.000")
        lines.append("2026-09-27 10:00:01,000\tx\tINFO\t====> 轮次：200 [2026-09-27 10:00:01]")
        (self.logs / "train.log").write_text("\n".join(lines), encoding="utf-8")
        s = app._rvc_loss_summary(self.NAME)
        self.assertEqual(s["points"], 120)
        self.assertEqual(s["epochs_logged"], 1)
        self.assertLess(s["tail"]["loss_disc"], s["head"]["loss_disc"],
                        "损失首尾要能看出收敛方向，否则'训练完成'四个字说明不了任何事")

    def test_preview_of_a_named_checkpoint_uses_its_own_cache(self):
        """成品和检查点共用一个缓存文件时，先听新的再听旧的会拿到错的音频。"""
        old = self._ckpt("1111", age_min=60)
        self._ckpt("2333333", age_min=0)
        (app.RVC_MODELS_DIR / f"{self.NAME}.pth").write_bytes(b"final")
        seen: dict = {}
        app._require_headroom_for_preview = lambda need_mb=2600: None
        real_small, real_infer = app._rvc_small_model, app._rvc_preview_infer

        def spy_small(ckpt, cache):
            seen["cache"] = Path(cache).name
            return Path(cache)

        def spy_infer(model_path, src, out, index=None):
            seen["model"] = Path(model_path).name
            Path(out).write_bytes(b"RIFFfake")

        app._rvc_small_model = spy_small
        app._rvc_preview_infer = spy_infer
        try:
            r = self.client.post(f"/api/rvc/train/preview/{self.rid}", json={"ck": old.name})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(seen["cache"], "preview_model_1111.pth",
                             "缓存按检查点各自存一份")
            self.assertEqual(seen["model"], "preview_model_1111.pth")
            self.assertIn("1111", r.json()["source"], "点名要的检查点不能偷换成成品")
            self.assertEqual(r.json()["ckpts"], ["G_2333333.pth", "G_1111.pth"])
            # 不点名时才是"用成品"
            r2 = self.client.post(f"/api/rvc/train/preview/{self.rid}", json={})
            self.assertEqual(r2.json()["source"], "成品")
            self.assertEqual(seen["model"], f"{self.NAME}.pth")
            bad = self.client.post(f"/api/rvc/train/preview/{self.rid}",
                                   json={"ck": "G_9999.pth"})
            self.assertEqual(bad.status_code, 404)
            self.assertIn("只保留最近", bad.json()["detail"])
        finally:
            app._rvc_small_model = real_small
            app._rvc_preview_infer = real_infer

    def test_stale_export_never_masquerades_as_this_runs_result(self):
        """同名音色重训：上一轮的成品比本轮检查点还旧时，试听必须听本轮的检查点，
        否则"成品"这个标签就是假话（用户以为听的是刚练的东西）。"""
        ck = self._ckpt("2333333", age_min=0)
        stale = app.RVC_MODELS_DIR / f"{self.NAME}.pth"
        stale.write_bytes(b"old")
        t = time.time() - 3600
        os.utime(stale, (t, t))
        real_infer = app._rvc_preview_infer
        seen = {}
        app._require_headroom_for_preview = lambda need_mb=2600: None
        app._rvc_small_model = lambda ckpt, cache: cache
        app._rvc_preview_infer = lambda m, s, o, i=None: (seen.update(model=Path(m).name),
                                                          Path(o).write_bytes(b"x"))
        try:
            r = self.client.post(f"/api/rvc/train/preview/{self.rid}", json={})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["source"], ck.name + "（step 2333333，非轮次）")
            self.assertEqual(seen["model"], "preview_model_2333333.pth")
            # 检查点也被清光、只剩成品时，才回落到成品
            ck.unlink()
            r2 = self.client.post(f"/api/rvc/train/preview/{self.rid}", json={})
            self.assertEqual(r2.json()["source"], "成品")
            stale.unlink()
            r3 = self.client.post(f"/api/rvc/train/preview/{self.rid}", json={})
            self.assertEqual(r3.status_code, 404)
            self.assertIn("还没有可试听的检查点", r3.json()["detail"])
        finally:
            app._rvc_preview_infer = real_infer

    def test_preview_prefers_short_slices_over_the_raw_upload(self):
        """整首原始上传也能试听，但本机实测那一次跑了 143 秒；切好的 0_gt_wavs
        只要十几秒。自检要挂在每次训练收尾，慢十倍就是给用户白等。"""
        gt = self.logs / "0_gt_wavs"
        gt.mkdir()
        (gt / "00000.wav").write_bytes(b"x")
        self.assertEqual(app._rvc_preview_source(self.rid, self.NAME).name, "00000.wav")
        self.assertEqual(app._rvc_preview_source(self.rid).name, "s0.wav",
                         "没有切片时仍要回退到用户上传的素材，不能直接报错")

    def _fake_extract(self, size: int, calls: list):
        """替身只做一件事：像真导出那样把产物落在专用临时目录里并交还路径。
        定稿端点拿到什么后续处置（校验/备份/原子替换）全部走真实代码。"""
        def fake(ckpt, stem, info, timeout=600, fail_msg=""):
            calls.append({"ckpt": Path(ckpt).name, "stem": stem, "info": info})
            work = app.RVC_DIR / "export_tmp" / stem
            (work / "assets" / "weights").mkdir(parents=True, exist_ok=True)
            produced = work / "assets" / "weights" / f"{stem}.pth"
            produced.write_bytes(b"p" * size)
            return produced, work
        return fake

    def test_promote_rejects_missing_checkpoint_and_records_the_choice(self):
        rid = self.rid
        bad = self.client.post(f"/api/rvc/train/promote/{rid}", json={"ck": "G_7.pth"})
        self.assertEqual(bad.status_code, 404)
        ck = self._ckpt("2333333")
        target = app.RVC_MODELS_DIR / f"{self.NAME}.pth"
        target.write_bytes(b"o" * 1_500_000)              # 上一版成品 1.5MB
        calls: list = []
        real = app._rvc_extract_small
        app._rvc_extract_small = self._fake_extract(1_200_000, calls)
        try:
            r = self.client.post(f"/api/rvc/train/promote/{rid}", json={"ck": ck.name})
        finally:
            app._rvc_extract_small = real
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["ckpt"], "G_2333333.pth", "定稿必须真用点名那个检查点")
        self.assertEqual(calls[0]["stem"], self.NAME)
        self.assertEqual(target.read_bytes(), b"p" * 1_200_000, "换点后的成品就是所选点导出的")
        bak = Path(str(target) + ".bak")
        self.assertEqual(bak.read_bytes(), b"o" * 1_500_000,
                         "替换前的上一版留一份 .bak 可回滚（评审 G2）")
        self.assertFalse((app.RVC_DIR / "export_tmp" / self.NAME).exists(),
                         "临时工作目录用完即删")
        self.assertEqual(json.loads((app.RVC_TRAIN_DIR / rid / "job.json")
                                    .read_text(encoding="utf-8"))["promoted_ckpt"],
                         "G_2333333.pth", "定稿用的是哪个点必须留痕")
        self.assertIn("已用", r.json()["message"])

    def test_promote_refuses_suspect_export_and_leaves_final_intact(self):
        """半截导出（超时被杀/磁盘满）体积远小于正常成品：拒收，原成品一个字节不动。
        以前的判据是"文件存在且 mtime 更新过"，半截货照样被判成功（评审 G2）。"""
        ck = self._ckpt("2222")
        target = app.RVC_MODELS_DIR / f"{self.NAME}.pth"
        original = b"o" * 1_500_000
        target.write_bytes(original)
        real = app._rvc_extract_small
        app._rvc_extract_small = self._fake_extract(200_000, [])
        try:
            r = self.client.post(f"/api/rvc/train/promote/{self.rid}", json={"ck": ck.name})
        finally:
            app._rvc_extract_small = real
        self.assertEqual(r.status_code, 500)
        self.assertIn("体积不可信", r.json()["detail"])
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(Path(str(target) + ".bak").exists(), "拒收时连 .bak 都不该产生")
        job = json.loads((app.RVC_TRAIN_DIR / self.rid / "job.json").read_text(encoding="utf-8"))
        self.assertNotIn("promoted_ckpt", job, "失败的定稿不留痕")

    def test_promote_fails_when_the_export_did_not_happen(self):
        ck = self._ckpt("2222")
        real_run = app.subprocess.run
        app.subprocess.run = lambda *a, **kw: types.SimpleNamespace(
            returncode=1, stderr=b"boom", stdout=b"")
        try:
            r = self.client.post(f"/api/rvc/train/promote/{self.rid}", json={"ck": ck.name})
        finally:
            app.subprocess.run = real_run
        self.assertEqual(r.status_code, 500)
        self.assertIn("定稿失败", r.json()["detail"])

    def test_small_model_export_lands_in_tmp_not_weights_dir(self):
        """评审 G3：走真实的 _rvc_extract_small/_rvc_small_model，只挡子进程边界。
        导出产物一旦落进 assets/weights，音色下拉框就会多出一个 'G_xxx' 杂音色。"""
        ck = self._ckpt("3333")
        before = sorted(p.name for p in app.RVC_MODELS_DIR.iterdir())
        seen: dict = {}

        def fake_run(cmd, *a, **kw):
            stem, work = cmd[4], Path(cmd[7])
            seen["work"] = str(work)
            self.assertTrue(str(app.RVC_DIR / "export_tmp") in str(work),
                            "导出必须被指派到专用临时目录，而不是音色权重目录")
            (work / "assets" / "weights").mkdir(parents=True, exist_ok=True)
            (work / "assets" / "weights" / f"{stem}.pth").write_bytes(b"p" * 10)
            return types.SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")

        real_sub, real_run = app.subprocess, app.subprocess.run
        app.subprocess = types.SimpleNamespace(run=fake_run)
        cache = self.tmp / "cache_3333.pth"
        try:
            out = app._rvc_small_model(ck, cache)
        finally:
            app.subprocess = real_sub
            app.subprocess.run = real_run
        self.assertEqual(out, cache)
        self.assertEqual(cache.read_bytes(), b"p" * 10, "产物应被搬进按检查点各自的缓存")
        self.assertEqual(sorted(p.name for p in app.RVC_MODELS_DIR.iterdir()), before,
                         "整个导出过程音色权重目录必须一个文件都不多")
        self.assertFalse(Path(seen["work"]).exists(), "临时工作目录用完即删")


class TestRvcDoneMeta(Sandbox):
    """评审 G4：盘上产物 meta 里 status=done 的任务不该还挂着 step='排队中'/queue_pos。
    前端不看它，但事后排查和 AI 侧读 meta 都会被带偏。"""

    RID = "20260101_000008_gggggggg"

    def test_done_meta_has_no_queue_stage_leftovers(self):
        saved = (app.RVC_DIR, app.RVC_MODELS_DIR, dict(app._RVC_JOBS),
                 app.subprocess, app._win_toast)
        rvc = self.tmp / "rvc"
        app.RVC_DIR = rvc
        app.RVC_MODELS_DIR = rvc / "assets" / "weights"
        app._RVC_JOBS.clear()
        app._win_toast = lambda *a, **k: None
        in_dir = app.RVC_JOB_DIR / self.RID
        in_dir.mkdir(parents=True)
        src = in_dir / "in.wav"
        src.write_bytes(b"RIFF")

        def fake_run(cmd, *a, **kw):
            (in_dir / "converted.wav").write_bytes(b"WAVEdata")
            return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        app.subprocess = types.SimpleNamespace(run=fake_run)
        try:
            job = {"id": self.RID, "status": "pending", "step": "排队中", "queue_pos": 2,
                   "model": "x.pth", "src_name": "in.wav"}
            app._rvc_convert_worker(self.RID, job, src, in_dir, "x.pth",
                                    0, "rmvpe", 0.0, 0.33, 1.0)
            meta = json.loads((app.OUTPUT_DIR / f"{self.RID}.json").read_text(encoding="utf-8"))
        finally:
            (app.RVC_DIR, app.RVC_MODELS_DIR) = saved[0], saved[1]
            app._RVC_JOBS.clear()
            app._RVC_JOBS.update(saved[2])
            app.subprocess, app._win_toast = saved[3], saved[4]
        self.assertEqual(meta["status"], "done")
        self.assertNotIn("step", meta, "done 的产物里不该有排队阶段的 step")
        self.assertNotIn("queue_pos", meta)
        self.assertIn("sec", meta, "真实耗时字段该留着——清的是撒谎的，不是干活")
        # 评审 C4：src 是 b"RIFF" 假文件，音域测量注定失败——不许把成功的换声改判失败，
        # 但失败必须留痕（meta.src_f0.error），不许静默。
        self.assertIn("src_f0", meta)
        self.assertIn("error", meta["src_f0"])


class TestPitchRange(Sandbox):
    """评审 C4：音域匹配与自动变调建议。目标端只采信本机训练留下的 2a_f0 实测值；
    源端用 pyworld 真跑（纯 CPU、毫秒级），测试不落任何真实 runtime 目录。"""

    def setUp(self):
        super().setUp()
        self._saved_rvc = app.RVC_DIR
        app.RVC_DIR = self.tmp / "rvc"
        (app.RVC_DIR / "logs").mkdir(parents=True)
        app._RVC_F0STATS_MEM.clear()

    def tearDown(self):
        app._RVC_F0STATS_MEM.clear()
        app.RVC_DIR = self._saved_rvc
        super().tearDown()

    def test_target_range_only_from_real_training_data(self):
        import numpy as np
        d = app.RVC_DIR / "logs" / "试唱" / "2a_f0"
        d.mkdir(parents=True)
        np.save(d / "a.npy", np.array([100.0, 200.0, 300.0, 0.0]))  # 0 帧必须被剔除
        np.save(d / "b.npy", np.array([200.0, 250.0]))
        st = app._rvc_f0_stats("试唱.pth")
        self.assertEqual(st["median_hz"], 200.0, "[100,200,200,250,300] 排序后中位是第 3 个 = 200")
        self.assertEqual(st["frames"], 5)
        self.assertEqual(st["files"], 2)
        self.assertTrue((app.RVC_DIR / "logs" / "试唱" / "f0_stats.json").is_file(),
                        "算过一次要落 sidecar，模型列表不该每次重扫几百个 npy")
        self.assertIsNone(app._rvc_f0_stats("王菲.pth"),
                          "外部下载音色没有训练素材——如实给 None，绝不拿别人的数字冒充")

    def test_source_f0_measured_for_real(self):
        """pyworld 走真路径：220Hz 正弦要量出 ~220；纯静音必须 422 而不是编一个数。"""
        import numpy as np
        import soundfile as sf
        sr = 22050
        t = np.arange(int(sr * 3.5)) / sr
        p = self.tmp / "sine.wav"
        sf.write(str(p), (0.5 * np.sin(2 * np.pi * 220 * t)).astype("float32"), sr)
        got = app._rvc_f0_of_audio(p)
        self.assertTrue(200 <= got["median_hz"] <= 242, f"220Hz 实测 {got['median_hz']}")
        self.assertLessEqual(got["analyzed_sec"], 3.6)
        z = self.tmp / "sil.wav"
        sf.write(str(z), np.zeros(sr * 4, dtype="float32"), sr)
        with self.assertRaises(HTTPException) as cm:
            app._rvc_f0_of_audio(z)
        self.assertEqual(cm.exception.status_code, 422)

    def test_suggestion_math_and_clamp(self):
        s = app._rvc_pitch_suggestion(220.0, 330.0, 0)   # 纯五度 = 7.02 半音
        self.assertEqual(s["suggested_pitch"], 7)
        self.assertTrue(s["apply"])
        s = app._rvc_pitch_suggestion(220.0, 226.0, 0)   # 差不到一个半音不许瞎建议
        self.assertEqual(s["suggested_pitch"], 0)
        self.assertFalse(s["apply"])
        s = app._rvc_pitch_suggestion(100.0, 1000.0, 0)  # 39.9 半音 → 钳 24（与闸门同口径）
        self.assertEqual(s["suggested_pitch"], 24)
        s = app._rvc_pitch_suggestion(440.0, 110.0, 0)   # 低八度 → -24
        self.assertEqual(s["suggested_pitch"], -24)

    def test_advice_endpoint_states_and_cleanup(self):
        import math
        import numpy as np
        import soundfile as sf
        saved_models = app._rvc_models
        client = TestClient(app.app, base_url=LOCAL_BASE)
        try:
            app._rvc_models = lambda: ["试唱.pth"]
            sr = 22050
            t = np.arange(int(sr * 3.5)) / sr
            p = self.tmp / "vocal.wav"
            sf.write(str(p), (0.5 * np.sin(2 * np.pi * 220 * t)).astype("float32"), sr)

            def post():
                return client.post("/api/rvc/pitch/advice",
                                   files={"file": ("vocal.wav", p.read_bytes(), "audio/wav")},
                                   data={"model": "试唱.pth"})

            r = post()
            self.assertEqual(r.status_code, 200, r.text)
            j = r.json()
            self.assertIsNone(j["target"])
            self.assertIn("没有音域数据", j["text"], "目标没数据就直说，不含糊")
            self.assertNotIn("suggestion", j)
            # 给"试唱"落一份中位 440Hz 的训练 f0 → 建议应恰为 +12
            d = app.RVC_DIR / "logs" / "试唱" / "2a_f0"
            d.mkdir(parents=True)
            np.save(d / "x.npy", np.full(500, 440.0))
            app._RVC_F0STATS_MEM.clear()
            r = post()
            self.assertEqual(r.status_code, 200, r.text)
            j = r.json()
            self.assertTrue(j.get("suggestion"), "两边都有数据就必须给出建议")
            self.assertLess(abs(j["source"]["median_hz"] - 220), 22)
            self.assertEqual(j["suggestion"]["suggested_pitch"],
                             round(12 * math.log2(440.0 / j["source"]["median_hz"])))
            self.assertEqual(list(app.RVC_JOB_DIR.glob("pitch_*")), [],
                             "分析是一次性的，临时目录不能留在盘上")
        finally:
            app._rvc_models = saved_models


class TestRvcModelsList(Sandbox):
    """评审 G3 兜底：检查点文件/临时文件一旦误落进权重目录，也不能被列成可选音色。"""

    def test_only_real_finals_are_listed(self):
        saved = app.RVC_MODELS_DIR
        d = self.tmp / "weights"
        d.mkdir()
        app.RVC_MODELS_DIR = d
        try:
            for n in ("王菲.pth", "G_2333333.pth", "D_2333333.pth", ".promote_tmp.pth"):
                (d / n).write_bytes(b"x")
            (d / "孙燕姿.pth.bak").write_bytes(b"x")
            self.assertEqual(app._rvc_models(), ["王菲.pth"])
        finally:
            app.RVC_MODELS_DIR = saved


class TestTrainCleanSilentGuard(Sandbox):
    """2026-09-27「蛋卷」真机事故锁测：legacy VR（UVR-*）多 band 卷积在这块卡上
    整批吐 NaN，被 nan_to_num 洗成数字零——产物退出码 0、文件齐全、内容全静音，
    一路骗到预处理才以"没有可用样本"收口（2.2 小时白跑）。三条防线：
    ① py312 sitecustomize 的 PYMSS_DISABLE_CUDNN 开关只喂 legacy VR 净化子进程；
    ② 选中声部峰值≈0 判失败：整批静音换 CPU 重跑、个别静音退回净化前、落档复测；
    ③ 续跑复用留档前先验峰值——上一轮留下的静音不能被当成"已净化"直接继承。
    stub 同样只放在 subprocess.run 边界，其余全走真实函数路径。"""

    DE_BS = "dereverb_bs_roformer_anvuew_sdr_22.5050"   # 中档去混响（GPU 正常）
    DN_VR = "UVR-DeNoise"                               # 中档降噪（本事故主犯）
    SUFFIX = {DE_BS: ["noreverb", "reverb"],
              DN_VR: ["No Noise", "Noise"]}

    def setUp(self) -> None:
        super().setUp()
        self._saved_guard = (app.subprocess, app.backend_mode, app._pymss_env,
                             app._pymss_creationflags)
        app.backend_mode = lambda: "cuda"
        app._pymss_env = lambda: {}
        app._pymss_creationflags = lambda: 0
        self.cmds: list[list[str]] = []
        self.envs: list[tuple[str, str, dict]] = []
        self.silent_on_cuda: set[str] = set()     # 模型 → cuda 跑出静音、cpu 正常
        self.no_output_for: set[tuple[str, str]] = set()
        self.real_audio = False                   # True 时产物写真 wav（测峰值链）

        class _R:
            returncode = 0
            stdout = b""
            stderr = b""

        def _write_wav(path: Path, silent: bool) -> None:
            import numpy as np
            import soundfile as sf
            n = 40000 * 3
            x = (np.zeros(n, dtype="float32") if silent else
                 (0.3 * np.sin(2 * np.pi * 220 * np.arange(n) / 40000)).astype("float32"))
            sf.write(str(path), x, 40000)

        def fake_run(cmd, **kw):
            cmd = [str(c) for c in cmd]
            self.cmds.append(cmd)
            model = cmd[cmd.index("infer") + 1]
            dev = cmd[cmd.index("--device") + 1]
            src = Path(cmd[cmd.index("-i") + 1])
            out = Path(cmd[cmd.index("-o") + 1])
            self.envs.append((model, dev, dict(kw.get("env") or {})))
            out.mkdir(parents=True, exist_ok=True)
            items = [src] if src.is_file() else sorted(p for p in src.iterdir()
                                                       if p.is_file())
            for w in items:
                if (model, w.stem) in self.no_output_for:
                    continue
                silent = model in self.silent_on_cuda and dev == "cuda"
                for s in self.SUFFIX[model]:
                    dst = out / f"{w.stem}_{s}.wav"
                    if silent:
                        _write_wav(dst, True)
                    elif self.real_audio:
                        _write_wav(dst, False)
                    else:
                        dst.write_bytes(s.encode())
            return _R()

        app.subprocess = types.SimpleNamespace(run=fake_run)

    def tearDown(self) -> None:
        app.subprocess, app.backend_mode, app._pymss_env, app._pymss_creationflags = \
            self._saved_guard
        super().tearDown()

    def _mk(self, *stems: str):
        rid = "trainsilent01"
        ds = app.RVC_TRAIN_DIR / rid / "dataset"
        ds.mkdir(parents=True, exist_ok=True)
        for s in stems:
            (ds / f"{s}.wav").write_bytes(b"orig")
        return ds, app.RVC_TRAIN_DIR / rid / "dataset_purified", {"id": rid}

    def test_cudnn_off_env_only_for_legacy_vr(self):
        ds, out, job = self._mk("a")
        app._rvc_clean_dataset(ds, out, "medium", job)
        vr_env = [e for m, d, e in self.envs if m == self.DN_VR]
        bs_env = [e for m, d, e in self.envs if m == self.DE_BS]
        self.assertTrue(vr_env and all(e.get("PYMSS_DISABLE_CUDNN") == "1" for e in vr_env),
                        "legacy VR 净化子进程必须带 PYMSS_DISABLE_CUDNN=1（cuDNN NaN 的唯一开关）")
        self.assertTrue(bs_env and all("PYMSS_DISABLE_CUDNN" not in e for e in bs_env),
                        "bs_roformer 不关 cuDNN——别为了修 VR 把分离/去混响一起拖慢")

    def test_gpu_silent_batch_reruns_on_cpu_and_lands_real_audio(self):
        ds, out, job = self._mk("a", "b")
        self.silent_on_cuda = {self.DN_VR}
        self.real_audio = True   # 走真实峰值链：CPU 重跑的产物必须是可读的真 wav
        info = app._rvc_clean_dataset(ds, out, "medium", job)
        dn_devs = [d for m, d, _ in self.envs if m == self.DN_VR]
        self.assertIn("cuda", dn_devs)
        self.assertIn("cpu", dn_devs, "GPU 整批吐静音必须自动换 CPU 重跑，而不是收工")
        self.assertEqual((info["fully_cleaned"], info["carried"]), (2, 0))
        self.assertIn("数字静音", info["note"] or "", "换设备这件事必须留在任务卡上")
        self.assertIsNotNone(info["min_peak"])
        for f in ("a.wav", "b.wav"):
            import soundfile as sf
            x, _ = sf.read(str(out / f), dtype="float32")
            self.assertGreater(float(abs(x).max()), 0.05,
                               "落档的成品必须真的是有声素材，不是数字零")

    def test_quiet_noise_stem_is_not_mistaken_for_silence(self):
        """降噪模型本来就极安静的 Noise 声部（真机实测峰值 4e-4）不能触发静音守卫，
        否则好素材会被误判、整批白白重跑一遍 CPU。"""
        import numpy as np
        import soundfile as sf
        quiet = self.tmp / "quiet.wav"
        sf.write(str(quiet), (4e-4 * np.ones(40000 * 2)).astype("float32"), 40000)
        silent = self.tmp / "silent.wav"
        sf.write(str(silent), np.zeros(40000 * 2, dtype="float32"), 40000)
        loud = self.tmp / "loud.wav"
        sf.write(str(loud), (0.3 * np.ones(40000 * 2)).astype("float32"), 40000)
        self.assertTrue(app._rvc_clean_pick_silent(silent))
        self.assertFalse(app._rvc_clean_pick_silent(quiet))
        self.assertFalse(app._rvc_clean_pick_silent(loud))

    def test_resume_reuse_refuses_stale_silent_purified(self):
        """续跑复用留档前先验峰值：上一轮 cuDNN NaN→0 留下的 dataset_purified
        不能被"文件齐了就跳过"直接继承，否则数字零会被喂进预处理再白跑一次。"""
        import numpy as np
        import soundfile as sf
        ds, out, job = self._mk("a")
        out.mkdir(parents=True, exist_ok=True)
        sf.write(str(out / "a.wav"), np.zeros(40000 * 2, dtype="float32"), 40000)
        self.no_output_for = {(self.DE_BS, "a"), (self.DN_VR, "a")}
        with self.assertRaises(RuntimeError) as cm:
            app._rvc_clean_dataset(ds, out, "medium", job, resume=True)
        self.assertIn("素材净化全部失败", str(cm.exception))

    def test_unreadable_output_is_not_silence(self):
        """读不动的产物交给下游报错，不许冒充"静音"——否则会把可诊断的损坏
        伪装成环境问题。"""
        self.assertIsNone(app._rvc_clean_wav_peak(self.tmp / "nope.wav"))


class TestPreprocessEmptySliceGuard(unittest.TestCase):
    """runtime/rvc/train/preprocess.py:109 的真空切片守卫（真机撞过）：
    切片器一片都切不出时老代码在 norm_write 上抛 UnboundLocalError，13 条素材
    全报同一个栈，真原因（整段静音/过短）被完全遮住。这里用真脚本、真静音素材跑。"""

    RUNTIME = ROOT / "runtime" / "rvc"

    @unittest.skipUnless((ROOT / "runtime" / "rvc" / "train" / "preprocess.py").is_file(),
                         "本机没有 vendored runtime")
    def test_silent_input_reports_skip_not_unbound_error(self):
        import subprocess
        import numpy as np
        import soundfile as sf
        with tempfile.TemporaryDirectory(prefix="yue2-pp-") as t:
            t = Path(t)
            src, out = t / "in", t / "out"
            src.mkdir()
            out.mkdir()
            sf.write(str(src / "sil.wav"), np.zeros(40000 * 30, dtype="float32"), 40000)
            env = {**os.environ, "PYTHONPATH": str(self.RUNTIME),
                   "RVC_AUDIO_FORCE_CPU": "1"}
            r = subprocess.run(
                [sys.executable, "-P", str(self.RUNTIME / "train" / "preprocess.py"),
                 str(src), "40000", "1", str(out), "True", "3.7"],
                capture_output=True, timeout=600, env=env, cwd=str(self.RUNTIME))
            log = (out / "preprocess.log")
            text = log.read_text(encoding="utf8") if log.is_file() else ""
            self.assertNotIn("UnboundLocalError", (r.stdout or b"").decode("utf8", "replace")
                             + (r.stderr or b"").decode("utf8", "replace") + text,
                             "空切片必须走新守卫，不许再抛 UnboundLocalError")
            self.assertIn("切不出任何片段", text)
            self.assertEqual(list((out / "0_gt_wavs").iterdir()), [],
                             "静音素材切不出片段，产物目录就该是空的")


class TestRvcIndexNamed(Sandbox):
    """推理必须点名给索引：本机 王菲 与 王菲V6 并存时，runtime 自己的子串猜测
    会按文件名排序选中 王菲V6 的外链——选 王菲 却在用别人的检索库。"""

    RID = "20260101_000007_hhhhhhhh"

    def setUp(self) -> None:
        super().setUp()
        self._saved_named = (app.subprocess, app._win_toast)
        app._win_toast = lambda title, body: None
        self.cmds: list[list[str]] = []

        class _Failed:
            returncode = 1
            stdout = b""
            stderr = b"intentional failure"

        def fake_run(cmd, **kw):
            self.cmds.append(list(map(str, cmd)))
            return _Failed()

        app.subprocess = types.SimpleNamespace(run=fake_run)
        models = self.tmp / "weights-named"
        models.mkdir()
        self._saved_named = (app.subprocess, app._win_toast, app.RVC_MODELS_DIR)
        app.RVC_MODELS_DIR = models
        (models / "王菲.pth").write_bytes(b"x")
        # 故意让"别人的"外链按文件名排在前面（IVF2574 < IVF3250）
        (self.rvc_logs / "added_IVF2574_Flat_nprobe_1_王菲V6_v2.index").write_bytes(b"x")
        self.mine = self.rvc_logs / "added_IVF3250_Flat_nprobe_1_王菲_v2.index"
        self.mine.write_bytes(b"x")

    def tearDown(self) -> None:
        app.subprocess, app._win_toast, app.RVC_MODELS_DIR = self._saved_named
        with app._RVC_LOCK:
            app._RVC_JOBS.pop(self.RID, None)
        super().tearDown()

    def _run_worker(self, index_rate: float = 0.75):
        in_dir = app.RVC_JOB_DIR / self.RID
        in_dir.mkdir(parents=True, exist_ok=True)
        job = {"id": self.RID, "status": "pending", "step": "排队中", "queue_pos": 1,
               "model": "王菲.pth", "src_name": "x.wav", "src_duration": 30,
               "ts": "2026-01-01T00:00:00"}
        with app._RVC_LOCK:
            app._RVC_JOBS[self.RID] = job
        app._rvc_convert_worker(self.RID, job, in_dir / "src.wav", in_dir,
                                "王菲.pth", 0, "rmvpe", index_rate, 0.33, 0.25)
        return job

    def test_worker_names_this_voices_own_index(self):
        self._run_worker()
        self.assertEqual(len(self.cmds), 1)
        cmd = self.cmds[0]
        i = cmd.index("--index") if "--index" in cmd else -1
        self.assertGreater(i, -1, "不给 --index 就等于把选哪个索引交给猜")
        self.assertEqual(Path(cmd[i + 1]).name, self.mine.name,
                         "必须是 王菲 自己的那份，不是 王菲V6 的")
        j = cmd.index("--index-rate")
        self.assertEqual(float(cmd[j + 1]), 0.75)
        self.assertEqual(cmd[cmd.index("--filter-radius") + 1], "3",
                         "不显式给参的调用方也拿官方默认 3，而不是永远 0")
        self.assertEqual(cmd[cmd.index("--resample-sr") + 1], "0")

    def test_index_flag_omitted_when_retrieval_is_off(self):
        self._run_worker(index_rate=0.0)
        self.assertNotIn("--index", self.cmds[0],
                         "关掉检索强度时不该还塞一个索引文件进去")
        self.assertEqual(self.cmds[0][self.cmds[0].index("--index-rate") + 1], "0.0")

    def test_worker_degrades_when_cli_lacks_filter_radius(self):
        """评审 H2：runtime/ 不入库，重装后是上游原版——不认识 --filter-radius，
        照传 argparse 退出码 2，等于每一单换声都失败。探测到缺能力必须**不传该参数**
        并在任务上明写 caps_warn（不许静默），--resample-sr 是原版就有的、照常传。"""
        app._rvc_cli_caps = lambda: {"filter_radius": False, "fcpe": False}
        job = self._run_worker()
        cmd = self.cmds[0]
        self.assertNotIn("--filter-radius", cmd)
        self.assertIn("--resample-sr", cmd)
        self.assertIn("caps_warn", job, "跳过这一步必须留痕，不许静默退化")
        self.assertIn("patches/rvc-infer", job["caps_warn"], "要给出路")

    def test_new_knobs_reach_the_command_line(self):
        """评审 C2/C3：平滑半径、输出采样率与 fcpe 必须按任务参数传给推理 CLI，
        而不是被 worker 里写死的旧值吃掉（--resample-sr 曾被硬编码成 48000）。"""
        in_dir = app.RVC_JOB_DIR / self.RID
        in_dir.mkdir(parents=True, exist_ok=True)
        job = {"id": self.RID, "status": "pending", "model": "王菲.pth",
               "src_name": "x.wav", "src_duration": 30, "ts": "2026-01-01T00:00:00"}
        with app._RVC_LOCK:
            app._RVC_JOBS[self.RID] = job
        app._rvc_convert_worker(self.RID, job, in_dir / "src.wav", in_dir,
                                "王菲.pth", 0, "fcpe", 0.5, 0.33, 1.0,
                                False, False, False, 5, 48000)
        cmd = self.cmds[0]
        self.assertEqual(cmd[cmd.index("--f0-method") + 1], "fcpe")
        self.assertEqual(cmd[cmd.index("--filter-radius") + 1], "5")
        self.assertEqual(cmd[cmd.index("--resample-sr") + 1], "48000")


class TestRvcCapsProbe(unittest.TestCase):
    """能力探测的实现锁（不走 Sandbox：那边把探测本身 stub 掉了）。
    这里只假 subprocess，验证真实解析/缓存/异常路径。"""

    def setUp(self):
        self._saved = (app.subprocess, app._RVC_CAPS, app._RVC_FCPE_OK)

    def tearDown(self):
        app.subprocess, app._RVC_CAPS, app._RVC_FCPE_OK = self._saved

    def test_caps_parses_help_and_caches(self):
        calls = []

        class _R:
            stdout = b"usage: cli.py [--f0-method {pm,rmvpe,fcpe}] [--filter-radius N]"

        def fake_run(cmd, **kw):
            calls.append(list(map(str, cmd)))
            return _R()

        app.subprocess, app._RVC_CAPS = types.SimpleNamespace(run=fake_run), None
        caps = app._rvc_cli_caps()
        self.assertEqual(caps, {"filter_radius": True, "fcpe": True})
        self.assertIn("--help", calls[0])
        app._rvc_cli_caps()
        self.assertEqual(len(calls), 1, "探测结果必须进程内缓存——每次都跑子进程会在提交路径上白等")

    def test_caps_probe_failure_reports_no_capabilities(self):
        """探测本身坏了不能反过来崩提交：按"缺能力"降级（fcpe 拒、filter-radius 跳过并留痕）。"""
        def boom(cmd, **kw):
            raise OSError("no python")
        app.subprocess, app._RVC_CAPS = types.SimpleNamespace(run=boom), None
        caps = app._rvc_cli_caps()
        self.assertEqual(caps, {"filter_radius": False, "fcpe": False})

    def test_fcpe_ok_import_and_cache(self):
        calls = []

        class _Ok:
            returncode = 0

        def fake_run(cmd, **kw):
            calls.append(list(map(str, cmd)))
            return _Ok()

        app.subprocess, app._RVC_FCPE_OK = types.SimpleNamespace(run=fake_run), None
        self.assertTrue(app._rvc_fcpe_ok())
        app._rvc_fcpe_ok()
        self.assertEqual(len(calls), 1, "import torchfcpe 实测 5 秒级，只许探一次")
        class _Bad:
            returncode = 1
        app.subprocess, app._RVC_FCPE_OK = types.SimpleNamespace(
            run=lambda cmd, **kw: _Bad()), None
        self.assertFalse(app._rvc_fcpe_ok())


class TestTrainClean(Sandbox):
    """A1 素材净化锁测：两阶段 CLI 的真实命令行形状、声部实名挑选
    （Dry / No Noise——"No Noise" 含 "Noise"，子串匹配会选错边）、
    逐文件回退、全失败不落脏副本、续跑复用——全部挂在 _rvc_clean_dataset 的
    真实调用路径上（stub 只放在 subprocess.run 边界）。"""

    DE = "UVR-DeReverb-aufr33-jarredou_4band_v4_ms_fullband"
    DN = "UVR-DeNoise-Lite"
    SUFFIX = {DE: ["Dry", "Reverb"], DN: ["Noise", "No Noise"]}

    def setUp(self) -> None:
        super().setUp()
        self._saved_clean = (app.subprocess, app.backend_mode,
                             app._pymss_env, app._pymss_creationflags)
        app.backend_mode = lambda: "cpu"
        app._pymss_env = lambda: {}
        app._pymss_creationflags = lambda: 0
        self.cmds: list[list[str]] = []
        self.stage_inputs: dict[str, list[str]] = {}
        self.no_output_for: set[tuple[str, str]] = set()   # (model, stem)→该文件无产物
        self.dir_fail: set[str] = set()   # model→整目录批跑炸（逐文件重试应能成）
        self.all_fail: set[str] = set()   # model→批跑与单文件都炸

        class _R:
            returncode = 0
            stdout = b""
            stderr = b""

        def fake_run(cmd, **kw):
            cmd = [str(c) for c in cmd]
            self.cmds.append(cmd)
            model = cmd[cmd.index("infer") + 1]
            src = Path(cmd[cmd.index("-i") + 1])
            out = Path(cmd[cmd.index("-o") + 1])
            if (src.is_dir() and model in self.dir_fail) or model in self.all_fail:
                raise RuntimeError("No module named 'tools.pymss'")
            self.stage_inputs.setdefault(model, []).append(
                sorted(p.name for p in src.iterdir()) if src.is_dir() else [src.name])
            out.mkdir(parents=True, exist_ok=True)
            items = [src] if src.is_file() else sorted(p for p in src.iterdir()
                                                       if p.is_file())
            for w in items:
                if (model, w.stem) in self.no_output_for:
                    continue
                for s in self.SUFFIX[model]:
                    (out / f"{w.stem}_{s}.wav").write_bytes(s.encode())
            return _R()

        app.subprocess = types.SimpleNamespace(run=fake_run)

    def tearDown(self) -> None:
        app.subprocess, app.backend_mode, app._pymss_env, app._pymss_creationflags = \
            self._saved_clean
        super().tearDown()

    def _mk(self, *stems: str):
        rid = "trainclean01"
        ds = app.RVC_TRAIN_DIR / rid / "dataset"
        ds.mkdir(parents=True, exist_ok=True)
        for s in stems:
            (ds / f"{s}.wav").write_bytes(b"orig")
        out = app.RVC_TRAIN_DIR / rid / "dataset_purified"
        return ds, out, {"id": rid}

    def test_two_stages_community_order_clean_stems_only(self):
        ds, out, job = self._mk("a", "b")
        info = app._rvc_clean_dataset(ds, out, "light", job)
        models = [c[c.index("infer") + 1] for c in self.cmds]
        self.assertEqual(models, [self.DE, self.DN],
                         "顺序必须是先去混响再降噪（社区口径），且各只批跑一次")
        self.assertEqual(self.stage_inputs[self.DE], [["a.wav", "b.wav"]])
        self.assertEqual(self.stage_inputs[self.DN], [["a.wav", "b.wav"]],
                         "第二阶段入口只能是挑选后的干净人声，Reverb/Noise 声部不得混入")
        for f in ("a.wav", "b.wav"):
            self.assertEqual((out / f).read_bytes(), b"No Noise",
                             "最终留下的必须是降噪模型的干净一路")
        self.assertEqual((ds / "a.wav").read_bytes(), b"orig",
                         "原始素材必须原样留档（A/B 的前提）")
        self.assertEqual((info["fully_cleaned"], info["carried"], info["label"]),
                         (2, 0, "轻"))
        self.assertFalse((out.parent / "_clean_tmp").exists(), "中间产物用完即清")
        self.assertEqual(self.cmds[0][:4], [str(app.RVC_PY), "-m", "pymss.cli", "infer"],
                         "包名必须是 pymss.cli：VR 模型的 modules 别名层只认 pymss.* 前缀，"
                         "沿用分离链的 tools.pymss.cli 会当场崩（真机实测）")
        self.assertIn("--download", self.cmds[0],
                      "换机器缺权重时 CLI 自己补下载，而不是静默失败")

    def test_missing_output_falls_back_per_file_and_is_counted(self):
        ds, out, job = self._mk("a", "b")
        self.no_output_for = {(self.DE, "a"), (self.DN, "a")}
        info = app._rvc_clean_dataset(ds, out, "light", job)
        self.assertEqual((out / "a.wav").read_bytes(), b"orig",
                         "两阶段都没产物的文件带着净化前版本进最终目录，绝不丢样本")
        self.assertEqual((out / "b.wav").read_bytes(), b"No Noise")
        self.assertEqual((info["fully_cleaned"], info["carried"]), (1, 2),
                         "回退必须计数留痕，不能装作整批都净化过")

    def test_batch_failure_retries_per_file(self):
        ds, out, job = self._mk("a", "b")
        self.dir_fail = {self.DE}
        info = app._rvc_clean_dataset(ds, out, "light", job)
        de_calls = [c for c in self.cmds if c[c.index("infer") + 1] == self.DE]
        self.assertEqual(len(de_calls), 3, "整批炸一次后必须逐文件重试（1 批 + 2 单）")
        self.assertEqual(info["fully_cleaned"], 2)

    def test_rc0_with_zero_output_is_not_success(self):
        """评审 J3（真机撞过）：-i 指到不存在的目录时 PyMSS 退 0、零产出。
        "退 0 即成功"必须被堵死——否则净化静默跳过，拿未净化素材继续练还报告成功。"""
        src = self.tmp / "j3_in"
        src.mkdir()
        (src / "a.wav").write_bytes(b"x")
        self.no_output_for = {(self.DE, "a")}
        err = app._rvc_pymss_stage(self.DE, src, self.tmp / "j3_out", "cpu")
        self.assertIn("退出码 0 但没有任何产物", err,
                      "rc=0 + 零产出必须返回错误文案，不许是约定的成功哨兵 \"\"")
        self.no_output_for = set()  # 恢复正常：有产物时防护不该误伤
        self.assertEqual(app._rvc_pymss_stage(self.DE, src, self.tmp / "j3_out2", "cpu"),
                         "")  # 有产物时仍判成功——别把防护变成误伤

    def test_zero_output_tries_per_file_and_reports(self):
        # 整批 rc0 零产出 → 走逐文件重试链，最终全败时报错文案带上这个原因
        ds, out, job = self._mk("a", "b")
        self.no_output_for = {(self.DE, "a"), (self.DE, "b"),
                              (self.DN, "a"), (self.DN, "b")}
        with self.assertRaises(RuntimeError) as cm:
            app._rvc_clean_dataset(ds, out, "light", job)
        self.assertIn("素材净化全部失败", str(cm.exception))
        self.assertIn("退出码 0", str(cm.exception),
                      "报错要能看出是 rc0 零产出，不是别的原因")
        self.assertEqual(list(out.iterdir()), [], "全失败时输出目录必须空着")

    def test_total_failure_leaves_no_unclean_copies(self):
        ds, out, job = self._mk("a")
        self.all_fail = {self.DE, self.DN}
        with self.assertRaises(RuntimeError) as cm:
            app._rvc_clean_dataset(ds, out, "light", job)
        self.assertIn("素材净化全部失败", str(cm.exception))
        self.assertIn("取消净化", str(cm.exception), "报错要给出路，不是只甩栈")
        self.assertEqual(list(out.iterdir()), [],
                         "全失败时输出目录必须空着——否则续跑会把未净化副本误判成已净化跳过")

    def test_resume_reuses_finished_purification(self):
        ds, out, job = self._mk("a", "b")
        out.mkdir(parents=True)
        for s in ("a", "b"):
            (out / f"{s}.wav").write_bytes(b"No Noise")
        info = app._rvc_clean_dataset(ds, out, "light", job, resume=True)
        self.assertEqual(self.cmds, [], "产物已齐就不该再跑任何推理")
        self.assertTrue(info["skipped"])

    def test_select_ignores_files_stolen_by_prefix_stems(self):
        d = self.tmp / "sel"
        d.mkdir()
        (d / "s0_Dry.wav").write_bytes(b"x")
        self.assertIsNone(app._rvc_clean_select(d, "s"),
                          "stem='s' 不许把 s0_Dry.wav 认成自己的声部（前缀撞名）")
        self.assertEqual(app._rvc_clean_select(d, "s0").name, "s0_Dry.wav")

    def test_tier_models_are_supported_in_vendored_catalog(self):
        cat = json.loads((app.RVC_DIR / "tools" / "pymss" / "resources"
                          / "model_catalog.json").read_text(encoding="utf-8"))
        for tier, models in app._RVC_CLEAN_TIERS.items():
            for m in models:
                hit = next((e for e in cat["models"]
                            if m == e["name"] or m in (e.get("aliases") or [])), None)
                self.assertIsNotNone(hit, f"{tier} 档模型 {m} 不在 PyMSS 目录里")
                self.assertTrue(hit["supported"])


class TestTrainSubmitClean(Sandbox):
    """A1 提交闸门：档位非法在落盘前 400；合法档位必须原样抵达任务与 worker 参数。"""

    def setUp(self) -> None:
        super().setUp()
        self._saved_sub = (app.RVC_MODELS_DIR, app.threading,
                           app._RVC_TRAIN_WORKER, app.backend_mode,
                           dict(app.RVC_TRAIN_JOBS))
        app.RVC_MODELS_DIR = self.tmp / "weights-clean"
        app.RVC_MODELS_DIR.mkdir()
        app.backend_mode = lambda: "cpu"
        app._RVC_TRAIN_WORKER = None
        app.RVC_TRAIN_JOBS.clear()
        self.started: list[tuple] = []

        class _FakeThread:
            def __init__(self, target=None, args=(), daemon=None):
                self.args, self.alive = args, False

            def start(self):
                self.alive = True

            def is_alive(self):
                return False
        self.thread_cls = _FakeThread
        app.threading = types.SimpleNamespace(
            Thread=lambda target=None, args=(), daemon=None: (
                self.started.append(args), _FakeThread())[1],
            Lock=threading.Lock)
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def tearDown(self) -> None:
        (app.RVC_MODELS_DIR, app.threading, app._RVC_TRAIN_WORKER,
         app.backend_mode) = self._saved_sub[:4]
        app.RVC_TRAIN_JOBS.clear()
        app.RVC_TRAIN_JOBS.update(self._saved_sub[4])
        super().tearDown()

    def _post(self, **data):
        form = {"name": "cleantest", "epochs": "30"}
        form.update({k: str(v) for k, v in data.items()})
        return self.client.post("/api/rvc/train",
                                files=[("files", ("s.wav", b"RIFF" + b"\0" * 400_000,
                                                  "audio/wav"))],
                                data=form)

    def test_bad_clean_tier_400_before_anything_lands(self):
        r = self._post(clean_tier="ultra")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("off/light/medium", r.json()["detail"])
        self.assertEqual(self.started, [], "坏档位不许把任务放进展队")

    def test_clean_tier_reaches_job_and_worker(self):
        r = self._post(clean_tier="light")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.started[0][-1], "light",
                         "worker 第 6 参就是净化档位（离线参数不许在传参处被吃掉）")
        with app.RVC_TRAIN_LOCK:
            job = next(iter(app.RVC_TRAIN_JOBS.values()))
        self.assertEqual(job["clean_tier"], "light")

    def test_default_is_off_for_existing_clients(self):
        r = self._post()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.started[0][-1], "off",
                         "不带新参数的老调用方（含 resume 老任务）行为不变")


class TestCheckupCleanAdvice(Sandbox):
    """A1 体检分档：底噪＝最安静 10% 帧的中位电平。必须能在噪声大到没有帧低于
    -45 静音门时照样报出高底噪（这正是分位数口径存在的理由）。"""

    def _scan_with(self, amp: float) -> dict:
        import numpy as np
        import soundfile as sf
        d = self.tmp / "ds"
        d.mkdir(exist_ok=True)
        sr = 40000
        t = np.arange(sr * 4) / sr
        seg = np.where(t < 2.0, 0.4 * np.sin(2 * np.pi * 220 * t),
                       np.random.default_rng(7).uniform(-amp, amp, sr * 4))
        sf.write(str(d / "s.wav"), seg.astype("float32"), sr)
        return app._rvc_dataset_scan(d)

    def test_noisy_gaps_still_measured_and_advised_medium(self):
        rep = self._scan_with(0.02)      # 安静段 RMS ≈ -39 dBFS：高于 -45 静音门
        self.assertTrue(rep["clean_advice"]["measured"],
                        "噪声大到没有'静音帧'时更得测出来，不能装看不见")
        self.assertEqual(rep["clean_advice"]["tier"], "medium")
        self.assertTrue(any("净化" in w or "底噪" in w for w in rep["warnings"]))

    def test_mild_noise_advises_light(self):
        rep = self._scan_with(0.006)     # RMS ≈ -49 dBFS
        self.assertEqual(rep["clean_advice"]["tier"], "light")

    def test_clean_material_advises_off_without_panic(self):
        rep = self._scan_with(1e-4)      # RMS ≈ -85 dBFS
        ca = rep["clean_advice"]
        self.assertEqual(ca["tier"], "off")
        self.assertTrue(any("混响" in r for r in ca["reasons"]),
                        "老实交代混响判不准，而不是宣布素材完美")


class TestF0Reference(Sandbox):
    """评审 J1：下载音色没有 2a_f0，C4 对本机 4/4 个现役音色 0% 生效。
    补救=传该音色本人一段歌建"参考音域"档案。锁三件事：建档真能解锁变调建议、
    训练实测永远优先且不许被参考覆盖（409）、来源标注不许撒谎。"""

    NAME = "试音.pth"

    def setUp(self):
        super().setUp()
        self._saved_f0r = (app.RVC_DIR, app.RVC_MODELS_DIR)
        app.RVC_DIR = self.tmp / "rvc"
        (app.RVC_DIR / "logs").mkdir(parents=True)
        app.RVC_MODELS_DIR = self.tmp / "weights"
        app.RVC_MODELS_DIR.mkdir()
        (app.RVC_MODELS_DIR / self.NAME).write_bytes(b"0")  # 只按文件名列举，不读内容
        app._RVC_F0STATS_MEM.clear()
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def tearDown(self):
        app._RVC_F0STATS_MEM.clear()
        app.RVC_DIR, app.RVC_MODELS_DIR = self._saved_f0r
        super().tearDown()

    def _sine(self, hz, name="v.wav"):
        import numpy as np
        import soundfile as sf
        sr = 22050
        t = np.arange(int(sr * 3.5)) / sr
        p = self.tmp / name
        sf.write(str(p), (0.5 * np.sin(2 * np.pi * hz * t)).astype("float32"), sr)
        return p

    def _post_ref(self, wav, name=NAME):
        return self.client.post(f"/api/rvc/models/{name}/f0-reference",
                                files={"file": (wav.name, wav.read_bytes(), "audio/wav")})

    def test_reference_unlocks_advice_and_is_labelled(self):
        r = self._post_ref(self._sine(220))
        self.assertEqual(r.status_code, 200, r.text)
        st = r.json()["stats"]
        self.assertEqual(st["source"], "reference", "参考档案必须自带来源标，不许冒充训练实测")
        self.assertTrue(200 <= st["median_hz"] <= 242)
        got = app._rvc_f0_stats(self.NAME)
        self.assertEqual(got["source"], "reference")
        # 建档后换声页的建议链路真的活了（源 440 → 目标 220 ≈ -12 半音）
        a = self.client.post("/api/rvc/pitch/advice",
                             files={"file": ("s.wav", self._sine(440, "s440.wav").read_bytes(),
                                             "audio/wav")},
                             data={"model": self.NAME})
        j = a.json()
        self.assertEqual(a.status_code, 200, a.text)
        self.assertTrue(j.get("suggestion"), "建档前 0% 生效、建档后必须出建议")
        self.assertIn("参考音频实测", j["text"], "文案要说清数字是参考音频量的，不是训练实测")

    def test_training_data_refuses_downgrade_by_reference(self):
        import numpy as np
        d = app.RVC_DIR / "logs" / self.NAME.removesuffix(".pth") / "2a_f0"
        d.mkdir(parents=True)
        np.save(d / "x.npy", np.full(500, 440.0))
        r = self._post_ref(self._sine(220))
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("不许", r.json()["detail"])
        self.assertFalse((d.parent / "f0_stats.json").is_file(),
                         "被拒的建档不能偷偷先把文件落下去")

    def test_training_wins_when_both_present(self):
        import numpy as np
        stem = self.NAME.removesuffix(".pth")
        side = app.RVC_DIR / "logs" / stem / "f0_stats.json"
        side.parent.mkdir(parents=True)
        side.write_text(json.dumps({"median_hz": 200.0, "p5_hz": 190.0, "p95_hz": 210.0,
                                    "source": "reference", "_stamp": 1}), encoding="utf-8")
        d = side.parent / "2a_f0"
        d.mkdir()
        np.save(d / "x.npy", np.full(500, 440.0))
        got = app._rvc_f0_stats(self.NAME)
        self.assertEqual((got["source"], got["median_hz"]), ("training", 440.0),
                         "几百切片的训练实测永远赢过一段 12 秒参考——不许降级")

    def test_empty_2a_f0_falls_back_to_reference(self):
        stem = self.NAME.removesuffix(".pth")
        side = app.RVC_DIR / "logs" / stem / "f0_stats.json"
        side.parent.mkdir(parents=True)
        side.write_text(json.dumps({"median_hz": 200.0, "source": "reference",
                                    "_stamp": 1}), encoding="utf-8")
        (side.parent / "2a_f0").mkdir()  # 空目录：一个 npy 都没留下
        got = app._rvc_f0_stats(self.NAME)
        self.assertEqual(got["source"], "reference",
                         "2a_f0 空壳不该把参考档案也挡没")

    def test_unknown_voice_404_and_silence_422_no_lie(self):
        r = self._post_ref(self._sine(220), name="查无此人.pth")
        self.assertEqual(r.status_code, 404)
        import numpy as np
        import soundfile as sf
        z = self.tmp / "z.wav"
        sf.write(str(z), np.zeros(22050 * 4, dtype="float32"), 22050)
        r = self._post_ref(z)
        self.assertEqual(r.status_code, 422, "量不出人声就 422，不许编一个档案")
        self.assertFalse((app.RVC_DIR / "logs" / "试音" / "f0_stats.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
