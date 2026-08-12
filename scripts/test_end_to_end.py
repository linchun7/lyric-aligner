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

            self.assertEqual(alignment_payload["algorithm_version"], "3.8")
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


if __name__ == "__main__":
    unittest.main()
