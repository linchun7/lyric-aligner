import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lyric_aligner import __version__
from lyric_aligner.contracts.artifacts import build_artifact_manifest
from task_contract import build_task_manifest, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "v4_plan_asr_second_pass.py"


def run_command(*args: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def artifact(root: Path, payload: Path, stage: str, role: str, fingerprint: str, upstreams=()):
    value = build_artifact_manifest(
        task_fingerprint_sha256=fingerprint,
        stage=stage,
        algorithm_version=__version__,
        outputs=((role, payload),),
        upstream_artifact_ids=tuple(upstreams),
    )
    path = root / f"{payload.name}.{stage}.artifact.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, value


class V4AsrSecondPassEndToEndTests(unittest.TestCase):
    def test_weak_local_job_routes_and_binds_exact_first_pass_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_root = root / "private" / "second-pass-task"
            input_dir = task_root / "input"
            qa_dir = task_root / "qa"
            lyrics_dir = input_dir / "lyrics"
            for directory in (qa_dir, lyrics_dir):
                directory.mkdir(parents=True, exist_ok=True)

            source_srt = input_dir / "source.srt"
            source_srt.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nplaceholder\n",
                encoding="utf-8",
            )
            audio = input_dir / "mix.wav"
            audio.write_bytes(b"synthetic-mix")
            song_list = input_dir / "songs.txt"
            song_list.write_text("00:00 Artist - Song\n", encoding="utf-8")
            (lyrics_dir / "Artist - Song.lrc").write_text(
                "[00:01.00]private lyric\n", encoding="utf-8"
            )
            manifest = build_task_manifest(
                root,
                "second-pass-task",
                source_srt=source_srt,
                audio=audio,
                song_list=song_list,
                lyrics_dir=lyrics_dir,
            )
            manifest_path = qa_dir / "task_manifest.json"
            write_json_atomic(manifest_path, manifest)
            fingerprint = manifest["task_fingerprint_sha256"]

            plan_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "mode": "plan_only",
                "backend_execution_performed": False,
                "source_run_artifact_id": "source-run-artifact",
                "jobs": [
                    {
                        "job_id": "job-1",
                        "occurrence_id": "occ-1",
                        "track_id": "track-1",
                        "ordinal": 1,
                        "priority": "high",
                        "canonical_line_index": 0,
                        "language_profile": "en",
                        "mix_window_ms": [1000, 2500],
                        "source_window_ms": [5000, 6500],
                        "canonical_text_sha256": "a" * 64,
                        "requested_capabilities": ["mix_asr", "word_timestamps"],
                        "reasons": ["editor_boundary_disagreement"],
                    }
                ],
            }
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan_payload), encoding="utf-8")
            plan_artifact_path, plan_artifact = artifact(
                root,
                plan_path,
                "alignment_job_planning",
                "alignment_plan",
                fingerprint,
                upstreams=("source-run-artifact",),
            )

            first_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "backend": "faster_whisper",
                "source_plan_artifact_id": plan_artifact["artifact_id"],
                "source_run_artifact_id": "source-run-artifact",
                "config": {"model_id": "fast-model"},
                "jobs": [
                    {
                        "job_id": "job-1",
                        "canonical_text_support_score": 0.40,
                        "language_probability": 0.50,
                        "segments": [
                            {
                                "avg_logprob": -1.1,
                                "no_speech_prob": 0.8,
                            }
                        ],
                    }
                ],
            }
            first_path = root / "first.json"
            first_path.write_text(json.dumps(first_payload), encoding="utf-8")
            first_artifact_path, first_artifact = artifact(
                root,
                first_path,
                "asr_evidence_local",
                "asr_evidence",
                fingerprint,
                upstreams=(plan_artifact["artifact_id"], "source-run-artifact"),
            )

            output = root / "second.json"
            output_artifact = root / "second.artifact.json"
            result = run_command(
                "--task-manifest", str(manifest_path),
                "--plan", str(plan_path),
                "--plan-artifact", str(plan_artifact_path),
                "--first-pass-evidence", str(first_path),
                "--first-pass-artifact", str(first_artifact_path),
                "--second-pass-model-id", "accuracy-model",
                "--out", str(output),
                "--artifact-out", str(output_artifact),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            out_artifact = json.loads(output_artifact.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "second_pass_plan_only")
            self.assertFalse(payload["policy_calibrated"])
            self.assertFalse(payload["backend_execution_performed"])
            self.assertEqual(payload["scope_policy"], "reuse_exact_first_pass_local_windows")
            self.assertEqual(payload["selected_job_ids"], ["job-1"])
            self.assertEqual(payload["jobs"][0]["mix_window_ms"], [1000, 2500])
            self.assertEqual(payload["jobs"][0]["source_window_ms"], [5000, 6500])
            self.assertEqual(payload["jobs"][0]["first_pass_priority"], "high")
            self.assertEqual(payload["first_pass_model_id"], "fast-model")
            self.assertEqual(payload["second_pass_model_id"], "accuracy-model")
            self.assertEqual(out_artifact["stage"], "asr_second_pass_planning")
            self.assertEqual(
                out_artifact["normalized_config"]["scope_policy"],
                "reuse_exact_first_pass_local_windows",
            )
            self.assertIn(plan_artifact["artifact_id"], out_artifact["upstream_artifact_ids"])
            self.assertIn(first_artifact["artifact_id"], out_artifact["upstream_artifact_ids"])
            self.assertNotIn("private lyric", json.dumps(payload))

            same_model = run_command(
                "--task-manifest", str(manifest_path),
                "--plan", str(plan_path),
                "--plan-artifact", str(plan_artifact_path),
                "--first-pass-evidence", str(first_path),
                "--first-pass-artifact", str(first_artifact_path),
                "--second-pass-model-id", "fast-model",
                "--out", str(root / "same.json"),
                "--artifact-out", str(root / "same.artifact.json"),
            )
            self.assertNotEqual(same_model.returncode, 0)
            self.assertIn("must differ from first-pass model_id", same_model.stderr)

            # Rewriting first-pass payload invalidates its artifact hash before routing.
            first_payload["source_plan_artifact_id"] = "foreign-plan"
            first_path.write_text(json.dumps(first_payload), encoding="utf-8")
            bad = run_command(
                "--task-manifest", str(manifest_path),
                "--plan", str(plan_path),
                "--plan-artifact", str(plan_artifact_path),
                "--first-pass-evidence", str(first_path),
                "--first-pass-artifact", str(first_artifact_path),
                "--second-pass-model-id", "accuracy-model",
                "--out", str(root / "bad.json"),
                "--artifact-out", str(root / "bad.artifact.json"),
            )
            self.assertNotEqual(bad.returncode, 0)


if __name__ == "__main__":
    unittest.main()
