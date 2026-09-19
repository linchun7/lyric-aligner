import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import soundfile as sf

from lyric_aligner.audio.cuts import locate_cut_boundary
from lyric_aligner.config import DEFAULT_V4_PROFILE


SR = 16000


def chord_source(duration=12.0):
    samples = int(round(duration * SR))
    output = np.zeros(samples, dtype=np.float32)
    segment_samples = int(0.5 * SR)
    chords = [
        (220.0, 277.18, 329.63),
        (246.94, 311.13, 369.99),
        (261.63, 329.63, 392.00),
        (293.66, 369.99, 440.00),
        (329.63, 415.30, 493.88),
        (349.23, 440.00, 523.25),
        (392.00, 493.88, 587.33),
        (440.00, 554.37, 659.25),
        (466.16, 587.33, 698.46),
        (523.25, 659.25, 783.99),
        (587.33, 739.99, 880.00),
        (622.25, 783.99, 932.33),
    ]
    for index in range((samples + segment_samples - 1) // segment_samples):
        start = index * segment_samples
        end = min(samples, start + segment_samples)
        count = end - start
        t = np.arange(count, dtype=np.float64) / SR
        frequencies = chords[index % len(chords)]
        wave = sum(
            np.sin(2.0 * np.pi * frequency * t + index * 0.173)
            for frequency in frequencies
        ) / len(frequencies)
        envelope = np.ones(count, dtype=np.float64)
        fade = min(int(0.015 * SR), count // 4)
        if fade > 1:
            envelope[:fade] = np.linspace(0.3, 1.0, fade)
            envelope[-fade:] = np.linspace(1.0, 0.3, fade)
        output[start:end] = (0.65 * wave * envelope).astype(np.float32)
    return output


def point(mix, source):
    return {
        "mix_center": mix,
        "source_center": source,
        "estimated_slope": 1.0,
        "fused_score": 0.95,
        "feature_scores": {"chroma": 0.95, "mfcc": 0.93},
    }


class V4CutBoundaryLocatorTests(unittest.TestCase):
    def test_localizes_known_source_jump_without_using_coarse_midpoint_as_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = chord_source()
            source_path = root / "source.wav"
            sf.write(source_path, source, SR)

            cut_source_start = 5.0
            cut_source_end = 8.0
            left = source[: int(cut_source_start * SR)]
            right = source[int(cut_source_end * SR) :]
            mix = np.concatenate([left, right])
            mix_path = root / "mix.wav"
            sf.write(mix_path, mix, SR)

            path = [
                point(1.0, 1.0),
                point(2.0, 2.0),
                point(3.0, 3.0),
                point(4.0, 4.0),
                point(4.5, 4.5),
                point(5.5, 8.5),
                point(6.0, 9.0),
                point(7.0, 10.0),
                point(8.0, 11.0),
            ]
            config = replace(
                DEFAULT_V4_PROFILE.cut_boundary,
                context_seconds=0.70,
                candidate_step_seconds=0.05,
                source_radius_seconds=0.60,
                min_side_score=0.45,
                min_side_margin=0.0,
                min_boundary_margin=0.01,
            )
            localized = locate_cut_boundary(
                mix_audio=mix_path,
                source_audio=source_path,
                candidate_id="candidate-1",
                issue_id="issue-1",
                discontinuity={
                    "mix_before": 4.5,
                    "mix_after": 5.5,
                    "source_before": 4.5,
                    "source_after": 8.5,
                },
                effective_alignment_path=path,
                config=config,
            )
            self.assertAlmostEqual(localized.cut_mix_time, 5.0, delta=0.15)
            self.assertGreater(localized.source_gap_seconds, 2.5)
            self.assertLess(localized.localized_source_gap_start, 5.4)
            self.assertGreater(localized.localized_source_gap_end, 7.6)


if __name__ == "__main__":
    unittest.main()
