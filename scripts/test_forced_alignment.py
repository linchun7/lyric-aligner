import unittest

from lyric_aligner.audio.forced_alignment import (
    ForcedAlignmentEvidenceError,
    alignment_boundary_ms,
    alignment_full_sequence_boundary_ms,
    alignment_internal_boundary_ms,
    build_forced_alignment_evidence_point,
    find_lexical_subsequence,
    padded_window_edge_clamp_reason,
    parse_textgrid_words,
)


TEXTGRID = '''File type = "ooTextFile"
Object class = "TextGrid"

xmin = 0
xmax = 7
item []:
    item [1]:
        class = "IntervalTier"
        name = "words"
        xmin = 0
        xmax = 7
        intervals: size = 8
        intervals [1]:
            xmin = 0
            xmax = 1.0
            text = "SP"
        intervals [2]:
            xmin = 1.0
            xmax = 1.4
            text = "wen"
        intervals [3]:
            xmin = 1.4
            xmax = 1.8
            text = "rou"
        intervals [4]:
            xmin = 1.8
            xmax = 2.0
            text = "de"
        intervals [5]:
            xmin = 2.0
            xmax = 2.8
            text = "ma"
        intervals [6]:
            xmin = 2.8
            xmax = 3.0
            text = "AP"
        intervals [7]:
            xmin = 3.0
            xmax = 3.4
            text = "hei"
        intervals [8]:
            xmin = 3.4
            xmax = 3.8
            text = "ban"
    item [2]:
        class = "IntervalTier"
        name = "phones"
        xmin = 0
        xmax = 7
        intervals: size = 1
        intervals [1]:
            xmin = 0
            xmax = 7
            text = "ignored"
'''


class ForcedAlignmentEvidenceTests(unittest.TestCase):
    def test_textgrid_parser_selects_words_tier_only(self):
        words = parse_textgrid_words(TEXTGRID)
        self.assertEqual(len(words), 8)
        self.assertEqual(words[0].text, "SP")
        self.assertEqual(words[-1].text, "ban")

    def test_nonlexical_sp_ap_do_not_break_target_match(self):
        words = parse_textgrid_words(TEXTGRID)
        matched = find_lexical_subsequence(
            words, ["wen", "rou", "de", "ma", "hei", "ban"]
        )
        self.assertEqual([word.text for word in matched], ["wen", "rou", "de", "ma", "hei", "ban"])
        self.assertAlmostEqual(matched[4].start_s, 3.0)

    def test_absolute_start_and_end_are_derived_from_lexical_words(self):
        words = parse_textgrid_words(TEXTGRID)
        self.assertEqual(
            alignment_boundary_ms(words, window_start_ms=10000, boundary_kind="start"),
            11000,
        )
        self.assertEqual(
            alignment_boundary_ms(words, window_start_ms=10000, boundary_kind="end"),
            13800,
        )

    def test_target_subsequence_boundary_ignores_surrounding_alignment(self):
        words = parse_textgrid_words(TEXTGRID)
        target = ["de", "ma", "hei"]
        self.assertEqual(
            alignment_boundary_ms(
                words,
                window_start_ms=10000,
                boundary_kind="start",
                target_tokens=target,
            ),
            11800,
        )
        self.assertEqual(
            alignment_boundary_ms(
                words,
                window_start_ms=10000,
                boundary_kind="end",
                target_tokens=target,
            ),
            13400,
        )

    def test_ambiguous_target_fails_closed(self):
        words = [
            {"start": 0.0, "end": 0.2, "text": "la"},
            {"start": 0.2, "end": 0.4, "text": "la"},
            {"start": 0.4, "end": 0.6, "text": "la"},
        ]
        with self.assertRaises(ForcedAlignmentEvidenceError):
            find_lexical_subsequence(words, ["la"])

    def test_evidence_is_diagnostic_until_calibrated_elsewhere(self):
        point = build_forced_alignment_evidence_point(
            words=parse_textgrid_words(TEXTGRID),
            window_start_ms=10000,
            boundary_kind="start",
            family="final_mix_singing_alignment",
            correlation_group="sofa-mandarin-v1",
            backend_id="sofa_onnx",
            backend_version="1.0",
            model_id="mandarin_singing",
            model_revision="model-sha",
            language="zh",
            raw_confidence=0.4032541849,
        )
        self.assertEqual(point["boundary_ms"], 11000)
        self.assertAlmostEqual(point["raw_confidence"], 0.4032541849)
        self.assertFalse(point["calibration_passed"])
        self.assertEqual(point["calibration_sha256"], "")

    def test_internal_boundary_uses_full_segment_sequence_not_substring_search(self):
        words = [
            {"start": 0.0, "end": 0.1, "text": "SP"},
            {"start": 0.1, "end": 0.3, "text": "wo"},
            {"start": 0.3, "end": 0.5, "text": "ai"},
            {"start": 0.5, "end": 0.55, "text": "BND"},
            {"start": 0.55, "end": 0.75, "text": "wo"},
            {"start": 0.75, "end": 0.95, "text": "ai"},
            {"start": 0.95, "end": 1.0, "text": "AP"},
        ]
        self.assertEqual(
            alignment_internal_boundary_ms(
                words,
                window_start_ms=10000,
                segment_tokens=[["wo", "ai"], ["wo", "ai"]],
                boundary_index=1,
            ),
            10550,
        )

    def test_internal_boundary_rejects_lexical_insertion_or_deletion(self):
        words = [
            {"start": 0.1, "end": 0.3, "text": "wo"},
            {"start": 0.3, "end": 0.5, "text": "ai"},
            {"start": 0.5, "end": 0.7, "text": "extra"},
            {"start": 0.7, "end": 0.9, "text": "ni"},
        ]
        with self.assertRaises(ForcedAlignmentEvidenceError):
            alignment_internal_boundary_ms(
                words,
                window_start_ms=0,
                segment_tokens=[["wo", "ai"], ["ni"]],
                boundary_index=1,
            )

    def test_full_sequence_boundary_supports_all_three_scopes(self):
        words = [
            {"start": 0.0, "end": 0.1, "text": "SP"},
            {"start": 0.1, "end": 0.3, "text": "wo"},
            {"start": 0.3, "end": 0.5, "text": "ai"},
            {"start": 0.5, "end": 0.6, "text": "BND"},
            {"start": 0.6, "end": 0.8, "text": "ni"},
            {"start": 0.8, "end": 1.0, "text": "ya"},
            {"start": 1.0, "end": 1.1, "text": "AP"},
        ]
        segments = [["wo", "ai"], ["ni", "ya"]]
        self.assertEqual(
            alignment_full_sequence_boundary_ms(
                words,
                window_start_ms=10000,
                segment_tokens=segments,
                boundary_kind="start",
            ),
            10100,
        )
        self.assertEqual(
            alignment_full_sequence_boundary_ms(
                words,
                window_start_ms=10000,
                segment_tokens=segments,
                boundary_kind="end",
            ),
            11000,
        )
        self.assertEqual(
            alignment_full_sequence_boundary_ms(
                words,
                window_start_ms=10000,
                segment_tokens=segments,
                boundary_kind="internal",
                boundary_index=1,
            ),
            10600,
        )

    def test_full_sequence_boundary_rejects_partial_alignment(self):
        words = [
            {"start": 0.1, "end": 0.3, "text": "wo"},
            {"start": 0.3, "end": 0.5, "text": "ai"},
        ]
        with self.assertRaises(ForcedAlignmentEvidenceError):
            alignment_full_sequence_boundary_ms(
                words,
                window_start_ms=0,
                segment_tokens=[["wo", "ai", "ni"]],
                boundary_kind="end",
            )

    def test_padded_window_edge_clamp_rejects_only_far_outer_edge_predictions(self):
        self.assertEqual(
            padded_window_edge_clamp_reason(
                10020,
                mix_window_ms=[10000, 20000],
                row_interval_ms=[11500, 18500],
                boundary_kind="start",
            ),
            "padded_window_edge_clamped_start",
        )
        self.assertEqual(
            padded_window_edge_clamp_reason(
                19980,
                mix_window_ms=[10000, 20000],
                row_interval_ms=[11500, 18500],
                boundary_kind="end",
            ),
            "padded_window_edge_clamped_end",
        )
        self.assertIsNone(
            padded_window_edge_clamp_reason(
                11000,
                mix_window_ms=[10000, 20000],
                row_interval_ms=[11500, 18500],
                boundary_kind="start",
            )
        )
        self.assertIsNone(
            padded_window_edge_clamp_reason(
                10020,
                mix_window_ms=[10000, 20000],
                row_interval_ms=[10040, 18500],
                boundary_kind="start",
            )
        )

    def test_padded_window_edge_clamp_fails_closed_on_invalid_intervals(self):
        with self.assertRaises(ForcedAlignmentEvidenceError):
            padded_window_edge_clamp_reason(
                10000,
                mix_window_ms=[11000, 20000],
                row_interval_ms=[10000, 18500],
                boundary_kind="start",
            )


if __name__ == "__main__":
    unittest.main()
