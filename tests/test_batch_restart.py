"""批量队列「失败任务重启」的回归用例。

锁住 2026-10-22 用户报的那两件事：
  ① 队列正在跑时，失败的条目也必须能单独/一键重启（以前 /batch/retry 一律 409）；
  ② 一键重启只能碰当前队列，历史队列的旧失败不能被一起翻出来重跑。

只读写临时目录，不触发任何生成：worker 被替换成空函数，只验状态机。

    py312\\python.exe -m unittest discover -s tests -p "test_batch_restart.py" -v
"""
from __future__ import annotations

import json
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

# app.py 只用到 main.app / main.settings；闭源 pyd 不入库，这里给替身
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
from fastapi.testclient import TestClient  # noqa: E402


class BatchRestartBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="yue2-batch-")
        self.tmp = Path(self._tmp.name)
        self._saved_state = app._BATCH_STATE
        self._saved_ensure = app._batch_ensure_worker
        self._saved_settings_json = app._SETTINGS_JSON
        self._saved_local_env = app._local_env_value
        self._saved_deepseek = app._DEEPSEEK_KEY_FILE
        self._saved_lrc_dir = app.LRC_STANDALONE_DIR
        self._saved_lrc_align = app.lrc_align_mod
        app._BATCH_STATE = self.tmp / "batch_state.json"
        app._SETTINGS_JSON = self.tmp / "settings.json"
        app._DEEPSEEK_KEY_FILE = self.tmp / "deepseek_api_key.txt"
        app.LRC_STANDALONE_DIR = self.tmp / "lrc_jobs"
        app._local_env_value = lambda name, path=None: ""
        self.ensure_calls = 0

        def _ensure():
            self.ensure_calls += 1

        app._batch_ensure_worker = _ensure
        self.client = TestClient(app.app, base_url=f"http://127.0.0.1:{app.settings.app_port}")

    def tearDown(self) -> None:
        app._BATCH_STATE = self._saved_state
        app._batch_ensure_worker = self._saved_ensure
        app._SETTINGS_JSON = self._saved_settings_json
        app._local_env_value = self._saved_local_env
        app._DEEPSEEK_KEY_FILE = self._saved_deepseek
        app.LRC_STANDALONE_DIR = self._saved_lrc_dir
        app.lrc_align_mod = self._saved_lrc_align
        self._tmp.cleanup()

    def write(self, state: dict) -> None:
        app._BATCH_STATE.parent.mkdir(parents=True, exist_ok=True)
        app._BATCH_STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def read(self) -> dict:
        return json.loads(app._BATCH_STATE.read_text(encoding="utf-8"))

    @staticmethod
    def item(rid: str, status: str, qid: str = "q-current", seed=None, **kw) -> dict:
        payload = {"style": "pop", "lyrics": "[Verse]\nhi"}
        if seed is not None:
            payload["seed"] = seed
        return {"id": rid, "qid": qid, "name": rid, "status": status,
                "payload": payload, **kw}


class MaterializeSeed(BatchRestartBase):
    def test_stored_seed_is_reused_on_retry(self):
        # 改回"每次派发重新抽"→ 重试时拿到新种子，检查点永远对不上，这里红
        self.assertEqual(app._resolve_batch_seed(902102507, -1), 902102507)
        self.assertEqual(app._resolve_batch_seed(902102507, None), 902102507)

    def test_explicit_seed_wins_over_stored(self):
        self.assertEqual(app._resolve_batch_seed(1, 123), 123)

    def test_random_seed_is_materialized(self):
        seed = app._resolve_batch_seed(None, -1)
        self.assertIsInstance(seed, int)
        self.assertGreaterEqual(seed, 0)

    def test_worker_persists_seed_and_does_not_deadlock(self):
        """跑真实 worker（子进程里，防止死锁把整个测试进程锁住）。

        锁内再调一个自己加锁的函数 → worker 卡死、/batch/status 超时；
        这条用 join 超时 + 父进程 timeout 双重兜底，改坏会红而不是挂住全套。
        """
        import subprocess
        import sys as _sys
        state = self.tmp / "worker_state.json"
        state.write_text(json.dumps({
            "qid": "q", "name": "q", "running": True, "current": None,
            "items": [self.item("it", "pending", seed=-1)],
        }, ensure_ascii=False), encoding="utf-8")
        script = self.tmp / "run_worker.py"
        script.write_text(
            "import json, os, sys, threading, types\n"
            "from pathlib import Path\n"
            "ROOT, STATE = Path(sys.argv[1]), Path(sys.argv[2])\n"
            "sys.path[:0] = [str(ROOT), str(ROOT / 'src')]\n"
            "from fastapi import FastAPI\n"
            "stub = types.ModuleType('main'); stub.app = FastAPI()\n"
            "stub.settings = types.SimpleNamespace(open_browser=True)\n"
            "sys.modules['main'] = stub\n"
            "for n in ('voices', 'asr', 'denoise', 'lrc'):\n"
            "    sys.modules.setdefault(n, types.ModuleType(n))\n"
            "import app\n"
            "app._BATCH_STATE = STATE\n"
            "app.OUTPUT_DIR = STATE.parent / 'output'; app.OUTPUT_DIR.mkdir()\n"
            "def fake_gen(job, payload):\n"
            "    app._output_write_meta({**job, 'status': 'done', 'sec': 1, 'bytes': 1})\n"
            "    app._gen_set_job(None)\n"
            "app._gen_run = fake_gen\n"
            "t = threading.Thread(target=app._batch_run_worker, daemon=True)\n"
            "t.start(); t.join(8)\n"
            "if t.is_alive():\n"
            "    print('DEADLOCK'); os._exit(3)\n"
            "st = json.loads(STATE.read_text(encoding='utf-8'))\n"
            "print(json.dumps({'seed': st['items'][0]['payload'].get('seed'),\n"
            "                  'status': st['items'][0]['status'], 'running': st['running']}))\n",
            encoding="utf-8")
        r = subprocess.run([_sys.executable, str(script), str(ROOT), str(state)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertGreaterEqual(out["seed"], 0)
        self.assertEqual(out["status"], "done")
        self.assertFalse(out["running"])


class SettingsAndPreflight(BatchRestartBase):
    def test_cpu_fallback_setting_roundtrip(self):
        r = self.client.post("/api/settings", json={"cpu_fallback_allowed": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(app._cpu_fallback_allowed())
        self.assertEqual(self.client.get("/api/settings").json()["cpu_fallback_allowed"], True)
        self.client.post("/api/settings", json={"cpu_fallback_allowed": False})
        self.assertFalse(app._cpu_fallback_allowed())

    def test_invalid_setting_is_400(self):
        self.assertEqual(self.client.post("/api/settings", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/settings",
                                          json={"cpu_fallback_allowed": "yes"}).status_code, 400)

    def test_preflight_blocks_long_song_only_with_big_vae_window(self):
        """预检要跟着**实际 VAE 窗口**走。

        512 窗口是分块解码：缓冲固定约 1.95GB，不再随帧数线性涨，长歌能过；
        1024 窗口（官方默认）按帧数涨，600 字约 7200 帧必超本机预算。
        2026-10-23 之前的写法把两件事混成一条（一律按 1024 估），
        于是在 512 窗口下把本来跑得动的歌提前拦掉了。
        """
        saved = (app.backend_mode, app._vulkan_heap_budget_gb, app._cpu_fallback_allowed,
                 app._read_vae_config, app._VAE_CONFIG_PATHS)
        app.backend_mode = lambda: "vulkan"
        app._vulkan_heap_budget_gb = lambda: 7.26
        app._VAE_CONFIG_PATHS = [Path("stub/yue2-vae-config.json")]
        try:
            # 512 窗口：长歌放行
            app._read_vae_config = lambda p: {"decode_core_frames": 512}
            app._cpu_fallback_allowed = lambda: False
            long_song = app._preflight_memory_estimate({"lyrics": "字" * 600})
            self.assertTrue(long_song["ok"], long_song)

            # 1024 窗口：长歌拦下，短歌放行
            app._read_vae_config = lambda p: {"decode_core_frames": 1024}
            long_song = app._preflight_memory_estimate({"lyrics": "字" * 600})
            self.assertFalse(long_song["ok"], long_song)
            self.assertIn("超过本机 Vulkan 预算", long_song["message"])
            short_song = app._preflight_memory_estimate({"lyrics": "字" * 150})
            self.assertTrue(short_song["ok"], short_song)

            # 开了 CPU 回退时不能硬拦：改为允许并给 warning（用户显式选择了慢路径）
            app._cpu_fallback_allowed = lambda: True
            slow = app._preflight_memory_estimate({"lyrics": "字" * 600})
            self.assertTrue(slow["ok"], slow)
            self.assertTrue(slow["warning"])
        finally:
            (app.backend_mode, app._vulkan_heap_budget_gb, app._cpu_fallback_allowed,
             app._read_vae_config, app._VAE_CONFIG_PATHS) = saved

    def test_deepseek_key_file_is_gitignored(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("secrets/deepseek_api_key.txt", ignore,
                      "DeepSeek Key 文件必须在 .gitignore 里，绝不能跟随提交")

    def test_capabilities_names_vae_free_features(self):
        j = self.client.get("/api/capabilities").json()
        labels = [f["label"] for f in j.get("features") or []]
        self.assertIn("歌词 TXT 导出", labels)
        self.assertIn("乐谱提取 / 转谱", labels)
        self.assertIn("歌曲生成（AR/NAR/VAE）", labels)

    def test_pymss_device_never_passes_vulkan(self):
        # PyMSS 的 argparse 只收 auto/cpu/cuda/mps/mlx；传 vulkan 会直接 usage 退出
        saved = app.backend_mode
        try:
            app.backend_mode = lambda: "vulkan"
            self.assertEqual(app._pymss_device(), "cpu")
            app.backend_mode = lambda: "cuda"
            self.assertEqual(app._pymss_device(), "cuda")
        finally:
            app.backend_mode = saved


class DeepseekSetting(BatchRestartBase):
    def test_save_and_clear_never_returns_the_key(self):
        r = self.client.post("/api/settings", json={"deepseek_api_key": "sk-test-123"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(app._DEEPSEEK_KEY_FILE.is_file())
        j = self.client.get("/api/settings").json()
        self.assertTrue(j["deepseek_api_key_set"])
        self.assertNotIn("deepseek_api_key", j, "接口绝不能把 Key 回传给前端")
        r = self.client.post("/api/settings", json={"deepseek_api_key_clear": True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(app._DEEPSEEK_KEY_FILE.exists())
        self.assertFalse(self.client.get("/api/settings").json()["deepseek_api_key_set"])


class LrcStandalone(BatchRestartBase):
    def setUp(self):
        super().setUp()

        class FakeAligner:
            @staticmethod
            def align(wav, lyrics, snap_window=1.2):
                return {"lrc": "[00:00.00]hi\n[00:01.00]there\n",
                        "elrc": "[00:00.00]hi(0,500)\n",
                        "method": "fake-align", "diagnostics": {"lines": 2}}

        app.lrc_align_mod = FakeAligner

    def _wait(self, rid):
        for _ in range(100):
            st = self.client.get(f"/api/lrc/status/{rid}").json()
            if st.get("status") in ("done", "error"):
                return st
            time.sleep(0.05)
        self.fail("歌词对齐任务 5 秒内没有进入终态")

    def test_upload_generates_lrc_and_elrc(self):
        r = self.client.post(
            "/api/lrc/start",
            files={"file": ("song.wav", b"RIFF" + b"0" * 64, "audio/wav")},
            data={"lyrics": "[Verse]\nhi\nthere", "task_name": "测试歌曲"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        rid = r.json()["id"]
        st = self._wait(rid)
        self.assertEqual(st["status"], "done", st)
        self.assertIn("[00:00.00]hi", st["lrc"])
        self.assertTrue(st["has_elrc"])
        self.assertIn("hi(0,500)", st["elrc"])
        lrc = self.client.get(f"/api/lrc/download/{rid}?fmt=lrc")
        self.assertEqual(lrc.status_code, 200)
        self.assertIn("[00:00.00]hi", lrc.text)
        elrc = self.client.get(f"/api/lrc/download/{rid}?fmt=elrc")
        self.assertEqual(elrc.status_code, 200)
        self.assertIn("(0,500)", elrc.text)
        ids = [x["id"] for x in self.client.get("/api/lrc/list").json()["items"]]
        self.assertIn(rid, ids)

    def test_rejects_empty_lyrics_and_bad_extension(self):
        r = self.client.post(
            "/api/lrc/start",
            files={"file": ("song.wav", b"RIFF", "audio/wav")},
            data={"lyrics": "  "},
        )
        self.assertEqual(r.status_code, 400)
        r = self.client.post(
            "/api/lrc/start",
            files={"file": ("song.exe", b"MZ", "application/octet-stream")},
            data={"lyrics": "hi"},
        )
        self.assertEqual(r.status_code, 400)


class RetryWhileRunning(BatchRestartBase):
    def test_retry_failed_item_does_not_stop_current_song(self):
        # 改回旧的 409 拦截 → 这里红
        self.write({"qid": "q-current", "name": "等你回来", "running": True,
                    "current": "cur", "items": [
                        self.item("cur", "running"),
                        self.item("failed", "error", error="alloc failed"),
                    ]})
        r = self.client.post("/api/batch/retry", json={"id": "failed"})
        self.assertEqual(r.status_code, 200, r.text)
        st = self.read()
        self.assertTrue(st["running"])
        self.assertEqual(st["current"], "cur")
        self.assertEqual(st["items"][0]["status"], "running")
        self.assertEqual(st["items"][1]["status"], "pending")
        self.assertNotIn("error", st["items"][1])
        self.assertEqual(self.ensure_calls, 1)
        self.assertIn("当前", r.json()["message"])

    def test_retry_refuses_the_item_currently_generating(self):
        # 去掉 current 判定 → 正在算的歌会被排成 pending，跑完可能重复
        self.write({"qid": "q-current", "name": "等你回来", "running": True,
                    "current": "cur", "items": [self.item("cur", "running")]})
        r = self.client.post("/api/batch/retry", json={"id": "cur"})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(self.read()["items"][0]["status"], "running")

    def test_retry_unknown_id_is_404(self):
        self.write({"qid": "q-current", "name": "q", "running": True,
                    "current": "cur", "items": [self.item("cur", "running")]})
        self.assertEqual(self.client.post("/api/batch/retry", json={"id": "nope"}).status_code, 404)


class ResumeFailedItems(BatchRestartBase):
    def test_resume_while_running_requeues_only_current_queue(self):
        # 全量复位（不按 qid 过滤）→ 历史旧队列那条也会变 pending，这里红
        self.write({"qid": "q-current", "name": "等你回来", "running": True,
                    "current": "cur", "items": [
                        self.item("cur", "running"),
                        self.item("e1", "error", error="oom"),
                        self.item("c1", "cancelled"),
                        self.item("old", "error", qid="q-old"),
                    ]})
        r = self.client.post("/api/batch/resume")
        self.assertEqual(r.status_code, 200, r.text)
        st = self.read()
        by_id = {it["id"]: it for it in st["items"]}
        self.assertEqual(by_id["cur"]["status"], "running", "正在算的那条不许被复位")
        self.assertEqual(by_id["e1"]["status"], "pending")
        self.assertNotIn("error", by_id["e1"])
        self.assertEqual(by_id["c1"]["status"], "pending")
        self.assertEqual(by_id["old"]["status"], "error", "历史队列的失败不许被一起重启")
        self.assertTrue(st["running"])
        self.assertEqual(r.json()["restored"], 2)
        self.assertEqual(self.ensure_calls, 1)

    def test_resume_when_stopped_starts_the_queue(self):
        self.write({"qid": "q-current", "name": "q", "running": False,
                    "current": None, "items": [
                        self.item("e1", "error"), self.item("done1", "done"),
                    ]})
        r = self.client.post("/api/batch/resume")
        self.assertEqual(r.status_code, 200, r.text)
        st = self.read()
        self.assertTrue(st["running"])
        self.assertEqual(st["items"][0]["status"], "pending")
        self.assertEqual(st["items"][1]["status"], "done")
        self.assertEqual(self.ensure_calls, 1)

    def test_resume_with_nothing_to_do_does_not_start_worker(self):
        self.write({"qid": "q-current", "name": "q", "running": False,
                    "current": None, "items": [self.item("done1", "done")]})
        r = self.client.post("/api/batch/resume")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["restored"], 0)
        self.assertEqual(self.ensure_calls, 0)


if __name__ == "__main__":
    unittest.main()
