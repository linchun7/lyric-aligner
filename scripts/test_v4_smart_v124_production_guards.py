from __future__ import annotations

import unittest

from lyric_aligner.timeline.bpm_sequence_reconcile import _is_unmapped_span
from lyric_aligner.timeline.smart_policy import SMART_POLICY_ID
from lyric_aligner.text_repair import MatchDecision


class SmartV124ProductionGuardTests(unittest.TestCase):
    def test_policy_id_is_v124(self) -> None:
        self.assertEqual(
            SMART_POLICY_ID,
            "smart-validation-policy-2026-08-21-v1.2.4",
        )

    def test_zero_width_span_is_unmapped(self) -> None:
        item = MatchDecision(
            cue_ordinal=0,
            canonical_ordinal=None,
            score=0.0,
            action="review",
            reason="unmatched_subtitle_cue",
            cue_span=(0, 1),
            canonical_span=(3, 3),
            source_text="uncertain",
            canonical_text="",
            output_text="uncertain",
            edit_operations=(),
        )
        self.assertTrue(_is_unmapped_span(item))


if __name__ == "__main__":
    unittest.main()
