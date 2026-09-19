from __future__ import annotations

import unittest

from lyric_aligner.text_repair import MatchDecision, SubtitleCue
from lyric_aligner.timeline.anchor_repair import SongTimingModel, TimedCanonicalOccurrence
from lyric_aligner.timeline.text_recovery import recover_text_reviews_from_timing


def _cue(ordinal: int, start_second: int, text: str) -> SubtitleCue:
    return SubtitleCue(
        ordinal=ordinal,
        number=str(ordinal + 1),
        timing=(
            f"00:00:{start_second:02d},000 --> "
            f"00:00:{start_second + 1:02d},000"
        ),
        text=text,
        normalized=text,
        raw_block_index=ordinal,
    )


def _canonical(ordinal: int, start_second: int, text: str) -> TimedCanonicalOccurrence:
    return TimedCanonicalOccurrence(
        ordinal=ordinal,
        source="song.lrc",
        source_ordinal=0,
        time_ms=start_second * 1000,
        text=text,
        normalized=text,
    )


class SmartTextRecoveryContractTests(unittest.TestCase):
    def test_recovery_may_not_skip_weak_adjacent_cue_to_borrow_far_anchor(self) -> None:
        cues = [
            _cue(0, 10, "左侧锚点"),
            _cue(1, 20, "完全错误毫不相似"),
            _cue(2, 30, "边界歌词错一字"),
            _cue(3, 40, "远处强锚点"),
        ]
        canonical = [
            _canonical(0, 10, "左侧锚点"),
            _canonical(1, 20, "规范歌词"),
            _canonical(2, 30, "边界歌词正确"),
            _canonical(3, 40, "远处强锚点"),
        ]
        decisions = [
            MatchDecision(
                cue_ordinal=0,
                canonical_ordinal=0,
                score=1.0,
                action="unchanged",
                reason="exact_unique_match",
                cue_span=(0, 1),
                canonical_span=(0, 1),
                source_text="左侧锚点",
                canonical_text="左侧锚点",
                output_text="左侧锚点",
            ),
            MatchDecision(
                cue_ordinal=1,
                canonical_ordinal=None,
                score=0.0,
                action="review",
                reason="low_similarity",
                cue_span=(1, 2),
                canonical_span=None,
                source_text="完全错误毫不相似",
                output_text="完全错误毫不相似",
            ),
            MatchDecision(
                cue_ordinal=2,
                canonical_ordinal=2,
                score=0.80,
                action="replace",
                reason="safe_text_repair",
                cue_span=(2, 3),
                canonical_span=(2, 3),
                source_text="边界歌词错一字",
                canonical_text="边界歌词正确",
                output_text="边界歌词正确",
            ),
            MatchDecision(
                cue_ordinal=3,
                canonical_ordinal=3,
                score=1.0,
                action="unchanged",
                reason="exact_unique_match",
                cue_span=(3, 4),
                canonical_span=(3, 4),
                source_text="远处强锚点",
                canonical_text="远处强锚点",
                output_text="远处强锚点",
            ),
        ]
        models = [
            SongTimingModel(
                source_ordinal=0,
                source="song.lrc",
                rate=1.0,
                offset_ms=0.0,
                rate_source="robust_anchor_estimate",
                anchor_count=6,
                inlier_count=6,
                median_abs_residual_ms=0.0,
                inlier_fraction=1.0,
                status="ready",
                word_timing_anchor_count=0,
            )
        ]

        replacements, output_decisions, summary = recover_text_reviews_from_timing(
            cues,
            canonical,
            decisions,
            models,
        )

        self.assertEqual(replacements, {})
        self.assertEqual(summary.resolved_cue_count, 0)
        self.assertEqual(summary.resolved_block_count, 0)
        self.assertEqual(output_decisions[1].action, "review")
        self.assertEqual(output_decisions[1].output_text, "完全错误毫不相似")


if __name__ == "__main__":
    unittest.main()
