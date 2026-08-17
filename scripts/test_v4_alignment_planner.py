import json
import sys
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.alignment.backends import (
    BackendCapability,
    capability_available,
    inspect_backends,
)
from lyric_aligner.alignment.planner import (
    AlignmentPlannerConfig,
    AlignmentPlanningError,
    build_alignment_plan,
)


class V4AlignmentPlannerTests(unittest.TestCase):
    def timeline(self):
        return {
            "result": {
                "occurrence_id": "occ-1",
                "track_id": "track-1",
                "ordinal": 1,
                "language_profile": "ko",
                "canonical_selection_sha256": "a" * 64,
                "lines": [
                    {
                        "canonical_line_index": 0,
                        "text": "private line zero",
                        "source_start_ms": 10000,
                        "source_end_ms": 11500,
                        "mix_start_ms": 5000,
                        "mix_end_ms": 6500,
                    },
                    {
                        "canonical_line_index": 1,
                        "text": "private line one",
                        "source_start_ms": 12000,
                        "source_end_ms": 13500,
                        "mix_start_ms": 7000,
                        "mix_end_ms": 8500,
                    },
                ],
            }
        }

    def editor(self):
        return {
            "mode": "shadow_only",
            "authority": {"automatic_timing_change_allowed": False},
            "occurrences": [
                {
                    "occurrence_id": "occ-1",
                    "lines": [
                        {
                            "canonical_line_index": 0,
                            "canonical_text_sha256": __import__("hashlib").sha256(
                                b"private line zero"
                            ).hexdigest(),
                            "best_editor_cue_number": 10,
                            "suggested_onset_delta_ms": 700,
                            "suggested_offset_delta_ms": 80,
                            "best_candidate_margin_uncalibrated": 0.30,
                        },
                        {
                            "canonical_line_index": 1,
                            "canonical_text_sha256": __import__("hashlib").sha256(
                                b"private line one"
                            ).hexdigest(),
                            "best_editor_cue_number": 11,
                            "suggested_onset_delta_ms": 30,
                            "suggested_offset_delta_ms": 20,
                            "best_candidate_margin_uncalibrated": 0.03,
                        },
                    ],
                }
            ],
        }

    def test_editor_disagreement_and_ambiguity_create_bounded_local_jobs(self):
        plan = build_alignment_plan(
            run={"issues": []},
            timeline_payloads=[self.timeline()],
            editor_evidence=self.editor(),
        )
        self.assertFalse(plan["backend_execution_performed"])
        self.assertEqual(plan["summary"]["job_count"], 2)
        first, second = plan["jobs"]
        self.assertEqual(first["reasons"], ["editor_boundary_disagreement"])
        self.assertEqual(first["mix_window_ms"], [3500, 8000])
        self.assertEqual(first["source_window_ms"], [9000, 12500])
        self.assertIn("mix_asr", first["requested_capabilities"])
        self.assertIn("source_forced_alignment", first["requested_capabilities"])
        self.assertEqual(second["reasons"], ["editor_candidate_ambiguous"])
        serialized = json.dumps(plan)
        self.assertNotIn("private line zero", serialized)
        self.assertNotIn("private line one", serialized)

    def test_run_issue_creates_high_priority_occurrence_job(self):
        plan = build_alignment_plan(
            run={
                "issues": [
                    {
                        "issue_id": "issue-1",
                        "kind": "transition_overlap",
                        "code": "cross_track_overlap_candidate",
                        "occurrence_id": "occ-1",
                        "interval_start": 4.0,
                        "interval_end": 5.0,
                    }
                ]
            },
            timeline_payloads=[self.timeline()],
        )
        self.assertEqual(plan["summary"]["job_count"], 1)
        job = plan["jobs"][0]
        self.assertEqual(job["priority"], "high")
        self.assertEqual(
            job["reasons"], ["run_issue:cross_track_overlap_candidate"]
        )
        self.assertEqual(job["mix_window_ms"], [2500, 6500])
        self.assertIsNone(job["canonical_line_index"])

    def test_editor_missing_is_opt_in_to_avoid_flooding_jobs(self):
        editor = self.editor()
        editor["occurrences"][0]["lines"][0].update(
            {
                "best_editor_cue_number": None,
                "suggested_onset_delta_ms": None,
                "suggested_offset_delta_ms": None,
                "best_candidate_margin_uncalibrated": None,
            }
        )
        editor["occurrences"][0]["lines"][1].update(
            {
                "best_editor_cue_number": None,
                "suggested_onset_delta_ms": None,
                "suggested_offset_delta_ms": None,
                "best_candidate_margin_uncalibrated": None,
            }
        )
        normal = build_alignment_plan(
            run={"issues": []}, timeline_payloads=[self.timeline()], editor_evidence=editor
        )
        self.assertEqual(normal["summary"]["job_count"], 0)
        opted_in = build_alignment_plan(
            run={"issues": []},
            timeline_payloads=[self.timeline()],
            editor_evidence=editor,
            config=AlignmentPlannerConfig(include_editor_missing=True),
        )
        self.assertEqual(opted_in["summary"]["job_count"], 2)
        self.assertTrue(
            all("editor_no_candidate" in job["reasons"] for job in opted_in["jobs"])
        )

    def test_job_limit_is_explicit_not_silent(self):
        timeline = self.timeline()
        editor = self.editor()
        plan = build_alignment_plan(
            run={"issues": []},
            timeline_payloads=[timeline],
            editor_evidence=editor,
            config=AlignmentPlannerConfig(max_jobs=1),
        )
        self.assertEqual(plan["summary"]["job_count"], 1)
        self.assertTrue(plan["summary"]["plan_truncated"])

    def test_editor_text_identity_mismatch_blocks(self):
        editor = self.editor()
        editor["occurrences"][0]["lines"][0]["canonical_text_sha256"] = "b" * 64
        with self.assertRaisesRegex(
            AlignmentPlanningError, "text identity mismatch"
        ):
            build_alignment_plan(
                run={"issues": []},
                timeline_payloads=[self.timeline()],
                editor_evidence=editor,
            )

    def test_external_command_registry_is_truthful(self):
        statuses = inspect_backends(external_forced_aligner_command=sys.executable)
        external = next(
            status for status in statuses if status.backend_id == "external_forced_aligner"
        )
        self.assertTrue(external.available)
        self.assertTrue(external.execution_ready)
        self.assertTrue(
            capability_available(
                statuses,
                BackendCapability.SOURCE_FORCED_ALIGNMENT,
                require_execution_ready=True,
            )
        )

        unconfigured = inspect_backends()
        external_missing = next(
            status
            for status in unconfigured
            if status.backend_id == "external_forced_aligner"
        )
        self.assertFalse(external_missing.available)
        self.assertFalse(external_missing.execution_ready)
        self.assertIn("external_command", external_missing.missing_execution_requirements)


if __name__ == "__main__":
    unittest.main()
