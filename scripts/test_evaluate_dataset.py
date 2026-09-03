import json
import tempfile
import unittest
from pathlib import Path

from evaluate_dataset import evaluate_manifest


class DatasetEvaluationTests(unittest.TestCase):
    def test_output_contains_metrics_but_not_lyrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.srt"
            predicted = root / "predicted.srt"
            reference.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nprivate synthetic phrase\n",
                encoding="utf-8",
            )
            predicted.write_text(
                "1\n00:00:01,100 --> 00:00:02,100\nprivate synthetic phrase\n",
                encoding="utf-8",
            )
            dataset = root / "dataset.json"
            dataset.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "dataset": "synthetic",
                        "cases": [
                            {
                                "id": "case-1",
                                "split": "blind_test",
                                "language": "en",
                                "reference_srt": "reference.srt",
                                "predicted_srt": "predicted.srt",
                                "audio_duration_seconds": 60,
                                "runtime_seconds": 6,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = evaluate_manifest(dataset)
            serialized = json.dumps(result, ensure_ascii=False)

            self.assertEqual(result["overall"]["unit_f1"], 1.0)
            self.assertEqual(result["overall"]["boundary_mae_ms"], 100.0)
            self.assertEqual(result["overall"]["runtime_per_audio_minute"], 6.0)
            self.assertFalse(any(key.startswith("structural:") for key in result["groups"]))
            self.assertNotIn("structural_scenarios", result["cases"][0])
            self.assertNotIn("private synthetic phrase", serialized)

    def test_missing_nine_of_ten_lines_is_not_reported_as_perfect_exact_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.srt"
            predicted = root / "predicted.srt"
            reference.write_text(
                "\n\n".join(
                    f"{index}\n00:00:{index:02d},000 --> 00:00:{index:02d},500\nline {index}"
                    for index in range(1, 11)
                )
                + "\n",
                encoding="utf-8",
            )
            predicted.write_text(
                "1\n00:00:01,000 --> 00:00:01,500\nline 1\n",
                encoding="utf-8",
            )
            dataset = root / "dataset.json"
            dataset.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "cases": [
                            {
                                "id": "missing-lines",
                                "split": "blind_test",
                                "language": "en",
                                "reference_srt": "reference.srt",
                                "predicted_srt": "predicted.srt",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = evaluate_manifest(dataset)
            self.assertEqual(result["overall"]["line_exact_recall"], 0.1)
            self.assertEqual(result["overall"]["cue_text_exact_match_rate"], 0.1)
            self.assertEqual(result["overall"]["missing_line_rate"], 0.9)

    def test_reordered_same_tokens_are_penalized_by_sequence_metric(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.srt"
            predicted = root / "predicted.srt"
            reference.write_text(
                "1\n00:00:01,000 --> 00:00:01,500\nhello\n\n"
                "2\n00:00:02,000 --> 00:00:02,500\nworld\n",
                encoding="utf-8",
            )
            predicted.write_text(
                "1\n00:00:01,000 --> 00:00:01,500\nworld\n\n"
                "2\n00:00:02,000 --> 00:00:02,500\nhello\n",
                encoding="utf-8",
            )
            dataset = root / "dataset.json"
            dataset.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "cases": [
                            {
                                "id": "reordered",
                                "split": "blind_test",
                                "language": "en",
                                "reference_srt": "reference.srt",
                                "predicted_srt": "predicted.srt",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = evaluate_manifest(dataset)
            self.assertLess(result["overall"]["unit_f1"], 1.0)
            self.assertGreater(result["overall"]["sequence_wer"], 0.0)
            self.assertLess(result["overall"]["line_exact_recall"], 1.0)
            self.assertGreater(result["overall"]["wrong_order_line_count"], 0)

    def test_split_cues_pair_as_one_group_for_boundary_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.srt"
            predicted = root / "predicted.srt"
            reference.write_text(
                "1\n00:00:01,000 --> 00:00:03,000\nhello world\n",
                encoding="utf-8",
            )
            predicted.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nhello\n\n"
                "2\n00:00:02,000 --> 00:00:03,000\nworld\n",
                encoding="utf-8",
            )
            dataset = root / "dataset.json"
            dataset.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "cases": [
                            {
                                "id": "split",
                                "split": "blind_test",
                                "language": "en",
                                "reference_srt": "reference.srt",
                                "predicted_srt": "predicted.srt",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            overall = evaluate_manifest(dataset)["overall"]
            self.assertEqual(overall["split_error_count"], 1)
            self.assertEqual(overall["onset_mae_ms"], 0.0)
            self.assertEqual(overall["offset_mae_ms"], 0.0)

    def test_cut_times_match_by_tolerance_not_shared_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt = "1\n00:00:01,000 --> 00:00:02,000\nline\n"
            (root / "r.srt").write_text(srt, encoding="utf-8")
            (root / "p.srt").write_text(srt, encoding="utf-8")
            dataset = root / "dataset.json"
            dataset.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "cases": [
                            {
                                "id": "cuts",
                                "split": "blind_test",
                                "language": "en",
                                "reference_srt": "r.srt",
                                "predicted_srt": "p.srt",
                                "expected_cuts": [
                                    {"time_ms": 10000},
                                    {"time_ms": 20000},
                                ],
                                "predicted_cuts": [
                                    {"time_ms": 10300},
                                    {"time_ms": 25000},
                                ],
                                "cut_tolerance_ms": 500,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            overall = evaluate_manifest(dataset)["overall"]
            self.assertEqual(overall["cut_precision"], 0.5)
            self.assertEqual(overall["cut_recall"], 0.5)

    def test_overlap_duration_iou_and_track_attribution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt = "1\n00:00:01,000 --> 00:00:02,000\nline\n"
            (root / "r.srt").write_text(srt, encoding="utf-8")
            (root / "p.srt").write_text(srt, encoding="utf-8")
            dataset = root / "dataset.json"
            dataset.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "cases": [
                            {
                                "id": "overlap",
                                "split": "blind_test",
                                "language": "en",
                                "reference_srt": "r.srt",
                                "predicted_srt": "p.srt",
                                "expected_overlaps": [
                                    {
                                        "start_ms": 1000,
                                        "end_ms": 3000,
                                        "tracks": ["a", "b"],
                                    }
                                ],
                                "predicted_overlaps": [
                                    {
                                        "start_ms": 1500,
                                        "end_ms": 3000,
                                        "tracks": ["b", "a"],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            overall = evaluate_manifest(dataset)["overall"]
            self.assertEqual(overall["overlap_duration_precision"], 1.0)
            self.assertEqual(overall["overlap_duration_recall"], 0.75)
            self.assertEqual(overall["overlap_iou"], 0.75)
            self.assertEqual(overall["track_attribution_accuracy"], 1.0)


    def test_schema_1_1_supports_structural_groups_and_synthetic_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt = "1\n00:00:01,000 --> 00:00:02,000\nsynthetic cue\n"
            (root / "r.srt").write_text(srt, encoding="utf-8")
            (root / "p.srt").write_text(srt, encoding="utf-8")
            dataset = root / "dataset.json"
            dataset.write_text(
                json.dumps(
                    {
                        "schema_version": "1.1",
                        "dataset": "synthetic-structural",
                        "cases": [
                            {
                                "id": "splice",
                                "split": "blind_test",
                                "language": "synthetic",
                                "reference_srt": "r.srt",
                                "predicted_srt": "p.srt",
                                "structural_scenarios": ["same_track_splice"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = evaluate_manifest(dataset)
            self.assertEqual(result["schema_version"], "1.1")
            self.assertIn("structural:same_track_splice", result["groups"])
            self.assertIn("language:generic", result["groups"])
            self.assertEqual(
                result["cases"][0]["structural_scenarios"],
                ["same_track_splice"],
            )

    def test_schema_1_0_rejects_structural_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt = "1\n00:00:01,000 --> 00:00:02,000\nline\n"
            (root / "r.srt").write_text(srt, encoding="utf-8")
            (root / "p.srt").write_text(srt, encoding="utf-8")
            dataset = root / "dataset.json"
            dataset.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "cases": [
                            {
                                "id": "old-schema",
                                "split": "blind_test",
                                "language": "en",
                                "reference_srt": "r.srt",
                                "predicted_srt": "p.srt",
                                "structural_scenarios": ["hard_cut"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "requires dataset schema_version 1.1"):
                evaluate_manifest(dataset)

    def test_invalid_structural_scenario_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt = "1\n00:00:01,000 --> 00:00:02,000\nline\n"
            (root / "r.srt").write_text(srt, encoding="utf-8")
            (root / "p.srt").write_text(srt, encoding="utf-8")
            dataset = root / "dataset.json"
            dataset.write_text(
                json.dumps(
                    {
                        "schema_version": "1.1",
                        "cases": [
                            {
                                "id": "bad-structural",
                                "split": "blind_test",
                                "language": "en",
                                "reference_srt": "r.srt",
                                "predicted_srt": "p.srt",
                                "structural_scenarios": ["invented-event"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unsupported structural scenario"):
                evaluate_manifest(dataset)


if __name__ == "__main__":
    unittest.main()
