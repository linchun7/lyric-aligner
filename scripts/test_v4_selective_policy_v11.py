from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from lyric_aligner.alignment.local_acoustic_v11 import (
    _slope_search_metadata,
    execute_region_source_match_jobs,
)
from lyric_aligner.alignment.selective_policy import build_selective_repair_plan_v11
from lyric_aligner.alignment.selective_repair import (
    SelectiveRepairConfig,
    SelectiveRepairPlanningError,
)
from lyric_aligner.text_repair import SubtitleCue
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.smart_current import SMART_POLICY_ID, SMART_SCHEMA_VERSION


def _cue(ordinal: int, start_ms: int, end_ms: int, text: str) -> SubtitleCue:
    def clock(ms: int) -> str:
        hour, rem = divmod(ms, 3_600_000)
        minute, rem = divmod(rem, 60_000)
        second, millis = divmod(rem, 1000)
        return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"

    return SubtitleCue(
        ordinal=ordinal,
        number=str(ordinal + 1),
        timing=f"{clock(start_ms)} --> {clock(end_ms)}",
        text=text,
        normalized=text,
        raw_block_index=ordinal * 2,
    )


def _canonical(ordinal: int, source_ordinal: int, time_ms: int, text: str):
    return TimedCanonicalOccurrence(
        ordinal=ordinal,
        source=f"{source_ordinal + 1:02d}.lrc",
        source_ordinal=source_ordinal,
        time_ms=time_ms,
        text=text,
        normalized=text,
    )


class SelectivePolicyV11Tests(unittest.TestCase):
    def _smart_report(self):
        return {
            "schema_version": SMART_SCHEMA_VERSION,
            "policy_id": SMART_POLICY_ID,
            "mode": "smart_anchor_timeline_repair_no_audio",
            "audio_read": False,
            "models": [
                {"source_ordinal": 0, "source": "01.lrc", "rate": 1.0, "status": "ready"},
                {"source_ordinal": 1, "source": "02.lrc", "rate": 1.0, "status": "ready"},
            ],
            "timing_decisions": [
                {
                    "cue_ordinal": 0,
                    "canonical_ordinal": 2,
                    "action": "review",
                    "reason": "unresolved_timing_model_not_ready",
                },
                {
                    "cue_ordinal": 1,
                    "canonical_ordinal": 3,
                    "action": "preserve",
                    "reason": "timing_matches_anchor_model",
                },
                {
                    "cue_ordinal": 2,
                    "canonical_ordinal": 4,
                    "action": "preserve",
                    "reason": "timing_matches_anchor_model",
                },
            ],
            "text_decisions": [
                {"cue_ordinal": 0, "canonical_ordinal": 2, "action": "unchanged"},
                {
                    "cue_ordinal": 1,
                    "canonical_ordinal": 3,
                    "action": "review",
                    "reason": "ambiguous_nearby_match",
                },
                {"cue_ordinal": 2, "canonical_ordinal": 4, "action": "unchanged"},
            ],
        }

    def test_pro_accepts_current_v126_and_rejects_v124_policy(self) -> None:
        cues = [_cue(0, 10_000, 11_000, "测试行")]
        canonical = [_canonical(0, 0, 10_000, "测试行")]
        report = self._smart_report()
        report["timing_decisions"] = [
            {
                "cue_ordinal": 0,
                "canonical_ordinal": 0,
                "action": "review",
                "reason": "unresolved_timing_model_not_ready",
            }
        ]
        report["text_decisions"] = [
            {"cue_ordinal": 0, "canonical_ordinal": 0, "action": "unchanged"}
        ]
        report["models"] = [
            {"source_ordinal": 0, "source": "01.lrc", "rate": 1.0, "status": "ready"}
        ]

        plan = build_selective_repair_plan_v11(
            smart_report=report,
            cues=cues,
            canonical=canonical,
        )
        self.assertEqual(plan["schema_version"], "1.1")

        stale_report = dict(report)
        stale_report["policy_id"] = "smart-validation-policy-2026-08-21-v1.2.4"
        with self.assertRaisesRegex(
            SelectiveRepairPlanningError,
            "current Smart production policy",
        ):
            build_selective_repair_plan_v11(
                smart_report=stale_report,
                cues=cues,
                canonical=canonical,
            )

    def test_reason_aware_routes_regions_and_boundary_competitor(self) -> None:
        cues = [
            _cue(0, 10_000, 11_000, "尾句"),
            _cue(1, 11_200, 12_200, "Next line"),
            _cue(2, 20_000, 21_000, "下一句"),
        ]
        canonical = [
            _canonical(0, 0, 1_000, "甲"),
            _canonical(1, 0, 5_000, "乙"),
            _canonical(2, 0, 9_000, "尾句"),
            _canonical(3, 1, 1_000, "Next line"),
            _canonical(4, 1, 5_000, "下一句"),
            _canonical(5, 1, 9_000, "再下一句"),
        ]
        plan = build_selective_repair_plan_v11(
            smart_report=self._smart_report(),
            cues=cues,
            canonical=canonical,
            language_by_source={0: "zh", 1: "en"},
        )

        primary = [job for job in plan["jobs"] if not job.get("shadow_evidence_only")]
        primary_by_cue = {int(job["cue_ordinal"]): job for job in primary}
        competitor = [job for job in plan["jobs"] if job.get("shadow_evidence_only")]
        self.assertEqual(len(primary), 2)
        self.assertEqual(len(competitor), 1)
        # Reason-aware value ranking may reorder primary jobs; route identity is
        # attached to the cue, not to a legacy list position.
        self.assertEqual(
            primary_by_cue[0]["requested_capabilities"],
            ["source_local_acoustic_match"],
        )
        self.assertIn("mix_asr", primary_by_cue[1]["requested_capabilities"])
        self.assertIn("source_forced_alignment", primary_by_cue[1]["requested_capabilities"])
        self.assertEqual(competitor[0]["source_ordinal"], 1)
        self.assertEqual(competitor[0]["boundary_role"], "next_source")
        self.assertEqual(plan["summary"]["acoustic_region_count"], 1)
        self.assertEqual(plan["summary"]["region_count"], 2)
        self.assertLess(
            plan["summary"]["planned_acoustic_mix_audio_ms_merged"],
            plan["summary"]["planned_acoustic_mix_audio_ms_unmerged"],
        )

    def test_max_jobs_is_applied_after_reason_aware_value_ranking(self) -> None:
        cues = [
            _cue(0, 0, 900, "甲"),
            _cue(1, 1_000, 1_900, "乙"),
            _cue(2, 2_000, 2_900, "丙"),
            _cue(3, 3_000, 3_900, "丁"),
        ]
        canonical = [
            _canonical(0, 0, 0, "甲"),
            _canonical(1, 0, 1_000, "乙"),
            _canonical(2, 0, 2_000, "丙"),
            _canonical(3, 0, 3_000, "丁"),
        ]
        report = {
            "schema_version": SMART_SCHEMA_VERSION,
            "policy_id": SMART_POLICY_ID,
            "mode": "smart_anchor_timeline_repair_no_audio",
            "audio_read": False,
            "models": [
                {"source_ordinal": 0, "source": "01.lrc", "rate": 1.0, "status": "ready"}
            ],
            "timing_decisions": [
                {"cue_ordinal": 0, "canonical_ordinal": 0, "action": "review", "reason": "unresolved_timing_model_not_ready", "proposed_start_ms": None, "proposed_end_ms": None},
                {"cue_ordinal": 1, "canonical_ordinal": 1, "action": "review", "reason": "unresolved_timing_model_not_ready", "proposed_start_ms": None, "proposed_end_ms": None},
                {"cue_ordinal": 2, "canonical_ordinal": 2, "action": "preserve", "reason": "timing_matches_anchor_model", "proposed_start_ms": None, "proposed_end_ms": None},
                {"cue_ordinal": 3, "canonical_ordinal": 3, "action": "review", "reason": "bpm_prior_conflict", "proposed_start_ms": 4_500, "proposed_end_ms": 5_400},
            ],
            "text_decisions": [
                {"cue_ordinal": 0, "canonical_ordinal": 0, "action": "unchanged"},
                {"cue_ordinal": 1, "canonical_ordinal": 1, "action": "unchanged"},
                {"cue_ordinal": 2, "canonical_ordinal": 2, "action": "review", "reason": "low_or_structurally_unsafe_similarity"},
                {"cue_ordinal": 3, "canonical_ordinal": 3, "action": "unchanged"},
            ],
        }

        plan = build_selective_repair_plan_v11(
            smart_report=report,
            cues=cues,
            canonical=canonical,
            config=SelectiveRepairConfig(max_jobs=2),
        )

        primary = [job for job in plan["jobs"] if not job.get("shadow_evidence_only")]
        self.assertEqual([job["cue_ordinal"] for job in primary], [3, 2])
        self.assertEqual(
            [job["selection_tier"] for job in primary],
            ["actionable_timing_suspicion", "text_review"],
        )
        self.assertEqual(primary[0]["timing_proposal_abs_shift_ms"], 1_500)
        # A large concrete Smart hypothesis is tested before text-only work;
        # small display-tolerance candidates and unvalidated timing are lower.
        self.assertEqual([job["priority"] for job in primary], ["high", "medium"])
        self.assertEqual(
            plan["summary"]["selection_policy"],
            "smart_high_value_then_actionable_text_model_shift_then_display_tolerance_then_unvalidated",
        )
        self.assertEqual(plan["summary"]["primary_candidate_job_count"], 4)
        self.assertEqual(plan["summary"]["primary_deferred_due_to_max_jobs"], 2)
        self.assertTrue(plan["summary"]["plan_truncated"])

    def test_smart_high_value_subset_wins_a_limited_pro_budget(self) -> None:
        cues = [
            _cue(0, 10_000, 11_000, "甲"),
            _cue(1, 20_000, 21_000, "乙"),
        ]
        canonical = [
            _canonical(0, 0, 1_000, "甲"),
            _canonical(1, 0, 2_000, "乙"),
        ]
        report = {
            "schema_version": SMART_SCHEMA_VERSION,
            "policy_id": SMART_POLICY_ID,
            "mode": "smart_anchor_timeline_repair_no_audio",
            "audio_read": False,
            "models": [
                {
                    "source_ordinal": 0,
                    "source": "01.lrc",
                    "rate": 1.0,
                    "status": "ready",
                    "inlier_count": 8,
                    "inlier_fraction": 1.0,
                    "median_abs_residual_ms": 40.0,
                }
            ],
            "timing_decisions": [
                {
                    "cue_ordinal": 0,
                    "canonical_ordinal": 0,
                    "action": "review",
                    "reason": "synthetic",
                    "old_start_ms": 10_000,
                    "proposed_start_ms": 5_000,
                    "proposed_end_ms": 6_000,
                },
                {
                    "cue_ordinal": 1,
                    "canonical_ordinal": 1,
                    "action": "review",
                    "reason": "synthetic",
                    "old_start_ms": 20_000,
                    "proposed_start_ms": 18_500,
                    "proposed_end_ms": 19_500,
                },
            ],
            "text_decisions": [
                {
                    "cue_ordinal": 0,
                    "canonical_ordinal": 0,
                    "action": "review",
                    "reason": "synthetic",
                },
                {
                    "cue_ordinal": 1,
                    "canonical_ordinal": 1,
                    "action": "unchanged",
                },
            ],
            "timing_high_value_pro_candidate_count": 1,
            "timing_high_value_pro_candidate_positions": [
                {"cue_ordinal": 1, "editor_cue_start_ms": 20_000, "smart_shift_abs_ms": 1_500}
            ],
        }

        plan = build_selective_repair_plan_v11(
            smart_report=report,
            cues=cues,
            canonical=canonical,
            config=SelectiveRepairConfig(max_jobs=1),
        )

        primary = [job for job in plan["jobs"] if not job.get("shadow_evidence_only")]
        self.assertEqual([job["cue_ordinal"] for job in primary], [1])
        self.assertTrue(primary[0]["smart_timing_high_value_pro_candidate"])
        self.assertEqual(plan["summary"]["smart_high_value_candidate_count"], 1)
        self.assertEqual(plan["summary"]["smart_high_value_selected_count"], 1)

    def test_strong_local_model_precedes_larger_weak_model_shift(self) -> None:
        cues = [_cue(0, 10_000, 11_000, "甲"), _cue(1, 20_000, 21_000, "乙")]
        canonical = [
            _canonical(0, 0, 1_000, "甲"),
            _canonical(1, 1, 1_000, "乙"),
        ]
        report = {
            "schema_version": SMART_SCHEMA_VERSION,
            "policy_id": SMART_POLICY_ID,
            "mode": "smart_anchor_timeline_repair_no_audio",
            "audio_read": False,
            "models": [
                {"source_ordinal": 0, "source": "01.lrc", "rate": 1.0, "status": "ready", "inlier_count": 8, "inlier_fraction": 1.0, "median_abs_residual_ms": 40.0},
                {"source_ordinal": 1, "source": "02.lrc", "rate": 1.0, "status": "ready", "inlier_count": 4, "inlier_fraction": 0.8, "median_abs_residual_ms": 180.0},
            ],
            "timing_decisions": [
                {"cue_ordinal": 0, "canonical_ordinal": 0, "source_ordinal": 0, "action": "review", "reason": "synthetic", "old_start_ms": 10_000, "proposed_start_ms": 8_800, "proposed_end_ms": 9_800},
                {"cue_ordinal": 1, "canonical_ordinal": 1, "source_ordinal": 1, "action": "review", "reason": "synthetic", "old_start_ms": 20_000, "proposed_start_ms": 15_000, "proposed_end_ms": 16_000},
            ],
            "text_decisions": [
                {"cue_ordinal": 0, "canonical_ordinal": 0, "action": "review", "reason": "synthetic"},
                {"cue_ordinal": 1, "canonical_ordinal": 1, "action": "review", "reason": "synthetic"},
            ],
        }

        plan = build_selective_repair_plan_v11(
            smart_report=report,
            cues=cues,
            canonical=canonical,
            config=SelectiveRepairConfig(max_jobs=1),
        )

        primary = [job for job in plan["jobs"] if not job.get("shadow_evidence_only")]
        self.assertEqual(len(primary), 1)
        self.assertEqual(primary[0]["cue_ordinal"], 0)
        self.assertEqual(primary[0]["timing_model_evidence_tier"], "strong")

    def test_acoustic_executor_extracts_mix_features_once_per_region(self) -> None:
        cues = [
            _cue(0, 10_000, 11_000, "尾句"),
            _cue(1, 11_200, 12_200, "Next line"),
            _cue(2, 20_000, 21_000, "下一句"),
        ]
        canonical = [
            _canonical(0, 0, 1_000, "甲"),
            _canonical(1, 0, 5_000, "乙"),
            _canonical(2, 0, 9_000, "尾句"),
            _canonical(3, 1, 1_000, "Next line"),
            _canonical(4, 1, 5_000, "下一句"),
            _canonical(5, 1, 9_000, "再下一句"),
        ]
        plan = build_selective_repair_plan_v11(
            smart_report=self._smart_report(),
            cues=cues,
            canonical=canonical,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mix = root / "mix.wav"
            source0 = root / "source0.wav"
            source1 = root / "source1.wav"
            for path in (mix, source0, source1):
                path.write_bytes(b"fake")

            calls: list[Path] = []

            def loader(path, *, sr, start_ms, end_ms):
                calls.append(Path(path))
                return np.zeros(max(sr * 5, int((end_ms - start_ms) * sr / 1000)), dtype=np.float32)

            features = SimpleNamespace(duration_seconds=20.0)
            best = SimpleNamespace(
                source_start=1.0,
                estimated_slope=1.0,
                fused_score=0.90,
                chroma_score=0.92,
                mfcc_score=0.82,
                feature_agreement=2,
            )
            retrieval = SimpleNamespace(top1=best, margin=0.08, ambiguous=False)
            with patch(
                "lyric_aligner.alignment.local_acoustic_v11.extract_harmonic_features",
                return_value=features,
            ) as extract, patch(
                "lyric_aligner.alignment.local_acoustic_v11.retrieve_coarse_window",
                return_value=retrieval,
            ):
                result = execute_region_source_match_jobs(
                    mix_audio_path=mix,
                    plan=plan,
                    source_audio_by_source_ordinal={0: source0, 1: source1},
                    audio_loader=loader,
                )

        acoustic_jobs = [
            job for job in plan["jobs"]
            if "source_local_acoustic_match" in job["requested_capabilities"]
        ]
        self.assertEqual(result["mix_feature_region_count"], 1)
        self.assertEqual(sum(path == mix for path in calls), 1)
        self.assertEqual(extract.call_count, 1 + len(acoustic_jobs))
        self.assertEqual(result["job_count"], len(acoustic_jobs))
        self.assertEqual(result["schema_version"], "1.3")
        self.assertFalse(result["automatic_timing_change_allowed"])
        self.assertFalse(result["automatic_text_change_allowed"])
        self.assertFalse(result["timing_mutation_performed"])
        for row in result["jobs"]:
            self.assertTrue(row["local_match_gate_passed"])
            self.assertTrue(row["timing_fusion_evidence_eligible"])
            self.assertFalse(row["slope_search_boundary_hit"])
            self.assertLess(row["slope_search_min"], row["estimated_slope"])
            self.assertGreater(row["slope_search_max"], row["estimated_slope"])
            self.assertEqual(row["local_match_status"], "gate_passed_unadjudicated")
            self.assertEqual(
                row["reliability_semantics"],
                "local_retrieval_gate_only_not_timing_authority",
            )
            self.assertEqual(
                row["acoustic_shift_ms"],
                -row["editor_start_residual_ms"],
            )
            self.assertFalse(row["automatic_timing_change_allowed"])
            self.assertFalse(row["automatic_text_change_allowed"])
            self.assertFalse(row["timing_mutation_performed"])

    def test_slope_search_endpoint_is_diagnostic_only(self) -> None:
        minimum, maximum, boundary_hit = _slope_search_metadata(
            [0.94, 0.95, 0.96, 0.97, 0.98, 0.99, 1.0, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06],
            1.06,
            step=0.01,
        )
        self.assertEqual(minimum, 0.94)
        self.assertEqual(maximum, 1.06)
        self.assertTrue(boundary_hit)


if __name__ == "__main__":
    unittest.main()
