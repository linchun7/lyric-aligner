from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner.text_repair import (
    CanonicalLine,
    parse_canonical_files,
    parse_srt_text,
    repair_srt_text,
    timeline_signature,
)


def canonical(*lines: str) -> list[CanonicalLine]:
    from lyric_aligner.text_repair import _normalize_for_match
    return [
        CanonicalLine(i, "song.lrc", text, _normalize_for_match(text))
        for i, text in enumerate(lines)
    ]


class TextRepairV2Tests(unittest.TestCase):
    def assert_timeline_unchanged(self, source: str, output: str) -> None:
        before = parse_srt_text(source)[1]
        after = parse_srt_text(output)[1]
        self.assertEqual(len(before), len(after))
        self.assertEqual(timeline_signature(before), timeline_signature(after))

    def test_missing_character_is_inserted_without_timing_change(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\n我真的爱\n"
        output, report = repair_srt_text(source, canonical("我真的爱你"))
        self.assertEqual(
            output,
            "1\n00:00:01,000 --> 00:00:02,000\n我真的爱你\n",
        )
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

    def test_three_editor_cues_can_match_one_canonical_line_when_near_exact(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n我曾经\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n跨过山\n\n"
            "3\n00:00:03,000 --> 00:00:04,000\n和大海\n"
        )
        output, report = repair_srt_text(source, canonical("我曾经跨过山和大海"))
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["segmentation_span_count"], 1)
        self.assert_timeline_unchanged(source, output)

    def test_one_editor_cue_can_match_two_canonical_lines(self):
        source = "1\n00:00:01,000 --> 00:00:03,000\n我曾经跨过山和大海也穿过人山人海\n"
        output, report = repair_srt_text(
            source,
            canonical("我曾经跨过山和大海", "也穿过人山人海"),
        )
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["segmentation_span_count"], 1)
        self.assert_timeline_unchanged(source, output)

    def test_one_editor_cue_can_match_three_canonical_lines_when_near_exact(self):
        source = (
            "1\n00:00:01,000 --> 00:00:04,000\n"
            "我曾经跨过山和大海也穿过人山人海看过许多风景\n"
        )
        output, report = repair_srt_text(
            source,
            canonical(
                "我曾经跨过山和大海",
                "也穿过人山人海",
                "看过许多风景",
            ),
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
            source,
            canonical("我曾经跨过山和大海", "也穿过人山人海"),
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
            source,
            canonical("我曾经跨过山和大海", "也穿过人山人海"),
        )
        self.assertIn("我曾经跨过山", output)
        self.assertNotIn("杉", output)
        self.assertEqual(report["status"], "ready")
        self.assert_timeline_unchanged(source, output)

    def test_missing_character_at_existing_cue_boundary_requires_review(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n我真的爱\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n你\n"
        )
        output, report = repair_srt_text(source, canonical("我真的爱着你"))
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["cue_review_count"], 2)
        self.assertTrue(
            all(
                item["reason"] == "segmentation_boundary_insertion_requires_review"
                for item in report["decisions"]
            )
        )
        self.assert_timeline_unchanged(source, output)

    def test_missing_character_inside_existing_cue_can_still_auto_repair(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n我真爱\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n你\n"
        )
        output, report = repair_srt_text(source, canonical("我真的爱你"))
        self.assertIn("我真的爱", output)
        self.assertEqual(report["status"], "ready")
        self.assertGreater(report["edit_counts"]["insert"], 0)
        self.assert_timeline_unchanged(source, output)

    def test_punctuation_line_breaks_and_music_marks_survive_insert(self):
        source = "1\n00:00:01,000 --> 00:00:03,000\n♪ 你，真\n的爱 ♪\n"
        output, report = repair_srt_text(source, canonical("你真的很爱"))
        self.assertEqual(
            output,
            "1\n00:00:01,000 --> 00:00:03,000\n♪ 你，真\n的很爱 ♪\n",
        )
        self.assertEqual(report["status"], "ready")
        self.assert_timeline_unchanged(source, output)

    def test_real_missing_canonical_line_is_not_swallowed_by_span(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n第一句\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n第三句\n"
        )
        output, report = repair_srt_text(
            source,
            canonical("第一句", "第二句", "第三句"),
        )
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["unmatched_canonical_count"], 1)
        self.assertEqual(report["unmatched_canonical"][0]["text"], "第二句")
        self.assert_timeline_unchanged(source, output)

    def test_duplicate_basenames_do_not_merge_canonical_song_boundaries(self):
        source = (
            "1\n00:00:01,000 --> 00:00:03,000\n"
            "第一首最后一句第二首第一句\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            first = first_dir / "song.lrc"
            second = second_dir / "song.lrc"
            first.write_text("第一首最后一句\n", encoding="utf-8")
            second.write_text("第二首第一句\n", encoding="utf-8")
            parsed = parse_canonical_files([first, second])
            output, report = repair_srt_text(source, parsed)

        self.assertEqual([line.source for line in parsed], ["song.lrc", "song.lrc"])
        self.assertEqual([line.source_ordinal for line in parsed], [0, 1])
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["segmentation_span_count"], 0)
        self.assert_timeline_unchanged(source, output)

    def test_lrc_timestamp_spacing_does_not_affect_text_repair(self):
        source = (
            "1\n00:00:10,000 --> 00:00:11,000\n这是第一巨歌词\n\n"
            "2\n00:00:11,000 --> 00:00:12,000\n这是第二句歌词\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            normal = root / "normal.lrc"
            faster = root / "faster.lrc"
            normal.write_text(
                "[00:10.00]这是第一句歌词\n[00:20.00]这是第二句歌词\n",
                encoding="utf-8",
            )
            faster.write_text(
                "[00:10.00]这是第一句歌词\n[00:15.00]这是第二句歌词\n",
                encoding="utf-8",
            )
            normal_output, normal_report = repair_srt_text(
                source,
                parse_canonical_files([normal]),
            )
            faster_output, faster_report = repair_srt_text(
                source,
                parse_canonical_files([faster]),
            )

        self.assertEqual(normal_output, faster_output)
        self.assertIn("这是第一句歌词", normal_output)
        self.assertEqual(normal_report["status"], "ready")
        self.assertEqual(faster_report["status"], "ready")
        self.assert_timeline_unchanged(source, normal_output)


if __name__ == "__main__":
    unittest.main()
