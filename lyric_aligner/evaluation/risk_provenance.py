"""Exact-provenance binding for production expected-loss boundary decisions.

A candidate and the editor baseline may each be statistically valid in isolation
while still being incomparable if they were measured on different human-gold
populations.  This module binds both profiles to the exact audited gold, selection
lock, final audio and holdout population before allowing risk adjudication.

It intentionally sits above :mod:`timeline.boundary_risk`: the latter remains a
small mathematical policy primitive, while this module owns production provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from lyric_aligner.evaluation.editor_risk_profile import (
    EditorRiskProfileError,
    authoritative_editor_profile_from_artifact,
)
from lyric_aligner.evaluation.production_calibration import (
    ProductionCalibrationError,
    evaluate_human_gold_backend_calibration,
)
from lyric_aligner.timeline.boundary_risk import (
    BoundaryErrorProfile,
    BoundaryLocalSupport,
    BoundaryRiskDecision,
    BoundaryRiskPolicy,
    adjudicate_calibrated_fallback,
    build_profile_from_calibration_artifact,
)


RISK_PROVENANCE_BINDING_VERSION = "1.0"


class RiskProvenanceError(ValueError):
    pass


def _is_sha(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BoundRiskProfile:
    binding_version: str
    role: str
    profile: BoundaryErrorProfile
    source_artifact_sha256: str
    human_gold_artifact_sha256: str
    human_gold_records_sha256: str
    selection_lock_sha256: str
    final_audio_sha256: str
    boundary_kind: str
    population: str

    def validate(self) -> None:
        if self.binding_version != RISK_PROVENANCE_BINDING_VERSION:
            raise RiskProvenanceError("unsupported risk provenance binding version")
        if self.role not in {"editor", "candidate"}:
            raise RiskProvenanceError("risk profile role must be editor or candidate")
        self.profile.validate()
        if not self.profile.production_authoritative:
            raise RiskProvenanceError("bound risk profile must already carry validated production authority")
        for label, value in (
            ("source_artifact_sha256", self.source_artifact_sha256),
            ("human_gold_artifact_sha256", self.human_gold_artifact_sha256),
            ("human_gold_records_sha256", self.human_gold_records_sha256),
            ("selection_lock_sha256", self.selection_lock_sha256),
            ("final_audio_sha256", self.final_audio_sha256),
        ):
            if not _is_sha(value):
                raise RiskProvenanceError(f"{label} must be a canonical SHA-256")
        if self.profile.provenance_sha256 != self.source_artifact_sha256:
            raise RiskProvenanceError("profile provenance SHA does not match bound source artifact")
        if self.boundary_kind not in {"start", "end", "internal"}:
            raise RiskProvenanceError("bound boundary_kind is invalid")
        if self.population != "holdout":
            raise RiskProvenanceError("production expected-loss decisions require holdout population")
        if self.profile.boundary_kind != self.boundary_kind:
            raise RiskProvenanceError("profile/binding boundary kind mismatch")
        if self.profile.population != self.population:
            raise RiskProvenanceError("profile/binding population mismatch")


def bind_editor_risk_profile(
    *,
    editor_risk_artifact: Mapping[str, Any],
    selection_lock: Mapping[str, Any],
    selection_lock_file_sha256: str,
    human_gold_artifact: Mapping[str, Any],
) -> BoundRiskProfile:
    """Validate and bind one holdout editor baseline artifact."""

    try:
        profile = authoritative_editor_profile_from_artifact(
            editor_risk_artifact,
            selection_lock=selection_lock,
            selection_lock_file_sha256=selection_lock_file_sha256,
            human_gold_artifact=human_gold_artifact,
        )
    except EditorRiskProfileError as exc:
        raise RiskProvenanceError(str(exc)) from exc
    if str(editor_risk_artifact.get("population") or "") != "holdout":
        raise RiskProvenanceError("production editor risk binding requires holdout population")
    binding = BoundRiskProfile(
        binding_version=RISK_PROVENANCE_BINDING_VERSION,
        role="editor",
        profile=profile,
        source_artifact_sha256=str(editor_risk_artifact.get("artifact_sha256") or ""),
        human_gold_artifact_sha256=str(editor_risk_artifact.get("human_gold_artifact_sha256") or ""),
        human_gold_records_sha256=str(editor_risk_artifact.get("human_gold_records_sha256") or ""),
        selection_lock_sha256=str(editor_risk_artifact.get("selection_lock_sha256") or ""),
        final_audio_sha256=str(editor_risk_artifact.get("final_audio_sha256") or ""),
        boundary_kind=str(editor_risk_artifact.get("boundary_kind") or ""),
        population="holdout",
    )
    binding.validate()
    return binding


def bind_candidate_risk_profile(
    *,
    calibration_artifact: Mapping[str, Any],
    human_gold_artifact: Mapping[str, Any],
    backend_id: str,
    family: str,
    correlation_group: str,
    expected_scope: Mapping[str, Any],
) -> BoundRiskProfile:
    """Bind an exact Human-Gold measurement artifact to holdout risk.

    Risk authority is intentionally different from precise timing authority.  A
    backend may fail the strict A-tier calibration thresholds and still be a valid
    B/C risk candidate if its *measurement artifact* can be reproduced exactly
    from the audited human gold and persisted predictions.  The downstream risk
    policy, not the A-tier precision gate, decides whether that measured error is
    acceptable for calibrated-better or rescue use.
    """

    if not isinstance(calibration_artifact, Mapping) or not isinstance(human_gold_artifact, Mapping):
        raise RiskProvenanceError("candidate calibration and human gold must be objects")
    records = calibration_artifact.get("records")
    if not isinstance(records, list) or not records:
        raise RiskProvenanceError("candidate calibration records are missing")
    predictions: dict[str, int | None] = {}
    for row in records:
        if not isinstance(row, Mapping):
            raise RiskProvenanceError("candidate calibration record must be an object")
        record_id = str(row.get("id") or "").strip()
        if not record_id or record_id in predictions:
            raise RiskProvenanceError("candidate calibration record ids must be unique/non-empty")
        predicted = row.get("predicted_ms")
        if predicted is None:
            predictions[record_id] = None
        else:
            try:
                value = int(predicted)
            except (TypeError, ValueError) as exc:
                raise RiskProvenanceError("candidate calibration predicted_ms is invalid") from exc
            if value < 0:
                raise RiskProvenanceError("candidate calibration predicted_ms must be nonnegative")
            predictions[record_id] = value
    try:
        rebuilt = evaluate_human_gold_backend_calibration(
            backend_id=backend_id,
            family=family,
            correlation_group=correlation_group,
            scope=expected_scope,
            human_gold_artifact=human_gold_artifact,
            predictions_ms=predictions,
        )
    except ProductionCalibrationError as exc:
        raise RiskProvenanceError(str(exc)) from exc
    claimed = str(calibration_artifact.get("artifact_sha256") or "").strip()
    unsigned = dict(calibration_artifact)
    unsigned.pop("artifact_sha256", None)
    if not _is_sha(claimed) or _sha_json(unsigned) != claimed:
        raise RiskProvenanceError("candidate calibration artifact self-hash is invalid")

    actual_core = dict(calibration_artifact)
    actual_core.pop("artifact_sha256", None)
    prediction_provenance = actual_core.pop("prediction_provenance", None)
    rebuilt_core = dict(rebuilt)
    rebuilt_core.pop("artifact_sha256", None)
    if actual_core != rebuilt_core:
        raise RiskProvenanceError(
            "candidate calibration cannot be reproduced from exact human gold, scope and predictions"
        )

    if prediction_provenance is not None:
        if not isinstance(prediction_provenance, Mapping):
            raise RiskProvenanceError("candidate prediction provenance is malformed")
        expected_keys = {
            "schema_version",
            "prediction_artifact_sha256",
            "predictions_sha256",
            "selection_lock_sha256",
            "production_provenance_complete",
            "source_blind_scope_artifact_sha256",
            "reuse_mode",
        }
        if set(prediction_provenance) != expected_keys:
            raise RiskProvenanceError("candidate prediction provenance fields are unexpected")
        if prediction_provenance.get("schema_version") != "human-boundary-backend-predictions-1.1":
            raise RiskProvenanceError("candidate prediction provenance schema is unsupported")
        if prediction_provenance.get("production_provenance_complete") is not True:
            raise RiskProvenanceError("candidate prediction provenance is incomplete")
        if str(prediction_provenance.get("selection_lock_sha256") or "") != str(
            human_gold_artifact.get("selection_lock_sha256") or ""
        ):
            raise RiskProvenanceError("candidate prediction provenance belongs to another selection lock")
        for key in (
            "prediction_artifact_sha256",
            "predictions_sha256",
            "source_blind_scope_artifact_sha256",
        ):
            if not _is_sha(prediction_provenance.get(key)):
                raise RiskProvenanceError(f"candidate prediction provenance {key} is invalid")
        persisted_prediction_rows = [
            {"id": str(row.get("id") or ""), "predicted_ms": row.get("predicted_ms")}
            for row in records
        ]
        if str(prediction_provenance.get("predictions_sha256") or "") != _sha_json(
            persisted_prediction_rows
        ):
            raise RiskProvenanceError("candidate prediction provenance rows SHA does not match calibration records")
        if not str(prediction_provenance.get("reuse_mode") or "").strip():
            raise RiskProvenanceError("candidate prediction provenance reuse_mode is missing")

    provenance = calibration_artifact.get("human_gold_provenance")
    if not isinstance(provenance, Mapping):
        raise RiskProvenanceError("candidate calibration lacks human-gold provenance")
    scope = calibration_artifact.get("scope")
    if not isinstance(scope, Mapping):
        raise RiskProvenanceError("candidate calibration scope is missing")
    profile = build_profile_from_calibration_artifact(
        calibration_artifact,
        population="holdout",
        production_authoritative=False,
    ).with_production_authority(claimed)
    binding = BoundRiskProfile(
        binding_version=RISK_PROVENANCE_BINDING_VERSION,
        role="candidate",
        profile=profile,
        source_artifact_sha256=claimed,
        human_gold_artifact_sha256=str(provenance.get("human_gold_artifact_sha256") or ""),
        human_gold_records_sha256=str(provenance.get("human_gold_records_sha256") or ""),
        selection_lock_sha256=str(provenance.get("selection_lock_sha256") or ""),
        final_audio_sha256=str(provenance.get("calibration_corpus_final_audio_sha256") or ""),
        boundary_kind=str(scope.get("boundary_kind") or ""),
        population="holdout",
    )
    binding.validate()
    return binding


def _assert_same_risk_population(editor: BoundRiskProfile, candidate: BoundRiskProfile) -> None:
    editor.validate()
    candidate.validate()
    if editor.role != "editor" or candidate.role != "candidate":
        raise RiskProvenanceError("bound risk roles are reversed or invalid")
    comparisons = (
        ("human_gold_artifact_sha256", editor.human_gold_artifact_sha256, candidate.human_gold_artifact_sha256),
        ("human_gold_records_sha256", editor.human_gold_records_sha256, candidate.human_gold_records_sha256),
        ("selection_lock_sha256", editor.selection_lock_sha256, candidate.selection_lock_sha256),
        ("final_audio_sha256", editor.final_audio_sha256, candidate.final_audio_sha256),
        ("boundary_kind", editor.boundary_kind, candidate.boundary_kind),
        ("population", editor.population, candidate.population),
    )
    for label, left, right in comparisons:
        if left != right:
            raise RiskProvenanceError(f"editor/candidate risk population mismatch: {label}")


def adjudicate_bound_calibrated_fallback(
    *,
    editor_ms: int,
    candidate_ms: int,
    editor: BoundRiskProfile,
    candidate: BoundRiskProfile,
    local_support: BoundaryLocalSupport | None = None,
    semantic_ambiguity: bool = False,
    structural_ambiguity: bool = False,
    global_constraints_satisfied: bool = True,
    policy: BoundaryRiskPolicy | None = None,
) -> BoundaryRiskDecision:
    """Run expected-loss fallback only after exact population equality is proven."""

    _assert_same_risk_population(editor, candidate)
    return adjudicate_calibrated_fallback(
        editor_ms=editor_ms,
        candidate_ms=candidate_ms,
        editor_profile=editor.profile,
        candidate_profile=candidate.profile,
        local_support=local_support,
        semantic_ambiguity=semantic_ambiguity,
        structural_ambiguity=structural_ambiguity,
        global_constraints_satisfied=global_constraints_satisfied,
        policy=policy,
    )


__all__ = [
    "RISK_PROVENANCE_BINDING_VERSION",
    "RiskProvenanceError",
    "BoundRiskProfile",
    "bind_editor_risk_profile",
    "bind_candidate_risk_profile",
    "adjudicate_bound_calibrated_fallback",
]
