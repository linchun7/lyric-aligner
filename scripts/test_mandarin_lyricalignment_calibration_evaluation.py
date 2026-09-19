import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import v4_evaluate_mandarin_lyricalignment_calibration as evaluator


class MandarinLyricAlignmentCalibrationEvaluationTests(unittest.TestCase):
    def _run_artifact(self, root: Path, *, backend_id: str = evaluator.EXPECTED_BACKEND_ID) -> Path:
        payload = {
            "schema_version": evaluator.EXPECTED_RUN_SCHEMA,
            "backend_id": backend_id,
            "partition": "calibration",
            "observer_revision": "r" * 64,
            "automatic_mutation_allowed": False,
            "subtitle_mutation_performed": False,
        }
        payload["artifact_sha256"] = evaluator.metric_engine._sha_json(payload)
        path = root / "run.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_wrong_backend_identity_fails_closed_before_metric_engine(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._run_artifact(Path(temporary), backend_id="wrong")
            with self.assertRaisesRegex(
                evaluator.MandarinLyricAlignmentCalibrationEvaluationError,
                "backend identity",
            ):
                evaluator._load_run(path)

    def test_holdout_run_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._run_artifact(root)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["partition"] = "holdout"
            payload.pop("artifact_sha256")
            payload["artifact_sha256"] = evaluator.metric_engine._sha_json(payload)
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                evaluator.MandarinLyricAlignmentCalibrationEvaluationError,
                "calibration only",
            ):
                evaluator._load_run(path)

    def test_metric_engine_payload_is_relabelled_as_observer_without_holdout(self):
        fake_run = {
            "artifact_sha256": "a" * 64,
            "observer_revision": "r" * 64,
            "backend_id": evaluator.EXPECTED_BACKEND_ID,
        }
        boundary = {
            "ctc_top1": {"prediction_coverage": 1.0},
            "editor": {},
            "sofa": {},
            "hubertfa": {},
            "ctc_vs_editor": {"ctc_wins": 2},
            "ctc_posterior": {"case_count": 8},
            "cases": [
                {
                    "ctc": {"effective_error_ms": 10},
                    "editor": {},
                    "sofa": {},
                    "hubertfa": {},
                    "ctc_vs_editor_effective_delta_ms": -5,
                    "ctc_better_than_editor": True,
                    "ctc_posterior": {"top1_probability": 0.7},
                }
            ],
        }
        fake_core = {
            "evaluator_revision": "e" * 64,
            "calibration_gold_view_artifact_sha256": "g" * 64,
            "source_human_gold_artifact_sha256": "s" * 64,
            "selection_lock_sha256": "l" * 64,
            "final_audio_sha256": "f" * 64,
            "outer_audit_csv_sha256": "o" * 64,
            "baseline_artifacts": {},
            "boundaries": {"start": boundary, "end": boundary},
            "holdout_read_or_run": False,
        }
        with (
            mock.patch.object(evaluator, "_load_run", return_value=fake_run),
            mock.patch.object(evaluator.metric_engine, "evaluate", return_value=fake_core),
        ):
            artifact = evaluator.evaluate(
                run_path=Path("run.json"),
                gold_path=Path("gold.json"),
                outer_audit_path=Path("audit.csv"),
                sofa_start_path=Path("sofa-start.json"),
                sofa_end_path=Path("sofa-end.json"),
                hubertfa_start_path=Path("hubert-start.json"),
                hubertfa_end_path=Path("hubert-end.json"),
            )
        start = artifact["boundaries"]["start"]
        self.assertIn("observer_top1", start)
        self.assertIn("observer_vs_editor", start)
        self.assertIn("observer_posterior", start)
        self.assertNotIn("ctc_top1", start)
        self.assertIn("observer", start["cases"][0])
        self.assertFalse(artifact["holdout_read_or_run"])
        self.assertFalse(artifact["automatic_mutation_allowed"])
        self.assertEqual(len(artifact["artifact_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
