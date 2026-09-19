"""Production-grade editor baseline error profile on locked human boundary gold.

Expected-loss fallback is meaningful only when the candidate and the status-quo
editor boundary are measured against the same locked human-gold population.  This
module derives a self-hashed editor baseline artifact from an immutable Human
Anchor selection lock plus its corresponding audited gold.

The stored profile is descriptive only.  Production authority is attached only by
``authoritative_editor_profile_from_artifact`` after the artifact self-hash and all
selection/gold lineage are revalidated, avoiding circular trust in a profile's own
``production_authoritative`` flag.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from lyric_aligner.evaluation.production_calibration import (
    ProductionCalibrationError,
    validate_human_boundary_gold_artifact,
)
from lyric_aligner.timeline.boundary_risk import (
    BoundaryErrorProfile,
    BoundaryRiskError,
    build_error_profile,
)


EDITOR_RISK_PROFILE_SCHEMA_VERSION = "editor-boundary-risk-profile-1.0"
EDITOR_RISK_PROFILE_AUTHORITY = "human_gold_editor_baseline"


class EditorRiskProfileError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_sha(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _selection_outer_index(selection_lock: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    selection = selection_lock.get("selection")
    if not isinstance(selection, Mapping):
        raise EditorRiskProfileError("selection lock lacks selection payload")
    populations = selection.get("populations")
    if not isinstance(populations, Mapping):
        raise EditorRiskProfileError("selection lock lacks populations")
    outer = populations.get("outer")
    if not isinstance(outer, list) or not outer:
        raise EditorRiskProfileError("selection lock outer population is missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in outer:
        if not isinstance(row, Mapping):
            raise EditorRiskProfileError("selection outer row must be an object")
        case_id = str(row.get("case_id") or "").strip()
        if not _is_sha(case_id) or case_id in result:
            raise EditorRiskProfileError("selection outer case ids are invalid/duplicated")
        try:
            start_ms = int(row["start_ms"])
            end_ms = int(row["end_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise EditorRiskProfileError("selection outer row timing is invalid") from exc
        if start_ms < 0 or end_ms <= start_ms:
            raise EditorRiskProfileError("selection outer row interval is invalid")
        if not str(row.get("track") or "").strip():
            raise EditorRiskProfileError("selection outer row track is missing")
        partition = str(row.get("partition") or "")
        if partition not in {"calibration", "holdout"}:
            raise EditorRiskProfileError("selection outer partition is invalid")
        result[case_id] = row
    return result


def _effective_error_ms(editor_ms: int, gold_ms: int, uncertainty_ms: int) -> int:
    return max(0, abs(int(editor_ms) - int(gold_ms)) - max(0, int(uncertainty_ms)))


def _profile_to_payload(profile: BoundaryErrorProfile) -> dict[str, Any]:
    return {
        "profile_id": profile.profile_id,
        "boundary_kind": profile.boundary_kind,
        "population": profile.population,
        "sample_count": profile.sample_count,
        "predicted_count": profile.predicted_count,
        "distinct_track_count": profile.distinct_track_count,
        "coverage": profile.coverage,
        "mean_effective_error_ms": profile.mean_effective_error_ms,
        "median_effective_error_ms": profile.median_effective_error_ms,
        "p90_effective_error_ms": profile.p90_effective_error_ms,
        "max_effective_error_ms": profile.max_effective_error_ms,
        "catastrophic_fraction": profile.catastrophic_fraction,
        "catastrophic_threshold_ms": profile.catastrophic_threshold_ms,
        "production_authoritative": False,
        "provenance_sha256": "",
    }


def _profile_from_payload(payload: Mapping[str, Any]) -> BoundaryErrorProfile:
    try:
        profile = BoundaryErrorProfile(
            profile_id=str(payload.get("profile_id") or ""),
            boundary_kind=str(payload.get("boundary_kind") or ""),
            population=str(payload.get("population") or ""),
            sample_count=int(payload.get("sample_count")),
            predicted_count=int(payload.get("predicted_count")),
            distinct_track_count=int(payload.get("distinct_track_count")),
            coverage=float(payload.get("coverage")),
            mean_effective_error_ms=float(payload.get("mean_effective_error_ms")),
            median_effective_error_ms=float(payload.get("median_effective_error_ms")),
            p90_effective_error_ms=float(payload.get("p90_effective_error_ms")),
            max_effective_error_ms=float(payload.get("max_effective_error_ms")),
            catastrophic_fraction=float(payload.get("catastrophic_fraction")),
            catastrophic_threshold_ms=float(payload.get("catastrophic_threshold_ms")),
            production_authoritative=bool(payload.get("production_authoritative", False)),
            provenance_sha256=str(payload.get("provenance_sha256") or ""),
        )
    except (TypeError, ValueError) as exc:
        raise EditorRiskProfileError("editor risk profile payload is malformed") from exc
    profile.validate()
    if profile.production_authoritative or profile.provenance_sha256:
        raise EditorRiskProfileError("stored editor profile must not self-assert production authority")
    return profile


def build_editor_risk_profile_artifact(
    *,
    selection_lock: Mapping[str, Any],
    selection_lock_file_sha256: str,
    human_gold_artifact: Mapping[str, Any],
    boundary_kind: str,
    population: str = "holdout",
) -> dict[str, Any]:
    """Build a self-hashed editor baseline profile on exact Human Anchor gold."""

    if boundary_kind not in {"start", "end"}:
        raise EditorRiskProfileError("editor baseline currently supports outer start/end only")
    if population not in {"calibration", "holdout"}:
        raise EditorRiskProfileError("editor risk population must be calibration or holdout")
    if not _is_sha(selection_lock_file_sha256):
        raise EditorRiskProfileError("selection_lock_file_sha256 is invalid")
    if not isinstance(selection_lock, Mapping) or not isinstance(human_gold_artifact, Mapping):
        raise EditorRiskProfileError("selection lock and human gold must be objects")

    try:
        verified_gold = validate_human_boundary_gold_artifact(human_gold_artifact)
    except ProductionCalibrationError as exc:
        raise EditorRiskProfileError(str(exc)) from exc
    lock_sha = str(selection_lock.get("lock_sha256") or "")
    unsigned_lock = dict(selection_lock)
    unsigned_lock.pop("lock_sha256", None)
    if not _is_sha(lock_sha) or _sha_json(unsigned_lock) != lock_sha:
        raise EditorRiskProfileError("selection lock self-hash is invalid")
    if lock_sha != str(verified_gold["selection_lock_sha256"]):
        raise EditorRiskProfileError("selection lock identity does not match human gold")
    # The production validator has already verified that this original artifact
    # field is a canonical SHA and is covered by the artifact self-hash.  Its
    # normalized return intentionally omits the file SHA, so read the verified
    # source field rather than assuming the normalized view preserves it.
    if str(human_gold_artifact.get("selection_lock_file_sha256") or "") != str(selection_lock_file_sha256):
        raise EditorRiskProfileError("selection lock file SHA does not match human gold")
    inputs = selection_lock.get("inputs")
    if not isinstance(inputs, Mapping):
        raise EditorRiskProfileError("selection lock inputs are missing")
    if str(inputs.get("final_audio_sha256") or "") != str(verified_gold["final_audio_sha256"]):
        raise EditorRiskProfileError("selection lock final-audio identity does not match human gold")

    outer = _selection_outer_index(selection_lock)
    gold_records = verified_gold.get("records")
    if not isinstance(gold_records, list):
        raise EditorRiskProfileError("verified human gold has no records")
    scoped = [
        row
        for row in gold_records
        if isinstance(row, Mapping)
        and str(row.get("boundary_kind") or "") == boundary_kind
        and str(row.get("population") or "") == "outer"
        and str(row.get("partition") or "") == population
    ]
    if not scoped:
        raise EditorRiskProfileError("human gold contains no matching editor baseline scope")

    errors: list[int] = []
    tracks: list[str] = []
    audit_records: list[dict[str, Any]] = []
    for gold in scoped:
        case_id = str(gold.get("case_id") or "")
        selection = outer.get(case_id)
        if selection is None:
            raise EditorRiskProfileError("human gold case is absent from selection outer population")
        if str(selection.get("partition") or "") != population:
            raise EditorRiskProfileError("selection/human-gold partition mismatch")
        if str(selection.get("track") or "") != str(gold.get("track") or ""):
            raise EditorRiskProfileError("selection/human-gold track mismatch")
        if int(selection.get("cue_number")) != int(gold.get("cue_number")):
            raise EditorRiskProfileError("selection/human-gold cue identity mismatch")
        editor_ms = int(selection[f"{boundary_kind}_ms"])
        gold_ms = int(gold["gold_ms"])
        uncertainty_ms = int(gold.get("gold_uncertainty_ms", 0))
        effective = _effective_error_ms(editor_ms, gold_ms, uncertainty_ms)
        errors.append(effective)
        tracks.append(str(gold["track"]))
        audit_records.append(
            {
                "record_id": str(gold["id"]),
                "case_id": case_id,
                "cue_number": int(gold["cue_number"]),
                "track": str(gold["track"]),
                "partition": population,
                "editor_ms": editor_ms,
                "gold_ms": gold_ms,
                "gold_uncertainty_ms": uncertainty_ms,
                "effective_error_ms": effective,
            }
        )

    policy = human_gold_artifact.get("production_policy")
    if not isinstance(policy, Mapping):
        raise EditorRiskProfileError("human gold production policy is missing")
    try:
        fps = float(policy.get("fps", 30.0))
        catastrophic_frames = float(policy.get("catastrophic_error_frames", 15.0))
    except (TypeError, ValueError) as exc:
        raise EditorRiskProfileError("human gold production policy timing is invalid") from exc
    if fps <= 0:
        raise EditorRiskProfileError("human gold production policy fps must be positive")
    catastrophic_ms = catastrophic_frames * (1000.0 / fps)
    try:
        profile = build_error_profile(
            profile_id=f"editor:{boundary_kind}:{population}",
            boundary_kind=boundary_kind,
            population=population,
            effective_errors_ms=errors,
            track_ids=tracks,
            catastrophic_threshold_ms=catastrophic_ms,
        )
    except BoundaryRiskError as exc:
        raise EditorRiskProfileError(str(exc)) from exc

    artifact: dict[str, Any] = {
        "schema_version": EDITOR_RISK_PROFILE_SCHEMA_VERSION,
        "authority_class": EDITOR_RISK_PROFILE_AUTHORITY,
        "boundary_kind": boundary_kind,
        "population": population,
        "selection_lock_sha256": lock_sha,
        "selection_lock_file_sha256": str(selection_lock_file_sha256),
        "human_gold_artifact_sha256": str(verified_gold["artifact_sha256"]),
        "human_gold_records_sha256": str(verified_gold["gold_sha256"]),
        "final_audio_sha256": str(verified_gold["final_audio_sha256"]),
        "profile": _profile_to_payload(profile),
        "records": audit_records,
    }
    artifact["records_sha256"] = _sha_json(audit_records)
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


def authoritative_editor_profile_from_artifact(
    artifact: Mapping[str, Any],
    *,
    selection_lock: Mapping[str, Any],
    selection_lock_file_sha256: str,
    human_gold_artifact: Mapping[str, Any],
) -> BoundaryErrorProfile:
    """Rebuild and compare an editor profile before attaching production authority."""

    if not isinstance(artifact, Mapping):
        raise EditorRiskProfileError("editor risk artifact must be an object")
    claimed = str(artifact.get("artifact_sha256") or "")
    unsigned = dict(artifact)
    unsigned.pop("artifact_sha256", None)
    if not _is_sha(claimed) or _sha_json(unsigned) != claimed:
        raise EditorRiskProfileError("editor risk artifact self-hash is invalid")
    if artifact.get("schema_version") != EDITOR_RISK_PROFILE_SCHEMA_VERSION:
        raise EditorRiskProfileError("editor risk artifact schema is unsupported")
    if artifact.get("authority_class") != EDITOR_RISK_PROFILE_AUTHORITY:
        raise EditorRiskProfileError("editor risk artifact authority class is invalid")
    records = artifact.get("records")
    if not isinstance(records, list) or str(artifact.get("records_sha256") or "") != _sha_json(records):
        raise EditorRiskProfileError("editor risk records SHA is invalid")

    rebuilt = build_editor_risk_profile_artifact(
        selection_lock=selection_lock,
        selection_lock_file_sha256=selection_lock_file_sha256,
        human_gold_artifact=human_gold_artifact,
        boundary_kind=str(artifact.get("boundary_kind") or ""),
        population=str(artifact.get("population") or ""),
    )
    if dict(artifact) != rebuilt:
        raise EditorRiskProfileError("editor risk artifact cannot be reproduced from bound human gold")
    profile_payload = artifact.get("profile")
    if not isinstance(profile_payload, Mapping):
        raise EditorRiskProfileError("editor risk artifact profile is missing")
    return _profile_from_payload(profile_payload).with_production_authority(claimed)


__all__ = [
    "EDITOR_RISK_PROFILE_SCHEMA_VERSION",
    "EDITOR_RISK_PROFILE_AUTHORITY",
    "EditorRiskProfileError",
    "build_editor_risk_profile_artifact",
    "authoritative_editor_profile_from_artifact",
]
