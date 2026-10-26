"""源码发布包构建器：内容白名单、卫生检查、zip + sha256。"""

from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts" / "build_release.py")
build_release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_release)


class BuildRelease(unittest.TestCase):
    def test_collect_excludes_runtime_and_secrets(self):
        files = build_release.collect_files(ROOT)
        self.assertIn("app.py", files)
        self.assertIn("static/index.html", files)
        self.assertIn("web/app.css", files)
        self.assertIn("scripts/build_ui.py", files)
        self.assertIn("secrets/local_env.example.bat", files)
        for bad in ("secrets/local_env.bat", "cpp/server.json", "runtime/data/engine_state.json",
                    "py312/python.exe", "main.cp312-win_amd64.pyd"):
            self.assertNotIn(bad, files)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            path = build_release.build(out, dry_run=True)
            self.assertFalse(path.exists())
            self.assertFalse(out.exists())

    def test_zip_and_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            path = build_release.build(out)
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 300_000)
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
            prefix = path.stem
            for rel in ("app.py", "static/index.html", "web/app.css",
                        "scripts/build_ui.py", "secrets/local_env.example.bat"):
                self.assertIn(f"{prefix}/{rel}", names)
            joined = "\n".join(names)
            self.assertNotIn("/cpp/", joined)
            self.assertNotIn("/runtime/", joined)
            self.assertNotIn("/py312/", joined)
            self.assertNotIn(".pyd", joined)
            self.assertNotIn("secrets/local_env.bat", joined)

            sha_file = out / (path.name.replace(".zip", ".sha256"))
            self.assertTrue(sha_file.is_file())
            line = sha_file.read_text(encoding="utf-8").split()
            self.assertEqual(line[0], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(line[1], path.name)


if __name__ == "__main__":
    unittest.main()
