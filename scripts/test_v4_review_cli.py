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


def run_command(command):
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class V4ReviewCLITests(unittest.TestCase):
    def fixture(self, root: Path):
        task_root = root / "private" / "review-task"
        input_dir = task_root / "input"
        qa_dir = task_root / "qa"
        lyrics_dir = input_dir / "lyrics"
        source_dir = input_dir / "source-audio"
        for directory in (qa_dir, lyrics_dir, source_dir):
            directory.mkdir(parents=True, exist_ok=True)

        source_srt = input_dir / "source.srt"
        source_srt.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nplaceholder\n",
            encoding="utf-8",
        )
        mix = input_dir / "mix.wav"
        mix.write_bytes(b"synthetic-review-mix")
        song_list = input_dir / "songs.txt"
        song_list.write_text("00:00 Artist - Song\n", encoding="utf-8")
        (lyrics_dir / "Artist - Song.lrc").write_text(
            "[00:00.00]line\n", encoding="utf-8"
        )
        (source_dir / "Artist - Song.wav").write_bytes(b"synthetic-source")

        manifest = build_task_manifest(
            root,
            "review-task",
            source_srt=source_srt,
            audio=mix,
            song_list=song_list,
            lyrics_dir=lyrics_dir,
            source_audio_dir=source_dir,
        )
        manifest_path = qa_dir / "task_manifest.json"
        write_json_atomic(manifest_path, manifest)

        run = {
            "schema_version": "1.0",
            "algorithm_version": __version__,
            "task_fingerprint_sha256": manifest["task_fingerprint_sha256"],
            "calibration_profile_version": "profile-test",
            "calibration_profile_id": "p" * 64,
            "status": "review_required",
            "legacy_fallback_used": False,
            "plan": {},
            "occurrences": [{"occurrence_id": "occ-1"}],
            "transitions": [
                {
                    "left_occurrence_id": "occ-1",
                    "right_occurrence_id": "occ-2",
                    "blocked": True,
                }
            ],
            "issues": [
                {
                    "kind": "transition",
                    "left_occurrence_id": "occ-1",
                    "right_occurrence_id": "occ-2",
                    "status": "review",
                    "reason": "adjacent transition has overlap/ambiguity evidence",
                    "overlap_candidate_count": 1,
                }
            ],
        }
        run_path = root / "v4_run.json"
        run_path.write_text(json.dumps(run), encoding="utf-8")
        artifact = build_artifact_manifest(
            task_fingerprint_sha256=manifest["task_fingerprint_sha256"],
            stage="production_orchestration",
            algorithm_version=__version__,
            outputs=(("v4_production_run", run_path),),
            normalized_config={
                "calibration_profile_version": "profile-test",
                "calibration_profile_id": "p" * 64,
                "legacy_fallback": False,
            },
            upstream_artifact_ids=("asset-artifact", "timeline-artifact"),
        )
        artifact_path = root / "v4_run.artifact.json"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        return manifest_path, run_path, artifact_path, artifact

    def test_template_then_apply_creates_replayable_review_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, run_path, run_artifact_path, base_artifact = self.fixture(root)
            decisions = root / "review_decisions.json"
            template_result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_review.py"),
                    "template",
                    "--task-manifest",
                    str(manifest),
                    "--run",
                    str(run_path),
                    "--run-artifact",
                    str(run_artifact_path),
                    "--out",
                    str(decisions),
                ]
            )
            self.assertEqual(template_result.returncode, 0, msg=template_result.stderr)
            template = json.loads(decisions.read_text(encoding="utf-8"))
            self.assertEqual(template["base_run_artifact_id"], base_artifact["artifact_id"])
            [item] = template["review_items"]
            self.assertEqual(
                item["allowed_actions"], ["resolved_clear", "confirmed_overlap"]
            )
            item["decision"] = {
                "action": "resolved_clear",
                "rationale": "Reviewed the transition evidence and rejected the overlap candidate.",
            }
            decisions.write_text(json.dumps(template), encoding="utf-8")

            reviewed_run = root / "reviewed_run.json"
            review_artifact_path = root / "reviewed_run.artifact.json"
            apply_result = run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "v4_review.py"),
                    "apply",
                    "--task-manifest",
                    str(manifest),
                    "--run",
                    str(run_path),
                    "--run-artifact",
                    str(run_artifact_path),
                    "--decisions",
                    str(decisions),
                    "--out",
                    str(reviewed_run),
                    "--artifact-out",
                    str(review_artifact_path),
                    "--git-commit",
                    "synthetic-review-test",
                ]
            )
            self.assertEqual(apply_result.returncode, 0, msg=apply_result.stderr)
            reviewed = json.loads(reviewed_run.read_text(encoding="utf-8"))
            self.assertEqual(reviewed["status"], "ready_for_render")
            self.assertEqual(reviewed["issues"], [])

            artifact = json.loads(review_artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact["stage"], "review_resolution")
            self.assertEqual(artifact["algorithm_version"], __version__)
            roles = {row["role"] for row in artifact["outputs"]}
            self.assertEqual(roles, {"v4_reviewed_run", "review_decisions"})
            upstreams = set(artifact["upstream_artifact_ids"])
            self.assertIn(base_artifact["artifact_id"], upstreams)
            self.assertIn("asset-artifact", upstreams)
            self.assertIn("timeline-artifact", upstreams)


if __name__ == "__main__":
    unittest.main()
