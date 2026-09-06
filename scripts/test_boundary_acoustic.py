import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from lyric_aligner.audio.boundary_acoustic import (
    AcousticBoundaryConfig,
    _analyze_array,
    analyze_final_mix_boundary,
    evidence_point_from_acoustic_result,
)


class BoundaryAcousticTests(unittest.TestCase):
    SR = 22050

    def config(self):
        return AcousticBoundaryConfig(
            sample_rate=self.SR,
            search_ms=450,
            analysis_padding_ms=150,
            frame_length=1024,
            hop_length=128,
            smoothing_frames=3,
            min_peak_z=1.2,
            max_candidate_disagreement_ms=180,
        )

    def tone_gate(self, *, start_s=1.0, end_s=2.0, duration_s=3.0):
        n = int(round(duration_s * self.SR))
        t = np.arange(n, dtype=np.float32) / self.SR
        y = np.zeros(n, dtype=np.float32)
        mask = (t >= start_s) & (t < end_s)
        # Two harmonics make HPSS behave more like a voiced region than a click.
        phase = t[mask] - start_s
        y[mask] = (
            0.55 * np.sin(2 * np.pi * 220.0 * phase)
            + 0.25 * np.sin(2 * np.pi * 440.0 * phase)
        ).astype(np.float32)
        # Short ramps avoid an artificial single-sample impulse while preserving
        # a well-defined onset/offset for the synthetic regression.
        ramp = int(round(0.025 * self.SR))
        active = np.flatnonzero(mask)
        if active.size > 2 * ramp:
            y[active[:ramp]] *= np.linspace(0.0, 1.0, ramp, dtype=np.float32)
            y[active[-ramp:]] *= np.linspace(1.0, 0.0, ramp, dtype=np.float32)
        return y

    def test_harmonic_start_is_localized_near_true_onset(self):
        result = _analyze_array(
            self.tone_gate(),
            sr=self.SR,
            window_start_ms=0,
            editor_ms=900,
            boundary_kind="start",
            config=self.config(),
        )
        self.assertIn("boundary_ms", result)
        self.assertLessEqual(abs(int(result["boundary_ms"]) - 1000), 120)

    def test_harmonic_end_is_localized_near_true_offset(self):
        result = _analyze_array(
            self.tone_gate(),
            sr=self.SR,
            window_start_ms=0,
            editor_ms=2100,
            boundary_kind="end",
            config=self.config(),
        )
        self.assertIn("boundary_ms", result)
        self.assertLessEqual(abs(int(result["boundary_ms"]) - 2000), 140)

    def test_silence_does_not_create_evidence(self):
        result = _analyze_array(
            np.zeros(self.SR * 2, dtype=np.float32),
            sr=self.SR,
            window_start_ms=0,
            editor_ms=1000,
            boundary_kind="start",
            config=self.config(),
        )
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "near_silence_window")
        self.assertIsNone(evidence_point_from_acoustic_result(result))

    def test_available_result_maps_to_independent_final_mix_family(self):
        synthetic = {
            "available": True,
            "family": "final_mix_acoustic_onset",
            "correlation_group": "librosa_final_mix_acoustic_v1",
            "boundary_ms": 1010,
            "confidence": 0.81,
            "uncertainty_ms": 70,
            "backend_id": "librosa_harmonic_boundary_v1",
            "backend_version": "0.11",
            "model_id": "none_signal_processing",
            "model_revision": "v1",
        }
        point = evidence_point_from_acoustic_result(synthetic)
        self.assertEqual(point["family"], "final_mix_acoustic_onset")
        self.assertEqual(point["audio_basis"], "final_mix")
        self.assertEqual(point["boundary_ms"], 1010)
        self.assertEqual(point["correlation_group"], "librosa_final_mix_acoustic_v1")

    def test_file_api_reads_real_wav_and_emits_no_authority_field(self):
        y = self.tone_gate()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "tone.wav"
            sf.write(path, y, self.SR)
            result = analyze_final_mix_boundary(
                path,
                editor_ms=900,
                boundary_kind="start",
                config=self.config(),
            )
        self.assertIn("boundary_ms", result)
        self.assertLessEqual(abs(int(result["boundary_ms"]) - 1000), 120)
        point = evidence_point_from_acoustic_result(result)
        if point is not None:
            self.assertNotIn("automatic_mutation_allowed", point)
            self.assertNotIn("boundary_authority", point)


if __name__ == "__main__":
    unittest.main()
