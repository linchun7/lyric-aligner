import csv
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from init_task import init_task
from task_contract import qa_metadata, write_json_atomic


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = REPOSITORY_ROOT / "scripts" / "redo_karaoke_pipeline.py"


def write_synthetic_wave(path: Path, seconds: float = 12.0, sample_rate: int = 2000) -> None:
    rng = np.random.default_rng(38)
    times = np.arange(int(seconds * sample_rate), dtype=np.float64) / sample_rate
    chirp = np.sin(2 * math.pi * (90 * times + 7.5 * times * times))
    pulse = np.sin(2 * math.pi * 3.7 * times) * np.sin(2 * math.pi * 173 * times)
    noise = rng.normal(0.0, 0.08, len(times))
    signal = np.clip(0.48 * chirp + 0.24 * pulse + noise, -1.0, 1.0)
    pcm = np.asarray(signal * 32767, dtype="<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def run_pipeline(root: Path, *arguments: str, expected: int = 0) -> subprocess.CompletedProcess:
    if os.name == "nt" and "libreoffice" in sys.executable.casefold():
        launcher = shutil.which("py")
        if not launcher:
            raise unittest.SkipTest("system Python launcher is unavailable")
        interpreter = [launcher, "-3.14"]
    else:
        interpreter = [sys.executable]
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [*interpreter, str(PIPELINE), *arguments],
        cwd=root,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=90,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"pipeline returned {result.returncode}, expected {expected}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class SyntheticEndToEndTests(unittest.TestCase):
    def test_prepare_align_build_finalize_and_qa_share_one_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "private" / "synthetic" / "input"
            lyrics_dir = input_root / "lyrics"
            source_audio_dir = input_root / "source-audio"
            output = root / "output" / "synthetic"
            lyrics_dir.mkdir(parents=True)
            source_audio_dir.mkdir()
            source_srt = input_root / "source.srt"
            audio = input_root / "mix.wav"
            song_list = input_root / "songs.txt"
            lyric = lyrics_dir / "Signal.lrc"
            source_audio = source_audio_dir / "Signal.wav"

            source_srt.write_text(
                "1\n00:00:00,000 --> 00:00:02,800\nfirst signal\n\n"
                "2\n00:00:03,000 --> 00:00:05,800\nsecond signal\n\n"
                "3\n00:00:06,000 --> 00:00:08,800\nthird signal\n\n"
                "4\n00:00:09,000 --> 00:00:11,800\nfourth signal\n",
                encoding="utf-8",
            )
            song_list.write_text(
                "00:00 Synthetic Artist - Signal\n", encoding="utf-8"
            )
            lyric.write_text(
                "[00:00.00]first signal\n"
                "[00:03.00]second signal\n"
                "[00:06.00]third signal\n"
                "[00:09.00]fourth signal\n",
                encoding="utf-8",
            )
            write_synthetic_wave(audio)
            source_audio.write_bytes(audio.read_bytes())

            initialized = init_task(
                root,
                "synthetic",
                source_srt=source_srt,
                audio=audio,
                song_list=song_list,
                lyrics_dir=lyrics_dir,
                source_audio_dir=source_audio_dir,
            )
            manifest = Path(initialized["task_manifest"])
            fingerprint = initialized["task_fingerprint_sha256"]
            prepare_dir = output / "01_prepare"
            alignment = output / "02_audio_alignment.json"
            draft_srt = output / "03_draft.srt"
            draft_report = output / "03_draft.csv"
            mapping = output / "03_mapping.json"
            final_srt = output / "synthetic_FINAL.srt"
            final_report = output / "synthetic_FINAL.csv"
            qa = output / "synthetic_FINAL_QA.json"
            review = output / "synthetic_REVIEW.csv"

            run_pipeline(
                root,
                "prepare",
                "--task-manifest", str(manifest),
                "--audio", str(audio),
                "--srt", str(source_srt),
                "--song-list", str(song_list),
                "--lyrics-dir", str(lyrics_dir),
                "--out-dir", str(prepare_dir),
            )
            run_pipeline(
                root,
                "audio-align",
                "--task-manifest", str(manifest),
                "--audio", str(audio),
                "--srt", str(source_srt),
                "--song-list", str(song_list),
                "--lyrics-dir", str(lyrics_dir),
                "--source-dir", str(source_audio_dir),
                "--out", str(alignment),
                "--sample-rate", "2000",
                "--window-seconds", "2",
                "--step-seconds", "1",
                "--candidate-count", "4",
            )
            run_pipeline(
                root,
                "build",
                "--task-manifest", str(manifest),
                "--srt", str(source_srt),
                "--song-list", str(song_list),
                "--lyrics-dir", str(lyrics_dir),
                "--audio-alignment", str(alignment),
                "--out-srt", str(draft_srt),
                "--out-report", str(draft_report),
                "--out-mapping", str(mapping),
            )
            run_pipeline(
                root,
                "finalize",
                "--task-manifest", str(manifest),
                "--srt", str(source_srt),
                "--song-list", str(song_list),
                "--lyrics-dir", str(lyrics_dir),
                "--audio-alignment", str(alignment),
                "--in-report", str(draft_report),
                "--manual-overrides", initialized["manual_overrides"],
                "--out-srt", str(final_srt),
                "--out-report", str(final_report),
            )
            run_pipeline(
                root,
                "qa",
                "--task-manifest", str(manifest),
                "--source-srt", str(source_srt),
                "--final-srt", str(final_srt),
                "--report", str(final_report),
                "--song-list", str(song_list),
                "--lyrics-dir", str(lyrics_dir),
                "--audio-alignment", str(alignment),
                "--manual-overrides", initialized["manual_overrides"],
                "--regression-cases", initialized["regression_cases"],
                "--out", str(qa),
                "--out-review", str(review),
            )

            alignment_payload = json.loads(alignment.read_text(encoding="utf-8"))
            mapping_payload = json.loads(mapping.read_text(encoding="utf-8"))
            qa_payload = json.loads(qa.read_text(encoding="utf-8"))
            with final_report.open(encoding="utf-8-sig", newline="") as handle:
                report_rows = list(csv.DictReader(handle))

            self.assertEqual(alignment_payload["algorithm_version"], "3.9")
            self.assertEqual(alignment_payload["task_fingerprint_sha256"], fingerprint)
            self.assertEqual(mapping_payload["task_fingerprint_sha256"], fingerprint)
            self.assertTrue(report_rows)
            self.assertEqual(
                {row["task_fingerprint_sha256"] for row in report_rows},
                {fingerprint},
            )
            self.assertTrue(qa_payload["structurally_valid"])
            self.assertTrue(qa_payload["fully_reviewed"])
            self.assertTrue(qa_payload["publish_ready"])
            self.assertEqual(qa_payload["release_status"], "ready")

    def test_reviewed_middle_cut_controls_build_projection_and_qa(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_root = root / "private" / "middle-cut" / "input"
            lyrics_dir = input_root / "lyrics"
            output = root / "output" / "middle-cut"
            lyrics_dir.mkdir(parents=True)
            output.mkdir(parents=True)
            source_srt = input_root / "source.srt"
            audio = input_root / "mix.wav"
            song_list = input_root / "songs.txt"
            lyric = lyrics_dir / "Signal.lrc"

            source_srt.write_text(
                "1\n00:00:00,000 --> 00:00:02,700\nfirst retained\n\n"
                "2\n00:00:03,000 --> 00:00:05,700\nsecond retained\n\n"
                "3\n00:00:06,000 --> 00:00:08,700\nfourth retained\n\n"
                "4\n00:00:09,000 --> 00:00:11,700\nfifth retained\n",
                encoding="utf-8",
            )
            song_list.write_text(
                "00:00 Synthetic Artist - Signal\n", encoding="utf-8"
            )
            lyric.write_text(
                "[00:00.00]first retained\n"
                "[00:03.00]second retained\n"
                "[00:06.00]third removed by edit\n"
                "[00:09.00]fourth retained\n"
                "[00:12.00]fifth retained\n",
                encoding="utf-8",
            )
            write_synthetic_wave(audio)

            initialized = init_task(
                root,
                "middle-cut",
                source_srt=source_srt,
                audio=audio,
                song_list=song_list,
                lyrics_dir=lyrics_dir,
            )
            manifest_path = Path(initialized["task_manifest"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            overrides_path = Path(initialized["manual_overrides"])
            overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
            edit = {
                "type": "forward_source_cut",
                "status": "review",
                "mix_time": 6.0,
                "source_start": 6.0,
                "source_end": 9.0,
                "skipped_source_seconds": 3.0,
            }
            overrides["_audio_edit_reviews"] = [
                {
                    "track": "Signal",
                    "mix_time": 6.0,
                    "source_start": 6.0,
                    "source_end": 9.0,
                    "status": "confirmed",
                    "evidence": "synthetic exact cut",
                }
            ]
            write_json_atomic(overrides_path, overrides)

            raw_alignment = output / "02_audio_alignment.json"
            reviewed_alignment = output / "02_audio_alignment_reviewed.json"
            draft_srt = output / "03_build.srt"
            draft_report = output / "03_build.csv"
            mapping = output / "03_mapping.json"
            final_srt = output / "middle-cut_FINAL.srt"
            final_report = output / "middle-cut_FINAL.csv"
            qa_path = output / "middle-cut_FINAL_QA.json"
            review_path = output / "middle-cut_REVIEW.csv"
            regression_path = Path(initialized["regression_cases"])

            write_json_atomic(
                raw_alignment,
                {
                    "algorithm_version": "3.9",
                    "task_fingerprint_sha256": initialized[
                        "task_fingerprint_sha256"
                    ],
                    "tracks": [
                        {
                            "track": {
                                "index": 1,
                                "start_ms": 0,
                                "end_ms": 11700,
                                "artist": "Synthetic Artist",
                                "title": "Signal",
                                "lrc_path": str(lyric.resolve()),
                            },
                            "bpm_tempo_ratio": 1.0,
                            "path": [
                                {
                                    "mix_center": 1.0,
                                    "selected": {
                                        "source_center": 1.0,
                                        "ncc": 0.9,
                                    },
                                },
                                {
                                    "mix_center": 4.0,
                                    "selected": {
                                        "source_center": 4.0,
                                        "ncc": 0.9,
                                    },
                                },
                                {
                                    "mix_center": 7.0,
                                    "selected": {
                                        "source_center": 10.0,
                                        "ncc": 0.9,
                                    },
                                },
                                {
                                    "mix_center": 10.0,
                                    "selected": {
                                        "source_center": 13.0,
                                        "ncc": 0.9,
                                    },
                                },
                            ],
                            "segments": [],
                            "edit_candidates": [edit],
                        }
                    ],
                },
            )

            failed = run_pipeline(
                root,
                "build",
                "--task-manifest", str(manifest_path),
                "--srt", str(source_srt),
                "--song-list", str(song_list),
                "--lyrics-dir", str(lyrics_dir),
                "--audio-alignment", str(raw_alignment),
                "--out-srt", str(draft_srt),
                "--out-report", str(draft_report),
                "--out-mapping", str(mapping),
                expected=1,
            )
            self.assertIn("review-audio-edits", failed.stderr)

            run_pipeline(
                root,
                "review-audio-edits",
                "--task-manifest", str(manifest_path),
                "--audio-alignment", str(raw_alignment),
                "--manual-overrides", str(overrides_path),
                "--out", str(reviewed_alignment),
            )
            run_pipeline(
                root,
                "build",
                "--task-manifest", str(manifest_path),
                "--srt", str(source_srt),
                "--song-list", str(song_list),
                "--lyrics-dir", str(lyrics_dir),
                "--audio-alignment", str(reviewed_alignment),
                "--out-srt", str(draft_srt),
                "--out-report", str(draft_report),
                "--out-mapping", str(mapping),
            )
            run_pipeline(
                root,
                "finalize",
                "--task-manifest", str(manifest_path),
                "--srt", str(source_srt),
                "--song-list", str(song_list),
                "--lyrics-dir", str(lyrics_dir),
                "--audio-alignment", str(reviewed_alignment),
                "--in-report", str(draft_report),
                "--manual-overrides", str(overrides_path),
                "--out-srt", str(final_srt),
                "--out-report", str(final_report),
            )
            write_json_atomic(
                regression_path,
                {
                    **qa_metadata(manifest, "regression_cases"),
                    "cases": [
                        {
                            "id": "removed-middle-lyric",
                            "kind": "absent_text",
                            "start_ms": 0,
                            "end_ms": 11700,
                            "text": "third removed by edit",
                        },
                        {
                            "id": "post-cut-line",
                            "kind": "interval_text",
                            "start_ms": 6000,
                            "end_ms": 8700,
                            "text": "fourth retained",
                        },
                    ],
                },
            )
            run_pipeline(
                root,
                "qa",
                "--task-manifest", str(manifest_path),
                "--source-srt", str(source_srt),
                "--final-srt", str(final_srt),
                "--report", str(final_report),
                "--song-list", str(song_list),
                "--lyrics-dir", str(lyrics_dir),
                "--audio-alignment", str(reviewed_alignment),
                "--manual-overrides", str(overrides_path),
                "--regression-cases", str(regression_path),
                "--out", str(qa_path),
                "--out-review", str(review_path),
            )

            final_text = final_srt.read_text(encoding="utf-8")
            mapping_payload = json.loads(mapping.read_text(encoding="utf-8"))
            qa_payload = json.loads(qa_path.read_text(encoding="utf-8"))
            cut_events = mapping_payload["mappings"][0]["cut_out_events"]
            self.assertNotIn("third removed by edit", final_text)
            self.assertIn("fourth retained", final_text)
            self.assertEqual([event["lrc_index"] for event in cut_events], [2])
            self.assertEqual(qa_payload["confirmed_audio_cut_count"], 1)
            self.assertEqual(qa_payload["project_regression"]["passed"], 2)
            self.assertTrue(qa_payload["publish_ready"])


if __name__ == "__main__":
    unittest.main()
