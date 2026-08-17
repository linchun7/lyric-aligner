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
            self.assertNotIn("private synthetic phrase", serialized)

    def test_missing_cues_reduce_line_exact_recall(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.srt"
            predicted = root / "predicted.srt"
            reference.write_text(
                "".join(
                    f"{index}\n00:00:{index:02d},000 --> 00:00:{index:02d},900\nline {index}\n\n"
                    for index in range(1, 11)
                ),
                encoding="utf-8",
            )
            predicted.write_text(
                "1\n00:00:01,000 --> 00:00:01,900\nline 1\n",
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
            overall = result["overall"]

            self.assertEqual(overall["line_exact_recall"], 0.1)
            self.assertEqual(overall["cue_text_exact_match_rate"], 0.1)
            self.assertEqual(overall["missing_line_rate"], 0.9)

    def test_reordered_units_are_not_perfect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.srt"
            predicted = root / "predicted.srt"
            reference.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nhello\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nworld\n",
                encoding="utf-8",
            )
            predicted.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nworld\n\n"
                "2\n00:00:03,000 --> 00:00:04,000\nhello\n",
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
            overall = result["overall"]

            self.assertLess(overall["unit_f1"], 1.0)
            self.assertGreater(overall["sequence_wer"], 0.0)
            self.assertLess(overall["line_exact_recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
