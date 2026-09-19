import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.test_human_boundary_anchor import anchor_gold_fixture
from scripts.v4_run_human_boundary_anchor_calibration_suite import run_anchor_calibration_suite


def _sha_json(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class _Profile:
    def __init__(self, profile_id):
        self.profile_id = profile_id

    def resolve(self, repository_root, *, language):
        return {
            "profile_id": self.profile_id,
            "backend_id": self.profile_id + "-backend",
            "backend_version": "v1",
            "family": self.profile_id + "-family",
            "correlation_group": self.profile_id + "-group",
            "model_id": self.profile_id + "-model",
            "model_revision": self.profile_id + "-revision",
            "implementation_revision": "a" * 64,
            "adapter_contract_revision": "b" * 64,
            "language": language,
            "audio_basis": "final_mix",
            "window_policy_id": "window-v1",
            "command": "unused",
        }


class HumanBoundaryAnchorCalibrationSuiteTests(unittest.TestCase):
    def test_lightweight_suite_can_grant_authority_from_36_point_anchor_gold(self):
        gold = anchor_gold_fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = root / "pack"
            pack.mkdir()
            outer_cases = []
            internal_cases = []
            for row in gold["records"]:
                target = internal_cases if row["boundary_kind"] == "internal" else outer_cases
                if row["boundary_kind"] == "end":
                    continue
                target.append(
                    {
                        "case_id": row["case_id"],
                        "segments": [
                            {"lrc_index": 0, "text": "left"},
                            {"lrc_index": 1, "text": "right"},
                        ] if row["boundary_kind"] == "internal" else [{"lrc_index": 0, "text": "line"}],
                        "internal_boundary_index": 1 if row["boundary_kind"] == "internal" else None,
                        "clip_start_ms": row["gold_ms"] - 500,
                        "clip_end_ms": row["gold_ms"] + 500,
                        "start_ms": row["gold_ms"] - 300,
                        "end_ms": row["gold_ms"] + 300,
                    }
                )
            lock = {
                "selection": {
                    "populations": {
                        "outer": outer_cases,
                        "internal": internal_cases,
                    }
                },
                "inputs": {"final_audio_path": str(root / "unused.wav")},
            }
            (pack / "selection.lock.json").write_text(json.dumps(lock), encoding="utf-8")

            def profile_getter(profile_id):
                return _Profile(profile_id)

            def predict(**kwargs):
                kind = kwargs["boundary_kind"]
                config = kwargs["config"]
                rows = [row for row in gold["records"] if row["boundary_kind"] == kind]
                records = [{"id": row["id"], "predicted_ms": row["gold_ms"]} for row in rows]
                scope = {
                    "boundary_kind": kind,
                    "audio_basis": config.audio_basis,
                    "language": config.language,
                    "backend_version": config.backend_version,
                    "backend_profile_id": config.profile_id,
                    "window_policy_id": config.window_policy_id,
                    "model_id": config.model_id,
                    "model_revision": config.model_revision,
                    "implementation_revision": config.implementation_revision,
                    "adapter_contract_revision": config.adapter_contract_revision,
                    "lexical_id": "alignment-lexical-v1",
                    "lexical_revision": "1",
                }
                artifact = {
                    "schema_version": "human-boundary-backend-predictions-1.1",
                    "selection_lock_sha256": gold["selection_lock_sha256"],
                    "backend_id": config.backend_id,
                    "family": config.family,
                    "correlation_group": config.correlation_group,
                    "scope": scope,
                    "record_count": len(records),
                    "records": records,
                    "predictions_sha256": _sha_json(records),
                }
                artifact["artifact_sha256"] = _sha_json(artifact)
                return artifact

            with patch(
                "scripts.v4_run_human_boundary_anchor_calibration_suite.build_human_boundary_anchor_artifact",
                return_value=gold,
            ):
                suite = run_anchor_calibration_suite(
                    pack_dir=pack,
                    out_dir=root / "suite",
                    profile_ids=("profile-a", "profile-b"),
                    repository_root=root,
                    profile_getter=profile_getter,
                    prediction_executor=predict,
                )

            self.assertTrue(suite["production_authority_ready"])
            self.assertEqual(suite["scope_count"], 6)
            self.assertTrue(all(row["production_authoritative"] for row in suite["scopes"]))
            self.assertFalse(suite["subtitle_mutation_performed"])


if __name__ == "__main__":
    unittest.main()
