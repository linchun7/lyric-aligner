from __future__ import annotations

import unittest
from types import SimpleNamespace

from lyric_aligner.text_repair import MatchDecision, SubtitleCue, _normalize_for_match
from lyric_aligner.timeline.anchor_repair import TimingDecision
from lyric_aligner.timeline.smart_policy import (
    _bpm_prior_compatibility,
    _review_reason_counts,
    _text_materialization_counts,
    _text_review_mapping_counts,
    _timing_review_proposal_counts,
)


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
                action="unchanged",
                reason="canonical_content_matches_source_segmentation",
                canonical_span=(2, 3),
            ),
        ]

        self.assertEqual(_text_review_mapping_counts(decisions), (1, 1))
        self.assertEqual(
            _review_reason_counts(decisions),
            {
                "low_or_structurally_unsafe_similarity": 1,
                "unmatched_subtitle_cue": 1,
            },
        )

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


if __name__ == "__main__":
    unittest.main()
