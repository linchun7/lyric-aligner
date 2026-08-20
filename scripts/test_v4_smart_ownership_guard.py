from __future__ import annotations

import unittest

from lyric_aligner.text_repair import MatchDecision, parse_srt_text
from lyric_aligner.timeline.ownership_guard import restore_editor_cue_ownership


def _srt(left: str, right: str) -> str:
    return (
        f"1\n00:00:00,000 --> 00:00:01,000\n{left}\n\n"
        f"2\n00:00:01,000 --> 00:00:02,000\n{right}\n"
    )


def _decision(cue: int, text: str, *, reason: str) -> MatchDecision:
    return MatchDecision(
        cue_ordinal=cue,
        canonical_ordinal=cue,
        score=0.8,
        action="replace",
        reason=reason,
        cue_span=(cue, cue + 1),
        canonical_span=(cue, cue + 1),
        source_text=text,
        canonical_text=text,
        output_text=text,
        edit_operations=(),
    )


class OwnershipGuardTests(unittest.TestCase):
    def test_drops_duplicate_boundary_fragment_from_right_cue(self) -> None:
        _, cues = parse_srt_text(_srt("前句末尾呼吸", "天窗玻璃打开"))
        decisions = [
            _decision(0, cues[0].text, reason="baseline_strong"),
            _decision(1, cues[1].text, reason="sequence_projection_confirms_frontier"),
        ]
        replacements = {0: "前句末尾呼吸", 1: "呼吸天窗玻璃打开"}
        changed, updated, count = restore_editor_cue_ownership(cues, decisions, replacements)
        self.assertEqual(count, 1)
        self.assertEqual(changed[0], "前句末尾呼吸")
        self.assertEqual(changed[1], "天窗玻璃打开")
        self.assertEqual(updated[1].reason, "editor_boundary_ownership_restored")

    def test_moves_recognized_prefix_back_to_right_cue(self) -> None:
        _, cues = parse_srt_text(_srt("纷纷绵绵", "谁人怜在柳边"))
        decisions = [
            _decision(0, cues[0].text, reason="sequence_projection_confirms_frontier"),
            _decision(1, cues[1].text, reason="baseline_review"),
        ]
        replacements = {0: "纷纷绵绵谁人怜", 1: "在柳边"}
        changed, _, count = restore_editor_cue_ownership(cues, decisions, replacements)
        self.assertEqual(count, 1)
        self.assertEqual(changed[0], "纷纷绵绵")
        self.assertEqual(changed[1], "谁人怜在柳边")

    def test_does_not_rewrite_unrelated_pair(self) -> None:
        _, cues = parse_srt_text(_srt("前一句", "后一句"))
        decisions = [
            _decision(0, cues[0].text, reason="baseline_strong"),
            _decision(1, cues[1].text, reason="baseline_strong"),
        ]
        changed, _, count = restore_editor_cue_ownership(
            cues,
            decisions,
            {0: "完全不同", 1: "另一句"},
        )
        self.assertEqual(count, 0)
        self.assertEqual(changed, {})


if __name__ == "__main__":
    unittest.main()
