import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.v4_plan_internal_segmentation import build_plan_from_paths
from scripts.v4_plan_boundary_refinement import sha256_file


class InternalSegmentationPlanCliTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source = self.root / "source.srt"
        self.audio = self.root / "final.wav"
        self.report = self.root / "report.csv"
        self.manifest = self.root / "task.manifest.json"
        self.source.write_text(
            "1\n00:00:01,000 --> 00:00:04,000\neditor text\n",
            encoding="utf-8",
        )
        self.audio.write_bytes(b"fixture-final-audio")
        segments = [
            {"track_index": 1, "lrc_index": 10, "text": "first line", "projected_ms": 1200},
            {"track_index": 1, "lrc_index": 11, "text": "second line", "projected_ms": 2500},
        ]
        with self.report.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "kind",
                    "original_cue",
                    "start_ms",
                    "end_ms",
                    "text",
                    "lrc_indices",
                    "canonical_segments_json",
                    "status",
                    "evidence",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "kind": "existing",
                    "original_cue": "1",
                    "start_ms": "1000",
                    "end_ms": "4000",
                    "text": "first line second line",
                    "lrc_indices": "10;11",
                    "canonical_segments_json": json.dumps(
                        segments,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "status": "replace_existing_unsplit_boundary_safe",
                    "evidence": "canonical+split_suppressed_no_audio_boundary_authority",
                }
            )
        payload = {
            "task_fingerprint_sha256": "a" * 64,
            "inputs": {
                "source_srt": {
                    "kind": "file",
                    "path": str(self.source),
                    "sha256": sha256_file(self.source),
                },
                "audio": {
                    "kind": "file",
                    "path": str(self.audio),
                    "sha256": sha256_file(self.audio),
                },
            },
        }
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_builds_exact_internal_plan_from_bound_inputs(self):
        plan = build_plan_from_paths(
            task_manifest_path=self.manifest,
            report_path=self.report,
        )
        self.assertEqual(plan["task_fingerprint_sha256"], "a" * 64)
        self.assertEqual(plan["source_srt_sha256"], sha256_file(self.source))
        self.assertEqual(plan["final_audio_sha256"], sha256_file(self.audio))
        self.assertEqual(plan["report_sha256"], sha256_file(self.report))
        self.assertEqual(plan["summary"]["planned_cue_count"], 1)
        self.assertEqual(plan["summary"]["internal_boundary_job_count"], 1)
        job = plan["jobs"][0]
        self.assertEqual(job["boundary_kind"], "internal")
        self.assertEqual(job["canonical_text"], "first line second line")
        self.assertFalse(job["automatic_mutation_allowed"])

    def test_manifest_audio_sha_tamper_is_rejected(self):
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        payload["inputs"]["audio"]["sha256"] = "f" * 64
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA mismatch"):
            build_plan_from_paths(
                task_manifest_path=self.manifest,
                report_path=self.report,
            )

    def test_report_provenance_mismatch_fails_closed_without_jobs(self):
        with self.report.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["text"] = "wrong ownership"
        with self.report.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        plan = build_plan_from_paths(
            task_manifest_path=self.manifest,
            report_path=self.report,
        )
        self.assertEqual(plan["summary"]["candidate_cue_count"], 1)
        self.assertEqual(plan["summary"]["provenance_mismatch_skipped_count"], 1)
        self.assertEqual(plan["summary"]["internal_boundary_job_count"], 0)

    def test_outer_planner_script_bootstraps_repo_imports_from_external_cwd(self):
        repository_root = Path(__file__).resolve().parents[1]
        script = repository_root / "scripts" / "v4_plan_boundary_refinement.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--task-manifest", completed.stdout)


if __name__ == "__main__":
    unittest.main()
