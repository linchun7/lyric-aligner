import unittest

from lyric_aligner.evaluation.gate import (
    EvaluationGateError,
    evaluate_gates,
    select_calibration_candidate,
    validate_blind_selection,
)


def evaluation(candidate_id, *, split="calibration", recall=0.90, p95=300.0, identity="a" * 64):
    return {
        "schema_version": "2.0",
        "candidate_id": candidate_id,
        "evaluated_split": split,
        "dataset_identity": {
            "dataset_ground_truth_sha256": identity,
            "case_count": 10,
            "case_ids_sha256": "b" * 64,
        },
        "overall": {
            "line_exact_recall": recall,
            "cut_boundary_p95_ms": p95,
        },
        "groups": {
            "language:zh": {
                "line_exact_recall": recall,
                "cut_boundary_p95_ms": p95,
            }
        },
    }


def policy(split="calibration"):
    return {
        "schema_version": "1.0",
        "split": split,
        "gates": [
            {
                "scope": "overall",
                "metric": "line_exact_recall",
                "direction": "higher",
                "max_regression_abs": 0.01,
                "min_candidate": 0.88,
            },
            {
                "scope": "overall",
                "metric": "cut_boundary_p95_ms",
                "direction": "lower",
                "max_regression_abs": 50.0,
                "max_candidate": 400.0,
            },
        ],
        "ranking": [
            {
                "scope": "overall",
                "metric": "line_exact_recall",
                "direction": "higher",
            },
            {
                "scope": "overall",
                "metric": "cut_boundary_p95_ms",
                "direction": "lower",
            },
        ],
    }


class V4CalibrationGateTests(unittest.TestCase):
    def test_gate_allows_explicit_small_regression_but_rejects_excess(self):
        baseline = evaluation("baseline", recall=0.90, p95=300.0)
        allowed = evaluation("candidate-a", recall=0.895, p95=340.0)
        rejected = evaluation("candidate-b", recall=0.87, p95=360.0)
        self.assertTrue(evaluate_gates(baseline, allowed, policy())["passed"])
        self.assertFalse(evaluate_gates(baseline, rejected, policy())["passed"])

    def test_calibration_selection_only_ranks_candidates_that_pass_all_gates(self):
        baseline = evaluation("baseline", recall=0.90, p95=300.0)
        weak = evaluation("weak", recall=0.87, p95=250.0)
        good = evaluation("good", recall=0.92, p95=320.0)
        best = evaluation("best", recall=0.93, p95=350.0)
        result = select_calibration_candidate(
            baseline=baseline,
            candidates=[weak, good, best],
            policy=policy(),
        )
        self.assertEqual(result["selected_candidate_id"], "best")
        self.assertFalse(result["candidate_gate_results"]["weak"]["passed"])
        self.assertTrue(result["candidate_gate_results"]["good"]["passed"])

    def test_baseline_and_candidate_must_share_exact_ground_truth_identity(self):
        baseline = evaluation("baseline", identity="a" * 64)
        candidate = evaluation("candidate", identity="c" * 64)
        with self.assertRaisesRegex(EvaluationGateError, "different ground-truth"):
            evaluate_gates(baseline, candidate, policy())

    def test_blind_candidate_must_match_calibration_selected_candidate(self):
        selection = {
            "selected_candidate_id": "chosen",
            "selected_calibration_evaluation_sha256": "d" * 64,
            "selection_payload_sha256": "e" * 64,
        }
        blind = evaluation("other", split="blind_test")
        with self.assertRaisesRegex(EvaluationGateError, "does not match"):
            validate_blind_selection(
                selection=selection,
                candidate_evaluation=blind,
                candidate_evaluation_sha256="f" * 64,
            )

    def test_blind_selection_accepts_locked_candidate_id(self):
        selection = {
            "selected_candidate_id": "chosen",
            "selected_calibration_evaluation_sha256": "d" * 64,
            "selection_payload_sha256": "e" * 64,
        }
        blind = evaluation("chosen", split="blind_test")
        validate_blind_selection(
            selection=selection,
            candidate_evaluation=blind,
            candidate_evaluation_sha256="f" * 64,
        )


if __name__ == "__main__":
    unittest.main()
