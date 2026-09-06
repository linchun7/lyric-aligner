import json
import tempfile
import unittest
from pathlib import Path

from scripts import v4_build_independent_fine_known_transform_pack as builder


class IndependentFinePackBuilderTests(unittest.TestCase):
    def _audit(self, root: Path) -> tuple[Path, str]:
        payload = {
            "truth_usable_count": 1,
            "pairs": [
                {
                    "pair": 1,
                    "truth_usable": True,
                    "identities": {
                        "source_path": "视频歌词字幕/private/source.flac",
                        "source_sha256": "1" * 64,
                        "adjusted_path": "视频歌词字幕/private/adjusted.wav",
                        "adjusted_sha256": "2" * 64,
                        "nominal_slope": 1.1,
                    },
                    "summary": {"median_offset_seconds": -2.0300000000000002},
                    "windows": [
                        {
                            "window": 1,
                            "valid": True,
                            "start_adjusted_seconds": 27.717031249999998,
                            "end_adjusted_seconds": 51.71703125,
                        },
                        {
                            "window": 2,
                            "valid": True,
                            "start_adjusted_seconds": 67.4340625,
                            "end_adjusted_seconds": 91.4340625,
                        },
                        {
                            "window": 3,
                            "valid": True,
                            "start_adjusted_seconds": 107.15109375,
                            "end_adjusted_seconds": 131.15109375,
                        },
                    ],
                }
            ],
        }
        path = root / "audit.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path, builder.sha256_file(path)

    def test_build_pack_is_calibration_only_and_preserves_exact_audit_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit_path, audit_sha = self._audit(root)
            manifest, truth = builder.build_pack(
                audit_path=audit_path,
                expected_audit_sha256=audit_sha,
                expected_pair_count=1,
            )
            self.assertEqual(manifest["partition"], "calibration")
            self.assertEqual(truth["partition"], "calibration")
            self.assertEqual(manifest["record_count"], 3)
            self.assertEqual(truth["record_count"], 3)
            self.assertEqual(manifest["source_audit_sha256"], audit_sha)
            self.assertEqual(truth["source_audit_sha256"], audit_sha)
            self.assertEqual(manifest["records"][0]["mix_start_s"], 27.717031249999998)
            self.assertEqual(manifest["records"][0]["source_path"], "private/source.flac")
            self.assertEqual(manifest["records"][0]["mix_path"], "private/adjusted.wav")
            self.assertEqual(
                manifest["records"][0]["slopes"],
                [1.09, 1.0950000000000002, 1.1, 1.105, 1.11],
            )
            self.assertEqual(
                [row["case_id"] for row in manifest["records"]],
                [row["case_id"] for row in truth["records"]],
            )
            self.assertEqual(manifest["manifest_sha256"], builder._sha_json({
                key: value for key, value in manifest.items() if key != "manifest_sha256"
            }))
            self.assertEqual(truth["artifact_sha256"], builder._sha_json({
                key: value for key, value in truth.items() if key != "artifact_sha256"
            }))
            self.assertNotIn("holdout", json.dumps(manifest, ensure_ascii=False))
            self.assertNotIn("holdout", json.dumps(truth, ensure_ascii=False))

    def test_audit_sha_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit_path, _ = self._audit(root)
            with self.assertRaisesRegex(builder.IndependentFinePackBuildError, "audit SHA mismatch"):
                builder.build_pack(
                    audit_path=audit_path,
                    expected_audit_sha256="a" * 64,
                    expected_pair_count=1,
                )

    def test_holdout_pack_binds_frozen_policy_selection_and_formal_audit_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit_path, _ = self._audit(root)
            payload = json.loads(audit_path.read_text(encoding="utf-8"))
            payload["partition"] = "holdout"
            payload["artifact_sha256"] = "a" * 64
            payload["frozen_policy_sha256"] = "b" * 64
            payload["pair_selection_sha256"] = "c" * 64
            payload["pairs"][0]["identities"]["source_path"] = "private/source.flac"
            payload["pairs"][0]["identities"]["adjusted_path"] = "private/adjusted.wav"
            audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            audit_file_sha = builder.sha256_file(audit_path)
            manifest, truth = builder.build_pack(
                audit_path=audit_path,
                expected_audit_sha256=audit_file_sha,
                expected_pair_count=1,
                partition="holdout",
                expected_frozen_policy_sha256="b" * 64,
                expected_pair_selection_sha256="c" * 64,
            )
            self.assertEqual(manifest["partition"], "holdout")
            self.assertEqual(truth["partition"], "holdout")
            self.assertEqual(manifest["source_audit_sha256"], "a" * 64)
            self.assertEqual(truth["source_audit_sha256"], "a" * 64)
            self.assertEqual(manifest["source_audit_file_sha256"], audit_file_sha)
            self.assertEqual(manifest["frozen_policy_sha256"], "b" * 64)
            self.assertEqual(manifest["pair_selection_sha256"], "c" * 64)
            self.assertEqual(truth["frozen_policy_sha256"], "b" * 64)
            self.assertEqual(truth["pair_selection_sha256"], "c" * 64)
            self.assertTrue(all(row["source_audit_sha256"] == "a" * 64 for row in truth["records"]))

            with self.assertRaisesRegex(builder.IndependentFinePackBuildError, "frozen policy SHA mismatch"):
                builder.build_pack(
                    audit_path=audit_path,
                    expected_audit_sha256=audit_file_sha,
                    expected_pair_count=1,
                    partition="holdout",
                    expected_frozen_policy_sha256="d" * 64,
                    expected_pair_selection_sha256="c" * 64,
                )

    def test_non_repository_private_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit_path, _ = self._audit(root)
            payload = json.loads(audit_path.read_text(encoding="utf-8"))
            payload["pairs"][0]["identities"]["source_path"] = "outside/source.flac"
            audit_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            audit_sha = builder.sha256_file(audit_path)
            with self.assertRaisesRegex(builder.IndependentFinePackBuildError, "repository private"):
                builder.build_pack(
                    audit_path=audit_path,
                    expected_audit_sha256=audit_sha,
                    expected_pair_count=1,
                )


if __name__ == "__main__":
    unittest.main()
