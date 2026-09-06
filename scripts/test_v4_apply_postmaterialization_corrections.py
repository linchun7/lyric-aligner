import csv
import json
import tempfile
import unittest
from pathlib import Path

from scripts.v4_apply_postmaterialization_corrections import (
    apply_repairs,
    sha256_json,
    validate_source_binding,
    verify_source_materialization,
)
from lyric_aligner.contracts.artifacts import sha256_file


class PostMaterializationCorrectionTests(unittest.TestCase):
    def row(self, *, cue="544", text="回到教室座位前后故意讨你温柔", indices="14;15"):
        return {
            "kind": "existing",
            "original_cue": cue,
            "start_ms": "2424366",
            "end_ms": "2428866",
            "text": text,
            "lrc_indices": indices,
            "canonical_segments_json": json.dumps(
                [
                    {"lrc_index": 14, "text": "回到教室座位前后"},
                    {"lrc_index": 15, "text": "故意讨你温柔的骂"},
                ],
                ensure_ascii=False,
            ),
            "evidence": "existing",
        }

    def repair(self, **updates):
        payload = {
            "kind": "text_ownership",
            "original_cue": "544",
            "expected_text": "回到教室座位前后故意讨你温柔",
            "replacement_text": "回到教室座位前后故意讨你温柔的骂",
            "expected_lrc_indices": "14;15",
            "reason": "human-confirmed ownership repair",
        }
        payload.update(updates)
        return payload

    def test_valid_repair_changes_only_text_and_audit_fields(self):
        row = self.row()
        original_interval = (row["start_ms"], row["end_ms"])
        applied = apply_repairs(rows=[row], repairs=[self.repair()])
        self.assertEqual(applied, ["544"])
        self.assertEqual(row["text"], "回到教室座位前后故意讨你温柔的骂")
        self.assertEqual((row["start_ms"], row["end_ms"]), original_interval)
        self.assertIn("post_materialization_human_text_ownership_repair", row["evidence"])
        self.assertEqual(row["post_materialization_correction"], "human_confirmed_text_ownership")

    def test_replacement_must_equal_bound_canonical_segments(self):
        with self.assertRaisesRegex(ValueError, "replacement does not equal"):
            apply_repairs(
                rows=[self.row()],
                repairs=[self.repair(replacement_text="错误文本")],
            )

    def test_source_text_and_lrc_identity_are_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "source text changed"):
            apply_repairs(rows=[self.row(text="changed")], repairs=[self.repair()])
        with self.assertRaisesRegex(ValueError, "LRC identity changed"):
            apply_repairs(rows=[self.row(indices="14")], repairs=[self.repair()])

    def test_correction_must_resolve_to_exactly_one_row(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            apply_repairs(rows=[self.row(), self.row()], repairs=[self.repair()])

    def test_materialization_artifact_binding_and_self_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            srt = root / "source.srt"
            report = root / "source.csv"
            srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nx\n", encoding="utf-8")
            with report.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["start_ms", "end_ms", "text"])
                writer.writeheader()
                writer.writerow({"start_ms": "1000", "end_ms": "2000", "text": "x"})
            payload = {
                "schema_version": "calibrated-alignment-materialization-1.0",
                "mode": "internal",
                "task_fingerprint_sha256": "a" * 64,
                "output_srt_sha256": sha256_file(srt),
                "output_report_sha256": sha256_file(report),
            }
            payload["artifact_sha256"] = sha256_json(payload)
            self.assertEqual(
                verify_source_materialization(
                    payload,
                    source_srt=srt,
                    source_report=report,
                    task_fingerprint="a" * 64,
                ),
                payload["artifact_sha256"],
            )
            tampered = dict(payload)
            tampered["output_srt_sha256"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "SRT SHA mismatch"):
                verify_source_materialization(
                    tampered,
                    source_srt=srt,
                    source_report=report,
                    task_fingerprint="a" * 64,
                )

    def test_srt_report_binding_detects_text_or_timing_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            srt = root / "source.srt"
            srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nx\n", encoding="utf-8")
            rows = [{"start_ms": "1000", "end_ms": "2000", "text": "x"}]
            validate_source_binding(srt, root / "unused.csv", rows)
            rows[0]["text"] = "y"
            with self.assertRaisesRegex(ValueError, "text mismatch"):
                validate_source_binding(srt, root / "unused.csv", rows)


if __name__ == "__main__":
    unittest.main()
