from __future__ import annotations

import unittest

from lyric_aligner.text_repair import MatchDecision, SubtitleCue
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence, build_anchor_timing_plan
from lyric_aligner.timeline.bpm_sequence_reconcile import recover_text_reviews_from_bpm_projection


def _clock(ms: int) -> str:
    hour, rem = divmod(ms, 3_600_000)
    minute, rem = divmod(rem, 60_000)
    second, millis = divmod(rem, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def _cue(index: int, text: str, start: int, duration: int = 1500) -> SubtitleCue:
    from lyric_aligner.text_repair import _normalize_for_match

    return SubtitleCue(
        ordinal=index,
        number=str(index + 1),
        timing=f"{_clock(start)} --> {_clock(start + duration)}",
        text=text,
        normalized=_normalize_for_match(text),
        raw_block_index=index * 2,
    )


def _line(index: int, text: str, source_ms: int) -> TimedCanonicalOccurrence:
    from lyric_aligner.text_repair import _normalize_for_match

    return TimedCanonicalOccurrence(
        ordinal=index,
        source="synthetic.lrc",
        source_ordinal=0,
        time_ms=source_ms,
        text=text,
        normalized=_normalize_for_match(text),
    )


def _decision(
    index: int,
    text: str,
    canonical: str,
    *,
    score: float,
    action: str,
    reason: str,
    canonical_ordinal: int | None = None,
) -> MatchDecision:
    ordinal = index if canonical_ordinal is None else canonical_ordinal
    return MatchDecision(
        cue_ordinal=index,
        canonical_ordinal=ordinal,
        score=score,
        action=action,
        reason=reason,
        cue_span=(index, index + 1),
        canonical_span=(ordinal, ordinal + 1),
        source_text=text,
        canonical_text=canonical,
        output_text=canonical if action != "review" else text,
        edit_operations=(),
    )


def _fixture(candidate_text: str = "完全错误的识别"):
    # source_time = 5000 + 1.1 * mix_time
    canonical_texts = [
        "安全锚点甲甲",
        "安全锚点乙乙",
        "应该恢复的规范歌词",
        "安全锚点丙丙",
        "安全锚点丁丁",
    ]
    source_times = [5_000, 16_000, 27_000, 38_000, 49_000]
    mix_times = [0, 10_000, 20_000, 30_000, 40_000]
    canonical = [_line(i, text, source_times[i]) for i, text in enumerate(canonical_texts)]
    cues = [_cue(i, candidate_text if i == 2 else text, mix_times[i]) for i, text in enumerate(canonical_texts)]
    decisions = [
        _decision(
            i,
            cues[i].text,
            canonical_texts[i],
            score=0.10 if i == 2 else 0.90,
            action="review" if i == 2 else "unchanged",
            reason="low_or_structurally_unsafe_similarity" if i == 2 else "canonical_content_matches_source_segmentation",
        )
        for i in range(len(cues))
    ]
    metadata = {
        0: {
            "value": 1.1,
            "provenance": "bpm_derived",
            "source_bpm": 100.0,
            "target_bpm": 110.0,
        }
    }
    return cues, canonical, decisions, metadata


def _timing_payload(decisions: list[MatchDecision]) -> list[dict[str, object]]:
    return [
        {
            "cue_ordinal": item.cue_ordinal,
            "canonical_ordinal": item.canonical_ordinal,
            "cue_span": list(item.cue_span) if item.cue_span else None,
            "canonical_span": list(item.canonical_span) if item.canonical_span else None,
            "score": item.score,
            "action": item.action,
            "reason": item.reason,
        }
        for item in decisions
    ]


class SmartBpmTextProjectionTests(unittest.TestCase):
    def test_validated_bpm_recovers_mapped_review_without_creating_timing_authority(self) -> None:
        cues, canonical, decisions, metadata = _fixture()
        replacements, recovered, summary, models = recover_text_reviews_from_bpm_projection(
            cues,
            canonical,
            decisions,
            rate_prior_metadata_by_source=metadata,
        )

        self.assertEqual(replacements[2], "应该恢复的规范歌词")
        self.assertEqual(summary.resolved_review_cue_count, 1)
        self.assertEqual(models[0].status, "ready")
        self.assertEqual(recovered[2].reason, "bpm_projection_confirms_mapped_canonical")
        self.assertLess(recovered[2].score, 0.92)

        _, timing_models = build_anchor_timing_plan(cues, canonical, _timing_payload(recovered))
        self.assertEqual(timing_models[0].anchor_count, 0)
        self.assertEqual(timing_models[0].status, "insufficient_anchors")

    def test_inconsistent_bpm_fails_closed(self) -> None:
        cues, canonical, decisions, metadata = _fixture()
        metadata[0]["value"] = 1.3
        replacements, recovered, summary, models = recover_text_reviews_from_bpm_projection(
            cues,
            canonical,
            decisions,
            rate_prior_metadata_by_source=metadata,
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_review_cue_count, 0)
        self.assertEqual(recovered[2].action, "review")
        self.assertEqual(models[0].status, "bpm_projection_unstable")

    def test_pure_editor_vocalization_is_not_overwritten_by_lexical_lyric(self) -> None:
        cues, canonical, decisions, metadata = _fixture(candidate_text="哦哦哦")
        replacements, recovered, summary, _ = recover_text_reviews_from_bpm_projection(
            cues,
            canonical,
            decisions,
            rate_prior_metadata_by_source=metadata,
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_review_cue_count, 0)
        self.assertEqual(recovered[2].output_text, "哦哦哦")

    def test_optional_edge_vocalization_may_be_trimmed_to_exact_canonical(self) -> None:
        cues, canonical, decisions, metadata = _fixture(candidate_text="应该恢复的规范歌词哦")
        decisions[2] = _decision(
            2,
            cues[2].text,
            canonical[2].text,
            score=0.84,
            action="review",
            reason="adjacent_alignment_gap_requires_review",
        )
        replacements, recovered, summary, _ = recover_text_reviews_from_bpm_projection(
            cues,
            canonical,
            decisions,
            rate_prior_metadata_by_source=metadata,
        )

        self.assertEqual(replacements[2], canonical[2].text)
        self.assertEqual(summary.vocalization_trim_count, 1)
        self.assertEqual(recovered[2].reason, "bpm_projection_trims_optional_vocalization")

    def test_split_continuation_is_not_stuffed_into_one_existing_cue(self) -> None:
        cues, canonical, decisions, metadata = _fixture(candidate_text="应该恢复")
        canonical[2] = _line(2, "应该恢复后半", 27_000)
        cues[3] = _cue(3, "后半", 30_000)
        decisions[3] = _decision(
            3,
            "后半",
            canonical[3].text,
            score=0.90,
            action="unchanged",
            reason="canonical_content_matches_source_segmentation",
            canonical_ordinal=3,
        )
        replacements, recovered, summary, _ = recover_text_reviews_from_bpm_projection(
            cues,
            canonical,
            decisions,
            rate_prior_metadata_by_source=metadata,
        )

        self.assertNotIn(2, replacements)
        self.assertEqual(summary.resolved_review_cue_count, 0)
        self.assertEqual(recovered[2].action, "review")

    def test_two_row_strict_leading_edge_can_recover_before_first_safe_anchor(self) -> None:
        # Three safe anchors start at cue 2; the two immediately preceding
        # lexical rows are recoverable because BPM is independently validated.
        texts = ["前沿规范甲", "前沿规范乙", "安全锚点甲", "安全锚点乙", "安全锚点丙"]
        source_times = [5_000, 16_000, 27_000, 38_000, 49_000]
        mix_times = [0, 10_000, 20_000, 30_000, 40_000]
        canonical = [_line(i, text, source_times[i]) for i, text in enumerate(texts)]
        cues = [
            _cue(0, "错误甲", 0),
            _cue(1, "错误乙", 10_000),
            _cue(2, texts[2], 20_000),
            _cue(3, texts[3], 30_000),
            _cue(4, texts[4], 40_000),
        ]
        decisions = [
            _decision(0, cues[0].text, texts[0], score=0.25, action="review", reason="low_or_structurally_unsafe_similarity"),
            _decision(1, cues[1].text, texts[1], score=0.25, action="review", reason="low_or_structurally_unsafe_similarity"),
            *[
                _decision(i, texts[i], texts[i], score=0.90, action="unchanged", reason="canonical_content_matches_source_segmentation")
                for i in range(2, 5)
            ],
        ]
        metadata = {0: {"value": 1.1, "provenance": "bpm_derived"}}
        replacements, _, summary, models = recover_text_reviews_from_bpm_projection(
            cues,
            canonical,
            decisions,
            rate_prior_metadata_by_source=metadata,
        )

        self.assertEqual(models[0].status, "ready")
        self.assertEqual(replacements[0], texts[0])
        self.assertEqual(replacements[1], texts[1])
        self.assertEqual(summary.resolved_review_cue_count, 2)


if __name__ == "__main__":
    unittest.main()
