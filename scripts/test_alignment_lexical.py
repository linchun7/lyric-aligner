import unittest

from lyric_aligner.text.alignment_lexical import (
    ALIGNMENT_LEXICAL_ID,
    ALIGNMENT_LEXICAL_REVISION,
    AlignmentLexicalError,
    alignment_units,
    english_units,
    segment_units_from_canonical_segments,
)


class AlignmentLexicalTests(unittest.TestCase):
    def test_english_units_are_deterministic(self):
        self.assertEqual(
            english_units("I'm HERE — 2026!"),
            ["i'm", "here", "2026"],
        )

    def test_segment_preparation_preserves_segment_boundaries(self):
        self.assertEqual(
            segment_units_from_canonical_segments(
                [{"text": "line one"}, {"text": "line two"}],
                language="en",
            ),
            [["line", "one"], ["line", "two"]],
        )

    def test_unsupported_language_fails_closed(self):
        with self.assertRaises(AlignmentLexicalError):
            alignment_units("mixed", "hello 世界")

    def test_identity_is_explicit(self):
        self.assertEqual(ALIGNMENT_LEXICAL_ID, "lyric-alignment-lexical")
        self.assertEqual(ALIGNMENT_LEXICAL_REVISION, "1.0")


if __name__ == "__main__":
    unittest.main()
