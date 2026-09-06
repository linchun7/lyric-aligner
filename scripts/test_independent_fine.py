import unittest

import numpy as np

from lyric_aligner.audio.independent_fine import (
    INDEPENDENT_FINE_AUTHORITY,
    INDEPENDENT_FINE_CORRELATION_GROUP,
    OnsetFeatureBundle,
    extract_percussive_onset_features,
    retrieve_independent_onset_window,
)


def bundle(onset, multiband, *, sr=1000, hop=100):
    onset = np.asarray(onset, dtype=np.float32)
    multiband = np.asarray(multiband, dtype=np.float32)
    return OnsetFeatureBundle(
        sr=sr,
        hop_length=hop,
        duration_seconds=multiband.shape[1] * hop / sr,
        onset=onset,
        multiband_flux=multiband,
    )


def pattern(length=20, bands=8):
    x = np.arange(length, dtype=np.float32)
    onset = (0.1 + ((x * 7) % 13) / 13.0)[None, :]
    rows = []
    for band in range(bands):
        rows.append(0.05 + (((x + 3 * band) * (band + 5)) % 17) / 17.0)
    return onset.astype(np.float32), np.asarray(rows, dtype=np.float32)


class IndependentFineTests(unittest.TestCase):
    def test_shadow_authority_and_real_feature_shapes(self):
        sr = 8000
        audio = np.zeros(sr * 3, dtype=np.float32)
        for at in (0.3, 0.7, 1.1, 1.65, 2.2, 2.55):
            index = int(at * sr)
            audio[index:index + 24] += np.hanning(24).astype(np.float32)
        features = extract_percussive_onset_features(audio, sr=sr, hop_length=128)
        self.assertEqual(features.onset.shape[0], 1)
        self.assertEqual(features.multiband_flux.shape[0], 8)
        self.assertEqual(features.onset.shape[1], features.multiband_flux.shape[1])
        self.assertGreater(features.frame_count, 20)

    def test_retrieves_unique_attack_pattern(self):
        query_onset, query_multi = pattern()
        source_onset = np.zeros((1, 100), dtype=np.float32)
        source_multi = np.zeros((8, 100), dtype=np.float32)
        source_onset[:, 20:40] = query_onset
        source_multi[:, 20:40] = query_multi
        result = retrieve_independent_onset_window(
            bundle(query_onset, query_multi),
            bundle(source_onset, source_multi),
            mix_start=0.0,
            mix_end=2.0,
            slopes=[1.0],
            source_search_start=0.0,
            source_search_end=9.5,
            candidate_step_seconds=0.1,
            min_score=0.4,
            min_margin=0.02,
        )
        self.assertAlmostEqual(result.top1.source_start, 2.0, places=6)
        self.assertGreater(result.top1.fused_score, 0.99)
        self.assertFalse(result.ambiguous)
        self.assertFalse(result.automatic_mutation_allowed)
        self.assertEqual(result.authority, INDEPENDENT_FINE_AUTHORITY)
        self.assertEqual(result.correlation_group, INDEPENDENT_FINE_CORRELATION_GROUP)

    def test_repeated_attack_pattern_is_ambiguous(self):
        query_onset, query_multi = pattern()
        source_onset = np.zeros((1, 100), dtype=np.float32)
        source_multi = np.zeros((8, 100), dtype=np.float32)
        for start in (20, 60):
            source_onset[:, start:start + 20] = query_onset
            source_multi[:, start:start + 20] = query_multi
        result = retrieve_independent_onset_window(
            bundle(query_onset, query_multi),
            bundle(source_onset, source_multi),
            mix_start=0.0,
            mix_end=2.0,
            slopes=[1.0],
            source_search_start=0.0,
            source_search_end=9.5,
            candidate_step_seconds=0.1,
            nms_separation_seconds=0.2,
            min_score=0.4,
            min_margin=0.02,
        )
        self.assertIsNotNone(result.top2)
        self.assertLess(result.margin, 1e-6)
        self.assertTrue(result.ambiguous)

    def test_recovers_time_stretched_pattern_with_correct_slope(self):
        long_onset, long_multi = pattern(length=24)
        positions = np.linspace(0.0, 23.0, 20)
        query_onset = np.asarray([
            np.interp(positions, np.arange(24), long_onset[0])
        ], dtype=np.float32)
        query_multi = np.asarray([
            np.interp(positions, np.arange(24), row) for row in long_multi
        ], dtype=np.float32)
        source_onset = np.zeros((1, 100), dtype=np.float32)
        source_multi = np.zeros((8, 100), dtype=np.float32)
        source_onset[:, 30:54] = long_onset
        source_multi[:, 30:54] = long_multi
        result = retrieve_independent_onset_window(
            bundle(query_onset, query_multi),
            bundle(source_onset, source_multi),
            mix_start=0.0,
            mix_end=2.0,
            slopes=[1.0, 1.2],
            source_search_start=2.0,
            source_search_end=7.0,
            candidate_step_seconds=0.1,
            min_score=0.4,
            min_margin=0.01,
        )
        self.assertAlmostEqual(result.top1.source_start, 3.0, places=6)
        self.assertAlmostEqual(result.top1.estimated_slope, 1.2, places=6)
        self.assertGreater(result.top1.fused_score, 0.99)

    def test_invalid_feature_sampling_mismatch_fails(self):
        q_onset, q_multi = pattern()
        left = bundle(q_onset, q_multi, sr=1000, hop=100)
        right = bundle(q_onset, q_multi, sr=2000, hop=100)
        with self.assertRaises(ValueError):
            retrieve_independent_onset_window(
                left,
                right,
                mix_start=0.0,
                mix_end=1.0,
                slopes=[1.0],
                source_search_start=0.0,
                source_search_end=1.5,
            )


if __name__ == "__main__":
    unittest.main()
