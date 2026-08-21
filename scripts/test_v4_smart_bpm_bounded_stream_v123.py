from __future__ import annotations

import unittest

from lyric_aligner.text_repair import MatchDecision, SubtitleCue, _normalize_for_match
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.bpm_sequence_reconcile import recover_text_reviews_from_bpm_projection


def _cue(index: int, start_ms: int, end_ms: int, text: str) -> SubtitleCue:
    def clock(value: int) -> str:
        minute, rem = divmod(value, 60_000)
        second, millis = divmod(rem, 1000)
        return f"00:{minute:02d}:{second:02d},{millis:03d}"

    return SubtitleCue(
        ordinal=index,
        number=str(index + 1),
        timing=f"{clock(start_ms)} --> {clock(end_ms)}",
        text=text,
        normalized=_normalize_for_match(text),
        raw_block_index=index * 2,
    )


def _canonical(index: int, time_ms: int, text: str, *, source_ordinal: int = 0) -> TimedCanonicalOccurrence:
    return TimedCanonicalOccurrence(
        ordinal=index,
        source=f"song-{source_ordinal}.lrc",
        source_ordinal=source_ordinal,
        time_ms=time_ms,
        text=text,
        normalized=_normalize_for_match(text),
    )


def _decision(
    cue_ordinal: int,
    canonical_ordinal: int | None,
    *,
    action: str,
    score: float,
    reason: str,
    source_text: str,
    canonical_text: str = "",
) -> MatchDecision:
    span = None if canonical_ordinal is None else (canonical_ordinal, canonical_ordinal + 1)
    return MatchDecision(
        cue_ordinal=cue_ordinal,
        canonical_ordinal=canonical_ordinal,
        score=score,
        action=action,
        reason=reason,
        cue_span=(cue_ordinal, cue_ordinal + 1),
        canonical_span=span,
        source_text=source_text,
        canonical_text=canonical_text,
        output_text=source_text if action == "review" else canonical_text or source_text,
        edit_operations=(),
    )


def _ready_fixture(*, middle_two: tuple[str, str], middle_two_spans: tuple[int | None, int | None]):
    cues = [
        _cue(0, 0, 1500, "起点锚定"),
        _cue(1, 2000, 3500, middle_two[0]),
        _cue(2, 4000, 5500, middle_two[1]),
        _cue(3, 6000, 7500, "终点锚定"),
        _cue(4, 12000, 13500, "远端锚定"),
    ]
    canonical = [
        _canonical(0, 0, "起点锚定"),
        _canonical(1, 2000, "山河破碎"),
        _canonical(2, 4000, "汉字到底懂不懂"),
        _canonical(3, 6000, "终点锚定"),
        _canonical(4, 12000, "远端锚定"),
    ]
    decisions = [
        _decision(0, 0, action="unchanged", score=1.0, reason="canonical_content_matches_source_segmentation", source_text="起点锚定", canonical_text="起点锚定"),
        _decision(1, middle_two_spans[0], action="review", score=0.10, reason="low_or_structurally_unsafe_similarity", source_text=middle_two[0], canonical_text="山河破碎"),
        _decision(2, middle_two_spans[1], action="review", score=0.0, reason="unmatched_subtitle_cue" if middle_two_spans[1] is None else "low_or_structurally_unsafe_similarity", source_text=middle_two[1], canonical_text="汉字到底懂不懂"),
        _decision(3, 3, action="unchanged", score=1.0, reason="canonical_content_matches_source_segmentation", source_text="终点锚定", canonical_text="终点锚定"),
        _decision(4, 4, action="unchanged", score=1.0, reason="canonical_content_matches_source_segmentation", source_text="远端锚定", canonical_text="远端锚定"),
    ]
    metadata = {0: {"provenance": "bpm_derived", "value": 1.0}}
    return cues, canonical, decisions, metadata


class SmartBpmBoundedStreamV123Tests(unittest.TestCase):
    def test_bilateral_stream_recovers_mapped_and_plausible_unmapped_review(self) -> None:
        cues, canonical, decisions, metadata = _ready_fixture(
            middle_two=("山河错碎", "汉到底都不同"),
            middle_two_spans=(1, None),
        )

        replacements, updated, summary, models = recover_text_reviews_from_bpm_projection(
            cues,
            canonical,
            decisions,
            rate_prior_metadata_by_source=metadata,
        )

        self.assertEqual(models[0].status, "ready")
        self.assertEqual(replacements[1], "山河破碎")
        self.assertEqual(replacements[2], "汉字到底懂不懂")
        self.assertEqual(summary.bounded_stream_region_count, 1)
        # The mapped review is intentionally solved first by the unchanged v1.2.2
        # 1:1 tier; the bounded tier adds only the formerly-unmapped cue.
        self.assertEqual(summary.bounded_stream_cue_count, 1)
        self.assertEqual(summary.bounded_stream_unmapped_cue_count, 1)
        self.assertEqual(summary.resolved_review_cue_count, 2)
        self.assertEqual(updated[1].reason, "bpm_projection_confirms_mapped_canonical")
        self.assertTrue(updated[2].reason.startswith("sequence_projection_confirms_bpm_bounded_stream"))

    def test_one_canonical_row_may_span_two_editor_cues_without_using_lrc_line_as_boundary(self) -> None:
        cues = [
            _cue(0, 0, 1500, "起点锚定"),
            _cue(1, 2000, 3300, "山河向前"),
            _cue(2, 3400, 5200, "继续奔流"),
            _cue(3, 6000, 7500, "终点锚定"),
            _cue(4, 12000, 13500, "远端锚定"),
        ]
        canonical = [
            _canonical(0, 0, "起点锚定"),
            _canonical(1, 2000, "山河向前继续奔流"),
            _canonical(2, 6000, "终点锚定"),
            _canonical(3, 12000, "远端锚定"),
        ]
        decisions = [
            _decision(0, 0, action="unchanged", score=1.0, reason="canonical_content_matches_source_segmentation", source_text="起点锚定", canonical_text="起点锚定"),
            _decision(1, 1, action="review", score=0.5, reason="adjacent_alignment_gap_requires_review", source_text="山河向前", canonical_text="山河向前继续奔流"),
            _decision(2, 1, action="review", score=0.5, reason="adjacent_alignment_gap_requires_review", source_text="继续奔流", canonical_text="山河向前继续奔流"),
            _decision(3, 2, action="unchanged", score=1.0, reason="canonical_content_matches_source_segmentation", source_text="终点锚定", canonical_text="终点锚定"),
            _decision(4, 3, action="unchanged", score=1.0, reason="canonical_content_matches_source_segmentation", source_text="远端锚定", canonical_text="远端锚定"),
        ]
        metadata = {0: {"provenance": "bpm_derived", "value": 1.0}}

        replacements, _, summary, _ = recover_text_reviews_from_bpm_projection(
            cues,
            canonical,
            decisions,
            rate_prior_metadata_by_source=metadata,
        )

        self.assertEqual(_normalize_for_match(replacements[1] + replacements[2]), _normalize_for_match("山河向前继续奔流"))
        self.assertEqual(summary.bounded_stream_region_count, 1)
        self.assertEqual(summary.bounded_stream_cue_count, 2)

    def test_pure_vocalization_inside_region_blocks_stream_repartition(self) -> None:
        cues, canonical, decisions, metadata = _ready_fixture(
            middle_two=("山河错碎", "哦哦"),
            middle_two_spans=(1, None),
        )

        replacements, _, summary, _ = recover_text_reviews_from_bpm_projection(
            cues,
            canonical,
            decisions,
            rate_prior_metadata_by_source=metadata,
        )

        self.assertNotIn(2, replacements)
        self.assertEqual(summary.bounded_stream_region_count, 0)

    def test_unmapped_low_similarity_adlib_is_not_forced_into_canonical_stream(self) -> None:
        cues, canonical, decisions, metadata = _ready_fixture(
            middle_two=("山河错碎", "random spoken aside"),
            middle_two_spans=(1, None),
        )

        replacements, _, summary, _ = recover_text_reviews_from_bpm_projection(
            cues,
            canonical,
            decisions,
            rate_prior_metadata_by_source=metadata,
        )

        self.assertNotIn(2, replacements)
        self.assertEqual(summary.bounded_stream_region_count, 0)


if __name__ == "__main__":
    unittest.main()
