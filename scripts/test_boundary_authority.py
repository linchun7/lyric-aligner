import unittest

from lyric_aligner.timeline.boundary_authority import (
    BoundaryAuthorityError,
    adjudicate_boundary_candidate,
    classify_boundary_difficulty,
    normalize_boundary_evidence,
    timing_mutation_has_audio_authority,
)


class BoundaryAuthorityPolicyTests(unittest.TestCase):
    def point(self, family, ms, confidence=0.95, uncertainty_ms=40, *, calibrated=True):
        return {
            "family": family,
            "correlation_group": family,
            "backend_id": f"backend:{family}",
            "boundary_ms": ms,
            "confidence": confidence,
            "uncertainty_ms": uncertainty_ms,
            "calibration_passed": calibrated,
            "calibration_sha256": "c" * 64 if calibrated else "",
        }

    def adjudicate(self, **kwargs):
        kwargs.setdefault("verified_calibration_sha256s", {"c" * 64})
        return adjudicate_boundary_candidate(**kwargs)

    def test_editor_or_lrc_priors_never_authorize_mutation(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=1120,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[
                self.point("editor_prior", 1000),
                self.point("lrc_projection", 1120),
            ],
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertEqual(decision.selected_ms, 1000)
        self.assertFalse(decision.automatic_mutation_allowed)

    def test_one_audio_family_is_not_enough(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=1080,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[self.point("final_mix_singing_alignment", 1060)],
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertEqual(decision.audio_family_count, 1)

    def test_two_source_projected_families_do_not_fake_independence(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=1080,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[
                self.point("source_forced_alignment_projection", 1060),
                self.point("source_mix_local_acoustic_projection", 1080),
            ],
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertIn("direct final-mix", decision.reason)

    def test_singing_asr_cannot_be_the_second_authority_vote(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=1080,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[
                self.point("final_mix_singing_alignment", 1060),
                self.point("asr_word_timing", 1080),
            ],
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertFalse(decision.automatic_mutation_allowed)
        self.assertIn("non-ASR", decision.reason)

    def test_uncalibrated_independent_audio_never_authorizes_mutation(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=1100,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[
                self.point("final_mix_singing_alignment", 1070, calibrated=False),
                self.point("source_forced_alignment_projection", 1110, calibrated=False),
            ],
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertFalse(decision.automatic_mutation_allowed)
        self.assertEqual(decision.audio_family_count, 0)

    def test_claimed_calibration_is_not_trusted_without_verified_catalog(self):
        decision = adjudicate_boundary_candidate(
            editor_ms=1000,
            proposed_ms=1100,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[
                self.point("final_mix_singing_alignment", 1070),
                self.point("source_forced_alignment_projection", 1110),
            ],
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertFalse(decision.automatic_mutation_allowed)
        self.assertEqual(decision.audio_family_count, 0)

    def test_direct_final_mix_plus_source_projection_can_refine_finely(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=1100,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[
                self.point("final_mix_singing_alignment", 1070),
                self.point("source_forced_alignment_projection", 1110),
            ],
        )
        self.assertEqual(decision.action, "auto_fine_refine")
        self.assertTrue(decision.automatic_mutation_allowed)
        self.assertEqual(decision.audio_spread_ms, 40)
        self.assertIn(decision.selected_ms, (1070, 1110))

    def test_audio_family_disagreement_keeps_editor(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=1100,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[
                self.point("final_mix_singing_alignment", 1000),
                self.point("source_forced_alignment_projection", 1401),
            ],
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertGreater(decision.audio_spread_ms, 300)

    def test_large_delta_escalates_even_when_two_audio_families_agree(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=3100,
            cue_duration_ms=5000,
            canonical_line_count=2,
            evidence_points=[
                self.point("final_mix_singing_alignment", 3050),
                self.point("source_forced_alignment_projection", 3090),
            ],
        )
        self.assertEqual(decision.action, "structural_escalation")
        self.assertEqual(decision.selected_ms, 1000)
        self.assertEqual(decision.difficulty_tier, "D_structural")

    def test_structurally_confirmed_large_delta_can_then_use_audio_consensus(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=3100,
            cue_duration_ms=5000,
            canonical_line_count=2,
            evidence_points=[
                self.point("final_mix_singing_alignment", 3050),
                self.point("source_forced_alignment_projection", 3090),
            ],
            structural_resolution_confirmed=True,
        )
        self.assertTrue(decision.automatic_mutation_allowed)
        self.assertEqual(decision.action, "auto_safe_refine")

    def test_cut_or_crossfade_routes_to_structural_tier(self):
        tier, capabilities = classify_boundary_difficulty(
            editor_ms=1000,
            proposed_ms=1050,
            cue_duration_ms=3000,
            canonical_line_count=1,
            risk_flags=["crossfade"],
        )
        self.assertEqual(tier, "D_structural")
        self.assertIn("structural_realignment", capabilities)
        self.assertIn("vocal_separation", capabilities)

    def test_long_multi_line_cue_routes_to_heavier_boundary_tier(self):
        tier, capabilities = classify_boundary_difficulty(
            editor_ms=1000,
            proposed_ms=1300,
            cue_duration_ms=9000,
            canonical_line_count=3,
        )
        self.assertEqual(tier, "C_heavy_boundary")
        self.assertIn("second_boundary_aligner", capabilities)

    def test_high_standardized_uncertainty_evidence_does_not_authorize(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=1100,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[
                self.point("final_mix_singing_alignment", 1070, uncertainty_ms=220),
                self.point("source_forced_alignment_projection", 1090, uncertainty_ms=220),
            ],
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertEqual(decision.audio_family_count, 0)

    def test_backend_raw_confidence_scale_does_not_override_calibration(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=1100,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[
                self.point("final_mix_singing_alignment", 1070, confidence=0.40),
                self.point("source_forced_alignment_projection", 1090, confidence=0.55),
            ],
        )
        self.assertTrue(decision.automatic_mutation_allowed)
        self.assertEqual(decision.action, "auto_fine_refine")

    def test_same_correlation_group_does_not_fake_two_independent_models(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=1080,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[
                {**self.point("final_mix_singing_alignment", 1060), "correlation_group": "same_model"},
                {**self.point("source_forced_alignment_projection", 1080), "correlation_group": "same_model"},
            ],
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertIn("independent", decision.reason)

    def test_same_backend_id_cannot_fake_two_independent_models(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=1080,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[
                {
                    **self.point("final_mix_singing_alignment", 1060),
                    "correlation_group": "group-a",
                    "backend_id": "same-backend",
                },
                {
                    **self.point("source_forced_alignment_projection", 1080),
                    "correlation_group": "group-b",
                    "backend_id": "same-backend",
                },
            ],
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertIn("backends", decision.reason)

    def test_direct_final_mix_timestamp_remains_primary_after_cross_validation(self):
        decision = self.adjudicate(
            editor_ms=1000,
            proposed_ms=1100,
            cue_duration_ms=2500,
            canonical_line_count=1,
            evidence_points=[
                self.point("final_mix_singing_alignment", 1070),
                self.point("source_forced_alignment_projection", 1110),
            ],
        )
        self.assertEqual(decision.selected_ms, 1070)

    def test_duplicate_evidence_family_is_rejected_fail_closed(self):
        with self.assertRaises(BoundaryAuthorityError):
            normalize_boundary_evidence(
                [
                    self.point("final_mix_singing_alignment", 1000),
                    self.point("final_mix_singing_alignment", 1010),
                ]
            )

    def test_only_explicit_authority_allows_timing_mutation(self):
        self.assertTrue(
            timing_mutation_has_audio_authority(
                {"boundary_authority": "audio_verified_boundary_v1"}
            )
        )
        self.assertTrue(
            timing_mutation_has_audio_authority(
                {"boundary_authority": "manual_verified_interval"}
            )
        )
        self.assertFalse(
            timing_mutation_has_audio_authority(
                {"status": "split_unreliable_long_cue", "evidence": "lrc_projection"}
            )
        )


if __name__ == "__main__":
    unittest.main()
