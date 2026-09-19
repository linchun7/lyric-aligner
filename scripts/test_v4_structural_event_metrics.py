import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.evaluation.strict_workflow import (
    StrictEvaluationError,
    ground_truth_identity,
    validate_manifest_metadata,
)
from lyric_aligner.evaluation.structural_events import (
    StructuralEventError,
    normalize_structural_events,
    structural_event_metrics,
    structural_event_truth_identity,
    validate_structural_event_case,
)


class StructuralEventMetricTests(unittest.TestCase):
    def test_point_and_interval_events_match_by_kind(self):
        case = {
            "id": "case-1",
            "expected_structural_events": [
                {"kind": "same_track_splice", "time_ms": 1000},
                {"kind": "reorder", "start_ms": 5000, "end_ms": 9000},
            ],
            "predicted_structural_events": [
                {"kind": "same_track_splice", "time_ms": 1120},
                {"kind": "reorder", "start_ms": 5200, "end_ms": 8800},
            ],
            "structural_event_tolerance_ms": 250,
            "structural_event_min_iou": 0.5,
        }
        metrics = structural_event_metrics([case])
        self.assertEqual(metrics["structural_event_expected_count"], 2)
        self.assertEqual(metrics["structural_event_match_count"], 2)
        self.assertEqual(metrics["structural_event_precision"], 1.0)
        self.assertEqual(metrics["structural_event_recall"], 1.0)
        self.assertEqual(metrics["structural_event_point_mae_ms"], 120.0)
        self.assertGreater(metrics["structural_event_interval_mean_iou"], 0.8)

    def test_structural_scope_filters_other_event_kinds(self):
        case = {
            "expected_structural_events": [
                {"kind": "crossfade", "start_ms": 1000, "end_ms": 2000},
                {"kind": "reorder", "start_ms": 5000, "end_ms": 9000},
            ],
            "predicted_structural_events": [
                {"kind": "crossfade", "start_ms": 1100, "end_ms": 1900},
            ],
        }
        crossfade = structural_event_metrics([case], kind_filter="crossfade")
        reorder = structural_event_metrics([case], kind_filter="reorder")
        self.assertEqual(crossfade["structural_event_recall"], 1.0)
        self.assertEqual(reorder["structural_event_recall"], 0.0)
        self.assertEqual(reorder["structural_event_miss_count"], 1)

    def test_none_and_unspecified_scope_detect_false_positive(self):
        case = {
            "expected_structural_events": [],
            "predicted_structural_events": [
                {"kind": "hard_cut", "time_ms": 1000}
            ],
        }
        for scope in ("none", "unspecified"):
            metrics = structural_event_metrics([case], kind_filter=scope)
            self.assertEqual(metrics["structural_event_false_positive_count"], 1)
            self.assertEqual(metrics["structural_event_clean_case_rate"], 0.0)

    def test_event_shape_and_thresholds_fail_closed(self):
        with self.assertRaisesRegex(StructuralEventError, "point event requires exactly"):
            validate_structural_event_case(
                {
                    "expected_structural_events": [
                        {
                            "kind": "hard_cut",
                            "time_ms": 1000,
                            "start_ms": 900,
                        }
                    ]
                }
            )
        with self.assertRaisesRegex(StructuralEventError, "between 0 and 1"):
            validate_structural_event_case({"structural_event_min_iou": 1.2})

    def test_event_identity_is_canonical_and_prediction_is_not_truth(self):
        first = {
            "expected_structural_events": [
                {"kind": "reorder", "start_ms": 5000, "end_ms": 9000},
                {"kind": "hard_cut", "time_ms": 1000},
            ]
        }
        second = {
            "expected_structural_events": list(
                reversed(first["expected_structural_events"])
            )
        }
        self.assertEqual(
            structural_event_truth_identity(first),
            structural_event_truth_identity(second),
        )

    def test_strict_ground_truth_hash_binds_expected_but_not_predicted_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.srt"
            reference.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nprivate line\n",
                encoding="utf-8",
            )
            manifest_path = root / "dataset.json"
            case = {
                "id": "opaque-1",
                "split": "calibration",
                "language": "en",
                "source_group": "sg-1",
                "reference_srt": reference.name,
                "predicted_srt": "prediction.srt",
                "qa_json": "qa.json",
                "audio_duration_seconds": 10.0,
                "expected_structural_events": [
                    {"kind": "reorder", "start_ms": 3000, "end_ms": 6000}
                ],
                "predicted_structural_events": [],
            }
            payload = {
                "schema_version": "1.1",
                "dataset": "structural-events",
                "dataset_revision": "r1",
                "cases": [case],
            }
            baseline = ground_truth_identity(manifest_path, payload, "calibration")
            case["predicted_structural_events"] = [
                {"kind": "reorder", "start_ms": 3100, "end_ms": 5900}
            ]
            prediction_changed = ground_truth_identity(
                manifest_path, payload, "calibration"
            )
            self.assertEqual(
                baseline["dataset_ground_truth_sha256"],
                prediction_changed["dataset_ground_truth_sha256"],
            )
            case["expected_structural_events"] = [
                {"kind": "reorder", "start_ms": 3200, "end_ms": 6000}
            ]
            truth_changed = ground_truth_identity(manifest_path, payload, "calibration")
            self.assertNotEqual(
                baseline["dataset_ground_truth_sha256"],
                truth_changed["dataset_ground_truth_sha256"],
            )

    def test_normalizer_rejects_duplicate_events(self):
        case = {
            "expected_structural_events": [
                {"kind": "hard_cut", "time_ms": 1000},
                {"kind": "hard_cut", "time_ms": 1000},
            ]
        }
        with self.assertRaisesRegex(StructuralEventError, "duplicates"):
            normalize_structural_events(case, "expected_structural_events")

    def test_prediction_requires_frozen_expected_event_list(self):
        payload = {
            "schema_version": "1.1",
            "dataset": "structural-events",
            "dataset_revision": "r1",
            "cases": [
                {
                    "id": "opaque-pred-only",
                    "split": "calibration",
                    "language": "en",
                    "source_group": "sg-pred-only",
                    "reference_srt": "reference.srt",
                    "predicted_srt": "prediction.srt",
                    "qa_json": "qa.json",
                    "structural_scenarios": ["reorder"],
                    "predicted_structural_events": [
                        {"kind": "reorder", "start_ms": 3000, "end_ms": 6000}
                    ],
                }
            ],
        }
        with self.assertRaisesRegex(
            StrictEvaluationError, "requires frozen expected_structural_events"
        ):
            validate_manifest_metadata(payload)

    def test_expected_event_requires_matching_explicit_scenario(self):
        base_case = {
            "id": "opaque-event",
            "split": "calibration",
            "language": "en",
            "source_group": "sg-event",
            "reference_srt": "reference.srt",
            "predicted_srt": "prediction.srt",
            "qa_json": "qa.json",
            "audio_duration_seconds": 10.0,
            "expected_structural_events": [
                {"kind": "reorder", "start_ms": 3000, "end_ms": 6000}
            ],
        }
        payload = {
            "schema_version": "1.1",
            "dataset": "structural-events",
            "dataset_revision": "r1",
            "cases": [dict(base_case)],
        }
        with self.assertRaisesRegex(
            StrictEvaluationError, "requires explicit structural_scenarios"
        ):
            validate_manifest_metadata(payload)

        payload["cases"][0]["structural_scenarios"] = ["detached_tail"]
        with self.assertRaisesRegex(
            StrictEvaluationError, "missing from structural_scenarios"
        ):
            validate_manifest_metadata(payload)

        payload["cases"][0]["structural_scenarios"] = ["reorder"]
        validation = validate_manifest_metadata(payload)
        self.assertEqual(validation["structural_scenario_counts"], {"reorder": 1})

    def test_none_scenario_requires_empty_expected_events(self):
        payload = {
            "schema_version": "1.1",
            "dataset": "structural-events",
            "dataset_revision": "r1",
            "cases": [
                {
                    "id": "opaque-none",
                    "split": "calibration",
                    "language": "en",
                    "source_group": "sg-none",
                    "reference_srt": "reference.srt",
                    "predicted_srt": "prediction.srt",
                    "qa_json": "qa.json",
                    "structural_scenarios": ["none"],
                    "expected_structural_events": [
                        {"kind": "hard_cut", "time_ms": 1000}
                    ],
                }
            ],
        }
        with self.assertRaisesRegex(
            StrictEvaluationError, "none.*empty expected_structural_events"
        ):
            validate_manifest_metadata(payload)


if __name__ == "__main__":
    unittest.main()
