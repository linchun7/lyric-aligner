import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.v4_seal_boundary_authority_overlay import verify_replayed_adjudication


class BoundaryAuthorityOverlayReplayTests(unittest.TestCase):
    def call_verify(self, *, evidence=None, decisions=None, bundle=None):
        expected_evidence = {"kind": "evidence"} if evidence is None else evidence
        expected_decisions = {"kind": "decisions"} if decisions is None else decisions
        expected_bundle = {"kind": "bundle"} if bundle is None else bundle
        return expected_evidence, expected_decisions, expected_bundle

    @patch("scripts.v4_seal_boundary_authority_overlay.run_adjudication")
    def test_exact_raw_replay_is_accepted(self, replay):
        evidence, decisions, bundle = self.call_verify()
        replay.return_value = (dict(evidence), dict(decisions), dict(bundle))
        verify_replayed_adjudication(
            plan={"plan": 1},
            backend_runs=[{"run": 1}, {"run": 2}],
            suite_dir=Path("suite"),
            joint_calibration={"joint": 1},
            evidence=evidence,
            decisions=decisions,
            bundle=bundle,
        )
        replay.assert_called_once()

    @patch("scripts.v4_seal_boundary_authority_overlay.run_adjudication")
    def test_tampered_evidence_is_rejected_even_if_downstream_hashes_were_rebuilt(self, replay):
        evidence, decisions, bundle = self.call_verify()
        replay.return_value = ({"kind": "raw-derived-evidence"}, dict(decisions), dict(bundle))
        with self.assertRaisesRegex(ValueError, "raw backend runs do not replay to the sealed evidence"):
            verify_replayed_adjudication(
                plan={"plan": 1},
                backend_runs=[{"run": 1}, {"run": 2}],
                suite_dir=Path("suite"),
                joint_calibration={"joint": 1},
                evidence=evidence,
                decisions=decisions,
                bundle=bundle,
            )

    @patch("scripts.v4_seal_boundary_authority_overlay.run_adjudication")
    def test_tampered_decision_or_bundle_is_rejected(self, replay):
        evidence, decisions, bundle = self.call_verify()
        replay.return_value = (dict(evidence), {"kind": "raw-derived-decisions"}, dict(bundle))
        with self.assertRaisesRegex(ValueError, "raw backend runs do not replay to the sealed decision"):
            verify_replayed_adjudication(
                plan={}, backend_runs=[], suite_dir=Path("suite"), joint_calibration={},
                evidence=evidence, decisions=decisions, bundle=bundle,
            )
        replay.return_value = (dict(evidence), dict(decisions), {"kind": "raw-derived-bundle"})
        with self.assertRaisesRegex(ValueError, "raw backend runs do not replay to the sealed adjudication bundle"):
            verify_replayed_adjudication(
                plan={}, backend_runs=[], suite_dir=Path("suite"), joint_calibration={},
                evidence=evidence, decisions=decisions, bundle=bundle,
            )


if __name__ == "__main__":
    unittest.main()
