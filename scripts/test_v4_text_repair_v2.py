from __future__ import annotations

import unittest

from lyric_aligner.text_repair import CanonicalLine, parse_srt_text, repair_srt_text, timeline_signature


def canonical(*lines: str) -> list[CanonicalLine]:
    from lyric_aligner.text_repair import _normalize_for_match
    return [CanonicalLine(i, "song.lrc", text, _normalize_for_match(text)) for i, text in enumerate(lines)]


class TextRepairV2Tests(unittest.TestCase):
    def assert_timeline_unchanged(self, source: str, output: str) -> None:
        before = parse_srt_text(source)[1]
        after = parse_srt_text(output)[1]
        self.assertEqual(len(before), len(after))
        self.assertEqual(timeline_signature(before), timeline_signature(after))

    def test_missing_character_is_inserted_without_timing_change(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\n我真的爱\n"
        output, report = repair_srt_text(source, canonical("我真的爱你"))
        self.assertEqual(output, "1\n00:00:01,000 --> 00:00:02,000\n我真的爱你\n")
        self.assertEqual(report["status"], "ready")
        self.assertGreater(report["edit_counts"]["insert"], 0)
        self.assert_timeline_unchanged(source, output)

    def test_extra_character_is_deleted_without_timing_change(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\n我我真的爱你\n"
        output, report = repair_srt_text(source, canonical("我真的爱你"))
        self.assertIn("我真的爱你", output)
        self.assertGreater(report["edit_counts"]["delete"], 0)
        self.assert_timeline_unchanged(source, output)

    def test_two_editor_cues_can_match_one_canonical_line(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n我曾经跨过\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n山和大海\n"
        )
        output, report = repair_srt_text(source, canonical("我曾经跨过山和大海"))
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["segmentation_span_count"], 1)
        self.assert_timeline_unchanged(source, output)

    def test_one_editor_cue_can_match_two_canonical_lines(self):
        source = "1\n00:00:01,000 --> 00:00:03,000\n我曾经跨过山和大海也穿过人山人海\n"
        output, report = repair_srt_text(
            source, canonical("我曾经跨过山和大海", "也穿过人山人海")
        )
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["segmentation_span_count"], 1)
        self.assert_timeline_unchanged(source, output)

    def test_different_two_by_two_line_breaks_keep_editor_boundaries(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n我曾经跨过山\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n和大海也穿过人山人海\n"
        )
        output, report = repair_srt_text(
            source, canonical("我曾经跨过山和大海", "也穿过人山人海")
        )
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["segmentation_span_count"], 1)
        self.assert_timeline_unchanged(source, output)

    def test_typo_inside_segmentation_mismatch_is_repaired_in_place(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n我曾经跨过杉\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n和大海也穿过人山人海\n"
        )
        output, report = repair_srt_text(
            source, canonical("我曾经跨过山和大海", "也穿过人山人海")
        )
        self.assertIn("我曾经跨过山", output)
        self.assertNotIn("杉", output)
        self.assertEqual(report["status"], "ready")
        self.assert_timeline_unchanged(source, output)

    def test_punctuation_line_breaks_and_music_marks_survive_insert(self):
        source = "1\n00:00:01,000 --> 00:00:03,000\n♪ 你，真\n的爱 ♪\n"
        output, report = repair_srt_text(source, canonical("你真的很爱"))
        self.assertEqual(output, "1\n00:00:01,000 --> 00:00:03,000\n♪ 你，真\n的很爱 ♪\n")
        self.assertEqual(report["status"], "ready")
        self.assert_timeline_unchanged(source, output)

    def test_real_missing_canonical_line_is_not_swallowed_by_span(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n第一句\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n第三句\n"
        )
        output, report = repair_srt_text(source, canonical("第一句", "第二句", "第三句"))
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["unmatched_canonical_count"], 1)
        self.assertEqual(report["unmatched_canonical"][0]["text"], "第二句")
        self.assert_timeline_unchanged(source, output)


if __name__ == "__main__":
    unittest.main()
