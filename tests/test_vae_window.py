# -*- coding: utf-8 -*-
"""VAE 低显存窗口脚本的回归：改得动、会备份、能恢复、非法值拒绝。"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("set_vae_window", ROOT / "scripts" / "set_vae_window.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


class VaeWindow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="yue2-vae-")
        self.root = Path(self.tmp.name)
        self.cfg = self.root / "cpp" / "model" / "m" / "sidecars" / "yue2-vae-config.json"
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text(json.dumps({"decode_core_frames": 1024, "decode_halo_frames": 16}),
                            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_patch_backup_and_restore(self):
        self.assertEqual(mod.find_configs(self.root), [self.cfg])
        old, new = mod.patch_file(self.cfg, 512)
        self.assertEqual((old, new), (1024, 512))
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8"))["decode_core_frames"], 512)
        backup = self.cfg.with_name(self.cfg.name + mod.BACKUP_SUFFIX)
        self.assertTrue(backup.is_file(), "改模型配置前必须留备份")
        self.assertTrue(mod.restore_file(self.cfg))
        self.assertEqual(json.loads(self.cfg.read_text(encoding="utf-8"))["decode_core_frames"], 1024)

    def test_same_value_is_noop_and_no_second_backup(self):
        mod.patch_file(self.cfg, 512)
        bak = self.cfg.with_name(self.cfg.name + mod.BACKUP_SUFFIX)
        first = bak.read_bytes()
        self.assertEqual(mod.patch_file(self.cfg, 512), (512, 512))
        self.assertEqual(bak.read_bytes(), first)

    def test_invalid_window_is_rejected(self):
        self.assertEqual(mod.main(["--frames", "513", "--root", str(self.root)]), 2)
        self.assertEqual(mod.main(["--frames", "8", "--root", str(self.root)]), 2)
        self.assertEqual(mod.main(["--frames", "4097", "--root", str(self.root)]), 2)


if __name__ == "__main__":
    unittest.main()
