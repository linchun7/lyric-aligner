import json
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest import mock
from pathlib import Path

import numpy as np

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from lyric_aligner.srt import parse_srt_strict
from task_contract import build_task_manifest, write_json_atomic
import v4_render as RENDER


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
    def test_synthetic_task_renders_evaluation_output_but_blocks_release(self):
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
            render_stdout = json.loads(render_result.stdout)
            self.assertFalse(render_stdout["publish_ready"])
            self.assertEqual(
                render_stdout["segmentation_authority"],
                "canonical_line_evaluation_only",
            )

            cues = parse_srt_strict(final_srt)
            self.assertEqual([cue.text for cue in cues], ["alpha line", "beta line", "gamma line"])
            self.assertTrue(all(cue.end_ms > cue.start_ms for cue in cues))
            self.assertLessEqual(cues[-1].end_ms, 12000)

            qa = json.loads(qa_json.read_text(encoding="utf-8"))
            self.assertFalse(qa["publish_ready"])
            self.assertTrue(qa["passed"])
            self.assertTrue(qa["structurally_valid"])
            self.assertEqual(
                qa["segmentation_authority"],
                "canonical_line_evaluation_only",
            )
            self.assertEqual(
                qa["release_blocked_reason"],
                "editor_cue_reconciliation_required",
            )
            self.assertEqual(qa["review_candidate_count"], 0)
            self.assertEqual(qa["algorithm_version"], __version__)
            self.assertEqual(qa["source_run_stage"], "production_orchestration")

            render_manifest = json.loads(render_artifact.read_text(encoding="utf-8"))
            self.assertEqual(
                render_manifest["normalized_config"]["segmentation_authority"],
                "canonical_line_evaluation_only",
            )
            self.assertFalse(render_manifest["evidence"]["publish_ready"])

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
            self.assertNotEqual(release_result.returncode, 0)
            self.assertIn(
                "no editor-reconciled segmentation authority",
                release_result.stderr,
            )
            self.assertFalse(release_manifest.exists())

            # Exercise the replayable review path without re-running audio stages.
            # Clearing an unrelated transition review can make the evidence run
            # ready for evaluation rendering, but it must not grant subtitle
            # segmentation authority or production release eligibility.
            production_artifact_path = out_dir / "v4_run.artifact.json"
            production_artifact = json.loads(
                production_artifact_path.read_text(encoding="utf-8")
            )
            occurrence_id = run_payload["occurrences"][0]["occurrence_id"]
            review_base = json.loads(json.dumps(run_payload))
            review_base["status"] = "review_required"
            review_base["transitions"] = [
                {
                    "left_occurrence_id": occurrence_id,
                    "right_occurrence_id": "synthetic-review-shadow",
                    "blocked": True,
                }
            ]
            review_base["issues"] = [
                {
                    "kind": "transition",
                    "left_occurrence_id": occurrence_id,
                    "right_occurrence_id": "synthetic-review-shadow",
                    "status": "review",
                    "reason": "synthetic transition candidate for review contract testing",
                    "overlap_candidate_count": 1,
                }
            ]
            review_base_path = out_dir / "review_base_run.json"
            review_base_path.write_text(json.dumps(review_base), encoding="utf-8")
            review_base_artifact = build_artifact_manifest(
                task_fingerprint_sha256=manifest["task_fingerprint_sha256"],
                stage="production_orchestration",
                algorithm_version=__version__,
                outputs=(("v4_production_run", review_base_path),),
                normalized_config=production_artifact["normalized_config"],
                upstream_artifact_ids=tuple(production_artifact["upstream_artifact_ids"]),
                evidence={"status": "review_required", "synthetic_review_fixture": True},
            )
            review_base_artifact_path = out_dir / "review_base_run.artifact.json"
            review_base_artifact_path.write_text(
                json.dumps(review_base_artifact), encoding="utf-8"
            )

            decisions_path = out_dir / "review_decisions.json"
            template_result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_review.py"),
                    "template",
                    "--task-manifest",
                    str(manifest_path),
                    "--run",
                    str(review_base_path),
                    "--run-artifact",
                    str(review_base_artifact_path),
                    "--out",
                    str(decisions_path),
                ]
            )
            self.assertEqual(template_result.returncode, 0, msg=template_result.stderr)
            decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
            decisions["review_items"][0]["decision"] = {
                "action": "resolved_clear",
                "rationale": "Synthetic reviewer rejected the transition overlap candidate.",
            }
            decisions_path.write_text(json.dumps(decisions), encoding="utf-8")

            reviewed_run_path = out_dir / "reviewed_run.json"
            reviewed_artifact_path = out_dir / "reviewed_run.artifact.json"
            apply_result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_review.py"),
                    "apply",
                    "--task-manifest",
                    str(manifest_path),
                    "--run",
                    str(review_base_path),
                    "--run-artifact",
                    str(review_base_artifact_path),
                    "--decisions",
                    str(decisions_path),
                    "--out",
                    str(reviewed_run_path),
                    "--artifact-out",
                    str(reviewed_artifact_path),
                    "--git-commit",
                    "synthetic-review-render-test",
                ]
            )
            self.assertEqual(apply_result.returncode, 0, msg=apply_result.stderr)
            reviewed_run = json.loads(reviewed_run_path.read_text(encoding="utf-8"))
            self.assertEqual(reviewed_run["status"], "ready_for_render")
            self.assertEqual(reviewed_run["issues"], [])

            reviewed_final_srt = final_dir / "REVIEWED_FINAL.srt"
            reviewed_report = final_dir / "REVIEWED_FINAL.csv"
            reviewed_qa = final_dir / "REVIEWED_FINAL.qa.json"
            reviewed_render_artifact = final_dir / "REVIEWED_FINAL.render.artifact.json"
            reviewed_render_result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_render.py"),
                    "--task-manifest",
                    str(manifest_path),
                    "--run",
                    str(reviewed_run_path),
                    "--run-artifact",
                    str(reviewed_artifact_path),
                    "--track-assets",
                    str(out_dir / "assets" / "track_assets.json"),
                    "--asset-artifact",
                    str(out_dir / "assets" / "track_assets.artifact.json"),
                    "--final-srt",
                    str(reviewed_final_srt),
                    "--report",
                    str(reviewed_report),
                    "--qa-json",
                    str(reviewed_qa),
                    "--artifact-out",
                    str(reviewed_render_artifact),
                    "--git-commit",
                    "synthetic-review-render-test",
                ]
            )
            self.assertEqual(
                reviewed_render_result.returncode,
                0,
                msg=reviewed_render_result.stderr,
            )
            reviewed_qa_payload = json.loads(reviewed_qa.read_text(encoding="utf-8"))
            self.assertFalse(reviewed_qa_payload["publish_ready"])
            self.assertEqual(
                reviewed_qa_payload["segmentation_authority"],
                "canonical_line_evaluation_only",
            )
            self.assertEqual(
                reviewed_qa_payload["source_run_stage"], "review_resolution"
            )
            self.assertEqual(
                [cue.text for cue in parse_srt_strict(reviewed_final_srt)],
                ["alpha line", "beta line", "gamma line"],
            )


class V4RenderAtomicWriteTests(unittest.TestCase):
    def test_atomic_text_replace_failure_preserves_existing_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "final.srt"
            original = b"existing-final-srt\n"
            target.write_bytes(original)

            with mock.patch.object(RENDER.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    RENDER._atomic_write_text(
                        target,
                        "1\n00:00:00,000 --> 00:00:01,000\nnew\n",
                        encoding="utf-8-sig",
                        newline="\n",
                    )

            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(root.glob(f".{target.name}.*.tmp")), [])

    def test_atomic_csv_replace_failure_preserves_existing_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "audit.csv"
            original = b"existing,audit\r\n"
            target.write_bytes(original)

            with mock.patch.object(RENDER.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    RENDER._atomic_write_csv(
                        target,
                        fieldnames=["position", "text"],
                        rows=[{"position": 1, "text": "new"}],
                    )

            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(root.glob(f".{target.name}.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
