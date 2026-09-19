"""Human-gold calibration for a pre-frozen two-aligner internal selector.

The production rule calibrated here was frozen before the final replacement
human label was revealed:

* require predictions from two independent direct-final-mix aligners;
* compute a deterministic lexical-ratio prior from the editor-owned cue interval
  and canonical segment lexical-unit counts;
* choose the aligner prediction nearest that prior;
* if both predictions are exactly equally distant, choose their rounded median.

No numeric selector threshold is fitted from human gold.  Human gold is used only
for the ordinary locked 8+4 production calibration gate.  This lets one aligner
rescue the other's singing failure without pretending either backend is globally
calibrated in isolation.
"""

from __future__ import annotations

import hashlib
import json
import math
from statistics import median
from typing import Any, Mapping, Sequence

from lyric_aligner.evaluation.human_boundary_anchor import HUMAN_ANCHOR_PRODUCTION_POLICY
from lyric_aligner.evaluation.production_calibration import (
    evaluate_human_gold_backend_calibration,
    validate_human_boundary_gold_artifact,
)


INTERNAL_SELECTIVE_CONSENSUS_CALIBRATION_SCHEMA_VERSION = (
    "internal-joint-selector-calibration-1.0"
)
INTERNAL_SELECTIVE_CONSENSUS_POLICY_ID = "dual-aligner-nearest-lexical-ratio-prior-v1"
INTERNAL_SELECTIVE_CONSENSUS_BOUNDARY_KIND = "internal"
INTERNAL_SELECTIVE_CONSENSUS_PRIOR_DEFINITION = (
    "editor_interval_projected_by_canonical_alignment_lexical_unit_count_ratio_at_internal_boundary"
)
INTERNAL_SELECTIVE_CONSENSUS_SELECTOR_DEFINITION = (
    "require_two_direct_aligners_choose_prediction_nearest_lexical_ratio_prior_exact_distance_tie_rounded_median"
)
# Immutable pre-final-label evidence.  The candidate was executed successfully at
# 2026-09-06T00:08Z while one replacement human label was still pending.  These
# hashes bind production promotion to that historical frozen rule rather than a
# post-hoc description of it.
INTERNAL_SELECTIVE_CONSENSUS_FREEZE_TASK_ID = (
    "subtitle-internal-lexical-prior-selector-v1-eval-20260906-0806"
)
INTERNAL_SELECTIVE_CONSENSUS_FREEZE_PLAN_SHA256 = (
    "c0ec4f3abefa6507486ae386e97cfe7161a2f74252dde680e8e30ad552b37f30"
)
INTERNAL_SELECTIVE_CONSENSUS_FREEZE_EVALUATOR_SHA256 = (
    "3f300ff1d3f8673f6c3a629be4caeea6f753e966e59c8f939a22f69388dd117f"
)
INTERNAL_SELECTIVE_CONSENSUS_FREEZE_EVALUATION_ARTIFACT_SHA256 = (
    "504d8567bd43692d24ba8108cc32c4261536f3470caa214ef6f53ea4ed703db8"
)
INTERNAL_SELECTIVE_CONSENSUS_FREEZE_FINISHED_AT = "2026-09-06T00:08:47.5310534Z"
INTERNAL_SELECTIVE_CONSENSUS_FROZEN_SOURCE_SCOPE_SHA256 = {
    "sofa_mandarin_v1": "5683a7fe937c9920ebdc7c6bb727401f286e15bf2149939fe93ffbf6ed940a5d",
    "hubertfa_mandarin_v1": "5f7760b3f019a4eff158811a2e167f0a30d9664c2e736658151fc0f1bac686a3",
}


class SelectiveConsensusCalibrationError(ValueError):
    """Raised when a joint-selector calibration artifact is unsafe."""


def frozen_selector_evidence() -> dict[str, str]:
    return {
        "task_id": INTERNAL_SELECTIVE_CONSENSUS_FREEZE_TASK_ID,
        "plan_sha256": INTERNAL_SELECTIVE_CONSENSUS_FREEZE_PLAN_SHA256,
        "evaluator_sha256": INTERNAL_SELECTIVE_CONSENSUS_FREEZE_EVALUATOR_SHA256,
        "evaluation_artifact_sha256": (
            INTERNAL_SELECTIVE_CONSENSUS_FREEZE_EVALUATION_ARTIFACT_SHA256
        ),
        "finished_at": INTERNAL_SELECTIVE_CONSENSUS_FREEZE_FINISHED_AT,
        "source_scope_artifact_sha256": dict(
            sorted(INTERNAL_SELECTIVE_CONSENSUS_FROZEN_SOURCE_SCOPE_SHA256.items())
        ),
    }


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _verified_artifact_sha(artifact: Mapping[str, Any], *, label: str) -> str:
    claimed = str(artifact.get("artifact_sha256") or "").strip()
    unsigned = dict(artifact)
    unsigned.pop("artifact_sha256", None)
    if len(claimed) != 64 or claimed != sha256_json(unsigned):
        raise SelectiveConsensusCalibrationError(f"{label} artifact SHA is invalid")
    return claimed


def _nearest_rank(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))
    return float(ordered[index])


def _source_identity(calibration: Mapping[str, Any]) -> dict[str, Any]:
    scope = calibration.get("scope")
    if not isinstance(scope, Mapping):
        raise SelectiveConsensusCalibrationError("source calibration scope is missing")
    if str(scope.get("boundary_kind") or "") != "internal":
        raise SelectiveConsensusCalibrationError("joint selector accepts internal source calibrations only")
    required_scope = (
        "audio_basis",
        "language",
        "backend_version",
        "backend_profile_id",
        "window_policy_id",
        "model_id",
        "model_revision",
        "implementation_revision",
        "adapter_contract_revision",
        "lexical_id",
        "lexical_revision",
    )
    if any(not str(scope.get(key) or "").strip() for key in required_scope):
        raise SelectiveConsensusCalibrationError("source calibration scope identity is incomplete")
    if str(scope.get("audio_basis") or "") != "final_mix":
        raise SelectiveConsensusCalibrationError("joint selector source must use final_mix")
    backend_id = str(calibration.get("backend_id") or "").strip()
    family = str(calibration.get("family") or "").strip()
    correlation_group = str(calibration.get("correlation_group") or "").strip()
    if not all((backend_id, family, correlation_group)):
        raise SelectiveConsensusCalibrationError("source calibration backend identity is incomplete")
    artifact_sha = _verified_artifact_sha(calibration, label=f"{backend_id} source calibration")
    return {
        "backend_id": backend_id,
        "family": family,
        "correlation_group": correlation_group,
        "backend_profile_id": str(scope["backend_profile_id"]),
        "backend_version": str(scope["backend_version"]),
        "model_id": str(scope["model_id"]),
        "model_revision": str(scope["model_revision"]),
        "implementation_revision": str(scope["implementation_revision"]),
        "adapter_contract_revision": str(scope["adapter_contract_revision"]),
        "window_policy_id": str(scope["window_policy_id"]),
        "lexical_id": str(scope["lexical_id"]),
        "lexical_revision": str(scope["lexical_revision"]),
        "language": str(scope["language"]),
        "audio_basis": "final_mix",
        "source_calibration_artifact_sha256": artifact_sha,
    }


def _record_map(calibration: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    records = calibration.get("records")
    if not isinstance(records, list) or len(records) != 12:
        raise SelectiveConsensusCalibrationError("source internal calibration must contain exactly 12 records")
    result: dict[str, Mapping[str, Any]] = {}
    for row in records:
        if not isinstance(row, Mapping):
            raise SelectiveConsensusCalibrationError("source calibration record must be an object")
        record_id = str(row.get("id") or "").strip()
        if not record_id or record_id in result:
            raise SelectiveConsensusCalibrationError("source calibration record IDs are invalid/duplicated")
        if str(row.get("boundary_kind") or "") != "internal":
            raise SelectiveConsensusCalibrationError("source calibration record boundary kind is not internal")
        result[record_id] = row
    return result


def _verify_blind_scope_artifact(
    artifact: Mapping[str, Any],
    *,
    expected_profile_id: str,
    expected_final_audio_sha256: str,
    expected_full_selection_lock_sha256: str,
) -> dict[str, Any]:
    """Verify one pre-human full-blind internal scope and its frozen identity."""

    claimed = _verified_artifact_sha(artifact, label=f"{expected_profile_id} blind scope")
    expected_claimed = INTERNAL_SELECTIVE_CONSENSUS_FROZEN_SOURCE_SCOPE_SHA256.get(
        expected_profile_id
    )
    if claimed != expected_claimed:
        raise SelectiveConsensusCalibrationError(
            "blind scope artifact differs from the pre-human frozen source"
        )
    if str(artifact.get("schema_version") or "") != "subtitle-machine-consensus-scope-1.2-full-adapter-contract":
        raise SelectiveConsensusCalibrationError("blind scope schema is unsupported")
    if str(artifact.get("profile_id") or "") != expected_profile_id:
        raise SelectiveConsensusCalibrationError("blind scope profile identity mismatch")
    if str(artifact.get("boundary_kind") or "") != "internal":
        raise SelectiveConsensusCalibrationError("blind scope boundary kind is not internal")
    if str(artifact.get("final_audio_sha256") or "") != expected_final_audio_sha256:
        raise SelectiveConsensusCalibrationError("blind scope final-audio identity mismatch")
    if str(artifact.get("selection_lock_sha256") or "") != expected_full_selection_lock_sha256:
        raise SelectiveConsensusCalibrationError("blind scope full-selection lock mismatch")
    if int(artifact.get("record_count") or 0) != 30:
        raise SelectiveConsensusCalibrationError("blind scope must contain the full 30-case internal population")
    predictions = artifact.get("predictions")
    if not isinstance(predictions, Mapping) or len(predictions) != 30:
        raise SelectiveConsensusCalibrationError("blind scope prediction map is incomplete")
    if int(artifact.get("nonnull_prediction_count") or 0) != sum(
        value is not None for value in predictions.values()
    ):
        raise SelectiveConsensusCalibrationError("blind scope non-null prediction count mismatch")
    return {
        "artifact_sha256": claimed,
        "profile_id": expected_profile_id,
        "backend_id": str(artifact.get("backend_id") or ""),
        "backend_version": str(artifact.get("backend_version") or ""),
        "family": str(artifact.get("family") or ""),
        "correlation_group": str(artifact.get("correlation_group") or ""),
        "model_id": str(artifact.get("model_id") or ""),
        "model_revision": str(artifact.get("model_revision") or ""),
        "implementation_revision": str(artifact.get("implementation_revision") or ""),
        "adapter_contract_revision": str(artifact.get("adapter_contract_revision") or ""),
        "window_policy_id": str(artifact.get("window_policy_id") or ""),
        "predictions": dict(predictions),
    }


def _verify_source_calibration_against_human_gold(
    calibration: Mapping[str, Any],
    *,
    human_gold_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify a failed-or-passed source calibration still comes from real human gold.

    Joint-selector authority deliberately does not require either source backend to
    pass on its own.  Therefore we cannot use the ordinary authoritative check as
    the source trust boundary.  Instead, reconstruct the production calibration
    from the embedded human gold and the source record predictions, then require
    byte-equivalent core fields.  Optional prediction-provenance metadata remains
    covered by the source artifact SHA but is not synthesized by the generic
    production calibration evaluator.
    """

    identity = _source_identity(calibration)
    scope = calibration.get("scope")
    if not isinstance(scope, Mapping):
        raise SelectiveConsensusCalibrationError("source calibration scope is missing")
    records = _record_map(calibration)
    predictions = {record_id: row.get("predicted_ms") for record_id, row in records.items()}
    recomputed = evaluate_human_gold_backend_calibration(
        backend_id=identity["backend_id"],
        family=identity["family"],
        correlation_group=identity["correlation_group"],
        scope=dict(scope),
        human_gold_artifact=human_gold_artifact,
        predictions_ms=predictions,
    )
    actual_core = dict(calibration)
    actual_core.pop("artifact_sha256", None)
    actual_core.pop("prediction_provenance", None)
    recomputed_core = dict(recomputed)
    recomputed_core.pop("artifact_sha256", None)
    if actual_core != recomputed_core:
        raise SelectiveConsensusCalibrationError(
            "source calibration cannot be reproduced from the bound human gold"
        )
    prediction_provenance = calibration.get("prediction_provenance")
    if prediction_provenance is not None:
        if not isinstance(prediction_provenance, Mapping):
            raise SelectiveConsensusCalibrationError("source prediction provenance is malformed")
        if prediction_provenance.get("production_provenance_complete") is not True:
            raise SelectiveConsensusCalibrationError("source prediction provenance is incomplete")
        if str(prediction_provenance.get("selection_lock_sha256") or "") != str(
            human_gold_artifact.get("selection_lock_sha256") or ""
        ):
            raise SelectiveConsensusCalibrationError(
                "source prediction provenance belongs to another selection lock"
            )
        for key in (
            "prediction_artifact_sha256",
            "predictions_sha256",
            "source_blind_scope_artifact_sha256",
        ):
            value = str(prediction_provenance.get(key) or "")
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise SelectiveConsensusCalibrationError(
                    f"source prediction provenance {key} is invalid"
                )
    return identity


def select_nearest_lexical_prior_prediction(
    predictions_by_profile: Mapping[str, int | None],
    *,
    lexical_prior_ms: int,
) -> tuple[int | None, str | None, str]:
    """Apply the frozen selector without using human-gold outcomes."""

    if len(predictions_by_profile) != 2:
        raise SelectiveConsensusCalibrationError("joint selector requires exactly two backend profiles")
    candidates: list[tuple[str, int, int]] = []
    for profile_id, raw in sorted(predictions_by_profile.items()):
        if raw is None:
            continue
        value = int(raw)
        candidates.append((str(profile_id), value, abs(value - int(lexical_prior_ms))))
    if len(candidates) != 2:
        return None, None, "requires_two_direct_aligners"
    candidates.sort(key=lambda item: (item[2], item[0]))
    first, second = candidates
    if first[2] == second[2]:
        return (
            int(round((first[1] + second[1]) / 2.0)),
            "tie_median",
            "equal_distance_to_lexical_ratio_prior_use_median",
        )
    return first[1], first[0], "choose_direct_aligner_nearest_lexical_ratio_prior"


def _row_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    policy = HUMAN_ANCHOR_PRODUCTION_POLICY
    predicted = [row for row in rows if row.get("selected_ms") is not None]
    errors_ms = [float(row["effective_error_ms"]) for row in predicted]
    errors_frames = [value / policy.frame_ms for value in errors_ms]
    return {
        "gold_boundary_count": len(rows),
        "prediction_count": len(predicted),
        "prediction_coverage": round(len(predicted) / len(rows), 6) if rows else 0.0,
        "distinct_track_count": len({str(row.get("track") or "") for row in rows if str(row.get("track") or "")}),
        "median_effective_error_ms": None if not errors_ms else float(median(errors_ms)),
        "p90_effective_error_ms": _nearest_rank(errors_ms, 0.90),
        "max_effective_error_ms": None if not errors_ms else float(max(errors_ms)),
        "median_effective_error_frames": None if not errors_frames else float(median(errors_frames)),
        "p90_effective_error_frames": _nearest_rank(errors_frames, 0.90),
        "max_effective_error_frames": None if not errors_frames else float(max(errors_frames)),
        "within_1_frame_fraction": None if not errors_frames else sum(value <= 1.0 for value in errors_frames) / len(errors_frames),
        "within_3_frames_fraction": None if not errors_frames else sum(value <= 3.0 for value in errors_frames) / len(errors_frames),
        "catastrophic_fraction": (
            None
            if not errors_frames
            else sum(value >= policy.catastrophic_error_frames for value in errors_frames) / len(errors_frames)
        ),
    }


def _partition_pass(summary: Mapping[str, Any], *, expected_count: int) -> bool:
    policy = HUMAN_ANCHOR_PRODUCTION_POLICY
    if int(summary.get("gold_boundary_count") or -1) != expected_count:
        return False
    if int(summary.get("distinct_track_count") or 0) < policy.min_distinct_tracks:
        return False
    if float(summary.get("prediction_coverage") or 0.0) < policy.min_prediction_coverage:
        return False
    required = (
        summary.get("median_effective_error_frames"),
        summary.get("p90_effective_error_frames"),
        summary.get("max_effective_error_frames"),
        summary.get("catastrophic_fraction"),
    )
    if any(value is None for value in required):
        return False
    return bool(
        float(summary["median_effective_error_frames"]) <= policy.max_median_effective_error_frames
        and float(summary["p90_effective_error_frames"]) <= policy.max_p90_effective_error_frames
        and float(summary["max_effective_error_frames"]) <= policy.max_single_effective_error_frames
        and float(summary["catastrophic_fraction"]) <= policy.max_catastrophic_fraction
    )


def build_internal_selective_consensus_calibration(
    *,
    suite_summary: Mapping[str, Any],
    human_gold_artifact: Mapping[str, Any],
    source_calibrations: Sequence[Mapping[str, Any]],
    source_blind_scope_artifacts: Sequence[Mapping[str, Any]],
    selector_priors_ms: Mapping[str, int],
    selector_prior_provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Calibrate the pre-frozen nearest-lexical-prior joint selector on 8+4 gold."""

    if len(source_calibrations) != 2:
        raise SelectiveConsensusCalibrationError("joint selector requires exactly two source calibrations")
    if len(source_blind_scope_artifacts) != 2:
        raise SelectiveConsensusCalibrationError("joint selector requires exactly two frozen blind source scopes")
    suite_sha = str(suite_summary.get("suite_sha256") or "").strip()
    unsigned_suite = dict(suite_summary)
    unsigned_suite.pop("suite_sha256", None)
    if len(suite_sha) != 64 or suite_sha != sha256_json(unsigned_suite):
        raise SelectiveConsensusCalibrationError("source calibration suite SHA is invalid")
    human_gold_sha = str(suite_summary.get("human_gold_artifact_sha256") or "").strip()
    final_audio_sha = str(suite_summary.get("final_audio_sha256") or "").strip()
    suite_selection_lock_sha = str(suite_summary.get("selection_lock_sha256") or "").strip()
    if len(human_gold_sha) != 64 or len(final_audio_sha) != 64 or len(suite_selection_lock_sha) != 64:
        raise SelectiveConsensusCalibrationError("suite human-gold/final-audio/selection-lock identity is invalid")
    verified_gold = validate_human_boundary_gold_artifact(human_gold_artifact)
    if (
        str(verified_gold["artifact_sha256"]) != human_gold_sha
        or str(verified_gold["final_audio_sha256"]) != final_audio_sha
        or str(verified_gold["selection_lock_sha256"]) != suite_selection_lock_sha
    ):
        raise SelectiveConsensusCalibrationError(
            "embedded human gold does not match the source calibration suite"
        )
    full_selection_lock_sha = str(
        human_gold_artifact.get("source_full_selection_lock_sha256") or ""
    )
    if len(full_selection_lock_sha) != 64 or any(
        character not in "0123456789abcdef" for character in full_selection_lock_sha
    ):
        raise SelectiveConsensusCalibrationError(
            "human anchor gold does not bind the full pre-human selection lock"
        )

    blind_by_profile: dict[str, Mapping[str, Any]] = {}
    for raw_scope in source_blind_scope_artifacts:
        if not isinstance(raw_scope, Mapping):
            raise SelectiveConsensusCalibrationError("blind source scope must be an object")
        profile_id = str(raw_scope.get("profile_id") or "").strip()
        if not profile_id or profile_id in blind_by_profile:
            raise SelectiveConsensusCalibrationError("blind source scope profile IDs are invalid/duplicated")
        blind_by_profile[profile_id] = raw_scope
    if set(blind_by_profile) != set(INTERNAL_SELECTIVE_CONSENSUS_FROZEN_SOURCE_SCOPE_SHA256):
        raise SelectiveConsensusCalibrationError(
            "blind source scope profile set differs from the pre-human frozen evaluation"
        )
    verified_blind = {
        profile_id: _verify_blind_scope_artifact(
            blind_by_profile[profile_id],
            expected_profile_id=profile_id,
            expected_final_audio_sha256=final_audio_sha,
            expected_full_selection_lock_sha256=full_selection_lock_sha,
        )
        for profile_id in sorted(blind_by_profile)
    }

    identities = [
        _verify_source_calibration_against_human_gold(
            value,
            human_gold_artifact=human_gold_artifact,
        )
        for value in source_calibrations
    ]
    for key in ("backend_id", "family", "correlation_group", "backend_profile_id"):
        if len({row[key] for row in identities}) != 2:
            raise SelectiveConsensusCalibrationError(f"joint selector source {key} values are not independent")
    if len({row["language"] for row in identities}) != 1:
        raise SelectiveConsensusCalibrationError("joint selector source languages differ")
    if len({row["lexical_id"] for row in identities}) != 1 or len({row["lexical_revision"] for row in identities}) != 1:
        raise SelectiveConsensusCalibrationError("joint selector source lexical contracts differ")
    identity_by_profile = {row["backend_profile_id"]: row for row in identities}
    for profile_id, identity in identity_by_profile.items():
        blind = verified_blind.get(profile_id)
        if not isinstance(blind, Mapping):
            raise SelectiveConsensusCalibrationError("joint selector source lacks its frozen blind scope")
        for key in (
            "backend_id",
            "backend_version",
            "family",
            "correlation_group",
            "model_id",
            "model_revision",
            "implementation_revision",
            "adapter_contract_revision",
            "window_policy_id",
        ):
            if str(identity.get(key) or "") != str(blind.get(key) or ""):
                raise SelectiveConsensusCalibrationError(
                    f"source calibration {key} differs from its frozen blind scope"
                )

    gold_hashes = {str(value.get("gold_sha256") or "") for value in source_calibrations}
    if len(gold_hashes) != 1 or any(len(value) != 64 for value in gold_hashes):
        raise SelectiveConsensusCalibrationError("source calibrations do not share one internal scoped-gold hash")
    internal_scoped_gold_sha = next(iter(gold_hashes))
    full_human_gold_records_sha = str(verified_gold["gold_sha256"])
    maps = [_record_map(value) for value in source_calibrations]
    if set(maps[0]) != set(maps[1]) or len(maps[0]) != 12:
        raise SelectiveConsensusCalibrationError("source calibration record sets differ")
    calibration_by_profile = {
        str(value["scope"]["backend_profile_id"]): value for value in source_calibrations
    }
    for profile_id, calibration in calibration_by_profile.items():
        blind = verified_blind[profile_id]
        blind_predictions = blind["predictions"]
        source_map = _record_map(calibration)
        for record_id, source_row in source_map.items():
            if record_id not in blind_predictions:
                raise SelectiveConsensusCalibrationError(
                    "anchor calibration record is absent from the frozen full-blind scope"
                )
            if source_row.get("predicted_ms") != blind_predictions.get(record_id):
                raise SelectiveConsensusCalibrationError(
                    "anchor calibration prediction differs from the frozen blind prediction"
                )
        prediction_provenance = calibration.get("prediction_provenance")
        if not isinstance(prediction_provenance, Mapping):
            raise SelectiveConsensusCalibrationError(
                "source calibration lacks blind-scope prediction provenance"
            )
        if str(prediction_provenance.get("source_blind_scope_artifact_sha256") or "") != str(
            blind["artifact_sha256"]
        ):
            raise SelectiveConsensusCalibrationError(
                "source calibration blind-scope provenance does not match the frozen source"
            )
    if set(selector_priors_ms) != set(maps[0]):
        raise SelectiveConsensusCalibrationError("selector-prior IDs must exactly match human-gold record IDs")

    provenance = dict(selector_prior_provenance)
    required_provenance = {
        "selection_lock_sha256",
        "lexical_id",
        "lexical_revision",
        "prior_definition",
        "selector_frozen_before_final_replacement_gold",
    }
    if set(provenance) != required_provenance:
        raise SelectiveConsensusCalibrationError("selector prior provenance fields are incomplete")
    if len(str(provenance["selection_lock_sha256"])) != 64:
        raise SelectiveConsensusCalibrationError("selector prior selection-lock SHA is invalid")
    if str(provenance["selection_lock_sha256"]) != suite_selection_lock_sha:
        raise SelectiveConsensusCalibrationError("selector prior belongs to another anchor selection lock")
    if str(provenance["lexical_id"]) != identities[0]["lexical_id"] or str(provenance["lexical_revision"]) != identities[0]["lexical_revision"]:
        raise SelectiveConsensusCalibrationError("selector prior lexical contract differs from backend scope")
    if str(provenance["prior_definition"]) != INTERNAL_SELECTIVE_CONSENSUS_PRIOR_DEFINITION:
        raise SelectiveConsensusCalibrationError("selector prior definition is invalid")
    if provenance["selector_frozen_before_final_replacement_gold"] is not True:
        raise SelectiveConsensusCalibrationError("selector lacks pre-final-gold freeze attestation")

    rows: list[dict[str, Any]] = []
    profiles = [row["backend_profile_id"] for row in identities]
    for record_id in sorted(maps[0]):
        left = maps[0][record_id]
        right = maps[1][record_id]
        for key in ("gold_ms", "gold_uncertainty_ms", "partition", "track", "boundary_kind"):
            if left.get(key) != right.get(key):
                raise SelectiveConsensusCalibrationError(
                    f"source calibration record identity differs for {record_id}: {key}"
                )
        partition = str(left.get("partition") or "")
        if partition not in {"calibration", "holdout"}:
            raise SelectiveConsensusCalibrationError("joint selector record partition is invalid")
        prior_ms = int(selector_priors_ms[record_id])
        predictions = {
            profiles[0]: left.get("predicted_ms"),
            profiles[1]: right.get("predicted_ms"),
        }
        selected_ms, selected_profile, reason = select_nearest_lexical_prior_prediction(
            predictions,
            lexical_prior_ms=prior_ms,
        )
        raw_error_ms = None if selected_ms is None else abs(selected_ms - int(left["gold_ms"]))
        effective_error_ms = (
            None
            if raw_error_ms is None
            else max(0, raw_error_ms - int(left["gold_uncertainty_ms"]))
        )
        rows.append(
            {
                "id": record_id,
                "partition": partition,
                "track": str(left["track"]),
                "gold_ms": int(left["gold_ms"]),
                "gold_uncertainty_ms": int(left["gold_uncertainty_ms"]),
                "lexical_ratio_prior_ms": prior_ms,
                "source_predictions_ms": predictions,
                "selected_ms": selected_ms,
                "selected_profile_id": selected_profile,
                "selection_reason": reason,
                "raw_abs_error_ms": raw_error_ms,
                "effective_error_ms": effective_error_ms,
                "effective_error_frames": (
                    None if effective_error_ms is None else effective_error_ms / HUMAN_ANCHOR_PRODUCTION_POLICY.frame_ms
                ),
            }
        )

    calibration_rows = [row for row in rows if row["partition"] == "calibration"]
    holdout_rows = [row for row in rows if row["partition"] == "holdout"]
    if len(calibration_rows) != HUMAN_ANCHOR_PRODUCTION_POLICY.min_calibration_samples or len(holdout_rows) != HUMAN_ANCHOR_PRODUCTION_POLICY.min_holdout_samples:
        raise SelectiveConsensusCalibrationError("joint selector requires the locked 8+4 anchor partition")
    calibration_summary = _row_summary(calibration_rows)
    holdout_summary = _row_summary(holdout_rows)
    all_summary = _row_summary(rows)
    calibration_passed = _partition_pass(
        calibration_summary,
        expected_count=HUMAN_ANCHOR_PRODUCTION_POLICY.min_calibration_samples,
    )
    holdout_passed = _partition_pass(
        holdout_summary,
        expected_count=HUMAN_ANCHOR_PRODUCTION_POLICY.min_holdout_samples,
    )
    passed = bool(calibration_passed and holdout_passed)
    reasons: list[str] = []
    if not calibration_passed:
        reasons.append("joint_selector_calibration_partition_exceeds_anchor_policy")
    if not holdout_passed:
        reasons.append("joint_selector_holdout_partition_exceeds_anchor_policy")

    artifact: dict[str, Any] = {
        "schema_version": INTERNAL_SELECTIVE_CONSENSUS_CALIBRATION_SCHEMA_VERSION,
        "policy_id": INTERNAL_SELECTIVE_CONSENSUS_POLICY_ID,
        "authority_class": "human_gold_production_joint_selector",
        "boundary_kind": "internal",
        "audio_basis": "final_mix",
        "language": identities[0]["language"],
        "source_suite_sha256": suite_sha,
        "selection_lock_sha256": suite_selection_lock_sha,
        "human_gold_artifact_sha256": human_gold_sha,
        "human_gold_records_sha256": full_human_gold_records_sha,
        "internal_scoped_gold_sha256": internal_scoped_gold_sha,
        "human_gold_artifact": dict(human_gold_artifact),
        "final_audio_sha256": final_audio_sha,
        "selector_prior_provenance": provenance,
        "selector_freeze_evidence": frozen_selector_evidence(),
        "selector_priors_sha256": sha256_json({key: int(selector_priors_ms[key]) for key in sorted(selector_priors_ms)}),
        "selector_definition": INTERNAL_SELECTIVE_CONSENSUS_SELECTOR_DEFINITION,
        "backend_sources": sorted(identities, key=lambda row: row["backend_profile_id"]),
        "source_calibration_artifacts": {
            str(value["artifact_sha256"]): dict(value)
            for value in sorted(
                source_calibrations,
                key=lambda row: str(row["scope"]["backend_profile_id"]),
            )
        },
        "source_blind_scope_artifacts": {
            profile_id: dict(blind_by_profile[profile_id])
            for profile_id in sorted(blind_by_profile)
        },
        "production_policy": dict(HUMAN_ANCHOR_PRODUCTION_POLICY.__dict__),
        "passed": passed,
        "reasons": reasons,
        "summary": {
            "calibration": calibration_summary,
            "holdout": holdout_summary,
            "all": all_summary,
        },
        "records": rows,
        "subtitle_mutation_performed": False,
    }
    artifact["artifact_sha256"] = sha256_json(artifact)
    return artifact


def internal_selective_consensus_calibration_is_authoritative(
    artifact: Mapping[str, Any],
    *,
    expected_final_audio_sha256: str,
    expected_source_suite_sha256: str | None = None,
    source_calibrations: Sequence[Mapping[str, Any]] | None = None,
) -> bool:
    """Verify the exact frozen-selector artifact and its bound source calibrations."""

    try:
        if artifact.get("schema_version") != INTERNAL_SELECTIVE_CONSENSUS_CALIBRATION_SCHEMA_VERSION:
            return False
        if artifact.get("policy_id") != INTERNAL_SELECTIVE_CONSENSUS_POLICY_ID:
            return False
        if str(artifact.get("authority_class") or "") != "human_gold_production_joint_selector":
            return False
        if str(artifact.get("boundary_kind") or "") != "internal" or str(artifact.get("audio_basis") or "") != "final_mix":
            return False
        if str(artifact.get("selector_definition") or "") != INTERNAL_SELECTIVE_CONSENSUS_SELECTOR_DEFINITION:
            return False
        if artifact.get("selector_freeze_evidence") != frozen_selector_evidence():
            return False
        if artifact.get("production_policy") != dict(HUMAN_ANCHOR_PRODUCTION_POLICY.__dict__):
            return False
        if not bool(artifact.get("passed")) or artifact.get("reasons") != [] or bool(artifact.get("subtitle_mutation_performed")):
            return False
        if str(artifact.get("final_audio_sha256") or "") != str(expected_final_audio_sha256):
            return False
        if expected_source_suite_sha256 is not None and str(artifact.get("source_suite_sha256") or "") != str(expected_source_suite_sha256):
            return False
        _verified_artifact_sha(artifact, label="joint selector calibration")
        human_gold_artifact = artifact.get("human_gold_artifact")
        if not isinstance(human_gold_artifact, Mapping):
            return False
        verified_gold = validate_human_boundary_gold_artifact(human_gold_artifact)
        if (
            str(verified_gold["artifact_sha256"]) != str(artifact.get("human_gold_artifact_sha256") or "")
            or str(verified_gold["gold_sha256"]) != str(artifact.get("human_gold_records_sha256") or "")
            or str(verified_gold["final_audio_sha256"]) != str(artifact.get("final_audio_sha256") or "")
            or str(verified_gold["selection_lock_sha256"]) != str(artifact.get("selection_lock_sha256") or "")
        ):
            return False
        provenance = artifact.get("selector_prior_provenance")
        if not isinstance(provenance, Mapping) or provenance.get("selector_frozen_before_final_replacement_gold") is not True:
            return False
        if len(str(artifact.get("selection_lock_sha256") or "")) != 64:
            return False
        if str(provenance.get("selection_lock_sha256") or "") != str(artifact.get("selection_lock_sha256") or ""):
            return False
        if str(provenance.get("prior_definition") or "") != INTERNAL_SELECTIVE_CONSENSUS_PRIOR_DEFINITION:
            return False
        full_selection_lock_sha = str(
            human_gold_artifact.get("source_full_selection_lock_sha256") or ""
        )
        if len(full_selection_lock_sha) != 64:
            return False
        blind_catalog = artifact.get("source_blind_scope_artifacts")
        if not isinstance(blind_catalog, Mapping) or set(blind_catalog) != set(
            INTERNAL_SELECTIVE_CONSENSUS_FROZEN_SOURCE_SCOPE_SHA256
        ):
            return False
        verified_blind = {}
        for profile_id in sorted(blind_catalog):
            raw_blind = blind_catalog[profile_id]
            if not isinstance(raw_blind, Mapping):
                return False
            verified_blind[profile_id] = _verify_blind_scope_artifact(
                raw_blind,
                expected_profile_id=str(profile_id),
                expected_final_audio_sha256=str(artifact.get("final_audio_sha256") or ""),
                expected_full_selection_lock_sha256=full_selection_lock_sha,
            )
        source_catalog = artifact.get("source_calibration_artifacts")
        if not isinstance(source_catalog, Mapping) or len(source_catalog) != 2:
            return False
        embedded_source_calibrations = []
        for claimed_sha, raw_source in source_catalog.items():
            if not isinstance(raw_source, Mapping) or str(raw_source.get("artifact_sha256") or "") != str(claimed_sha):
                return False
            embedded_source_calibrations.append(raw_source)
        scoped_gold_hashes = {
            str(value.get("gold_sha256") or "") for value in embedded_source_calibrations
        }
        if (
            len(scoped_gold_hashes) != 1
            or next(iter(scoped_gold_hashes)) != str(artifact.get("internal_scoped_gold_sha256") or "")
        ):
            return False
        actual_sources = sorted(
            (
                _verify_source_calibration_against_human_gold(
                    value,
                    human_gold_artifact=human_gold_artifact,
                )
                for value in embedded_source_calibrations
            ),
            key=lambda row: row["backend_profile_id"],
        )
        sources = artifact.get("backend_sources")
        if not isinstance(sources, list) or len(sources) != 2 or any(not isinstance(row, Mapping) for row in sources):
            return False
        for key in ("backend_id", "family", "correlation_group", "backend_profile_id"):
            if len({str(row.get(key) or "") for row in sources}) != 2:
                return False
        if [dict(row) for row in sources] != actual_sources:
            return False
        for source in sources:
            profile_id = str(source["backend_profile_id"])
            blind = verified_blind.get(profile_id)
            if not isinstance(blind, Mapping):
                return False
            for key in (
                "backend_id",
                "backend_version",
                "family",
                "correlation_group",
                "model_id",
                "model_revision",
                "implementation_revision",
                "adapter_contract_revision",
                "window_policy_id",
            ):
                if str(source.get(key) or "") != str(blind.get(key) or ""):
                    return False
        records = artifact.get("records")
        if not isinstance(records, list) or len(records) != 12:
            return False
        priors = {str(row.get("id") or ""): int(row.get("lexical_ratio_prior_ms")) for row in records}
        if len(priors) != 12 or sha256_json({key: priors[key] for key in sorted(priors)}) != str(artifact.get("selector_priors_sha256") or ""):
            return False
        profile_ids = [str(row["backend_profile_id"]) for row in sources]
        embedded_maps = {
            str(value["scope"]["backend_profile_id"]): _record_map(value)
            for value in embedded_source_calibrations
        }
        if set(embedded_maps) != set(profile_ids):
            return False
        for source_calibration in embedded_source_calibrations:
            profile_id = str(source_calibration["scope"]["backend_profile_id"])
            prediction_provenance = source_calibration.get("prediction_provenance")
            if not isinstance(prediction_provenance, Mapping):
                return False
            if str(prediction_provenance.get("source_blind_scope_artifact_sha256") or "") != str(
                verified_blind[profile_id]["artifact_sha256"]
            ):
                return False
        rebuilt_rows: list[dict[str, Any]] = []
        for row in records:
            record_id = str(row.get("id") or "")
            predictions = row.get("source_predictions_ms")
            if not isinstance(predictions, Mapping) or set(predictions) != set(profile_ids):
                return False
            for profile_id in profile_ids:
                if record_id not in embedded_maps[profile_id]:
                    return False
                expected_prediction = embedded_maps[profile_id][record_id].get("predicted_ms")
                if predictions[profile_id] != expected_prediction:
                    return False
                blind_predictions = verified_blind[profile_id]["predictions"]
                if record_id not in blind_predictions or blind_predictions.get(record_id) != expected_prediction:
                    return False
            selected, selected_profile, reason = select_nearest_lexical_prior_prediction(
                {key: predictions[key] for key in profile_ids},
                lexical_prior_ms=priors[record_id],
            )
            if selected != row.get("selected_ms") or selected_profile != row.get("selected_profile_id") or reason != row.get("selection_reason"):
                return False
            raw_error = None if selected is None else abs(int(selected) - int(row["gold_ms"]))
            effective = None if raw_error is None else max(0, raw_error - int(row["gold_uncertainty_ms"]))
            if raw_error != row.get("raw_abs_error_ms") or effective != row.get("effective_error_ms"):
                return False
            rebuilt_rows.append(dict(row))
        calibration = [row for row in rebuilt_rows if row.get("partition") == "calibration"]
        holdout = [row for row in rebuilt_rows if row.get("partition") == "holdout"]
        if _row_summary(calibration) != artifact.get("summary", {}).get("calibration"):
            return False
        if _row_summary(holdout) != artifact.get("summary", {}).get("holdout"):
            return False
        if _row_summary(rebuilt_rows) != artifact.get("summary", {}).get("all"):
            return False
        if not _partition_pass(_row_summary(calibration), expected_count=HUMAN_ANCHOR_PRODUCTION_POLICY.min_calibration_samples):
            return False
        if not _partition_pass(_row_summary(holdout), expected_count=HUMAN_ANCHOR_PRODUCTION_POLICY.min_holdout_samples):
            return False
        if source_calibrations is not None:
            if len(source_calibrations) != 2:
                return False
            provided_catalog = {
                str(value.get("artifact_sha256") or ""): dict(value)
                for value in source_calibrations
                if isinstance(value, Mapping)
            }
            if provided_catalog != {
                str(key): dict(value) for key, value in source_catalog.items()
            }:
                return False
        return True
    except (KeyError, TypeError, ValueError, SelectiveConsensusCalibrationError):
        return False
