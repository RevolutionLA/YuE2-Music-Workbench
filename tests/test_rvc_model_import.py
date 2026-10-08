"""v2.0 §四-⑥ / §六 P1：外部音色入库的回归锁定（规划 §9.1 表第 4 行）。

覆盖：① 非 .pth 拒绝；② 同名冲突处理（不静默覆盖）；
③ 导入后强制建档音域（外部音色无 f0_stats.json 时不得跳过）；
④ 索引文件归属精确（不抢邻居）。

沿用 tests/test_review_fixes.py 的 Sandbox 基线，并把 RVC_DIR / RVC_MODELS_DIR /
索引目录都按住到临时沙箱；_rvc_f0_of_audio 换成替身——建档走的是 pyworld+ffmpeg，
真跑一次就把这轮回归拖成几十秒且换机器结果不同。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_review_fixes import LOCAL_BASE, Sandbox, app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

STATS = {"median_hz": 220.0, "p5_hz": 180.0, "p95_hz": 300.0,
         "p99_hz": 330.0, "voiced_frames": 320, "mean_hz": 225.0}


class ModelImportBase(Sandbox):
    def setUp(self) -> None:
        super().setUp()
        self._saved_imp = (app.RVC_DIR, app.RVC_MODELS_DIR, app._rvc_f0_of_audio,
                       app._win_toast, app._DISK_FLOOR_BYTES)
        app.RVC_DIR = self.tmp / "rvc"
        (app.RVC_DIR / "logs").mkdir(parents=True)
        app.RVC_MODELS_DIR = self.tmp / "assets" / "weights"
        app.RVC_MODELS_DIR.mkdir(parents=True)
        app._win_toast = lambda *a, **k: None
        app._DISK_FLOOR_BYTES = 0     # 沙箱所在分区剩余不足 2GB 时不该把用例挡成 507
        app._RVC_F0STATS_MEM.clear()
        self.f0_calls: list[str] = []
        app._rvc_f0_of_audio = lambda probe: (self.f0_calls.append(str(probe)) or dict(STATS))
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def tearDown(self) -> None:
        app._RVC_F0STATS_MEM.clear()
        (app.RVC_DIR, app.RVC_MODELS_DIR, app._rvc_f0_of_audio,
         app._win_toast, app._DISK_FLOOR_BYTES) = self._saved_imp
        super().tearDown()

    # ---- 小工具 ---- #
    def _post(self, pth_name="新人.pth", model_bytes=b"PTH-DATA", index=None,
              ref=None, data=None):
        files = {"model": (pth_name, model_bytes, "application/octet-stream")}
        if index is not None:
            name, blob = index
            files["index"] = (name, blob, "application/octet-stream")
        if ref is not None:
            name, blob = ref
            files["ref_audio"] = (name, blob, "audio/wav")
        return self.client.post("/api/rvc/models/import", files=files, data=data or {})

    def _ref(self, name="本人.wav"):
        return (name, b"RIFFfake")

    def _sidecar(self, stem: str) -> Path:
        return app.RVC_DIR / "logs" / stem / "f0_stats.json"

    def _add_training_f0(self, stem: str):
        import numpy as np
        d = app.RVC_DIR / "logs" / stem / "2b-f0nsf"
        d.mkdir(parents=True, exist_ok=True)
        np.save(d / "x.npy", np.full(400, 200.0))


class TestModelImportValidation(ModelImportBase):
    def test_rejects_non_pth(self):
        for fn in ("音色.onnx", "weights.safetensors", "readme.txt", "noext"):
            r = self._post(fn, ref=self._ref())
            self.assertEqual(r.status_code, 400, f"{fn}：{r.text}")
            self.assertIn("只接受 .pth", r.json()["detail"])

    def test_rejects_bad_names(self):
        for bad in ("../evil", "..\\evil", "a/b", "a\\b", "C:\\Windows\\evil",
                    "G_checkpoint", "D_something", ".hidden"):
            r = self._post("x.pth", ref=self._ref(), data={"name": bad})
            self.assertEqual(r.status_code, 400, f"{bad!r}：{r.text}")
        self.assertEqual(list(app.RVC_MODELS_DIR.glob("*.pth")), [],
                         "被拒的导入一个字节都不许落盘")

    def test_rejects_index_with_wrong_extension(self):
        r = self._post("新人.pth", index=("假索引.faiss", b"X"), ref=self._ref())
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn(".index", r.json()["detail"])
        self.assertEqual(list(app.RVC_MODELS_DIR.glob("*.pth")), [])


class TestModelImportConflict(ModelImportBase):
    def test_same_name_is_not_overwritten_silently(self):
        self.assertTrue(self._post("撞名.pth", ref=self._ref()).status_code == 200)
        target = app.RVC_MODELS_DIR / "撞名.pth"
        original = target.read_bytes()
        r = self._post("撞名.pth", model_bytes=b"NEW-CONTENT", ref=self._ref())
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("已存在", r.json()["detail"])
        self.assertEqual(target.read_bytes(), original, "冲突时旧音色字节必须原样保留")

    def test_overwrite_on_replaces_and_reports(self):
        self._post("撞名.pth", ref=self._ref())
        r = self._post("撞名.pth", model_bytes=b"NEW-CONTENT",
                       ref=self._ref(), data={"overwrite": "on"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["overwritten"])
        self.assertEqual((app.RVC_MODELS_DIR / "撞名.pth").read_bytes(), b"NEW-CONTENT")

    def test_first_import_is_not_reported_as_overwrite(self):
        r = self._post("全新.pth", ref=self._ref())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["overwritten"])


class TestModelImportForcesF0Reference(ModelImportBase):
    def test_external_model_without_ref_audio_is_rejected(self):
        r = self._post("外部.pth")        # 没有 ref_audio、没有训练 f0、没有旧 sidecar
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("建档", r.json()["detail"])
        self.assertFalse((app.RVC_MODELS_DIR / "外部.pth").exists(),
                         "建档条件不满足时整个导入都不该发生")

    def test_import_builds_sidecar_and_unlocks_range(self):
        r = self._post("外部.pth", ref=self._ref())
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertEqual(j["f0_reference"]["source"], "reference")
        self.assertTrue(j["f0_reference"]["ok"])
        self.assertEqual(len(self.f0_calls), 1, "导入必须真的走一次建档，不能只是记一笔")
        side = self._sidecar("外部")
        self.assertTrue(side.is_file())
        rec = json.loads(side.read_text(encoding="utf-8"))
        self.assertEqual(rec["source"], "reference")
        self.assertEqual(rec["median_hz"], STATS["median_hz"])
        # 音域立刻对换声页可用（/_rvc_f0_stats 是唯一口径，不许另算一份）
        got = app._rvc_f0_stats("外部.pth")
        self.assertEqual(got["source"], "reference")
        self.assertIn("外部.pth", self.client.get("/api/rvc/models").json()["models"])

    def test_existing_sidecar_may_be_kept_but_says_so(self):
        side = self._sidecar("旧档")
        side.parent.mkdir(parents=True, exist_ok=True)
        side.write_text(json.dumps({**STATS, "source": "reference"}), encoding="utf-8")
        r = self._post("旧档.pth")        # 不带 ref_audio：允许，但必须说清不是新测的
        self.assertEqual(r.status_code, 200, r.text)
        f0 = r.json()["f0_reference"]
        self.assertFalse(f0["attempted"])
        self.assertIn("沿用", f0["note"])
        self.assertEqual(len(self.f0_calls), 0)

    def test_training_f0_is_never_downgraded_by_import(self):
        self._add_training_f0("本机练的")
        r = self._post("本机练的.pth", ref=self._ref())
        self.assertEqual(r.status_code, 200, r.text)
        f0 = r.json()["f0_reference"]
        self.assertEqual(f0["source"], "training")
        self.assertFalse(f0["attempted"])
        self.assertFalse(self._sidecar("本机练的").exists(),
                         "有训练实测时连参考档案都不许落盘")
        self.assertEqual(len(self.f0_calls), 0)

    def test_f0_failure_is_reported_not_hidden(self):
        def boom(probe):
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="有声帧太少，量不出可信音域")

        app._rvc_f0_of_audio = boom
        r = self._post("含糊.pth", ref=self._ref())
        self.assertEqual(r.status_code, 200, r.text)
        j = r.json()
        self.assertFalse(j["f0_reference"]["ok"])
        self.assertIn("422", j["warning"])
        self.assertIn("f0-reference", j["warning"], "要给出可重试的确切路径")
        self.assertFalse(self._sidecar("含糊").exists())

    def test_ref_audio_extension_is_validated_by_shared_builder(self):
        # 建档复用 f0-reference 的实现：参考音频超上限/读不出来的那一路要如实回传
        r = self._post("坏参考.pth", ref=("本人.txt", b"not audio"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.f0_calls), 1, "扩展名不在白名单时按 .wav 落地后仍要真跑建档")


class TestModelIndexOwnership(ModelImportBase):
    def test_loose_index_name_is_rewritten_to_owned(self):
        r = self._post("王菲.pth", index=("anything.index", b"IDX"), ref=self._ref())
        self.assertEqual(r.status_code, 200, r.text)
        name = r.json()["index"]
        self.assertEqual(name, "王菲_added_王菲_v2.index")
        self.assertTrue(app._rvc_index_owned("王菲", name))
        idx_dir = app._rvc_index_dirs()[0]
        self.assertTrue((idx_dir / name).is_file(), "索引必须落在推理侧会看的第一目录")

    def test_client_owned_name_is_kept(self):
        own = "王菲_added_IVF2_flat_ip64_王菲_v2.index"
        r = self._post("王菲.pth", index=(own, b"IDX"), ref=self._ref())
        self.assertEqual(r.json()["index"], own)

    def test_imported_index_does_not_steal_neighbour(self):
        """王菲 与 王菲V6 同时在库时，检索库不许串台（历史事故：按文件名排序抢走邻居的）。"""
        self._post("王菲.pth", index=("anything.index", b"IDX-WANGFEI"), ref=self._ref())
        self._post("王菲V6.pth", index=("anything.index", b"IDX-V6"), ref=self._ref())
        wf = app._rvc_index_files("王菲")
        v6 = app._rvc_index_files("王菲V6")
        self.assertTrue(any("王菲_added_王菲_v2.index" == p.name for p in wf),
                        f"王菲 必须找到自己那一份：{[p.name for p in wf]}")
        # 排第一的必须是"严格属于自己的"那份，绝不能是邻居的
        self.assertEqual(wf[0].name, "王菲_added_王菲_v2.index")
        self.assertEqual(v6[0].name, "王菲V6_added_王菲V6_v2.index")
        self.assertNotEqual(wf[0].name, v6[0].name)

    def test_index_conflict_is_not_overwritten_silently(self):
        # 场景：.pth 早先被删过、检索库还留在 assets/indices 里（历史孤儿），
        # 这时重新入库同名音色——索引不许被静默盖掉
        idx_dir = app._rvc_index_dirs()[0]
        idx_dir.mkdir(parents=True, exist_ok=True)
        orphan = "甲_added_甲_v2.index"
        (idx_dir / orphan).write_bytes(b"OLD")
        r = self._post("甲.pth", index=(orphan, b"NEW"), ref=self._ref())
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("已存在", r.json()["detail"])
        self.assertEqual((idx_dir / orphan).read_bytes(), b"OLD")


if __name__ == "__main__":
    unittest.main()
