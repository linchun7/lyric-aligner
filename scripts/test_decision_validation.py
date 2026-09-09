from __future__ import annotations

import unittest

from lyric_aligner.evaluation.decision_validation import evaluate_timing_decisions


class DecisionValidationTests(unittest.TestCase):
    def test_reports_improvement_harm_rescue_and_missed_opportunity(self):
        records = [
            {
                "id": "a",
                "track": "t1",
                "partition": "blind",
                "boundary_kind": "start",
                "gold_ms": 1000,
                "uncertainty_ms": 0,
                "editor_ms": 1020,
                "old_final_ms": 1800,
                "hybrid_ms": 1040,
                "language": "zh",
            },
            {
                "id": "b",
                "track": "t1",
                "partition": "blind",
                "boundary_kind": "end",
                "gold_ms": 3000,
                "uncertainty_ms": 0,
                "editor_ms": 3020,
                "old_final_ms": 3050,
                "hybrid_ms": 3700,
                "language": "zh",
            },
            {
                "id": "c",
                "track": "t2",
                "partition": "blind",
                "boundary_kind": "start",
                "gold_ms": 5000,
                "uncertainty_ms": 0,
                "editor_ms": 5050,
                "old_final_ms": 5600,
                "hybrid_ms": 5600,
                "alternatives": {"observer_x": 5010},
                "structural_scenario": "crossfade",
            },
        ]
        report = evaluate_timing_decisions(records, manual_tolerance_ms=100)
        overall = report["scopes"]["overall"]
        self.assertEqual(overall["changed_count"], 2)
        self.assertEqual(overall["improved_count"], 1)
        self.assertEqual(overall["regressed_count"], 1)
        self.assertEqual(overall["rescue_over_500ms_count"], 1)
        self.assertEqual(overall["harmful_over_100ms_count"], 1)
        self.assertEqual(overall["new_over_500ms_error_count"], 1)
        self.assertEqual(overall["missed_editor_rescue_over_100ms_count"], 1)
        self.assertEqual(overall["missed_any_candidate_rescue_over_100ms_count"], 1)
        self.assertEqual(
            overall["automatic_change_confusion"],
            {
                "beneficial_change_count": 1,
                "harmful_change_count": 1,
                "borderline_change_count": 0,
                "missed_beneficial_candidate_count": 1,
            },
        )
        self.assertEqual(overall["editor_recovery_confusion"]["false_negative_count"], 2)
        self.assertEqual(overall["manual_repair_needed_before_count"], 2)
        self.assertEqual(overall["manual_repair_needed_after_count"], 2)
        self.assertIn("language:zh", report["scopes"])
        self.assertIn("structural_scenario:crossfade", report["scopes"])

    def test_tolerance_adjustment_controls_manual_repair_metric(self):
        records = [
            {
                "id": "a",
                "track": "t1",
                "partition": "holdout",
                "boundary_kind": "start",
                "gold_ms": 1000,
                "uncertainty_ms": 80,
                "editor_ms": 1000,
                "old_final_ms": 1180,
                "hybrid_ms": 1120,
            }
        ]
        report = evaluate_timing_decisions(records, manual_tolerance_ms=100)
        overall = report["scopes"]["overall"]
        self.assertEqual(overall["manual_repair_needed_before_count"], 0)
        self.assertEqual(overall["manual_repair_needed_after_count"], 0)

    def test_editor_can_be_absent_without_being_fabricated(self):
        records = [
            {
                "id": "no-editor",
                "track": "t1",
                "partition": "blind",
                "boundary_kind": "start",
                "gold_ms": 1000,
                "uncertainty_ms": 0,
                "old_final_ms": 1300,
                "hybrid_ms": 1050,
            }
        ]
        report = evaluate_timing_decisions(records)
        overall = report["scopes"]["overall"]
        self.assertEqual(overall["editor_observed_count"], 0)
        self.assertIsNone(overall["missed_editor_rescue_over_100ms_count"])
        self.assertIsNone(overall["editor_recovery_confusion"])
        self.assertEqual(overall["improved_count"], 1)
        self.assertIsNone(report["records"][0]["editor_ms"])
        self.assertIsNone(report["records"][0]["editor_error_ms"])

    def test_reserved_candidate_name_fails_closed(self):
        records = [
            {
                "id": "a",
                "track": "t1",
                "partition": "blind",
                "boundary_kind": "start",
                "gold_ms": 1000,
                "uncertainty_ms": 0,
                "editor_ms": 1000,
                "old_final_ms": 1000,
                "hybrid_ms": 1000,
                "alternatives": {"editor": 1000},
            }
        ]
        with self.assertRaisesRegex(ValueError, "reserved"):
            evaluate_timing_decisions(records)


if __name__ == "__main__":
    unittest.main()
