from __future__ import annotations

import unittest

from lyric_aligner.evaluation.family_calibration import _fusion_contract


class EvidenceFamilyPolicyIdentityTests(unittest.TestCase):
    def _fusion(self, *, artifact_suffix: str, threshold: float = 500.0) -> dict:
        return {
            "mode": "shadow_only",
            "policy_calibrated": False,
            "release_gate_eligible": False,
            "algorithm_version": "4.0.0a8",
            "policy_id": "evidence-fusion-shadow-2026-08-18-v2-forced",
            "config": {
                "conflict_boundary_ms": threshold,
                "conflict_policy": "any_auxiliary_pair_over_threshold_blocks",
                "editor_evidence_artifact_id": f"editor-{artifact_suffix}",
                "asr_evidence_artifact_id": f"asr-{artifact_suffix}",
                "forced_mix_evidence_artifact_id": f"forced-{artifact_suffix}",
            },
            "lines": [],
        }

    def test_per_case_artifact_ids_do_not_change_policy_identity(self) -> None:
        first = _fusion_contract(self._fusion(artifact_suffix="case-a"))
        second = _fusion_contract(self._fusion(artifact_suffix="case-b"))

        self.assertEqual(
            first["fusion_policy_identity_sha256"],
            second["fusion_policy_identity_sha256"],
        )
        self.assertNotIn("editor_evidence_artifact_id", first["config"])
        self.assertNotIn("asr_evidence_artifact_id", first["config"])
        self.assertNotIn("forced_mix_evidence_artifact_id", first["config"])

    def test_semantic_threshold_change_still_changes_policy_identity(self) -> None:
        first = _fusion_contract(self._fusion(artifact_suffix="case-a", threshold=500.0))
        second = _fusion_contract(self._fusion(artifact_suffix="case-b", threshold=350.0))

        self.assertNotEqual(
            first["fusion_policy_identity_sha256"],
            second["fusion_policy_identity_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
