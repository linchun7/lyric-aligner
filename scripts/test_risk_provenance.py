import copy
import hashlib
import json
import unittest

from lyric_aligner.evaluation.editor_risk_profile import build_editor_risk_profile_artifact
from lyric_aligner.evaluation.production_calibration import evaluate_human_gold_backend_calibration
from lyric_aligner.evaluation.risk_provenance import (
    RiskProvenanceError,
    adjudicate_bound_calibrated_fallback,
    bind_candidate_risk_profile,
    bind_editor_risk_profile,
)
from lyric_aligner.timeline.boundary_risk import BoundaryLocalSupport
from scripts.test_editor_risk_profile import _fixture


BACKEND_ID = "risk-observer"
FAMILY = "final_mix_forced_alignment"
CORRELATION_GROUP = "risk-observer-architecture"


def _sha_json(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _scope(boundary_kind="start"):
    return {
        "boundary_kind": boundary_kind,
        "audio_basis": "final_mix",
        "language": "en",
        "backend_version": "risk-observer-v1",
        "backend_profile_id": "risk-profile-v1",
        "window_policy_id": "risk-window-v1",
        "model_id": "risk-model",
        "model_revision": "risk-model-r1",
        "implementation_revision": "a" * 64,
        "adapter_contract_revision": "b" * 64,
    }


def _candidate_measurement(gold, *, boundary_kind="start", offset_ms=100, missing_holdout=0):
    scoped = [row for row in gold["records"] if row["boundary_kind"] == boundary_kind]
    holdout_ids = [row["id"] for row in scoped if row["partition"] == "holdout"]
    missing = set(holdout_ids[:missing_holdout])
    predictions = {
        row["id"]: (None if row["id"] in missing else row["gold_ms"] + offset_ms)
        for row in scoped
    }
    return evaluate_human_gold_backend_calibration(
        backend_id=BACKEND_ID,
        family=FAMILY,
        correlation_group=CORRELATION_GROUP,
        scope=_scope(boundary_kind),
        human_gold_artifact=gold,
        predictions_ms=predictions,
    )


def _with_prediction_provenance(artifact, gold):
    value = copy.deepcopy(artifact)
    rows = [
        {"id": str(row["id"]), "predicted_ms": row.get("predicted_ms")}
        for row in value["records"]
    ]
    value.pop("artifact_sha256")
    value["prediction_provenance"] = {
        "schema_version": "human-boundary-backend-predictions-1.1",
        "prediction_artifact_sha256": "c" * 64,
        "predictions_sha256": _sha_json(rows),
        "selection_lock_sha256": gold["selection_lock_sha256"],
        "production_provenance_complete": True,
        "source_blind_scope_artifact_sha256": "d" * 64,
        "reuse_mode": "fixture_blind_scope_reuse",
    }
    value["artifact_sha256"] = _sha_json(value)
    return value


def _local_support(candidate_ms: int, *, p90_ms: float = 120.0) -> BoundaryLocalSupport:
    return BoundaryLocalSupport(
        support_id="fixture-risk-local-support",
        boundary_kind="start",
        candidate_ms=candidate_ms,
        estimated_p90_error_ms=p90_ms,
    ).with_production_authority("e" * 64)


def _bound_pair(*, editor_offset_ms=500, candidate_offset_ms=100, missing_holdout=0):
    lock, gold, lock_file_sha = _fixture(
        start_offset_ms=editor_offset_ms,
        end_offset_ms=editor_offset_ms,
    )
    editor_artifact = build_editor_risk_profile_artifact(
        selection_lock=lock,
        selection_lock_file_sha256=lock_file_sha,
        human_gold_artifact=gold,
        boundary_kind="start",
        population="holdout",
    )
    candidate_artifact = _candidate_measurement(
        gold,
        boundary_kind="start",
        offset_ms=candidate_offset_ms,
        missing_holdout=missing_holdout,
    )
    editor = bind_editor_risk_profile(
        editor_risk_artifact=editor_artifact,
        selection_lock=lock,
        selection_lock_file_sha256=lock_file_sha,
        human_gold_artifact=gold,
    )
    candidate = bind_candidate_risk_profile(
        calibration_artifact=candidate_artifact,
        human_gold_artifact=gold,
        backend_id=BACKEND_ID,
        family=FAMILY,
        correlation_group=CORRELATION_GROUP,
        expected_scope=_scope("start"),
    )
    return lock, gold, lock_file_sha, editor_artifact, candidate_artifact, editor, candidate


class RiskProvenanceTests(unittest.TestCase):
    def test_exact_same_gold_profiles_can_adjudicate_calibrated_better(self):
        _, _, _, _, candidate_artifact, editor, candidate = _bound_pair(
            editor_offset_ms=500,
            candidate_offset_ms=100,
        )
        self.assertTrue(candidate_artifact["passed"])
        self.assertEqual(editor.human_gold_artifact_sha256, candidate.human_gold_artifact_sha256)
        self.assertEqual(editor.selection_lock_sha256, candidate.selection_lock_sha256)
        decision = adjudicate_bound_calibrated_fallback(
            editor_ms=1000,
            candidate_ms=1120,
            editor=editor,
            candidate=candidate,
            local_support=_local_support(1120),
        )
        self.assertEqual(decision.action, "auto_calibrated_better")
        self.assertEqual(decision.selected_ms, 1120)

    def test_risk_measurement_may_be_valid_even_when_strict_a_tier_calibration_fails(self):
        _, _, _, _, candidate_artifact, editor, candidate = _bound_pair(
            editor_offset_ms=1000,
            candidate_offset_ms=330,
        )
        # 330ms raw offset with 50ms Human Anchor uncertainty -> 280ms effective.
        # That deliberately misses the strict A-tier p90 ceiling, but remains a
        # genuine, exactly reproducible Human-Gold risk measurement.
        self.assertFalse(candidate_artifact["passed"])
        self.assertEqual(candidate.profile.mean_effective_error_ms, 280.0)
        self.assertEqual(candidate.profile.p90_effective_error_ms, 280.0)
        decision = adjudicate_bound_calibrated_fallback(
            editor_ms=10000,
            candidate_ms=9400,
            editor=editor,
            candidate=candidate,
            local_support=_local_support(9400, p90_ms=300.0),
        )
        self.assertEqual(decision.action, "auto_rescue")
        self.assertEqual(decision.selected_ms, 9400)

    def test_missing_predictions_reduce_predicted_track_diversity_and_block_auto(self):
        _, _, _, _, _, editor, candidate = _bound_pair(
            editor_offset_ms=1000,
            candidate_offset_ms=100,
            missing_holdout=2,
        )
        self.assertEqual(candidate.profile.sample_count, 4)
        self.assertEqual(candidate.profile.predicted_count, 2)
        self.assertEqual(candidate.profile.distinct_track_count, 2)
        self.assertEqual(candidate.profile.coverage, 0.5)
        decision = adjudicate_bound_calibrated_fallback(
            editor_ms=10000,
            candidate_ms=9500,
            editor=editor,
            candidate=candidate,
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertIn("profiles are not sufficiently", decision.reason)

    def test_bound_profiles_still_require_current_candidate_local_support(self):
        _, _, _, _, _, editor, candidate = _bound_pair(
            editor_offset_ms=1000,
            candidate_offset_ms=100,
        )
        decision = adjudicate_bound_calibrated_fallback(
            editor_ms=10000,
            candidate_ms=30000,
            editor=editor,
            candidate=candidate,
        )
        self.assertEqual(decision.action, "keep_editor")
        self.assertIn("without local support", decision.reason)

    def test_candidate_artifact_rehashed_tamper_cannot_gain_risk_authority(self):
        lock, gold, lock_file_sha, editor_artifact, artifact, _, _ = _bound_pair()
        del lock, lock_file_sha, editor_artifact
        tampered = copy.deepcopy(artifact)
        tampered["records"][0]["effective_error_ms"] = 0
        tampered.pop("artifact_sha256")
        tampered["artifact_sha256"] = _sha_json(tampered)
        with self.assertRaisesRegex(RiskProvenanceError, "cannot be reproduced"):
            bind_candidate_risk_profile(
                calibration_artifact=tampered,
                human_gold_artifact=gold,
                backend_id=BACKEND_ID,
                family=FAMILY,
                correlation_group=CORRELATION_GROUP,
                expected_scope=_scope("start"),
            )

    def test_known_prediction_provenance_extension_is_validated_and_accepted(self):
        _, gold, _, _, artifact, _, _ = _bound_pair()
        extended = _with_prediction_provenance(artifact, gold)
        candidate = bind_candidate_risk_profile(
            calibration_artifact=extended,
            human_gold_artifact=gold,
            backend_id=BACKEND_ID,
            family=FAMILY,
            correlation_group=CORRELATION_GROUP,
            expected_scope=_scope("start"),
        )
        self.assertTrue(candidate.profile.production_authoritative)
        self.assertEqual(candidate.source_artifact_sha256, extended["artifact_sha256"])

    def test_prediction_provenance_rows_sha_tamper_fails_even_after_rehash(self):
        _, gold, _, _, artifact, _, _ = _bound_pair()
        extended = _with_prediction_provenance(artifact, gold)
        extended["prediction_provenance"]["predictions_sha256"] = "e" * 64
        extended.pop("artifact_sha256")
        extended["artifact_sha256"] = _sha_json(extended)
        with self.assertRaisesRegex(RiskProvenanceError, "rows SHA"):
            bind_candidate_risk_profile(
                calibration_artifact=extended,
                human_gold_artifact=gold,
                backend_id=BACKEND_ID,
                family=FAMILY,
                correlation_group=CORRELATION_GROUP,
                expected_scope=_scope("start"),
            )

    def test_candidate_scope_identity_mismatch_fails_closed(self):
        _, gold, _, _, artifact, _, _ = _bound_pair()
        wrong_scope = dict(_scope("start"))
        wrong_scope["model_revision"] = "different-model-revision"
        with self.assertRaisesRegex(RiskProvenanceError, "cannot be reproduced"):
            bind_candidate_risk_profile(
                calibration_artifact=artifact,
                human_gold_artifact=gold,
                backend_id=BACKEND_ID,
                family=FAMILY,
                correlation_group=CORRELATION_GROUP,
                expected_scope=wrong_scope,
            )

    def test_candidate_cannot_be_rebound_to_different_human_gold(self):
        _, gold, _, _, artifact, _, _ = _bound_pair()
        other_gold = copy.deepcopy(gold)
        other_gold["final_audio_sha256"] = "9" * 64
        other_gold.pop("artifact_sha256")
        other_gold["artifact_sha256"] = _sha_json(other_gold)
        with self.assertRaises(RiskProvenanceError):
            bind_candidate_risk_profile(
                calibration_artifact=artifact,
                human_gold_artifact=other_gold,
                backend_id=BACKEND_ID,
                family=FAMILY,
                correlation_group=CORRELATION_GROUP,
                expected_scope=_scope("start"),
            )

    def test_bound_profiles_from_different_boundary_kinds_cannot_be_compared(self):
        lock, gold, lock_file_sha = _fixture(start_offset_ms=500, end_offset_ms=500)
        editor_artifact = build_editor_risk_profile_artifact(
            selection_lock=lock,
            selection_lock_file_sha256=lock_file_sha,
            human_gold_artifact=gold,
            boundary_kind="start",
            population="holdout",
        )
        end_candidate_artifact = _candidate_measurement(gold, boundary_kind="end", offset_ms=100)
        editor = bind_editor_risk_profile(
            editor_risk_artifact=editor_artifact,
            selection_lock=lock,
            selection_lock_file_sha256=lock_file_sha,
            human_gold_artifact=gold,
        )
        candidate = bind_candidate_risk_profile(
            calibration_artifact=end_candidate_artifact,
            human_gold_artifact=gold,
            backend_id=BACKEND_ID,
            family=FAMILY,
            correlation_group=CORRELATION_GROUP,
            expected_scope=_scope("end"),
        )
        with self.assertRaisesRegex(RiskProvenanceError, "boundary_kind"):
            adjudicate_bound_calibrated_fallback(
                editor_ms=1000,
                candidate_ms=1100,
                editor=editor,
                candidate=candidate,
            )

    def test_editor_calibration_partition_cannot_be_bound_for_production_risk(self):
        lock, gold, lock_file_sha = _fixture()
        editor_artifact = build_editor_risk_profile_artifact(
            selection_lock=lock,
            selection_lock_file_sha256=lock_file_sha,
            human_gold_artifact=gold,
            boundary_kind="start",
            population="calibration",
        )
        with self.assertRaisesRegex(RiskProvenanceError, "requires holdout"):
            bind_editor_risk_profile(
                editor_risk_artifact=editor_artifact,
                selection_lock=lock,
                selection_lock_file_sha256=lock_file_sha,
                human_gold_artifact=gold,
            )


if __name__ == "__main__":
    unittest.main()
