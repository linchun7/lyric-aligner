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


if __name__ == "__main__":
    unittest.main()
