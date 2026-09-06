"""Production calibration bound to locked human-audited final-mix gold.

The generic boundary calibration evaluator is deliberately reusable for research,
fixtures and diagnostics. That does not make every statistically passing artifact
safe mutation authority. This module adds the production trust boundary: only a
structurally valid, versioned full human-boundary gold artifact or the explicitly
versioned 24-clip / 36-point production human-anchor gold artifact may be promoted
to production calibration authority under its bound policy.

Human annotation remains a procedural trust root; software cannot prove that a
human listened carefully. It can, however, prevent accidental substitution of
synthetic gold, changed partitions, stale runtime/adapter scopes, oversized
uncertainty, or unrelated fixtures.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from lyric_aligner.timeline.boundary_calibration import (
    BoundaryCalibrationPolicy,
    calibration_artifact_is_authoritative,
    evaluate_boundary_backend_calibration,
)
from lyric_aligner.evaluation.human_boundary_anchor import (
    ANCHOR_BOUNDARY_POINTS_PER_KIND,
    ANCHOR_CALIBRATION_COUNT,
    ANCHOR_HOLDOUT_COUNT,
    ANCHOR_INTERNAL_CUE_COUNT,
    ANCHOR_MIN_DISTINCT_TRACKS,
    ANCHOR_OUTER_CUE_COUNT,
    ANCHOR_TOTAL_BOUNDARY_POINTS,
    HUMAN_ANCHOR_AUDIT_UI_REVISION,
    HUMAN_ANCHOR_AUDIT_UX_REVISION,
    HUMAN_ANCHOR_GOLD_AUTHORITY,
    HUMAN_ANCHOR_GOLD_LEGACY_SCHEMA_VERSION,
    HUMAN_ANCHOR_GOLD_SCHEMA_VERSION,
    HUMAN_ANCHOR_PRODUCTION_POLICY,
    HUMAN_ANCHOR_UNCERTAINTY_POLICY_ID,
)


HUMAN_BOUNDARY_GOLD_SCHEMA_VERSION = "human-boundary-gold-2.1"
HUMAN_BOUNDARY_GOLD_AUTHORITY = "human_audited_final_mix_acoustic_boundary"
PRODUCTION_CALIBRATION_AUTHORITY_CLASS = "human_gold_production"
EXPECTED_GOLD_COUNT_PER_KIND = 30
EXPECTED_TOTAL_GOLD_COUNT = 90
SUPPORTED_PRODUCTION_AUDIO_BASES = frozenset({"final_mix", "source_projection"})


class ProductionCalibrationError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_sha(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def validate_human_boundary_gold_artifact(
    artifact: Mapping[str, Any],
    *,
    policy: BoundaryCalibrationPolicy | None = None,
) -> dict[str, Any]:
    """Validate the immutable structure required for production human gold."""

    if not isinstance(artifact, Mapping):
        raise ProductionCalibrationError("human boundary gold artifact must be an object")
    schema_version = str(artifact.get("schema_version") or "")
    audit_confirmation: dict[str, Any] | None = None
    anchor_confirmation_provenance_complete = False
    if schema_version in {HUMAN_ANCHOR_GOLD_SCHEMA_VERSION, HUMAN_ANCHOR_GOLD_LEGACY_SCHEMA_VERSION}:
        expected_policy = HUMAN_ANCHOR_PRODUCTION_POLICY
        expected_authority = HUMAN_ANCHOR_GOLD_AUTHORITY
        expected_total = ANCHOR_TOTAL_BOUNDARY_POINTS
        expected_per_kind = ANCHOR_BOUNDARY_POINTS_PER_KIND
        expected_outer_cases = ANCHOR_OUTER_CUE_COUNT
        expected_internal_cases = ANCHOR_INTERNAL_CUE_COUNT
        expected_population_counts = {"outer": 24, "internal": 12}
        expected_calibration = ANCHOR_CALIBRATION_COUNT
        expected_holdout = ANCHOR_HOLDOUT_COUNT
        expected_min_tracks = ANCHOR_MIN_DISTINCT_TRACKS
        if artifact.get("uncertainty_policy_id") != HUMAN_ANCHOR_UNCERTAINTY_POLICY_ID:
            raise ProductionCalibrationError("human anchor uncertainty policy is invalid")
        if artifact.get("production_policy") != dict(expected_policy.__dict__):
            raise ProductionCalibrationError("human anchor production policy binding is invalid")
        if schema_version == HUMAN_ANCHOR_GOLD_SCHEMA_VERSION:
            raw_confirmation = artifact.get("audit_confirmation")
            expected_confirmation_fields = {
                "recheck_file_sha256",
                "recheck_schema_version",
                "required_ui_revision",
                "required_ui_ux_revision",
                "last_confirmed_ui_revision",
                "last_confirmed_ui_ux_revision",
                "pending_case_count",
                "confirmed_case_count",
            }
            if not isinstance(raw_confirmation, Mapping) or set(raw_confirmation) != expected_confirmation_fields:
                raise ProductionCalibrationError("human anchor audit confirmation provenance is incomplete")
            if not _is_sha(raw_confirmation.get("recheck_file_sha256")):
                raise ProductionCalibrationError("human anchor audit recheck SHA is invalid")
            if str(raw_confirmation.get("recheck_schema_version") or "") != "human-boundary-anchor-audit-recheck-1.0":
                raise ProductionCalibrationError("human anchor audit recheck schema is invalid")
            if str(raw_confirmation.get("required_ui_revision") or "") != HUMAN_ANCHOR_AUDIT_UI_REVISION:
                raise ProductionCalibrationError("human anchor audit required UI revision is invalid")
            if str(raw_confirmation.get("required_ui_ux_revision") or "") != HUMAN_ANCHOR_AUDIT_UX_REVISION:
                raise ProductionCalibrationError("human anchor audit required UI UX revision is invalid")
            if str(raw_confirmation.get("last_confirmed_ui_revision") or "") != HUMAN_ANCHOR_AUDIT_UI_REVISION:
                raise ProductionCalibrationError("human anchor audit confirmed UI revision is invalid")
            if str(raw_confirmation.get("last_confirmed_ui_ux_revision") or "") != HUMAN_ANCHOR_AUDIT_UX_REVISION:
                raise ProductionCalibrationError("human anchor audit confirmed UI UX revision is invalid")
            if int(raw_confirmation.get("pending_case_count") or 0) != 0:
                raise ProductionCalibrationError("human anchor audit still has pending cases")
            if int(raw_confirmation.get("confirmed_case_count") or -1) != ANCHOR_OUTER_CUE_COUNT + ANCHOR_INTERNAL_CUE_COUNT:
                raise ProductionCalibrationError("human anchor audit confirmed case count is invalid")
            audit_confirmation = dict(raw_confirmation)
            anchor_confirmation_provenance_complete = True
    elif schema_version == HUMAN_BOUNDARY_GOLD_SCHEMA_VERSION:
        expected_policy = BoundaryCalibrationPolicy()
        expected_authority = HUMAN_BOUNDARY_GOLD_AUTHORITY
        expected_total = EXPECTED_TOTAL_GOLD_COUNT
        expected_per_kind = EXPECTED_GOLD_COUNT_PER_KIND
        expected_outer_cases = 30
        expected_internal_cases = 30
        expected_population_counts = {"outer": 60, "internal": 30}
        expected_calibration = expected_policy.min_calibration_samples
        expected_holdout = expected_policy.min_holdout_samples
        expected_min_tracks = expected_policy.min_distinct_tracks
    else:
        raise ProductionCalibrationError("unsupported human boundary gold schema")
    if policy is not None and policy != expected_policy:
        raise ProductionCalibrationError("human boundary gold policy does not match its authority schema")
    policy = expected_policy
    policy.validate()
    if artifact.get("authority") != expected_authority:
        raise ProductionCalibrationError("human boundary gold authority marker is invalid")
    language_scope = str(artifact.get("language_scope") or "").strip().lower()
    if (
        not language_scope
        or language_scope != str(artifact.get("language_scope") or "")
        or not re.fullmatch(r"[a-z0-9-]+", language_scope)
    ):
        raise ProductionCalibrationError("human boundary gold language scope is missing or non-canonical")

    claimed_artifact_sha = str(artifact.get("artifact_sha256") or "").strip()
    if not _is_sha(claimed_artifact_sha):
        raise ProductionCalibrationError("human boundary gold artifact SHA is invalid")
    without_hash = dict(artifact)
    without_hash.pop("artifact_sha256", None)
    if claimed_artifact_sha != _sha_json(without_hash):
        raise ProductionCalibrationError("human boundary gold artifact hash mismatch")

    records = artifact.get("records")
    if not isinstance(records, list) or len(records) != expected_total:
        raise ProductionCalibrationError("production human gold has the wrong boundary-point count")
    if int(artifact.get("record_count") or -1) != expected_total:
        raise ProductionCalibrationError("human boundary gold record_count mismatch")
    claimed_gold_sha = str(artifact.get("gold_sha256") or "").strip()
    if not _is_sha(claimed_gold_sha) or claimed_gold_sha != _sha_json(records):
        raise ProductionCalibrationError("human boundary gold record SHA mismatch")

    for key in (
        "selection_lock_sha256",
        "selection_lock_file_sha256",
        "outer_audit_csv_sha256",
        "internal_audit_csv_sha256",
        "final_audio_sha256",
    ):
        if not _is_sha(artifact.get(key)):
            raise ProductionCalibrationError(f"human boundary gold {key} is invalid")

    kind_counts = artifact.get("boundary_kind_counts")
    expected_kind_counts = {
        "start": expected_per_kind,
        "end": expected_per_kind,
        "internal": expected_per_kind,
    }
    if kind_counts != expected_kind_counts:
        raise ProductionCalibrationError("human boundary gold kind counts are invalid")
    if artifact.get("population_counts") != expected_population_counts:
        raise ProductionCalibrationError("human boundary gold population counts are invalid")

    seen_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ProductionCalibrationError("human boundary gold record must be an object")
        record_id = str(raw.get("id") or "").strip()
        if not record_id or record_id in seen_ids:
            raise ProductionCalibrationError("human boundary gold IDs must be unique/non-empty")
        seen_ids.add(record_id)
        if str(raw.get("kind") or "") != "boundary":
            raise ProductionCalibrationError("human gold record kind must be boundary")
        boundary_kind = str(raw.get("boundary_kind") or "")
        if boundary_kind not in {"start", "end", "internal"}:
            raise ProductionCalibrationError("human gold boundary_kind is invalid")
        partition = str(raw.get("partition") or "")
        if partition not in {"calibration", "holdout"}:
            raise ProductionCalibrationError("human gold partition is invalid")
        track = str(raw.get("track") or "").strip()
        case_id = str(raw.get("case_id") or "").strip()
        population = str(raw.get("population") or "")
        if not track or not _is_sha(case_id):
            raise ProductionCalibrationError("human gold track/case identity is incomplete")
        if boundary_kind in {"start", "end"} and population != "outer":
            raise ProductionCalibrationError("outer boundary gold has wrong population")
        if boundary_kind == "internal" and population != "internal":
            raise ProductionCalibrationError("internal boundary gold has wrong population")
        if any(key in raw for key in ("prediction_ms", "backend_id", "model_id", "model_revision")):
            raise ProductionCalibrationError("human gold must not embed backend predictions or model identity")
        try:
            gold_ms = int(raw["gold_ms"])
            uncertainty_ms = int(raw.get("gold_uncertainty_ms", 0))
            cue_number = int(raw["cue_number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProductionCalibrationError("human gold timing/cue identity is invalid") from exc
        if gold_ms < 0 or uncertainty_ms < 0 or cue_number <= 0:
            raise ProductionCalibrationError("human gold timing must be nonnegative and cue identity must be positive")
        if uncertainty_ms > policy.max_gold_uncertainty_ms:
            raise ProductionCalibrationError("human gold uncertainty exceeds production policy")
        if schema_version in {HUMAN_ANCHOR_GOLD_SCHEMA_VERSION, HUMAN_ANCHOR_GOLD_LEGACY_SCHEMA_VERSION}:
            clarity = str(raw.get("gold_clarity") or "").strip().lower()
            if clarity not in {"clear", "ambiguous"}:
                raise ProductionCalibrationError("human anchor gold must preserve its clarity class")
            if uncertainty_ms != (50 if clarity == "clear" else 100):
                raise ProductionCalibrationError("human anchor clarity/uncertainty derivation is inconsistent")
        if record_id != f"{case_id}:{boundary_kind}":
            raise ProductionCalibrationError("human gold record ID does not match its locked case/boundary identity")
        if boundary_kind == "internal":
            try:
                boundary_index = int(raw["internal_boundary_index"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ProductionCalibrationError("internal human gold lacks boundary index") from exc
            if boundary_index < 1:
                raise ProductionCalibrationError("internal human gold boundary index is invalid")
        normalized.append(dict(raw))

    outer_by_case: dict[str, dict[str, dict[str, Any]]] = {}
    internal_case_ids: set[str] = set()
    for row in normalized:
        case_id = str(row["case_id"])
        if row["population"] == "outer":
            kind = str(row["boundary_kind"])
            case = outer_by_case.setdefault(case_id, {})
            if kind in case:
                raise ProductionCalibrationError("outer human gold duplicates one case/boundary kind")
            case[kind] = row
        else:
            if case_id in internal_case_ids:
                raise ProductionCalibrationError("internal human gold duplicates one case")
            internal_case_ids.add(case_id)
    if len(outer_by_case) != expected_outer_cases or len(internal_case_ids) != expected_internal_cases:
        raise ProductionCalibrationError("human gold outer/internal case counts are invalid")
    if set(outer_by_case).intersection(internal_case_ids):
        raise ProductionCalibrationError("outer/internal human-gold case populations must be disjoint")
    for case in outer_by_case.values():
        if set(case) != {"start", "end"}:
            raise ProductionCalibrationError("each outer human-gold case must contain exactly start and end")
        start = case["start"]
        end = case["end"]
        for key in ("track", "cue_number", "partition", "case_id"):
            if start.get(key) != end.get(key):
                raise ProductionCalibrationError("outer start/end pair identity is inconsistent")

    for boundary_kind in ("start", "end", "internal"):
        rows = [row for row in normalized if row["boundary_kind"] == boundary_kind]
        if len(rows) != expected_per_kind:
            raise ProductionCalibrationError("human gold scope has the wrong point count")
        calibration = [row for row in rows if row["partition"] == "calibration"]
        holdout = [row for row in rows if row["partition"] == "holdout"]
        if len(calibration) != expected_calibration or len(holdout) != expected_holdout:
            raise ProductionCalibrationError("human gold calibration/holdout partition sizes are invalid")
        if len({row["track"] for row in calibration}) < expected_min_tracks:
            raise ProductionCalibrationError("human gold calibration partition lacks track diversity")
        if len({row["track"] for row in holdout}) < expected_min_tracks:
            raise ProductionCalibrationError("human gold holdout partition lacks track diversity")

    return {
        "artifact_sha256": claimed_artifact_sha,
        "gold_sha256": claimed_gold_sha,
        "schema_version": schema_version,
        "authority": expected_authority,
        "production_policy": policy,
        "expected_count_per_kind": expected_per_kind,
        "language_scope": language_scope,
        "selection_lock_sha256": str(artifact["selection_lock_sha256"]),
        "final_audio_sha256": str(artifact["final_audio_sha256"]),
        "audit_confirmation": audit_confirmation,
        "anchor_confirmation_provenance_complete": anchor_confirmation_provenance_complete,
        "records": normalized,
    }


def evaluate_human_gold_backend_calibration(
    *,
    backend_id: str,
    family: str,
    correlation_group: str,
    scope: Mapping[str, Any],
    human_gold_artifact: Mapping[str, Any],
    predictions_ms: Mapping[str, int | None],
    policy: BoundaryCalibrationPolicy | None = None,
) -> dict[str, Any]:
    """Evaluate one backend scope and mark it production-authoritative only via human gold."""

    verified = validate_human_boundary_gold_artifact(human_gold_artifact, policy=policy)
    policy = verified["production_policy"]
    boundary_kind = str(scope.get("boundary_kind") or "").strip()
    if boundary_kind not in {"start", "end", "internal"}:
        raise ProductionCalibrationError("production calibration scope has invalid boundary_kind")
    language = str(scope.get("language") or "").strip().lower()
    if language != str(scope.get("language") or "") or language != verified["language_scope"]:
        raise ProductionCalibrationError("production calibration language does not match human-gold language scope")
    audio_basis = str(scope.get("audio_basis") or "").strip()
    if audio_basis not in SUPPORTED_PRODUCTION_AUDIO_BASES:
        raise ProductionCalibrationError(
            "production human-gold calibration supports final_mix or source_projection audio basis"
        )
    for key in (
        "backend_version",
        "backend_profile_id",
        "window_policy_id",
        "model_id",
        "model_revision",
    ):
        if not str(scope.get(key) or "").strip():
            raise ProductionCalibrationError(
                f"production calibration scope must bind non-empty {key}"
            )
    scoped_gold = [
        row for row in verified["records"] if str(row.get("boundary_kind") or "") == boundary_kind
    ]
    expected_ids = {str(row["id"]) for row in scoped_gold}
    unknown_prediction_ids = set(str(key) for key in predictions_ms) - expected_ids
    if unknown_prediction_ids:
        raise ProductionCalibrationError("prediction artifact contains IDs outside the audited calibration scope")

    base = evaluate_boundary_backend_calibration(
        backend_id=backend_id,
        family=family,
        correlation_group=correlation_group,
        scope=scope,
        gold_records=scoped_gold,
        predictions_ms=predictions_ms,
        policy=policy,
    )
    base.pop("artifact_sha256", None)
    base["authority_class"] = PRODUCTION_CALIBRATION_AUTHORITY_CLASS
    human_gold_provenance: dict[str, Any] = {
        "schema_version": verified["schema_version"],
        "authority": verified["authority"],
        "human_gold_artifact_sha256": verified["artifact_sha256"],
        "human_gold_records_sha256": verified["gold_sha256"],
        "selection_lock_sha256": verified["selection_lock_sha256"],
        "calibration_corpus_final_audio_sha256": verified["final_audio_sha256"],
        "boundary_kind": boundary_kind,
        "scoped_gold_sha256": str(base["gold_sha256"]),
    }
    if verified["schema_version"] == HUMAN_ANCHOR_GOLD_SCHEMA_VERSION:
        confirmation = verified.get("audit_confirmation")
        if not isinstance(confirmation, Mapping):
            raise ProductionCalibrationError("human anchor audit confirmation provenance disappeared after validation")
        human_gold_provenance.update(
            {
                "audit_recheck_file_sha256": confirmation["recheck_file_sha256"],
                "audit_ui_revision": confirmation["last_confirmed_ui_revision"],
                "audit_ui_ux_revision": confirmation["last_confirmed_ui_ux_revision"],
            }
        )
    base["human_gold_provenance"] = human_gold_provenance
    base["artifact_sha256"] = _sha_json(base)
    return base


def production_calibration_artifact_is_authoritative(
    artifact: Mapping[str, Any],
    *,
    backend_id: str,
    family: str,
    correlation_group: str,
    expected_scope: Mapping[str, Any],
) -> bool:
    """Strict production authority check layered over the generic calibration validator."""

    provenance = artifact.get("human_gold_provenance")
    if not isinstance(provenance, Mapping):
        return False
    schema_version = str(provenance.get("schema_version") or "")
    if schema_version == HUMAN_BOUNDARY_GOLD_SCHEMA_VERSION:
        policy = BoundaryCalibrationPolicy()
        expected_authority = HUMAN_BOUNDARY_GOLD_AUTHORITY
    elif schema_version == HUMAN_ANCHOR_GOLD_SCHEMA_VERSION:
        policy = HUMAN_ANCHOR_PRODUCTION_POLICY
        expected_authority = HUMAN_ANCHOR_GOLD_AUTHORITY
        if not _is_sha(provenance.get("audit_recheck_file_sha256")):
            return False
        if provenance.get("audit_ui_revision") != HUMAN_ANCHOR_AUDIT_UI_REVISION:
            return False
        if provenance.get("audit_ui_ux_revision") != HUMAN_ANCHOR_AUDIT_UX_REVISION:
            return False
    else:
        return False
    scope = artifact.get("scope")
    if not isinstance(scope, Mapping):
        return False
    for key in ("implementation_revision", "adapter_contract_revision"):
        actual = str(scope.get(key) or "")
        expected = str(expected_scope.get(key) or "")
        if not _is_sha(actual) or not _is_sha(expected) or actual != expected:
            return False
    if not calibration_artifact_is_authoritative(
        artifact,
        backend_id=backend_id,
        family=family,
        correlation_group=correlation_group,
        expected_scope=expected_scope,
        policy=policy,
    ):
        return False
    if artifact.get("authority_class") != PRODUCTION_CALIBRATION_AUTHORITY_CLASS:
        return False
    if provenance.get("authority") != expected_authority:
        return False
    for key in (
        "human_gold_artifact_sha256",
        "human_gold_records_sha256",
        "selection_lock_sha256",
        "calibration_corpus_final_audio_sha256",
        "scoped_gold_sha256",
    ):
        if not _is_sha(provenance.get(key)):
            return False
    boundary_kind = str(expected_scope.get("boundary_kind") or "")
    if provenance.get("boundary_kind") != boundary_kind:
        return False
    if provenance.get("scoped_gold_sha256") != artifact.get("gold_sha256"):
        return False
    return True
