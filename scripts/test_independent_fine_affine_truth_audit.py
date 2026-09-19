import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from scripts import v4_audit_independent_fine_affine_truth as audit


class IndependentFineAffineTruthAuditTests(unittest.TestCase):
    def test_audit_window_recovers_known_affine_offset(self):
        slope = 1.08
        offset = -1.73
        source_times = (np.arange(5000, dtype=np.float64) + 0.5) * 0.05
        source_values = (
            np.sin(source_times * 0.71)
            + 0.55 * np.sin(source_times * 1.37 + 0.4)
            + 0.25 * np.cos(source_times * 2.13)
        )
        mix_times = (np.arange(4200, dtype=np.float64) + 0.5) * 0.05
        projected = slope * mix_times + offset
        mix_values = np.interp(projected, source_times, source_values)
        result = audit._audit_window(
            mix_times=mix_times,
            mix_values=mix_values,
            source_times=source_times,
            source_values=source_values,
            start_s=60.0,
            end_s=84.0,
            nominal_slope=slope,
        )
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["best_offset_seconds"], offset, places=2)
        self.assertGreater(result["best_correlation"], 0.99)
        self.assertGreater(result["distant_second_margin"], 0.015)

    def test_robust_log_rms_is_finite_and_50ms_spaced(self):
        sr = 8000
        t = np.arange(sr * 3, dtype=np.float64) / sr
        audio = (0.2 + 0.1 * np.sin(2 * np.pi * 2 * t)) * np.sin(2 * np.pi * 220 * t)
        times, values = audit._robust_log_rms(audio, sr=sr)
        self.assertGreater(len(values), 40)
        self.assertTrue(np.all(np.isfinite(values)))
        self.assertAlmostEqual(times[1] - times[0], 0.05, places=9)
        self.assertLessEqual(float(np.max(values)), 5.0)
        self.assertGreaterEqual(float(np.min(values)), -5.0)

    def test_selection_that_consulted_independent_fine_is_rejected_before_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "selection.json"
            fake = {
                "schema_version": audit.SELECTION_SCHEMA_VERSION,
                "partition": "holdout",
                "independent_fine_predictions_consulted": True,
                "records": [{}, {}, {}, {}],
                "artifact_sha256": "a" * 64,
            }
            with mock.patch.object(audit, "_load_hashed", return_value=fake):
                with self.assertRaisesRegex(audit.IndependentFineAffineAuditError, "must not consult"):
                    audit.audit_selection(selection_path=path, expected_partition="holdout")


if __name__ == "__main__":
    unittest.main()
