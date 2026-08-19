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

    def test_batch_rejects_cross_job_output_overwriting_an_input_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_source = root / "first.srt"
            second_source = root / "second.srt"
            lyric = root / "song.lrc"
            manifest = root / "batch.json"
            first_source.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\n第一句歌词\n",
                encoding="utf-8",
            )
            second_original = "1\n00:00:03,000 --> 00:00:04,000\n第二句歌词\n"
            second_source.write_text(second_original, encoding="utf-8")
            lyric.write_text("[00:01.00]第一句歌词\n", encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "jobs": [
                            {
                                "id": "unsafe",
                                "source_srt": first_source.name,
                                "canonical_lyrics": [lyric.name],
                                "out": second_source.name,
                            },
                            {
                                "id": "reader",
                                "source_srt": second_source.name,
                                "canonical_lyrics": [lyric.name],
                                "out": "reader.out.srt",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(manifest)],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("must not overwrite manifest or any batch input", completed.stderr)
            self.assertEqual(second_source.read_text(encoding="utf-8"), second_original)
            self.assertFalse((root / "reader.out.srt").exists())

    def test_batch_rejects_output_overwriting_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            lyric = root / "song.lrc"
            manifest = root / "batch.json"
            source.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\n第一句歌词\n",
                encoding="utf-8",
            )
            lyric.write_text("[00:01.00]第一句歌词\n", encoding="utf-8")
            original_manifest = json.dumps(
                {
                    "jobs": [
                        {
                            "id": "unsafe-manifest",
                            "source_srt": source.name,
                            "canonical_lyrics": [lyric.name],
                            "out": manifest.name,
                        }
                    ]
                },
                ensure_ascii=False,
            )
            manifest.write_text(original_manifest, encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--manifest", str(manifest)],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("must not overwrite manifest or any batch input", completed.stderr)
            self.assertEqual(manifest.read_text(encoding="utf-8"), original_manifest)


if __name__ == "__main__":
    unittest.main()
