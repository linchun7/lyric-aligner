import json
import tempfile
import unittest
from pathlib import Path

from scripts import v4_build_human_anchor_calibration_view as builder


class HumanAnchorCalibrationViewTests(unittest.TestCase):
    def _source(self) -> dict:
        records = []
        for index in range(8):
            case_id = f"{index + 1:064x}"
            for kind in ("start", "end"):
                records.append(
                    {
                        "id": f"{case_id}:{kind}",
                        "case_id": case_id,
                        "boundary_kind": kind,
                        "partition": "calibration",
                        "population": "outer",
                        "gold_ms": 1000 + index * 100 + (50 if kind == "end" else 0),
                        "gold_uncertainty_ms": 50,
                        "track": f"track-{index}",
                    }
                )
        records.extend(
            [
                {
                    "id": "f" * 64 + ":start",
                    "case_id": "f" * 64,
                    "boundary_kind": "start",
                    "partition": "holdout",
                    "population": "outer",
                    "gold_ms": 9999,
                },
                {
                    "id": "e" * 64 + ":internal",
                    "case_id": "e" * 64,
                    "boundary_kind": "internal",
                    "partition": "calibration",
                    "population": "internal",
                    "gold_ms": 8888,
                },
            ]
        )
        payload = {
            "schema_version": "fixture-human-gold",
            "selection_lock_sha256": "a" * 64,
            "final_audio_sha256": "b" * 64,
            "outer_audit_csv_sha256": "c" * 64,
            "uncertainty_policy_id": "fixture-policy",
            "production_policy": {"fps": 30.0},
            "records": records,
        }
        payload["artifact_sha256"] = builder._sha_json(payload)
        return payload

    def test_build_view_physically_excludes_holdout_and_internal_records(self):
        source = self._source()
        view = builder.build_calibration_view(source)
        self.assertEqual(view["schema_version"], builder.SCHEMA_VERSION)
        self.assertEqual(view["record_count"], 16)
        self.assertEqual(view["unique_case_count"], 8)
        self.assertTrue(all(row["partition"] == "calibration" for row in view["records"]))
        self.assertTrue(all(row["population"] == "outer" for row in view["records"]))
        self.assertTrue(all(row["boundary_kind"] in {"start", "end"} for row in view["records"]))
        self.assertFalse(view["contains_holdout_records"])
        self.assertFalse(view["contains_internal_records"])
        self.assertEqual(view["source_human_gold_artifact_sha256"], source["artifact_sha256"])
        claimed = view["artifact_sha256"]
        without_hash = dict(view)
        without_hash.pop("artifact_sha256")
        self.assertEqual(claimed, builder._sha_json(without_hash))

    def test_source_artifact_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "gold.json"
            payload = self._source()
            payload["records"][0]["gold_ms"] += 1
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(builder.HumanAnchorCalibrationViewError, "artifact SHA mismatch"):
                builder._load_verified_source(path)


if __name__ == "__main__":
    unittest.main()
