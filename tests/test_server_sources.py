"""⑨换声 / ⑦音色制作的「从服务器自己的库取输入」后端锁。

新原则：每个页面只从本机库里点名，不再由前端把 A 页的文件递给 B 页。落到后端就是
一个标识解析器 `_server_audio_path()` + 三个吃标识的入口（/rvc/convert 的 source_id、
/rvc/train/check 与 /rvc/train 的 source_ids）。这里锁四件事：

1. 拒绝面（防目录穿越）：只认 `<rid>` 与 `<rid>|<part>` 两种写法，非法输入一律 4xx，
   而且**不创建任何目录/文件**——参数与标识都在建目录之前规整完；
2. 正面证据：同一段 wav，走 source_id 与走 multipart 上传，进到换声流水线之前的
   落盘位置与字节完全一致（真实 worker/子进程全部桩掉，绝不碰 GPU）；
3. 体积闸门不许被绕过：复制进来的字节与上传一样计入 budget / _MAX_UPLOAD_TOTAL；
4. 换声结果（meta kind=rvc）不能当源，与 POST /api/rvc/convert/{rid} 同口径。

跑法（CPU 也行，全程不落任何真实任务）：
    py312\\python.exe -m unittest tests.test_server_sources
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from _env import HAS_RVC_ENV, SKIP_RVC  # noqa: E402
from test_review_fixes import LOCAL_BASE, Sandbox, app  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SR = 40000
MODEL = "孙燕姿.pth"


def wav_bytes(seconds: float, amp: float = 0.2, sr: int = SR) -> bytes:
    """真 wav（40kHz 单声道 PCM16）：sf.info 要能读出时长，体检也要能读。"""
    import soundfile as sf
    n = int(sr * seconds)
    t = np.arange(n) / sr
    x = (amp * np.sin(2 * np.pi * 220 * t)).astype("float32")
    buf = io.BytesIO()
    sf.write(buf, x, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def snapshot(*dirs: Path) -> list[str]:
    """目录树快照（相对路径 + 大小）：用来证明"失败的调用什么都没留下"。"""
    out: list[str] = []
    for d in dirs:
        if not Path(d).is_dir():
            continue
        for p in sorted(Path(d).rglob("*")):
            out.append(f"{p.relative_to(Path(d))}|{p.stat().st_size if p.is_file() else 'dir'}")
    return out


class ServerSourceBase(Sandbox):
    """Sandbox 已经把 OUTPUT_DIR / RVC_JOB_DIR / RVC_TRAIN_DIR / 索引目录都指到临时目录。"""

    def setUp(self) -> None:
        super().setUp()
        # 注意别覆盖 Sandbox._saved（那是临时目录还原用的字典）
        self._saved_rvc = (app._rvc_models, app._rvc_submit, app._rvc_train_worker,
                           dict(app._RVC_JOBS), list(app._RVC_QUEUE),
                           dict(app.RVC_TRAIN_JOBS))
        self.seen: list[tuple] = []
        self.train_starts: list[tuple] = []
        app._rvc_models = lambda: [MODEL]
        app._rvc_submit = lambda args: (self.seen.append(args), 1)[1]

        def fake_train_worker(rid, name, epochs, *a, **kw):
            self.train_starts.append((rid, name, epochs, a, kw))

        app._rvc_train_worker = fake_train_worker
        app._RVC_JOBS.clear()
        app._RVC_QUEUE.clear()
        app.RVC_TRAIN_JOBS.clear()
        self.client = TestClient(app.app, base_url=LOCAL_BASE)

    def tearDown(self) -> None:
        app._rvc_models, app._rvc_submit, app._rvc_train_worker = self._saved_rvc[:3]
        app._RVC_JOBS.clear()
        app._RVC_JOBS.update(self._saved_rvc[3])
        app._RVC_QUEUE[:] = self._saved_rvc[4]
        app.RVC_TRAIN_JOBS.clear()
        app.RVC_TRAIN_JOBS.update(self._saved_rvc[5])
        super().tearDown()

    def put_output(self, rid: str, data: bytes, kind: str = "generate",
                   parts: tuple[str, ...] = ()) -> dict[str, Path]:
        """在 output/ 里造一份"本机已有音频"：整曲 + 可选的⑩分离产物 + 对应 meta。"""
        paths = {}
        main = app.OUTPUT_DIR / f"{rid}.wav"
        main.write_bytes(data)
        paths[""] = main
        assets = {}
        for part in parts:
            p = app.OUTPUT_DIR / f"{rid}_{part}.wav"
            p.write_bytes(data)
            paths[part] = p
            assets[part] = p.name
        (app.OUTPUT_DIR / f"{rid}.json").write_text(
            json.dumps({"id": rid, "kind": kind, "status": "done", "assets": assets}),
            encoding="utf-8")
        return paths


class TestServerAudioPath(ServerSourceBase):
    """helper 本体：标识 → 本机绝对路径。拒绝面必须严，且一个文件都不许多。"""

    RID = "20260101_000000_abcd1234"

    def test_plain_rid_resolves_to_output_wav(self):
        paths = self.put_output(self.RID, wav_bytes(1.0))
        self.assertEqual(app._server_audio_path(self.RID), paths[""])
        self.assertEqual(app._server_audio_path(f"  {self.RID}  "), paths[""], "首尾空白不算非法")

    def test_part_spec_resolves_to_separation_product(self):
        paths = self.put_output(self.RID, wav_bytes(1.0),
                                parts=("vocals", "vocals_noharmony", "accompaniment"))
        for part in ("vocals", "vocals_noharmony", "accompaniment"):
            self.assertEqual(app._server_audio_path(f"{self.RID}|{part}"), paths[part])

    def test_traversal_and_malformed_specs_are_refused_without_touching_disk(self):
        self.put_output(self.RID, wav_bytes(1.0), parts=("vocals",))
        before = snapshot(app.OUTPUT_DIR, app.RVC_JOB_DIR, app.RVC_TRAIN_DIR)
        bad = [
            "", "   ",                          # 空标识
            "..%2F..%2Fapp.py",                 # URL 编码后的穿越（表单里是字面 %2F）
            "../../app.py", "..\\..\\app.py",   # 真分隔符穿越
            f"{self.RID}/|vocals", "a\\b",      # rid 里带 / 或 \
            f"{self.RID}|",                     # 有 | 没产物名
            f"{self.RID}|VOCALS",               # 大小写不是可商量的口径
            f"{self.RID}|vocal", f"{self.RID}|full_song",  # 白名单外（后者是换声的多产物名）
            f"{self.RID}|vocals|accompaniment",  # 两个分隔符
            "2026 01 01", "任务ID", "rid;rm -rf", "*", "a b", ".", "..",
            self.RID + "A" * 200,               # 过长
        ]
        for spec in bad:
            with self.subTest(spec=spec):
                try:
                    app._server_audio_path(spec)
                except HTTPException as e:
                    self.assertIn(e.status_code, (400, 404), f"{spec!r} 应是 4xx：{e.status_code}")
                    self.assertIn("服务器素材标识", e.detail,
                                  f"{spec!r} 的 detail 要讲清可接受的写法：{e.detail}")
                    self.assertIn(repr(spec), e.detail,
                                  f"{spec!r} 的 detail 要回显收到的那串：{e.detail}")
                else:
                    self.fail(f"非法标识 {spec!r} 竟然解析成功")
        self.assertEqual(snapshot(app.OUTPUT_DIR, app.RVC_JOB_DIR, app.RVC_TRAIN_DIR), before,
                         "拒绝面只许说不，不许顺手创建任何东西")

    def test_unknown_rid_is_404_with_the_resolved_path(self):
        missing = "20260101_000000_ffffffff"
        with self.assertRaises(HTTPException) as cm:
            app._server_audio_path(missing)
        self.assertEqual(cm.exception.status_code, 404)
        self.assertIn(missing, cm.exception.detail, "要说「没找到」，并说清找的是哪个文件")
        self.assertIn(f"{missing}.wav", cm.exception.detail)
        # 整曲在、分离产物不在：不能假装那份产物存在
        self.put_output(self.RID, wav_bytes(1.0))
        with self.assertRaises(HTTPException) as cm2:
            app._server_audio_path(f"{self.RID}|vocals")
        self.assertEqual(cm2.exception.status_code, 404)
        self.assertIn(f"{self.RID}_vocals.wav", cm2.exception.detail)

    def test_rvc_result_is_not_a_source(self):
        """与 POST /api/rvc/convert/{rid} 同口径：换过的声音不能再送换声/当素材。"""
        rid = "20260101_000001_beefcafe"
        self.put_output(rid, wav_bytes(1.0), kind="rvc", parts=("accompaniment",))
        for spec in (rid, f"{rid}|accompaniment"):
            with self.subTest(spec=spec):
                with self.assertRaises(HTTPException) as cm:
                    app._server_audio_path(spec)
                self.assertEqual(cm.exception.status_code, 400)
                self.assertIn("换声结果", cm.exception.detail)
                self.assertIn("kind=rvc", cm.exception.detail)

    def test_generated_and_separated_products_are_both_accepted(self):
        # kind=generate 的整曲、kind=separate 的分离产物都要能用（⑦⑨⑩各自的库）
        g = "20260101_000002_aaaaaaaa"
        self.put_output(g, wav_bytes(1.0), kind="generate")
        self.assertTrue(app._server_audio_path(g).is_file())
        s = "20260101_000003_bbbbbbbb"
        self.put_output(s, wav_bytes(1.0), kind="separate", parts=("vocals",))
        self.assertTrue(app._server_audio_path(f"{s}|vocals").is_file())


@unittest.skipUnless(HAS_RVC_ENV, SKIP_RVC)
class TestConvertFromServerSource(ServerSourceBase):
    """POST /api/rvc/convert 的 source_id：与上传同一个落点、同一条流水线、同样入队。"""

    RID = "20260102_000000_11111111"

    def _post(self, data: dict, files=None):
        form = {"model": MODEL, "index_rate": "0", "pitch": "3",
                "separate_vocal": "auto", "gate": "on", "strip_harmony": "off"}
        form.update({k: str(v) for k, v in data.items()})
        kw = {"data": form}
        if files is not None:
            kw["files"] = files
        return self.client.post("/api/rvc/convert", **kw)

    def test_source_id_and_upload_land_identically(self):
        """正面证据：同一段 wav，两条路进到流水线前的位置与字节一模一样。"""
        data = wav_bytes(3.0)
        src = self.put_output(self.RID, data)[""]
        r_up = self._post({}, files={"file": ("库里的歌.wav", data, "audio/wav")})
        self.assertEqual(r_up.status_code, 200, r_up.text)
        r_srv = self._post({"source_id": self.RID, "task_name": "从库里取"})
        self.assertEqual(r_srv.status_code, 200, r_srv.text)
        self.assertEqual(len(self.seen), 2)
        args_up, args_srv = self.seen

        job_up, job_srv = r_up.json()["job"], r_srv.json()["job"]
        # 入队参数（除来源名与 src_rid 外）必须一致：换声行为等价，不看来源是哪条路
        for i, label in ((4, "model"), (5, "pitch"), (6, "f0_method"), (7, "index_rate"),
                         (8, "protect"), (9, "rms_mix_rate"), (10, "separate_vocal"),
                         (11, "gate"), (12, "strip_harmony"), (13, "filter_radius"),
                         (14, "resample_sr")):
            self.assertEqual(args_up[i], args_srv[i], f"{label} 两条路不该有差别")
        self.assertEqual(job_up["separate_vocal"], job_srv["separate_vocal"])
        self.assertEqual(job_up["gate"], job_srv["gate"])
        self.assertEqual(job_up["strip_harmony"], job_srv["strip_harmony"])

        p_up, p_srv = Path(args_up[2]), Path(args_srv[2])
        self.assertEqual(p_up.name, "src.wav", "上传路径按扩展名落地，这里是 wav")
        self.assertEqual(p_srv.name, "src.wav")
        self.assertEqual(p_up.parent.name, job_up["id"])
        self.assertEqual(p_srv.parent.name, job_srv["id"])
        self.assertEqual(p_srv.read_bytes(), data, "复制必须逐字节等价，不是转码也不是截断")
        self.assertEqual(p_up.stat().st_size, p_srv.stat().st_size)
        self.assertEqual(job_up["src_size"], job_srv["src_size"])
        self.assertEqual(job_up["src_duration"], job_srv["src_duration"],
                         "时长读的是落盘那份，两条路该给同一个数")
        self.assertEqual(job_srv["src_spec"], self.RID, "任务卡要说清用的是库里哪一段")
        self.assertIn(self.RID, job_srv["src_name"])
        self.assertTrue(src.is_file(), "取用不是搬运：output/ 里的原件必须还在")
        self.assertEqual(src.read_bytes(), data)

    def test_part_spec_uses_the_separation_product(self):
        data = wav_bytes(2.0)
        self.put_output(self.RID, data, kind="separate", parts=("vocals",))
        r = self._post({"source_id": f"{self.RID}|vocals"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(Path(self.seen[-1][2]).read_bytes(), data)

    def test_separate_vocal_off_and_harmony_flags_still_apply(self):
        """source_id 不是一条新流水线：既有语义（分离/去和声）照旧传到 worker。"""
        self.put_output(self.RID, wav_bytes(1.0))
        r = self._post({"source_id": self.RID, "separate_vocal": "off",
                        "strip_harmony": "on", "gate": "off"})
        self.assertEqual(r.status_code, 200, r.text)
        args = self.seen[-1]
        self.assertEqual((args[10], args[11], args[12]), (False, False, True))

    def test_both_sources_is_400_and_neither_is_400(self):
        self.put_output(self.RID, wav_bytes(1.0))
        before = snapshot(app.RVC_JOB_DIR, app.OUTPUT_DIR)
        r = self._post({"source_id": self.RID},
                       files={"file": ("demo.wav", wav_bytes(1.0), "audio/wav")})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("只能给一个", r.json()["detail"])
        for body in ({}, {"source_id": "   "}):
            q = self._post(body, files=None)
            self.assertEqual(q.status_code, 400, q.text)
            self.assertIn("缺少换声的源", q.json()["detail"])
        self.assertEqual(snapshot(app.RVC_JOB_DIR, app.OUTPUT_DIR), before)
        self.assertEqual(self.seen, [], "两条都没入队")

    def test_multipart_with_text_fields_only_is_a_valid_source_id_call(self):
        """前端取库里的素材时，FormData 里就只有文本字段（没有 file part）。"""
        self.put_output(self.RID, wav_bytes(1.0))
        r = self.client.post("/api/rvc/convert",
                             files={"model": (None, MODEL),
                                    "index_rate": (None, "0"),
                                    "source_id": (None, self.RID)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(self.seen), 1)

    def test_empty_file_field_is_a_shape_error_not_a_silent_source(self):
        """边界锁死：空 filename 的 file part 会被 Starlette 当文本字段 → FastAPI 回 422。
        所以前端只能在"真的选了文件"时 fd.append("file", f)；不许留一个永远空的 file 字段，
        也别指望后端把""当成"没给文件"。"""
        self.put_output(self.RID, wav_bytes(1.0))
        before = snapshot(app.RVC_JOB_DIR)
        r = self.client.post("/api/rvc/convert",
                             files={"file": ("", b"", "application/octet-stream"),
                                    "model": (None, MODEL),
                                    "index_rate": (None, "0"),
                                    "source_id": (None, self.RID)})
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(snapshot(app.RVC_JOB_DIR), before)
        self.assertEqual(self.seen, [])

    def test_bad_source_leaves_no_half_made_job_dir(self):
        self.put_output(self.RID, wav_bytes(1.0))
        before = snapshot(app.RVC_JOB_DIR, app.OUTPUT_DIR)
        for spec in ("../../app.py", "20260101_000000_ffffffff", f"{self.RID}|nope"):
            r = self._post({"source_id": spec})
            self.assertIn(r.status_code, (400, 404), f"{spec!r} → {r.text}")
        self.assertEqual(snapshot(app.RVC_JOB_DIR, app.OUTPUT_DIR), before,
                         "标识解析失败不许在 jobs/ 下留下半成品目录")
        self.assertEqual(self.seen, [])

    def test_bad_params_are_still_refused_before_any_copy(self):
        """"参数在拷文件之前全部规整完"这条顺序对 source_id 同样成立。"""
        self.put_output(self.RID, wav_bytes(1.0))
        before = snapshot(app.RVC_JOB_DIR)
        self.assertEqual(self._post({"source_id": self.RID, "index_rate": "2"}).status_code, 400)
        self.assertEqual(self._post({"source_id": self.RID, "pitch": "90"}).status_code, 400)
        # 缺索引 + 检索强度 > 0：老闸门对两条源都要拦住
        self.assertEqual(self._post({"source_id": self.RID, "index_rate": "0.75"}).status_code, 400)
        self.assertEqual(snapshot(app.RVC_JOB_DIR), before)
        self.assertEqual(self.seen, [])

    def test_rvc_result_refused_at_the_endpoint(self):
        rid = "20260102_000001_22222222"
        self.put_output(rid, wav_bytes(1.0), kind="rvc")
        before = snapshot(app.RVC_JOB_DIR)
        r = self._post({"source_id": rid})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("换声结果", r.json()["detail"])
        self.assertEqual(snapshot(app.RVC_JOB_DIR), before)
        self.assertEqual(self.seen, [])

    def test_upload_path_still_works_unchanged(self):
        """file 从必填变可选，不能把原来的上传入口碰坏。"""
        r = self._post({"task_name": "老路子"},
                       files={"file": ("demo.wav", wav_bytes(1.0), "audio/wav")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["job"]["src_name"], "demo.wav")
        self.assertNotIn("src_spec", r.json()["job"])


@unittest.skipUnless(HAS_RVC_ENV, SKIP_RVC)
class TestTrainFromServerSource(ServerSourceBase):
    """POST /rvc/train/check 与 /rvc/train 的 source_ids：素材真的进了 dataset，闸门照样管。"""

    A = "20260103_000000_aaaaaaaa"
    B = "20260103_000001_bbbbbbbb"

    def _check(self, source_ids: str = "", files=None):
        kw = {"data": {"source_ids": source_ids}}
        if files is not None:
            kw["files"] = files
        return self.client.post("/api/rvc/train/check", **kw)

    def _train(self, data: dict, files=None):
        form = {"name": "库声", "epochs": "30"}
        form.update({k: str(v) for k, v in data.items()})
        kw = {"data": form}
        if files is not None:
            kw["files"] = files
        return self.client.post("/api/rvc/train", **kw)

    def test_check_copies_server_audio_into_dataset(self):
        data = wav_bytes(6.0)
        paths_a = self.put_output(self.A, data)
        paths_b = self.put_output(self.B, data, kind="separate", parts=("vocals",))
        pa, pb = paths_a[""], paths_b["vocals"]
        r = self._check(source_ids=f"{self.A} , {self.B}|vocals")
        self.assertEqual(r.status_code, 200, r.text)
        rid = r.json()["id"]
        ds = app.RVC_TRAIN_DIR / rid / "dataset"
        self.assertEqual(sorted(p.name for p in ds.iterdir()),
                         ["sample_000.wav", "sample_001.wav"],
                         "命名必须与上传一致（sample_%03d.wav）")
        self.assertEqual((ds / "sample_000.wav").read_bytes(), pa.read_bytes())
        self.assertEqual((ds / "sample_001.wav").read_bytes(), pb.read_bytes())
        rep = r.json()["report"]
        self.assertEqual(rep["files"], 2, "报告里的样本数要认得出复制来的素材")
        self.assertEqual(rep["from_server"], "2 个素材来自本机 output/（复制，不是上传）")
        self.assertGreaterEqual(rep["total_sec"], 11.5, "两段各 6 秒：复制来的必须一起量")
        job = app._rvc_train_read(rid)
        self.assertEqual(job["samples"], 2)
        self.assertEqual(job["bytes"], pa.stat().st_size + pb.stat().st_size,
                         "复制的字节也得写进体检记录")
        self.assertTrue(pa.is_file() and pb.is_file(), "取用不是搬运：output/ 原件必须还在")

    def test_check_mixes_uploads_and_server_sources(self):
        data = wav_bytes(5.0)
        p = self.put_output(self.A, data)[""]
        r = self._check(source_ids=self.A,
                        files=[("files", ("先传的这个.wav", wav_bytes(4.0), "audio/wav"))])
        self.assertEqual(r.status_code, 200, r.text)
        ds = app.RVC_TRAIN_DIR / r.json()["id"] / "dataset"
        names = sorted(x.name for x in ds.iterdir())
        self.assertEqual(names, ["sample_000.wav", "sample_001.wav"],
                         "上传占 000，服务器素材接着往后编（与 /rvc/train 同一套编号）")
        self.assertEqual((ds / "sample_001.wav").read_bytes(), p.read_bytes())

    def test_check_volume_gate_counts_copied_bytes(self):
        """复制的字节也要算进"上传总量"——否则 source_ids 就是体积闸门的旁门。"""
        data = wav_bytes(6.0)
        self.put_output(self.A, data)
        self.put_output(self.B, data)
        saved = app._MAX_UPLOAD_TOTAL
        app._MAX_UPLOAD_TOTAL = len(data) + len(data) // 2   # 只够一份半
        try:
            r = self._check(source_ids=f"{self.A},{self.B}")
        finally:
            app._MAX_UPLOAD_TOTAL = saved
        self.assertEqual(r.status_code, 413, r.text)
        self.assertIn("上限", r.json()["detail"])
        self.assertEqual(list(app.RVC_TRAIN_DIR.iterdir()), [],
                         "闸门拦下的请求不该留下半份素材目录")

    def test_check_refuses_server_source_beyond_its_own_size_ceiling(self):
        data = wav_bytes(6.0)
        self.put_output(self.A, data)
        saved = app._MAX_UPLOAD_TOTAL
        app._MAX_UPLOAD_TOTAL = 1024   # 连一份都放不下
        try:
            r = self._check(source_ids=self.A)
        finally:
            app._MAX_UPLOAD_TOTAL = saved
        self.assertEqual(r.status_code, 413, r.text)
        self.assertEqual(list(app.RVC_TRAIN_DIR.iterdir()), [])

    def test_check_needs_at_least_one_source(self):
        r = self._check()
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("source_ids", r.json()["detail"], "要说清另一条路怎么走")
        self.assertEqual(list(app.RVC_TRAIN_DIR.iterdir()), [])

    def test_check_bad_spec_touches_nothing(self):
        self.put_output(self.A, wav_bytes(6.0))
        before = snapshot(app.RVC_TRAIN_DIR, app.OUTPUT_DIR)
        for spec in ("../../app.py", "20260101_000000_ffffffff", f"{self.A}|nope"):
            r = self._check(source_ids=spec)
            self.assertIn(r.status_code, (400, 404), f"{spec!r} → {r.text}")
        self.assertEqual(snapshot(app.RVC_TRAIN_DIR, app.OUTPUT_DIR), before)

    def test_check_rejects_rvc_result_as_material(self):
        rid = "20260103_000002_cccccccc"
        self.put_output(rid, wav_bytes(6.0), kind="rvc")
        before = snapshot(app.RVC_TRAIN_DIR)
        r = self._check(source_ids=rid)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("换声结果", r.json()["detail"])
        self.assertEqual(snapshot(app.RVC_TRAIN_DIR), before)

    def test_train_reuses_the_checked_dataset_by_id(self):
        """check 与 train 用同一份素材：dataset_id 优先，不再复制一遍。"""
        data = wav_bytes(6.0)
        self.put_output(self.A, data)
        self.put_output(self.B, data, kind="separate", parts=("vocals",))
        rid = self._check(source_ids=f"{self.A},{self.B}|vocals").json()["id"]
        only = list(app.RVC_TRAIN_DIR.iterdir())
        q = self._train({"dataset_id": rid, "source_ids": self.A})
        self.assertEqual(q.status_code, 200, q.text)
        self.assertEqual(self.train_starts[0][0], rid, "开练用的就是体检那份素材目录")
        self.assertEqual(list(app.RVC_TRAIN_DIR.iterdir()), only,
                         "dataset_id 给定时 source_ids 必须被忽略（素材是同一批才谈得上复用）")

    def test_train_from_source_ids_builds_dataset(self):
        data = wav_bytes(6.0)
        paths = self.put_output(self.A, data, parts=("vocals",))
        q = self._train({"source_ids": f"{self.A},{self.A}|vocals"})
        self.assertEqual(q.status_code, 200, q.text)
        body = q.json()
        rid = body["id"]
        ds = app.RVC_TRAIN_DIR / rid / "dataset"
        self.assertEqual(sorted(p.name for p in ds.iterdir()),
                         ["sample_000.wav", "sample_001.wav"],
                         "与体检入口同一套编号，素材才是同一批")
        self.assertEqual((ds / "sample_000.wav").read_bytes(), paths[""].read_bytes())
        self.assertEqual((ds / "sample_001.wav").read_bytes(), paths["vocals"].read_bytes())
        self.assertEqual(body["job"]["samples"], 2)
        self.assertEqual(body["job"]["source_ids"], f"{self.A},{self.A}|vocals",
                         "任务卡要记得素材是从库里哪几段起的")
        self.assertEqual(self.train_starts[0][0], rid)

    def test_train_without_any_source_explains_all_three(self):
        r = self._train({})
        self.assertEqual(r.status_code, 400, r.text)
        d = r.json()["detail"]
        self.assertIn("dataset_id", d, "既有测试与页面都靠这句话认出路")
        self.assertIn("source_ids", d)
        self.assertEqual(list(app.RVC_TRAIN_DIR.iterdir()), [])

    def test_train_bad_spec_touches_nothing(self):
        self.put_output(self.A, wav_bytes(6.0))
        before = snapshot(app.RVC_TRAIN_DIR, app.OUTPUT_DIR)
        for spec in ("../../app.py", "20260103_000009_deadbeef"):
            r = self._train({"source_ids": spec})
            self.assertIn(r.status_code, (400, 404), f"{spec!r} → {r.text}")
        self.assertEqual(snapshot(app.RVC_TRAIN_DIR, app.OUTPUT_DIR), before)
        self.assertEqual(self.train_starts, [])

    def test_train_volume_gate_counts_copied_bytes(self):
        data = wav_bytes(6.0)
        self.put_output(self.A, data)
        self.put_output(self.B, data)
        saved = app._MAX_UPLOAD_TOTAL
        app._MAX_UPLOAD_TOTAL = int(len(data) * 1.5)
        try:
            r = self._train({"source_ids": f"{self.A},{self.B}"})
        finally:
            app._MAX_UPLOAD_TOTAL = saved
        self.assertEqual(r.status_code, 413, r.text)
        self.assertEqual(list(app.RVC_TRAIN_DIR.iterdir()), [],
                         "复制中途被闸门拦下，整个素材目录都不该留下")


class TestServerAudioCopyIsHonest(ServerSourceBase):
    """复制这一步自己：IO 在不在事件循环外、失败留不留半份文件。"""

    RID = "20260104_000000_abcdabcd"

    def test_copy_is_run_off_the_event_loop_and_is_atomic(self):
        import asyncio

        data = wav_bytes(1.0)
        src = self.put_output(self.RID, data)[""]
        dst = app.RVC_JOB_DIR / "x" / "sample_000.wav"
        seen_loop: list[bool] = []

        real_copyfile = app.shutil.copyfile

        def spy(a, b):
            try:
                asyncio.get_running_loop()
                seen_loop.append(True)      # 落在事件循环里 = 会把网关冻住
            except RuntimeError:
                seen_loop.append(False)
            return real_copyfile(a, b)

        saved = app.shutil.copyfile
        app.shutil.copyfile = spy
        try:
            size = asyncio.run(app._server_audio_copy(src, dst))
        finally:
            app.shutil.copyfile = saved
        self.assertEqual(seen_loop, [False], "大文件复制必须丢线程池（run_in_threadpool）")
        self.assertEqual(size, len(data))
        self.assertEqual(dst.read_bytes(), data)
        self.assertEqual([p.name for p in dst.parent.iterdir()], ["sample_000.wav"],
                         ".part 中间文件不许留在盘上")

    def test_missing_source_is_reported_and_nothing_is_written(self):
        """只有 meta 没有 wav（产物被单独删过）：说"文件不在"，不许开个半份任务目录。"""
        (app.OUTPUT_DIR / f"{self.RID}.json").write_text(
            json.dumps({"id": self.RID, "kind": "generate"}), encoding="utf-8")
        before = snapshot(app.RVC_JOB_DIR, app.OUTPUT_DIR)
        with self.assertRaises(HTTPException) as cm:
            app._server_audio_path(self.RID)
        self.assertEqual(cm.exception.status_code, 404)
        self.assertIn(f"{self.RID}.wav", cm.exception.detail)
        self.assertEqual(snapshot(app.RVC_JOB_DIR, app.OUTPUT_DIR), before)

    def test_disk_floor_is_checked_before_copying(self):
        """磁盘剩余不足（507）与上传同一个口径：复制一样要占地方，不许旁门。"""
        import asyncio

        data = wav_bytes(1.0)
        src = self.put_output(self.RID, data)[""]
        dst = app.RVC_JOB_DIR / "z" / "sample_000.wav"
        saved = app._free_bytes
        app._free_bytes = lambda path: 1024          # 假装只剩 1KB
        try:
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(app._server_audio_copy(src, dst))
        finally:
            app._free_bytes = saved
        self.assertEqual(cm.exception.status_code, 507)
        self.assertFalse(dst.exists(), "拦下来的那次连目录都不该有素材")


if __name__ == "__main__":
    unittest.main()
