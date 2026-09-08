import json
import tempfile
import unittest
from pathlib import Path

from scripts import v4_freeze_independent_fine_holdout_protocol as protocol


class IndependentFineHoldoutProtocolTests(unittest.TestCase):
    def _write_hashed(self, path: Path, payload: dict) -> Path:
        payload = dict(payload)
        payload["artifact_sha256"] = protocol._sha_json(payload)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _policy(self, root: Path) -> Path:
        return self._write_hashed(
            root / "policy.json",
            {
                "schema_version": "independent-fine-local-support-policy-1.0",
                "holdout_status": "not_run_at_policy_freeze",
                "production_authoritative": False,
                "automatic_mutation_allowed": False,
                "selector": {
                    "status": "aligned",
                    "require_observer_ambiguous_false": True,
                    "minimum_top1_top2_margin": 0.05,
                },
                "calibration_gate": {
                    "minimum_selected_coverage": 0.75,
                    "minimum_selected_distinct_pairs": 8,
                    "maximum_median_source_start_abs_error_ms": 50.0,
                    "maximum_p90_source_start_abs_error_ms": 100.0,
                    "maximum_source_start_abs_error_ms": 250.0,
                    "maximum_p90_slope_abs_error": 0.005,
                    "catastrophic_threshold_ms": 500.0,
                    "maximum_catastrophic_fraction": 0.0,
                },
            },
        )

    def _audit(self, root: Path, policy_sha: str, *, consulted: bool = False) -> Path:
        return self._write_hashed(
            root / "audit.json",
            {
                "schema_version": "independent-fine-affine-truth-audit-1.0",
                "partition": "holdout",
                "frozen_policy_sha256": policy_sha,
                "pair_selection_sha256": "c" * 64,
                "pair_count": 8,
                "truth_usable_count": 8,
                "independent_fine_predictions_consulted": consulted,
            },
        )

    def test_freezes_pre_prediction_protocol_and_copies_calibration_thresholds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy_path = self._policy(root)
            policy_payload = json.loads(policy_path.read_text(encoding="utf-8"))
            audit_path = self._audit(root, policy_payload["artifact_sha256"])
            artifact = protocol.freeze_protocol(policy_path=policy_path, audit_path=audit_path)
            self.assertEqual(artifact["holdout_predictions_status"], "not_run_at_protocol_freeze")
            self.assertEqual(artifact["selector"]["minimum_top1_top2_margin"], 0.05)
            self.assertEqual(artifact["holdout_gate"]["minimum_selected_coverage"], 0.75)
            self.assertEqual(artifact["holdout_gate"]["maximum_p90_source_start_abs_error_ms"], 100.0)
            self.assertEqual(artifact["holdout_gate"]["minimum_selected_distinct_pairs"], 4)
            self.assertFalse(artifact["production_authoritative"])
            self.assertFalse(artifact["direct_subtitle_timing_authority"])
            self.assertFalse(artifact["automatic_mutation_allowed"])

    def test_contextual_protocol_binds_policy_observer_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy_path = self._policy(root)
            payload = json.loads(policy_path.read_text(encoding="utf-8"))
            payload.pop("artifact_sha256")
            identity = dict(observer_variant="contextual", observer_version="1.1.1",
                            observer_implementation_revision="e" * 64, observer_sample_rate=22050)
            payload.update(identity)
            self._write_hashed(policy_path, payload)
            payload = json.loads(policy_path.read_text(encoding="utf-8"))
            audit_path = self._audit(root, payload["artifact_sha256"])
            artifact = protocol.freeze_protocol(policy_path=policy_path, audit_path=audit_path)
            for key, value in identity.items():
                self.assertEqual(artifact[key], value)

    def test_rejects_audit_that_consulted_predictions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy_path = self._policy(root)
            policy_payload = json.loads(policy_path.read_text(encoding="utf-8"))
            audit_path = self._audit(root, policy_payload["artifact_sha256"], consulted=True)
            with self.assertRaisesRegex(protocol.IndependentFineHoldoutProtocolError, "consulted Independent Fine predictions"):
                protocol.freeze_protocol(policy_path=policy_path, audit_path=audit_path)

    def test_rejects_policy_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy_path = self._policy(root)
            audit_path = self._audit(root, "d" * 64)
            with self.assertRaisesRegex(protocol.IndependentFineHoldoutProtocolError, "does not bind frozen policy"):
                protocol.freeze_protocol(policy_path=policy_path, audit_path=audit_path)


if __name__ == "__main__":
    unittest.main()
