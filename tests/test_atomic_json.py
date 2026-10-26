"""原子 JSON 写：整库保存不能留下半截文件。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import json_store  # noqa: E402


class AtomicJson(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.json"
            json_store.atomic_write_json(p, {"a": 1, "中文": "值"})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")),
                             {"a": 1, "中文": "值"})
            self.assertFalse(p.with_suffix(".json.tmp").exists())

    def test_old_file_survives_serialization_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.json"
            p.write_text('{"old": true}', encoding="utf-8")
            with self.assertRaises(TypeError):
                json_store.atomic_write_json(p, {"bad": object()})
            self.assertEqual(p.read_text(encoding="utf-8"), '{"old": true}')
            self.assertFalse(p.with_suffix(".json.tmp").exists())

    def test_old_file_survives_write_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.json"
            p.write_text('{"old": true}', encoding="utf-8")
            real = Path.write_text

            def boom(self, *a, **k):
                if str(self).endswith(".json.tmp"):
                    raise OSError("disk full")
                return real(self, *a, **k)

            with mock.patch.object(Path, "write_text", boom):
                with self.assertRaises(OSError):
                    json_store.atomic_write_json(p, {"new": True})
            self.assertEqual(p.read_text(encoding="utf-8"), '{"old": true}')
            self.assertFalse(p.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
