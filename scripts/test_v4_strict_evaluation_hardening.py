import json
import math
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.evaluation.strict_workflow import (
    StrictEvaluationError,
    cut_boundary_metrics,
    load_policy,
    match_cut_errors,
    validate_blind_baseline_lock,
)


class V4StrictEvaluationHardeningTests(unittest.TestCase):
    def test_cut_matching_maximizes_match_count_before_error(self):
        # Greedy nearest-pair matching would consume 10<->9 first and leave
        # 0 unmatched. The optimal monotonic assignment is 0<->9, 10<->20.
        errors = match_cut_errors([0.0, 10.0], [9.0, 20.0], 11.0)
        self.assertEqual(len(errors), 2)
        self.assertEqual(errors, [9.0, 10.0])

    def test_cut_boundary_metrics_report_coverage_with_error(self):
        metrics = cut_boundary_metrics(
            [
                {
                    "id": "cut-case",
                    "expected_cuts": [
                        {"time_ms": 1000},
                        {"time_ms": 2000},
                    ],
                    "predicted_cuts": [{"time_ms": 1100}],
                    "cut_tolerance_ms": 500,
                }
            ]
        )
        self.assertEqual(metrics["cut_boundary_expected_count"], 2)
        self.assertEqual(metrics["cut_boundary_predicted_count"], 1)
        self.assertEqual(metrics["cut_boundary_match_count"], 1)
        self.assertEqual(metrics["cut_boundary_reference_coverage"], 0.5)
        self.assertEqual(metrics["cut_boundary_prediction_coverage"], 1.0)
        self.assertEqual(metrics["cut_boundary_mae_ms"], 100.0)

    def test_blind_baseline_revision_and_runtime_are_locked(self):
        selection = {
            "dataset": "private-v1",
            "dataset_revision": "dataset-r1",
            "baseline_candidate_id": "baseline",
            "baseline_candidate_revision": "base-rev-1",
            "baseline_runtime_identity": {
                "algorithm_version": "4.0.0a8",
                "calibration_profile_version": "p1",
                "calibration_profile_id": "a" * 64,
            },
        }
        baseline = {
            "dataset": "private-v1",
            "dataset_revision": "dataset-r1",
            "candidate_id": "baseline",
            "candidate_revision": "base-rev-CHANGED",
            "runtime_identity": selection["baseline_runtime_identity"],
        }
        with self.assertRaisesRegex(StrictEvaluationError, "baseline revision differs"):
            validate_blind_baseline_lock(selection, baseline)

        baseline["candidate_revision"] = "base-rev-1"
        baseline["runtime_identity"] = {
            **selection["baseline_runtime_identity"],
            "algorithm_version": "4.0.0a9",
        }
        with self.assertRaisesRegex(
            StrictEvaluationError, "baseline runtime identity differs"
        ):
            validate_blind_baseline_lock(selection, baseline)

    def test_policy_rejects_negative_and_nonfinite_tolerances(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            negative = root / "negative.json"
            negative.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "split": "blind_test",
                        "gates": [
                            {
                                "scope": "overall",
                                "metric": "line_exact_recall",
                                "direction": "higher",
                                "max_regression_abs": -0.1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StrictEvaluationError, ">= 0"):
                load_policy(negative, "blind_test")

            nonfinite = root / "nonfinite.json"
            nonfinite.write_text(
                '{"schema_version":"1.0","split":"blind_test","gates":'
                '[{"scope":"overall","metric":"line_exact_recall",'
                '"direction":"higher","max_regression_abs":NaN}]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StrictEvaluationError, "finite"):
                load_policy(nonfinite, "blind_test")

    def test_calibration_ranking_contract_is_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policy.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "split": "calibration",
                        "gates": [
                            {
                                "scope": "overall",
                                "metric": "line_exact_recall",
                                "direction": "higher",
                                "max_regression_abs": 0.0,
                            }
                        ],
                        "ranking": [
                            {
                                "scope": "overall",
                                "metric": "boundary_p95_ms",
                                "direction": "sideways",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(StrictEvaluationError, "ranking 0 direction"):
                load_policy(path, "calibration")


if __name__ == "__main__":
    unittest.main()
