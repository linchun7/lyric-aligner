from __future__ import annotations

import unittest

from lyric_aligner.text_repair import SubtitleCue, _normalize_for_match
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.sequence_reconcile import (
    SequenceProjectionModel,
    _frontier_choice,
)


class SmartSequenceFrontierGuardTests(unittest.TestCase):
    def _cue(self, text: str, start_ms: int = 10_000, end_ms: int = 13_000) -> SubtitleCue:
        return SubtitleCue(
            ordinal=0,
            number="1",
            timing="00:00:10,000 --> 00:00:13,000",
            text=text,
            normalized=_normalize_for_match(text),
            raw_block_index=0,
        )

    def _line(self, text: str, time_ms: int = 10_050) -> TimedCanonicalOccurrence:
        return TimedCanonicalOccurrence(
            ordinal=0,
            source="song.lrc",
            source_ordinal=0,
            time_ms=time_ms,
            text=text,
            normalized=_normalize_for_match(text),
        )

    def _model(self) -> SequenceProjectionModel:
        return SequenceProjectionModel(
            source_ordinal=0,
            source="song.lrc",
            rate=1.0,
            offset_ms=0.0,
            rate_source="robust_strong_text_anchors",
            strong_anchor_count=4,
            a_anchor_count=3,
            inlier_count=4,
            median_abs_residual_ms=20.0,
            inlier_fraction=1.0,
            status="ready",
        )

    def test_short_editor_adlib_is_not_replaced_by_nearby_lexical_line(self) -> None:
        choice = _frontier_choice(
            self._cue("哎"),
            [self._line("这是一句完整的规范歌词")],
            self._model(),
            next_cue_start_ms=None,
        )
        self.assertIsNone(choice)

    def test_normal_length_severe_asr_can_still_use_single_line_sequence_timing(self) -> None:
        choice = _frontier_choice(
            self._cue("完全错误内容甲甲甲甲"),
            [self._line("规范目标内容乙乙乙乙")],
            self._model(),
            next_cue_start_ms=None,
        )
        self.assertIsNotNone(choice)
        assert choice is not None
        self.assertEqual([item.text for item in choice[0]], ["规范目标内容乙乙乙乙"])

    def test_corrupted_but_recognizable_single_line_can_use_frontier(self) -> None:
        choice = _frontier_choice(
            self._cue("这厢是梦寐脸上画中的仙"),
            [self._line("这厢是梦梅恋上画中的仙")],
            self._model(),
            next_cue_start_ms=None,
        )
        self.assertIsNotNone(choice)
        assert choice is not None
        self.assertEqual([item.text for item in choice[0]], ["这厢是梦梅恋上画中的仙"])


if __name__ == "__main__":
    unittest.main()
