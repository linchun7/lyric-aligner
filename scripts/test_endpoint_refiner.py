import unittest

from lyric_aligner.audio.endpoint_refiner import (
    ENDPOINT_REFINER_AUTHORITY,
    EndpointRefinementError,
    EndpointRefinementPolicy,
    endpoint_search_interval_from_alignment,
    refine_boundary_by_masked_score,
)


class EndpointRefinerTests(unittest.TestCase):
    def test_start_refiner_finds_latest_score_preserving_onset(self):
        true_start = 1420

        def scorer(visible_start, visible_end):
            del visible_end
            if visible_start <= true_start:
                return 0.90
            distance = visible_start - true_start
            return max(0.0, 0.90 - distance / 500.0)

        result = refine_boundary_by_masked_score(
            boundary_kind="start",
            initial_boundary_ms=500,
            search_interval_ms=[500, 2500],
            score_visible_interval=scorer,
            policy=EndpointRefinementPolicy(coarse_step_ms=100, precision_ms=20),
        )
        self.assertTrue(result.available)
        self.assertLessEqual(abs(result.refined_boundary_ms - true_start), 40)
        self.assertGreater(result.shift_ms, 800)
        self.assertFalse(result.automatic_mutation_allowed)
        self.assertEqual(result.authority, ENDPOINT_REFINER_AUTHORITY)

    def test_end_refiner_finds_earliest_score_preserving_offset(self):
        true_end = 4210

        def scorer(visible_start, visible_end):
            del visible_start
            if visible_end >= true_end:
                return 0.92
            distance = true_end - visible_end
            return max(0.0, 0.92 - distance / 500.0)

        result = refine_boundary_by_masked_score(
            boundary_kind="end",
            initial_boundary_ms=5000,
            search_interval_ms=[3000, 5000],
            score_visible_interval=scorer,
            policy=EndpointRefinementPolicy(coarse_step_ms=100, precision_ms=20),
        )
        self.assertTrue(result.available)
        self.assertLessEqual(abs(result.refined_boundary_ms - true_end), 50)
        self.assertGreater(result.shift_ms, 700)
        self.assertFalse(result.automatic_mutation_allowed)

    def test_later_spurious_score_recovery_does_not_jump_past_sustained_failure(self):
        def scorer(visible_start, visible_end):
            del visible_end
            if visible_start <= 1200:
                return 0.90
            if visible_start in {1500, 1600}:
                return 0.88
            return 0.20

        result = refine_boundary_by_masked_score(
            boundary_kind="start",
            initial_boundary_ms=500,
            search_interval_ms=[500, 2000],
            score_visible_interval=scorer,
            policy=EndpointRefinementPolicy(
                coarse_step_ms=100,
                precision_ms=20,
                consecutive_failures_to_stop=2,
            ),
        )
        self.assertLess(result.refined_boundary_ms, 1500)
        self.assertLessEqual(abs(result.refined_boundary_ms - 1200), 40)

    def test_low_baseline_score_returns_unavailable_without_mutation(self):
        result = refine_boundary_by_masked_score(
            boundary_kind="end",
            initial_boundary_ms=3000,
            search_interval_ms=[1000, 3000],
            score_visible_interval=lambda start, end: 0.30,
        )
        self.assertFalse(result.available)
        self.assertEqual(result.refined_boundary_ms, 3000)
        self.assertEqual(result.stop_reason, "baseline_target_score_below_minimum")
        self.assertEqual(result.score_call_count, 1)

    def test_start_search_interval_uses_first_lexical_token_end_not_clamped_start(self):
        words = [
            {"start": 0.0, "end": 0.8, "text": "first"},
            {"start": 0.9, "end": 1.4, "text": "second"},
        ]
        interval = endpoint_search_interval_from_alignment(
            words,
            window_start_ms=10000,
            window_end_ms=15000,
            boundary_kind="start",
            max_token_span_ms=2000,
        )
        self.assertEqual(interval, (10000, 10800))

    def test_end_search_interval_uses_last_lexical_token_start_not_clamped_end(self):
        words = [
            {"start": 0.5, "end": 1.0, "text": "first"},
            {"start": 2.1, "end": 5.0, "text": "last"},
        ]
        interval = endpoint_search_interval_from_alignment(
            words,
            window_start_ms=10000,
            window_end_ms=15000,
            boundary_kind="end",
            max_token_span_ms=2000,
        )
        self.assertEqual(interval, (12100, 14100))

    def test_nonlexical_alignment_intervals_are_ignored_for_interior_anchor(self):
        words = [
            {"start": 0.0, "end": 0.4, "text": "SP"},
            {"start": 0.4, "end": 1.0, "text": "lyric"},
            {"start": 1.0, "end": 1.5, "text": "AP"},
        ]
        start_interval = endpoint_search_interval_from_alignment(
            words,
            window_start_ms=1000,
            window_end_ms=4000,
            boundary_kind="start",
            max_token_span_ms=1200,
        )
        end_interval = endpoint_search_interval_from_alignment(
            words,
            window_start_ms=1000,
            window_end_ms=4000,
            boundary_kind="end",
            max_token_span_ms=1200,
        )
        self.assertEqual(start_interval, (1000, 2000))
        self.assertEqual(end_interval, (1400, 2600))

    def test_invalid_scorer_probability_fails_closed(self):
        with self.assertRaises(EndpointRefinementError):
            refine_boundary_by_masked_score(
                boundary_kind="start",
                initial_boundary_ms=1000,
                search_interval_ms=[1000, 2000],
                score_visible_interval=lambda start, end: 1.5,
            )

    def test_initial_boundary_must_be_inside_search_interval(self):
        with self.assertRaises(EndpointRefinementError):
            refine_boundary_by_masked_score(
                boundary_kind="end",
                initial_boundary_ms=3000,
                search_interval_ms=[1000, 2500],
                score_visible_interval=lambda start, end: 0.9,
            )


if __name__ == "__main__":
    unittest.main()
