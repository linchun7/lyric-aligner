import unittest

import numpy as np

from lyric_aligner.audio.ctc_forced_alignment import (
    CTC_FORCED_ALIGNMENT_AUTHORITY,
    CTCForcedAlignmentError,
    ctc_outer_boundary_hypothesis_set,
    ctc_viterbi_force_align,
)


class CTCForcedAlignmentTests(unittest.TestCase):
    def test_known_sequence_aligns_to_expected_token_regions(self):
        # class 0 = blank, 1/2 = lexical tokens.  Two lexical islands are clear.
        logits = np.array(
            [
                [8.0, 0.0, 0.0],
                [7.0, 1.0, 0.0],
                [0.0, 8.0, 0.0],
                [0.0, 8.0, 0.0],
                [7.0, 1.0, 0.0],
                [7.0, 0.0, 1.0],
                [0.0, 0.0, 8.0],
                [0.0, 0.0, 8.0],
                [8.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
        result = ctc_viterbi_force_align(logits, [1, 2], blank_id=0, frame_ms=20.0)
        self.assertEqual(result.boundary_start_ms, 40)
        self.assertEqual(result.boundary_end_ms, 160)
        self.assertEqual([(x.start_frame, x.end_frame_exclusive) for x in result.token_intervals], [(2, 4), (6, 8)])
        self.assertGreater(result.lexical_mean_probability, 0.99)
        self.assertFalse(result.automatic_mutation_allowed)
        self.assertEqual(result.authority, CTC_FORCED_ALIGNMENT_AUTHORITY)

    def test_repeated_target_tokens_require_intervening_blank(self):
        logits = np.array(
            [
                [7.0, 0.0],
                [0.0, 8.0],
                [8.0, 0.0],
                [0.0, 8.0],
                [8.0, 0.0],
            ],
            dtype=np.float64,
        )
        result = ctc_viterbi_force_align(logits, [1, 1], blank_id=0, frame_ms=10.0)
        first, second = result.token_intervals
        self.assertEqual((first.start_frame, first.end_frame_exclusive), (1, 2))
        self.assertEqual((second.start_frame, second.end_frame_exclusive), (3, 4))
        self.assertGreater(second.start_frame, first.end_frame_exclusive)

    def test_too_few_frames_for_repeated_tokens_fails_closed(self):
        logits = np.zeros((2, 2), dtype=np.float64)
        with self.assertRaisesRegex(CTCForcedAlignmentError, "too few frames"):
            ctc_viterbi_force_align(logits, [1, 1], blank_id=0, frame_ms=20.0)

    def test_target_may_begin_at_first_frame_and_end_at_last_frame(self):
        logits = np.array(
            [
                [0.0, 8.0, 0.0],
                [8.0, 0.0, 0.0],
                [0.0, 0.0, 8.0],
            ],
            dtype=np.float64,
        )
        result = ctc_viterbi_force_align(logits, [1, 2], blank_id=0, frame_ms=25.0)
        self.assertEqual(result.boundary_start_ms, 0)
        self.assertEqual(result.boundary_end_ms, 75)

    def test_framewise_constant_logit_offset_does_not_change_alignment(self):
        base = np.array(
            [
                [8.0, 0.0, 0.0],
                [0.0, 8.0, 0.0],
                [8.0, 0.0, 0.0],
                [0.0, 0.0, 8.0],
                [8.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
        shifted = base + np.arange(base.shape[0], dtype=np.float64)[:, None] * 100.0
        left = ctc_viterbi_force_align(base, [1, 2], blank_id=0, frame_ms=20.0)
        right = ctc_viterbi_force_align(shifted, [1, 2], blank_id=0, frame_ms=20.0)
        self.assertEqual(left.state_path, right.state_path)
        self.assertEqual(left.boundary_start_ms, right.boundary_start_ms)
        self.assertEqual(left.boundary_end_ms, right.boundary_end_ms)

    def test_invalid_blank_and_target_ids_fail_closed(self):
        logits = np.zeros((4, 3), dtype=np.float64)
        with self.assertRaises(CTCForcedAlignmentError):
            ctc_viterbi_force_align(logits, [1], blank_id=3, frame_ms=20.0)
        with self.assertRaises(CTCForcedAlignmentError):
            ctc_viterbi_force_align(logits, [0], blank_id=0, frame_ms=20.0)
        with self.assertRaises(CTCForcedAlignmentError):
            ctc_viterbi_force_align(logits, [4], blank_id=0, frame_ms=20.0)

    def test_nonfinite_scores_fail_closed(self):
        logits = np.zeros((4, 3), dtype=np.float64)
        logits[1, 1] = np.nan
        with self.assertRaisesRegex(CTCForcedAlignmentError, "non-finite"):
            ctc_viterbi_force_align(logits, [1], blank_id=0, frame_ms=20.0)

    def test_outer_boundary_posteriors_peak_near_clear_viterbi_edges(self):
        logits = np.array(
            [
                [8.0, 0.0, 0.0],
                [7.0, 1.0, 0.0],
                [0.0, 8.0, 0.0],
                [0.0, 8.0, 0.0],
                [7.0, 1.0, 0.0],
                [7.0, 0.0, 1.0],
                [0.0, 0.0, 8.0],
                [0.0, 0.0, 8.0],
                [8.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
        common = dict(
            frame_scores=logits,
            target_token_ids=[1, 2],
            blank_id=0,
            frame_ms=20.0,
            window_start_ms=1000,
            observer_id="fixture-ctc",
            observer_revision="r1",
            correlation_group="fixture-ctc-family",
            boundary_id="cue-1",
            audio_basis="final_mix",
            language="zh",
            max_hypotheses=3,
        )
        start = ctc_outer_boundary_hypothesis_set(boundary_kind="start", **common)
        end = ctc_outer_boundary_hypothesis_set(boundary_kind="end", **common)
        self.assertEqual(start.hypotheses[0].boundary_ms, 1040)
        self.assertEqual(end.hypotheses[0].boundary_ms, 1160)
        self.assertGreater(start.hypotheses[0].probability, 0.95)
        self.assertGreater(end.hypotheses[0].probability, 0.95)
        self.assertAlmostEqual(
            sum(item.probability for item in start.hypotheses) + start.residual_probability,
            1.0,
            places=6,
        )
        self.assertFalse(start.automatic_mutation_allowed)

    def test_ambiguous_boundary_retains_probability_mass_and_small_margin(self):
        logits = np.array(
            [
                [8.0, 0.0, 0.0],
                [4.0, 4.0, 0.0],
                [4.0, 4.0, 0.0],
                [8.0, 0.0, 0.0],
                [0.0, 0.0, 8.0],
                [8.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
        result = ctc_outer_boundary_hypothesis_set(
            logits,
            [1, 2],
            blank_id=0,
            frame_ms=20.0,
            boundary_kind="start",
            window_start_ms=0,
            observer_id="fixture-ctc",
            observer_revision="r1",
            correlation_group="fixture-ctc-family",
            boundary_id="cue-ambiguous",
            audio_basis="final_mix",
            language="zh",
            max_hypotheses=2,
        )
        self.assertEqual(len(result.hypotheses), 2)
        self.assertGreater(result.hypotheses[1].probability, 0.05)
        self.assertIsNotNone(result.top1_top2_margin)
        self.assertLess(result.top1_top2_margin, 0.9)
        self.assertGreaterEqual(result.residual_probability, 0.0)

    def test_posterior_entropy_uses_full_boundary_distribution_not_lumped_residual(self):
        logits = np.array(
            [
                [4.0, 4.0, 0.0],
                [4.0, 4.0, 0.0],
                [4.0, 4.0, 0.0],
                [4.0, 4.0, 0.0],
                [4.0, 4.0, 0.0],
                [8.0, 0.0, 0.0],
                [0.0, 0.0, 8.0],
                [8.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
        result = ctc_outer_boundary_hypothesis_set(
            logits,
            [1, 2],
            blank_id=0,
            frame_ms=20.0,
            boundary_kind="start",
            window_start_ms=0,
            observer_id="fixture-ctc",
            observer_revision="r1",
            correlation_group="fixture-ctc-family",
            boundary_id="cue-diffuse",
            audio_basis="final_mix",
            language="zh",
            max_hypotheses=1,
        )
        top = result.hypotheses[0].probability
        lumped_entropy = 0.0
        for probability in (top, result.residual_probability):
            if probability > 0.0:
                lumped_entropy -= probability * np.log2(probability)
        self.assertGreater(result.residual_probability, 0.1)
        self.assertGreater(result.posterior_entropy_bits, lumped_entropy + 0.1)


if __name__ == "__main__":
    unittest.main()
