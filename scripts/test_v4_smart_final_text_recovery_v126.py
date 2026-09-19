from __future__ import annotations

import unittest

from lyric_aligner.text_repair import MatchDecision, SubtitleCue, _normalize_for_match
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence
from lyric_aligner.timeline.final_text_recovery import recover_final_text_reviews


def _cue(index: int, text: str) -> SubtitleCue:
    return SubtitleCue(
        ordinal=index,
        number=str(index + 1),
        timing=f"00:00:0{index},000 --> 00:00:0{index + 1},000",
        text=text,
        normalized=_normalize_for_match(text),
        raw_block_index=index * 2,
    )


def _canonical(index: int, text: str) -> TimedCanonicalOccurrence:
    return TimedCanonicalOccurrence(
        ordinal=index,
        source="synthetic.lrc",
        source_ordinal=0,
        time_ms=index * 1000,
        text=text,
        normalized=_normalize_for_match(text),
    )


def _decision(index: int, *, action: str, span: tuple[int, int]) -> MatchDecision:
    return MatchDecision(
        cue_ordinal=index,
        canonical_ordinal=span[0],
        score=0.8,
        action=action,
        reason=(
            "adjacent_alignment_gap_requires_review"
            if action == "review"
            else "canonical_content_matches_source_segmentation"
        ),
        cue_span=(index, index + 1),
        canonical_span=span,
    )


class SmartFinalTextRecoveryV126Tests(unittest.TestCase):
    def test_same_length_single_character_correction_preserves_ownership(self) -> None:
        cues = [_cue(0, "今天风景分外清新")]
        canonical = [_canonical(0, "今天风景格外清新")]
        decisions = [_decision(0, action="review", span=(0, 1))]

        replacements, updated, summary = recover_final_text_reviews(
            cues, canonical, decisions
        )

        self.assertEqual(replacements[0], "今天风景格外清新")
        self.assertEqual(updated[0].action, "replace")
        self.assertEqual(summary.isomorphic_recovery_count, 1)

    def test_previous_editor_suffix_proves_canonical_prefix_ownership(self) -> None:
        cues = [
            _cue(0, "额外开场 沿着这条路线"),
            _cue(1, "继续向前走"),
        ]
        canonical = [_canonical(0, "沿着这条路线 继续向前走走")]
        decisions = [
            _decision(0, action="review", span=(0, 1)),
            _decision(1, action="review", span=(0, 1)),
        ]

        replacements, updated, summary = recover_final_text_reviews(
            cues, canonical, decisions
        )

        self.assertNotIn(0, replacements)
        self.assertEqual(replacements[1], "继续向前走走")
        self.assertEqual(updated[1].action, "replace")
        self.assertEqual(summary.suffix_ownership_recovery_count, 1)

    def test_does_not_move_unproven_canonical_prefix(self) -> None:
        cues = [_cue(0, "其他文字"), _cue(1, "继续向前走")]
        canonical = [_canonical(0, "沿着这条路线 继续向前走走")]
        decisions = [
            _decision(0, action="review", span=(0, 1)),
            _decision(1, action="review", span=(0, 1)),
        ]

        replacements, updated, summary = recover_final_text_reviews(
            cues, canonical, decisions
        )

        self.assertEqual(replacements, {})
        self.assertEqual(updated[1].action, "review")
        self.assertEqual(summary.suffix_ownership_recovery_count, 0)

    def test_resolved_predecessor_can_confirm_cross_script_vocalization(self) -> None:
        cues = [_cue(0, "继续前行"), _cue(1, "哈咿呀哦")]
        canonical = [_canonical(0, "继续前行"), _canonical(1, "Ha-ya ho")]
        decisions = [
            _decision(0, action="unchanged", span=(0, 1)),
            _decision(1, action="review", span=(1, 2)),
        ]

        replacements, updated, summary = recover_final_text_reviews(
            cues, canonical, decisions
        )

        self.assertEqual(replacements[1], "Ha-ya ho")
        self.assertEqual(updated[1].action, "replace")
        self.assertEqual(
            updated[1].reason,
            "preceding_canonical_anchor_confirms_cross_script_vocalization",
        )
        self.assertEqual(summary.cross_script_vocalization_recovery_count, 1)

    def test_cross_script_vocalization_does_not_accept_lexical_cjk(self) -> None:
        cues = [_cue(0, "继续前行"), _cue(1, "今天好呀")]
        canonical = [_canonical(0, "继续前行"), _canonical(1, "Ha-ya ho")]
        decisions = [
            _decision(0, action="unchanged", span=(0, 1)),
            _decision(1, action="review", span=(1, 2)),
        ]

        replacements, updated, summary = recover_final_text_reviews(
            cues, canonical, decisions
        )

        self.assertNotIn(1, replacements)
        self.assertEqual(updated[1].action, "review")
        self.assertEqual(summary.cross_script_vocalization_recovery_count, 0)


if __name__ == "__main__":
    unittest.main()
