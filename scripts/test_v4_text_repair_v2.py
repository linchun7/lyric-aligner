from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner.text_repair import (
    CanonicalLine,
    SubtitleCue,
    _normalize_for_match,
    _unique_exact_anchors,
    parse_canonical_files,
    parse_srt_text,
    repair_srt_text,
    timeline_signature,
)


def canonical(*lines: str) -> list[CanonicalLine]:
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

    def test_missing_character_at_space_boundary_requires_review(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\nI lov you\n"
        output, report = repair_srt_text(source, canonical("I love you"))
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["cue_review_count"], 1)
        self.assertEqual(
            report["decisions"][0]["reason"],
            "layout_boundary_insertion_requires_review",
        )
        self.assert_timeline_unchanged(source, output)

    def test_missing_character_at_line_break_boundary_requires_review(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\nI lov\nyou\n"
        output, report = repair_srt_text(source, canonical("I love you"))
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(
            report["decisions"][0]["reason"],
            "layout_boundary_insertion_requires_review",
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

    def test_real_missing_canonical_line_is_coverage_warning_not_cue_failure(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n第一句\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n第三句\n"
        )
        output, report = repair_srt_text(
            source,
            canonical("第一句", "第二句", "第三句"),
        )
        self.assertEqual(output, source)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["coverage_status"], "warning")
        self.assertEqual(report["review_count"], 0)
        self.assertEqual(report["coverage_warning_count"], 1)
        self.assertEqual(report["unmatched_canonical_count"], 1)
        self.assertEqual(report["unmatched_canonical"][0]["text"], "第二句")
        self.assert_timeline_unchanged(source, output)

    def test_timestamped_metadata_is_filtered_after_timestamp_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            lyric = Path(directory) / "song.lrc"
            lyric.write_text(
                "[00:00.00]作词：某某\n"
                "[00:01.00]作曲：某某\n"
                "[00:10.00]真正的歌词\n",
                encoding="utf-8",
            )
            parsed = parse_canonical_files([lyric])
        self.assertEqual([line.text for line in parsed], ["真正的歌词"])

    def test_mixed_timed_and_untimed_lyric_text_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            lyric = Path(directory) / "song.lrc"
            lyric.write_text(
                "[00:10.00]第一句歌词\n"
                "这是一条没有时间戳的正文\n"
                "[00:20.00]第二句歌词\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "mixes timed and untimed lyric text"):
                parse_canonical_files([lyric])

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

    def test_unique_exact_anchor_chain_scales_to_two_thousand_cues(self):
        cue_rows: list[SubtitleCue] = []
        canonical_rows: list[CanonicalLine] = []
        for index in range(2000):
            text = f"第{index:04d}句歌词内容"
            normalized = _normalize_for_match(text)
            cue_rows.append(
                SubtitleCue(
                    ordinal=index,
                    number=str(index + 1),
                    timing="00:00:00,000 --> 00:00:00,001",
                    text=text,
                    normalized=normalized,
                    raw_block_index=index * 2,
                )
            )
            canonical_rows.append(
                CanonicalLine(index, "long.lrc", text, normalized)
            )
        anchors = _unique_exact_anchors(cue_rows, canonical_rows)
        self.assertEqual(len(anchors), 2000)
        self.assertEqual(anchors[0], (0, 0))
        self.assertEqual(anchors[-1], (1999, 1999))


if __name__ == "__main__":
    unittest.main()