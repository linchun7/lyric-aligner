import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts import v4_evaluate_wav2vec2_ctc_calibration as evaluator


class Wav2Vec2CTCCalibrationEvaluationTests(unittest.TestCase):
    def _write_hashed_json(self, path: Path, payload: dict) -> Path:
        payload = dict(payload)
        payload["artifact_sha256"] = evaluator._sha_json(payload)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _fixture(self, root: Path) -> dict[str, Path]:
        cases = [f"{index + 1:064x}" for index in range(8)]
        audit = root / "outer.csv"
        with audit.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "case_id",
                    "editor_start_ms_reference_only",
                    "editor_end_ms_reference_only",
                ],
            )
            writer.writeheader()
            for index, case_id in enumerate(cases):
                start = 10000 + index * 10000
                end = start + 5000
                writer.writerow(
                    {
                        "case_id": case_id,
                        "editor_start_ms_reference_only": start + 100,
                        "editor_end_ms_reference_only": end + 200,
                    }
                )
        audit_sha = evaluator.hashlib.sha256(audit.read_bytes()).hexdigest()

        gold_records = []
        run_records = []
        for index, case_id in enumerate(cases):
            start = 10000 + index * 10000
            end = start + 5000
            for kind, value in (("start", start), ("end", end)):
                gold_records.append(
                    {
                        "id": f"{case_id}:{kind}",
                        "case_id": case_id,
                        "kind": "boundary",
                        "boundary_kind": kind,
                        "partition": "calibration",
                        "population": "outer",
                        "track": f"track-{index}",
                        "gold_ms": value,
                        "gold_uncertainty_ms": 50,
                    }
                )
            run_records.append(
                {
                    "case_id": case_id,
                    "status": "aligned",
                    "predicted_start_ms": start,
                    "predicted_end_ms": end,
                    "evidence": {
                        "start": {
                            "hypotheses": [
                                {"boundary_ms": start, "probability": 0.8},
                                {"boundary_ms": start + 100, "probability": 0.1},
                            ],
                            "residual_probability": 0.1,
                            "posterior_entropy_bits": 0.92,
                            "top1_top2_margin": 0.7,
                        },
                        "end": {
                            "hypotheses": [
                                {"boundary_ms": end, "probability": 0.75},
                                {"boundary_ms": end - 100, "probability": 0.15},
                            ],
                            "residual_probability": 0.1,
                            "posterior_entropy_bits": 1.05,
                            "top1_top2_margin": 0.6,
                        },
                    },
                }
            )

        selection = "a" * 64
        final_audio = "b" * 64
        gold_path = self._write_hashed_json(
            root / "gold.json",
            {
                "schema_version": evaluator.CALIBRATION_GOLD_VIEW_SCHEMA_VERSION,
                "selection_lock_sha256": selection,
                "final_audio_sha256": final_audio,
                "outer_audit_csv_sha256": audit_sha,
                "source_human_gold_artifact_sha256": "d" * 64,
                "production_policy": {"fps": 30.0, "catastrophic_error_frames": 15.0},
                "partition": "calibration",
                "population": "outer",
                "record_count": 16,
                "unique_case_count": 8,
                "contains_holdout_records": False,
                "contains_internal_records": False,
                "records": gold_records,
            },
        )
        run_path = self._write_hashed_json(
            root / "run.json",
            {
                "partition": "calibration",
                "selection_lock_sha256": selection,
                "final_audio_sha256": final_audio,
                "outer_audit_csv_sha256": audit_sha,
                "observer_revision": "c" * 64,
                "records": run_records,
                "automatic_mutation_allowed": False,
                "subtitle_mutation_performed": False,
            },
        )

        def baseline(path: Path, kind: str, offset: int, missing_last: bool = False) -> Path:
            records = []
            for index, case_id in enumerate(cases):
                value = 10000 + index * 10000 + (0 if kind == "start" else 5000)
                records.append(
                    {
                        "id": f"{case_id}:{kind}",
                        "predicted_ms": None if missing_last and index == 7 else value + offset,
                    }
                )
            return self._write_hashed_json(path, {"records": records})

        return {
            "run": run_path,
            "gold": gold_path,
            "outer": audit,
            "sofa_start": baseline(root / "sofa_start.json", "start", 300),
            "sofa_end": baseline(root / "sofa_end.json", "end", 400),
            "hubert_start": baseline(root / "hubert_start.json", "start", 500),
            "hubert_end": baseline(root / "hubert_end.json", "end", 600, missing_last=True),
        }

    def test_full_calibration_evaluation_preserves_top1_and_posterior_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            artifact = evaluator.evaluate(
                run_path=paths["run"],
                gold_path=paths["gold"],
                outer_audit_path=paths["outer"],
                sofa_start_path=paths["sofa_start"],
                sofa_end_path=paths["sofa_end"],
                hubertfa_start_path=paths["hubert_start"],
                hubertfa_end_path=paths["hubert_end"],
            )
        start = artifact["boundaries"]["start"]
        end = artifact["boundaries"]["end"]
        self.assertEqual(start["ctc_top1"]["median_effective_error_ms"], 0.0)
        self.assertEqual(start["editor"]["median_effective_error_ms"], 50.0)
        self.assertEqual(end["hubertfa"]["prediction_count"], 7)
        self.assertEqual(start["ctc_vs_editor"]["ctc_wins"], 8)
        self.assertEqual(start["ctc_vs_editor"]["ctc_losses"], 0)
        self.assertEqual(start["ctc_vs_editor"]["mean_ctc_minus_editor_effective_error_ms"], -50.0)
        self.assertEqual(start["ctc_vs_editor"]["best_ctc_improvement_ms"], 50.0)
        self.assertEqual(start["ctc_vs_editor"]["worst_ctc_degradation_ms"], 0.0)
        self.assertEqual(start["cases"][0]["ctc_vs_editor_effective_delta_ms"], -50.0)
        self.assertTrue(start["cases"][0]["ctc_better_than_editor"])
        self.assertAlmostEqual(start["ctc_posterior"]["mean_top1_probability"], 0.8)
        self.assertAlmostEqual(
            start["ctc_posterior"]["mean_selected_probability_mass_within_1_frames"],
            0.8,
        )
        self.assertEqual(len(artifact["evaluator_revision"]), 64)
        self.assertEqual(artifact["source_human_gold_artifact_sha256"], "d" * 64)
        self.assertEqual(len(artifact["calibration_gold_view_artifact_sha256"]), 64)
        self.assertFalse(artifact["holdout_read_or_run"])
        self.assertFalse(artifact["automatic_mutation_allowed"])

    def test_holdout_run_is_rejected_before_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            run = json.loads(paths["run"].read_text(encoding="utf-8"))
            run.pop("artifact_sha256")
            run["partition"] = "holdout"
            self._write_hashed_json(paths["run"], run)
            with self.assertRaisesRegex(
                evaluator.Wav2Vec2CTCCalibrationEvaluationError,
                "calibration runs only",
            ):
                evaluator.evaluate(
                    run_path=paths["run"],
                    gold_path=paths["gold"],
                    outer_audit_path=paths["outer"],
                    sofa_start_path=paths["sofa_start"],
                    sofa_end_path=paths["sofa_end"],
                    hubertfa_start_path=paths["hubert_start"],
                    hubertfa_end_path=paths["hubert_end"],
                )

    def test_gold_view_with_holdout_record_is_rejected_before_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            gold = json.loads(paths["gold"].read_text(encoding="utf-8"))
            gold.pop("artifact_sha256")
            gold["records"][0]["partition"] = "holdout"
            gold["contains_holdout_records"] = True
            self._write_hashed_json(paths["gold"], gold)
            with self.assertRaisesRegex(
                evaluator.Wav2Vec2CTCCalibrationEvaluationError,
                "non-calibration records|leaked records",
            ):
                evaluator.evaluate(
                    run_path=paths["run"],
                    gold_path=paths["gold"],
                    outer_audit_path=paths["outer"],
                    sofa_start_path=paths["sofa_start"],
                    sofa_end_path=paths["sofa_end"],
                    hubertfa_start_path=paths["hubert_start"],
                    hubertfa_end_path=paths["hubert_end"],
                )


if __name__ == "__main__":
    unittest.main()
