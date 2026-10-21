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
        app._BATCH_STATE = self.tmp / "batch_state.json"
        self.ensure_calls = 0

        def _ensure():
            self.ensure_calls += 1

        app._batch_ensure_worker = _ensure
        self.client = TestClient(app.app, base_url=f"http://127.0.0.1:{app.settings.app_port}")

    def tearDown(self) -> None:
        app._BATCH_STATE = self._saved_state
        app._batch_ensure_worker = self._saved_ensure
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
