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
PLAN_SCRIPT = ROOT / "scripts" / "v4_plan_alignment.py"
BACKEND_SCRIPT = ROOT / "scripts" / "v4_alignment_backends.py"


def run(script: Path, *args: str):
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class V4AlignmentPlannerEndToEndTests(unittest.TestCase):
    def test_run_issue_plans_local_artifact_without_model_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_root = root / "private" / "planner-task"
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
                "planner-task",
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
                    "window": {"start_ms": 0, "end_ms": 10000},
                    "line_count": 1,
                    "lines": [
                        {
                            "canonical_line_index": 0,
                            "text": "private lyric",
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
            timeline_artifact = build_artifact_manifest(
                task_fingerprint_sha256=fingerprint,
                stage="canonical_timeline_projection",
                algorithm_version=__version__,
                outputs=(("canonical_timeline", timeline_path),),
            )
            timeline_artifact_path = root / "timeline.artifact.json"
            timeline_artifact_path.write_text(
                json.dumps(timeline_artifact), encoding="utf-8"
            )

            run_payload = {
                "schema_version": "1.0",
                "algorithm_version": __version__,
                "task_fingerprint_sha256": fingerprint,
                "calibration_profile_version": "profile-r1",
                "calibration_profile_id": "b" * 64,
                "status": "review_required",
                "legacy_fallback_used": False,
                "occurrences": [
                    {
                        "occurrence_id": "occ-1",
                        "ordinal": 1,
                        "track_id": "track-1",
                        "timeline_path": str(timeline_path),
                        "timeline_artifact_path": str(timeline_artifact_path),
                    }
                ],
                "issues": [
                    {
                        "issue_id": "issue-1",
                        "kind": "transition_overlap",
                        "code": "cross_track_overlap_candidate",
                        "occurrence_id": "occ-1",
                        "interval_start": 3.2,
                        "interval_end": 4.2,
                        "status": "review",
                    }
                ],
            }
            run_path = root / "run.json"
            run_path.write_text(json.dumps(run_payload), encoding="utf-8")
            run_artifact = build_artifact_manifest(
                task_fingerprint_sha256=fingerprint,
                stage="production_orchestration",
                algorithm_version=__version__,
                outputs=(("v4_production_run", run_path),),
                upstream_artifact_ids=(timeline_artifact["artifact_id"],),
            )
            run_artifact_path = root / "run.artifact.json"
            run_artifact_path.write_text(json.dumps(run_artifact), encoding="utf-8")

            plan_path = root / "alignment-plan.json"
            plan_artifact_path = root / "alignment-plan.artifact.json"
            result = run(
                PLAN_SCRIPT,
                "--task-manifest",
                str(manifest_path),
                "--run",
                str(run_path),
                "--run-artifact",
                str(run_artifact_path),
                "--out",
                str(plan_path),
                "--artifact-out",
                str(plan_artifact_path),
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            artifact = json.loads(plan_artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(plan["mode"], "plan_only")
            self.assertFalse(plan["backend_execution_performed"])
            self.assertEqual(plan["summary"]["job_count"], 1)
            self.assertEqual(plan["jobs"][0]["mix_window_ms"], [1700, 5700])
            self.assertEqual(plan["jobs"][0]["execution_state"], "planned_not_executed")
            self.assertNotIn("private lyric", json.dumps(plan))
            self.assertEqual(artifact["stage"], "alignment_job_planning")
            self.assertIn(
                run_artifact["artifact_id"], artifact["upstream_artifact_ids"]
            )
            self.assertIn(
                timeline_artifact["artifact_id"], artifact["upstream_artifact_ids"]
            )

    def test_backend_cli_returns_nonzero_when_required_capability_is_unavailable(self):
        result = run(
            BACKEND_SCRIPT,
            "--external-forced-aligner-command",
            "lyric-aligner-command-that-does-not-exist-xyz",
            "--require-capability",
            "source_forced_alignment",
            "--require-execution-ready",
        )
        self.assertEqual(result.returncode, 2, msg=result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "requirements_unmet")
        self.assertIn(
            "source_forced_alignment", payload["missing_required_capabilities"]
        )
        self.assertFalse(payload["model_loading_performed"])


if __name__ == "__main__":
    unittest.main()
