"""回归用例：adversarial-review 308f833 一轮整改的锁定测试。

只碰临时目录与只读端点，绝不触发任何计算（不发 /api/generate/*、/api/batch/start、
/rvc/*、/train/* 的真实任务），因此 CPU/GPU 正在跑歌时也可以放心执行：

    py312\\python.exe -m unittest discover -s tests -v

每个用例注释里标了对应的蓝军编号（B/D/S/Y），便于评审报告逐条回溯。
"""
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
