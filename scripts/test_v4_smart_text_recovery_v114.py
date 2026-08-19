from __future__ import annotations

import unittest

from lyric_aligner.text_repair import MatchDecision, SubtitleCue, _normalize_for_match
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.smart_policy import _text_payload
from lyric_aligner.timeline.text_recovery_consensus import (
    recover_text_reviews_from_consensus,
)


def _cue(ordinal: int, start_ms: int, text: str, duration_ms: int = 2000) -> SubtitleCue:
    def clock(value: int) -> str:
        minute, rem = divmod(value, 60_000)
        second, millis = divmod(rem, 1000)
        return f"00:{minute:02d}:{second:02d},{millis:03d}"

    return SubtitleCue(
        ordinal=ordinal,
        number=str(ordinal + 1),
        timing=f"{clock(start_ms)} --> {clock(start_ms + duration_ms)}",
        text=text,
        normalized=_normalize_for_match(text),
        raw_block_index=ordinal,
    )


def _canonical(ordinal: int, start_ms: int, text: str) -> TimedCanonicalOccurrence:
    return TimedCanonicalOccurrence(
        ordinal=ordinal,
        source="song.lrc",
        source_ordinal=0,
        time_ms=start_ms,
        text=text,
        normalized=_normalize_for_match(text),
    )


def _unchanged(cue: SubtitleCue, canonical_ordinal: int, score: float = 1.0) -> MatchDecision:
    return MatchDecision(
        cue_ordinal=cue.ordinal,
        canonical_ordinal=canonical_ordinal,
        score=score,
        action="unchanged",
        reason="canonical_content_matches_source_segmentation",
        cue_span=(cue.ordinal, cue.ordinal + 1),
        canonical_span=(canonical_ordinal, canonical_ordinal + 1),
        source_text=cue.text,
        canonical_text=cue.text,
        output_text=cue.text,
    )


def _review(
    cue: SubtitleCue,
    canonical_span: tuple[int, int] | None,
    *,
    score: float = 0.1,
) -> MatchDecision:
    return MatchDecision(
        cue_ordinal=cue.ordinal,
        canonical_ordinal=(canonical_span[0] if canonical_span else None),
        score=score,
        action="review",
        reason="low_or_structurally_unsafe_similarity",
        cue_span=(cue.ordinal, cue.ordinal + 1),
        canonical_span=canonical_span,
        source_text=cue.text,
        canonical_text="",
        output_text=cue.text,
    )


class SmartTextRecoveryV114Tests(unittest.TestCase):
    def test_local_bilateral_whole_span_preserves_editor_character_ownership(self) -> None:
        canonical = [
            _canonical(0, 10_000, "左侧锚点"),
            _canonical(1, 20_000, "第一段第二段"),
            _canonical(2, 30_000, "右侧锚点"),
        ]
        cues = [
            _cue(0, 10_000, "左侧锚点"),
            _cue(1, 18_000, "第一段错"),
            _cue(2, 22_000, "第二段"),
            _cue(3, 30_000, "右侧锚点"),
        ]
        decisions = [
            _unchanged(cues[0], 0),
            _review(cues[1], (1, 2), score=0.65),
            _review(cues[2], (1, 2), score=0.65),
            _unchanged(cues[3], 2),
        ]

        replacements, output, _, summary = recover_text_reviews_from_consensus(
            cues,
            canonical,
            decisions,
        )

        self.assertEqual(replacements[1], "第一段")
        self.assertEqual(replacements[2], "第二段")
        self.assertEqual(summary.local_bilateral_block_count, 1)
        self.assertEqual(summary.local_segmentation_preserve_count, 2)
        for ordinal in (1, 2):
            self.assertEqual(
                output[ordinal].reason,
                "local_bilateral_span_preserves_editor_segmentation",
            )
            # One canonical line spans two editor cues, so no fake 1:1 timing
            # identity may be manufactured for either display cue.
            self.assertIsNone(output[ordinal].canonical_span)

    def test_local_bilateral_timing_partitions_severe_asr_block(self) -> None:
        canonical = [
            _canonical(0, 30_000, "左侧锚点"),
            _canonical(1, 40_000, "规范甲"),
            _canonical(2, 44_000, "规范乙"),
            _canonical(3, 50_000, "规范丙"),
            _canonical(4, 54_000, "规范丁"),
            _canonical(5, 60_000, "右侧锚点"),
        ]
        cues = [
            _cue(0, 30_000, "左侧锚点"),
            _cue(1, 40_000, "乱码完全不相似一", 9000),
            _cue(2, 50_000, "乱码完全不相似二", 9000),
            _cue(3, 60_000, "右侧锚点"),
        ]
        decisions = [
            _unchanged(cues[0], 0),
            _review(cues[1], (1, 3), score=0.05),
            _review(cues[2], (3, 5), score=0.05),
            _unchanged(cues[3], 5),
        ]

        replacements, output, _, summary = recover_text_reviews_from_consensus(
            cues,
            canonical,
            decisions,
        )

        self.assertEqual(replacements[1], "规范甲 规范乙")
        self.assertEqual(replacements[2], "规范丙 规范丁")
        self.assertEqual(summary.local_timing_partition_count, 2)
        self.assertEqual(
            output[1].reason,
            "local_bilateral_timing_confirms_canonical_sequence",
        )

    def test_exact_consensus_recovers_low_similarity_mapped_cue(self) -> None:
        canonical_texts = [
            "锚点甲",
            "锚点乙",
            "锚点丙",
            "目标规范歌词",
            "锚点丁",
            "锚点戊",
            "锚点己",
            "锚点庚",
        ]
        starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000, 70_000, 80_000]
        canonical = [
            _canonical(index, start, text)
            for index, (start, text) in enumerate(zip(starts, canonical_texts))
        ]
        cues = [
            _cue(index, start, ("完全错误的识别" if index == 3 else text))
            for index, (start, text) in enumerate(zip(starts, canonical_texts))
        ]
        decisions = [
            (_review(cues[index], (index, index + 1), score=0.0) if index == 3 else _unchanged(cues[index], index))
            for index in range(len(cues))
        ]

        replacements, output, models, summary = recover_text_reviews_from_consensus(
            cues,
            canonical,
            decisions,
        )

        self.assertEqual(replacements[3], "目标规范歌词")
        self.assertEqual(summary.consensus_timing_cue_count, 1)
        self.assertEqual(summary.consensus_model_count, 1)
        self.assertGreaterEqual(models[0].anchor_count, 6)
        self.assertEqual(
            output[3].reason,
            "exact_consensus_timing_confirms_mapped_canonical",
        )

    def test_exact_consensus_does_not_recover_when_onset_is_outside_cue(self) -> None:
        canonical_texts = [
            "锚点甲",
            "锚点乙",
            "锚点丙",
            "目标规范歌词",
            "锚点丁",
            "锚点戊",
            "锚点己",
            "锚点庚",
        ]
        starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000, 70_000, 80_000]
        canonical = [
            _canonical(index, start, text)
            for index, (start, text) in enumerate(zip(starts, canonical_texts))
        ]
        cues = []
        decisions = []
        for index, (start, text) in enumerate(zip(starts, canonical_texts)):
            cue_start = 45_000 if index == 3 else start
            cue = _cue(index, cue_start, "完全错误的识别" if index == 3 else text)
            cues.append(cue)
            decisions.append(
                _review(cue, (index, index + 1), score=0.0)
                if index == 3
                else _unchanged(cue, index)
            )

        replacements, output, _, summary = recover_text_reviews_from_consensus(
            cues,
            canonical,
            decisions,
        )

        self.assertNotIn(3, replacements)
        self.assertEqual(summary.consensus_timing_cue_count, 0)
        self.assertEqual(output[3].action, "review")

    def test_consensus_preserves_leading_editor_adlib_only_with_timing_lead(self) -> None:
        canonical_texts = [
            "锚点甲",
            "锚点乙",
            "锚点丙",
            "规范歌词",
            "锚点丁",
            "锚点戊",
            "锚点己",
            "锚点庚",
        ]
        starts = [10_000, 20_000, 30_000, 40_000, 50_000, 60_000, 70_000, 80_000]
        canonical = [
            _canonical(index, start, text)
            for index, (start, text) in enumerate(zip(starts, canonical_texts))
        ]
        cues = [
            _cue(index, (39_500 if index == 3 else start), ("哟乱码" if index == 3 else text))
            for index, (start, text) in enumerate(zip(starts, canonical_texts))
        ]
        decisions = [
            (_review(cues[index], (index, index + 1), score=0.0) if index == 3 else _unchanged(cues[index], index))
            for index in range(len(cues))
        ]

        replacements, _, _, _ = recover_text_reviews_from_consensus(
            cues,
            canonical,
            decisions,
        )
        self.assertEqual(replacements[3], "哟 规范歌词")

    def test_recovery_reason_is_capped_out_of_primary_timing_grade(self) -> None:
        cue = _cue(0, 10_000, "原文")
        decision = MatchDecision(
            cue_ordinal=0,
            canonical_ordinal=0,
            score=1.0,
            action="unchanged",
            reason="exact_consensus_timing_confirms_mapped_canonical",
            cue_span=(0, 1),
            canonical_span=(0, 1),
            source_text="原文",
            canonical_text="原文",
            output_text="原文",
        )

        report_payload = _text_payload([decision])
        timing_payload = _text_payload([decision], for_timing=True)
        self.assertEqual(report_payload[0]["score"], 1.0)
        self.assertLess(timing_payload[0]["score"], 0.92)


if __name__ == "__main__":
    unittest.main()
