import unittest

from lyric_aligner.evidence.editor import (
    build_editor_evidence,
    evidence_for_line,
    phonetic_support_score,
    span_policy,
    text_support_score,
)
from lyric_aligner.srt import Cue


class V4EditorEvidenceTests(unittest.TestCase):
    def line(self, text: str, start=1000, end=2200, index=0):
        return {
            "canonical_line_index": index,
            "text": text,
            "mix_start_ms": start,
            "mix_end_ms": end,
        }

    def test_english_and_mandarin_are_direct_text(self):
        en = span_policy("hello world", track_language="en")
        zh = span_policy("你好世界", track_language="zh")
        self.assertEqual({row["mode"] for row in en}, {"direct_text"})
        self.assertEqual({row["mode"] for row in zh}, {"direct_text"})
        self.assertGreater(text_support_score("hello world", "hello world"), 0.99)

    def test_korean_latin_phonetic_editor_output_is_weak_evidence_only(self):
        score, backend = phonetic_support_score("ko", "안녕", "annyeong")
        self.assertIsNotNone(score)
        self.assertGreater(score, 0.85)
        self.assertEqual(backend, "builtin_hangul_romanization")
        row = evidence_for_line(
            self.line("안녕"),
            [Cue(1, 1050, 2150, "annyeong")],
            track_language="ko",
        )
        best = row["candidates"][0]
        self.assertIsNone(best["direct_text_support_score"])
        self.assertGreater(best["phonetic_support_score"], 0.85)
        self.assertTrue(row["shadow_only"])
        self.assertFalse(row["automatic_timing_change_allowed"])

    def test_japanese_kana_can_use_phonetic_hint_but_kanji_is_not_guessed(self):
        kana, backend = phonetic_support_score("ja", "ありがとう", "arigatou")
        self.assertIsNotNone(kana)
        self.assertGreater(kana, 0.9)
        self.assertEqual(backend, "builtin_kana_romanization")

        kanji, reason = phonetic_support_score("ja", "東京", "tokyo")
        self.assertIsNone(kanji)
        self.assertEqual(reason, "kanji_reading_unavailable")

    def test_cantonese_text_never_becomes_direct_or_phonetic_authority(self):
        policy = span_policy("廣東歌", track_language="yue")
        self.assertEqual({row["mode"] for row in policy}, {"timing_hint"})
        self.assertEqual({row["text_weight"] for row in policy}, {0.0})
        row = evidence_for_line(
            self.line("廣東歌"),
            [Cue(1, 1000, 2200, "廣東歌")],
            track_language="yue",
        )
        best = row["candidates"][0]
        self.assertIsNone(best["text_support_score"])
        self.assertEqual(best["effective_text_weight"], 0.0)
        self.assertGreater(best["timing_support_score"], 0.9)

    def test_mixed_line_routes_english_and_korean_spans_separately(self):
        policy = span_policy("hello 안녕", track_language="ko")
        self.assertEqual([row["language"] for row in policy], ["en", "ko"])
        self.assertEqual([row["mode"] for row in policy], ["direct_text", "phonetic_hint"])

    def test_nearest_supported_editor_cue_ranks_first_but_is_not_applied(self):
        row = evidence_for_line(
            self.line("hello world", start=5000, end=6500),
            [
                Cue(1, 1000, 2000, "hello world"),
                Cue(2, 5050, 6500, "hello world"),
                Cue(3, 5200, 6700, "totally different"),
            ],
            track_language="en",
            search_radius_ms=2500,
            max_candidates=3,
        )
        self.assertEqual(row["best_editor_cue_number"], 2)
        self.assertEqual(row["suggested_onset_delta_ms"], 50)
        self.assertFalse(row["automatic_timing_change_allowed"])

    def test_build_evidence_never_emits_raw_editor_or_canonical_text(self):
        timeline = {
            "result": {
                "occurrence_id": "occ-1",
                "ordinal": 1,
                "track_id": "track-1",
                "language_profile": "en",
                "canonical_selection_sha256": "a" * 64,
                "lines": [self.line("private canonical phrase")],
            }
        }
        result = build_editor_evidence(
            [timeline],
            [Cue(1, 1000, 2200, "private editor phrase")],
        )
        serialized = str(result)
        self.assertNotIn("private canonical phrase", serialized)
        self.assertNotIn("private editor phrase", serialized)
        self.assertEqual(result["mode"], "shadow_only")
        self.assertFalse(result["authority"]["automatic_timing_change_allowed"])


if __name__ == "__main__":
    unittest.main()
