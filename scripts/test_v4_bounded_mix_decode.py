from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from lyric_aligner.audio.bounded_mix import load_bounded_mix


class BoundedMixDecodeTests(unittest.TestCase):
    def test_small_terminal_shortfall_is_clamped_without_padding(self):
        sr = 1000
        # decode_start=8, requested end=10.000, decoder returns through 9.998.
        audio = np.zeros(1998, dtype=np.float32)
        with patch("lyric_aligner.audio.bounded_mix.librosa.load", return_value=(audio, sr)):
            result = load_bounded_mix(
                "mix.mp3",
                sr=sr,
                mix_start=9.0,
                mix_end=10.0,
                full_mix_duration=10.0,
                padding_seconds=1.0,
                terminal_tolerance_seconds=0.005,
            )
        self.assertTrue(result.terminal_clamped)
        self.assertAlmostEqual(result.decode_start, 8.0)
        self.assertAlmostEqual(result.effective_mix_end, 9.998)
        self.assertAlmostEqual(result.terminal_shortfall_seconds, 0.002)
        self.assertIs(result.audio, audio)

    def test_mid_file_shortfall_still_fails(self):
        sr = 1000
        audio = np.zeros(1998, dtype=np.float32)
        with patch("lyric_aligner.audio.bounded_mix.librosa.load", return_value=(audio, sr)):
            with self.assertRaisesRegex(ValueError, "ended before requested interval"):
                load_bounded_mix(
                    "mix.mp3",
                    sr=sr,
                    mix_start=9.0,
                    mix_end=10.0,
                    full_mix_duration=20.0,
                    padding_seconds=1.0,
                    terminal_tolerance_seconds=0.005,
                )

    def test_large_terminal_shortfall_still_fails(self):
        sr = 1000
        audio = np.zeros(1980, dtype=np.float32)
        with patch("lyric_aligner.audio.bounded_mix.librosa.load", return_value=(audio, sr)):
            with self.assertRaisesRegex(ValueError, "ended before requested interval"):
                load_bounded_mix(
                    "mix.mp3",
                    sr=sr,
                    mix_start=9.0,
                    mix_end=10.0,
                    full_mix_duration=10.0,
                    padding_seconds=1.0,
                    terminal_tolerance_seconds=0.005,
                )

    def test_historical_one_sample_tolerance_does_not_clamp(self):
        sr = 1000
        # Required samples=2000; len+1 is sufficient under the existing contract.
        audio = np.zeros(1999, dtype=np.float32)
        with patch("lyric_aligner.audio.bounded_mix.librosa.load", return_value=(audio, sr)):
            result = load_bounded_mix(
                "mix.wav",
                sr=sr,
                mix_start=9.0,
                mix_end=10.0,
                full_mix_duration=10.0,
                padding_seconds=1.0,
            )
        self.assertFalse(result.terminal_clamped)
        self.assertEqual(result.effective_mix_end, 10.0)
        self.assertEqual(result.terminal_shortfall_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()
