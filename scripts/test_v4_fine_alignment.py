import unittest

import librosa
import numpy as np

from lyric_aligner.audio.coarse_mapper import build_coarse_timewarp
from lyric_aligner.audio.fine_alignment import refine_coarse_mapping, should_run_fine_alignment


SR = 8000


def song(seconds=20.0):
    length = int(seconds * SR)
    y = np.zeros(length, dtype=np.float32)
    notes = [196, 233.08, 277.18, 329.63, 392, 466.16, 349.23, 261.63, 415.30, 311.13]
    for idx, freq in enumerate(notes):
        start = int(idx * 2 * SR)
        end = min(length, int((idx + 1) * 2 * SR))
        if start >= length:
            break
        t = np.arange(end - start, dtype=np.float32) / SR
        y[start:end] = 0.7 * np.sin(2 * np.pi * freq * t) + 0.18 * np.sin(4 * np.pi * freq * t)
    return y


def click(length, bpm=140):
    y = np.zeros(length, dtype=np.float32)
    for beat in np.arange(0, length / SR, 60 / bpm):
        start = int(beat * SR)
        end = min(length, start + 60)
        y[start:end] += 0.7
    return y


class V4FineAlignmentTests(unittest.TestCase):
    def test_clean_affine_is_skipped_by_default(self):
        coarse = {
            "result": {
                "windows": [{"ambiguous": False}],
                "timewarp": {"blocked": False, "selection": "AFFINE_ACCEPTED"},
            }
        }
        self.assertFalse(should_run_fine_alignment(coarse))
        result = refine_coarse_mapping(
            np.zeros(1000), np.zeros(1000), coarse, sr=SR
        )
        self.assertFalse(result["applied"])

    def test_forced_fine_refines_time_stretched_clicked_mapping_without_large_regression(self):
        source = song()
        source_start = 3.35
        source_end = 17.35
        segment = source[int(source_start * SR) : int(source_end * SR)]
        rate = 1.20
        mix = librosa.effects.time_stretch(segment, rate=rate)
        mix = mix + click(len(mix))
        coarse = build_coarse_timewarp(
            mix,
            source,
            sr=SR,
            mix_start=0,
            mix_end=len(mix) / SR,
            bpm_prior=1.05,
            feature_hop_length=512,
            window_seconds=4,
            step_seconds=2,
            candidate_step_seconds=0.5,
            slope_minimum=1.0,
            slope_maximum=1.4,
            slope_step=0.1,
            min_score=0.50,
            min_margin=0.0,
        )
        wrapped = {"result": coarse}
        coarse_errors = []
        for point in coarse["path"]:
            true_center = source_start + rate * point["mix_center"]
            coarse_errors.append(abs(point["source_center"] - true_center))
        fine = refine_coarse_mapping(
            mix,
            source,
            wrapped,
            sr=SR,
            force=True,
            hop_length=256,
            source_radius_seconds=1.0,
            slope_radius=0.08,
            slope_step=0.02,
            candidate_step_seconds=0.05,
            min_score=0.50,
            min_margin=0.0,
            bpm_prior=1.05,
        )
        fine_errors = []
        for point in fine["path"]:
            true_center = source_start + rate * point["mix_center"]
            fine_errors.append(abs(point["refined_source_center"] - true_center))
        self.assertTrue(fine["applied"])
        self.assertLessEqual(
            float(np.median(fine_errors)),
            float(np.median(coarse_errors)) + 0.20,
        )
        self.assertAlmostEqual(
            fine["timewarp"]["mapping"]["base_slope"], rate, delta=0.15
        )


if __name__ == "__main__":
    unittest.main()
