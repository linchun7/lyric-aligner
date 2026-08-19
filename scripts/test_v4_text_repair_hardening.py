from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner.text_repair import parse_canonical_files, repair_srt_text


class V4TextRepairHardeningTests(unittest.TestCase):
    def test_missing_canonical_line_requires_review(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n第一句\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n第三句\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            lyric = Path(directory) / "song.lrc"
            lyric.write_text(
                "[00:01.00]第一句\n"
                "[00:02.00]第二句\n"
                "[00:03.00]第三句\n",
                encoding="utf-8",
            )
            canonical = parse_canonical_files([lyric])
            output, report = repair_srt_text(source, canonical)

        self.assertEqual(output, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["unmatched_canonical_count"], 1)
        self.assertEqual(report["review_count"], 1)
        self.assertEqual(report["unmatched_canonical"][0]["text"], "第二句")

    def test_multiple_lrc_timestamps_are_sorted_by_occurrence_time(self):
        with tempfile.TemporaryDirectory() as directory:
            lyric = Path(directory) / "repeat.lrc"
            lyric.write_text(
                "[00:10.00][00:30.00]副歌\n"
                "[00:20.00]主歌\n",
                encoding="utf-8",
            )
            canonical = parse_canonical_files([lyric])

        self.assertEqual([line.text for line in canonical], ["副歌", "主歌", "副歌"])

    def test_multiple_files_keep_song_order_when_timestamps_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.lrc"
            second = Path(directory) / "second.lrc"
            first.write_text("[00:30.00]第一首\n", encoding="utf-8")
            second.write_text("[00:10.00]第二首\n", encoding="utf-8")
            canonical = parse_canonical_files([first, second])

        self.assertEqual([line.text for line in canonical], ["第一首", "第二首"])

    def test_word_fix_preserves_source_punctuation_spacing_and_line_breaks(self):
        source = "1\n00:00:01,000 --> 00:00:03,000\n你，真\n的号\n"
        with tempfile.TemporaryDirectory() as directory:
            lyric = Path(directory) / "song.lrc"
            lyric.write_text("[00:01.00]你真的好\n", encoding="utf-8")
            canonical = parse_canonical_files([lyric])
            output, report = repair_srt_text(source, canonical)

        self.assertEqual(
            output,
            "1\n00:00:01,000 --> 00:00:03,000\n你，真\n的好\n",
        )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["replacement_count"], 1)

    def test_punctuation_only_difference_does_not_reformat_source(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\nHello world\n"
        with tempfile.TemporaryDirectory() as directory:
            lyric = Path(directory) / "song.txt"
            lyric.write_text("Hello, world!\n", encoding="utf-8")
            canonical = parse_canonical_files([lyric])
            output, report = repair_srt_text(source, canonical)

        self.assertEqual(output, source)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["replacement_count"], 0)
        self.assertEqual(report["unchanged_count"], 1)

    def test_safe_missing_character_is_inserted(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\n我真的爱\n"
        with tempfile.TemporaryDirectory() as directory:
            lyric = Path(directory) / "song.lrc"
            lyric.write_text("[00:01.00]我真的爱你\n", encoding="utf-8")
            canonical = parse_canonical_files([lyric])
            output, report = repair_srt_text(source, canonical)

        self.assertEqual(output, "1\n00:00:01,000 --> 00:00:02,000\n我真的爱你\n")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["replacement_count"], 1)
        self.assertGreater(report["edit_counts"]["insert"], 0)

    def test_large_structural_mismatch_still_fails_closed(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\n我爱\n"
        with tempfile.TemporaryDirectory() as directory:
            lyric = Path(directory) / "song.lrc"
            lyric.write_text("[00:01.00]今天晚上我真的非常爱你\n", encoding="utf-8")
            canonical = parse_canonical_files([lyric])
            output, report = repair_srt_text(source, canonical)

        self.assertEqual(output, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["replacement_count"], 0)


if __name__ == "__main__":
    unittest.main()
