from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.qa.viewer_lexical_floor import audit_viewer_lexical_floor


FIELDS = [
    "position",
    "cue_number",
    "text",
    "canonical_text",
    "display_text",
    "display_change_reasons",
    "occurrence_id",
    "canonical_line_index",
    "canonical_line_indices",
]


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class ViewerLexicalFloorTests(unittest.TestCase):
    def test_presentation_only_and_sensitive_mask_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "final.csv"
            srt = root / "final.srt"
            write_csv(
                audit,
                [
                    {
                        "position": 1,
                        "cue_number": 1,
                        "text": "Bodies moving togetherlet's take another shot",
                        "display_text": "Bodies moving together let's take another shot",
                        "display_change_reasons": "model_override:restore missing word boundary",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 1,
                    },
                    {
                        "position": 2,
                        "cue_number": 2,
                        "text": "what the fuck",
                        "display_text": "what the f*",
                        "display_change_reasons": "strong_profanity_mask",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 2,
                    },
                ],
            )
            srt.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nBodies moving together let's take another shot\n\n"
                "2\n00:00:02,000 --> 00:00:03,000\nwhat the f*\n",
                encoding="utf-8",
            )
            report = audit_viewer_lexical_floor(final_srt=srt, final_audit=audit)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["presentation_only_change_count"], 1)
        self.assertEqual(report["sensitive_mask_change_count"], 1)
        self.assertEqual(report["unauthorized_lexical_change_count"], 0)

    def test_real_display_audit_uses_preserved_canonical_text_not_mutated_text_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "final.csv"
            srt = root / "final.srt"
            write_csv(
                audit,
                [
                    {
                        "position": 1,
                        "cue_number": 1,
                        "text": "I could do this forever, no I don't wanna stop",
                        "canonical_text": "I could do this forever know I don't wanna stop",
                        "display_text": "I could do this forever, no I don't wanna stop",
                        "display_change_reasons": "model_override:correct transcription",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 2,
                    }
                ],
            )
            srt.write_text(
                "1\n00:00:01,000 --> 00:00:03,000\nI could do this forever, no I don't wanna stop\n",
                encoding="utf-8",
            )
            report = audit_viewer_lexical_floor(final_srt=srt, final_audit=audit)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["unauthorized_lexical_change_count"], 1)

    def test_mask_reason_does_not_legalize_arbitrary_lexical_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "final.csv"
            srt = root / "final.srt"
            write_csv(
                audit,
                [
                    {
                        "position": 1,
                        "cue_number": 1,
                        "text": "what the fuck",
                        "canonical_text": "what the fuck",
                        "display_text": "what the hello",
                        "display_change_reasons": "strong_profanity_mask",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 2,
                    }
                ],
            )
            srt.write_text(
                "1\n00:00:01,000 --> 00:00:03,000\nwhat the hello\n",
                encoding="utf-8",
            )
            report = audit_viewer_lexical_floor(final_srt=srt, final_audit=audit)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["sensitive_mask_change_count"], 0)
        self.assertEqual(report["unauthorized_lexical_change_count"], 1)

    def test_unproven_model_lexical_override_fails_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "final.csv"
            srt = root / "final.srt"
            write_csv(
                audit,
                [
                    {
                        "position": 1,
                        "cue_number": 1,
                        "text": "I could do this forever know I don't wanna stop",
                        "display_text": "I could do this forever, no I don't wanna stop",
                        "display_change_reasons": "model_override:correct transcription",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 2,
                    }
                ],
            )
            srt.write_text(
                "1\n00:00:01,000 --> 00:00:03,000\nI could do this forever, no I don't wanna stop\n",
                encoding="utf-8",
            )
            report = audit_viewer_lexical_floor(final_srt=srt, final_audit=audit)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["unauthorized_lexical_change_count"], 1)

    def test_authorized_truth_overlay_can_legitimize_exact_lexical_rebuttal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "final.csv"
            srt = root / "final.srt"
            overlay = root / "overlay.json"
            write_csv(
                audit,
                [
                    {
                        "position": 1,
                        "cue_number": 1,
                        "text": "I could do this forever know I don't wanna stop",
                        "display_text": "I could do this forever, no I don't wanna stop",
                        "display_change_reasons": "model_override:correct transcription",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 2,
                    }
                ],
            )
            srt.write_text(
                "1\n00:00:01,000 --> 00:00:03,000\nI could do this forever, no I don't wanna stop\n",
                encoding="utf-8",
            )
            overlay.write_text(
                json.dumps(
                    {
                        "schema_version": "canonical-semantic-truth-overlay-1.0",
                        "rows": [
                            {
                                "occurrence_id": "occ1",
                                "canonical_line_index": 2,
                                "truth_status": "authorized",
                                "truth_text": "I could do this forever, no I don't wanna stop",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = audit_viewer_lexical_floor(
                final_srt=srt,
                final_audit=audit,
                canonical_truth_overlay=overlay,
            )
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["authorized_lexical_rebuttal_count"], 1)
        self.assertEqual(report["unauthorized_lexical_change_count"], 0)

    def test_srt_must_match_viewer_text_in_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "final.csv"
            srt = root / "final.srt"
            write_csv(
                audit,
                [
                    {
                        "position": 1,
                        "cue_number": 1,
                        "text": "hello",
                        "display_text": "hello",
                        "display_change_reasons": "",
                        "occurrence_id": "occ1",
                        "canonical_line_index": 1,
                    }
                ],
            )
            srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nbye\n", encoding="utf-8")
            report = audit_viewer_lexical_floor(final_srt=srt, final_audit=audit)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["srt_audit_mismatch_count"], 1)


if __name__ == "__main__":
    unittest.main()
