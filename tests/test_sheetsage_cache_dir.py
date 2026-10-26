"""出谱加载必须显式传项目缓存目录，不能依赖 transformers 的全局常量。

LRC/FunASR 先把 transformers 导入后，TRANSFORMERS_CACHE 常量已冻结；只改环境变量
不会生效，MERT 父模型会去错误缓存里找。这里锁住显式 cache_dir + local_files_only。
"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import sheetsage_pt  # noqa: E402


class FakeModel:
    def eval(self):
        return self

    def to(self, device):
        return self


class CacheDirIsExplicit(unittest.TestCase):
    def setUp(self):
        self._saved_model = sheetsage_pt._model
        sheetsage_pt._model = None

    def tearDown(self):
        sheetsage_pt._model = self._saved_model

    def test_from_pretrained_gets_cache_dir_and_offline(self):
        seen = {}

        def fake_from_pretrained(path, *a, **kw):
            seen.update(kw)
            seen["path"] = path
            return FakeModel()

        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: False))
        fake_tf = types.SimpleNamespace(
            AutoModel=types.SimpleNamespace(from_pretrained=fake_from_pretrained))

        with tempfile.TemporaryDirectory() as tmp:
            hub = Path(tmp) / "hub"
            with mock.patch.object(sheetsage_pt, "configure_hf_home"), \
                 mock.patch.object(sheetsage_pt, "resolve_checkpoint",
                                   return_value=Path(tmp)), \
                 mock.patch.object(sheetsage_pt, "use_offline", return_value=True), \
                 mock.patch.object(sheetsage_pt, "apply_offline_mode"), \
                 mock.patch.object(sheetsage_pt, "hf_download_dir",
                                   return_value=Path(tmp)), \
                 mock.patch.dict(sys.modules, {"torch": fake_torch, "transformers": fake_tf}):
                sheetsage_pt.get_model()

        self.assertEqual(seen["cache_dir"], str(hub))
        self.assertTrue(seen["local_files_only"])
        self.assertTrue(seen["trust_remote_code"])


if __name__ == "__main__":
    unittest.main()
