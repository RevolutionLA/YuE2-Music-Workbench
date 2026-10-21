# -*- coding: utf-8 -*-
"""把"同一份输入不许两条路出不同结果 / 改了用户的选择必须留痕"钉住。

用户 2026-10-16 的要求：按严重度逐条完全修复那五个问题，别再出现这类
"低级功能性问题"。这些用例只读源码与纯函数，不触发任何 GPU 任务。
"""
import ast
import inspect
import io
import os
import re
import unittest

import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")


def _read(p):
    with io.open(p, encoding="utf-8") as fh:
        return fh.read()


class TestSubmitPathsAgree(unittest.TestCase):
    """「生成歌曲」与「加入队列」两条入口必须给同一份输入同一个结果。"""

    def setUp(self):
        self.src = _read(os.path.join(ROOT, "app.py"))
        i = self.src.index("def batch_start")
        self.batch = self.src[i:i + 4200]
        j = self.src.index("def generate_start")
        self.single = self.src[j:i]

    def test_batch_also_strips_tempo_from_style(self):
        self.assertIn("_style_yield_to_abc", self.batch,
                      "批量入口没做「节奏以乐谱为准」的剥离：同一请求走两条路会得到不同 style")

    def test_batch_cot_default_matches_single(self):
        self.assertIn('str(t.get("cot") or "full")', self.batch,
                      "批量的缺省档位不再是 full（与单首不一致）")
        self.assertIn('str(payload.get("cot") or "full")', self.single)

    def test_multipart_defaults_defer_to_the_normalizer(self):
        """multipart 的 Form 缺省必须是 None。

        写死 0.75/1.0 的话，用户没填的字段会被 FastAPI 先填成旧上游缺省，
        `_rvc_convert_params` 里那套评审后改过的缺省（0.5/0.25）永远轮不到，
        于是"上传入口"与"JSON 入口"两套结果。
        """
        i = self.src.index("async def rvc_convert(")
        sig = self.src[i:self.src.index("):", i)]
        for name, bad in (("index_rate", "0.75"), ("rms_mix_rate", "1.0")):
            self.assertNotIn("%s: float = Form(%s)" % (name, bad), sig,
                             "%s 又写死了 Form 缺省，统一闸门的缺省会被它盖掉" % name)
            self.assertRegex(sig, r"%s: float \| None = Form\(None\)" % name)


class TestRewritesLeaveATrace(unittest.TestCase):
    """服务端替用户改写选择时，说明必须能事后查到，不能只闪一下 toast。"""

    def test_unrecognized_cot_says_so(self):
        mode, note = app._resolve_cot("bogus", "")
        self.assertEqual(mode, "full")
        self.assertIn("无法识别", note, "静默兜底：用户以为引擎按他给的档位跑了")

    def test_empty_cot_stays_quiet(self):
        self.assertEqual(app._resolve_cot("", "")[1], "",
                         "空值走默认是正常路径，不该混进「改写过」的提示里")

    def test_two_notes_are_composed_not_dropped(self):
        mode, note = app._resolve_cot("bogus", "X:1\nK:C\nCDEF|")
        self.assertEqual(mode, "melody")
        self.assertIn("无法识别", note)
        self.assertIn("melody", note, "两条改写同时发生时，只报一条等于把另一半藏起来")

    def test_cot_note_reaches_the_record_and_the_api(self):
        src = self.src = _read(os.path.join(ROOT, "app.py"))
        self.assertIn('"cot_note": str(cot_note or "")[:400]', src,
                      "单首生成的 job 记录里没有 cot_note")
        self.assertIn("cot_note = str(nxt.get(\"cot_note\") or \"\")", src,
                      "队列条目开跑时没有把 cot_note 带出来")
        self.assertIn('"cot_note": cot_note[:400]', src,
                      "队列条目跑完写产物 meta 时又把 cot_note 丢了")
        out = app._unified_task({"id": "x", "status": "done",
                                 "cot_note": "已改用 melody"}, "generate")
        self.assertEqual(out.get("cot_note"), "已改用 melody",
                         "统一任务视图没把 cot_note 透给前端")

    def test_frontend_shows_the_note_in_history(self):
        html = _read(INDEX)
        self.assertIn("h.cot_note", html, "⑪ 任务管理不再显示改写原因：事后又查不到了")

    def test_loaded_weights_are_reported(self):
        src = _read(os.path.join(ROOT, "app.py"))
        self.assertIn('"loaded_vae_from"', src, "vae 是「同目录」还是「公共退回」没记录")
        html = _read(INDEX)
        self.assertIn('"engines.loaded_main"', html, "⑬ 查不到当前实际加载的主模型")


class TestRefreshDoesNotClobberInput(unittest.TestCase):
    """刷新时回填「在跑那一首」的参数，只许填空白格，不许盖掉用户正在写的东西。"""

    def setUp(self):
        self.html = _read(INDEX)
        i = self.html.index("async function resumeRunningJob")
        self.body = self.html[i:i + 3000]

    def test_no_unconditional_overwrite(self):
        for el in ("style", "lyrics", "abc"):
            self.assertNotIn('$("%s").value = job.' % el, self.body,
                             "resumeRunningJob 又无条件覆写 #%s 了" % el)
        self.assertIn("const fillIfEmpty =", self.body)

    def test_cot_select_is_left_alone(self):
        self.assertNotIn('$("cot").value = job.cot', self.body,
                         "档位下拉框被刷新改掉：用户选的 full 变 melody")

    def test_undo_still_guarded_for_lyrics(self):
        # 只有真的回填了歌词才压撤销栈；用户自己有词时一个字都不动
        i = self.body.index("pushLyricUndo")
        self.assertIn("fillIfEmpty($(\"lyrics\")", self.body[:i][-260:],
                      "撤销栈的压入点变了")


class TestGpuLockNeverBlocksTheEventLoop(unittest.TestCase):
    """红线：`async def` 里不许阻塞等 _GPU_SEM（评审 B-3 把它变成了实害）。

    `_GPU_SEM` 是 `threading.Semaphore`（app.py），在协程里 `with` 它会把整个事件循环
    按住——一首歌占锁十几到几十分钟，期间 /api/system/info、健康探测全部无人应答，
    看门狗连 5 次短探 + 90s 深探无回话就判"假死"并击杀网关（watchdog.log 里
    2026-10-16 10:09:22 已实际杀掉一次当时带着在跑批量任务的进程）。
    这条用 ast 扫，扫的是结构而不是某一行文字。
    """

    def _async_bodies(self):
        tree = ast.parse(_read(os.path.join(ROOT, "app.py")))

        def walk(node, out, inside_async=False):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.AsyncFunctionDef):
                    out.append(child)
                    for sub in ast.iter_child_nodes(child):
                        walk(sub, out, True)        # 协程里再套协程也要看
                elif isinstance(child, ast.FunctionDef):
                    if inside_async:
                        continue                    # 同步嵌套函数自己会进线程池，不算违规
                    walk(child, out, False)
                else:
                    walk(child, out, inside_async)
        out = []
        walk(tree, out)
        return out

    def _gpu_uses(self, fn):
        hits = []

        def rec(node):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue      # 嵌套函数自己跑在线程里，不在事件循环上等锁
                if isinstance(child, ast.With):
                    for item in child.items:
                        if isinstance(item.context_expr, ast.Name) and item.context_expr.id == "_GPU_SEM":
                            hits.append("with _GPU_SEM @ 行%d" % child.lineno)
                if isinstance(child, ast.Call):
                    f = child.func
                    if (isinstance(f, ast.Attribute) and f.attr == "acquire"
                            and isinstance(f.value, ast.Name) and f.value.id == "_GPU_SEM"):
                        kw = {k.arg: getattr(k.value, "value", None) for k in child.keywords}
                        if kw.get("blocking", True):
                            hits.append("_GPU_SEM.acquire() 阻塞式 @ 行%d" % child.lineno)
                rec(child)
        rec(fn)
        return hits

    def test_no_blocking_gpu_wait_in_coroutines(self):
        bad = []
        for fn in self._async_bodies():
            bad += ["%s(): %s" % (fn.name, h) for h in self._gpu_uses(fn)]
        self.assertEqual(bad, [],
                         "协程里出现了阻塞等 GPU 锁的写法：" + "；".join(bad) +
                         "。等锁要放进 run_in_threadpool，否则冻的是整个网关。")


class TestSeedIsMaterializedOnBothPaths(unittest.TestCase):
    """「随机」必须在派发时定成真实种子，且两条提交路径口径一致（评审 B-2）。"""

    def test_random_values_become_real_seeds(self):
        import inspect
        for bad in (-1, None, "abc", True, False, 1.5):
            got = app._materialize_seed(bad)
            self.assertIsInstance(got, int, "%r 没被具体化成整数" % (bad,))
            self.assertGreaterEqual(got, 0)
            self.assertLess(got, 2 ** 31)
        draws = {app._materialize_seed(-1) for _ in range(30)}
        self.assertGreater(len(draws), 1, "随机种子恒等于同一个值，等于没随机")

    def test_explicit_seed_passes_through(self):
        self.assertEqual(app._materialize_seed(42), 42)
        self.assertEqual(app._materialize_seed(0), 0, "0 是合法种子，不能被当成「随机」重抽")

    def test_both_paths_call_the_same_helper(self):
        for name in ("generate_start", "_batch_run_worker"):
            body = inspect.getsource(getattr(app, name))
            # 批量路径先走 _resolve_batch_seed（沿用条目里已定的种子，保住 AR 检查点），
            # 再落到同一个 _materialize_seed；单首路径直接调 _materialize_seed。
            self.assertTrue("_resolve_batch_seed" in body or "_materialize_seed" in body,
                            "%s 没有具体化种子：这一条路出来的歌不可复现" % name)


class TestWeightDisplayTellsTruth(unittest.TestCase):
    """⑬ 那一行必须报后端真正会加载的权重，并在对不上时把矛盾摆出来（评审 B-1）。"""

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp(prefix="yue2-weights-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.model_dir = self.tmp / "cpp" / "model" / "q4"
        self.model_dir.mkdir(parents=True)
        self._saved = (app.ROOT, app.MODEL_DIR, app._read_server_json, app._engine_state_read)
        app.ROOT = self.tmp
        app.MODEL_DIR = self.tmp / "cpp" / "model"
        app._engine_state_read = lambda: dict(self.state)

    def tearDown(self):
        app.ROOT, app.MODEL_DIR, app._read_server_json, app._engine_state_read = self._saved

    def _cfg(self, main, vae, path="model/q4/%s" % "x"):
        app._read_server_json = lambda: {"models": [{
            "path": path, "session_options": {"yue2.model_gguf": main, "yue2.vae_gguf": vae}}]}

    def test_reports_the_file_that_actually_exists(self):
        self.state = {"loaded_main": "yue2-3b-q4_k_m.gguf", "loaded_vae": "yue2-vae-f16.gguf",
                      "loaded_vae_from": "同目录"}
        (self.model_dir / "yue2-3b-q4_k_m.gguf").write_bytes(b"x")
        (self.model_dir / "yue2-vae-f16.gguf").write_bytes(b"x")
        self._cfg("yue2-3b-q4_k_m.gguf", "yue2-vae-f16.gguf",
                  path="model/q4/yue2-3b-q4_k_m.gguf")
        self.assertEqual(app._loaded_main_label(), "yue2-3b-q4_k_m.gguf")
        self.assertEqual(app._loaded_vae_label(), "yue2-vae-f16.gguf（同目录）")

    def test_missing_file_is_flagged_not_shown_as_normal(self):
        # 这正是评审时线上遇到的形状：状态文件写着 yue2-q4.gguf，磁盘上没有这份东西
        self.state = {"loaded_main": "yue2-q4.gguf", "loaded_vae": "yue2-vae-f16.gguf"}
        self._cfg("yue2-q4.gguf", "yue2-vae-f16.gguf", path="model/yue2-q4.gguf")
        label = app._loaded_main_label()
        self.assertIn("⚠", label, "磁盘上不存在的权重被当成「当前主模型」平铺展示")
        self.assertIn("不存在", label)
        self.assertIn("⚠", app._loaded_vae_label(), "vae 缺失同样要标出来")

    def test_state_disagreement_shows_both_names(self):
        self.state = {"loaded_main": "别的.gguf"}
        (self.model_dir / "yue2-3b-q8_0.gguf").write_bytes(b"x")
        self._cfg("yue2-3b-q8_0.gguf", "", path="model/q4/yue2-3b-q8_0.gguf")
        label = app._loaded_main_label()
        self.assertIn("yue2-3b-q8_0.gguf", label)
        self.assertIn("别的.gguf", label, "两处真相不一致时必须都摆出来，不许静默取其一")
        self.assertIn("server.json", label)

    def test_engine_state_write_is_serialised(self):
        """读-改-写必须加锁：只改 backend 的写入方会把别人的 loaded_* 带回旧值。"""
        import inspect
        self.assertIn("_ENGINE_STATE_LOCK", inspect.getsource(app._engine_state_write))


class TestPageStillDoesNotTouchOtherPages(unittest.TestCase):
    """界面显示 = 真实提交。这一组锁的是评审 F-2/F-3/F-4/F-5/F-7 那几个出口。"""

    def setUp(self):
        self.html = _read(INDEX)

    def _body_from(self, marker, stop=None, limit=2600):
        i = self.html.index(marker)
        end = self.html.index(stop, i) if stop and stop in self.html[i:] else i + limit
        return self.html[i:min(end, i + limit)]

    def test_submit_never_reads_page_ones_controls(self):
        self.assertNotIn("function styleWithGender", self.html,
                         "②⑤ 提交又要读 ① 装配台上的 #gender/#bpm 了：用户在本页看不见这个改写")
        self.assertNotIn("styleWithGender($", self.html, "提交口又调它了")
        for marker in ('$("go").onclick', '$("queue").onclick'):
            body = self._body_from(marker, limit=900)
            self.assertIn('const style = $("style").value.trim();', body,
                          "%s 交出去的不再是本页那句曲风" % marker)

    def test_my_preset_fills_only_its_own_page(self):
        body = self._body_from("function restoreMyPreset", stop="\nfunction ", limit=1800)
        for el in ("lyrics", "cot", "abc"):
            self.assertNotIn('$("%s")' % el, body,
                             "① 的「我的预设」又往别的页灌 %s 了" % el)

    def test_param_summary_only_mentions_what_this_page_submits(self):
        body = self._body_from("function updateParamSummary", stop="\nfunction ", limit=1600)
        self.assertIn('curTab === "scoreSing"', body,
                      "② 的参数摘要又去读共用的 #abc：明明交空谱却写「按谱走」")

    def test_clearing_the_score_voids_its_analysis(self):
        body = self._body_from("abcAnalyzeTimer = setTimeout")
        self.assertIn("lastAbcAnalysis = null", body,
                      "谱框清空后上一首的分析结论还留着，档位会被幽灵结论锁住")

    def test_resume_does_not_overwrite_cfg_or_steps(self):
        body = self._body_from("async function resumeRunningJob", limit=3200)
        self.assertNotIn('$("cfg").value = p.cfg_scale', body,
                         "刷新又把在跑那首的 cfg 写成用户的选择了")
        self.assertNotIn('$("steps").value = p.num_inference_steps', body)
        self.assertNotIn('$("gender").value', body, "刷新反写 ① 的人声下拉")

    def test_restart_button_stays_inside_its_queue_row(self):
        html = self.html
        i = html.index('data-restart')
        row = html[max(0, i - 360):i + 120]
        self.assertIn("isQueue", row, "「↻ 重启」又对单首任务出现了（单首要走 data-regen）")
        self.assertIn('h.status === "error"', row,
                      "对 running 条目给重排按钮 = 先杀再 404")
        body = self._body_from("const rsBtn =", limit=1500)
        self.assertNotIn('fetch("/api/generate/stop', body,
                         "重排按钮又盲发全局终止：掐的是此刻在算的那一条，不是用户点的这一条")

    def test_server_material_submit_does_not_read_a_file_object(self):
        body = self._body_from('$("rvcGo").onclick', limit=3600)
        self.assertNotIn("src_name: f.name", body,
                         "走「服务器素材」时没有 File 对象，读 f.name 会把已成功的提交报成失败")


if __name__ == "__main__":
    unittest.main()
