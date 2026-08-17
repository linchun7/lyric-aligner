import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.evaluation.protocol import (
    EvaluationProtocolError,
    augment_evaluation,
    cut_boundary_metrics_by_scope,
    dataset_ground_truth_identity,
    load_dataset_manifest,
    validate_dataset_manifest,
)


class V4EvaluationProtocolTests(unittest.TestCase):
    def _write_case_files(self, root: Path, prefix: str, reference_text: str = "alpha"):
        reference = root / f"{prefix}.reference.srt"
        predicted = root / f"{prefix}.predicted.srt"
        reference.write_text(
            f"1\n00:00:01,000 --> 00:00:02,000\n{reference_text}\n",
            encoding="utf-8",
        )
        predicted.write_text(
            f"1\n00:00:01,050 --> 00:00:02,050\n{reference_text}\n",
            encoding="utf-8",
        )
        return reference, predicted

    def _manifest(self, root: Path, cases):
        path = root / "dataset.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.1",
                    "dataset": "private-calibration-v1",
                    "cases": cases,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_source_group_cannot_cross_calibration_and_blind_test(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cal_ref, cal_pred = self._write_case_files(root, "cal")
            blind_ref, blind_pred = self._write_case_files(root, "blind")
            manifest_path = self._manifest(
                root,
                [
                    {
                        "id": "cal-1",
                        "split": "calibration",
                        "language": "zh",
                        "source_group": "same-song-version",
                        "reference_srt": cal_ref.name,
                        "predicted_srt": cal_pred.name,
                    },
                    {
                        "id": "blind-1",
                        "split": "blind_test",
                        "language": "zh",
                        "source_group": "same-song-version",
                        "reference_srt": blind_ref.name,
                        "predicted_srt": blind_pred.name,
                    },
                ],
            )
            payload = load_dataset_manifest(manifest_path)
            with self.assertRaisesRegex(EvaluationProtocolError, "crosses dataset splits"):
                validate_dataset_manifest(
                    manifest_path,
                    payload,
                    require_source_groups=True,
                )

    def test_ground_truth_identity_changes_when_reference_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference, predicted = self._write_case_files(root, "case")
            manifest_path = self._manifest(
                root,
                [
                    {
                        "id": "cal-1",
                        "split": "calibration",
                        "language": "en",
                        "source_group": "song-a",
                        "reference_srt": reference.name,
                        "predicted_srt": predicted.name,
                    }
                ],
            )
            payload = load_dataset_manifest(manifest_path)
            before = dataset_ground_truth_identity(
                manifest_path,
                payload,
                split="calibration",
            )
            reference.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nbeta\n",
                encoding="utf-8",
            )
            after = dataset_ground_truth_identity(
                manifest_path,
                payload,
                split="calibration",
            )
            self.assertNotEqual(
                before["dataset_ground_truth_sha256"],
                after["dataset_ground_truth_sha256"],
            )

    def test_cut_boundary_metrics_report_mae_and_tail_percentiles(self):
        payload = {
            "schema_version": "1.1",
            "dataset": "private-calibration-v1",
            "cases": [
                {
                    "id": "cal-1",
                    "split": "calibration",
                    "language": "zh",
                    "source_group": "song-a",
                    "reference_srt": "unused",
                    "predicted_srt": "unused",
                    "expected_cuts": [1000, 2000],
                    "predicted_cuts": [1100, 2400],
                    "cut_tolerance_ms": 500,
                }
            ],
        }
        scopes = cut_boundary_metrics_by_scope(payload, selected_split="calibration")
        overall = scopes["overall"]
        self.assertEqual(overall["cut_boundary_match_count"], 2)
        self.assertEqual(overall["cut_boundary_mae_ms"], 250.0)
        self.assertEqual(overall["cut_boundary_p50_ms"], 250.0)
        self.assertEqual(overall["cut_boundary_p95_ms"], 385.0)
        self.assertEqual(overall["cut_boundary_within_500ms_rate"], 1.0)

    def test_augment_evaluation_binds_split_identity_without_lyric_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference, predicted = self._write_case_files(root, "case", "private lyric")
            manifest_path = self._manifest(
                root,
                [
                    {
                        "id": "cal-opaque-1",
                        "split": "calibration",
                        "language": "ja",
                        "source_group": "song-ja-1",
                        "reference_srt": reference.name,
                        "predicted_srt": predicted.name,
                        "expected_cuts": [1000],
                        "predicted_cuts": [1080],
                    }
                ],
            )
            payload = load_dataset_manifest(manifest_path)
            enriched = augment_evaluation(
                {
                    "schema_version": "2.0",
                    "overall": {},
                    "groups": {"language:ja": {}},
                    "cases": [{"id": "cal-opaque-1"}],
                },
                dataset_path=manifest_path,
                dataset_payload=payload,
                selected_split="calibration",
                candidate_id="candidate-a",
                require_source_groups=True,
            )
            serialized = json.dumps(enriched, ensure_ascii=False)
            self.assertNotIn("private lyric", serialized)
            self.assertEqual(enriched["candidate_id"], "candidate-a")
            self.assertEqual(enriched["evaluated_split"], "calibration")
            self.assertTrue(
                enriched["dataset_validation"]["source_group_isolation_enforced"]
            )
            self.assertEqual(enriched["overall"]["cut_boundary_mae_ms"], 80.0)


if __name__ == "__main__":
    unittest.main()
