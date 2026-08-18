from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from lyric_aligner.evaluation.family_calibration import (
    FamilyCalibrationError,
    evaluate_family_dataset,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EvidenceFamilyEvaluationTests(unittest.TestCase):
    def _write(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _fixture(self, root: Path) -> Path:
        truth = {
            "schema_version": "1.0",
            "lines": [
                {
                    "occurrence_id": "occ-1",
                    "canonical_line_index": 0,
                    "canonical_text_sha256": _sha("line-a"),
                    "truth_start_ms": 1000,
                    "truth_end_ms": 2000,
                },
                {
                    "occurrence_id": "occ-1",
                    "canonical_line_index": 1,
                    "canonical_text_sha256": _sha("line-b"),
                    "truth_start_ms": 3000,
                    "truth_end_ms": 4000,
                },
            ],
        }
        fusion = {
            "mode": "shadow_only",
            "policy_calibrated": False,
            "release_gate_eligible": False,
            "lines": [
                {
                    "occurrence_id": "occ-1",
                    "canonical_line_index": 0,
                    "canonical_text_sha256": _sha("line-a"),
                    "source_timeline_boundary_ms": [1100, 2100],
                    "shadow_level": "HIGH",
                    "families": [
                        {"family": "source_timeline", "available": True, "boundary_ms": [1100, 2100]},
                        {"family": "editor", "available": True, "boundary_ms": [1050, 1950]},
                        {"family": "asr", "available": True, "boundary_ms": [1250, 2250]},
                        {
                            "family": "forced_alignment",
                            "available": True,
                            "projection_status": "projected",
                            "boundary_ms": [1000, 2000],
                        },
                    ],
                },
                {
                    "occurrence_id": "occ-1",
                    "canonical_line_index": 1,
                    "canonical_text_sha256": _sha("line-b"),
                    "source_timeline_boundary_ms": [3500, 4500],
                    "shadow_level": "CONFLICT",
                    "families": [
                        {"family": "source_timeline", "available": True, "boundary_ms": [3500, 4500]},
                        {"family": "editor", "available": False, "reason": "none"},
                        {"family": "asr", "available": True, "boundary_ms": [3100, 3900]},
                        {
                            "family": "forced_alignment",
                            "available": False,
                            "projection_status": "unprojectable",
                            "reason": "cross_cut",
                        },
                    ],
                },
            ],
        }
        self._write(root / "truth.json", truth)
        self._write(root / "fusion.json", fusion)
        manifest = {
            "schema_version": "1.0",
            "dataset": "private-calibration",
            "dataset_revision": "r1",
            "split": "calibration",
            "cases": [
                {
                    "id": "case-1",
                    "source_group": "group-1",
                    "language": "zh",
                    "risk_buckets": ["cut", "weak_vocal"],
                    "truth_json": "truth.json",
                    "fusion_json": "fusion.json",
                }
            ],
        }
        path = root / "dataset.json"
        self._write(path, manifest)
        return path

    def test_metrics_cover_all_four_families_and_risk_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate_family_dataset(self._fixture(Path(tmp)))
        overall = report["overall"]
        self.assertEqual(overall["truth_line_count"], 2)
        self.assertEqual(overall["conflict_line_count"], 1)
        self.assertEqual(overall["forced_unprojectable_rate"], 0.5)
        self.assertEqual(overall["families"]["source_timeline"]["coverage_rate"], 1.0)
        self.assertEqual(overall["families"]["source_timeline"]["boundary_mae_ms"], 300.0)
        self.assertEqual(overall["families"]["forced_alignment"]["coverage_rate"], 0.5)
        self.assertEqual(overall["families"]["forced_alignment"]["boundary_mae_ms"], 0.0)
        self.assertEqual(overall["families"]["asr"]["boundary_mae_ms"], 175.0)
        self.assertIn("zh", report["groups"]["language"])
        self.assertIn("cut", report["groups"]["risk_bucket"])
        self.assertFalse(report["automatic_timing_change_allowed"])

    def test_canonical_hash_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self._fixture(root)
            fusion_path = root / "fusion.json"
            fusion = json.loads(fusion_path.read_text(encoding="utf-8"))
            fusion["lines"][0]["canonical_text_sha256"] = "0" * 64
            self._write(fusion_path, fusion)
            with self.assertRaises(FamilyCalibrationError):
                evaluate_family_dataset(manifest)

    def test_output_omits_local_paths_and_lyrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = evaluate_family_dataset(self._fixture(root))
            rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(str(root), rendered)
        self.assertNotIn("line-a", rendered)
        self.assertNotIn("truth.json", rendered)
        self.assertIn("aggregate metrics", report["privacy"])

    def test_blind_split_is_supported_without_promoting_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self._fixture(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["split"] = "blind_test"
            self._write(manifest, payload)
            report = evaluate_family_dataset(manifest)
        self.assertEqual(report["split"], "blind_test")
        self.assertFalse(report["policy_calibrated"])
        self.assertFalse(report["release_gate_eligible"])


if __name__ == "__main__":
    unittest.main()
