from __future__ import annotations

import unittest

import numpy as np

from lyric_aligner.audio.prepared_stem import (
    PreparedStemConfig,
    diagnose_same_track_splice,
)


class PreparedStemSpliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sample_rate = 1000
        rng = np.random.default_rng(20260902)
        self.stem = rng.normal(0.0, 0.25, 25 * self.sample_rate).astype(np.float64)
        self.duration = 10.0
        frames = int(self.duration * self.sample_rate)
        index = np.arange(frames)
        early = self.stem[index + 2 * self.sample_rate]
        late = self.stem[index + 5 * self.sample_rate]
        time = index / self.sample_rate
        late_share = np.clip((time - 4.8) / 0.6, 0.0, 1.0)
        self.splice_mix = (1.0 - late_share) * early + late_share * late
        self.single_mix = early.copy()

    def test_detects_two_mode_same_track_crossfade(self) -> None:
        result = diagnose_same_track_splice(
            self.splice_mix,
            self.stem,
            sample_rate=self.sample_rate,
            occurrence_start=0.0,
            occurrence_end=self.duration,
        )
        self.assertEqual(result["status"], "splice_supported")
        self.assertTrue(result["splice_supported"])
        self.assertTrue(result["diagnostic_only"])
        self.assertFalse(result["automatic_timing_change_allowed"])
        self.assertGreaterEqual(len(result["modes"]), 2)
        offsets = sorted(mode["offset_seconds"] for mode in result["modes"][:4])
        self.assertTrue(any(abs(value - 2.0) <= 0.08 for value in offsets))
        self.assertTrue(any(abs(value - 5.0) <= 0.08 for value in offsets))
        self.assertGreaterEqual(result["verification"]["dual_positive_count"], 5)
        self.assertGreaterEqual(result["verification"]["dual_positive_span_seconds"], 0.2)
        self.assertIsNotNone(result["crossover"])
        self.assertAlmostEqual(result["crossover"]["mix_time_seconds"], 5.1, delta=0.15)

    def test_single_lag_stem_does_not_claim_splice(self) -> None:
        result = diagnose_same_track_splice(
            self.single_mix,
            self.stem,
            sample_rate=self.sample_rate,
            occurrence_start=0.0,
            occurrence_end=self.duration,
        )
        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["splice_supported"])
        self.assertTrue(result["diagnostic_only"])
        self.assertFalse(result["automatic_timing_change_allowed"])
        self.assertFalse(result["negative_result_is_clear_authority"])
        self.assertIsNone(result.get("crossover"))

    def test_invalid_config_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            PreparedStemConfig(mode_min_support_centers=1).validate()
        with self.assertRaises(ValueError):
            PreparedStemConfig(verification_min_r2=1.1).validate()


if __name__ == "__main__":
    unittest.main()
