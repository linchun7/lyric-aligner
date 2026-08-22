"""Synthetic regressions for Smart report-only diagnostics."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from lyric_aligner.text_repair import MatchDecision, SubtitleCue, _normalize_for_match
from lyric_aligner.timeline.anchor_repair import SongTimingModel, TimingDecision
from lyric_aligner.timeline.smart_policy import (
    _bpm_prior_compatibility,
    _model_payload,
    _review_reason_counts,
    _text_materialization_counts,
    _text_review_mapping_counts,
    _timing_review_proposal_counts,
)
from lyric_aligner.timeline.smart_policy_v126 import add_timing_product_semantics
from lyric_aligner.timeline.smart_policy_v128 import add_timing_review_product_semantics


def _cue(index: int, text: str) -> SubtitleCue:
    return SubtitleCue(
        ordinal=index,
        number=str(index + 1),
        timing=f"00:00:0{index},000 --> 00:00:0{index + 1},000",
        text=text,
        normalized=_normalize_for_match(text),
        raw_block_index=index * 2,
    )


def _text_decision(
    index: int,
    *,
    action: str,
    reason: str,
    canonical_span: tuple[int, int] | None,
) -> MatchDecision:
    return MatchDecision(
        cue_ordinal=index,
        canonical_ordinal=canonical_span[0] if canonical_span else None,
        score=0.5,
        action=action,
        reason=reason,
        cue_span=(index, index + 1),
        canonical_span=canonical_span,
        source_text="source",
        canonical_text="canonical",
        output_text="source" if action == "review" else "canonical",
        edit_operations=(),
    )


class SmartReportSemanticsV122Tests(unittest.TestCase):
    def test_bpm_compatibility_ignores_placeholder_rate_without_evidence(self) -> None:
        models = [
            SimpleNamespace(
                source_ordinal=0,
                rate=1.0,
                rate_source="none",
            )
        ]
        metadata = {
            0: {
                "value": 0.94,
                "provenance": "bpm_derived",
            }
        }

        self.assertEqual(_bpm_prior_compatibility(models, metadata), {})

    def test_bpm_compatibility_uses_anchor_estimated_rate(self) -> None:
        models = [
            SimpleNamespace(
                source_ordinal=0,
                rate=0.95,
                rate_source="robust_anchor_estimate",
            )
        ]
        metadata = {
            0: {
                "value": 0.94,
                "provenance": "bpm_derived",
            }
        }

        result = _bpm_prior_compatibility(models, metadata)
        self.assertIn(0, result)
        self.assertTrue(result[0][1])

    def test_materialized_and_semantic_text_change_counts_are_distinct(self) -> None:
        cues = [_cue(0, "Hello World"), _cue(1, "原文")]
        repaired = (
            "1\n00:00:00,000 --> 00:00:01,000\nhello world\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\n新文\n"
        )

        exact, semantic = _text_materialization_counts(cues, repaired)
        self.assertEqual(exact, 2)
        self.assertEqual(semantic, 1)

    def test_text_review_mapping_and_reason_counts_are_explicit(self) -> None:
        decisions = [
            _text_decision(
                0,
                action="review",
                reason="low_or_structurally_unsafe_similarity",
                canonical_span=(0, 1),
            ),
            _text_decision(
                1,
                action="review",
                reason="unmatched_subtitle_cue",
                canonical_span=None,
            ),
            _text_decision(
                2,
                action="review",
                reason="unmatched_subtitle_cue",
                canonical_span=(2, 2),
            ),
            _text_decision(
                3,
                action="unchanged",
                reason="canonical_content_matches_source_segmentation",
                canonical_span=(3, 4),
            ),
        ]

        self.assertEqual(_text_review_mapping_counts(decisions), (1, 2))
        self.assertEqual(
            _review_reason_counts(decisions),
            {
                "low_or_structurally_unsafe_similarity": 1,
                "unmatched_subtitle_cue": 2,
            },
        )

    def test_timing_model_ready_is_reported_as_prediction_readiness_only(self) -> None:
        model = SongTimingModel(
            source_ordinal=0,
            source="song.lrc",
            rate=1.0,
            offset_ms=0.0,
            rate_source="robust_anchor_estimate",
            anchor_count=4,
            inlier_count=4,
            median_abs_residual_ms=10.0,
            inlier_fraction=1.0,
            status="ready",
            word_timing_anchor_count=0,
        )

        payload = _model_payload([model], {}, {})[0]
        self.assertTrue(payload["prediction_ready"])
        self.assertEqual(
            payload["status_semantics"],
            "prediction_readiness_not_auto_repair_authority",
        )
        self.assertEqual(payload["status"], "ready")

    def test_timing_reviews_distinguish_concrete_proposal_from_no_proposal(self) -> None:
        base = dict(
            source_ordinal=0,
            canonical_ordinal=0,
            anchor_grade="A",
            action="review",
            old_start_ms=0,
            old_end_ms=1000,
            residual_ms=None,
            model_status="ready",
            evidence=(),
        )
        decisions = [
            TimingDecision(
                cue_ordinal=0,
                reason="bpm_prior_conflict",
                proposed_start_ms=100,
                proposed_end_ms=1100,
                **base,
            ),
            TimingDecision(
                cue_ordinal=1,
                reason="unresolved_timing_model_not_ready",
                proposed_start_ms=None,
                proposed_end_ms=None,
                **base,
            ),
        ]

        self.assertEqual(_timing_review_proposal_counts(decisions), (1, 1))
        self.assertEqual(
            _review_reason_counts(decisions),
            {
                "bpm_prior_conflict": 1,
                "unresolved_timing_model_not_ready": 1,
            },
        )

    def test_product_timing_semantics_do_not_call_all_unvalidated_cues_review(self) -> None:
        report = {
            "timing_validated_preserve_count": 5,
            "timing_repair_count": 1,
            "timing_review_count": 9,
            "timing_review_with_proposal_count": 2,
            "timing_review_without_proposal_count": 7,
            "timing_decisions": [
                {
                    "action": "review",
                    "old_start_ms": 10_000,
                    "proposed_start_ms": 8_800,
                },
                {
                    "action": "review",
                    "old_start_ms": 20_000,
                    "proposed_start_ms": 19_600,
                },
            ],
        }

        add_timing_product_semantics(report)

        self.assertEqual(report["timing_validated_count"], 6)
        self.assertEqual(report["timing_suspected_count"], 2)
        self.assertEqual(report["timing_unvalidated_count"], 7)
        self.assertEqual(report["timing_suspected_actionable_count"], 1)
        self.assertEqual(report["timing_suspected_within_display_tolerance_count"], 1)
        self.assertEqual(report["manual_timing_review_candidate_count"], 1)
        self.assertEqual(report["timing_product_state"], "suspected_and_unvalidated")
        self.assertEqual(
            report["timing_review_count_semantics"],
            "legacy_unresolved_total_not_manual_review_queue",
        )

    def test_actionable_hypotheses_are_ranked_by_model_and_text_evidence(self) -> None:
        report = {
            "models": [
                {
                    "source_ordinal": 0,
                    "status": "ready",
                    "inlier_count": 8,
                    "inlier_fraction": 1.0,
                    "median_abs_residual_ms": 40.0,
                },
                {
                    "source_ordinal": 1,
                    "status": "ready",
                    "inlier_count": 4,
                    "inlier_fraction": 0.8,
                    "median_abs_residual_ms": 200.0,
                },
            ],
            "text_decisions": [
                {"cue_ordinal": 0, "action": "review"},
                {"cue_ordinal": 1, "action": "unchanged"},
                {"cue_ordinal": 2, "action": "review"},
                {"cue_ordinal": 3, "action": "replace", "reason": "preceding_canonical_anchor_confirms_cross_script_vocalization"},
            ],
            "timing_decisions": [
                {"cue_ordinal": 0, "source_ordinal": 0, "action": "review", "old_start_ms": 10_000, "proposed_start_ms": 8_800},
                {"cue_ordinal": 1, "source_ordinal": 0, "action": "review", "old_start_ms": 20_000, "proposed_start_ms": 18_800},
                {"cue_ordinal": 2, "source_ordinal": 1, "action": "review", "old_start_ms": 30_000, "proposed_start_ms": 28_800},
                {"cue_ordinal": 3, "source_ordinal": 0, "action": "review", "old_start_ms": 40_000, "proposed_start_ms": 38_800},
            ],
        }

        report["text_review_count"] = 0
        report["timing_unvalidated_count"] = 0
        report["timing_suspected_actionable_count"] = 4
        add_timing_review_product_semantics(report)

        self.assertEqual(report["timing_actionable_strong_model_count"], 3)
        self.assertEqual(report["timing_actionable_weak_or_unknown_model_count"], 1)
        self.assertEqual(report["timing_actionable_text_unresolved_count"], 2)
        self.assertEqual(report["timing_actionable_resolved_text_count"], 2)
        self.assertEqual(report["timing_actionable_text_identity_special_count"], 3)
        self.assertEqual(report["timing_high_value_pro_candidate_count"], 2)
        self.assertEqual(report["manual_timing_review_candidate_count"], 4)
        self.assertEqual(report["product_status"], "review_required")
        self.assertTrue(report["manual_review_required"])
        self.assertEqual(
            report["timing_high_value_pro_candidate_positions"][0]["cue_ordinal"],
            0,
        )

    def test_actionable_timing_cannot_be_hidden_by_empty_high_value_subset(self) -> None:
        report = {
            "models": [
                {
                    "source_ordinal": 0,
                    "status": "ready",
                    "inlier_count": 8,
                    "inlier_fraction": 1.0,
                    "median_abs_residual_ms": 40.0,
                }
            ],
            "text_decisions": [{"cue_ordinal": 0, "action": "unchanged"}],
            "timing_decisions": [
                {
                    "cue_ordinal": 0,
                    "source_ordinal": 0,
                    "action": "review",
                    "old_start_ms": 10_000,
                    "proposed_start_ms": 8_800,
                }
            ],
            "text_review_count": 0,
            "timing_unvalidated_count": 0,
            "timing_suspected_actionable_count": 1,
        }

        add_timing_review_product_semantics(report)

        self.assertEqual(report["timing_high_value_pro_candidate_count"], 0)
        self.assertEqual(report["manual_timing_review_candidate_count"], 1)
        self.assertEqual(report["product_status"], "review_required")


if __name__ == "__main__":
    unittest.main()
