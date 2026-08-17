import csv
import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.contracts.artifacts import validate_upstream_artifact
from lyric_aligner.qa.final_integrity import (
    FinalIntegrityError,
    build_release_artifact_manifest,
    validate_srt_report_binding,
)
from lyric_aligner.srt import SRTParseError, parse_srt_strict, timeline_end_ms


FINGERPRINT = "a" * 64


def write_case(root: Path):
    srt = root / "final.srt"
    report = root / "final.csv"
    qa = root / "qa.json"
    srt.write_text(
        "2\n00:00:10,000 --> 00:00:12,000\nsecond in file\n\n"
        "1\n00:00:01,000 --> 00:00:03,000\nfirst in file\n",
        encoding="utf-8-sig",
    )
    with report.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["task_fingerprint_sha256", "start_ms", "end_ms", "text"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "task_fingerprint_sha256": FINGERPRINT,
                "start_ms": 10000,
                "end_ms": 12000,
                "text": "second in file",
            }
        )
        writer.writerow(
            {
                "task_fingerprint_sha256": FINGERPRINT,
                "start_ms": 1000,
                "end_ms": 3000,
                "text": "first in file",
            }
        )
    qa.write_text(
        json.dumps(
            {
                "algorithm_version": "3.9",
                "task_fingerprint_sha256": FINGERPRINT,
                "passed": True,
                "structurally_valid": True,
                "fully_reviewed": True,
                "publish_ready": True,
                "review_candidate_count": 0,
            }
        ),
        encoding="utf-8",
    )
    return srt, report, qa


class V4ReleaseIntegrityTests(unittest.TestCase):
    def test_strict_binding_accepts_exact_outputs_and_uses_max_timeline_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt, report, _ = write_case(root)
            result = validate_srt_report_binding(
                srt, report, expected_task_fingerprint=FINGERPRINT
            )
            self.assertEqual(result["cue_count"], 2)
            self.assertEqual(timeline_end_ms(parse_srt_strict(srt)), 12000)

    def test_tampered_final_text_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt, report, _ = write_case(root)
            srt.write_text(
                srt.read_text(encoding="utf-8-sig").replace(
                    "second in file", "tampered unrelated text"
                ),
                encoding="utf-8-sig",
            )
            with self.assertRaisesRegex(FinalIntegrityError, "text mismatch"):
                validate_srt_report_binding(
                    srt, report, expected_task_fingerprint=FINGERPRINT
                )

    def test_tampered_timing_and_missing_report_row_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt, report, _ = write_case(root)
            with report.open(encoding="utf-8-sig", newline="") as source:
                rows = list(csv.DictReader(source))
            with report.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerow({**rows[0], "end_ms": "12500"})
            with self.assertRaises(FinalIntegrityError) as context:
                validate_srt_report_binding(
                    srt, report, expected_task_fingerprint=FINGERPRINT
                )
            message = str(context.exception)
            self.assertIn("row count mismatch", message)
            self.assertIn("timing mismatch", message)

    def test_cross_algorithm_qa_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt, report, qa = write_case(root)
            with self.assertRaisesRegex(FinalIntegrityError, "algorithm version mismatch"):
                build_release_artifact_manifest(
                    final_srt=srt,
                    audit_csv=report,
                    qa_json=qa,
                    task_fingerprint_sha256=FINGERPRINT,
                    algorithm_version="4.0.0a1",
                )

    def test_release_manifest_binds_all_output_hashes_and_detects_manifest_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            srt, report, qa = write_case(root)
            manifest = build_release_artifact_manifest(
                final_srt=srt,
                audit_csv=report,
                qa_json=qa,
                task_fingerprint_sha256=FINGERPRINT,
                algorithm_version="3.9",
                git_commit="synthetic",
            )
            self.assertEqual(len(manifest["outputs"]), 3)
            self.assertEqual(
                validate_upstream_artifact(
                    manifest,
                    expected_task_fingerprint=FINGERPRINT,
                    expected_algorithm_version="3.9",
                    expected_stage="release",
                ),
                [],
            )
            manifest["outputs"][0]["sha256"] = "0" * 64
            self.assertIn(
                "artifact_id does not match manifest contents",
                validate_upstream_artifact(
                    manifest,
                    expected_task_fingerprint=FINGERPRINT,
                    expected_algorithm_version="3.9",
                    expected_stage="release",
                ),
            )

    def test_malformed_srt_block_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.srt"
            path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nok\n\n"
                "THIS BLOCK IS BROKEN\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SRTParseError, "malformed SRT block 2"):
                parse_srt_strict(path)


if __name__ == "__main__":
    unittest.main()
