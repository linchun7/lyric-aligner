from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "v4_text_repair.py"


class TextRepairCliTests(unittest.TestCase):
    def test_single_cli_rejects_threshold_below_production_floor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            lyric = root / "song.lrc"
            output = root / "out.srt"
            source.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\n第一句歌词\n",
                encoding="utf-8",
            )
            lyric.write_text("[00:01.00]第一句歌词\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--source-srt",
                    str(source),
                    "--canonical-lrc",
                    str(lyric),
                    "--out",
                    str(output),
                    "--auto-threshold",
                    "0.5",
                ],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("production auto-threshold must be at least 0.72", completed.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()