import hashlib
import json
import unittest

from lyric_aligner.evaluation.production_calibration import (
    ProductionCalibrationError,
    evaluate_human_gold_backend_calibration,
    production_calibration_artifact_is_authoritative,
    validate_human_boundary_gold_artifact,
)
from lyric_aligner.timeline.boundary_calibration import (
    calibration_artifact_is_authoritative,
    evaluate_boundary_backend_calibration,
)
from scripts.test_support_production_calibration import (
    human_gold_artifact,
    production_calibration,
)


def _sha_json(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _scope(*, boundary_kind="start", audio_basis="final_mix"):
    return {
        "boundary_kind": boundary_kind,
        "audio_basis": audio_basis,
        "language": "en",
        "backend_version": "fixture-backend-v1",
        "backend_profile_id": "fixture-profile-v1",
        "window_policy_id": "fixture-window-v1",
        "model_id": "fixture-model",
        "model_revision": "fixture-revision",
        "implementation_revision": "a" * 64,
        "adapter_contract_revision": "b" * 64,
    }


class ProductionCalibrationTests(unittest.TestCase):
    def test_valid_locked_human_gold_fixture_passes_structural_validation(self):
        gold = human_gold_artifact()

        verified = validate_human_boundary_gold_artifact(gold)

        self.assertEqual(verified["artifact_sha256"], gold["artifact_sha256"])
        self.assertEqual(verified["gold_sha256"], gold["gold_sha256"])
        self.assertEqual(len(verified["records"]), 90)

    def test_rehashed_oversized_human_uncertainty_still_fails_semantic_policy(self):
        gold = human_gold_artifact()
        gold["records"][0]["gold_uncertainty_ms"] = 101
        gold["gold_sha256"] = _sha_json(gold["records"])
        gold.pop("artifact_sha256", None)
        gold["artifact_sha256"] = _sha_json(gold)

        with self.assertRaisesRegex(
            ProductionCalibrationError,
            "uncertainty exceeds production policy",
        ):
            validate_human_boundary_gold_artifact(gold)

    def test_generic_statistically_passing_calibration_is_not_production_authority(self):
        gold = human_gold_artifact()
        scoped = [row for row in gold["records"] if row["boundary_kind"] == "start"]
        scope = _scope()
        predictions = {row["id"]: row["gold_ms"] for row in scoped}

        generic = evaluate_boundary_backend_calibration(
            backend_id="generic-backend",
            family="final_mix_forced_alignment",
            correlation_group="generic-group",
            scope=scope,
            gold_records=scoped,
            predictions_ms=predictions,
        )

        self.assertTrue(generic["passed"])
        self.assertTrue(
            calibration_artifact_is_authoritative(
                generic,
                backend_id="generic-backend",
                family="final_mix_forced_alignment",
                correlation_group="generic-group",
                expected_scope=scope,
            )
        )
        self.assertFalse(
            production_calibration_artifact_is_authoritative(
                generic,
                backend_id="generic-backend",
                family="final_mix_forced_alignment",
                correlation_group="generic-group",
                expected_scope=scope,
            )
        )

    def test_human_gold_wrapper_produces_authoritative_exact_scope_artifact(self):
        scope = _scope()
        artifact = production_calibration(
            backend_id="backend-a",
            family="final_mix_forced_alignment",
            correlation_group="group-a",
            boundary_kind="start",
            audio_basis="final_mix",
            language="en",
            backend_version=scope["backend_version"],
            backend_profile_id=scope["backend_profile_id"],
            window_policy_id=scope["window_policy_id"],
            model_id=scope["model_id"],
            model_revision=scope["model_revision"],
        )

        self.assertTrue(
            production_calibration_artifact_is_authoritative(
                artifact,
                backend_id="backend-a",
                family="final_mix_forced_alignment",
                correlation_group="group-a",
                expected_scope=scope,
            )
        )
        mismatched = dict(scope)
        mismatched["model_revision"] = "other-revision"
        self.assertFalse(
            production_calibration_artifact_is_authoritative(
                artifact,
                backend_id="backend-a",
                family="final_mix_forced_alignment",
                correlation_group="group-a",
                expected_scope=mismatched,
            )
        )

    def test_legacy_calibration_without_runtime_provenance_is_readable_but_not_production_authority(self):
        scope = _scope()
        legacy_scope = dict(scope)
        legacy_scope.pop("implementation_revision")
        legacy_scope.pop("adapter_contract_revision")
        artifact = production_calibration(
            backend_id="backend-a",
            family="final_mix_forced_alignment",
            correlation_group="group-a",
            boundary_kind="start",
            audio_basis="final_mix",
            language="en",
            backend_version=legacy_scope["backend_version"],
            backend_profile_id=legacy_scope["backend_profile_id"],
            window_policy_id=legacy_scope["window_policy_id"],
            model_id=legacy_scope["model_id"],
            model_revision=legacy_scope["model_revision"],
            implementation_revision="",
            adapter_contract_revision="",
        )
        self.assertTrue(artifact["passed"])
        self.assertFalse(
            production_calibration_artifact_is_authoritative(
                artifact,
                backend_id="backend-a",
                family="final_mix_forced_alignment",
                correlation_group="group-a",
                expected_scope=legacy_scope,
            )
        )

    def test_prediction_ids_outside_locked_boundary_scope_are_rejected(self):
        gold = human_gold_artifact()
        scope = _scope()
        scoped = [row for row in gold["records"] if row["boundary_kind"] == "start"]
        predictions = {row["id"]: row["gold_ms"] for row in scoped}
        predictions["not-a-human-gold-id"] = 12345

        with self.assertRaisesRegex(
            ProductionCalibrationError,
            "outside the audited calibration scope",
        ):
            evaluate_human_gold_backend_calibration(
                backend_id="backend-a",
                family="final_mix_forced_alignment",
                correlation_group="group-a",
                scope=scope,
                human_gold_artifact=gold,
                predictions_ms=predictions,
            )

    def test_source_projection_backend_can_be_calibrated_against_final_mix_human_gold(self):
        scope = _scope(audio_basis="source_projection")
        artifact = production_calibration(
            backend_id="source-backend",
            family="source_projected_forced_alignment",
            correlation_group="source-group",
            boundary_kind="start",
            audio_basis="source_projection",
            language="en",
            backend_version=scope["backend_version"],
            backend_profile_id=scope["backend_profile_id"],
            window_policy_id=scope["window_policy_id"],
            model_id=scope["model_id"],
            model_revision=scope["model_revision"],
        )

        self.assertTrue(
            production_calibration_artifact_is_authoritative(
                artifact,
                backend_id="source-backend",
                family="source_projected_forced_alignment",
                correlation_group="source-group",
                expected_scope=scope,
            )
        )


if __name__ == "__main__":
    unittest.main()
