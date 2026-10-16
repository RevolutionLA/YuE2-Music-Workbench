# -*- coding: utf-8 -*-
"""把"同一份输入不许两条路出不同结果 / 改了用户的选择必须留痕"钉住。

用户 2026-10-16 的要求：按严重度逐条完全修复那五个问题，别再出现这类
"低级功能性问题"。这些用例只读源码与纯函数，不触发任何 GPU 任务。
"""
import io
import os
import re
import unittest

import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "static", "index.html")


def _read(p):
    return io.open(p, encoding="utf-8").read()


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
        self.assertIn('"cot_note": str(nxt.get("cot_note") or "")[:400]', src,
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


if __name__ == "__main__":
    unittest.main()
