"""Derive Partial Timeline Repair mapping context from formal v4 lineage.

P2 deliberately accepted caller-supplied mapping labels while the evidence bridge
was still being isolated. P3 removes those labels from the production-facing
path: the effective run and its exact artifact decide whether each occurrence is
AFFINE, PIECEWISE_RATE, CUT_AWARE, or currently unavailable.

Confirmed-cut identity is never inferred from a review flag alone. CUT_AWARE is
accepted only from a materialized ``cut_timewarp_rebuild`` artifact that is
upstream of the supplied effective run.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from lyric_aligner.contracts.artifacts import (
    validate_artifact_output,
    validate_upstream_artifact,
)
from lyric_aligner.srt import Cue
from lyric_aligner.timeline.partial_repair import (
    CueTrust,
    PartialTimelineRepairError,
    TimingCandidate,
)
from lyric_aligner.timeline.partial_repair_evidence import (
    ExplicitCueTrust,
    bridge_fusion_to_partial_repair,
)


PARTIAL_REPAIR_CONTEXT_SCHEMA_VERSION = "1.0"
_RUN_ROLES = {
    "production_orchestration": "v4_production_run",
    "review_resolution": "v4_reviewed_run",
    "overlap_recomposition": "v4_recomposed_run",
    "cut_rebuild": "v4_cut_rebuilt_run",
    "combined_recomposition": "v4_combined_run",
}
_CONTINUOUS_MODES = {"AFFINE", "PIECEWISE_RATE"}


@dataclass(frozen=True)
class OccurrenceMappingContext:
    occurrence_id: str
    status: str
    mapping_kind: str | None
    mapping_source: str
    source_stage: str | None
    source_artifact_id: str | None
    confirmed_cut: bool
    cut_count: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EffectiveRunMappingContext:
    schema_version: str
    task_fingerprint_sha256: str
    algorithm_version: str
    run_stage: str
    run_artifact_id: str
    occurrences: tuple[OccurrenceMappingContext, ...]

    @property
    def mapping_kind_by_occurrence(self) -> dict[str, str]:
        return {
            row.occurrence_id: row.mapping_kind
            for row in self.occurrences
            if row.status == "ready" and row.mapping_kind is not None
        }

    @property
    def confirmed_cut_occurrence_ids(self) -> set[str]:
        return {
            row.occurrence_id
            for row in self.occurrences
            if row.status == "ready" and row.confirmed_cut
        }

    def to_report(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_fingerprint_sha256": self.task_fingerprint_sha256,
            "algorithm_version": self.algorithm_version,
            "run_stage": self.run_stage,
            "run_artifact_id": self.run_artifact_id,
            "mapping_authority": "effective_run_artifact_lineage_only",
            "confirmed_cut_policy": (
                "CUT_AWARE requires a materialized cut_timewarp_rebuild artifact; "
                "review flags and caller labels are insufficient"
            ),
            "occurrences": [row.to_dict() for row in self.occurrences],
        }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PartialTimelineRepairError(
            f"cannot read lineage JSON: {path.name}"
        ) from exc
    if not isinstance(payload, dict):
        raise PartialTimelineRepairError(
            f"lineage JSON must be an object: {path.name}"
        )
    return payload


def _validate_pair(
    *,
    payload_path: Path,
    artifact_path: Path,
    fingerprint: str,
    algorithm_version: str,
    stage: str,
    role: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _load_json(payload_path)
    artifact = _load_json(artifact_path)
    issues = validate_upstream_artifact(
        artifact,
        expected_task_fingerprint=fingerprint,
        expected_algorithm_version=algorithm_version,
        expected_stage=stage,
    )
    issues.extend(
        validate_artifact_output(artifact, role=role, path=payload_path)
    )
    if issues:
        raise PartialTimelineRepairError(
            f"invalid {stage} lineage artifact: " + "; ".join(issues)
        )
    if payload.get("task_fingerprint_sha256") != fingerprint:
        raise PartialTimelineRepairError(
            f"{stage} payload belongs to another task"
        )
    if payload.get("algorithm_version") != algorithm_version:
        raise PartialTimelineRepairError(
            f"{stage} payload algorithm version mismatch"
        )
    return payload, artifact


def _required_path(row: dict[str, Any], key: str) -> Path:
    value = str(row.get(key) or "").strip()
    if not value:
        raise PartialTimelineRepairError(
            f"effective occurrence is missing {key}"
        )
    path = Path(value)
    if not path.is_file():
        raise PartialTimelineRepairError(
            f"effective occurrence lineage file is missing: {path.name}"
        )
    return path


def _artifact_is_effective_upstream(
    artifact: dict[str, Any], effective_upstreams: set[str], *, label: str
) -> str:
    artifact_id = str(artifact.get("artifact_id") or "").strip()
    if not artifact_id or artifact_id not in effective_upstreams:
        raise PartialTimelineRepairError(
            f"{label} artifact is not upstream of the effective run"
        )
    return artifact_id


def _validate_continuous_mapping(mapping: object) -> str:
    if not isinstance(mapping, dict):
        raise PartialTimelineRepairError(
            "effective continuous TimeWarp has no mapping"
        )
    mode = str(mapping.get("mode") or "").strip()
    if mode not in _CONTINUOUS_MODES:
        raise PartialTimelineRepairError(
            f"unsupported effective continuous mapping mode: "
            f"{mode or '<missing>'}"
        )
    breakpoints = mapping.get("breakpoints")
    deltas = mapping.get("slope_deltas")
    if not isinstance(breakpoints, list) or not isinstance(deltas, list):
        raise PartialTimelineRepairError(
            "continuous mapping hinge arrays are invalid"
        )
    if len(breakpoints) != len(deltas):
        raise PartialTimelineRepairError(
            "continuous mapping hinge arrays disagree"
        )
    if mode == "AFFINE" and (breakpoints or deltas):
        raise PartialTimelineRepairError(
            "AFFINE mapping unexpectedly contains rate breakpoints"
        )
    if mode == "PIECEWISE_RATE" and not breakpoints:
        raise PartialTimelineRepairError(
            "PIECEWISE_RATE mapping has no rate breakpoint"
        )
    return mode


def _continuous_identity(payload: dict[str, Any], *, label: str) -> tuple[str, str]:
    track_id = str(payload.get("track_id") or "").strip()
    canonical_selection = str(
        payload.get("canonical_selection_sha256") or ""
    ).strip()
    if not track_id or not canonical_selection:
        raise PartialTimelineRepairError(
            f"{label} alignment is missing track/canonical identity"
        )
    return track_id, canonical_selection


def _derive_continuous_context(
    *,
    occurrence: dict[str, Any],
    occurrence_id: str,
    fingerprint: str,
    algorithm_version: str,
    effective_upstreams: set[str],
) -> OccurrenceMappingContext:
    mapping_source = str(occurrence.get("mapping_source") or "").strip()
    if bool(occurrence.get("mapping_blocked")):
        return OccurrenceMappingContext(
            occurrence_id=occurrence_id,
            status="unavailable",
            mapping_kind=None,
            mapping_source=mapping_source,
            source_stage=None,
            source_artifact_id=None,
            confirmed_cut=False,
            cut_count=0,
            reason="effective_source_to_mix_mapping_is_blocked",
        )

    coarse_path = _required_path(occurrence, "coarse_path")
    coarse_artifact_path = _required_path(
        occurrence, "coarse_artifact_path"
    )
    coarse, coarse_artifact = _validate_pair(
        payload_path=coarse_path,
        artifact_path=coarse_artifact_path,
        fingerprint=fingerprint,
        algorithm_version=algorithm_version,
        stage="coarse_audio_alignment",
        role="coarse_alignment",
    )
    coarse_id = _artifact_is_effective_upstream(
        coarse_artifact, effective_upstreams, label="coarse"
    )
    if str(coarse.get("occurrence_id") or "") != occurrence_id:
        raise PartialTimelineRepairError(
            "coarse/effective-run occurrence identity mismatch"
        )
    coarse_track_id, coarse_canonical_selection = _continuous_identity(
        coarse, label="coarse"
    )

    if mapping_source == "coarse":
        if bool(occurrence.get("fine_applied")):
            raise PartialTimelineRepairError(
                "effective run says Fine was applied but mapping_source is coarse"
            )
        timewarp = coarse.get("result", {}).get("timewarp")
        if not isinstance(timewarp, dict):
            raise PartialTimelineRepairError(
                "coarse alignment has no TimeWarp"
            )
        if bool(timewarp.get("blocked")):
            raise PartialTimelineRepairError(
                "effective occurrence is unblocked but coarse TimeWarp is blocked"
            )
        mode = _validate_continuous_mapping(timewarp.get("mapping"))
        return OccurrenceMappingContext(
            occurrence_id=occurrence_id,
            status="ready",
            mapping_kind=mode,
            mapping_source="coarse",
            source_stage="coarse_audio_alignment",
            source_artifact_id=coarse_id,
            confirmed_cut=False,
            cut_count=0,
            reason="derived_from_effective_coarse_timewarp",
        )

    if mapping_source != "fine":
        raise PartialTimelineRepairError(
            f"unsupported continuous mapping_source: "
            f"{mapping_source or '<missing>'}"
        )
    if not bool(occurrence.get("fine_applied")):
        raise PartialTimelineRepairError(
            "effective run says mapping_source is fine but Fine was not applied"
        )
    fine_path = _required_path(occurrence, "fine_path")
    fine_artifact_path = _required_path(
        occurrence, "fine_artifact_path"
    )
    fine, fine_artifact = _validate_pair(
        payload_path=fine_path,
        artifact_path=fine_artifact_path,
        fingerprint=fingerprint,
        algorithm_version=algorithm_version,
        stage="fine_audio_alignment",
        role="fine_alignment",
    )
    fine_id = _artifact_is_effective_upstream(
        fine_artifact, effective_upstreams, label="fine"
    )
    if str(fine.get("occurrence_id") or "") != occurrence_id:
        raise PartialTimelineRepairError(
            "fine/effective-run occurrence identity mismatch"
        )
    fine_track_id, fine_canonical_selection = _continuous_identity(
        fine, label="fine"
    )
    if fine_track_id != coarse_track_id:
        raise PartialTimelineRepairError(
            "Fine/coarse track identity mismatch"
        )
    if fine_canonical_selection != coarse_canonical_selection:
        raise PartialTimelineRepairError(
            "Fine/coarse canonical selection identity mismatch"
        )
    if coarse_id not in {
        str(value)
        for value in fine_artifact.get("upstream_artifact_ids", [])
    }:
        raise PartialTimelineRepairError(
            "Fine artifact is not derived from effective coarse"
        )
    fine_result = fine.get("result")
    if (
        not isinstance(fine_result, dict)
        or fine_result.get("applied") is not True
    ):
        raise PartialTimelineRepairError(
            "effective Fine payload is not applied"
        )
    timewarp = fine_result.get("timewarp")
    if not isinstance(timewarp, dict) or bool(timewarp.get("blocked")):
        raise PartialTimelineRepairError(
            "effective Fine TimeWarp is missing or blocked"
        )
    mode = _validate_continuous_mapping(timewarp.get("mapping"))
    return OccurrenceMappingContext(
        occurrence_id=occurrence_id,
        status="ready",
        mapping_kind=mode,
        mapping_source="fine",
        source_stage="fine_audio_alignment",
        source_artifact_id=fine_id,
        confirmed_cut=False,
        cut_count=0,
        reason="derived_from_applied_effective_fine_timewarp",
    )


def _derive_cut_context(
    *,
    run: dict[str, Any],
    occurrence: dict[str, Any],
    occurrence_id: str,
    fingerprint: str,
    algorithm_version: str,
    effective_upstreams: set[str],
) -> OccurrenceMappingContext:
    if occurrence.get("cut_rebuilt") is not True:
        raise PartialTimelineRepairError(
            "cut_aware_rebuild mapping_source requires cut_rebuilt=true"
        )
    mapping_path = _required_path(occurrence, "cut_mapping_path")
    artifact_path = _required_path(
        occurrence, "cut_mapping_artifact_path"
    )
    payload, artifact = _validate_pair(
        payload_path=mapping_path,
        artifact_path=artifact_path,
        fingerprint=fingerprint,
        algorithm_version=algorithm_version,
        stage="cut_timewarp_rebuild",
        role="cut_aware_timewarp",
    )
    artifact_id = _artifact_is_effective_upstream(
        artifact, effective_upstreams, label="cut TimeWarp"
    )
    if str(payload.get("occurrence_id") or "") != occurrence_id:
        raise PartialTimelineRepairError(
            "cut mapping/effective-run occurrence identity mismatch"
        )
    cut_track_id = str(payload.get("track_id") or "").strip()
    cut_canonical_selection = str(
        payload.get("canonical_selection_sha256") or ""
    ).strip()
    if not cut_track_id or not cut_canonical_selection:
        raise PartialTimelineRepairError(
            "cut mapping is missing track/canonical identity"
        )
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("kind") != "CUT_AWARE":
        raise PartialTimelineRepairError(
            "materialized cut mapping is not CUT_AWARE"
        )
    cuts = result.get("cuts")
    segments = result.get("segments")
    if not isinstance(cuts, list) or not cuts:
        raise PartialTimelineRepairError(
            "CUT_AWARE mapping has no materialized cut"
        )
    if not isinstance(segments, list) or len(segments) < 2:
        raise PartialTimelineRepairError(
            "CUT_AWARE mapping has insufficient retained segments"
        )
    cut_count = len(cuts)
    try:
        summary_cut_count = int(occurrence.get("cut_count", -1))
        artifact_cut_count = int(
            artifact.get("evidence", {}).get("cut_count", -1)
        )
    except (TypeError, ValueError) as exc:
        raise PartialTimelineRepairError(
            "materialized cut count is invalid"
        ) from exc
    if summary_cut_count != cut_count or artifact_cut_count != cut_count:
        raise PartialTimelineRepairError(
            "CUT_AWARE cut count disagrees across lineage"
        )
    if (
        str(artifact.get("evidence", {}).get("occurrence_id") or "")
        != occurrence_id
    ):
        raise PartialTimelineRepairError(
            "cut artifact evidence occurrence identity mismatch"
        )
    confirmed_candidate_ids = artifact.get("normalized_config", {}).get(
        "confirmed_candidate_ids"
    )
    if (
        not isinstance(confirmed_candidate_ids, list)
        or len(confirmed_candidate_ids) != cut_count
    ):
        raise PartialTimelineRepairError(
            "cut artifact confirmed candidate identity is incomplete"
        )
    if any(not str(value).strip() for value in confirmed_candidate_ids):
        raise PartialTimelineRepairError(
            "cut artifact contains empty confirmed candidate identity"
        )

    cut_meta = run.get("cut_rebuild")
    if not isinstance(cut_meta, dict):
        raise PartialTimelineRepairError(
            "effective cut run is missing cut_rebuild metadata"
        )
    review_id = str(
        cut_meta.get("source_review_artifact_id") or ""
    ).strip()
    if not review_id:
        raise PartialTimelineRepairError(
            "cut_rebuild metadata has no source review artifact"
        )
    if (
        artifact.get("normalized_config", {}).get(
            "source_review_artifact_id"
        )
        != review_id
    ):
        raise PartialTimelineRepairError(
            "cut mapping belongs to another review materialization"
        )
    if review_id not in {
        str(value)
        for value in artifact.get("upstream_artifact_ids", [])
    }:
        raise PartialTimelineRepairError(
            "cut mapping artifact does not bind its review artifact"
        )
    if review_id not in effective_upstreams:
        raise PartialTimelineRepairError(
            "source review artifact is not upstream of effective run"
        )

    return OccurrenceMappingContext(
        occurrence_id=occurrence_id,
        status="ready",
        mapping_kind="CUT_AWARE",
        mapping_source="cut_aware_rebuild",
        source_stage="cut_timewarp_rebuild",
        source_artifact_id=artifact_id,
        confirmed_cut=True,
        cut_count=cut_count,
        reason="derived_from_materialized_confirmed_cut_lineage",
    )


def derive_effective_run_mapping_context(
    *,
    run_path: Path,
    run_artifact_path: Path,
) -> EffectiveRunMappingContext:
    """Derive mapping kind/cut identity without caller-supplied labels."""

    run = _load_json(run_path)
    artifact = _load_json(run_artifact_path)
    fingerprint = str(
        run.get("task_fingerprint_sha256") or ""
    ).strip()
    algorithm_version = str(run.get("algorithm_version") or "").strip()
    if not fingerprint or not algorithm_version:
        raise PartialTimelineRepairError(
            "effective run is missing task/algorithm identity"
        )
    stage = str(artifact.get("stage") or "").strip()
    role = _RUN_ROLES.get(stage)
    if role is None:
        raise PartialTimelineRepairError(
            f"unsupported effective run stage: {stage or '<missing>'}"
        )
    validated_run, validated_artifact = _validate_pair(
        payload_path=run_path,
        artifact_path=run_artifact_path,
        fingerprint=fingerprint,
        algorithm_version=algorithm_version,
        stage=stage,
        role=role,
    )
    if validated_run is not run and validated_run != run:
        raise PartialTimelineRepairError(
            "effective run changed during lineage validation"
        )
    if validated_run.get("legacy_fallback_used") is not False:
        raise PartialTimelineRepairError(
            "partial repair refuses a legacy-fallback run"
        )
    run_artifact_id = str(
        validated_artifact.get("artifact_id") or ""
    ).strip()
    effective_upstreams = {
        str(value)
        for value in validated_artifact.get("upstream_artifact_ids", [])
        if str(value).strip()
    }

    rows = validated_run.get("occurrences")
    if not isinstance(rows, list) or not rows:
        raise PartialTimelineRepairError(
            "effective run has no occurrences"
        )
    seen: set[str] = set()
    contexts: list[OccurrenceMappingContext] = []
    for occurrence in rows:
        if not isinstance(occurrence, dict):
            raise PartialTimelineRepairError(
                "effective run occurrence must be an object"
            )
        occurrence_id = str(
            occurrence.get("occurrence_id") or ""
        ).strip()
        if not occurrence_id or occurrence_id in seen:
            raise PartialTimelineRepairError(
                "effective run occurrence identity is missing/duplicated"
            )
        seen.add(occurrence_id)
        mapping_source = str(
            occurrence.get("mapping_source") or ""
        ).strip()
        if mapping_source == "cut_aware_rebuild":
            context = _derive_cut_context(
                run=validated_run,
                occurrence=occurrence,
                occurrence_id=occurrence_id,
                fingerprint=fingerprint,
                algorithm_version=algorithm_version,
                effective_upstreams=effective_upstreams,
            )
        elif mapping_source in {"coarse", "fine"}:
            context = _derive_continuous_context(
                occurrence=occurrence,
                occurrence_id=occurrence_id,
                fingerprint=fingerprint,
                algorithm_version=algorithm_version,
                effective_upstreams=effective_upstreams,
            )
        else:
            raise PartialTimelineRepairError(
                f"effective run has unsupported mapping_source "
                f"{mapping_source!r}"
            )
        contexts.append(context)

    return EffectiveRunMappingContext(
        schema_version=PARTIAL_REPAIR_CONTEXT_SCHEMA_VERSION,
        task_fingerprint_sha256=fingerprint,
        algorithm_version=algorithm_version,
        run_stage=stage,
        run_artifact_id=run_artifact_id,
        occurrences=tuple(contexts),
    )


def bridge_effective_run_to_partial_repair(
    *,
    cues: Sequence[Cue],
    fusion: dict[str, Any],
    run_path: Path,
    run_artifact_path: Path,
    explicit_trust: Iterable[ExplicitCueTrust],
) -> tuple[list[CueTrust], list[TimingCandidate], dict[str, Any]]:
    """Production-facing P3 bridge with mapping/cut context derived from lineage."""

    context = derive_effective_run_mapping_context(
        run_path=run_path,
        run_artifact_path=run_artifact_path,
    )
    if (
        fusion.get("task_fingerprint_sha256")
        != context.task_fingerprint_sha256
    ):
        raise PartialTimelineRepairError(
            "P9 fusion belongs to another task"
        )
    if fusion.get("algorithm_version") != context.algorithm_version:
        raise PartialTimelineRepairError(
            "P9 fusion algorithm version mismatch"
        )
    if fusion.get("source_run_stage") != context.run_stage:
        raise PartialTimelineRepairError(
            "P9 fusion source run stage mismatch"
        )
    if fusion.get("source_run_artifact_id") != context.run_artifact_id:
        raise PartialTimelineRepairError(
            "P9 fusion is not bound to the effective run artifact"
        )

    trust, candidates, report = bridge_fusion_to_partial_repair(
        cues=cues,
        fusion=fusion,
        mapping_kind_by_occurrence=context.mapping_kind_by_occurrence,
        explicit_trust=explicit_trust,
        confirmed_cut_occurrence_ids=context.confirmed_cut_occurrence_ids,
    )
    report["effective_run_mapping_context"] = context.to_report()
    report[
        "mapping_context_source"
    ] = "formal_effective_run_artifact_lineage"
    return trust, candidates, report
