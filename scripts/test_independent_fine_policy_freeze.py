import json
import tempfile
import unittest
from pathlib import Path

from scripts import v4_freeze_independent_fine_local_support_policy as freeze


class IndependentFinePolicyFreezeTests(unittest.TestCase):
    def _evaluation(self, root: Path, *, bad_tail: bool = False, partition: str = "calibration") -> Path:
        cases = []
        for pair in range(1, 9):
            for window in range(1, 4):
                error = 10.0 + pair + window
                margin = 0.10
                ambiguous = False
                if bad_tail and pair == 8 and window == 3:
                    error = 800.0
                cases.append({
                    "case_id": f"{pair:02x}{window:02x}".ljust(64, "0"),
                    "pair_index": pair,
                    "window_index": window,
                    "status": "aligned",
                    "source_start_abs_error_ms": error,
                    "slope_abs_error": 0.0,
                    "margin": margin,
                    "ambiguous": ambiguous,
                })
        payload = {
            "schema_version": "independent-fine-known-transform-evaluation-1.0",
            "authority": "evaluation_only_no_production_timing_authority",
            "partition": partition,
            "source_audit_sha256": "a" * 64,
            "run_artifact_sha256": "b" * 64,
            "truth_artifact_sha256": "c" * 64,
            "evaluator_revision": "d" * 64,
            "record_count": len(cases),
            "aligned_count": len(cases),
            "prediction_coverage": 1.0,
            "cases": cases,
            "automatic_mutation_allowed": False,
            "subtitle_mutation_performed": False,
        }
        payload["artifact_sha256"] = freeze._sha_json(payload)
        path = root / "evaluation.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_freeze_passes_calibration_and_remains_non_authoritative(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._evaluation(Path(temporary))
            artifact = freeze.freeze_policy(evaluation_path=path)
            self.assertEqual(artifact["policy_id"], "independent_fine_margin_0p05_v1")
            self.assertEqual(artifact["calibration_result"]["selected_count"], 24)
            self.assertEqual(artifact["calibration_result"]["selected_distinct_pair_count"], 8)
            self.assertTrue(artifact["calibration_result"]["passed"])
            self.assertEqual(artifact["holdout_status"], "not_run_at_policy_freeze")
            self.assertFalse(artifact["production_authoritative"])
            self.assertFalse(artifact["direct_subtitle_timing_authority"])
            self.assertFalse(artifact["automatic_mutation_allowed"])

    def test_contextual_policy_preserves_exact_observer_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._evaluation(Path(temporary))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.pop("artifact_sha256")
            identity = dict(observer_variant="contextual", observer_version="1.1.1",
                            observer_implementation_revision="e" * 64, observer_sample_rate=22050)
            payload.update(identity)
            payload["artifact_sha256"] = freeze._sha_json(payload)
            path.write_text(json.dumps(payload), encoding="utf-8")
            artifact = freeze.freeze_policy(evaluation_path=path)
            for key, value in identity.items():
                self.assertEqual(artifact[key], value)

    def test_freeze_rejects_holdout_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._evaluation(Path(temporary), partition="holdout")
            with self.assertRaisesRegex(freeze.IndependentFinePolicyFreezeError, "only be frozen from calibration"):
                freeze.freeze_policy(evaluation_path=path)

    def test_freeze_rejects_selected_catastrophic_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._evaluation(Path(temporary), bad_tail=True)
            with self.assertRaisesRegex(freeze.IndependentFinePolicyFreezeError, "calibration selector gate failed"):
                freeze.freeze_policy(evaluation_path=path)

    def test_margin_gate_excludes_low_margin_tail_before_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._evaluation(Path(temporary))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["cases"][0]["source_start_abs_error_ms"] = 2000.0
            payload["cases"][0]["margin"] = 0.049
            payload.pop("artifact_sha256")
            payload["artifact_sha256"] = freeze._sha_json(payload)
            path.write_text(json.dumps(payload), encoding="utf-8")
            artifact = freeze.freeze_policy(evaluation_path=path)
            self.assertEqual(artifact["calibration_result"]["selected_count"], 23)
            self.assertLess(artifact["calibration_result"]["source_start_abs_error_ms"]["max"], 250.0)


if __name__ == "__main__":
    unittest.main()
