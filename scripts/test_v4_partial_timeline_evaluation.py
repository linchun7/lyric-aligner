from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.evaluation.partial_timeline import (
    PartialTimelineEvaluationError,
    evaluate_partial_timeline_dataset,
)


SOURCE_SHA = "a" * 64
CANONICAL_SHA = "b" * 64
OCCURRENCE_ID = "occ-1"


class PartialTimelineEvaluationTests(unittest.TestCase):
    def write_case(
        self,
        root: Path,
        *,
        preview: dict | None = None,
        truth: dict | None = None,
        split: str = "calibration",
        cases: list[dict] | None = None,
    ) -> Path:
        preview_payload = preview or {
            "schema_version": "1.0",
            "mode": "partial_timeline_repair_preview",
            "status": "review_required",
            "releaseable": False,
            "automatic_timing_change_allowed": False,
            "subtitle_text_unchanged": True,
            "selected_cue_count": 4,
            "inputs": {
                "source_srt_sha256": SOURCE_SHA,
                "canonical_lrc_sha256": CANONICAL_SHA,
                "mapping_payload_sha256": "c" * 64,
                "mapping_identity": {"occurrence_id": OCCURRENCE_ID},
            },
            "decisions": [
                {
                    "cue_number": 1,
                    "action": "propose",
                    "reason": "source_to_mix_one_to_one_timing_preview",
                    "text": "私人歌词甲",
                    "canonical_text": "私人歌词甲",
                    "original_start_ms": 1000,
                    "original_end_ms": 2000,
                    "suggested_start_ms": 1100,
                    "suggested_end_ms": 2100,
                },
                {
                    "cue_number": 2,
                    "action": "propose",
                    "reason": "source_to_mix_one_to_one_timing_preview",
                    "text": "私人歌词乙",
                    "canonical_text": "私人歌词乙",
                    "original_start_ms": 3000,
                    "original_end_ms": 4000,
                    "suggested_start_ms": 3500,
                    "suggested_end_ms": 4500,
                },
                {
                    "cue_number": 3,
                    "action": "review",
                    "reason": "source_interval_is_unprojectable",
                    "text": "私人歌词丙",
                    "canonical_text": "私人歌词丙",
                    "original_start_ms": 5000,
                    "original_end_ms": 6000,
                    "suggested_start_ms": None,
                    "suggested_end_ms": None,
                },
                {
                    "cue_number": 4,
                    "action": "unchanged",
                    "reason": "source_to_mix_matches_existing_timing",
                    "text": "私人歌词丁",
                    "canonical_text": "私人歌词丁",
                    "original_start_ms": 7000,
                    "original_end_ms": 8000,
                    "suggested_start_ms": 7000,
                    "suggested_end_ms": 8000,
                },
            ],
        }
        truth_payload = truth or {
            "schema_version": "1.0",
            "source_srt_sha256": SOURCE_SHA,
            "canonical_lrc_sha256": CANONICAL_SHA,
            "occurrence_id": OCCURRENCE_ID,
            "cues": [
                {"cue_number": 1, "truth_start_ms": 1120, "truth_end_ms": 2080},
                {"cue_number": 2, "truth_start_ms": 3200, "truth_end_ms": 4200},
                {"cue_number": 3, "truth_start_ms": 5500, "truth_end_ms": 6500},
                {"cue_number": 4, "truth_start_ms": 7400, "truth_end_ms": 8400},
            ],
        }
        (root / "preview.json").write_text(
            json.dumps(preview_payload, ensure_ascii=False), encoding="utf-8"
        )
        (root / "truth.json").write_text(
            json.dumps(truth_payload, ensure_ascii=False), encoding="utf-8"
        )
        manifest_cases = cases or [
            {
                "id": "case-1",
                "language": "zh",
                "risk_buckets": ["global_rate", "weak_vocal"],
                "preview_report_json": "preview.json",
                "truth_json": "truth.json",
            }
        ]
        manifest = {
            "schema_version": "1.0",
            "dataset": "private-partial-timing",
            "dataset_revision": "r1",
            "split": split,
            "cases": manifest_cases,
        }
        path = root / "dataset.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return path

    def test_evaluator_reports_error_and_failure_metrics_without_lyrics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_case(Path(directory))
            report = evaluate_partial_timeline_dataset(path, error_threshold_ms=250)

        self.assertEqual(report["mode"], "partial_timeline_preview_evaluation")
        self.assertEqual(report["split"], "calibration")
        self.assertFalse(report["releaseable"])
        self.assertFalse(report["automatic_timing_change_allowed"])
        self.assertTrue(report["evaluation_only"])

        overall = report["overall"]
        self.assertEqual(overall["selected_cue_count"], 4)
        self.assertEqual(overall["proposed_count"], 2)
        self.assertEqual(overall["review_count"], 1)
        self.assertEqual(overall["unchanged_count"], 1)
        self.assertEqual(overall["bad_proposal_count"], 1)
        self.assertEqual(overall["bad_proposal_rate"], 0.5)
        self.assertEqual(overall["unnecessary_proposal_count"], 2)
        self.assertEqual(overall["harmful_proposal_count"], 1)
        self.assertEqual(overall["missed_needed_change_count"], 1)
        self.assertEqual(overall["review_needed_change_count"], 1)
        self.assertEqual(overall["proposal_onset_mae_ms"], 160.0)
        self.assertEqual(overall["proposal_offset_mae_ms"], 160.0)
        self.assertEqual(overall["proposal_line_max_error_p50_ms"], 160.0)
        self.assertEqual(overall["proposal_line_max_error_p95_ms"], 286.0)
        self.assertEqual(overall["original_line_max_error_p95_ms"], 485.0)
        self.assertEqual(overall["proposal_within_threshold_rate"], 0.5)

        self.assertEqual(report["by_language"]["zh"], overall)
        self.assertEqual(report["by_risk_bucket"]["global_rate"], overall)
        self.assertEqual(report["by_risk_bucket"]["weak_vocal"], overall)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("私人歌词甲", serialized)
        self.assertNotIn("canonical_text", serialized)
        self.assertNotIn('"text"', serialized)

    def test_blind_split_is_preserved_and_does_not_change_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_case(Path(directory), split="blind_test")
            report = evaluate_partial_timeline_dataset(path)
        self.assertEqual(report["split"], "blind_test")
        self.assertFalse(report["releaseable"])
        self.assertFalse(report["automatic_timing_change_allowed"])

    def test_truth_must_cover_exact_selected_preview_cues(self):
        truth = {
            "schema_version": "1.0",
            "source_srt_sha256": SOURCE_SHA,
            "canonical_lrc_sha256": CANONICAL_SHA,
            "occurrence_id": OCCURRENCE_ID,
            "cues": [
                {"cue_number": 1, "truth_start_ms": 1000, "truth_end_ms": 2000}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_case(Path(directory), truth=truth)
            with self.assertRaisesRegex(
                PartialTimelineEvaluationError, "must match exactly"
            ):
                evaluate_partial_timeline_dataset(path)

    def test_truth_must_bind_exact_preview_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_case(root)
            truth = json.loads((root / "truth.json").read_text(encoding="utf-8"))
            truth["source_srt_sha256"] = "d" * 64
            (root / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
            with self.assertRaisesRegex(
                PartialTimelineEvaluationError, "input identity mismatch"
            ):
                evaluate_partial_timeline_dataset(path)

    def test_truth_requires_occurrence_and_sha_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_case(root)
            truth = json.loads((root / "truth.json").read_text(encoding="utf-8"))
            truth.pop("occurrence_id")
            (root / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
            with self.assertRaisesRegex(
                PartialTimelineEvaluationError, "truth requires occurrence_id"
            ):
                evaluate_partial_timeline_dataset(path)

    def test_preview_authority_flags_must_remain_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_case(root)
            preview = json.loads((root / "preview.json").read_text(encoding="utf-8"))
            preview["releaseable"] = True
            (root / "preview.json").write_text(json.dumps(preview), encoding="utf-8")
            with self.assertRaisesRegex(
                PartialTimelineEvaluationError, "non-releaseable"
            ):
                evaluate_partial_timeline_dataset(path)

    def test_preview_must_confirm_subtitle_text_was_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_case(root)
            preview = json.loads((root / "preview.json").read_text(encoding="utf-8"))
            preview["subtitle_text_unchanged"] = False
            (root / "preview.json").write_text(json.dumps(preview), encoding="utf-8")
            with self.assertRaisesRegex(
                PartialTimelineEvaluationError, "preserve subtitle text"
            ):
                evaluate_partial_timeline_dataset(path)

    def test_duplicate_case_ids_are_rejected(self):
        cases = [
            {
                "id": "same",
                "language": "zh",
                "risk_buckets": [],
                "preview_report_json": "preview.json",
                "truth_json": "truth.json",
            },
            {
                "id": "same",
                "language": "en",
                "risk_buckets": [],
                "preview_report_json": "preview.json",
                "truth_json": "truth.json",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_case(Path(directory), cases=cases)
            with self.assertRaisesRegex(
                PartialTimelineEvaluationError, "case ids must be unique"
            ):
                evaluate_partial_timeline_dataset(path)

    def test_invalid_split_and_threshold_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_case(Path(directory), split="production")
            with self.assertRaisesRegex(
                PartialTimelineEvaluationError, "split must be"
            ):
                evaluate_partial_timeline_dataset(path)
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_case(Path(directory))
            with self.assertRaisesRegex(
                PartialTimelineEvaluationError, "must be positive"
            ):
                evaluate_partial_timeline_dataset(path, error_threshold_ms=0)

    def test_invalid_preview_action_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_case(root)
            preview = json.loads((root / "preview.json").read_text(encoding="utf-8"))
            preview["decisions"][0]["action"] = "auto_apply"
            (root / "preview.json").write_text(json.dumps(preview), encoding="utf-8")
            with self.assertRaisesRegex(
                PartialTimelineEvaluationError, "invalid action"
            ):
                evaluate_partial_timeline_dataset(path)

    def test_selected_count_must_match_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self.write_case(root)
            preview = json.loads((root / "preview.json").read_text(encoding="utf-8"))
            preview["selected_cue_count"] = 99
            (root / "preview.json").write_text(json.dumps(preview), encoding="utf-8")
            with self.assertRaisesRegex(
                PartialTimelineEvaluationError, "does not match decisions"
            ):
                evaluate_partial_timeline_dataset(path)


if __name__ == "__main__":
    unittest.main()
