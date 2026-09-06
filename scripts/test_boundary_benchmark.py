import unittest

from lyric_aligner.evaluation.boundary_benchmark import (
    BoundaryBenchmarkError,
    evaluate_boundary_evidence_against_gold,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def plan(boundary_kind="start"):
    return {
        "task_fingerprint_sha256": SHA_A,
        "source_srt_sha256": SHA_B,
        "final_audio_sha256": SHA_C,
        "jobs": [
            {
                "boundary_id": "id-1",
                "cue_number": 12,
                "boundary_kind": boundary_kind,
                "editor_ms": 1000,
            }
        ],
    }


def evidence(predicted_ms=1030, family="final_mix_singing_alignment"):
    return {
        "task_fingerprint_sha256": SHA_A,
        "source_srt_sha256": SHA_B,
        "final_audio_sha256": SHA_C,
        "records": [
            {
                "boundary_id": "id-1",
                "points": [
                    {
                        "family": family,
                        "correlation_group": family,
                        "boundary_ms": predicted_ms,
                    }
                ],
            }
        ],
    }


def gold(*, partition="calibration", uncertainty=20, boundary_kind="start"):
    return {
        "schema_version": "1.0",
        "task_fingerprint_sha256": SHA_A,
        "source_srt_sha256": SHA_B,
        "final_audio_sha256": SHA_C,
        "records": [
            {
                "cue_number": 12,
                "boundary_kind": boundary_kind,
                "gold_ms": 1040,
                "gold_uncertainty_ms": uncertainty,
                "track": "track-a",
                "partition": partition,
            }
        ],
    }


class BoundaryBenchmarkTests(unittest.TestCase):
    def test_human_uncertainty_creates_zero_effective_error_inside_interval(self):
        report = evaluate_boundary_evidence_against_gold(
            plan=plan(), evidence=evidence(1030), gold=gold(uncertainty=20)
        )
        sample = report["samples"][0]
        self.assertEqual(sample["raw_abs_error_ms"], 10)
        self.assertEqual(sample["effective_error_ms"], 0)

    def test_editor_baseline_is_measured_alongside_backend(self):
        report = evaluate_boundary_evidence_against_gold(
            plan=plan(), evidence=evidence(1040), gold=gold(uncertainty=0)
        )
        sample = report["samples"][0]
        self.assertEqual(sample["editor_effective_error_ms"], 40)
        self.assertEqual(sample["improvement_vs_editor_ms"], 40)

    def test_benchmark_never_grants_authority(self):
        report = evaluate_boundary_evidence_against_gold(
            plan=plan(), evidence=evidence(1040), gold=gold(uncertainty=0)
        )
        summary = report["scope_summaries"]["final_mix_singing_alignment::start"]
        self.assertFalse(summary["authority_granted"])
        self.assertIn("boundary_calibration", summary["authority_note"])

    def test_start_and_end_are_separate_benchmark_scopes(self):
        report = evaluate_boundary_evidence_against_gold(
            plan=plan("end"),
            evidence=evidence(1040),
            gold=gold(uncertainty=0, boundary_kind="end"),
        )
        self.assertIn(
            "final_mix_singing_alignment::end", report["scope_summaries"]
        )
        self.assertNotIn(
            "final_mix_singing_alignment::start", report["scope_summaries"]
        )

    def test_lineage_mismatch_fails_closed(self):
        broken = gold()
        broken["final_audio_sha256"] = "d" * 64
        with self.assertRaises(BoundaryBenchmarkError):
            evaluate_boundary_evidence_against_gold(
                plan=plan(), evidence=evidence(), gold=broken
            )

    def test_duplicate_gold_identity_fails_closed(self):
        duplicate = gold()
        duplicate["records"].append(dict(duplicate["records"][0]))
        with self.assertRaises(BoundaryBenchmarkError):
            evaluate_boundary_evidence_against_gold(
                plan=plan(), evidence=evidence(), gold=duplicate
            )


if __name__ == "__main__":
    unittest.main()
