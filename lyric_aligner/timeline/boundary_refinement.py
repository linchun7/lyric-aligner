"""Plan, adjudicate, and safely apply lyric-boundary refinements.

The publication target is the final mix.  Editor SRT boundaries are treated as
strong but rebuttable priors.  Canonical/LRC timing may help route work but never
counts as independent acoustic evidence.  A timing mutation is materialized only
when :mod:`boundary_authority` grants it; otherwise the editor boundary survives.

This module is backend-neutral.  SOFA, WhisperX, a source-side forced aligner,
local acoustic matching, or future models can all produce evidence records, but
none of them writes subtitle timing directly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from math import ceil
from typing import Any, Iterable, Mapping, Sequence

from lyric_aligner.timeline.boundary_authority import (
    BOUNDARY_AUTHORITY_POLICY_VERSION,
    BoundaryAuthorityError,
    adjudicate_boundary_candidate,
    classify_boundary_difficulty,
)
from lyric_aligner.evaluation.production_calibration import (
    production_calibration_artifact_is_authoritative,
)
from lyric_aligner.text.alignment_lexical import (
    AlignmentLexicalError,
    alignment_units,
)


BOUNDARY_REFINEMENT_PLAN_SCHEMA_VERSION = "1.0"
BOUNDARY_EVIDENCE_SCHEMA_VERSION = "1.0"
BOUNDARY_DECISION_SCHEMA_VERSION = "1.0"
BOUNDARY_REFINEMENT_POLICY_ID = "final-mix-boundary-refinement-2026-09-04-v1"


class BoundaryRefinementError(ValueError):
    """Raised when boundary refinement artifacts cannot be trusted."""


@dataclass(frozen=True)
class BoundaryRefinementConfig:
    local_context_ms: int = 900
    multi_evidence_context_ms: int = 1800
    heavy_context_ms: int = 3500
    structural_context_ms: int = 8000
    max_boundaries: int = 5000

    def validate(self) -> None:
        for label, value in (
            ("local_context_ms", self.local_context_ms),
            ("multi_evidence_context_ms", self.multi_evidence_context_ms),
            ("heavy_context_ms", self.heavy_context_ms),
            ("structural_context_ms", self.structural_context_ms),
        ):
            if int(value) < 0:
                raise BoundaryRefinementError(f"{label} must be >= 0")
        if int(self.max_boundaries) < 1:
            raise BoundaryRefinementError("max_boundaries must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _finite_nonnegative_int(value: Any, *, label: str) -> int:
    try:
        result = int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise BoundaryRefinementError(f"{label} is invalid") from exc
    if result < 0:
        raise BoundaryRefinementError(f"{label} must be >= 0")
    return result


def _calibration_uncertainty_floor_ms(artifact: Mapping[str, Any]) -> int:
    summary = artifact.get("summary")
    if not isinstance(summary, Mapping):
        raise BoundaryRefinementError("calibration artifact has no summary")
    holdout = summary.get("holdout")
    if not isinstance(holdout, Mapping):
        raise BoundaryRefinementError("calibration artifact has no holdout summary")
    raw = holdout.get("p90_effective_error_ms")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise BoundaryRefinementError(
            "calibration artifact has invalid holdout p90 error"
        ) from exc
    if value < 0:
        raise BoundaryRefinementError("calibration holdout p90 error must be nonnegative")
    return int(ceil(value))


def _outer_lexical_units_sha256(*, language: str, canonical_text: str) -> str:
    try:
        units = alignment_units(language, canonical_text)
    except AlignmentLexicalError as exc:
        raise BoundaryRefinementError("outer canonical lexical preparation is unavailable") from exc
    return _sha_json([units])


def bind_calibration_to_boundary_point(
    point: Mapping[str, Any],
    calibration_artifact: Mapping[str, Any],
    *,
    boundary_kind: str,
) -> dict[str, Any]:
    """Attach one exact production human-gold calibration to a raw boundary point."""

    kind = str(boundary_kind or "").strip()
    if kind not in {"start", "end"}:
        raise BoundaryRefinementError("outer calibration binding requires start or end boundary_kind")
    family = str(point.get("family") or "").strip()
    correlation_group = str(point.get("correlation_group") or "").strip()
    backend_id = str(point.get("backend_id") or "").strip()
    backend_version = str(point.get("backend_version") or "").strip()
    backend_profile_id = str(point.get("backend_profile_id") or "").strip()
    model_id = str(point.get("model_id") or "").strip()
    model_revision = str(point.get("model_revision") or "").strip()
    implementation_revision = str(point.get("implementation_revision") or "").strip()
    adapter_contract_revision = str(point.get("adapter_contract_revision") or "").strip()
    lexical_id = str(point.get("lexical_id") or "").strip()
    lexical_revision = str(point.get("lexical_revision") or "").strip()
    audio_basis = str(point.get("audio_basis") or "").strip()
    language = str(point.get("language") or "").strip()
    window_policy_id = str(point.get("window_policy_id") or "").strip()
    evidence_sha256 = str(point.get("evidence_sha256") or "").strip()
    required = (
        family,
        correlation_group,
        backend_id,
        backend_version,
        backend_profile_id,
        model_id,
        model_revision,
        implementation_revision,
        adapter_contract_revision,
        lexical_id,
        lexical_revision,
        audio_basis,
        language,
        window_policy_id,
    )
    if any(not value for value in required):
        raise BoundaryRefinementError("raw boundary point identity is incomplete")
    if len(evidence_sha256) != 64:
        raise BoundaryRefinementError("raw boundary point must carry a 64-character evidence SHA")
    expected_scope = {
        "boundary_kind": kind,
        "audio_basis": audio_basis,
        "language": language,
        "backend_version": backend_version,
        "backend_profile_id": backend_profile_id,
        "window_policy_id": window_policy_id,
        "model_id": model_id,
        "model_revision": model_revision,
        "implementation_revision": implementation_revision,
        "adapter_contract_revision": adapter_contract_revision,
        "lexical_id": lexical_id,
        "lexical_revision": lexical_revision,
    }
    if not production_calibration_artifact_is_authoritative(
        calibration_artifact,
        backend_id=backend_id,
        family=family,
        correlation_group=correlation_group,
        expected_scope=expected_scope,
    ):
        raise BoundaryRefinementError("calibration artifact does not authorize this outer backend point")
    artifact_sha = str(calibration_artifact.get("artifact_sha256") or "").strip()
    bound = dict(point)
    bound["calibration_passed"] = True
    bound["calibration_sha256"] = artifact_sha
    bound["calibration_artifact"] = dict(calibration_artifact)
    bound["uncertainty_ms"] = max(
        _finite_nonnegative_int(bound.get("uncertainty_ms", 0), label="boundary uncertainty_ms"),
        _calibration_uncertainty_floor_ms(calibration_artifact),
    )
    return bound


def _cue_number(cue: Mapping[str, Any]) -> int:
    raw = cue.get("number", cue.get("original_cue"))
    try:
        number = int(raw)
    except (TypeError, ValueError) as exc:
        raise BoundaryRefinementError("source cue has invalid number") from exc
    if number <= 0:
        raise BoundaryRefinementError("source cue number must be > 0")
    return number


def _cue_interval(cue: Mapping[str, Any]) -> tuple[int, int]:
    start = _finite_nonnegative_int(cue.get("start_ms"), label="source cue start")
    end = _finite_nonnegative_int(cue.get("end_ms"), label="source cue end")
    if end <= start:
        raise BoundaryRefinementError("source cue interval must be monotonic")
    return start, end


def _row_lrc_indices(row: Mapping[str, Any]) -> tuple[int, ...]:
    values: set[int] = set()
    for item in str(row.get("lrc_indices") or "").split(";"):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit():
            raise BoundaryRefinementError("report row has non-numeric lrc_indices")
        values.add(int(item))
    return tuple(sorted(values))


def _risk_flags_for_group(
    rows: Sequence[Mapping[str, Any]],
    *,
    cue_number: int,
    previous_cue_number: int | None,
    next_cue_number: int | None,
) -> tuple[str, ...]:
    flags: set[str] = set()
    evidence = " ".join(str(row.get("evidence") or "") for row in rows).lower()
    status = " ".join(str(row.get("status") or "") for row in rows).lower()
    if "cut" in evidence or "cut" in status:
        flags.add("possible_cut")
    if "crossfade" in evidence or "overlap" in evidence:
        flags.add("crossfade")
    if "source_gap" in evidence or "unprojectable" in evidence:
        flags.add("source_gap")
    if "split_suppressed" in evidence or "split_unreliable_long_cue" in status:
        flags.add("mapping_discontinuity")
    if previous_cue_number is not None and cue_number != previous_cue_number + 1:
        flags.add("track_transition")
    if next_cue_number is not None and next_cue_number != cue_number + 1:
        flags.add("track_transition")
    return tuple(sorted(flags))


def _context_for_tier(config: BoundaryRefinementConfig, tier: str) -> int:
    if tier == "A_local_refine":
        return int(config.local_context_ms)
    if tier == "B_multi_evidence":
        return int(config.multi_evidence_context_ms)
    if tier == "C_heavy_boundary":
        return int(config.heavy_context_ms)
    return int(config.structural_context_ms)


def build_boundary_refinement_plan(
    *,
    task_fingerprint_sha256: str,
    source_srt_sha256: str,
    final_audio_sha256: str,
    report_sha256: str,
    source_cues: Sequence[Mapping[str, Any]],
    report_rows: Sequence[Mapping[str, Any]],
    config: BoundaryRefinementConfig | None = None,
) -> dict[str, Any]:
    """Build per-start/per-end boundary jobs without reading audio.

    Every original editor cue is eligible.  Difficulty routing controls the
    amount of acoustic work requested later.  LRC/projected timing is never
    copied into a job as an authoritative timestamp.
    """

    config = config or BoundaryRefinementConfig()
    config.validate()
    for label, value in (
        ("task_fingerprint_sha256", task_fingerprint_sha256),
        ("source_srt_sha256", source_srt_sha256),
        ("final_audio_sha256", final_audio_sha256),
        ("report_sha256", report_sha256),
    ):
        if len(str(value or "")) != 64:
            raise BoundaryRefinementError(f"{label} must be a SHA-256 hex digest")

    cue_index: dict[int, Mapping[str, Any]] = {}
    ordered_numbers: list[int] = []
    for cue in source_cues:
        number = _cue_number(cue)
        if number in cue_index:
            raise BoundaryRefinementError("duplicate source cue number")
        _cue_interval(cue)
        cue_index[number] = cue
        ordered_numbers.append(number)
    if not cue_index:
        raise BoundaryRefinementError("boundary plan requires at least one source cue")

    rows_by_original: dict[int, list[Mapping[str, Any]]] = {}
    for row in report_rows:
        raw = str(row.get("original_cue") or "").strip()
        if not raw:
            continue
        if not raw.isdigit():
            raise BoundaryRefinementError("report row original_cue is invalid")
        number = int(raw)
        if number not in cue_index:
            raise BoundaryRefinementError("report row references unknown source cue")
        rows_by_original.setdefault(number, []).append(row)

    ordered_numbers.sort()
    jobs: list[dict[str, Any]] = []
    for position, number in enumerate(ordered_numbers):
        cue = cue_index[number]
        start_ms, end_ms = _cue_interval(cue)
        rows = rows_by_original.get(number, [])
        indices = sorted({index for row in rows for index in _row_lrc_indices(row)})
        canonical_line_count = max(1, len(indices))
        text = " ".join(str(row.get("text") or "").strip() for row in rows if str(row.get("text") or "").strip())
        if not text:
            text = str(cue.get("text") or "")
        previous_number = ordered_numbers[position - 1] if position else None
        next_number = ordered_numbers[position + 1] if position + 1 < len(ordered_numbers) else None
        flags = _risk_flags_for_group(
            rows,
            cue_number=number,
            previous_cue_number=previous_number,
            next_cue_number=next_number,
        )
        duration = end_ms - start_ms
        for boundary_kind, editor_ms in (("start", start_ms), ("end", end_ms)):
            # At planning time there may be no acoustic proposal yet.  Use the
            # editor location solely to classify ordinary work; risk flags,
            # duration, and canonical multiplicity still escalate hard cases.
            tier, capabilities = classify_boundary_difficulty(
                editor_ms=editor_ms,
                proposed_ms=editor_ms,
                cue_duration_ms=duration,
                canonical_line_count=canonical_line_count,
                risk_flags=flags,
            )
            context = _context_for_tier(config, tier)
            window = [max(0, editor_ms - context), editor_ms + context]
            identity = {
                "cue_number": number,
                "boundary_kind": boundary_kind,
                "editor_ms": editor_ms,
                "canonical_indices": indices,
                "canonical_text_sha256": _sha_text(text),
                "mix_window_ms": window,
            }
            jobs.append(
                {
                    "boundary_id": _sha_json(identity),
                    "cue_number": number,
                    "boundary_kind": boundary_kind,
                    "editor_ms": editor_ms,
                    "cue_interval_ms": [start_ms, end_ms],
                    "cue_duration_ms": duration,
                    "canonical_line_count": canonical_line_count,
                    "canonical_indices": indices,
                    "canonical_text": text,
                    "canonical_text_sha256": _sha_text(text),
                    "difficulty_tier": tier,
                    "risk_flags": list(flags),
                    "mix_window_ms": window,
                    "requested_capabilities": list(capabilities),
                    "automatic_mutation_allowed": False,
                    "execution_state": "planned_not_executed",
                }
            )

    if len(jobs) > config.max_boundaries:
        raise BoundaryRefinementError(
            f"boundary plan has {len(jobs)} jobs, exceeding max_boundaries={config.max_boundaries}"
        )
    tier_counts: dict[str, int] = {}
    for job in jobs:
        tier = str(job["difficulty_tier"])
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    return {
        "schema_version": BOUNDARY_REFINEMENT_PLAN_SCHEMA_VERSION,
        "policy_id": BOUNDARY_REFINEMENT_POLICY_ID,
        "mode": "plan_only",
        "backend_execution_performed": False,
        "task_fingerprint_sha256": task_fingerprint_sha256,
        "source_srt_sha256": source_srt_sha256,
        "final_audio_sha256": final_audio_sha256,
        "report_sha256": report_sha256,
        "editor_timing_authority": "strong_but_rebuttable_prior",
        "lrc_timing_authority": "routing_prior_only_never_mutation_authority",
        "final_mix_audio_authority": "required_for_automatic_boundary_mutation",
        "config": config.to_dict(),
        "summary": {
            "source_cue_count": len(cue_index),
            "boundary_job_count": len(jobs),
            "difficulty_tier_counts": dict(sorted(tier_counts.items())),
        },
        "jobs": jobs,
    }


def build_boundary_evidence_artifact(
    *,
    plan: Mapping[str, Any],
    evidence_by_boundary_id: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Bind backend evidence to an exact boundary plan.

    Evidence providers remain non-authoritative.  This function validates their
    identity and preserves correlation groups for later independence checks.
    """

    if plan.get("schema_version") != BOUNDARY_REFINEMENT_PLAN_SCHEMA_VERSION:
        raise BoundaryRefinementError("unsupported boundary plan schema")
    if plan.get("mode") != "plan_only":
        raise BoundaryRefinementError("boundary plan is not plan_only")
    raw_jobs = plan.get("jobs")
    if not isinstance(raw_jobs, list):
        raise BoundaryRefinementError("boundary plan jobs must be a list")
    jobs = {str(job.get("boundary_id") or ""): job for job in raw_jobs if isinstance(job, Mapping)}
    if "" in jobs or len(jobs) != len(raw_jobs):
        raise BoundaryRefinementError("boundary plan IDs must be unique/non-empty")
    unknown = sorted(set(evidence_by_boundary_id) - set(jobs))
    if unknown:
        raise BoundaryRefinementError("boundary evidence references unknown boundary_id")

    records: list[dict[str, Any]] = []
    calibration_artifacts: dict[str, dict[str, Any]] = {}
    for boundary_id in sorted(jobs):
        job = jobs[boundary_id]
        points = evidence_by_boundary_id.get(boundary_id, ())
        normalized: list[dict[str, Any]] = []
        seen_families: set[str] = set()
        for raw in points:
            family = str(raw.get("family") or "").strip()
            correlation_group = str(raw.get("correlation_group") or family).strip()
            backend_id = str(raw.get("backend_id") or "").strip()
            backend_version = str(raw.get("backend_version") or "").strip()
            backend_profile_id = str(raw.get("backend_profile_id") or "").strip()
            model_id = str(raw.get("model_id") or "").strip()
            model_revision = str(raw.get("model_revision") or "").strip()
            implementation_revision = str(raw.get("implementation_revision") or "").strip()
            adapter_contract_revision = str(raw.get("adapter_contract_revision") or "").strip()
            lexical_id = str(raw.get("lexical_id") or "").strip()
            lexical_revision = str(raw.get("lexical_revision") or "").strip()
            lexical_units_sha256 = str(raw.get("lexical_units_sha256") or "").strip()
            if bool(lexical_id) != bool(lexical_revision):
                raise BoundaryRefinementError(
                    "boundary evidence lexical_id and lexical_revision must be declared together"
                )
            audio_basis = str(raw.get("audio_basis") or "").strip()
            language = str(raw.get("language") or "").strip()
            window_policy_id = str(raw.get("window_policy_id") or "").strip()
            if not family or family in seen_families:
                raise BoundaryRefinementError("evidence families must be unique/non-empty per boundary")
            seen_families.add(family)
            if not correlation_group or not backend_id or not model_id:
                raise BoundaryRefinementError(
                    "boundary evidence requires correlation_group, backend_id, and model_id"
                )
            boundary_ms = _finite_nonnegative_int(raw.get("boundary_ms"), label="evidence boundary_ms")
            uncertainty_ms = _finite_nonnegative_int(
                raw.get("uncertainty_ms", 0), label="evidence uncertainty_ms"
            )
            try:
                confidence = float(raw.get("confidence"))
            except (TypeError, ValueError) as exc:
                raise BoundaryRefinementError("evidence confidence is invalid") from exc
            if not 0.0 <= confidence <= 1.0:
                raise BoundaryRefinementError("evidence confidence must be within [0,1]")

            calibration_passed = bool(raw.get("calibration_passed", False))
            calibration_sha256 = str(raw.get("calibration_sha256") or "").strip()
            calibration_artifact = raw.get("calibration_artifact")
            calibration_uncertainty_floor_ms = 0
            if calibration_passed:
                if not language or not backend_profile_id or not window_policy_id:
                    raise BoundaryRefinementError(
                        "calibrated boundary evidence must declare language, backend profile, and window policy scope"
                    )
                if not implementation_revision or not adapter_contract_revision:
                    raise BoundaryRefinementError(
                        "calibrated boundary evidence must bind implementation and adapter-contract revisions"
                    )
                if not lexical_id or not lexical_revision or len(lexical_units_sha256) != 64:
                    raise BoundaryRefinementError(
                        "calibrated boundary evidence must carry lexical identity and lexical-unit SHA"
                    )
                expected_lexical_sha = _outer_lexical_units_sha256(
                    language=language,
                    canonical_text=str(job["canonical_text"]),
                )
                if lexical_units_sha256 != expected_lexical_sha:
                    raise BoundaryRefinementError(
                        "boundary evidence lexical-unit SHA does not match canonical job text"
                    )
                if not isinstance(calibration_artifact, Mapping):
                    raise BoundaryRefinementError(
                        "calibrated boundary evidence must carry its calibration artifact"
                    )
                artifact_sha = str(calibration_artifact.get("artifact_sha256") or "").strip()
                if len(artifact_sha) != 64:
                    raise BoundaryRefinementError("calibration artifact SHA is invalid")
                if calibration_sha256 and calibration_sha256 != artifact_sha:
                    raise BoundaryRefinementError(
                        "claimed calibration SHA does not match calibration artifact"
                    )
                evidence_sha256 = str(raw.get("evidence_sha256") or "").strip()
                if len(evidence_sha256) != 64:
                    raise BoundaryRefinementError(
                        "calibrated boundary evidence must carry a 64-character evidence SHA"
                    )
                expected_scope = {
                    "boundary_kind": str(job["boundary_kind"]),
                    "audio_basis": audio_basis,
                    "language": language,
                    "backend_version": backend_version,
                    "backend_profile_id": backend_profile_id,
                    "window_policy_id": window_policy_id,
                    "model_id": model_id,
                    "model_revision": model_revision,
                    "implementation_revision": implementation_revision,
                    "adapter_contract_revision": adapter_contract_revision,
                }
                if lexical_id:
                    expected_scope["lexical_id"] = lexical_id
                    expected_scope["lexical_revision"] = lexical_revision
                if not production_calibration_artifact_is_authoritative(
                    calibration_artifact,
                    backend_id=backend_id,
                    family=family,
                    correlation_group=correlation_group,
                    expected_scope=expected_scope,
                ):
                    raise BoundaryRefinementError(
                        "boundary evidence calibration artifact is not authoritative for this backend/scope"
                    )
                calibration_uncertainty_floor_ms = _calibration_uncertainty_floor_ms(
                    calibration_artifact
                )
                uncertainty_ms = max(uncertainty_ms, calibration_uncertainty_floor_ms)
                artifact_dict = dict(calibration_artifact)
                existing = calibration_artifacts.get(artifact_sha)
                if existing is not None and existing != artifact_dict:
                    raise BoundaryRefinementError("conflicting calibration artifacts share one SHA")
                calibration_artifacts[artifact_sha] = artifact_dict
                calibration_sha256 = artifact_sha
            else:
                if calibration_sha256 or calibration_artifact is not None:
                    raise BoundaryRefinementError(
                        "uncalibrated boundary evidence must not carry calibration claims"
                    )

            normalized.append(
                {
                    "family": family,
                    "correlation_group": correlation_group,
                    "boundary_ms": boundary_ms,
                    "confidence": confidence,
                    "uncertainty_ms": uncertainty_ms,
                    "calibration_uncertainty_floor_ms": calibration_uncertainty_floor_ms,
                    "backend_id": backend_id,
                    "backend_version": backend_version,
                    "backend_profile_id": backend_profile_id,
                    "model_id": model_id,
                    "model_revision": model_revision,
                    "implementation_revision": implementation_revision,
                    "adapter_contract_revision": adapter_contract_revision,
                    "lexical_id": lexical_id,
                    "lexical_revision": lexical_revision,
                    "lexical_units_sha256": lexical_units_sha256,
                    "audio_basis": audio_basis,
                    "language": language,
                    "window_policy_id": window_policy_id,
                    "evidence_sha256": str(raw.get("evidence_sha256") or ""),
                    "calibration_passed": calibration_passed,
                    "calibration_sha256": calibration_sha256,
                }
            )
        records.append(
            {
                "boundary_id": boundary_id,
                "cue_number": int(job["cue_number"]),
                "boundary_kind": str(job["boundary_kind"]),
                "points": normalized,
            }
        )
    return {
        "schema_version": BOUNDARY_EVIDENCE_SCHEMA_VERSION,
        "policy_id": BOUNDARY_REFINEMENT_POLICY_ID,
        "mode": "evidence_only_no_mutation",
        "task_fingerprint_sha256": str(plan["task_fingerprint_sha256"]),
        "source_srt_sha256": str(plan["source_srt_sha256"]),
        "final_audio_sha256": str(plan["final_audio_sha256"]),
        "report_sha256": str(plan["report_sha256"]),
        "plan_sha256": _sha_json(plan),
        "calibration_artifacts": dict(sorted(calibration_artifacts.items())),
        "record_count": len(records),
        "records": records,
    }


def adjudicate_boundary_refinement(
    *,
    plan: Mapping[str, Any],
    evidence: Mapping[str, Any],
    structural_confirmations: Iterable[str] = (),
) -> dict[str, Any]:
    """Turn evidence into explicit keep/refine/escalate decisions."""

    if plan.get("schema_version") != BOUNDARY_REFINEMENT_PLAN_SCHEMA_VERSION:
        raise BoundaryRefinementError("unsupported boundary plan schema")
    if evidence.get("schema_version") != BOUNDARY_EVIDENCE_SCHEMA_VERSION:
        raise BoundaryRefinementError("unsupported boundary evidence schema")
    if evidence.get("plan_sha256") != _sha_json(plan):
        raise BoundaryRefinementError("boundary evidence belongs to another plan")
    for key in (
        "task_fingerprint_sha256",
        "source_srt_sha256",
        "final_audio_sha256",
        "report_sha256",
    ):
        if str(evidence.get(key) or "") != str(plan.get(key) or ""):
            raise BoundaryRefinementError(f"boundary evidence {key} mismatch")

    raw_jobs = plan.get("jobs")
    raw_records = evidence.get("records")
    if not isinstance(raw_jobs, list) or not isinstance(raw_records, list):
        raise BoundaryRefinementError("boundary artifacts are malformed")
    jobs = {str(row["boundary_id"]): row for row in raw_jobs}
    records = {str(row["boundary_id"]): row for row in raw_records}
    if set(jobs) != set(records):
        raise BoundaryRefinementError("boundary evidence record set does not match plan")
    structural = {str(value) for value in structural_confirmations}
    calibration_artifacts = evidence.get("calibration_artifacts")
    if not isinstance(calibration_artifacts, Mapping):
        raise BoundaryRefinementError("boundary evidence calibration catalog is missing")

    decisions: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for boundary_id in sorted(jobs):
        job = jobs[boundary_id]
        record = records[boundary_id]
        points = record.get("points")
        if not isinstance(points, list):
            raise BoundaryRefinementError("boundary evidence points must be a list")
        verified_points: list[dict[str, Any]] = []
        verified_calibration_sha256s: set[str] = set()
        for point in points:
            if not isinstance(point, Mapping):
                raise BoundaryRefinementError("boundary evidence point must be an object")
            sanitized = dict(point)
            claimed = bool(point.get("calibration_passed", False))
            claimed_sha = str(point.get("calibration_sha256") or "").strip()
            if claimed:
                artifact = calibration_artifacts.get(claimed_sha)
                expected_scope = {
                    "boundary_kind": str(job["boundary_kind"]),
                    "audio_basis": str(point.get("audio_basis") or ""),
                    "language": str(point.get("language") or ""),
                    "backend_version": str(point.get("backend_version") or ""),
                    "backend_profile_id": str(point.get("backend_profile_id") or ""),
                    "window_policy_id": str(point.get("window_policy_id") or ""),
                    "model_id": str(point.get("model_id") or ""),
                    "model_revision": str(point.get("model_revision") or ""),
                    "implementation_revision": str(point.get("implementation_revision") or ""),
                    "adapter_contract_revision": str(point.get("adapter_contract_revision") or ""),
                }
                lexical_id = str(point.get("lexical_id") or "").strip()
                lexical_revision = str(point.get("lexical_revision") or "").strip()
                if bool(lexical_id) != bool(lexical_revision):
                    raise BoundaryRefinementError(
                        "boundary evidence lexical identity is incomplete during adjudication"
                    )
                lexical_units_sha256 = str(point.get("lexical_units_sha256") or "").strip()
                if not lexical_id or len(lexical_units_sha256) != 64:
                    raise BoundaryRefinementError(
                        "calibrated boundary evidence lexical-unit identity is missing during adjudication"
                    )
                if lexical_units_sha256 != _outer_lexical_units_sha256(
                    language=str(point.get("language") or ""),
                    canonical_text=str(job["canonical_text"]),
                ):
                    raise BoundaryRefinementError(
                        "boundary evidence lexical-unit SHA no longer matches canonical job text"
                    )
                expected_scope["lexical_id"] = lexical_id
                expected_scope["lexical_revision"] = lexical_revision
                if len(str(point.get("evidence_sha256") or "")) != 64:
                    raise BoundaryRefinementError(
                        "calibrated boundary evidence SHA is invalid during adjudication"
                    )
                if (
                    not isinstance(artifact, Mapping)
                    or str(artifact.get("artifact_sha256") or "") != claimed_sha
                    or not production_calibration_artifact_is_authoritative(
                        artifact,
                        backend_id=str(point.get("backend_id") or ""),
                        family=str(point.get("family") or ""),
                        correlation_group=str(point.get("correlation_group") or ""),
                        expected_scope=expected_scope,
                    )
                ):
                    raise BoundaryRefinementError(
                        "boundary evidence contains an unverifiable calibration claim"
                    )
                floor_ms = _calibration_uncertainty_floor_ms(artifact)
                claimed_uncertainty = _finite_nonnegative_int(
                    point.get("uncertainty_ms", 0), label="evidence uncertainty_ms"
                )
                sanitized["uncertainty_ms"] = max(claimed_uncertainty, floor_ms)
                sanitized["calibration_uncertainty_floor_ms"] = floor_ms
                verified_calibration_sha256s.add(claimed_sha)
            elif claimed_sha:
                raise BoundaryRefinementError(
                    "uncalibrated boundary evidence carries a calibration SHA"
                )
            sanitized["calibration_passed"] = claimed
            verified_points.append(sanitized)

        proposed_candidates = [
            int(point["boundary_ms"])
            for point in verified_points
            if str(point.get("family") or "")
            in {
                "final_mix_forced_alignment",
                "final_mix_singing_alignment",
                "final_mix_acoustic_onset",
                "final_mix_acoustic_offset",
            }
        ]
        proposed_ms = (
            sorted(proposed_candidates)[len(proposed_candidates) // 2]
            if proposed_candidates
            else int(job["editor_ms"])
        )
        try:
            decision = adjudicate_boundary_candidate(
                editor_ms=int(job["editor_ms"]),
                proposed_ms=proposed_ms,
                cue_duration_ms=int(job["cue_duration_ms"]),
                canonical_line_count=int(job["canonical_line_count"]),
                evidence_points=verified_points,
                risk_flags=job.get("risk_flags") or [],
                structural_resolution_confirmed=boundary_id in structural,
                verified_calibration_sha256s=verified_calibration_sha256s,
            )
        except BoundaryAuthorityError as exc:
            raise BoundaryRefinementError(str(exc)) from exc
        row = {
            "boundary_id": boundary_id,
            "cue_number": int(job["cue_number"]),
            "boundary_kind": str(job["boundary_kind"]),
            "editor_ms": int(job["editor_ms"]),
            "selected_ms": int(decision.selected_ms),
            "action": decision.action,
            "automatic_mutation_allowed": decision.automatic_mutation_allowed,
            "candidate_delta_ms": int(decision.candidate_delta_ms),
            "audio_family_count": int(decision.audio_family_count),
            "audio_spread_ms": decision.audio_spread_ms,
            "evidence_basis": list(decision.evidence_basis),
            "difficulty_tier": decision.difficulty_tier,
            "required_capabilities": list(decision.required_capabilities),
            "reason": decision.reason,
        }
        decisions.append(row)
        counts[row["action"]] = counts.get(row["action"], 0) + 1

    return {
        "schema_version": BOUNDARY_DECISION_SCHEMA_VERSION,
        "policy_id": BOUNDARY_REFINEMENT_POLICY_ID,
        "boundary_authority_policy_version": BOUNDARY_AUTHORITY_POLICY_VERSION,
        "mode": "decision_only",
        "task_fingerprint_sha256": str(plan["task_fingerprint_sha256"]),
        "source_srt_sha256": str(plan["source_srt_sha256"]),
        "final_audio_sha256": str(plan["final_audio_sha256"]),
        "report_sha256": str(plan["report_sha256"]),
        "plan_sha256": _sha_json(plan),
        "evidence_sha256": _sha_json(evidence),
        "summary": {
            "boundary_count": len(decisions),
            "action_counts": dict(sorted(counts.items())),
            "automatic_mutation_count": sum(
                bool(row["automatic_mutation_allowed"]) for row in decisions
            ),
        },
        "decisions": decisions,
    }


def apply_boundary_decisions_to_rows(
    *,
    report_rows: Sequence[Mapping[str, Any]],
    source_cues: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
    evidence: Mapping[str, Any],
    decisions: Mapping[str, Any],
    structural_confirmations: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Apply only audio-authorized outer-boundary refinements.

    Rows representing the same original cue must still form one unsplit interval;
    structural splitting is intentionally outside this function.  That prevents a
    boundary model from silently changing lyric ownership while refining timing.

    Decisions are not trusted as standalone mutation authority.  The exact evidence
    artifact is freshly adjudicated here and must reproduce the caller-supplied
    decision artifact exactly before any row is changed.
    """

    if plan.get("schema_version") != BOUNDARY_REFINEMENT_PLAN_SCHEMA_VERSION:
        raise BoundaryRefinementError("unsupported boundary plan schema")
    if decisions.get("schema_version") != BOUNDARY_DECISION_SCHEMA_VERSION:
        raise BoundaryRefinementError("unsupported boundary decision schema")
    if str(decisions.get("plan_sha256") or "") != _sha_json(plan):
        raise BoundaryRefinementError("boundary decisions belong to another plan")
    recomputed = adjudicate_boundary_refinement(
        plan=plan,
        evidence=evidence,
        structural_confirmations=structural_confirmations,
    )
    if dict(decisions) != recomputed:
        raise BoundaryRefinementError(
            "boundary decisions do not match fresh adjudication of the bound evidence"
        )
    raw_decisions = decisions.get("decisions")
    if not isinstance(raw_decisions, list):
        raise BoundaryRefinementError("boundary decisions must be a list")
    by_cue: dict[int, dict[str, Mapping[str, Any]]] = {}
    for row in raw_decisions:
        number = int(row["cue_number"])
        kind = str(row["boundary_kind"])
        if kind not in {"start", "end"}:
            raise BoundaryRefinementError("boundary decision kind is invalid")
        if kind in by_cue.setdefault(number, {}):
            raise BoundaryRefinementError("duplicate boundary decision for cue/kind")
        by_cue[number][kind] = row

    source_index = {_cue_number(cue): cue for cue in source_cues}
    output = [dict(row) for row in report_rows]
    rows_by_original: dict[int, list[dict[str, Any]]] = {}
    for row in output:
        raw = str(row.get("original_cue") or "").strip()
        if raw.isdigit():
            rows_by_original.setdefault(int(raw), []).append(row)

    for number, boundary_rows in rows_by_original.items():
        if number not in source_index or number not in by_cue:
            continue
        # Structural/internal splits require their own reviewed topology evidence;
        # outer-boundary refinement only owns one-row editor intervals.
        if len(boundary_rows) != 1:
            continue
        row = boundary_rows[0]
        source_start, source_end = _cue_interval(source_index[number])
        current_start = _finite_nonnegative_int(row.get("start_ms"), label="current row start")
        current_end = _finite_nonnegative_int(row.get("end_ms"), label="current row end")
        if current_end <= current_start:
            raise BoundaryRefinementError(f"current cue {number} interval is not monotonic")
        existing_authority = str(row.get("boundary_authority") or "").strip()
        current_differs_from_editor = (current_start, current_end) != (source_start, source_end)
        if current_differs_from_editor and existing_authority not in {
            "audio_verified_boundary_v1",
            "manual_verified_boundary",
            "manual_verified_interval",
        }:
            raise BoundaryRefinementError(
                f"cue {number} already differs from editor timing without boundary authority"
            )
        if current_differs_from_editor and existing_authority in {
            "manual_verified_boundary",
            "manual_verified_interval",
        }:
            # A machine refinement must never overrule a human-audited interval.
            continue
        # Preserve any already-authorized boundary instead of silently resetting
        # the untouched side to the editor interval.
        start = current_start
        end = current_end
        authority_details: list[str] = []
        for kind in ("start", "end"):
            decision = by_cue[number].get(kind)
            if not decision or not bool(decision.get("automatic_mutation_allowed")):
                continue
            selected = _finite_nonnegative_int(
                decision.get("selected_ms"), label="selected boundary"
            )
            if kind == "start":
                start = selected
            else:
                end = selected
            authority_details.append(f"{kind}:{decision['boundary_id']}")
        if end <= start:
            raise BoundaryRefinementError(
                f"authorized boundary decisions invert cue {number} interval"
            )
        if authority_details:
            row["start_ms"] = start
            row["end_ms"] = end
            row["boundary_authority"] = "audio_verified_boundary_v1"
            row["boundary_authority_details"] = ";".join(authority_details)
            row["evidence"] = str(row.get("evidence") or "") + "+boundary_refinement_v1"
    return output
