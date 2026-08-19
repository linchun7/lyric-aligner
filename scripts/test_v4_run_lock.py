from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.pipeline.run_lock import OutputRunLock


class V4RunLockTests(unittest.TestCase):
    def test_second_lock_on_same_output_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "v4"
            with OutputRunLock(out_dir):
                lock_path = out_dir / ".v4-run.lock"
                self.assertTrue(lock_path.is_file())
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema_version"], "1.0")
                self.assertGreater(payload["pid"], 0)
                self.assertTrue(payload["token"])
                with self.assertRaises(RuntimeError):
                    with OutputRunLock(out_dir):
                        pass
            self.assertFalse((out_dir / ".v4-run.lock").exists())

    def test_lock_is_released_after_exception(self):
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "v4"
            with self.assertRaisesRegex(ValueError, "boom"):
                with OutputRunLock(out_dir):
                    raise ValueError("boom")
            self.assertFalse((out_dir / ".v4-run.lock").exists())
            with OutputRunLock(out_dir):
                self.assertTrue((out_dir / ".v4-run.lock").exists())

    def test_owner_does_not_delete_replaced_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "v4"
            lock_path = out_dir / ".v4-run.lock"
            with OutputRunLock(out_dir):
                lock_path.unlink()
                lock_path.write_text(
                    json.dumps({"schema_version": "1.0", "pid": 999, "token": "other"}) + "\n",
                    encoding="utf-8",
                )
            self.assertTrue(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
