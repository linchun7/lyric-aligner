import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "v4_run_script",
    ROOT / "scripts" / "v4_run.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load scripts/v4_run.py")
V4_RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V4_RUN)


class V4RunCutCandidateTests(unittest.TestCase):
    def test_forward_discontinuity_materializes_candidate_level_issue(self):
        row = {
            "mix_before": 4.5,
            "mix_after": 5.5,
            "source_before": 4.5,
            "source_after": 8.5,
            "observed_rate": 4.0,
            "excess_source_jump": 3.0,
            "reason": "forward source jump",
        }
        issue = V4_RUN._forward_discontinuity_issue(
            occurrence_id="occ-1",
            selection="MIDDLE_DISCONTINUITY_REVIEW_REQUIRED",
            row=row,
        )
        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertEqual(issue["kind"], "timewarp_discontinuity")
        self.assertEqual(issue["code"], "source_position_discontinuity")
        self.assertEqual(issue["occurrence_id"], "occ-1")
        self.assertTrue(issue["candidate_id"])
        self.assertEqual(issue["mix_before"], 4.5)
        self.assertEqual(issue["mix_after"], 5.5)
        self.assertEqual(issue["source_before"], 4.5)
        self.assertEqual(issue["source_after"], 8.5)
        self.assertEqual(issue["excess_source_jump"], 3.0)

    def test_backward_or_invalid_jump_never_materializes_as_cut_candidate(self):
        self.assertIsNone(
            V4_RUN._forward_discontinuity_issue(
                occurrence_id="occ-1",
                selection="BLOCKED",
                row={
                    "mix_before": 4.5,
                    "mix_after": 5.5,
                    "source_before": 8.5,
                    "source_after": 4.5,
                },
            )
        )
        self.assertIsNone(
            V4_RUN._forward_discontinuity_issue(
                occurrence_id="occ-1",
                selection="BLOCKED",
                row={
                    "mix_before": 5.5,
                    "mix_after": 4.5,
                    "source_before": 4.5,
                    "source_after": 8.5,
                },
            )
        )

    def test_effective_timewarp_payload_uses_applied_fine_evidence(self):
        coarse = {
            "result": {
                "timewarp": {
                    "selection": "COARSE",
                    "discontinuities": [{"source": "coarse"}],
                }
            }
        }
        fine = {
            "result": {
                "applied": True,
                "timewarp": {
                    "selection": "FINE",
                    "discontinuities": [{"source": "fine"}],
                },
            }
        }
        payload, source = V4_RUN._effective_timewarp_payload(coarse, fine)
        self.assertEqual(source, "fine")
        self.assertEqual(payload["selection"], "FINE")
        self.assertEqual(payload["discontinuities"][0]["source"], "fine")

    def test_non_applied_fine_does_not_override_coarse_evidence(self):
        coarse = {"result": {"timewarp": {"selection": "COARSE"}}}
        fine = {
            "result": {
                "applied": False,
                "timewarp": {"selection": "FINE_NOT_APPLIED"},
            }
        }
        payload, source = V4_RUN._effective_timewarp_payload(coarse, fine)
        self.assertEqual(source, "coarse")
        self.assertEqual(payload["selection"], "COARSE")


if __name__ == "__main__":
    unittest.main()
