import hashlib
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
SCRIPT = ROOT / "scripts" / "v4_execute_asr_second_pass.py"


def run_command(*args: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def write_artifact(
    root: Path,
    payload: Path,
    stage: str,
    role: str,
    fingerprint: str,
    upstreams=(),
):
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


class V4AsrSecondPassExecutionEndToEndTests(unittest.TestCase):
    def test_empty_second_pass_plan_never_loads_model_and_strips_retained_private_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_root = root / "private" / "second-exec-task"
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
            audio.write_bytes(b"synthetic-mix-no-model-needed")
            song_list = input_dir / "songs.txt"
            song_list.write_text("00:00 Artist - Song\n", encoding="utf-8")
            lyric_text = "private lyric"
            (lyrics_dir / "Artist - Song.lrc").write_text(
                f"[00:01.00]{lyric_text}\n", encoding="utf-8"
            )
            manifest = build_task_manifest(
                root,
                "second-exec-task",
                source_srt=source_srt,
                audio=audio,
                song_list=song_list,
                lyrics_dir=lyrics_dir,
            )
            manifest_path = qa_dir / "task_manifest.json"
            write_json_atomic(manifest_path, manifest)
            fingerprint = manifest["task_fingerprint_sha256"]

            timeline = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "occurrence_id": "occ-1",
                "track_id": "track-1",
                "result": {
                    "occurrence_id": "occ-1",
                    "track_id": "track-1",
                    "ordinal": 1,
                    "language_profile": "en",
                    "canonical_selection_sha256": "a" * 64,
                    "lines": [
                        {
                            "canonical_line_index": 0,
                            "text": lyric_text,
                            "source_start_ms": 1000,
                            "source_end_ms": 2500,
                            "mix_start_ms": 3000,
                            "mix_end_ms": 4500,
                        }
                    ],
                },
            }
            timeline_path = root / "timeline.json"
            timeline_path.write_text(json.dumps(timeline), encoding="utf-8")
            timeline_artifact_path, timeline_artifact = write_artifact(
                root,
                timeline_path,
                "canonical_timeline_projection",
                "canonical_timeline",
                fingerprint,
            )

            run_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "status": "ready",
                "occurrences": [
                    {
                        "occurrence_id": "occ-1",
                        "ordinal": 1,
                        "track_id": "track-1",
                        "timeline_path": str(timeline_path),
                        "timeline_artifact_path": str(timeline_artifact_path),
                    }
                ],
                "issues": [],
            }
            run_path = root / "run.json"
            run_path.write_text(json.dumps(run_payload), encoding="utf-8")
            run_artifact_path, run_artifact = write_artifact(
                root,
                run_path,
                "production_orchestration",
                "v4_production_run",
                fingerprint,
                upstreams=(timeline_artifact["artifact_id"],),
            )

            text_sha = hashlib.sha256(lyric_text.encode("utf-8")).hexdigest()
            plan_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "mode": "plan_only",
                "backend_execution_performed": False,
                "source_run_artifact_id": run_artifact["artifact_id"],
                "jobs": [
                    {
                        "job_id": "job-1",
                        "occurrence_id": "occ-1",
                        "track_id": "track-1",
                        "ordinal": 1,
                        "priority": "medium",
                        "canonical_line_index": 0,
                        "language_profile": "en",
                        "mix_window_ms": [2500, 5000],
                        "source_window_ms": [500, 3000],
                        "canonical_text_sha256": text_sha,
                        "requested_capabilities": ["mix_asr", "word_timestamps"],
                    }
                ],
            }
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan_payload), encoding="utf-8")
            plan_artifact_path, plan_artifact = write_artifact(
                root,
                plan_path,
                "alignment_job_planning",
                "alignment_plan",
                fingerprint,
                upstreams=(run_artifact["artifact_id"], timeline_artifact["artifact_id"]),
            )

            first_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "backend": "faster_whisper",
                "config": {"model_id": "fast-model", "include_private_text": True},
                "source_plan_artifact_id": plan_artifact["artifact_id"],
                "source_run_artifact_id": run_artifact["artifact_id"],
                "jobs": [
                    {
                        "job_id": "job-1",
                        "occurrence_id": "occ-1",
                        "canonical_line_index": 0,
                        "mix_window_ms": [2500, 5000],
                        "canonical_text_support_score": 0.95,
                        "observed_text": "PRIVATE OBSERVED TEXT",
                        "segments": [
                            {
                                "start_ms": 3000,
                                "end_ms": 4400,
                                "text": "PRIVATE SEGMENT TEXT",
                                "avg_logprob": -0.2,
                                "no_speech_prob": 0.01,
                                "words": [
                                    {
                                        "start_ms": 3050,
                                        "end_ms": 3300,
                                        "text": "PRIVATE WORD",
                                        "probability": 0.9,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
            first_path = root / "first.json"
            first_path.write_text(json.dumps(first_payload), encoding="utf-8")
            first_artifact_path, first_artifact = write_artifact(
                root,
                first_path,
                "asr_evidence_local",
                "asr_evidence",
                fingerprint,
                upstreams=(plan_artifact["artifact_id"], run_artifact["artifact_id"]),
            )

            second_plan_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "mode": "second_pass_plan_only",
                "policy_calibrated": False,
                "backend_execution_performed": False,
                "scope_policy": "reuse_exact_first_pass_local_windows",
                "source_run_artifact_id": run_artifact["artifact_id"],
                "source_plan_artifact_id": plan_artifact["artifact_id"],
                "source_first_pass_artifact_id": first_artifact["artifact_id"],
                "first_pass_model_id": "fast-model",
                "second_pass_model_id": "accuracy-model-that-is-never-loaded",
                "selected_job_ids": [],
                "jobs": [],
            }
            second_plan_path = root / "second-plan.json"
            second_plan_path.write_text(json.dumps(second_plan_payload), encoding="utf-8")
            second_artifact_path, second_artifact = write_artifact(
                root,
                second_plan_path,
                "asr_second_pass_planning",
                "asr_second_pass_plan",
                fingerprint,
                upstreams=(plan_artifact["artifact_id"], first_artifact["artifact_id"]),
            )

            output = root / "composite.json"
            output_artifact = root / "composite.artifact.json"
            result = run_command(
                "--task-manifest", str(manifest_path),
                "--plan", str(plan_path),
                "--plan-artifact", str(plan_artifact_path),
                "--first-pass-evidence", str(first_path),
                "--first-pass-artifact", str(first_artifact_path),
                "--second-pass-plan", str(second_plan_path),
                "--second-pass-plan-artifact", str(second_artifact_path),
                "--run", str(run_path),
                "--run-artifact", str(run_artifact_path),
                "--model-id", "accuracy-model-that-is-never-loaded",
                "--out", str(output),
                "--artifact-out", str(output_artifact),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            out_artifact = json.loads(output_artifact.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "composite_second_pass_evidence")
            self.assertFalse(payload["model_loaded_second_pass"])
            self.assertEqual(payload["second_pass_selected_job_count"], 0)
            self.assertEqual(payload["second_pass_executed_job_count"], 0)
            self.assertEqual(payload["first_pass_retained_job_count"], 1)
            self.assertEqual(payload["jobs"][0]["evidence_pass"], "first")
            serialized = json.dumps(payload)
            self.assertNotIn("PRIVATE OBSERVED TEXT", serialized)
            self.assertNotIn("PRIVATE SEGMENT TEXT", serialized)
            self.assertNotIn("PRIVATE WORD", serialized)
            self.assertNotIn(lyric_text, serialized)
            self.assertEqual(out_artifact["stage"], "asr_evidence_local")
            self.assertIn(second_artifact["artifact_id"], out_artifact["upstream_artifact_ids"])
            self.assertFalse(out_artifact["evidence"]["model_loaded_second_pass"])


if __name__ == "__main__":
    unittest.main()
