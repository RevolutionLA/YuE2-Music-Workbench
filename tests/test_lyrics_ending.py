"""v2.0.1：歌词收尾段 [Outro] 的三条口径锁（用户实测报的坑）。

坑的形状：歌词没有收尾段时生成链会自动补一段 `[Outro]（器乐渐弱收尾）`，
这首歌能正常生成；但那句是**在存盘之前**追加的，于是⑨回填到②的歌词里多出一句
用户从没写过的话，再点生成时它被我们自己的结构校验拦下
（`[Outro] 行数 1 不在 4-8 行范围`）——同一句话，生成链写得出、校验器不许看。

真源分工（三条各锁一个方向）：
- `_ensure_song_ending`（app.py）：引擎要"一定有收尾段"，追加只发生在送引擎之前；
- `_validate_lyrics_impl`（src/ai_tools.py）：人看的结构体检，必须认得生成链自己的输出；
- 存盘/回填：用户写的那份词，一个字都不许多。
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src"), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_review_fixes import LOCAL_BASE, Sandbox, app  # noqa: E402
from ai_tools import _validate_lyrics_impl  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

MARKER = "（器乐渐弱收尾）"

BODY = ("[Verse 1]\n一行两行三四行\n五句六句也正常\n七句八句不夸张\n还有第四行在这里\n\n"
        "[Chorus]\n副歌第一行\n副歌第二行\n副歌第三行\n副歌第四行")


class TestValidatorKnowsItsOwnOutput(unittest.TestCase):
    """校验器不许把生成链自己补的那一行判成用户的错。"""

    def test_machine_added_outro_passes(self):
        lyrics, note = app._ensure_song_ending(BODY)
        self.assertTrue(note, "没有收尾段就该补")
        self.assertIn(MARKER, lyrics)
        r = _validate_lyrics_impl(lyrics)
        self.assertEqual(r["errors"], [], "补完的歌词必须能过自己的校验，否则回填重跑必然报错")
        self.assertTrue(r["ok"])

    def test_empty_outro_and_direction_only_pass(self):
        for tail in ("[Outro]", "[Outro]\n（器乐渐弱收尾）", "[Outro]\n(harmonies fade out)",
                     "[End]\n（渐弱）"):
            r = _validate_lyrics_impl(BODY + "\n\n" + tail)
            self.assertEqual(r["errors"], [], f"{tail!r} 应当合法：{r['errors']}")

    def test_sung_outro_still_needs_four_to_eight_lines(self):
        """放开的是"器乐收尾"，不是把行数规则作废：真唱的词照样要成段。"""
        r = _validate_lyrics_impl(BODY + "\n\n[Outro]\n只有一句\n还有第二句")
        self.assertEqual(len(r["errors"]), 1, r["errors"])
        self.assertIn("唱词行数 2", r["errors"][0])

    def test_existing_outro_is_not_duplicated(self):
        lyrics, note = app._ensure_song_ending(BODY + "\n\n[Outro]\n四行收束\n第二行\n第三行\n第四行")
        self.assertEqual(note, "", "已有收尾段就不许再动用户的词")
        self.assertEqual(lyrics, BODY + "\n\n[Outro]\n四行收束\n第二行\n第三行\n第四行")

    def test_empty_lyrics_gets_no_fake_ending(self):
        lyrics, note = app._ensure_song_ending("   \n")
        self.assertEqual(lyrics.strip(), "", "空歌词不许替它编一段收尾")
        self.assertEqual(note, "")


class TestStoredLyricsAreTheUsersOwn(Sandbox):
    """存盘与回填只认用户写的那份词。"""

    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(app.app, base_url=LOCAL_BASE)
        # Sandbox 自己占用了 self._saved（是它要还原的 dict），这里另起名，别把它的表盖掉
        self._saved_gen = (app._gen_run, app._GEN_JOB)
        app._GEN_JOB = None
        self.seen: list[tuple[dict, dict]] = []

    def tearDown(self) -> None:
        app._gen_run, app._GEN_JOB = self._saved_gen
        super().tearDown()

    def test_generate_start_stores_user_lyrics_and_tells_about_the_marker(self):
        def spy(job, payload):
            self.seen.append((job, dict(payload)))

        app._gen_run = spy
        r = self.client.post("/api/generate/start", json={
            "style": "Mandarin folk", "lyrics": BODY, "cot": "full", "task_name": "收尾段口径"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("已补 [Outro]", body["cot_note"], "补了就必须说出来，不许静默改词")

        for _ in range(50):
            if self.seen:
                break
            time.sleep(0.05)
        self.assertEqual(len(self.seen), 1, "后台线程没跑到 _gen_run")
        job, payload = self.seen[0]
        self.assertNotIn(MARKER, payload["lyrics"], "payload 是被记录的那份，不许带机器补的话")
        self.assertEqual(payload["lyrics"], BODY)
        self.assertNotIn(MARKER, job["lyrics"], "任务卡/历史里存的必须是用户自己写的词")

    def test_engine_receives_the_closing_section(self):
        """送引擎的最后一刻才追加：YuE2 的规划器需要收尾段，磁盘上的记录不需要。"""
        got: dict = {}

        def fake_once(p):
            got.update(p)
            return 500, "桩：不让它真跑", b"", {}

        names = ("_ensure_backend", "_gen_run_once", "_commit_free_mb",
                 "backend_mode", "_win_toast")
        saved = {n: getattr(app, n) for n in names}
        app._ensure_backend = lambda: None
        app._gen_run_once = fake_once
        app._commit_free_mb = lambda: 999999
        app.backend_mode = lambda: "cpu"
        app._win_toast = lambda *a, **k: None
        payload = {"style": "Mandarin folk", "lyrics": BODY, "cot": "full", "seed": 7}
        job = {"id": app._new_id(), "status": "running", "style": "Mandarin folk", "lyrics": BODY}
        try:
            app._gen_run(job, payload)
        finally:
            for n, v in saved.items():
                setattr(app, n, v)
        self.assertIn(MARKER, got["lyrics"], "引擎那份必须带收尾段")
        self.assertNotIn(MARKER, payload["lyrics"], "调用方传进来的 dict 不许被就地改写")
        self.assertEqual(payload["lyrics"], BODY, "存盘用的那份必须一字未动")
        self.assertEqual(job["status"], "error", "桩回 500 就如实记失败，不许留个 running")


if __name__ == "__main__":
    unittest.main()
