import unittest

import librosa
import numpy as np

from lyric_aligner.audio.features import extract_harmonic_features, retrieve_coarse_window, slope_grid


SR = 8000


def melody(seconds=18.0):
    length = int(seconds * SR)
    y = np.zeros(length, dtype=np.float32)
    notes = [220.0, 277.18, 329.63, 392.0, 466.16, 349.23, 293.66, 246.94, 415.30]
    note_seconds = 2.0
    for index, frequency in enumerate(notes):
        start = int(index * note_seconds * SR)
        end = min(length, int((index + 1) * note_seconds * SR))
        if start >= length:
            break
        t = np.arange(end - start, dtype=np.float32) / SR
        envelope = np.minimum(1.0, np.minimum(t / 0.08, (t[-1] - t + 1e-6) / 0.08))
        y[start:end] = (
            0.65 * np.sin(2 * np.pi * frequency * t)
            + 0.25 * np.sin(2 * np.pi * frequency * 2 * t)
        ) * envelope
    return y


def add_click(y, bpm=140.0, amplitude=0.9):
    out = y.copy()
    interval = 60.0 / bpm
    click = max(1, int(0.012 * SR))
    for beat in np.arange(0, len(y) / SR, interval):
        start = int(beat * SR)
        end = min(len(out), start + click)
        if end > start:
            out[start:end] += amplitude * np.hanning((end - start) * 2)[: end - start]
    return out


class V4AudioFeatureTests(unittest.TestCase):
    def test_wrong_bpm_prior_does_not_remove_global_slope_search(self):
        grid = slope_grid(minimum=0.8, maximum=1.5, step=0.1, bpm_prior=1.05)
        self.assertIn(1.4, grid)
        self.assertIn(1.05, grid)

    def test_harmonic_chroma_retrieves_time_stretched_segment_under_strong_click(self):
        source = melody()
        source_start = 4.0
        source_end = 12.0
        segment = source[int(source_start * SR) : int(source_end * SR)]
        rate = 1.20
        mix_segment = librosa.effects.time_stretch(segment, rate=rate)
        mix_segment = add_click(mix_segment)
        padding = np.zeros(int(1.0 * SR), dtype=np.float32)
        mix = np.concatenate([padding, mix_segment, padding])

        source_features = extract_harmonic_features(source, sr=SR, hop_length=512)
        mix_features = extract_harmonic_features(mix, sr=SR, hop_length=512)
        result = retrieve_coarse_window(
            mix_features,
            source_features,
            mix_start=1.0,
            mix_end=1.0 + len(mix_segment) / SR,
            slopes=[1.0, 1.1, 1.2, 1.3, 1.4],
            candidate_step_seconds=0.25,
            min_score=0.60,
            min_margin=0.0,
        )
        self.assertAlmostEqual(result.top1.source_start, source_start, delta=0.8)
        self.assertAlmostEqual(result.top1.estimated_slope, rate, delta=0.11)
        self.assertGreaterEqual(result.top1.feature_agreement, 1)

    def test_repeated_motif_surfaces_low_margin_instead_of_false_unique_match(self):
        motif = melody(8.0)
        source = np.concatenate([motif, np.zeros(SR), motif])
        mix = motif.copy()
        source_features = extract_harmonic_features(source, sr=SR, hop_length=512)
        mix_features = extract_harmonic_features(mix, sr=SR, hop_length=512)
        result = retrieve_coarse_window(
            mix_features,
            source_features,
            mix_start=0.0,
            mix_end=len(mix) / SR,
            slopes=[1.0],
            candidate_step_seconds=0.25,
            nms_separation_seconds=4.0,
            min_score=0.50,
            min_margin=0.10,
        )
        self.assertIsNotNone(result.top2)
        self.assertLess(result.margin, 0.10)
        self.assertTrue(result.ambiguous)


if __name__ == "__main__":
    unittest.main()
