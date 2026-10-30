"""生成质量基准（issue #12）的离线用例：样例集结构、dry-run、归档与清单。

全部离线：不打网关、不占显卡。真跑基准用 bench/run_bench.py，人工听感打分在
docs/BENCHMARK.md 里完成。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("run_bench", ROOT / "bench" / "run_bench.py")
run_bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_bench)


class BenchPromptsTest(unittest.TestCase):
    def setUp(self):
        self.doc = run_bench.load_set()

    def test_five_fixed_groups_with_unique_ids(self):
        groups = self.doc["groups"]
        self.assertEqual(len(groups), 5, "样本集固定 5 组，扩了就没有历史可比性")
        ids = [g["id"] for g in groups]
        self.assertEqual(len(ids), len(set(ids)))

    def test_params_are_pinned(self):
        for g in self.doc["groups"]:
            self.assertIsInstance(g["seed"], int, f"{g['id']} 的 seed 必须是整数")
            self.assertNotEqual(g["seed"], -1, "seed=-1 是随机，基准不能用")
            self.assertIn(g["cot"], ("off", "melody"), f"{g['id']} 的 cot 只能是 off/melody")
            self.assertEqual(g["steps"], 8, "基准固定 8 步，改了就不可比")
            self.assertEqual(float(g["cfg_scale"]), 1.0)
            self.assertIsInstance(g["enabled"], bool)
            self.assertTrue(str(g["style"]).strip())
            self.assertTrue(str(g["lyrics"]).strip())

    def test_cover_group_is_disabled_until_asset_arrives(self):
        cover = next(g for g in self.doc["groups"] if g["id"] == "cover")
        self.assertFalse(cover["enabled"], "没有固定参考音频之前，翻唱组不能启用")
        self.assertTrue(cover.get("reason"))
        self.assertIn("bench/assets/", cover.get("asset") or "")

    def test_build_tasks_pins_every_field(self):
        groups = run_bench.enabled_groups(self.doc)
        tasks = run_bench.build_tasks(groups)
        self.assertEqual(len(tasks), 4)
        for g, t in zip(groups, tasks):
            self.assertEqual(t["name"], f"bench-{g['id']}")
            self.assertEqual(t["seed"], g["seed"])
            self.assertEqual(t["cot"], g["cot"])
            self.assertEqual(t["num_inference_steps"], g["steps"])
            self.assertEqual(t["cfg_scale"], 1.0)
            self.assertEqual(t["model"], "yue2")

    def test_cover_group_cannot_ride_the_text_runner(self):
        cover = next(g for g in self.doc["groups"] if g["id"] == "cover")
        with self.assertRaises(ValueError):
            run_bench.build_tasks([cover])


class BenchDryRunTest(unittest.TestCase):
    def test_dry_run_lists_enabled_groups_and_skips_cover(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run_bench.main(["--dry-run"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        for gid in ("zh_pop", "en_pop", "mixed_pop", "instrumental"):
            self.assertIn(gid, out)
        self.assertIn("跳过", out)
        self.assertIn("cover", out)

    def test_unknown_only_group_fails_cleanly(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = run_bench.main(["--dry-run", "--only", "no_such_group"])
        self.assertEqual(rc, 3)
        self.assertIn("未知组", err.getvalue())


class BenchArchiveTest(unittest.TestCase):
    def test_archive_copies_audio_and_records_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "src"
            src.mkdir()
            (src / "20260101_000000_deadbeef.wav").write_bytes(b"RIFF" + b"\0" * 64)
            (src / "20260101_000000_deadbeef.json").write_text(
                json.dumps({"id": "20260101_000000_deadbeef"}), encoding="utf-8")
            entry = run_bench.archive_group("zh_pop", "20260101_000000_deadbeef",
                                            tmp / "out", src_dir=src)
            self.assertTrue((tmp / "out" / "zh_pop.wav").is_file())
            self.assertTrue((tmp / "out" / "zh_pop.json").is_file())
            self.assertEqual(entry["bytes"], 68)
            self.assertEqual(len(entry["sha256"]), 64)

    def test_archive_missing_audio_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                run_bench.archive_group("zh_pop", "missing", Path(tmp) / "out",
                                        src_dir=Path(tmp))

    def test_manifest_is_written_where_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = run_bench.load_set()
            path = run_bench.write_manifest(
                "v9.9.9", doc,
                [{"id": "zh_pop", "status": "done", "bytes": 1, "sha256": "x"}],
                {"version": "v9.9.9", "gateway_kernel": {"kind": "shim"}},
                "http://127.0.0.1:7863", results_dir=Path(tmp))
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["label"], "v9.9.9")
            self.assertEqual(data["gateway_version"], "v9.9.9")
            self.assertEqual(data["quality"], "draft（off/8）")
            self.assertEqual(len(data["groups"]), 1)


class BenchArchiveOnlyTest(unittest.TestCase):
    """轮询被打断时，--archive-only 只归档、不重复提交（2026-10-30 实测踩到超时后加的）。"""

    def _args(self, **over):
        args = run_bench.argparse.Namespace(
            dry_run=False, list=False, only="", base="http://fake", label="",
            prompts="", timeout=1.0, poll=0.01, keep_library=True, archive_only=True)
        for k, v in over.items():
            setattr(args, k, v)
        return args

    def test_archive_only_copies_terminal_items_and_writes_manifest(self):
        doc = run_bench.load_set()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            lib = tmp / "lib"
            lib.mkdir()
            items = []
            for g in doc["groups"]:
                if not g.get("enabled"):
                    continue
                rid = f"rid_{g['id']}"
                (lib / f"{rid}.wav").write_bytes(b"RIFF" + b"\0" * 32)
                (lib / f"{rid}.json").write_text("{}", encoding="utf-8")
                items.append({"id": rid, "name": f"bench-{g['id']}", "status": "done"})

            def fake_api(base, method, path, payload=None, timeout=30.0):
                if path == "/api/system/info":
                    return {"version": "v9.9.9", "gateway_kernel": {"kind": "shim"}}
                if path == "/api/batch/status":
                    return {"items": items}
                if path.startswith("/api/generate/"):
                    raise AssertionError("keep_library=True 时不该删历史")
                raise AssertionError(f"意外请求 {path}")

            with mock.patch.object(run_bench, "api", fake_api), \
                    mock.patch.object(run_bench, "OUTPUT_ROOT", tmp / "out"), \
                    mock.patch.object(run_bench, "LIB_OUTPUT", lib), \
                    mock.patch.object(run_bench, "RESULTS_DIR", tmp / "results"):
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = run_bench.run(self._args())
            self.assertEqual(rc, 0)
            self.assertTrue((tmp / "out" / "v9.9.9" / "zh_pop.wav").is_file())
            manifest = json.loads((tmp / "results" / "v9.9.9.json").read_text(encoding="utf-8"))
            self.assertEqual({g["id"] for g in manifest["groups"]},
                             {"zh_pop", "en_pop", "mixed_pop", "instrumental"})


if __name__ == "__main__":
    unittest.main()
