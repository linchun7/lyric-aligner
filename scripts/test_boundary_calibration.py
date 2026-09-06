import unittest

from lyric_aligner.timeline.boundary_calibration import (
    BoundaryCalibrationError,
    BoundaryCalibrationPolicy,
    calibration_artifact_is_authoritative,
    evaluate_boundary_backend_calibration,
)


class BoundaryCalibrationTests(unittest.TestCase):
    def scope(self, boundary_kind="start"):
        return {
            "language": "zh",
            "audio_basis": "final_mix",
            "boundary_kind": boundary_kind,
            "backend_version": "1.0",
            "model_id": "test-model",
            "model_revision": "r1",
        }

    def gold(self, calibration=20, holdout=10, boundary_kind="start"):
        rows = []
        total = calibration + holdout
        for i in range(total):
            rows.append(
                {
                    "id": f"b{i}",
                    "kind": "boundary",
                    "boundary_kind": boundary_kind,
                    "gold_ms": 1000 + i * 1000,
                    "gold_uncertainty_ms": 0,
                    "partition": "calibration" if i < calibration else "holdout",
                    "track": f"track-{i % 5}",
                }
            )
        return rows

    def evaluate(self, predictions, *, calibration=20, holdout=10, boundary_kind="start"):
        return evaluate_boundary_backend_calibration(
            backend_id="test_backend",
            family="final_mix_singing_alignment",
            correlation_group="test_group",
            scope=self.scope(boundary_kind),
            gold_records=self.gold(calibration, holdout, boundary_kind),
            predictions_ms=predictions,
        )

    def perfect_predictions(self, calibration=20, holdout=10):
        return {
            f"b{i}": 1000 + i * 1000 + (30 if i % 2 else -20)
            for i in range(calibration + holdout)
        }

    def test_small_accurate_set_does_not_grant_authority(self):
        artifact = self.evaluate(
            self.perfect_predictions(8, 4), calibration=8, holdout=4
        )
        self.assertFalse(artifact["passed"])
        self.assertIn("insufficient_calibration_samples", artifact["reasons"])
        self.assertIn("insufficient_holdout_samples", artifact["reasons"])

    def test_twenty_plus_ten_across_five_tracks_can_pass(self):
        artifact = self.evaluate(self.perfect_predictions())
        self.assertTrue(artifact["passed"], artifact)
        self.assertLessEqual(
            artifact["summary"]["holdout"]["p90_effective_error_frames"], 5.0
        )
        self.assertTrue(
            calibration_artifact_is_authoritative(
                artifact,
                backend_id="test_backend",
                family="final_mix_singing_alignment",
                correlation_group="test_group",
                expected_scope=self.scope("start"),
            )
        )

    def test_one_large_holdout_error_blocks_even_when_median_is_good(self):
        predictions = self.perfect_predictions()
        predictions["b29"] += 700
        artifact = self.evaluate(predictions)
        self.assertFalse(artifact["passed"])
        self.assertGreater(
            artifact["summary"]["holdout"]["max_effective_error_frames"], 12.0
        )

    def test_missing_predictions_reduce_partition_coverage(self):
        predictions = {
            f"b{i}": 1000 + i * 1000 for i in list(range(14)) + list(range(20, 25))
        }
        artifact = self.evaluate(predictions)
        self.assertFalse(artifact["passed"])
        self.assertLess(
            artifact["summary"]["calibration"]["prediction_coverage"], 0.75
        )
        self.assertLess(
            artifact["summary"]["holdout"]["prediction_coverage"], 0.75
        )

    def test_current_real_acoustic_probe_error_would_fail_policy(self):
        gold = self.gold()
        predictions = {row["id"]: row["gold_ms"] for row in gold}
        # User-audited real boundary: 16:07.000. The current acoustic probe
        # returned 16:07.731, so put that failure in held-out data.
        gold[20] = {
            **gold[20],
            "gold_ms": 967000,
            "track": "track-0",
        }
        predictions["b20"] = 967731
        artifact = evaluate_boundary_backend_calibration(
            backend_id="librosa_harmonic_boundary_v1",
            family="final_mix_acoustic_onset",
            correlation_group="librosa_final_mix_acoustic_v1",
            scope={
                "language": "en",
                "audio_basis": "final_mix",
                "boundary_kind": "start",
                "backend_version": "0.11",
                "model_id": "none_signal_processing",
                "model_revision": "v1",
            },
            gold_records=gold,
            predictions_ms=predictions,
        )
        self.assertFalse(artifact["passed"])
        self.assertGreater(
            artifact["summary"]["holdout"]["max_effective_error_frames"], 12.0
        )

    def test_tampered_artifact_hash_is_not_authoritative(self):
        artifact = self.evaluate(self.perfect_predictions())
        artifact["summary"]["holdout"]["median_effective_error_ms"] = 999
        self.assertFalse(
            calibration_artifact_is_authoritative(
                artifact,
                backend_id="test_backend",
                family="final_mix_singing_alignment",
                correlation_group="test_group",
                expected_scope=self.scope("start"),
            )
        )

    def test_start_calibration_cannot_authorize_end_scope(self):
        artifact = self.evaluate(self.perfect_predictions())
        self.assertFalse(
            calibration_artifact_is_authoritative(
                artifact,
                backend_id="test_backend",
                family="final_mix_singing_alignment",
                correlation_group="test_group",
                expected_scope={**self.scope("start"), "boundary_kind": "end"},
            )
        )


    def test_relaxed_policy_artifact_never_grants_production_authority(self):
        gold = self.gold(calibration=2, holdout=2)
        predictions = {row["id"]: row["gold_ms"] for row in gold}
        artifact = evaluate_boundary_backend_calibration(
            backend_id="test_backend",
            family="final_mix_singing_alignment",
            correlation_group="test_group",
            scope=self.scope("start"),
            gold_records=gold,
            predictions_ms=predictions,
            policy=BoundaryCalibrationPolicy(
                min_calibration_samples=2,
                min_holdout_samples=2,
                min_distinct_tracks=1,
            ),
        )
        self.assertTrue(artifact["passed"])
        self.assertFalse(
            calibration_artifact_is_authoritative(
                artifact,
                backend_id="test_backend",
                family="final_mix_singing_alignment",
                correlation_group="test_group",
                expected_scope=self.scope("start"),
            )
        )

    def test_internal_boundary_has_separate_calibration_scope(self):
        artifact = self.evaluate(
            self.perfect_predictions(), boundary_kind="internal"
        )
        self.assertTrue(artifact["passed"], artifact)
        self.assertTrue(
            calibration_artifact_is_authoritative(
                artifact,
                backend_id="test_backend",
                family="final_mix_singing_alignment",
                correlation_group="test_group",
                expected_scope=self.scope("internal"),
            )
        )

    def test_excessive_human_uncertainty_cannot_zero_out_model_error(self):
        gold = self.gold()
        gold[0] = {**gold[0], "gold_uncertainty_ms": 5000}
        predictions = {row["id"]: row["gold_ms"] + 4000 for row in gold}
        with self.assertRaises(BoundaryCalibrationError):
            evaluate_boundary_backend_calibration(
                backend_id="test_backend",
                family="final_mix_singing_alignment",
                correlation_group="test_group",
                scope=self.scope("start"),
                gold_records=gold,
                predictions_ms=predictions,
            )


if __name__ == "__main__":
    unittest.main()
