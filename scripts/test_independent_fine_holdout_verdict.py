import json
import tempfile
import unittest
from pathlib import Path

from scripts import v4_evaluate_independent_fine_holdout_verdict as verdict


class IndependentFineHoldoutVerdictTests(unittest.TestCase):
    def _write_hashed(self, path: Path, payload: dict) -> Path:
        payload = dict(payload)
        payload["artifact_sha256"] = verdict._sha_json(payload)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _protocol(self, root: Path) -> Path:
        return self._write_hashed(root / "protocol.json", {
            "schema_version": "independent-fine-holdout-protocol-1.0",
            "frozen_policy_sha256": "b" * 64,
            "pair_selection_sha256": "c" * 64,
            "holdout_affine_truth_audit_sha256": "a" * 64,
            "selector": {"status": "aligned", "require_observer_ambiguous_false": True, "minimum_top1_top2_margin": 0.05},
            "holdout_gate": {
                "minimum_selected_coverage": 0.75,
                "minimum_selected_distinct_pairs": 4,
                "maximum_median_source_start_abs_error_ms": 50.0,
                "maximum_p90_source_start_abs_error_ms": 100.0,
                "maximum_source_start_abs_error_ms": 250.0,
                "maximum_p90_slope_abs_error": 0.005,
                "catastrophic_threshold_ms": 500.0,
                "maximum_catastrophic_fraction": 0.0,
            },
            "holdout_predictions_status": "not_run_at_protocol_freeze",
            "production_authoritative": False,
            "automatic_mutation_allowed": False,
        })

    def _evaluation(self, root: Path, protocol_sha: str, *, bad_selected_tail: bool = False) -> Path:
        cases = []
        for pair in range(1, 9):
            for window in range(1, 4):
                error = 10.0 + pair + window
                if bad_selected_tail and pair == 8 and window == 3:
                    error = 800.0
                cases.append({
                    "case_id": f"{pair:02x}{window:02x}".ljust(64, "0"),
                    "pair_index": pair,
                    "window_index": window,
                    "status": "aligned",
                    "source_start_abs_error_ms": error,
                    "slope_abs_error": 0.0,
                    "margin": 0.10,
                    "ambiguous": False,
                })
        return self._write_hashed(root / "evaluation.json", {
            "schema_version": "independent-fine-known-transform-evaluation-1.0",
            "authority": "evaluation_only_no_production_timing_authority",
            "partition": "holdout",
            "source_audit_sha256": "a" * 64,
            "frozen_policy_sha256": "b" * 64,
            "pair_selection_sha256": "c" * 64,
            "holdout_protocol_sha256": protocol_sha,
            "record_count": len(cases),
            "cases": cases,
            "automatic_mutation_allowed": False,
            "subtitle_mutation_performed": False,
        })

    def test_passes_frozen_holdout_without_granting_production_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protocol_path = self._protocol(root)
            protocol_payload = json.loads(protocol_path.read_text(encoding="utf-8"))
            evaluation_path = self._evaluation(root, protocol_payload["artifact_sha256"])
            artifact = verdict.evaluate_verdict(evaluation_path=evaluation_path, protocol_path=protocol_path)
            self.assertEqual(artifact["verdict"], "passed_frozen_holdout_protocol")
            self.assertTrue(artifact["result"]["passed"])
            self.assertEqual(artifact["result"]["selected_count"], 24)
            self.assertFalse(artifact["policy_retuning_from_holdout_allowed"])
            self.assertFalse(artifact["production_authoritative"])
            self.assertFalse(artifact["automatic_mutation_allowed"])

    def test_selected_catastrophic_tail_fails_but_does_not_throw_or_retime_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protocol_path = self._protocol(root)
            protocol_payload = json.loads(protocol_path.read_text(encoding="utf-8"))
            evaluation_path = self._evaluation(root, protocol_payload["artifact_sha256"], bad_selected_tail=True)
            artifact = verdict.evaluate_verdict(evaluation_path=evaluation_path, protocol_path=protocol_path)
            self.assertEqual(artifact["verdict"], "failed_frozen_holdout_protocol")
            self.assertFalse(artifact["result"]["passed"])
            self.assertGreater(artifact["result"]["catastrophic_count"], 0)
            self.assertFalse(artifact["policy_retuning_from_holdout_allowed"])

    def test_protocol_link_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            protocol_path = self._protocol(root)
            evaluation_path = self._evaluation(root, "e" * 64)
            with self.assertRaisesRegex(verdict.IndependentFineHoldoutVerdictError, "holdout_protocol_sha256"):
                verdict.evaluate_verdict(evaluation_path=evaluation_path, protocol_path=protocol_path)


if __name__ == "__main__":
    unittest.main()
