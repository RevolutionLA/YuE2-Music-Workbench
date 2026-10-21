"""③④ 转谱的缓存口径（2026-10-21 干净机器实测出来的坑）。

现场：`checkpoints/config.json` 钉了 `base_model_revision`，远端 `modeling_sheetsage2.py:248`
会按这个 revision 精确调 `cached_file()` 找 MERT 的配置/权重。而 `configure_hf_home()`
把**已废弃但 transformers 4.45 仍优先使用**的 `TRANSFORMERS_CACHE` 指到了
`hf_download/transformers`，权重却下载在 `hf_download/hub` → 必然 OSError，
再被 get_model() 吞成一句"本地缓存不完整"。改成同源后 25 秒加载成功。

这些用例只验证环境口径与配置，不加载模型：
    py312\\python.exe -m unittest discover -s tests -p "test_sheetsage_cache.py" -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import sheetsage_pt  # noqa: E402


class CacheDirsAgree(unittest.TestCase):
    def test_transformers_cache_points_at_the_same_hub_dir(self):
        """把 TRANSFORMERS_CACHE 改回 <hf_download>/transformers，这条立刻红。"""
        saved = {k: os.environ.get(k) for k in
                 ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE")}
        try:
            sheetsage_pt.configure_hf_home()
            self.assertEqual(
                os.environ["TRANSFORMERS_CACHE"], os.environ["HF_HUB_CACHE"],
                "两个缓存变量必须同源：transformers 4.45 的 cached_file() 优先用前者，"
                "而权重是下载到后者的",
            )
            self.assertTrue(os.environ["HF_HUB_CACHE"].endswith(os.path.join("hf_download", "hub")))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_hf_download_dir_is_inside_the_project(self):
        """缓存必须落在项目内：装完就能整目录拷走，不依赖 ~/.cache。"""
        d = sheetsage_pt.hf_download_dir()
        self.assertEqual(d, (ROOT / "runtime" / "hf_download").resolve())


class TransformersSeesTheSameCache(unittest.TestCase):
    """光设 env 不算修好：必须断言 transformers 真正读到的那份值（评审 B14）。

    transformers 4.45 的 cached_file() 优先用 TRANSFORMERS_CACHE，而且常量在 import 期
    求值；所以在一个干净子进程里先 configure 再 import，才能证明顺序没问题。
    """

    def test_transformers_constant_matches_hub_cache(self):
        import subprocess
        code = (
            "import os,sys;"
            "sys.path.insert(0, r'{src}');"
            "import sheetsage_pt;"
            "sheetsage_pt.configure_hf_home();"
            "import transformers.utils.hub as h;"
            "print(h.TRANSFORMERS_CACHE);"
            "print(os.environ['HF_HUB_CACHE'])"
        ).format(src=str(ROOT / "src"))
        r = subprocess.run([str(ROOT / "py312" / "python.exe"), "-c", code],
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        tf_cache, hub_cache = [ln.strip() for ln in r.stdout.strip().splitlines()[-2:]]
        self.assertEqual(os.path.normcase(tf_cache), os.path.normcase(hub_cache),
                         "transformers 实际用的缓存目录与 HUB 缓存不一致，转谱会找不到 MERT")


class PinnedRevision(unittest.TestCase):
    def test_config_pins_a_concrete_revision(self):
        cfg = json.loads((ROOT / "checkpoints" / "config.json").read_text(encoding="utf-8"))
        rev = str(cfg.get("base_model_revision") or "")
        self.assertRegex(rev, r"^[0-9a-f]{40}$",
                         "base_model_revision 必须是具体 commit：下 main 时文件哈希可能一样，"
                         "但 revision 路径不匹配照样加载失败（本机实测）")
        self.assertEqual(cfg.get("base_model_name_or_path"), "m-a-p/MERT-v2-FullSong")

    def test_setup_reads_the_pin_instead_of_hardcoding(self):
        src = (ROOT / "scripts" / "setup_runtime.py").read_text(encoding="utf-8")
        self.assertIn('cfg.get("base_model_revision")', src,
                      "安装脚本应当从 checkpoints/config.json 读 revision，别手抄一串哈希")
        self.assertIn("TRANSFORMERS_CACHE", src,
                      "安装脚本要与 configure_hf_home 同口径设缓存目录，否则装完照样加载失败")


class TranscribeRequirements(unittest.TestCase):
    def test_declares_what_pretty_midi_needs(self):
        p = ROOT / "requirements-transcribe.txt"
        self.assertTrue(p.is_file())
        b = p.read_bytes()
        self.assertTrue(b.startswith(b"\xef\xbb\xbf"), "必须 UTF-8 with BOM（pip 在中文 Windows 上按 cp936 解）")
        text = b.decode("utf-8-sig")
        # 只看真正的依赖行：注释里提到了 numpy==1.24.3 这串字，不能当"钉了 numpy"的证据
        reqs = [line.strip() for line in text.splitlines()
                if line.strip() and not line.strip().startswith("#")]
        body = "\n".join(reqs)
        for pkg in ("torch==", "torchaudio==", "transformers==", "pretty_midi==", "setuptools=="):
            self.assertIn(pkg, body, f"{pkg} 缺了会让转谱在 import 阶段就崩")
        # numpy/scipy 不能在这里钉：主环境的 librosa 1.0 要 numpy>=2
        self.assertFalse(any(r.startswith(("numpy==", "scipy==")) for r in reqs),
                         "转谱清单不许钉 numpy/scipy，否则会拆掉主环境")


if __name__ == "__main__":
    unittest.main()
