from __future__ import annotations

import unittest

from lyric_aligner.text_repair import MatchDecision, SubtitleCue, _normalize_for_match
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.bpm_sequence_reconcile import recover_text_reviews_from_bpm_projection


def _clock(ms: int) -> str:
    hour, rem = divmod(ms, 3_600_000)
    minute, rem = divmod(rem, 60_000)
    second, millis = divmod(rem, 1000)
    return f"{hour:02d}:{minute:02d}:{second:02d},{millis:03d}"


def _cue(index: int, text: str, start_ms: int) -> SubtitleCue:
    return SubtitleCue(
        ordinal=index,
        number=str(index + 1),
        timing=f"{_clock(start_ms)} --> {_clock(start_ms + 1500)}",
        text=text,
        normalized=_normalize_for_match(text),
        raw_block_index=index * 2,
    )


def _line(index: int, text: str, source_ms: int) -> TimedCanonicalOccurrence:
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
    source_text: str,
    canonical_text: str,
    *,
    action: str,
    score: float,
    reason: str,
) -> MatchDecision:
    return MatchDecision(
        cue_ordinal=index,
        canonical_ordinal=index,
        score=score,
        action=action,
        reason=reason,
        cue_span=(index, index + 1),
        canonical_span=(index, index + 1),
        source_text=source_text,
        canonical_text=canonical_text,
        output_text=canonical_text if action != "review" else source_text,
        edit_operations=(),
    )


class SmartBpmAdjacentLexicalGuardV122Tests(unittest.TestCase):
    def test_next_canonical_prefix_already_owned_by_editor_cue_fails_closed(self) -> None:
        # source_time = 5000 + 1.1 * mix_time.  Cue 2 is mapped to row 2,
        # but the editor cue legitimately already contains the first two
        # characters of row 3.  Single-row BPM recovery must not delete them.
        canonical_texts = [
            "稳定锚点甲甲",
            "稳定锚点乙乙",
            "本行规范文字",
            "邻行开头片段",
            "稳定锚点丁丁",
        ]
        source_times = [5_000, 16_000, 27_000, 38_000, 49_000]
        mix_times = [0, 10_000, 20_000, 30_000, 40_000]
        canonical = [
            _line(i, text, source_times[i]) for i, text in enumerate(canonical_texts)
        ]
        cues = [
            _cue(0, canonical_texts[0], mix_times[0]),
            _cue(1, canonical_texts[1], mix_times[1]),
            _cue(2, canonical_texts[2] + "邻行", mix_times[2]),
            _cue(3, canonical_texts[3], mix_times[3]),
            _cue(4, canonical_texts[4], mix_times[4]),
        ]
        decisions = [
            _decision(
                i,
                cues[i].text,
                canonical_texts[i],
                action="review" if i == 2 else "unchanged",
                score=0.70 if i == 2 else 0.90,
                reason=(
                    "adjacent_alignment_gap_requires_review"
                    if i == 2
                    else "canonical_content_matches_source_segmentation"
                ),
            )
            for i in range(5)
        ]
        metadata = {0: {"value": 1.1, "provenance": "bpm_derived"}}

        replacements, recovered, summary, models = recover_text_reviews_from_bpm_projection(
            cues,
            canonical,
            decisions,
            rate_prior_metadata_by_source=metadata,
        )

        self.assertEqual(models[0].status, "ready")
        self.assertNotIn(2, replacements)
        self.assertEqual(summary.resolved_review_cue_count, 0)
        self.assertEqual(recovered[2].action, "review")
        self.assertEqual(recovered[2].output_text, cues[2].text)

    def test_previous_canonical_suffix_already_owned_by_editor_cue_fails_closed(self) -> None:
        canonical_texts = [
            "稳定锚点甲甲",
            "上一行尾部片段",
            "本行规范文字",
            "稳定锚点丙丙",
            "稳定锚点丁丁",
        ]
        source_times = [5_000, 16_000, 27_000, 38_000, 49_000]
        mix_times = [0, 10_000, 20_000, 30_000, 40_000]
        canonical = [
            _line(i, text, source_times[i]) for i, text in enumerate(canonical_texts)
        ]
        cues = [
            _cue(0, canonical_texts[0], mix_times[0]),
            _cue(1, canonical_texts[1], mix_times[1]),
            _cue(2, "片段" + canonical_texts[2], mix_times[2]),
            _cue(3, canonical_texts[3], mix_times[3]),
            _cue(4, canonical_texts[4], mix_times[4]),
        ]
        decisions = [
            _decision(
                i,
                cues[i].text,
                canonical_texts[i],
                action="review" if i == 2 else "unchanged",
                score=0.70 if i == 2 else 0.90,
                reason=(
                    "adjacent_alignment_gap_requires_review"
                    if i == 2
                    else "canonical_content_matches_source_segmentation"
                ),
            )
            for i in range(5)
        ]
        metadata = {0: {"value": 1.1, "provenance": "bpm_derived"}}

        replacements, recovered, summary, models = recover_text_reviews_from_bpm_projection(
            cues,
            canonical,
            decisions,
            rate_prior_metadata_by_source=metadata,
        )

        self.assertEqual(models[0].status, "ready")
        self.assertNotIn(2, replacements)
        self.assertEqual(summary.resolved_review_cue_count, 0)
        self.assertEqual(recovered[2].action, "review")
        self.assertEqual(recovered[2].output_text, cues[2].text)


if __name__ == "__main__":
    unittest.main()
