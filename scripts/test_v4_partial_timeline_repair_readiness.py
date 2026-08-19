from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from lyric_aligner.timeline.partial_repair import PartialTimelineRepairError
from lyric_aligner.timeline.partial_repair_context import (
    EffectiveRunMappingContext,
    OccurrenceMappingContext,
)
from lyric_aligner.timeline.partial_repair_readiness import (
    inspect_partial_timeline_repair_readiness,
)


def fake_context() -> EffectiveRunMappingContext:
    return EffectiveRunMappingContext(
        schema_version="1.0",
        task_fingerprint_sha256="f" * 64,
        algorithm_version="4.0.0a8",
        run_stage="review_resolution",
        run_artifact_id="a" * 64,
        occurrences=(
            OccurrenceMappingContext(
                occurrence_id="occ-1",
                status="ready",
                mapping_kind="AFFINE",
                mapping_source="coarse",
                source_stage="coarse_audio_alignment",
                source_artifact_id="1" * 64,
                confirmed_cut=False,
                cut_count=0,
                reason="ok",
            ),
            OccurrenceMappingContext(
                occurrence_id="occ-2",
                status="ready",
                mapping_kind="CUT_AWARE",
                mapping_source="cut_aware_rebuild",
                source_stage="cut_timewarp_rebuild",
                source_artifact_id="2" * 64,
                confirmed_cut=True,
                cut_count=1,
                reason="ok",
            ),
            OccurrenceMappingContext(
                occurrence_id="occ-3",
                status="unavailable",
                mapping_kind=None,
                mapping_source="coarse",
                source_stage=None,
                source_artifact_id=None,
                confirmed_cut=False,
                cut_count=0,
                reason="blocked",
            ),
        ),
    )


def fake_fusion() -> dict:
    return {
        "lines": [
            {"language_profile": "zh", "shadow_level": "HIGH"},
            {"language_profile": "ko", "shadow_level": "CONFLICT"},
            {"language_profile": "zh", "shadow_level": "MEDIUM"},
        ]
    }


def fake_lock(*, actionable: bool = True) -> dict:
    return {
        "trust_policy_lock_sha256": "b" * 64,
        "eligible_language_scopes": ["language:zh"] if actionable else [],
        "cue_trust_generation_allowed": actionable,
        "policy_calibrated": True,
        "independent_blind_gate_passed": True,
        "automatic_timing_change_allowed": False,
        "release_gate_eligible": False,
    }


def call_paths() -> dict:
    return {
        "run_path": Path("run.json"),
        "run_artifact_path": Path("run.artifact.json"),
        "fusion_path": Path("fusion.json"),
        "fusion_artifact_path": Path("fusion.artifact.json"),
    }


class PartialTimelineRepairReadinessTests(unittest.TestCase):
    def test_no_inputs_is_not_requested_and_never_ready(self):
        report = inspect_partial_timeline_repair_readiness(
            run_path=None,
            run_artifact_path=None,
            fusion_path=None,
            fusion_artifact_path=None,
        )
        self.assertEqual(report["status"], "not_requested")
        self.assertFalse(report["lineage"]["valid"])
        self.assertFalse(report["automatic_timing_change_allowed"])
        self.assertFalse(report["release_gate_eligible"])

    def test_incomplete_run_fusion_pair_fails_closed(self):
        report = inspect_partial_timeline_repair_readiness(
            run_path=Path("run.json"),
            run_artifact_path=None,
            fusion_path=Path("fusion.json"),
            fusion_artifact_path=Path("fusion.artifact.json"),
        )
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(
            report["lineage"]["detail"],
            "effective_run_and_fusion_pairs_incomplete",
        )

    def test_valid_lineage_without_lock_requires_human_review_or_calibration(self):
        with patch(
            "lyric_aligner.timeline.partial_repair_readiness."
            "inspect_partial_repair_artifacts",
            return_value=(fake_context(), fake_fusion(), {"artifact_id": "c" * 64}),
        ):
            report = inspect_partial_timeline_repair_readiness(**call_paths())
        self.assertEqual(report["status"], "human_review_or_calibration_required")
        self.assertTrue(report["lineage"]["valid"])
        self.assertEqual(report["lineage"]["fusion_conflict_count"], 1)
        self.assertEqual(
            report["lineage"]["fusion_language_scopes"],
            ["language:ko", "language:zh"],
        )
        mapping = report["lineage"]["mapping"]
        self.assertEqual(mapping["mapping_kind_counts"]["AFFINE"], 1)
        self.assertEqual(mapping["mapping_kind_counts"]["CUT_AWARE"], 1)
        self.assertEqual(mapping["unavailable_occurrence_count"], 1)
        self.assertEqual(mapping["confirmed_cut_occurrence_count"], 1)
        self.assertEqual(
            report["recommended_next_action"]["action"],
            "human_review_or_build_private_trust_lock",
        )

    def test_actionable_lock_without_decisions_reports_next_missing_stage(self):
        with patch(
            "lyric_aligner.timeline.partial_repair_readiness."
            "inspect_partial_repair_artifacts",
            return_value=(fake_context(), fake_fusion(), {"artifact_id": "c" * 64}),
        ), patch(
            "lyric_aligner.timeline.partial_repair_readiness."
            "load_calibrated_trust_policy_lock",
            return_value=fake_lock(actionable=True),
        ):
            report = inspect_partial_timeline_repair_readiness(
                **call_paths(), trust_lock_path=Path("trust.lock.json")
            )
        self.assertEqual(report["status"], "calibrated_decisions_required")
        self.assertTrue(report["trust_lock"]["valid"])
        self.assertTrue(report["trust_lock"]["actionable"])
        self.assertEqual(
            report["trust_lock"]["eligible_language_scopes"],
            ["language:zh"],
        )

    def test_valid_but_non_actionable_lock_requires_human_review(self):
        with patch(
            "lyric_aligner.timeline.partial_repair_readiness."
            "inspect_partial_repair_artifacts",
            return_value=(fake_context(), fake_fusion(), {"artifact_id": "c" * 64}),
        ), patch(
            "lyric_aligner.timeline.partial_repair_readiness."
            "load_calibrated_trust_policy_lock",
            return_value=fake_lock(actionable=False),
        ):
            report = inspect_partial_timeline_repair_readiness(
                **call_paths(), trust_lock_path=Path("trust.lock.json")
            )
        self.assertEqual(report["status"], "human_review_required")
        self.assertFalse(report["trust_lock"]["actionable"])
        self.assertEqual(
            report["recommended_next_action"]["action"],
            "human_review_or_expand_blind_language_gates",
        )

    def test_verified_decision_artifact_reaches_proposal_inputs_ready_only(self):
        decision_counts = {
            "trusted": 8,
            "untrusted": 2,
            "unknown": 1,
            "uncovered_scope": 0,
            "conflict_downgraded": 1,
            "ambiguous_binding": 0,
        }
        with patch(
            "lyric_aligner.timeline.partial_repair_readiness."
            "inspect_partial_repair_artifacts",
            return_value=(fake_context(), fake_fusion(), {"artifact_id": "c" * 64}),
        ), patch(
            "lyric_aligner.timeline.partial_repair_readiness."
            "load_calibrated_trust_policy_lock",
            return_value=fake_lock(actionable=True),
        ), patch(
            "lyric_aligner.timeline.partial_repair_readiness."
            "validate_calibrated_trust_decision_artifact",
            return_value={"artifact_id": "d" * 64},
        ), patch(
            "lyric_aligner.timeline.partial_repair_readiness."
            "calibrated_decisions_to_explicit_trust",
            return_value=(
                [],
                {"decision_count": 11, "counts": decision_counts},
            ),
        ):
            report = inspect_partial_timeline_repair_readiness(
                **call_paths(),
                trust_lock_path=Path("trust.lock.json"),
                decision_path=Path("decisions.json"),
                decision_artifact_path=Path("decisions.artifact.json"),
            )
        self.assertEqual(report["status"], "proposal_inputs_ready")
        self.assertTrue(report["decisions"]["valid"])
        self.assertEqual(report["decisions"]["decision_count"], 11)
        self.assertEqual(report["decisions"]["counts"], decision_counts)
        self.assertFalse(report["automatic_timing_change_allowed"])
        self.assertFalse(report["publish_ready"])
        self.assertEqual(
            report["recommended_next_action"]["action"],
            "build_partial_repair_proposal",
        )

    def test_decision_payload_without_artifact_is_blocked(self):
        with patch(
            "lyric_aligner.timeline.partial_repair_readiness."
            "inspect_partial_repair_artifacts",
            return_value=(fake_context(), fake_fusion(), {"artifact_id": "c" * 64}),
        ), patch(
            "lyric_aligner.timeline.partial_repair_readiness."
            "load_calibrated_trust_policy_lock",
            return_value=fake_lock(actionable=True),
        ):
            report = inspect_partial_timeline_repair_readiness(
                **call_paths(),
                trust_lock_path=Path("trust.lock.json"),
                decision_path=Path("decisions.json"),
            )
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(
            report["decisions"]["detail"],
            "decision_payload_artifact_pair_incomplete",
        )

    def test_error_details_redact_posix_and_windows_absolute_paths(self):
        leaks = (
            "cannot read /Users/chun/private/run.json",
            r"cannot read C:\Users\Chun\private\run.json",
        )
        for leak in leaks:
            with self.subTest(leak=leak), patch(
                "lyric_aligner.timeline.partial_repair_readiness."
                "inspect_partial_repair_artifacts",
                side_effect=PartialTimelineRepairError(leak),
            ):
                report = inspect_partial_timeline_repair_readiness(**call_paths())
            detail = report["lineage"]["detail"]
            self.assertIn("<local_path>", detail)
            self.assertNotIn("/Users/chun", detail)
            self.assertNotIn(r"C:\Users\Chun", detail)


if __name__ == "__main__":
    unittest.main()
