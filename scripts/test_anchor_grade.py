from __future__ import annotations

import unittest

from lyric_aligner.timeline.anchor_repair import (
    TimedCanonicalOccurrence,
    _decision_grade,
)
from lyric_aligner.text_repair import SubtitleCue


def cue(text: str) -> SubtitleCue:
    return SubtitleCue(0, "1", "00:00:01,000 --> 00:00:02,000", text, text.casefold().replace(" ", ""), 0)


def occurrence(text: str) -> TimedCanonicalOccurrence:
    return TimedCanonicalOccurrence(0, "x.lrc", 0, 1000, text, text.casefold().replace(" ", ""))


class AnchorGradeTests(unittest.TestCase):
    def grade(self, action: str, cue_text: str = "Hello world", canonical_text: str = "Hello world") -> str:
        c = cue(cue_text)
        o = occurrence(canonical_text)
        return _decision_grade(
            {"cue_span": [0, 1], "canonical_span": [0, 1], "score": 0.999, "action": action},
            c, o, canonical_count=1, cue_count=1,
        )

    def test_presentation_replace_can_be_a(self):
        self.assertEqual(self.grade("replace", "HELLO WORLD!"), "A")

    def test_lexical_replace_is_not_a(self):
        self.assertEqual(self.grade("replace", "Helo world"), "B")

    def test_repeated_identity_is_not_a(self):
        c = cue("Hello world")
        o = occurrence("Hello world")
        self.assertEqual(_decision_grade({"cue_span": [0, 1], "canonical_span": [0, 1], "score": .999, "action": "unchanged"}, c, o, canonical_count=2, cue_count=1), "C")


if __name__ == "__main__":
    unittest.main()
