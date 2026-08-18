import json
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from task_contract import build_task_manifest, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]
SR = 11025


def synthetic_song(seconds: float = 12.0) -> np.ndarray:
    length = int(seconds * SR)
    audio = np.zeros(length, dtype=np.float32)
    frequencies = [196.0, 246.94, 293.66, 369.99, 440.0, 329.63]
    segment = 2.0
    for index, frequency in enumerate(frequencies):
        start = int(index * segment * SR)
        end = min(length, int((index + 1) * segment * SR))
        t = np.arange(end - start, dtype=np.float32) / SR
        envelope = np.minimum(1.0, np.arange(end - start, dtype=np.float32) / 200.0)
        audio[start:end] = envelope * (
            0.65 * np.sin(2 * np.pi * frequency * t)
            + 0.18 * np.sin(4 * np.pi * frequency * t)
        )
    return audio


def write_wav(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(audio, -0.99, 0.99)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())


class V4RunEndToEndTests(unittest.TestCase):
    def test_one_track_production_run_builds_timeline_and_safely_resumes(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            task = repo / "private" / "synthetic_v4"
            input_dir = task / "input"
            qa_dir = task / "qa"
            lyrics_dir = input_dir / "lyrics"
            source_dir = input_dir / "source-audio"
            for directory in (qa_dir, lyrics_dir, source_dir):
                directory.mkdir(parents=True, exist_ok=True)

            source_srt = input_dir / "source.srt"
            source_srt.write_text(
                "1\n00:00:01,000 --> 00:00:03,000\neditor placeholder\n",
                encoding="utf-8",
            )
            song_list = input_dir / "songs.txt"
            song_list.write_text("00:00 Test Artist - Test Song\n", encoding="utf-8")
            lyric = lyrics_dir / "Test Artist - Test Song.lrc"
            lyric.write_text(
                "[00:02.00]alpha line\n"
                "[00:05.00]beta line\n"
                "[00:08.00]gamma line\n",
                encoding="utf-8",
            )

            source_audio = source_dir / "Test Artist - Test Song.wav"
            mix_audio = input_dir / "mix.wav"
            audio = synthetic_song()
            write_wav(source_audio, audio)
            write_wav(mix_audio, audio)

            manifest = build_task_manifest(
                repo,
                "synthetic_v4",
                source_srt=source_srt,
                audio=mix_audio,
                song_list=song_list,
                lyrics_dir=lyrics_dir,
                source_audio_dir=source_dir,
            )
            manifest_path = qa_dir / "task_manifest.json"
            write_json_atomic(manifest_path, manifest)

            out_dir = repo / "output" / "synthetic_v4" / "v4"
            command = [
                sys.executable,
                str(ROOT / "scripts" / "v4_run.py"),
                "--task-manifest",
                str(manifest_path),
                "--out-dir",
                str(out_dir),
                "--git-commit",
                "synthetic-test",
                "--workers",
                "2",
            ]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, msg=completed.stderr)

            run = json.loads((out_dir / "v4_run.json").read_text(encoding="utf-8"))
            self.assertEqual(run["status"], "ready_for_render")
            self.assertFalse(run["legacy_fallback_used"])
            self.assertEqual(len(run["occurrences"]), 1)
            self.assertEqual(run["transitions"], [])
            self.assertEqual(run["issues"], [])

            occurrence = run["occurrences"][0]
            self.assertFalse(occurrence["mapping_blocked"])
            self.assertGreaterEqual(occurrence["timeline_line_count"], 3)
            timeline_path = Path(occurrence["timeline_path"])
            self.assertTrue(timeline_path.is_file())
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
            self.assertEqual(
                [line["text"] for line in timeline["result"]["lines"]],
                ["alpha line", "beta line", "gamma line"],
            )

            artifact = json.loads(
                (out_dir / "v4_run.artifact.json").read_text(encoding="utf-8")
            )
            self.assertEqual(artifact["stage"], "production_orchestration")
            self.assertEqual(
                artifact["task_fingerprint_sha256"],
                manifest["task_fingerprint_sha256"],
            )
            self.assertGreaterEqual(len(artifact["upstream_artifact_ids"]), 3)

            first_execution = json.loads(
                (out_dir / "cache" / "execution_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(first_execution["resume_enabled"])
            self.assertGreaterEqual(first_execution["executed"], 2)

            # A second invocation with the exact same task + producer commit must
            # reuse at least the expensive coarse artifact. Asset resolution is
            # intentionally fresh across runs and timeline/final lineage is
            # deterministically rebuilt by the authoritative core.
            second = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            second_execution = json.loads(
                (out_dir / "cache" / "execution_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(second_execution["resume_enabled"])
            self.assertGreaterEqual(second_execution["resume_hits"], 1)
            self.assertGreaterEqual(second_execution["memo_hits"], 2)

            second_artifact = json.loads(
                (out_dir / "v4_run.artifact.json").read_text(encoding="utf-8")
            )
            self.assertEqual(second_artifact["artifact_id"], artifact["artifact_id"])


if __name__ == "__main__":
    unittest.main()
