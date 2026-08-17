import unittest

from lyric_aligner.text.language_spans import editor_mode_for_span, language_spans


class V4LanguageSpanTests(unittest.TestCase):
    def test_korean_english_line_gets_span_specific_editor_modes(self):
        spans = language_spans("널 사랑해 baby come back", track_language="ko")
        self.assertEqual([span.language for span in spans], ["ko", "en"])
        self.assertEqual(
            [editor_mode_for_span(span) for span in spans],
            ["phonetic_hint", "direct_text"],
        )

    def test_japanese_english_line(self):
        spans = language_spans("君が好き baby", track_language="ja")
        self.assertEqual([span.language for span in spans], ["ja", "en"])

    def test_cantonese_han_stays_yue_but_embedded_latin_can_use_english_editor_text(self):
        spans = language_spans("我鍾意你 forever", track_language="yue")
        self.assertEqual([span.language for span in spans], ["yue", "en"])
        self.assertEqual(editor_mode_for_span(spans[0]), "timing_hint")
        self.assertEqual(editor_mode_for_span(spans[1]), "direct_text")

    def test_unknown_latin_song_is_not_silently_called_english(self):
        spans = language_spans("aku cinta kamu", track_language="auto")
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].language, "generic")
        self.assertEqual(editor_mode_for_span(spans[0]), "timing_hint")

    def test_unknown_han_is_not_silently_called_mandarin(self):
        spans = language_spans("未知漢字", track_language="auto")
        self.assertEqual(spans[0].language, "und-han")


if __name__ == "__main__":
    unittest.main()
