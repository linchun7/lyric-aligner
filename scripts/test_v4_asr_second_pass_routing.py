import unittest

from lyric_aligner.alignment.asr_routing import (
    AsrRoutingError,
    AsrSecondPassRoutingConfig,
    build_second_pass_plan,
)


class V4AsrSecondPassRoutingTests(unittest.TestCase):
    def alignment_plan(self):
        return {
            "mode": "plan_only",
            "backend_execution_performed": False,
            "jobs": [
                {
                    "job_id": "job-good",
                    "occurrence_id": "occ-1",
                    "track_id": "track-1",
                    "ordinal": 1,
                    "priority": "medium",
                    "canonical_line_index": 0,
                    "language_profile": "en",
                    "mix_window_ms": [1000, 2500],
                    "source_window_ms": [5000, 6500],
                    "canonical_text_sha256": "a" * 64,
                    "requested_capabilities": ["mix_asr", "word_timestamps"],
                    "reasons": ["editor_boundary_disagreement"],
                },
                {
                    "job_id": "job-weak",
                    "occurrence_id": "occ-1",
                    "track_id": "track-1",
                    "ordinal": 1,
                    "priority": "high",
                    "canonical_line_index": 1,
                    "language_profile": "ko",
                    "mix_window_ms": [3000, 4500],
                    "source_window_ms": [7000, 8500],
                    "canonical_text_sha256": "b" * 64,
                    "requested_capabilities": ["mix_asr", "word_timestamps"],
                    "reasons": ["editor_candidate_ambiguous"],
                },
                {
                    "job_id": "job-forced-only",
                    "occurrence_id": "occ-1",
                    "track_id": "track-1",
                    "ordinal": 1,
                    "priority": "high",
                    "canonical_line_index": 2,
                    "language_profile": "ja",
                    "mix_window_ms": [5000, 6500],
                    "source_window_ms": [9000, 10500],
                    "canonical_text_sha256": "c" * 64,
                    "requested_capabilities": ["source_forced_alignment"],
                    "reasons": ["run_issue:fragment"],
                },
            ],
        }

    def first_pass(self):
        return {
            "backend": "faster_whisper",
            "jobs": [
                {
                    "job_id": "job-good",
                    "canonical_text_support_score": 0.92,
                    "language_probability": 0.95,
                    "segments": [
                        {
                            "avg_logprob": -0.2,
                            "no_speech_prob": 0.05,
                        }
                    ],
                },
                {
                    "job_id": "job-weak",
                    "canonical_text_support_score": 0.40,
                    "language_probability": 0.50,
                    "segments": [
                        {
                            "avg_logprob": -1.2,
                            "no_speech_prob": 0.80,
                        }
                    ],
                },
            ],
        }

    def test_only_weak_first_pass_job_is_routed(self):
        result = build_second_pass_plan(
            alignment_plan=self.alignment_plan(),
            first_pass_evidence=self.first_pass(),
        )
        self.assertEqual(result["summary"]["first_pass_mix_asr_job_count"], 2)
        self.assertEqual(result["summary"]["second_pass_job_count"], 1)
        row = result["jobs"][0]
        self.assertEqual(row["job_id"], "job-weak")
        self.assertEqual(row["mix_window_ms"], [3000, 4500])
        self.assertEqual(row["source_window_ms"], [7000, 8500])
        self.assertEqual(row["first_pass_priority"], "high")
        self.assertEqual(
            set(row["second_pass_reasons"]),
            {
                "low_avg_logprob",
                "low_canonical_text_support",
                "low_language_probability",
                "high_no_speech_probability",
            },
        )
        self.assertEqual(result["scope_policy"], "reuse_exact_first_pass_local_windows")
        self.assertFalse(result["policy_calibrated"])
        self.assertFalse(result["backend_execution_performed"])

    def test_missing_first_pass_evidence_is_routed(self):
        evidence = self.first_pass()
        evidence["jobs"] = [evidence["jobs"][0]]
        result = build_second_pass_plan(
            alignment_plan=self.alignment_plan(),
            first_pass_evidence=evidence,
        )
        self.assertEqual(result["summary"]["second_pass_job_count"], 1)
        self.assertEqual(result["jobs"][0]["job_id"], "job-weak")
        self.assertEqual(
            result["jobs"][0]["second_pass_reasons"],
            ["missing_first_pass_evidence"],
        )
        self.assertEqual(result["jobs"][0]["second_pass_severity_rank"], 0)

    def test_missing_segments_and_line_support_are_explicit(self):
        evidence = self.first_pass()
        evidence["jobs"][1]["segments"] = []
        evidence["jobs"][1]["canonical_text_support_score"] = None
        evidence["jobs"][1]["language_probability"] = 0.9
        result = build_second_pass_plan(
            alignment_plan=self.alignment_plan(),
            first_pass_evidence=evidence,
        )
        reasons = set(result["jobs"][0]["second_pass_reasons"])
        self.assertEqual(reasons, {"missing_canonical_text_support", "missing_segments"})

    def test_missing_segment_quality_routes_instead_of_defaulting_to_good(self):
        evidence = self.first_pass()
        evidence["jobs"][1]["canonical_text_support_score"] = 0.9
        evidence["jobs"][1]["language_probability"] = 0.9
        evidence["jobs"][1]["segments"] = [{"avg_logprob": None, "no_speech_prob": None}]
        result = build_second_pass_plan(
            alignment_plan=self.alignment_plan(),
            first_pass_evidence=evidence,
        )
        self.assertEqual(result["jobs"][0]["job_id"], "job-weak")
        self.assertEqual(result["jobs"][0]["second_pass_reasons"], ["missing_segment_quality"])

    def test_missing_reasons_can_be_disabled(self):
        evidence = self.first_pass()
        evidence["jobs"][1]["segments"] = []
        evidence["jobs"][1]["canonical_text_support_score"] = None
        evidence["jobs"][1]["language_probability"] = 0.9
        result = build_second_pass_plan(
            alignment_plan=self.alignment_plan(),
            first_pass_evidence=evidence,
            config=AsrSecondPassRoutingConfig(
                reroute_missing_segments=False,
                reroute_missing_line_support=False,
            ),
        )
        self.assertEqual(result["summary"]["second_pass_job_count"], 0)

    def test_extra_first_pass_job_not_in_original_plan_blocks(self):
        evidence = self.first_pass()
        evidence["jobs"].append(
            {
                "job_id": "foreign-job",
                "canonical_text_support_score": 0.1,
                "segments": [],
            }
        )
        with self.assertRaisesRegex(AsrRoutingError, "jobs not present as mix_asr jobs"):
            build_second_pass_plan(
                alignment_plan=self.alignment_plan(),
                first_pass_evidence=evidence,
            )

    def test_max_jobs_truncation_keeps_high_priority_job_not_earliest_line(self):
        evidence = self.first_pass()
        evidence["jobs"][0]["canonical_text_support_score"] = 0.1
        plan = self.alignment_plan()
        plan["jobs"][0]["priority"] = "low"
        plan["jobs"][1]["priority"] = "high"
        result = build_second_pass_plan(
            alignment_plan=plan,
            first_pass_evidence=evidence,
            config=AsrSecondPassRoutingConfig(max_jobs=1),
        )
        self.assertEqual(result["summary"]["eligible_second_pass_job_count_before_truncation"], 2)
        self.assertEqual(result["summary"]["second_pass_job_count"], 1)
        self.assertTrue(result["summary"]["second_pass_plan_truncated"])
        self.assertEqual(result["jobs"][0]["job_id"], "job-weak")
        self.assertEqual(result["jobs"][0]["first_pass_priority"], "high")

    def test_invalid_priority_rejected(self):
        plan = self.alignment_plan()
        plan["jobs"][1]["priority"] = "urgent-ish"
        with self.assertRaisesRegex(AsrRoutingError, "invalid first-pass planner priority"):
            build_second_pass_plan(
                alignment_plan=plan,
                first_pass_evidence=self.first_pass(),
            )

    def test_invalid_nonfinite_threshold_rejected(self):
        with self.assertRaisesRegex(AsrRoutingError, "finite"):
            build_second_pass_plan(
                alignment_plan=self.alignment_plan(),
                first_pass_evidence=self.first_pass(),
                config=AsrSecondPassRoutingConfig(min_avg_logprob=float("nan")),
            )


if __name__ == "__main__":
    unittest.main()
