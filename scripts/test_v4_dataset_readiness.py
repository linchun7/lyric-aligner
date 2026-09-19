import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.evaluation.readiness import (
    DatasetReadinessError,
    clone_candidate_manifest,
    inspect_dataset_readiness,
    scaffold_manifest,
    write_scaffold_directories,
)
from lyric_aligner.evaluation.strict_workflow import validate_manifest_metadata


class V4DatasetReadinessTests(unittest.TestCase):
    def _write_manifest(self, root: Path, payload: dict, name="baseline.dataset.json") -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_scaffold_is_strict_metadata_but_not_fake_ready(self):
        payload = scaffold_manifest(
            dataset="opaque-private-set",
            dataset_revision="r1",
            candidate_id="baseline",
            calibration_cases=2,
            blind_cases=2,
        )
        metadata = validate_manifest_metadata(payload)
        self.assertTrue(metadata["source_group_isolation_enforced"])
        self.assertEqual(metadata["split_counts"]["calibration"], 2)
        self.assertEqual(metadata["split_counts"]["blind_test"], 2)
        groups = [row["source_group"] for row in payload["cases"]]
        self.assertEqual(len(groups), len(set(groups)))
        self.assertEqual(payload["scaffold"]["status"], "empty_placeholders_only")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_scaffold_directories(root, payload)
            manifest = self._write_manifest(root, payload)
            report = inspect_dataset_readiness(manifest)
            self.assertTrue(report["metadata_ready"])
            self.assertFalse(report["all_selected_references_ready"])
            self.assertFalse(report["all_selected_evaluations_ready"])
            for split in ("calibration", "blind_test"):
                self.assertFalse(report["splits"][split]["reference_ready"])
                self.assertFalse(report["splits"][split]["prediction_files_ready"])
                self.assertFalse(report["splits"][split]["evaluation_ready"])

    def test_candidate_clone_changes_only_candidate_outputs(self):
        baseline = scaffold_manifest(
            dataset="private-set",
            dataset_revision="r2",
            candidate_id="baseline",
            calibration_cases=1,
            blind_cases=1,
        )
        baseline["cases"][0]["expected_cuts"] = [{"time_ms": 1234}]
        baseline["cases"][0]["predicted_cuts"] = [{"time_ms": 9999}]
        candidate = clone_candidate_manifest(baseline, candidate_id="candidate-a")

        for left, right in zip(baseline["cases"], candidate["cases"]):
            self.assertEqual(left["id"], right["id"])
            self.assertEqual(left["source_group"], right["source_group"])
            self.assertEqual(left["split"], right["split"])
            self.assertEqual(left["reference_srt"], right["reference_srt"])
            self.assertEqual(left.get("expected_cuts"), right.get("expected_cuts"))
            self.assertIn("predictions/candidate-a/", right["predicted_srt"])
            self.assertIn("predictions/candidate-a/", right["qa_json"])
            self.assertEqual(right.get("predicted_cuts"), [])

    def test_selected_split_can_be_ready_while_blind_files_do_not_exist(self):
        payload = scaffold_manifest(
            dataset="private-set",
            dataset_revision="r3",
            candidate_id="candidate-a",
            calibration_cases=1,
            blind_cases=1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root, payload)
            calibration = next(
                row for row in payload["cases"] if row["split"] == "calibration"
            )
            for role in ("reference_srt", "predicted_srt"):
                path = root / calibration[role]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "1\n00:00:00,000 --> 00:00:01,000\nsynthetic\n",
                    encoding="utf-8",
                )
            qa = root / calibration["qa_json"]
            qa.parent.mkdir(parents=True, exist_ok=True)
            qa.write_text(
                json.dumps(
                    {
                        "algorithm_version": "4.0.0a8",
                        "calibration_profile_version": "profile-r1",
                        "calibration_profile_id": "a" * 64,
                    }
                ),
                encoding="utf-8",
            )

            report = inspect_dataset_readiness(manifest, split="calibration")
            self.assertTrue(report["splits"]["calibration"]["evaluation_ready"])
            self.assertTrue(report["all_selected_evaluations_ready"])
            self.assertNotIn("blind_test", report["splits"])

    def test_invalid_or_mixed_qa_identity_blocks_evaluation_readiness(self):
        payload = scaffold_manifest(
            dataset="private-set",
            dataset_revision="r4",
            candidate_id="candidate-a",
            calibration_cases=2,
            blind_cases=1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._write_manifest(root, payload)
            calibration_rows = [
                row for row in payload["cases"] if row["split"] == "calibration"
            ]
            for index, row in enumerate(calibration_rows):
                for role in ("reference_srt", "predicted_srt"):
                    path = root / row[role]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(
                        "1\n00:00:00,000 --> 00:00:01,000\nsynthetic\n",
                        encoding="utf-8",
                    )
                qa = root / row["qa_json"]
                qa.parent.mkdir(parents=True, exist_ok=True)
                qa.write_text(
                    json.dumps(
                        {
                            "algorithm_version": f"4.0.0a{8 + index}",
                            "calibration_profile_version": "profile-r1",
                            "calibration_profile_id": "a" * 64,
                        }
                    ),
                    encoding="utf-8",
                )
            report = inspect_dataset_readiness(manifest, split="calibration")
            row = report["splits"]["calibration"]
            self.assertTrue(row["prediction_files_ready"])
            self.assertFalse(row["runtime_identity_ready"])
            self.assertFalse(row["evaluation_ready"])
            self.assertEqual(row["runtime_identity_variant_count"], 2)

    def test_candidate_id_path_traversal_is_rejected(self):
        with self.assertRaises(DatasetReadinessError):
            scaffold_manifest(
                dataset="private-set",
                dataset_revision="r5",
                candidate_id="../escape",
                calibration_cases=1,
                blind_cases=1,
            )


if __name__ == "__main__":
    unittest.main()
