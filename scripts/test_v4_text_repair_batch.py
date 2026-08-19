from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "v4_text_repair_batch.py"


class TextRepairBatchTests(unittest.TestCase):
    def test_batch_writes_ready_output_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            lyric = root / "song.lrc"
            output = root / "out.srt"
            report = root / "report.json"
            summary = root / "summary.json"
            manifest = root / "batch.json"
            source.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\n我真的爱\n",
                encoding="utf-8",
            )
            lyric.write_text("[00:01.00]我真的爱你\n", encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "jobs": [
                            {
                                "id": "one",
                                "source_srt": source.name,
                                "canonical_lyrics": [lyric.name],
                                "out": output.name,
                                "report": report.name,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--summary",
                    str(summary),
                ],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("我真的爱你", output.read_text(encoding="utf-8"))
            payload = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(payload["ready_count"], 1)
            self.assertEqual(payload["review_required_count"], 0)
            self.assertEqual(payload["error_count"], 0)
            self.assertTrue(payload["jobs"][0]["timeline_unchanged"])
            self.assertTrue(payload["jobs"][0]["cue_count_unchanged"])

    def test_batch_continues_after_one_job_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            lyric = root / "song.lrc"
            output = root / "out.srt"
            summary = root / "summary.json"
            manifest = root / "batch.json"
            source.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\n忘不掉的妳\n",
                encoding="utf-8",
            )
            lyric.write_text("[00:01.00]忘不掉的你\n", encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "jobs": [
                            {
                                "id": "broken",
                                "source_srt": "missing.srt",
                                "canonical_lyrics": [lyric.name],
                                "out": "broken.out.srt",
                            },
                            {
                                "id": "good",
                                "source_srt": source.name,
                                "canonical_lyrics": [lyric.name],
                                "out": output.name,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--summary",
                    str(summary),
                ],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 3)
            self.assertTrue(output.is_file())
            payload = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(payload["ready_count"], 1)
            self.assertEqual(payload["error_count"], 1)
            self.assertEqual([job["status"] for job in payload["jobs"]], ["error", "ready"])


if __name__ == "__main__":
    unittest.main()
