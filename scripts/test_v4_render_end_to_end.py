import json
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from lyric_aligner import __version__
from lyric_aligner.srt import parse_srt_strict
from task_contract import build_task_manifest, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]
SR = 11025


def synthetic_song(seconds: float = 12.0) -> np.ndarray:
    length = int(seconds * SR)
    audio = np.zeros(length, dtype=np.float32)
    frequencies = [196.0, 246.94, 293.66, 369.99, 440.0, 329.63]
    for index, frequency in enumerate(frequencies):
        start = int(index * 2.0 * SR)
        end = min(length, int((index + 1) * 2.0 * SR))
        t = np.arange(end - start, dtype=np.float32) / SR
        audio[start:end] = (
            0.65 * np.sin(2 * np.pi * frequency * t)
            + 0.18 * np.sin(4 * np.pi * frequency * t)
        )
    return audio


def write_wav(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -0.99, 0.99) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())


def run_command(command):
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class V4RenderEndToEndTests(unittest.TestCase):
    def test_synthetic_task_runs_from_manifest_to_release_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            task = repo / "private" / "synthetic_v4_render"
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
            (lyrics_dir / "Test Artist - Test Song.lrc").write_text(
                "[00:02.00]alpha line\n"
                "[00:05.00]beta line\n"
                "[00:08.00]gamma line\n",
                encoding="utf-8",
            )

            audio = synthetic_song()
            source_audio = source_dir / "Test Artist - Test Song.wav"
            mix_audio = input_dir / "mix.wav"
            write_wav(source_audio, audio)
            write_wav(mix_audio, audio)

            manifest = build_task_manifest(
                repo,
                "synthetic_v4_render",
                source_srt=source_srt,
                audio=mix_audio,
                song_list=song_list,
                lyrics_dir=lyrics_dir,
                source_audio_dir=source_dir,
            )
            manifest_path = qa_dir / "task_manifest.json"
            write_json_atomic(manifest_path, manifest)

            out_dir = repo / "output" / "synthetic_v4_render" / "v4"
            run_result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_run.py"),
                    "--task-manifest",
                    str(manifest_path),
                    "--out-dir",
                    str(out_dir),
                    "--git-commit",
                    "synthetic-render-test",
                ]
            )
            self.assertEqual(run_result.returncode, 0, msg=run_result.stderr)
            run_payload = json.loads((out_dir / "v4_run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_payload["status"], "ready_for_render")

            final_dir = out_dir / "final"
            final_srt = final_dir / "FINAL.srt"
            report = final_dir / "FINAL.csv"
            qa_json = final_dir / "FINAL.qa.json"
            render_artifact = final_dir / "FINAL.render.artifact.json"
            render_result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_render.py"),
                    "--task-manifest",
                    str(manifest_path),
                    "--run",
                    str(out_dir / "v4_run.json"),
                    "--run-artifact",
                    str(out_dir / "v4_run.artifact.json"),
                    "--track-assets",
                    str(out_dir / "assets" / "track_assets.json"),
                    "--asset-artifact",
                    str(out_dir / "assets" / "track_assets.artifact.json"),
                    "--final-srt",
                    str(final_srt),
                    "--report",
                    str(report),
                    "--qa-json",
                    str(qa_json),
                    "--artifact-out",
                    str(render_artifact),
                    "--git-commit",
                    "synthetic-render-test",
                ]
            )
            self.assertEqual(render_result.returncode, 0, msg=render_result.stderr)

            cues = parse_srt_strict(final_srt)
            self.assertEqual([cue.text for cue in cues], ["alpha line", "beta line", "gamma line"])
            self.assertTrue(all(cue.end_ms > cue.start_ms for cue in cues))
            self.assertLessEqual(cues[-1].end_ms, 12000)

            qa = json.loads(qa_json.read_text(encoding="utf-8"))
            self.assertTrue(qa["publish_ready"])
            self.assertEqual(qa["review_candidate_count"], 0)
            self.assertEqual(qa["algorithm_version"], __version__)

            release_manifest = final_dir / "release.artifact.json"
            release_result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_validate_release.py"),
                    "--task-manifest",
                    str(manifest_path),
                    "--final-srt",
                    str(final_srt),
                    "--report",
                    str(report),
                    "--qa-json",
                    str(qa_json),
                    "--algorithm-version",
                    __version__,
                    "--upstream-artifact",
                    str(render_artifact),
                    "--out-manifest",
                    str(release_manifest),
                    "--git-commit",
                    "synthetic-render-test",
                ]
            )
            self.assertEqual(release_result.returncode, 0, msg=release_result.stderr)
            release = json.loads(release_manifest.read_text(encoding="utf-8"))
            self.assertEqual(release["stage"], "release")
            self.assertEqual(release["algorithm_version"], __version__)
            self.assertEqual(
                release["task_fingerprint_sha256"],
                manifest["task_fingerprint_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
