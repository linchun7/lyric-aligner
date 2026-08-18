import unittest

import librosa
import numpy as np

from lyric_aligner.audio.coarse_mapper import build_coarse_timewarp


SR = 8000


def source_song(seconds=24.0):
    length = int(seconds * SR)
    y = np.zeros(length, dtype=np.float32)
    frequencies = [
        196.0,
        246.94,
        293.66,
        349.23,
        440.0,
        329.63,
        261.63,
        392.0,
        523.25,
        311.13,
        233.08,
        466.16,
    ]
    for index, frequency in enumerate(frequencies):
        start = int(index * 2.0 * SR)
        end = min(length, int((index + 1) * 2.0 * SR))
        if start >= length:
            break
        t = np.arange(end - start, dtype=np.float32) / SR
        y[start:end] = (
            0.7 * np.sin(2 * np.pi * frequency * t)
            + 0.2 * np.sin(4 * np.pi * frequency * t)
        )
    return y


def clicks(length, bpm=140.0):
    y = np.zeros(length, dtype=np.float32)
    for beat in np.arange(0, length / SR, 60.0 / bpm):
        start = int(beat * SR)
        end = min(length, start + int(0.01 * SR))
        if end > start:
            y[start:end] += 0.8
    return y


class V4CoarseMapperTests(unittest.TestCase):
    def test_multiple_windows_recover_affine_path_under_click(self):
        source = source_song()
        segment_start = 3.0
        segment_end = 19.0
        segment = source[int(segment_start * SR) : int(segment_end * SR)]
        rate = 1.20
        stretched = librosa.effects.time_stretch(segment, rate=rate)
        mix = stretched + clicks(len(stretched))
        result = build_coarse_timewarp(
            mix,
            source,
            sr=SR,
            mix_start=0.0,
            mix_end=len(mix) / SR,
            bpm_prior=1.05,
            feature_hop_length=512,
            window_seconds=4.0,
            step_seconds=2.0,
            candidate_step_seconds=0.25,
            slope_minimum=0.9,
            slope_maximum=1.4,
            slope_step=0.1,
            min_score=0.55,
            min_margin=0.0,
        )
        self.assertGreaterEqual(len(result["path"]), 4)
        self.assertFalse(result["timewarp"]["blocked"])
        self.assertAlmostEqual(
            result["timewarp"]["mapping"]["base_slope"], rate, delta=0.15
        )
        starts = [point["source_center"] for point in result["path"]]
        self.assertEqual(starts, sorted(starts))

    def test_interval_scoped_features_restore_global_mix_coordinates(self):
        source = source_song()
        segment = source[int(4.0 * SR) : int(18.0 * SR)]
        rate = 1.20
        stretched = librosa.effects.time_stretch(segment, rate=rate)
        body = stretched + clicks(len(stretched))
        prefix_seconds = 7.0
        suffix_seconds = 5.0
        mix = np.concatenate(
            [
                np.zeros(int(prefix_seconds * SR), dtype=np.float32),
                body,
                np.zeros(int(suffix_seconds * SR), dtype=np.float32),
            ]
        )
        mix_start = prefix_seconds
        mix_end = prefix_seconds + len(body) / SR
        result = build_coarse_timewarp(
            mix,
            source,
            sr=SR,
            mix_start=mix_start,
            mix_end=mix_end,
            feature_hop_length=512,
            window_seconds=4.0,
            step_seconds=2.0,
            candidate_step_seconds=0.25,
            slope_minimum=0.9,
            slope_maximum=1.4,
            slope_step=0.1,
            min_score=0.55,
            min_margin=0.0,
        )
        self.assertGreaterEqual(result["windows"][0]["mix_start"], mix_start)
        self.assertGreater(result["path"][0]["mix_center"], mix_start)
        self.assertLessEqual(result["path"][-1]["mix_center"], mix_end)
        scope = result["feature_scope"]
        self.assertAlmostEqual(scope["mix_feature_start"], mix_start, delta=1 / SR)
        self.assertLess(
            scope["mix_feature_end"] - scope["mix_feature_start"],
            scope["full_mix_duration"],
        )
        self.assertAlmostEqual(
            result["timewarp"]["mapping"]["base_slope"], rate, delta=0.15
        )

    def test_bounded_mix_buffer_matches_full_mix_result(self):
        source = source_song()
        segment = source[int(4.0 * SR) : int(18.0 * SR)]
        rate = 1.20
        body = librosa.effects.time_stretch(segment, rate=rate)
        body = body + clicks(len(body))
        prefix_seconds = 7.0
        suffix_seconds = 5.0
        mix = np.concatenate(
            [
                np.zeros(int(prefix_seconds * SR), dtype=np.float32),
                body,
                np.zeros(int(suffix_seconds * SR), dtype=np.float32),
            ]
        )
        mix_start = prefix_seconds
        mix_end = prefix_seconds + len(body) / SR
        kwargs = {
            "sr": SR,
            "mix_start": mix_start,
            "mix_end": mix_end,
            "feature_hop_length": 512,
            "window_seconds": 4.0,
            "step_seconds": 2.0,
            "candidate_step_seconds": 0.25,
            "slope_minimum": 0.9,
            "slope_maximum": 1.4,
            "slope_step": 0.1,
            "min_score": 0.55,
            "min_margin": 0.0,
        }
        full = build_coarse_timewarp(mix, source, **kwargs)

        buffer_start = 6.0
        buffer_end = min(len(mix) / SR, mix_end + 1.0)
        bounded = mix[int(buffer_start * SR) : int(buffer_end * SR)]
        cropped = build_coarse_timewarp(
            bounded,
            source,
            mix_audio_start=buffer_start,
            full_mix_duration=len(mix) / SR,
            **kwargs,
        )

        self.assertEqual(cropped["windows"], full["windows"])
        self.assertEqual(cropped["path"], full["path"])
        self.assertEqual(cropped["timewarp"], full["timewarp"])
        self.assertEqual(
            cropped["feature_scope"]["full_mix_duration"],
            full["feature_scope"]["full_mix_duration"],
        )


if __name__ == "__main__":
    unittest.main()
