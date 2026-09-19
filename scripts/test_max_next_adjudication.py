import unittest

from lyric_aligner.timeline.boundary_risk import BoundaryLocalSupport, build_error_profile
from lyric_aligner.timeline.max_next_adjudication import (
    MAX_NEXT_ADJUDICATION_AUTHORITY,
    MaxNextAdjudicationError,
    adjudicate_max_next_boundary,
)


TRACKS = ["a", "b", "c", "d"]
SHA_A = "a" * 64
SHA_B = "b" * 64


def profile(profile_id, errors, sha):
    return build_error_profile(
        profile_id=profile_id,
        boundary_kind="start",
        population="holdout",
        effective_errors_ms=errors,
        track_ids=TRACKS,
        catastrophic_threshold_ms=1500,
    ).with_production_authority(sha)


def precise(action="keep_editor", selected=1000, editor=1000):
    return {"action": action, "selected_ms": selected, "editor_ms": editor}


def local_support(candidate_ms, *, p90=120.0):
    return BoundaryLocalSupport(
        support_id="fixture-max-next-local-support",
        boundary_kind="start",
        candidate_ms=candidate_ms,
        estimated_p90_error_ms=p90,
    ).with_production_authority("c" * 64)


class MaxNextAdjudicationTests(unittest.TestCase):
    def test_precise_authority_remains_first_choice(self):
        result = adjudicate_max_next_boundary(
            precise_decision=precise("auto_fine_refine", selected=1120),
            fallback_candidate_ms=1300,
            editor_profile=profile("editor", [900, 1000, 1100, 1200], SHA_A),
            candidate_profile=profile("candidate", [100, 120, 140, 160], SHA_B),
        )
        self.assertEqual(result.tier, "A_verified_precise")
        self.assertEqual(result.selected_ms, 1120)
        self.assertTrue(result.automatic_selection_recommended)
        self.assertFalse(result.production_mutation_allowed)
        self.assertEqual(result.authority, MAX_NEXT_ADJUDICATION_AUTHORITY)

    def test_global_veto_overrides_even_local_precise_decision(self):
        result = adjudicate_max_next_boundary(
            precise_decision=precise("auto_safe_refine", selected=1200),
            global_constraints_satisfied=False,
        )
        self.assertEqual(result.tier, "D_structural_ambiguity")
        self.assertEqual(result.selected_ms, 1000)
        self.assertFalse(result.automatic_selection_recommended)

    def test_calibrated_better_becomes_tier_b(self):
        result = adjudicate_max_next_boundary(
            precise_decision=precise(),
            fallback_candidate_ms=1180,
            editor_profile=profile("editor", [300, 400, 500, 600], SHA_A),
            candidate_profile=profile("candidate", [100, 120, 140, 160], SHA_B),
            local_support=local_support(1180),
        )
        self.assertEqual(result.tier, "B_calibrated_better")
        self.assertEqual(result.action, "select_calibrated_better")
        self.assertEqual(result.selected_ms, 1180)
        self.assertTrue(result.automatic_selection_recommended)
        self.assertFalse(result.production_mutation_allowed)
        self.assertEqual(result.risk_action, "auto_calibrated_better")
        self.assertEqual(result.local_support_id, "fixture-max-next-local-support")
        self.assertEqual(result.local_support_p90_error_ms, 120.0)
        self.assertTrue(result.local_support_authoritative)

    def test_demonstrably_bad_editor_can_be_tier_c_rescue(self):
        result = adjudicate_max_next_boundary(
            precise_decision=precise(editor=10000, selected=10000),
            fallback_candidate_ms=9400,
            editor_profile=profile("editor", [800, 900, 1000, 1100], SHA_A),
            candidate_profile=profile("candidate", [200, 250, 250, 350], SHA_B),
            local_support=local_support(9400, p90=300.0),
        )
        self.assertEqual(result.tier, "C_automatic_rescue")
        self.assertEqual(result.selected_ms, 9400)
        self.assertGreater(result.expected_improvement_ms, 500)
        self.assertFalse(result.production_mutation_allowed)
        self.assertEqual(result.local_support_p90_error_ms, 300.0)

    def test_semantic_ambiguity_stays_tier_d(self):
        result = adjudicate_max_next_boundary(
            precise_decision=precise(),
            fallback_candidate_ms=1200,
            editor_profile=profile("editor", [800, 900, 1000, 1100], SHA_A),
            candidate_profile=profile("candidate", [20, 30, 40, 50], SHA_B),
            semantic_ambiguity=True,
        )
        self.assertEqual(result.tier, "D_structural_ambiguity")
        self.assertEqual(result.selected_ms, 1000)

    def test_missing_fallback_keeps_editor(self):
        result = adjudicate_max_next_boundary(precise_decision=precise())
        self.assertEqual(result.tier, "E_keep_editor")
        self.assertEqual(result.selected_ms, 1000)

    def test_good_aggregate_fallback_without_local_support_keeps_editor(self):
        result = adjudicate_max_next_boundary(
            precise_decision=precise(editor=10000, selected=10000),
            fallback_candidate_ms=30000,
            editor_profile=profile("editor", [800, 900, 1000, 1100], SHA_A),
            candidate_profile=profile("candidate", [100, 120, 140, 160], SHA_B),
        )
        self.assertEqual(result.tier, "E_keep_editor")
        self.assertEqual(result.selected_ms, 10000)
        self.assertIn("without local support", result.reason)

    def test_small_gain_keeps_editor(self):
        result = adjudicate_max_next_boundary(
            precise_decision=precise(),
            fallback_candidate_ms=1020,
            editor_profile=profile("editor", [100, 120, 140, 160], SHA_A),
            candidate_profile=profile("candidate", [90, 110, 130, 150], SHA_B),
        )
        self.assertEqual(result.tier, "E_keep_editor")
        self.assertFalse(result.automatic_selection_recommended)

    def test_invalid_precise_payload_fails_closed(self):
        with self.assertRaises(MaxNextAdjudicationError):
            adjudicate_max_next_boundary(
                precise_decision={"action": "keep_editor", "selected_ms": "bad", "editor_ms": 1000}
            )


if __name__ == "__main__":
    unittest.main()
