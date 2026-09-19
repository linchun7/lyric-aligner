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

    def test_release_semantic_anchors_bind_full_source_audio(self):
        plan = build_alignment_plan(
            run={"issues": []}, timeline_payloads=[self.timeline()],
            config=AlignmentPlannerConfig(release_semantic_anchors_per_track=2),
            source_duration_ms_by_occurrence={"occ-1": 180000},
        )
        jobs = [j for j in plan["jobs"] if "release_semantic_anchor" in j["reasons"]]
        self.assertEqual(len(jobs), 2)
        for job in jobs:
            self.assertEqual(job["source_window_ms"], [0, 180000])
            self.assertIn("source_forced_alignment", job["requested_capabilities"])
            self.assertNotIn("mix_asr", job["requested_capabilities"])
            self.assertIsNone(job["mix_window_ms"])
        self.assertNotIn("private line", json.dumps(plan))

    def test_release_semantic_asr_anchors_are_planned_from_direct_measurement(self):
        editor = self.editor()
        for index, row in enumerate(editor["occurrences"][0]["lines"]):
            row["best_candidate_margin_uncalibrated"] = 0.30
            row["candidates"] = [{
                "timing_support_score": 0.90,
                "direct_text_support_score": 0.95,
                "phonetic_support_score": None,
                "editor_start_ms": 5000 + index * 2000,
                "editor_end_ms": 6500 + index * 2000,
            }]
        plan = build_alignment_plan(
            run={"issues": []},
            timeline_payloads=[self.timeline()],
            editor_evidence=editor,
            config=AlignmentPlannerConfig(release_semantic_anchors_per_track=2, max_jobs=20),
            source_duration_ms_by_occurrence={"occ-1": 180000},
        )
        jobs = [j for j in plan["jobs"] if "release_semantic_asr_anchor" in j["reasons"]]
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all("mix_asr" in job["requested_capabilities"] for job in jobs))
        self.assertTrue(all("word_timestamps" in job["requested_capabilities"] for job in jobs))
        self.assertTrue(all("source_forced_alignment" not in job["requested_capabilities"] for job in jobs))

    def test_release_semantic_asr_spreads_after_reliability_filter(self):
        timeline = self.timeline()
        timeline["result"]["lines"].extend([
            {
                "canonical_line_index": 2,
                "text": "private line two",
                "source_start_ms": 14000,
                "source_end_ms": 15500,
                "mix_start_ms": 9000,
                "mix_end_ms": 10500,
            },
            {
                "canonical_line_index": 3,
                "text": "private line three",
                "source_start_ms": 16000,
                "source_end_ms": 17500,
                "mix_start_ms": 11000,
                "mix_end_ms": 12500,
            },
        ])
        editor = {
            "mode": "shadow_only",
            "authority": {"automatic_timing_change_allowed": False},
            "occurrences": [{"occurrence_id": "occ-1", "lines": []}],
        }
        for index, line in enumerate(timeline["result"]["lines"]):
            row = {
                "canonical_line_index": index,
                "canonical_text_sha256": __import__("hashlib").sha256(line["text"].encode()).hexdigest(),
                "best_editor_cue_number": index + 1,
                "suggested_onset_delta_ms": 20,
                "suggested_offset_delta_ms": 20,
                "best_candidate_margin_uncalibrated": 0.30,
                "candidates": [],
            }
            if index in {1, 3}:
                row["candidates"] = [{
                    "timing_support_score": 0.90,
                    "direct_text_support_score": 0.95,
                    "phonetic_support_score": None,
                    "editor_start_ms": int(line["mix_start_ms"]) + 20,
                    "editor_end_ms": int(line["mix_end_ms"]) + 20,
                }]
            editor["occurrences"][0]["lines"].append(row)
        plan = build_alignment_plan(
            run={"issues": []},
            timeline_payloads=[timeline],
            editor_evidence=editor,
            config=AlignmentPlannerConfig(release_semantic_anchors_per_track=2, max_jobs=20),
            source_duration_ms_by_occurrence={"occ-1": 180000},
        )
        jobs = [j for j in plan["jobs"] if "release_semantic_asr_anchor" in j["reasons"]]
        self.assertEqual([j["canonical_line_index"] for j in jobs], [1, 3])

    def test_release_semantic_anchors_require_duration_mapping(self):
        with self.assertRaises(AlignmentPlanningError):
            build_alignment_plan(
                run={"issues": []}, timeline_payloads=[self.timeline()],
                config=AlignmentPlannerConfig(release_semantic_anchors_per_track=2),
            )

    def test_release_semantic_anchors_cannot_be_truncated(self):
        with self.assertRaisesRegex(AlignmentPlanningError, "truncate release semantic anchors"):
            build_alignment_plan(
                run={"issues": [{"occurrence_id": "occ-1", "code": "x", "interval_start": 1, "interval_end": 2}]},
                timeline_payloads=[self.timeline()],
                config=AlignmentPlannerConfig(release_semantic_anchors_per_track=2, max_jobs=1),
                source_duration_ms_by_occurrence={"occ-1": 180000},
            )


if __name__ == "__main__":
    unittest.main()
