import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.v4_adjudicate_calibrated_alignment import ADJUDICATION_BUNDLE_SCHEMA_VERSION
from scripts.v4_materialize_calibrated_alignment import (
    materialize_from_paths,
    sha256_json,
)
from scripts.v4_plan_boundary_refinement import sha256_file


class CalibratedAlignmentMaterializationCliTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source = self.root / "source.srt"
        self.audio = self.root / "final.wav"
        self.report = self.root / "report.csv"
        self.manifest = self.root / "task.manifest.json"
        self.plan_path = self.root / "plan.json"
        self.evidence_path = self.root / "evidence.json"
        self.decisions_path = self.root / "decisions.json"
        self.bundle_path = self.root / "bundle.json"
        self.source.write_text(
            "1\n00:00:01,000 --> 00:00:04,000\nalpha beta\n",
            encoding="utf-8",
        )
        self.audio.write_bytes(b"fixture-final-audio")
        self._write_report(start_ms=1000, end_ms=4000, text="alpha beta")
        self.task_fingerprint = "a" * 64
        self.source_sha = sha256_file(self.source)
        self.audio_sha = sha256_file(self.audio)
        self.manifest.write_text(
            json.dumps(
                {
                    "task_fingerprint_sha256": self.task_fingerprint,
                    "inputs": {
                        "source_srt": {
                            "kind": "file",
                            "path": str(self.source),
                            "sha256": self.source_sha,
                        },
                        "audio": {
                            "kind": "file",
                            "path": str(self.audio),
                            "sha256": self.audio_sha,
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _write_report(self, *, start_ms, end_ms, text):
        with self.report.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["kind", "original_cue", "start_ms", "end_ms", "text", "lrc_indices"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "kind": "existing",
                    "original_cue": "1",
                    "start_ms": str(start_ms),
                    "end_ms": str(end_ms),
                    "text": text,
                    "lrc_indices": "10",
                }
            )

    def _prepare_artifacts(self, *, mode="outer", structural=None):
        plan = {
            "schema_version": "fixture-plan",
            "task_fingerprint_sha256": self.task_fingerprint,
            "source_srt_sha256": self.source_sha,
            "final_audio_sha256": self.audio_sha,
            "report_sha256": sha256_file(self.report),
            "jobs": [],
        }
        evidence = {"schema_version": "fixture-evidence", "records": []}
        summary_key = "automatic_mutation_count" if mode == "outer" else "automatic_split_boundary_count"
        decisions = {
            "schema_version": "fixture-decisions",
            "summary": {summary_key: 1},
            "decisions": [],
        }
        structural = list(structural or [])
        bundle = {
            "schema_version": ADJUDICATION_BUNDLE_SCHEMA_VERSION,
            "mode": mode,
            "plan_sha256": sha256_json(plan),
            "final_audio_sha256": self.audio_sha,
            "calibration_suite_sha256": "c" * 64,
            "backend_profile_ids": ["sofa_mandarin_v1", "hubertfa_mandarin_v1"],
            "structural_confirmations": structural,
            "evidence_sha256": sha256_json(evidence),
            "decisions_sha256": sha256_json(decisions),
            "subtitle_mutation_performed": False,
        }
        bundle["bundle_sha256"] = sha256_json(bundle)
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        self.evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        self.decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
        self.bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        return plan, evidence, decisions, bundle

    def _run(self, *, mode, out_name):
        return materialize_from_paths(
            mode=mode,
            task_manifest_path=self.manifest,
            report_path=self.report,
            plan_path=self.plan_path,
            evidence_path=self.evidence_path,
            decisions_path=self.decisions_path,
            bundle_path=self.bundle_path,
            out_dir=self.root / out_name,
        )

    def test_outer_materialization_uses_structural_confirmations_from_bound_bundle(self):
        self._prepare_artifacts(mode="outer", structural=["boundary-a"])
        captured = {}

        def fake_apply(**kwargs):
            captured.update(kwargs)
            row = dict(kwargs["report_rows"][0])
            row["start_ms"] = 1100
            row["boundary_authority"] = "audio_verified_boundary_v1"
            return [row]

        with patch(
            "scripts.v4_materialize_calibrated_alignment.apply_boundary_decisions_to_rows",
            side_effect=fake_apply,
        ):
            artifact = self._run(mode="outer", out_name="outer-materialized")
        self.assertEqual(tuple(captured["structural_confirmations"]), ("boundary-a",))
        self.assertEqual(artifact["structural_confirmations"], ["boundary-a"])
        self.assertEqual(artifact["automatic_mutation_count"], 1)
        self.assertEqual(artifact["adjudication_bundle_sha256"], json.loads(self.bundle_path.read_text())["bundle_sha256"])
        self.assertTrue((self.root / "outer-materialized" / "materialized.srt").is_file())
        self.assertTrue((self.root / "outer-materialized" / "materialized.csv").is_file())

    def test_internal_materialization_accepts_empty_structural_bundle(self):
        self._prepare_artifacts(mode="internal", structural=[])
        with patch(
            "scripts.v4_materialize_calibrated_alignment.apply_internal_segmentation_to_rows",
            return_value=[
                {
                    "kind": "split",
                    "original_cue": "1",
                    "start_ms": 1000,
                    "end_ms": 2500,
                    "text": "alpha",
                    "lrc_indices": "10",
                    "status": "audio_verified_internal_split",
                },
                {
                    "kind": "split",
                    "original_cue": "1",
                    "start_ms": 2500,
                    "end_ms": 4000,
                    "text": "beta",
                    "lrc_indices": "11",
                    "status": "audio_verified_internal_split",
                },
            ],
        ):
            artifact = self._run(mode="internal", out_name="internal-materialized")
        self.assertEqual(artifact["input_row_count"], 1)
        self.assertEqual(artifact["output_row_count"], 2)
        self.assertEqual(artifact["audio_verified_internal_split_row_count"], 2)
        self.assertEqual(artifact["structural_confirmations"], [])

    def test_rehashed_bundle_with_wrong_evidence_sha_is_rejected(self):
        self._prepare_artifacts(mode="outer")
        bundle = json.loads(self.bundle_path.read_text(encoding="utf-8"))
        bundle["evidence_sha256"] = "f" * 64
        bundle.pop("bundle_sha256")
        bundle["bundle_sha256"] = sha256_json(bundle)
        self.bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "another evidence"):
            self._run(mode="outer", out_name="bad-evidence")
        self.assertFalse((self.root / "bad-evidence").exists())

    def test_bundle_sha_tamper_is_rejected(self):
        self._prepare_artifacts(mode="outer")
        bundle = json.loads(self.bundle_path.read_text(encoding="utf-8"))
        bundle["bundle_sha256"] = "0" * 64
        self.bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "bundle SHA"):
            self._run(mode="outer", out_name="bad-bundle-sha")

    def test_decision_replacement_is_rejected_even_if_json_is_valid(self):
        self._prepare_artifacts(mode="outer")
        replacement = json.loads(self.decisions_path.read_text(encoding="utf-8"))
        replacement["summary"]["automatic_mutation_count"] = 999
        self.decisions_path.write_text(json.dumps(replacement), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "another decision"):
            self._run(mode="outer", out_name="bad-decision")

    def test_report_change_after_plan_is_rejected(self):
        self._prepare_artifacts(mode="outer")
        self._write_report(start_ms=1200, end_ms=4000, text="alpha beta")
        with self.assertRaisesRegex(ValueError, "another input report"):
            self._run(mode="outer", out_name="changed-report")

    def test_internal_bundle_cannot_carry_structural_confirmation(self):
        self._prepare_artifacts(mode="internal", structural=["boundary-a"])
        with self.assertRaisesRegex(ValueError, "internal adjudication bundle"):
            self._run(mode="internal", out_name="bad-internal-structural")


if __name__ == "__main__":
    unittest.main()
