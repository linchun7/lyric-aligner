from __future__ import annotations

import unittest
from dataclasses import replace

from lyric_aligner.text_repair import MatchDecision, SubtitleCue, _normalize_for_match
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence, TimingDecision
from smart_a_bounded_shadow import recover_mapped_reviews_from_a_bounded_shadow


def _clock(value: int) -> str:
    minute, rem = divmod(value, 60_000)
    second, millis = divmod(rem, 1000)
    return f"00:{minute:02d}:{second:02d},{millis:03d}"


def _cue(index: int, text: str) -> SubtitleCue:
    start_ms = index * 2_000
    end_ms = start_ms + 1_500
    return SubtitleCue(
        ordinal=index,
        number=str(index + 1),
        timing=f"{_clock(start_ms)} --> {_clock(end_ms)}",
        text=text,
        normalized=_normalize_for_match(text),
        raw_block_index=index * 2,
    )


def _canonical(
    index: int,
    text: str,
    *,
    source_ordinal: int = 0,
) -> TimedCanonicalOccurrence:
    return TimedCanonicalOccurrence(
        ordinal=index,
        source=f"synthetic-{source_ordinal}.lrc",
        source_ordinal=source_ordinal,
        time_ms=index * 2_000,
        text=text,
        normalized=_normalize_for_match(text),
    )


def _decision(
    cue_ordinal: int,
    canonical_span: tuple[int, int] | None,
    *,
    action: str,
    text: str,
    reason: str | None = None,
    score: float = 1.0,
) -> MatchDecision:
    canonical_ordinal = canonical_span[0] if canonical_span else None
    return MatchDecision(
        cue_ordinal=cue_ordinal,
        canonical_ordinal=canonical_ordinal,
        score=score,
        action=action,
        reason=reason
        or (
            "low_or_structurally_unsafe_similarity"
            if action == "review"
            else "canonical_content_matches_source_segmentation"
        ),
        cue_span=(cue_ordinal, cue_ordinal + 1),
        canonical_span=canonical_span,
        source_text=text,
        canonical_text=text if action != "review" else "",
        output_text=text,
        edit_operations=(),
    )


def _timing(
    cue_ordinal: int,
    canonical_ordinal: int,
    *,
    source_ordinal: int = 0,
    grade: str = "A",
    residual_ms: float = 0.0,
    model_status: str = "ready",
) -> TimingDecision:
    start_ms = cue_ordinal * 2_000
    return TimingDecision(
        cue_ordinal=cue_ordinal,
        source_ordinal=source_ordinal,
        canonical_ordinal=canonical_ordinal,
        anchor_grade=grade,
        action="preserve",
        reason="timing_matches_anchor_model",
        old_start_ms=start_ms,
        old_end_ms=start_ms + 1_500,
        proposed_start_ms=None,
        proposed_end_ms=None,
        residual_ms=residual_ms,
        model_status=model_status,
        evidence=("canonical_identity", "affine_model"),
    )


def _positive_fixture():
    cues = [
        _cue(0, "左侧锚点"),
        _cue(1, "春风吹过山何"),
        _cue(2, "星光落在常街"),
        _cue(3, "局部边界"),
        _cue(4, "远端锚点"),
    ]
    canonical = [
        _canonical(0, "左侧锚点"),
        _canonical(1, "春风吹过山河"),
        _canonical(2, "星光落在长街"),
        _canonical(3, "局部边界"),
        _canonical(4, "远端锚点"),
    ]
    decisions = [
        _decision(0, (0, 1), action="unchanged", text=cues[0].text),
        _decision(1, (1, 2), action="review", text=cues[1].text, score=0.70),
        _decision(2, (2, 3), action="review", text=cues[2].text, score=0.70),
        _decision(3, (3, 4), action="unchanged", text=cues[3].text),
        _decision(4, (4, 5), action="unchanged", text=cues[4].text),
    ]
    # Right A is deliberately farther than the immediate resolved local boundary.
    timing = [_timing(0, 0, residual_ms=120.0), _timing(4, 4, residual_ms=-180.0)]
    return cues, canonical, decisions, timing


class SmartABoundedShadowV125Tests(unittest.TestCase):
    def test_bilateral_a_region_recovers_mapped_reviews_without_timing_authority(self) -> None:
        cues, canonical, decisions, timing = _positive_fixture()
        original_timing = [(cue.number, cue.timing) for cue in cues]

        replacements, updated, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        self.assertEqual(replacements[1], "春风吹过山河")
        self.assertEqual(replacements[2], "星光落在长街")
        self.assertEqual(summary.resolved_region_count, 1)
        self.assertEqual(summary.resolved_review_cue_count, 2)
        self.assertEqual(summary.materialized_text_change_count, 2)
        self.assertTrue(
            all(
                item.reason == "a_bounded_region_confirms_canonical_stream"
                and item.score <= 0.89
                for item in updated[1:3]
            )
        )
        self.assertEqual(original_timing, [(cue.number, cue.timing) for cue in cues])

    def test_one_canonical_row_may_remain_split_across_two_editor_cues(self) -> None:
        cues = [
            _cue(0, "左侧锚点"),
            _cue(1, "山河向前继续"),
            _cue(2, "奔流直到远访"),
            _cue(3, "局部边界"),
            _cue(4, "远端锚点"),
        ]
        canonical = [
            _canonical(0, "左侧锚点"),
            _canonical(1, "山河向前继续奔流直到远方"),
            _canonical(2, "局部边界"),
            _canonical(3, "远端锚点"),
        ]
        decisions = [
            _decision(0, (0, 1), action="unchanged", text=cues[0].text),
            _decision(1, (1, 2), action="review", text=cues[1].text, score=0.60),
            _decision(2, (1, 2), action="review", text=cues[2].text, score=0.60),
            _decision(3, (2, 3), action="unchanged", text=cues[3].text),
            _decision(4, (3, 4), action="unchanged", text=cues[4].text),
        ]
        timing = [_timing(0, 0), _timing(4, 3)]

        replacements, updated, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        final = [replacements.get(cue.ordinal, cue.text) for cue in cues]
        self.assertEqual(
            _normalize_for_match(final[1] + final[2]),
            _normalize_for_match(canonical[1].text),
        )
        self.assertEqual(updated[1].canonical_span, (1, 2))
        self.assertEqual(updated[2].canonical_span, (1, 2))
        self.assertEqual(summary.resolved_region_count, 1)
        self.assertEqual(summary.resolved_review_cue_count, 2)

    def test_unmapped_none_blocks_entire_region(self) -> None:
        cues, canonical, decisions, timing = _positive_fixture()
        decisions[1] = replace(decisions[1], canonical_ordinal=None, canonical_span=None)

        replacements, updated, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        self.assertEqual(replacements, {})
        self.assertEqual(updated[1].action, "review")
        self.assertEqual(summary.resolved_region_count, 0)

    def test_zero_width_unmapped_span_blocks_entire_region(self) -> None:
        cues, canonical, decisions, timing = _positive_fixture()
        decisions[1] = replace(decisions[1], canonical_span=(1, 1))

        replacements, _, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_region_count, 0)

    def test_a_anchor_residual_over_750_ms_fails_closed(self) -> None:
        cues, canonical, decisions, timing = _positive_fixture()
        timing[1] = replace(timing[1], residual_ms=751.0)

        replacements, _, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_region_count, 0)

    def test_non_ready_a_anchor_fails_closed(self) -> None:
        cues, canonical, decisions, timing = _positive_fixture()
        timing[1] = replace(timing[1], model_status="insufficient_anchors")

        replacements, _, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_region_count, 0)

    def test_cross_source_canonical_gap_fails_closed(self) -> None:
        cues, canonical, decisions, timing = _positive_fixture()
        canonical[2] = _canonical(2, "星光落在长街", source_ordinal=1)

        replacements, _, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_region_count, 0)

    def test_multi_cue_latin_region_fails_closed(self) -> None:
        cues = [
            _cue(0, "left anchor"),
            _cue(1, "silver river glowx"),
            _cue(2, "under quiet skiex"),
            _cue(3, "local boundary"),
            _cue(4, "right anchor"),
        ]
        canonical = [
            _canonical(0, "left anchor"),
            _canonical(1, "silver river glows"),
            _canonical(2, "under quiet skies"),
            _canonical(3, "local boundary"),
            _canonical(4, "right anchor"),
        ]
        decisions = [
            _decision(0, (0, 1), action="unchanged", text=cues[0].text),
            _decision(1, (1, 2), action="review", text=cues[1].text),
            _decision(2, (2, 3), action="review", text=cues[2].text),
            _decision(3, (3, 4), action="unchanged", text=cues[3].text),
            _decision(4, (4, 5), action="unchanged", text=cues[4].text),
        ]
        timing = [_timing(0, 0), _timing(4, 4)]

        replacements, _, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_region_count, 0)

    def test_pure_vocalization_inside_region_fails_closed(self) -> None:
        cues, canonical, decisions, timing = _positive_fixture()
        cues[1] = _cue(1, "哦哦哦哦哦哦")
        canonical[1] = _canonical(1, "哦哦哦哦哦哦")
        decisions[1] = _decision(
            1, (1, 2), action="review", text=cues[1].text, score=0.70
        )

        replacements, _, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_region_count, 0)

    def test_boundary_insertion_fails_closed(self) -> None:
        cues = [
            _cue(0, "左侧锚点"),
            _cue(1, "甲乙丙丁戊己"),
            _cue(2, "庚辛壬癸子丑"),
            _cue(3, "局部边界"),
            _cue(4, "远端锚点"),
        ]
        canonical = [
            _canonical(0, "左侧锚点"),
            _canonical(1, "甲乙丙丁戊己新"),
            _canonical(2, "庚辛壬癸子丑"),
            _canonical(3, "局部边界"),
            _canonical(4, "远端锚点"),
        ]
        decisions = [
            _decision(0, (0, 1), action="unchanged", text=cues[0].text),
            _decision(1, (1, 2), action="review", text=cues[1].text),
            _decision(2, (2, 3), action="review", text=cues[2].text),
            _decision(3, (3, 4), action="unchanged", text=cues[3].text),
            _decision(4, (4, 5), action="unchanged", text=cues[4].text),
        ]
        timing = [_timing(0, 0), _timing(4, 4)]

        replacements, _, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_region_count, 0)

    def test_low_region_similarity_fails_closed(self) -> None:
        cues, canonical, decisions, timing = _positive_fixture()
        cues[1] = _cue(1, "天地玄黄宇宙")
        cues[2] = _cue(2, "洪荒日月盈昃")
        decisions[1] = _decision(1, (1, 2), action="review", text=cues[1].text)
        decisions[2] = _decision(2, (2, 3), action="review", text=cues[2].text)

        replacements, _, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_region_count, 0)

    def test_short_region_under_twelve_normalized_chars_fails_closed(self) -> None:
        cues, canonical, decisions, timing = _positive_fixture()
        cues[1] = _cue(1, "春风山何")
        cues[2] = _cue(2, "星光常街")
        canonical[1] = _canonical(1, "春风山河")
        canonical[2] = _canonical(2, "星光长街")
        decisions[1] = _decision(1, (1, 2), action="review", text=cues[1].text)
        decisions[2] = _decision(2, (2, 3), action="review", text=cues[2].text)

        replacements, _, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_region_count, 0)

    def test_length_ratio_below_point_85_fails_closed(self) -> None:
        cues, canonical, decisions, timing = _positive_fixture()
        cues[1] = _cue(1, "春风吹过山河")
        cues[2] = _cue(2, "星光落在长街")
        canonical[1] = _canonical(1, "春风吹过山河继续向前")
        canonical[2] = _canonical(2, "星光落在长街直到天明")
        decisions[1] = _decision(1, (1, 2), action="review", text=cues[1].text)
        decisions[2] = _decision(2, (2, 3), action="review", text=cues[2].text)

        replacements, _, summary = recover_mapped_reviews_from_a_bounded_shadow(
            cues, canonical, decisions, timing
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_region_count, 0)


if __name__ == "__main__":
    unittest.main()
