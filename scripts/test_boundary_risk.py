import unittest

from lyric_aligner.timeline.boundary_risk import (
    BoundaryLocalSupport,
    BoundaryRiskError,
    adjudicate_calibrated_fallback,
    build_error_profile,
    build_profile_from_calibration_artifact,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
TRACKS = ["track-a", "track-b", "track-c", "track-d"]


def authoritative_profile(profile_id, errors, *, boundary_kind="start", threshold=500.0, sha=SHA_A):
    return build_error_profile(
        profile_id=profile_id,
        boundary_kind=boundary_kind,
        population="holdout",
        effective_errors_ms=errors,
        track_ids=TRACKS,
        catastrophic_threshold_ms=threshold,
    ).with_production_authority(sha)


def local_support(candidate_ms, *, boundary_kind="start", p90=120.0, authoritative=True, contradictory=False):
    value = BoundaryLocalSupport(
        support_id="fixture-local-support",
        boundary_kind=boundary_kind,
        candidate_ms=candidate_ms,
        estimated_p90_error_ms=p90,
        contradictory=contradictory,
    )
    return value.with_production_authority(SHA_B) if authoritative else value


class BoundaryRiskTests(unittest.TestCase):
    def test_build_profile_preserves_missing_prediction_as_coverage_loss(self):
        profile = build_error_profile(
            profile_id="candidate",
            boundary_kind="end",
            population="holdout",
            effective_errors_ms=[10, None, 30, 50],
            track_ids=TRACKS,
            catastrophic_threshold_ms=500,
        )
        self.assertEqual(profile.sample_count, 4)
        self.assertEqual(profile.predicted_count, 3)
        self.assertEqual(profile.distinct_track_count, 3)
        self.assertEqual(profile.coverage, 0.75)
        self.assertEqual(profile.mean_effective_error_ms, 30.0)
        self.assertEqual(profile.median_effective_error_ms, 30.0)
        self.assertEqual(profile.p90_effective_error_ms, 50.0)
        self.assertFalse(profile.production_authoritative)

    def test_non_authoritative_profile_never_auto_mutates(self):
        editor = authoritative_profile("editor", [300, 400, 500, 600])
        candidate = build_error_profile(
            profile_id="candidate",
            boundary_kind="start",
            population="holdout",
            effective_errors_ms=[100, 120, 140, 160],
            track_ids=TRACKS,
            catastrophic_threshold_ms=500,
        )
        decision = adjudicate_calibrated_fallback(
            editor_ms=1000,
            candidate_ms=1200,
            editor_profile=editor,
            candidate_profile=candidate,
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertFalse(decision.automatic_mutation_allowed)

    def test_materially_better_holdout_profile_auto_mutates(self):
        editor = authoritative_profile("editor", [300, 400, 500, 600])
        candidate = authoritative_profile(
            "candidate",
            [100, 120, 140, 160],
            threshold=500,
            sha=SHA_B,
        )
        decision = adjudicate_calibrated_fallback(
            editor_ms=1000,
            candidate_ms=1180,
            editor_profile=editor,
            candidate_profile=candidate,
            local_support=local_support(1180),
        )
        self.assertEqual(decision.action, "auto_calibrated_better")
        self.assertEqual(decision.selected_ms, 1180)
        self.assertTrue(decision.automatic_mutation_allowed)
        self.assertGreater(decision.expected_improvement_ms, 0)

    def test_less_precise_candidate_can_auto_rescue_demonstrably_bad_editor(self):
        editor = authoritative_profile(
            "editor",
            [800, 900, 1000, 1100],
            threshold=1500,
        )
        candidate = authoritative_profile(
            "candidate",
            [200, 250, 250, 350],
            threshold=1500,
            sha=SHA_B,
        )
        decision = adjudicate_calibrated_fallback(
            editor_ms=10000,
            candidate_ms=9400,
            editor_profile=editor,
            candidate_profile=candidate,
            local_support=local_support(9400, p90=300.0),
        )
        self.assertEqual(decision.action, "auto_rescue")
        self.assertEqual(decision.selected_ms, 9400)
        self.assertTrue(decision.automatic_mutation_allowed)
        self.assertLessEqual(decision.estimated_candidate_p90_error_ms, 600)

    def test_structural_ambiguity_blocks_even_good_candidate(self):
        editor = authoritative_profile("editor", [600, 700, 800, 900])
        candidate = authoritative_profile(
            "candidate",
            [20, 30, 40, 50],
            sha=SHA_B,
        )
        decision = adjudicate_calibrated_fallback(
            editor_ms=5000,
            candidate_ms=4500,
            editor_profile=editor,
            candidate_profile=candidate,
            structural_ambiguity=True,
        )
        self.assertEqual(decision.action, "structural_review")
        self.assertEqual(decision.selected_ms, 5000)
        self.assertFalse(decision.automatic_mutation_allowed)

    def test_semantic_identity_ambiguity_blocks_even_good_candidate(self):
        editor = authoritative_profile("editor", [600, 700, 800, 900])
        candidate = authoritative_profile(
            "candidate",
            [20, 30, 40, 50],
            sha=SHA_B,
        )
        decision = adjudicate_calibrated_fallback(
            editor_ms=5000,
            candidate_ms=4500,
            editor_profile=editor,
            candidate_profile=candidate,
            semantic_ambiguity=True,
        )
        self.assertEqual(decision.action, "structural_review")

    def test_global_topology_violation_blocks_even_good_candidate(self):
        editor = authoritative_profile("editor", [600, 700, 800, 900])
        candidate = authoritative_profile(
            "candidate",
            [20, 30, 40, 50],
            sha=SHA_B,
        )
        decision = adjudicate_calibrated_fallback(
            editor_ms=5000,
            candidate_ms=4500,
            editor_profile=editor,
            candidate_profile=candidate,
            global_constraints_satisfied=False,
        )
        self.assertEqual(decision.action, "structural_review")

    def test_high_catastrophic_tail_is_not_auto_rescue(self):
        editor = authoritative_profile(
            "editor",
            [800, 900, 1000, 1100],
            threshold=500,
        )
        candidate = authoritative_profile(
            "candidate",
            [50, 50, 50, 600],
            threshold=500,
            sha=SHA_B,
        )
        decision = adjudicate_calibrated_fallback(
            editor_ms=10000,
            candidate_ms=9500,
            editor_profile=editor,
            candidate_profile=candidate,
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertFalse(decision.automatic_mutation_allowed)

    def test_small_or_uncertain_gain_keeps_editor(self):
        editor = authoritative_profile("editor", [100, 120, 140, 160])
        candidate = authoritative_profile(
            "candidate",
            [90, 110, 130, 150],
            sha=SHA_B,
        )
        decision = adjudicate_calibrated_fallback(
            editor_ms=1000,
            candidate_ms=1020,
            editor_profile=editor,
            candidate_profile=candidate,
        )
        self.assertEqual(decision.action, "keep_editor")

    def test_good_aggregate_profile_without_current_local_support_keeps_editor(self):
        editor = authoritative_profile("editor", [800, 900, 1000, 1100], threshold=1500)
        candidate = authoritative_profile(
            "candidate",
            [100, 120, 140, 160],
            threshold=1500,
            sha=SHA_B,
        )
        decision = adjudicate_calibrated_fallback(
            editor_ms=10000,
            candidate_ms=30000,
            editor_profile=editor,
            candidate_profile=candidate,
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertIn("without local support", decision.reason)

    def test_non_authoritative_or_contradictory_local_support_keeps_editor(self):
        editor = authoritative_profile("editor", [800, 900, 1000, 1100], threshold=1500)
        candidate = authoritative_profile(
            "candidate",
            [100, 120, 140, 160],
            threshold=1500,
            sha=SHA_B,
        )
        untrusted = adjudicate_calibrated_fallback(
            editor_ms=10000,
            candidate_ms=9400,
            editor_profile=editor,
            candidate_profile=candidate,
            local_support=local_support(9400, authoritative=False),
        )
        contradictory = adjudicate_calibrated_fallback(
            editor_ms=10000,
            candidate_ms=9400,
            editor_profile=editor,
            candidate_profile=candidate,
            local_support=local_support(9400, contradictory=True),
        )
        self.assertEqual(untrusted.action, "keep_editor")
        self.assertEqual(contradictory.action, "keep_editor")

    def test_local_support_must_bind_exact_candidate_and_kind(self):
        editor = authoritative_profile("editor", [300, 400, 500, 600])
        candidate = authoritative_profile("candidate", [100, 120, 140, 160], sha=SHA_B)
        with self.assertRaisesRegex(BoundaryRiskError, "another candidate timestamp"):
            adjudicate_calibrated_fallback(
                editor_ms=1000,
                candidate_ms=1180,
                editor_profile=editor,
                candidate_profile=candidate,
                local_support=local_support(1190),
            )
        with self.assertRaisesRegex(BoundaryRiskError, "boundary_kind"):
            adjudicate_calibrated_fallback(
                editor_ms=1000,
                candidate_ms=1180,
                editor_profile=editor,
                candidate_profile=candidate,
                local_support=local_support(1180, boundary_kind="end"),
            )

    def test_calibration_population_cannot_auto_even_if_marked_authoritative(self):
        editor = build_error_profile(
            profile_id="editor",
            boundary_kind="start",
            population="calibration",
            effective_errors_ms=[300, 400, 500, 600],
            track_ids=TRACKS,
            catastrophic_threshold_ms=500,
        ).with_production_authority(SHA_A)
        candidate = build_error_profile(
            profile_id="candidate",
            boundary_kind="start",
            population="calibration",
            effective_errors_ms=[100, 120, 140, 160],
            track_ids=TRACKS,
            catastrophic_threshold_ms=500,
        ).with_production_authority(SHA_B)
        decision = adjudicate_calibrated_fallback(
            editor_ms=1000,
            candidate_ms=1180,
            editor_profile=editor,
            candidate_profile=candidate,
            local_support=local_support(1180),
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertIn("requires holdout", decision.reason)

    def test_profile_population_or_catastrophic_threshold_mismatch_fails_closed(self):
        editor = authoritative_profile("editor", [100, 120, 140, 160], threshold=500)
        calibration_candidate = build_error_profile(
            profile_id="candidate",
            boundary_kind="start",
            population="calibration",
            effective_errors_ms=[20, 30, 40, 50],
            track_ids=TRACKS,
            catastrophic_threshold_ms=500,
        ).with_production_authority(SHA_B)
        with self.assertRaisesRegex(BoundaryRiskError, "share population"):
            adjudicate_calibrated_fallback(
                editor_ms=1000,
                candidate_ms=1100,
                editor_profile=editor,
                candidate_profile=calibration_candidate,
                local_support=local_support(1100),
            )
        threshold_candidate = authoritative_profile(
            "candidate",
            [20, 30, 40, 50],
            threshold=1500,
            sha=SHA_B,
        )
        with self.assertRaisesRegex(BoundaryRiskError, "catastrophic threshold"):
            adjudicate_calibrated_fallback(
                editor_ms=1000,
                candidate_ms=1100,
                editor_profile=editor,
                candidate_profile=threshold_candidate,
                local_support=local_support(1100),
            )

    def test_profile_boundary_kind_mismatch_fails_closed(self):
        editor = authoritative_profile("editor", [100, 120, 140, 160], boundary_kind="start")
        candidate = authoritative_profile(
            "candidate",
            [20, 30, 40, 50],
            boundary_kind="end",
            sha=SHA_B,
        )
        with self.assertRaises(BoundaryRiskError):
            adjudicate_calibrated_fallback(
                editor_ms=1000,
                candidate_ms=1100,
                editor_profile=editor,
                candidate_profile=candidate,
            )

    def test_calibration_artifact_adapter_uses_holdout_rows(self):
        artifact = {
            "backend_id": "observer-a",
            "scope": {"boundary_kind": "internal"},
            "policy": {"fps": 30.0, "catastrophic_error_frames": 15.0},
            "artifact_sha256": SHA_A,
            "records": [
                {
                    "partition": "calibration",
                    "track": "track-z",
                    "effective_error_ms": 999,
                },
                {
                    "partition": "holdout",
                    "track": "track-a",
                    "effective_error_ms": 10,
                },
                {
                    "partition": "holdout",
                    "track": "track-b",
                    "effective_error_ms": 20,
                },
                {
                    "partition": "holdout",
                    "track": "track-c",
                    "effective_error_ms": 30,
                },
                {
                    "partition": "holdout",
                    "track": "track-d",
                    "effective_error_ms": 40,
                },
            ],
        }
        profile = build_profile_from_calibration_artifact(
            artifact,
            population="holdout",
            production_authoritative=True,
        )
        self.assertEqual(profile.boundary_kind, "internal")
        self.assertEqual(profile.sample_count, 4)
        self.assertEqual(profile.max_effective_error_ms, 40.0)
        self.assertTrue(profile.production_authoritative)
        self.assertEqual(profile.provenance_sha256, SHA_A)


if __name__ == "__main__":
    unittest.main()
