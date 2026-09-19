from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.qa.lexical_floor import audit_resolved_lexical_floor


CANONICAL_FIELDS = ["occurrence_id", "canonical_line_index", "text"]
FINAL_FIELDS = [
    "position",
    "cue_number",
    "text",
    "occurrence_id",
    "canonical_line_index",
    "canonical_line_indices",
    "canonical_content_start",
    "canonical_content_end",
]


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class ProductionLexicalFloorTests(unittest.TestCase):
    def test_split_merge_character_coverage_can_be_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical.csv"
            final_audit = root / "final.csv"
            final_srt = root / "final.srt"
            write_csv(
                canonical,
                CANONICAL_FIELDS,
                [
                    {"occurrence_id": "occ1", "canonical_line_index": 0, "text": "ABC"},
                    {"occurrence_id": "occ1", "canonical_line_index": 1, "text": "DEF"},
                ],
            )
            write_csv(
                final_audit,
                FINAL_FIELDS,
                [
                    {
                        "position": 1,
                        "cue_number": 1,
                        "text": "AB",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 0,
                        "canonical_line_indices": "[0]",
                        "canonical_content_start": 0,
                        "canonical_content_end": 2,
                    },
                    {
                        "position": 2,
                        "cue_number": 2,
                        "text": "CDEF",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 0,
                        "canonical_line_indices": "[0, 1]",
                        "canonical_content_start": 2,
                        "canonical_content_end": 6,
                    },
                ],
            )
            final_srt.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nAB\n\n"
                "2\n00:00:02,000 --> 00:00:03,000\nCDEF\n",
                encoding="utf-8",
            )
            report = audit_resolved_lexical_floor(
                canonical_evaluation_audit=canonical,
                final_srt=final_srt,
                final_audit=final_audit,
            )
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["canonical_character_count"], 6)
            self.assertEqual(report["covered_character_count"], 6)
            self.assertEqual(report["lexical_error_count"], 0)
            self.assertEqual(report["coverage_gap_count"], 0)
            self.assertEqual(report["coverage_overlap_count"], 0)

    def test_wrong_word_fails_even_when_ownership_span_is_geometrically_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical.csv"
            final_audit = root / "final.csv"
            final_srt = root / "final.srt"
            write_csv(
                canonical,
                CANONICAL_FIELDS,
                [{"occurrence_id": "occ1", "canonical_line_index": 0, "text": "123456"}],
            )
            write_csv(
                final_audit,
                FINAL_FIELDS,
                [{
                    "position": 1,
                    "cue_number": 1,
                    "text": "123446",
                    "occurrence_id": "occ1",
                    "canonical_line_index": 0,
                    "canonical_line_indices": "[0]",
                    "canonical_content_start": 0,
                    "canonical_content_end": 6,
                }],
            )
            final_srt.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\n123446\n",
                encoding="utf-8",
            )
            report = audit_resolved_lexical_floor(
                canonical_evaluation_audit=canonical,
                final_srt=final_srt,
                final_audit=final_audit,
            )
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["lexical_error_count"], 1)

    def test_missing_character_span_creates_coverage_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical.csv"
            final_audit = root / "final.csv"
            final_srt = root / "final.srt"
            write_csv(
                canonical,
                CANONICAL_FIELDS,
                [{"occurrence_id": "occ1", "canonical_line_index": 0, "text": "ABCDE"}],
            )
            write_csv(
                final_audit,
                FINAL_FIELDS,
                [
                    {
                        "position": 1,
                        "cue_number": 1,
                        "text": "AB",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 0,
                        "canonical_line_indices": "[0]",
                        "canonical_content_start": 0,
                        "canonical_content_end": 2,
                    },
                    {
                        "position": 2,
                        "cue_number": 2,
                        "text": "DE",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 0,
                        "canonical_line_indices": "[0]",
                        "canonical_content_start": 3,
                        "canonical_content_end": 5,
                    },
                ],
            )
            final_srt.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nAB\n\n"
                "2\n00:00:02,000 --> 00:00:03,000\nDE\n",
                encoding="utf-8",
            )
            report = audit_resolved_lexical_floor(
                canonical_evaluation_audit=canonical,
                final_srt=final_srt,
                final_audit=final_audit,
            )
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["coverage_gap_count"], 1)

    def test_nonzero_absolute_span_origin_is_inferred_from_text_and_line_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical.csv"
            final_audit = root / "final.csv"
            final_srt = root / "final.srt"
            write_csv(
                canonical,
                CANONICAL_FIELDS,
                [
                    {"occurrence_id": "occ1", "canonical_line_index": 1, "text": "ABC"},
                    {"occurrence_id": "occ1", "canonical_line_index": 2, "text": "DEF"},
                ],
            )
            write_csv(
                final_audit,
                FINAL_FIELDS,
                [
                    {
                        "position": 1,
                        "cue_number": 1,
                        "text": "AB",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 1,
                        "canonical_line_indices": "[1]",
                        "canonical_content_start": 2,
                        "canonical_content_end": 4,
                    },
                    {
                        "position": 2,
                        "cue_number": 2,
                        "text": "CDEF",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 1,
                        "canonical_line_indices": "[1, 2]",
                        "canonical_content_start": 4,
                        "canonical_content_end": 8,
                    },
                ],
            )
            final_srt.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nAB\n\n"
                "2\n00:00:02,000 --> 00:00:03,000\nCDEF\n",
                encoding="utf-8",
            )
            report = audit_resolved_lexical_floor(
                canonical_evaluation_audit=canonical,
                final_srt=final_srt,
                final_audit=final_audit,
            )
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["inferred_canonical_content_origins"], {"occ1": 2})
            self.assertEqual(report["covered_character_count"], 6)

    def test_ambiguous_nonzero_absolute_span_origin_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical.csv"
            final_audit = root / "final.csv"
            final_srt = root / "final.srt"
            write_csv(
                canonical,
                CANONICAL_FIELDS,
                [{"occurrence_id": "occ1", "canonical_line_index": 1, "text": "ABAB"}],
            )
            write_csv(
                final_audit,
                FINAL_FIELDS,
                [{
                    "position": 1,
                    "cue_number": 1,
                    "text": "AB",
                    "occurrence_id": "occ1",
                    "canonical_line_index": 1,
                    "canonical_line_indices": "[1]",
                    "canonical_content_start": 5,
                    "canonical_content_end": 7,
                }],
            )
            final_srt.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nAB\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unique canonical content origin"):
                audit_resolved_lexical_floor(
                    canonical_evaluation_audit=canonical,
                    final_srt=final_srt,
                    final_audit=final_audit,
                )

    def test_fallback_full_line_ownership_works_for_unmodified_canonical_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            canonical = root / "canonical.csv"
            final_audit = root / "final.csv"
            final_srt = root / "final.srt"
            write_csv(
                canonical,
                CANONICAL_FIELDS,
                [
                    {"occurrence_id": "occ1", "canonical_line_index": 4, "text": "hello"},
                    {"occurrence_id": "occ1", "canonical_line_index": 7, "text": "world"},
                ],
            )
            write_csv(
                final_audit,
                FINAL_FIELDS,
                [
                    {
                        "position": 1,
                        "cue_number": 1,
                        "text": "hello",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 4,
                    },
                    {
                        "position": 2,
                        "cue_number": 2,
                        "text": "world",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 7,
                    },
                ],
            )
            final_srt.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nhello\n\n"
                "2\n00:00:02,000 --> 00:00:03,000\nworld\n",
                encoding="utf-8",
            )
            report = audit_resolved_lexical_floor(
                canonical_evaluation_audit=canonical,
                final_srt=final_srt,
                final_audit=final_audit,
            )
            self.assertEqual(report["status"], "complete")


if __name__ == "__main__":
    unittest.main()
