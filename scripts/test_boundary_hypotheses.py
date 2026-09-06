import unittest

from lyric_aligner.alignment.boundary_hypotheses import (
    BOUNDARY_HYPOTHESES_AUTHORITY,
    BoundaryHypothesisError,
    build_boundary_hypothesis_set,
    hypothesis_set_from_dict,
)


BASE = {
    "observer_id": "observer-a",
    "observer_revision": "rev-1",
    "correlation_group": "architecture-a",
    "boundary_id": "boundary-1",
    "boundary_kind": "start",
    "audio_basis": "final_mix",
    "language": "zh",
}


class BoundaryHypothesisTests(unittest.TestCase):
    def test_scores_are_normalized_and_ranked(self):
        result = build_boundary_hypothesis_set(
            **BASE,
            candidates=[
                {"hypothesis_id": "h1", "boundary_ms": 1000, "score": 2.0, "uncertainty_ms": 40},
                {"hypothesis_id": "h2", "boundary_ms": 1100, "score": 1.0, "uncertainty_ms": 50},
            ],
        )
        self.assertEqual([item.hypothesis_id for item in result.hypotheses], ["h1", "h2"])
        self.assertAlmostEqual(result.hypotheses[0].probability, 2 / 3)
        self.assertAlmostEqual(result.hypotheses[1].probability, 1 / 3)
        self.assertAlmostEqual(result.top1_top2_margin, 1 / 3)
        self.assertAlmostEqual(result.residual_probability, 0.0)
        self.assertFalse(result.automatic_mutation_allowed)
        self.assertEqual(result.authority, BOUNDARY_HYPOTHESES_AUTHORITY)

    def test_truncation_preserves_omitted_probability_as_residual(self):
        result = build_boundary_hypothesis_set(
            **BASE,
            candidates=[
                {"hypothesis_id": "h1", "boundary_ms": 1000, "probability": 0.50},
                {"hypothesis_id": "h2", "boundary_ms": 1100, "probability": 0.30},
                {"hypothesis_id": "h3", "boundary_ms": 1200, "probability": 0.20},
            ],
            max_hypotheses=2,
        )
        self.assertEqual(len(result.hypotheses), 2)
        self.assertAlmostEqual(result.residual_probability, 0.20)
        self.assertAlmostEqual(
            sum(item.probability for item in result.hypotheses) + result.residual_probability,
            1.0,
        )

    def test_low_margin_high_entropy_representation_is_retained(self):
        result = build_boundary_hypothesis_set(
            **BASE,
            candidates=[
                {"hypothesis_id": "a", "boundary_ms": 1000, "score": 1.00},
                {"hypothesis_id": "b", "boundary_ms": 1400, "score": 0.99},
                {"hypothesis_id": "c", "boundary_ms": 1800, "score": 0.98},
            ],
        )
        self.assertLess(result.top1_top2_margin, 0.01)
        self.assertGreater(result.posterior_entropy_bits, 1.5)

    def test_tie_break_prefers_lower_uncertainty_then_time_then_id(self):
        result = build_boundary_hypothesis_set(
            **BASE,
            candidates=[
                {"hypothesis_id": "late", "boundary_ms": 1200, "score": 1.0, "uncertainty_ms": 60},
                {"hypothesis_id": "early", "boundary_ms": 900, "score": 1.0, "uncertainty_ms": 40},
                {"hypothesis_id": "middle", "boundary_ms": 1000, "score": 1.0, "uncertainty_ms": 40},
            ],
        )
        self.assertEqual(
            [item.hypothesis_id for item in result.hypotheses],
            ["early", "middle", "late"],
        )

    def test_roundtrip_dict_preserves_contract(self):
        original = build_boundary_hypothesis_set(
            **BASE,
            candidates=[
                {"hypothesis_id": "h1", "boundary_ms": 1000, "score": 4.0, "uncertainty_ms": 20},
                {"hypothesis_id": "h2", "boundary_ms": 1300, "score": 1.0, "uncertainty_ms": 50},
            ],
        )
        restored = hypothesis_set_from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_observer_contract_cannot_claim_direct_mutation(self):
        original = build_boundary_hypothesis_set(
            **BASE,
            candidates=[{"hypothesis_id": "h1", "boundary_ms": 1000, "score": 1.0}],
        ).to_dict()
        original["automatic_mutation_allowed"] = True
        with self.assertRaises(BoundaryHypothesisError):
            hypothesis_set_from_dict(original)

    def test_duplicate_candidate_ids_fail_closed(self):
        with self.assertRaises(BoundaryHypothesisError):
            build_boundary_hypothesis_set(
                **BASE,
                candidates=[
                    {"hypothesis_id": "dup", "boundary_ms": 1000, "score": 1.0},
                    {"hypothesis_id": "dup", "boundary_ms": 1100, "score": 1.0},
                ],
            )

    def test_zero_total_score_fails_closed(self):
        with self.assertRaises(BoundaryHypothesisError):
            build_boundary_hypothesis_set(
                **BASE,
                candidates=[
                    {"hypothesis_id": "a", "boundary_ms": 1000, "score": 0.0},
                    {"hypothesis_id": "b", "boundary_ms": 1100, "score": 0.0},
                ],
            )

    def test_negative_timing_and_probability_fail_closed(self):
        with self.assertRaises(BoundaryHypothesisError):
            build_boundary_hypothesis_set(
                **BASE,
                candidates=[{"hypothesis_id": "a", "boundary_ms": -1, "score": 1.0}],
            )
        with self.assertRaises(BoundaryHypothesisError):
            build_boundary_hypothesis_set(
                **BASE,
                candidates=[{"hypothesis_id": "a", "boundary_ms": 1000, "score": -1.0}],
            )


if __name__ == "__main__":
    unittest.main()
