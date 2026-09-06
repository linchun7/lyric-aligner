import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lyric_aligner.evaluation.production_calibration import (
    production_calibration_artifact_is_authoritative,
)
from scripts import v4_build_production_boundary_calibration as cli
from scripts.test_support_production_calibration import human_gold_artifact


class ProductionBoundaryCalibrationCliTests(unittest.TestCase):
    def prediction_artifact(
        self,
        gold,
        *,
        boundary_kind="start",
        language="en",
        null_count=0,
    ):
        family = "final_mix_singing_alignment"
        scope = {
            "boundary_kind": boundary_kind,
            "audio_basis": "final_mix",
            "language": language,
            "backend_version": "1",
            "backend_profile_id": "fixture-profile-v1",
            "window_policy_id": "fixture-window-policy-v1",
            "model_id": "model-1",
            "model_revision": "r1",
            "implementation_revision": "a" * 64,
            "adapter_contract_revision": "b" * 64,
        }
        if boundary_kind == "internal":
            scope["lexical_id"] = "lyric-alignment-lexical"
            scope["lexical_revision"] = "1.0"
        rows = [row for row in gold["records"] if row["boundary_kind"] == boundary_kind]
        records = [
            {
                "id": row["id"],
                "predicted_ms": None if index < null_count else row["gold_ms"] + 20,
            }
            for index, row in enumerate(rows)
        ]
        artifact = {
            "schema_version": cli.PREDICTION_SCHEMA_VERSION,
            "selection_lock_sha256": gold["selection_lock_sha256"],
            "backend_id": "backend-1",
            "family": family,
            "correlation_group": "model-independent-1",
            "scope": scope,
            "record_count": 30,
            "records": records,
            "predictions_sha256": cli.sha256_json(records),
        }
        artifact["artifact_sha256"] = cli.sha256_json(artifact)
        return artifact

    def write_json(self, path: Path, payload) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_cli_builds_production_authoritative_calibration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = human_gold_artifact(language_scope="en")
            prediction = self.prediction_artifact(gold)
            gold_path = root / "gold.json"
            prediction_path = root / "prediction.json"
            out = root / "calibration.json"
            self.write_json(gold_path, gold)
            self.write_json(prediction_path, prediction)
            with patch.object(
                sys,
                "argv",
                [
                    "v4_build_production_boundary_calibration.py",
                    "--gold",
                    str(gold_path),
                    "--predictions",
                    str(prediction_path),
                    "--out",
                    str(out),
                ],
            ):
                self.assertEqual(cli.main(), 0)
            artifact = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(artifact["passed"])
            self.assertEqual(
                artifact["prediction_provenance"]["prediction_artifact_sha256"],
                prediction["artifact_sha256"],
            )
            self.assertTrue(
                production_calibration_artifact_is_authoritative(
                    artifact,
                    backend_id="backend-1",
                    family="final_mix_singing_alignment",
                    correlation_group="model-independent-1",
                    expected_scope=prediction["scope"],
                )
            )

    def test_prediction_language_must_match_human_gold(self):
        gold = human_gold_artifact(language_scope="zh")
        prediction = self.prediction_artifact(gold, language="en")
        with self.assertRaises(cli.ProductionPredictionError):
            cli.validate_prediction_artifact(prediction, human_gold_artifact=gold)

    def test_legacy_prediction_schema_remains_readable_but_is_not_provenance_complete(self):
        gold = human_gold_artifact(language_scope="en")
        prediction = self.prediction_artifact(gold)
        prediction["schema_version"] = cli.LEGACY_PREDICTION_SCHEMA_VERSION
        prediction["scope"].pop("implementation_revision")
        prediction["scope"].pop("adapter_contract_revision")
        prediction.pop("artifact_sha256")
        prediction["artifact_sha256"] = cli.sha256_json(prediction)
        verified = cli.validate_prediction_artifact(prediction, human_gold_artifact=gold)
        self.assertEqual(verified["schema_version"], cli.LEGACY_PREDICTION_SCHEMA_VERSION)
        self.assertFalse(verified["production_provenance_complete"])
        calibration = cli.build_production_calibration_from_prediction_artifact(
            human_gold_artifact=gold,
            prediction_artifact=prediction,
        )
        self.assertTrue(calibration["passed"])
        self.assertFalse(
            production_calibration_artifact_is_authoritative(
                calibration,
                backend_id="backend-1",
                family="final_mix_singing_alignment",
                correlation_group="model-independent-1",
                expected_scope=prediction["scope"],
            )
        )

    def test_prediction_must_keep_all_preselected_ids(self):
        gold = human_gold_artifact(language_scope="en")
        prediction = self.prediction_artifact(gold)
        prediction["records"] = prediction["records"][:-1]
        prediction["record_count"] = 29
        prediction["predictions_sha256"] = cli.sha256_json(prediction["records"])
        prediction.pop("artifact_sha256")
        prediction["artifact_sha256"] = cli.sha256_json(prediction)
        with self.assertRaises(cli.ProductionPredictionError):
            cli.validate_prediction_artifact(prediction, human_gold_artifact=gold)

    def test_prediction_rows_cannot_carry_gold_fields(self):
        gold = human_gold_artifact(language_scope="en")
        prediction = self.prediction_artifact(gold)
        prediction["records"][0]["gold_ms"] = 123
        prediction["predictions_sha256"] = cli.sha256_json(prediction["records"])
        prediction.pop("artifact_sha256")
        prediction["artifact_sha256"] = cli.sha256_json(prediction)
        with self.assertRaises(cli.ProductionPredictionError):
            cli.validate_prediction_artifact(prediction, human_gold_artifact=gold)

    def test_prediction_ms_rejects_json_float_even_when_integral(self):
        gold = human_gold_artifact(language_scope="en")
        prediction = self.prediction_artifact(gold)
        prediction["records"][0]["predicted_ms"] = 123.0
        prediction["predictions_sha256"] = cli.sha256_json(prediction["records"])
        prediction.pop("artifact_sha256")
        prediction["artifact_sha256"] = cli.sha256_json(prediction)
        with self.assertRaises(cli.ProductionPredictionError):
            cli.validate_prediction_artifact(prediction, human_gold_artifact=gold)

    def test_missing_predictions_use_null_and_fail_coverage_instead_of_disappearing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gold = human_gold_artifact(language_scope="en")
            prediction = self.prediction_artifact(gold, null_count=12)
            gold_path = root / "gold.json"
            prediction_path = root / "prediction.json"
            out = root / "calibration.json"
            self.write_json(gold_path, gold)
            self.write_json(prediction_path, prediction)
            with patch.object(
                sys,
                "argv",
                [
                    "v4_build_production_boundary_calibration.py",
                    "--gold",
                    str(gold_path),
                    "--predictions",
                    str(prediction_path),
                    "--out",
                    str(out),
                ],
            ):
                self.assertEqual(cli.main(), 0)
            artifact = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(artifact["passed"])
            self.assertEqual(artifact["summary"]["calibration"]["prediction_coverage"], 0.4)
            self.assertIn("calibration_partition_exceeds_policy", artifact["reasons"])


if __name__ == "__main__":
    unittest.main()
