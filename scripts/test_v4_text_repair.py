from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lyric_aligner.text_repair import (
    parse_canonical_files,
    parse_srt_text,
    repair_srt_text,
    timeline_signature,
)


class V4TextRepairTests(unittest.TestCase):
    def test_repairs_chinese_typo_and_preserves_timeline(self):
        source = (
            "1\r\n00:00:01,000 --> 00:00:03,500\r\n忘不掉的妳\r\n\r\n"
            "2\r\n00:00:04,000 --> 00:00:06,000\r\n还在我心里\r\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            lrc = Path(directory) / "song.lrc"
            lrc.write_text(
                "[00:01.00]忘不掉的你\n[00:04.00]还在我心里\n",
                encoding="utf-8",
            )
            canonical = parse_canonical_files([lrc])
            before = timeline_signature(parse_srt_text(source)[1])
            output, report = repair_srt_text(source, canonical)
            after = timeline_signature(parse_srt_text(output)[1])

        self.assertEqual(before, after)
        self.assertIn("忘不掉的你", output)
        self.assertIn("00:00:01,000 --> 00:00:03,500", output)
        self.assertIn("\r\n", output)
        self.assertEqual(report["replacement_count"], 1)
        self.assertEqual(report["review_count"], 0)
        self.assertTrue(report["timeline_unchanged"])

    def test_multiple_lrc_timestamps_duplicate_canonical_occurrence(self):
        with tempfile.TemporaryDirectory() as directory:
            lrc = Path(directory) / "repeat.lrc"
            lrc.write_text(
                "[00:01.00][00:20.00]副歌一句\n",
                encoding="utf-8",
            )
            canonical = parse_canonical_files([lrc])

        self.assertEqual(
            [line.text for line in canonical],
            ["副歌一句", "副歌一句"],
        )

    def test_qrc_token_timing_is_removed_from_canonical_text(self):
        with tempfile.TemporaryDirectory() as directory:
            qrc = Path(directory) / "song.qrc"
            qrc.write_text(
                "[1000,2000]你(1000,500)好(1500,500)\n",
                encoding="utf-8",
            )
            canonical = parse_canonical_files([qrc])

        self.assertEqual([line.text for line in canonical], ["你好"])

    def test_ambiguous_or_bad_length_is_left_unchanged_for_review(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\n"
            "完全不同的一整句字幕\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            lrc = Path(directory) / "song.lrc"
            lrc.write_text("[00:01.00]你好\n", encoding="utf-8")
            canonical = parse_canonical_files([lrc])
            output, report = repair_srt_text(source, canonical)

        self.assertEqual(output, source)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["review_count"], 1)

    def test_near_tie_similar_lyric_lines_fail_closed(self):
        source = (
            "1\n00:00:01,000 --> 00:00:03,000\n"
            "今夜我真的很想你\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            lrc = Path(directory) / "song.lrc"
            lrc.write_text(
                "[00:01.00]今夜我真的很爱你\n"
                "[00:02.00]今夜我真的很念你\n",
                encoding="utf-8",
            )
            canonical = parse_canonical_files([lrc])
            output, report = repair_srt_text(source, canonical)

        self.assertEqual(output, source)
        self.assertEqual(report["replacement_count"], 0)
        self.assertEqual(report["status"], "review_required")
        self.assertEqual(report["cue_review_count"], 1)
        self.assertEqual(report["review_count"], 1)
        self.assertEqual(report["coverage_status"], "warning")
        self.assertEqual(report["coverage_warning_count"], 1)
        self.assertEqual(report["unmatched_canonical_count"], 1)
        self.assertIn(
            report["decisions"][0]["reason"],
            {"ambiguous_nearby_canonical_match", "adjacent_alignment_gap_requires_review"},
        )

    def test_metadata_and_enhanced_tags_do_not_become_text(self):
        with tempfile.TemporaryDirectory() as directory:
            lrc = Path(directory) / "song.lrc"
            lrc.write_text(
                "[ar:歌手]\n[ti:歌名]\n作词: 某某\n词：某某\n"
                "[00:01.00]<00:01.00>你<00:01.50>好\n",
                encoding="utf-8",
            )
            canonical = parse_canonical_files([lrc])

        self.assertEqual([line.text for line in canonical], ["你好"])

    def test_exact_text_needs_no_replacement(self):
        source = (
            "1\n00:00:01,000 --> 00:00:02,000\nHello, world!\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            lyric = Path(directory) / "song.txt"
            lyric.write_text("Hello, world!\n", encoding="utf-8")
            canonical = parse_canonical_files([lyric])
            output, report = repair_srt_text(source, canonical)

        self.assertEqual(output, source)
        self.assertEqual(report["replacement_count"], 0)
        self.assertEqual(report["unchanged_count"], 1)

    def test_replacement_preserves_terminal_newline(self):
        source = "1\n00:00:01,000 --> 00:00:02,000\n忘不掉的妳\n"
        with tempfile.TemporaryDirectory() as directory:
            lyric = Path(directory) / "song.lrc"
            lyric.write_text("[00:01.00]忘不掉的你\n", encoding="utf-8")
            canonical = parse_canonical_files([lyric])
            output, report = repair_srt_text(source, canonical)

        self.assertTrue(output.endswith("\n"))
        self.assertEqual(
            output,
            "1\n00:00:01,000 --> 00:00:02,000\n忘不掉的你\n",
        )
        self.assertEqual(report["replacement_count"], 1)

    def test_inserted_canonical_line_does_not_move_srt_timeline(self):
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
            before = timeline_signature(parse_srt_text(source)[1])
            output, report = repair_srt_text(source, canonical)
            after = timeline_signature(parse_srt_text(output)[1])

        self.assertEqual(before, after)
        self.assertEqual(output, source)
        self.assertEqual(report["replacement_count"], 0)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["review_count"], 0)
        self.assertEqual(report["coverage_status"], "warning")
        self.assertEqual(report["coverage_warning_count"], 1)
        self.assertEqual(report["unmatched_canonical_count"], 1)


if __name__ == "__main__":
    unittest.main()
