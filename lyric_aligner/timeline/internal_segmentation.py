"""Audio-authorized splitting of long editor cues into canonical lyric segments.

Outer editor cue start/end timing and internal lyric segmentation are different
kinds of authority.  This module owns only the latter.  It consumes canonical
sub-line provenance persisted by the rebuild stage, asks independent *direct
final-mix* aligners for each internal boundary, verifies their human-gold
calibration artifacts, and materializes a split only when every internal
boundary of the cue is independently authorized.

Projected LRC times remain routing priors only.  Source-projected alignment,
editor timing, ASR timing, or one calibrated aligner can never create an
internal split on their own.
"""

from __future__ import annotations

import hashlib
import json
from math import ceil
from statistics import median
from typing import Any, Mapping, Sequence

from lyric_aligner.text.alignment_lexical import (
    AlignmentLexicalError,
    segment_units_from_canonical_segments,
)
from lyric_aligner.evaluation.production_calibration import (
    production_calibration_artifact_is_authoritative,
)
from lyric_aligner.evaluation.selective_consensus_calibration import (
    internal_selective_consensus_calibration_is_authoritative,
    select_nearest_lexical_prior_prediction,
)


INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION = "internal-segmentation-plan-1.0"
INTERNAL_SEGMENTATION_EVIDENCE_SCHEMA_VERSION = "internal-segmentation-evidence-1.0"
INTERNAL_SEGMENTATION_SELECTIVE_EVIDENCE_SCHEMA_VERSION = (
    "internal-segmentation-evidence-1.1-selective-consensus"
)
INTERNAL_SEGMENTATION_DECISION_SCHEMA_VERSION = "internal-segmentation-decision-1.0"
INTERNAL_SEGMENTATION_POLICY_ID = "audio-verified-internal-segmentation-v1"

DIRECT_INTERNAL_ALIGNMENT_FAMILIES = frozenset(
    {
        "final_mix_singing_alignment",
        "final_mix_forced_alignment",
    }
)
MAX_INTERNAL_EVIDENCE_UNCERTAINTY_MS = 180
MAX_INTERNAL_ALIGNMENT_SPREAD_MS = 250
MIN_MATERIALIZED_SEGMENT_MS = 250
DEFAULT_INTERNAL_CONTEXT_MS = 3500


class InternalSegmentationError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _nonnegative_int(value: Any, *, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise InternalSegmentationError(f"{label} is invalid") from exc
    if result < 0:
        raise InternalSegmentationError(f"{label} must be nonnegative")
    return result


def _cue_number(cue: Mapping[str, Any]) -> int:
    try:
        number = int(cue.get("number", cue.get("original_cue")))
    except (TypeError, ValueError) as exc:
        raise InternalSegmentationError("cue number is invalid") from exc
    if number <= 0:
        raise InternalSegmentationError("cue number must be positive")
    return number


def _cue_interval(cue: Mapping[str, Any]) -> tuple[int, int]:
    start = _nonnegative_int(cue.get("start_ms"), label="cue start")
    end = _nonnegative_int(cue.get("end_ms"), label="cue end")
    if end <= start:
        raise InternalSegmentationError("cue interval must be monotonic")
    return start, end


def _parse_segments(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = row.get("canonical_segments_json")
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InternalSegmentationError("canonical_segments_json is malformed") from exc
    else:
        value = raw
    if not isinstance(value, list):
        raise InternalSegmentationError("canonical_segments_json must contain a list")

    segments: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for raw_segment in value:
        if not isinstance(raw_segment, Mapping):
            raise InternalSegmentationError("canonical segment must be an object")
        text = str(raw_segment.get("text") or "").strip()
        if not text:
            raise InternalSegmentationError("canonical segment text must be non-empty")
        track_index = _nonnegative_int(raw_segment.get("track_index"), label="segment track_index")
        lrc_index = _nonnegative_int(raw_segment.get("lrc_index"), label="segment lrc_index")
        projected_ms = _nonnegative_int(raw_segment.get("projected_ms"), label="segment projected_ms")
        identity = (track_index, lrc_index)
        if identity in seen:
            raise InternalSegmentationError("canonical segment identity is duplicated")
        seen.add(identity)
        segment = {
            "track_index": track_index,
            "lrc_index": lrc_index,
            "text": text,
            "projected_ms": projected_ms,
        }
        segment["segment_sha256"] = _sha_json(segment)
        segments.append(segment)
    return segments


def _row_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "original_cue": str(row.get("original_cue") or ""),
        "start_ms": _nonnegative_int(row.get("start_ms"), label="row start_ms"),
        "end_ms": _nonnegative_int(row.get("end_ms"), label="row end_ms"),
        "text": str(row.get("text") or ""),
        "lrc_indices": str(row.get("lrc_indices") or ""),
        "canonical_segments_json": str(row.get("canonical_segments_json") or ""),
    }


def _row_fingerprint(row: Mapping[str, Any]) -> str:
    identity = _row_identity(row)
    if identity["end_ms"] <= identity["start_ms"]:
        raise InternalSegmentationError("report row interval is not monotonic")
    return _sha_json(identity)


def _topology_risk_flags(row: Mapping[str, Any]) -> list[str]:
    evidence = str(row.get("evidence") or "").casefold()
    status = str(row.get("status") or "").casefold()
    flags: set[str] = set()
    if "crossfade" in evidence:
        flags.add("crossfade")
    if "overlap" in evidence:
        flags.add("overlap")
    if "split_unreliable" in status:
        flags.add("preexisting_split_unreliable")
    return sorted(flags)


def build_internal_segmentation_plan(
    *,
    task_fingerprint_sha256: str,
    source_srt_sha256: str,
    final_audio_sha256: str,
    report_sha256: str,
    source_cues: Sequence[Mapping[str, Any]],
    report_rows: Sequence[Mapping[str, Any]],
    context_ms: int = DEFAULT_INTERNAL_CONTEXT_MS,
) -> dict[str, Any]:
    """Build internal-boundary jobs from persisted canonical sub-line provenance."""

    for label, value in (
        ("task_fingerprint_sha256", task_fingerprint_sha256),
        ("source_srt_sha256", source_srt_sha256),
        ("final_audio_sha256", final_audio_sha256),
        ("report_sha256", report_sha256),
    ):
        if len(str(value or "")) != 64:
            raise InternalSegmentationError(f"{label} must be a SHA-256 digest")
    context_ms = _nonnegative_int(context_ms, label="context_ms")
    if context_ms < 500:
        raise InternalSegmentationError("context_ms must be at least 500ms")

    source_index: dict[int, Mapping[str, Any]] = {}
    for cue in source_cues:
        number = _cue_number(cue)
        if number in source_index:
            raise InternalSegmentationError("duplicate source cue number")
        _cue_interval(cue)
        source_index[number] = cue

    rows_by_original: dict[int, list[Mapping[str, Any]]] = {}
    for row in report_rows:
        raw_number = str(row.get("original_cue") or "").strip()
        if not raw_number:
            continue
        if not raw_number.isdigit():
            raise InternalSegmentationError("report row original_cue is invalid")
        number = int(raw_number)
        if number not in source_index:
            raise InternalSegmentationError("report row references unknown source cue")
        rows_by_original.setdefault(number, []).append(row)

    jobs: list[dict[str, Any]] = []
    candidate_cues = 0
    provenance_mismatch_skipped = 0
    for number in sorted(rows_by_original):
        rows = rows_by_original[number]
        # Already-split/report-multirow cues have topology we do not own here.
        if len(rows) != 1:
            continue
        row = rows[0]
        segments = _parse_segments(row)
        if len(segments) < 2:
            continue
        candidate_cues += 1
        row_start, row_end = _cue_interval(row)
        source_start, source_end = _cue_interval(source_index[number])
        if row_start < 0 or row_end <= row_start:
            raise InternalSegmentationError("candidate row interval is invalid")
        risks = _topology_risk_flags(row)
        row_fingerprint = _row_fingerprint(row)
        canonical_text = " ".join(segment["text"] for segment in segments)
        expected_lrc_indices = ";".join(str(segment["lrc_index"]) for segment in segments)
        if (
            str(row.get("text") or "").strip() != canonical_text
            or str(row.get("lrc_indices") or "").strip() != expected_lrc_indices
        ):
            provenance_mismatch_skipped += 1
            continue

        for boundary_index in range(1, len(segments)):
            left = segments[boundary_index - 1]
            right = segments[boundary_index]
            projected = int(right["projected_ms"])
            if row_start + MIN_MATERIALIZED_SEGMENT_MS <= projected <= row_end - MIN_MATERIALIZED_SEGMENT_MS:
                routing_prior_ms = projected
                routing_basis = "right_segment_projected_lrc_prior"
            else:
                routing_prior_ms = int(round(row_start + (row_end - row_start) * boundary_index / len(segments)))
                routing_basis = "proportional_fallback_no_timing_authority"
            routing_search_window = [
                max(row_start, routing_prior_ms - context_ms),
                min(row_end, routing_prior_ms + context_ms),
            ]
            # Forced alignment receives the complete owning cue. Cropping only
            # around the internal prior can remove lexical material from the
            # first/last canonical segment and make full-sequence validation
            # impossible. The narrow range remains routing/diagnostic only.
            alignment_window = [row_start, row_end]
            identity = {
                "cue_number": number,
                "boundary_kind": "internal",
                "boundary_index": boundary_index,
                "left_segment_sha256": left["segment_sha256"],
                "right_segment_sha256": right["segment_sha256"],
                "row_fingerprint_sha256": row_fingerprint,
                "source_interval_ms": [source_start, source_end],
            }
            jobs.append(
                {
                    "boundary_id": _sha_json(identity),
                    "cue_number": number,
                    "boundary_kind": "internal",
                    "boundary_index": boundary_index,
                    "segment_count": len(segments),
                    "left_segment": left,
                    "right_segment": right,
                    "canonical_text": canonical_text,
                    "canonical_segments": segments,
                    "routing_prior_ms": routing_prior_ms,
                    "routing_prior_basis": routing_basis,
                    "routing_search_window_ms": routing_search_window,
                    "mix_window_ms": alignment_window,
                    "row_interval_ms": [row_start, row_end],
                    "source_interval_ms": [source_start, source_end],
                    "row_fingerprint_sha256": row_fingerprint,
                    "topology_risk_flags": risks,
                    "requested_capabilities": [
                        "direct_final_mix_singing_alignment",
                        "independent_direct_final_mix_forced_alignment",
                    ],
                    "automatic_mutation_allowed": False,
                    "execution_state": "planned_not_executed",
                }
            )

    return {
        "schema_version": INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION,
        "policy_id": INTERNAL_SEGMENTATION_POLICY_ID,
        "mode": "plan_only",
        "task_fingerprint_sha256": str(task_fingerprint_sha256),
        "source_srt_sha256": str(source_srt_sha256),
        "final_audio_sha256": str(final_audio_sha256),
        "report_sha256": str(report_sha256),
        "lrc_timing_authority": "routing_prior_only_never_split_authority",
        "internal_split_authority": "two_independent_calibrated_direct_final_mix_aligners_required",
        "summary": {
            "candidate_cue_count": candidate_cues,
            "provenance_mismatch_skipped_count": provenance_mismatch_skipped,
            "planned_cue_count": len({int(job["cue_number"]) for job in jobs}),
            "internal_boundary_job_count": len(jobs),
        },
        "jobs": jobs,
    }


def _calibration_uncertainty_floor_ms(artifact: Mapping[str, Any]) -> int:
    summary = artifact.get("summary")
    if not isinstance(summary, Mapping):
        raise InternalSegmentationError("calibration artifact has no summary")
    holdout = summary.get("holdout")
    if not isinstance(holdout, Mapping):
        raise InternalSegmentationError("calibration artifact has no holdout summary")
    try:
        value = float(holdout.get("p90_effective_error_ms"))
    except (TypeError, ValueError) as exc:
        raise InternalSegmentationError("calibration holdout P90 is invalid") from exc
    if value < 0:
        raise InternalSegmentationError("calibration holdout P90 must be nonnegative")
    return int(ceil(value))


def bind_calibration_to_internal_point(
    point: Mapping[str, Any],
    calibration_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach one verified production calibration artifact to a raw backend point."""

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
    language = str(point.get("language") or "").strip()
    audio_basis = str(point.get("audio_basis") or "").strip()
    window_policy_id = str(point.get("window_policy_id") or "").strip()
    if family not in DIRECT_INTERNAL_ALIGNMENT_FAMILIES or audio_basis != "final_mix":
        raise InternalSegmentationError("only direct final-mix alignment points can be calibrated for internal splits")
    if not all((correlation_group, backend_id, backend_version, backend_profile_id, model_id, model_revision, implementation_revision, adapter_contract_revision, lexical_id, lexical_revision, language, window_policy_id)):
        raise InternalSegmentationError("internal alignment point identity is incomplete")
    expected_scope = {
        "boundary_kind": "internal",
        "audio_basis": "final_mix",
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
        raise InternalSegmentationError("calibration artifact does not authorize this internal backend point")
    artifact_sha = str(calibration_artifact.get("artifact_sha256") or "")
    bound = dict(point)
    bound["calibration_passed"] = True
    bound["calibration_sha256"] = artifact_sha
    bound["calibration_artifact"] = dict(calibration_artifact)
    bound["uncertainty_ms"] = max(
        _nonnegative_int(bound.get("uncertainty_ms", 0), label="internal uncertainty_ms"),
        _calibration_uncertainty_floor_ms(calibration_artifact),
    )
    return bound


def build_internal_segmentation_evidence(
    *,
    plan: Mapping[str, Any],
    evidence_by_boundary_id: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Bind exact, calibrated direct-final-mix evidence to internal jobs."""

    if plan.get("schema_version") != INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION:
        raise InternalSegmentationError("unsupported internal segmentation plan schema")
    raw_jobs = plan.get("jobs")
    if not isinstance(raw_jobs, list):
        raise InternalSegmentationError("internal segmentation plan jobs must be a list")
    jobs = {str(job.get("boundary_id") or ""): job for job in raw_jobs if isinstance(job, Mapping)}
    if "" in jobs or len(jobs) != len(raw_jobs):
        raise InternalSegmentationError("internal boundary IDs must be unique/non-empty")
    unknown = sorted(set(evidence_by_boundary_id) - set(jobs))
    if unknown:
        raise InternalSegmentationError("internal evidence references unknown boundary_id")

    calibration_artifacts: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    for boundary_id in sorted(jobs):
        job = jobs[boundary_id]
        normalized: list[dict[str, Any]] = []
        seen_families: set[str] = set()
        for raw in evidence_by_boundary_id.get(boundary_id, ()):
            if not isinstance(raw, Mapping):
                raise InternalSegmentationError("internal evidence point must be an object")
            family = str(raw.get("family") or "").strip()
            correlation_group = str(raw.get("correlation_group") or "").strip()
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
            language = str(raw.get("language") or "").strip()
            audio_basis = str(raw.get("audio_basis") or "").strip()
            window_policy_id = str(raw.get("window_policy_id") or "").strip()
            if family not in DIRECT_INTERNAL_ALIGNMENT_FAMILIES:
                raise InternalSegmentationError("internal segmentation accepts direct alignment families only")
            if family in seen_families:
                raise InternalSegmentationError("internal evidence families must be unique per boundary")
            seen_families.add(family)
            if not all((correlation_group, backend_id, backend_version, backend_profile_id, model_id, model_revision, implementation_revision, adapter_contract_revision, lexical_id, lexical_revision, language, window_policy_id)):
                raise InternalSegmentationError("internal evidence metadata is incomplete")
            if len(lexical_units_sha256) != 64:
                raise InternalSegmentationError("internal evidence lexical-unit SHA is invalid")
            try:
                expected_units = segment_units_from_canonical_segments(
                    job["canonical_segments"], language=language
                )
            except (KeyError, AlignmentLexicalError) as exc:
                raise InternalSegmentationError(
                    "internal job lexical preparation is unavailable for evidence verification"
                ) from exc
            if lexical_units_sha256 != _sha_json(expected_units):
                raise InternalSegmentationError(
                    "internal evidence lexical-unit SHA does not match the canonical job"
                )
            if audio_basis != "final_mix":
                raise InternalSegmentationError("internal segmentation evidence must use final_mix audio")
            boundary_ms = _nonnegative_int(raw.get("boundary_ms"), label="internal boundary_ms")
            uncertainty_ms = _nonnegative_int(raw.get("uncertainty_ms", 0), label="internal uncertainty_ms")
            evidence_sha256 = str(raw.get("evidence_sha256") or "").strip()
            if len(evidence_sha256) != 64:
                raise InternalSegmentationError("calibrated internal evidence must carry an evidence SHA")
            calibration_artifact = raw.get("calibration_artifact")
            claimed_sha = str(raw.get("calibration_sha256") or "").strip()
            if not bool(raw.get("calibration_passed", False)) or not isinstance(calibration_artifact, Mapping):
                raise InternalSegmentationError("internal evidence must carry a passed calibration artifact")
            artifact_sha = str(calibration_artifact.get("artifact_sha256") or "").strip()
            if len(artifact_sha) != 64 or (claimed_sha and claimed_sha != artifact_sha):
                raise InternalSegmentationError("internal calibration SHA is invalid")
            expected_scope = {
                "boundary_kind": "internal",
                "audio_basis": "final_mix",
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
                raise InternalSegmentationError("internal calibration artifact is not authoritative")
            floor_ms = _calibration_uncertainty_floor_ms(calibration_artifact)
            uncertainty_ms = max(uncertainty_ms, floor_ms)
            artifact_dict = dict(calibration_artifact)
            existing = calibration_artifacts.get(artifact_sha)
            if existing is not None and existing != artifact_dict:
                raise InternalSegmentationError("conflicting calibration artifacts share one SHA")
            calibration_artifacts[artifact_sha] = artifact_dict
            normalized.append(
                {
                    "family": family,
                    "correlation_group": correlation_group,
                    "boundary_ms": boundary_ms,
                    "uncertainty_ms": uncertainty_ms,
                    "calibration_uncertainty_floor_ms": floor_ms,
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
                    "language": language,
                    "audio_basis": "final_mix",
                    "window_policy_id": window_policy_id,
                    "evidence_sha256": evidence_sha256,
                    "calibration_passed": True,
                    "calibration_sha256": artifact_sha,
                    "raw_confidence": raw.get("raw_confidence", raw.get("confidence")),
                }
            )
        records.append(
            {
                "boundary_id": boundary_id,
                "cue_number": int(job["cue_number"]),
                "boundary_kind": "internal",
                "boundary_index": int(job["boundary_index"]),
                "points": normalized,
            }
        )

    return {
        "schema_version": INTERNAL_SEGMENTATION_EVIDENCE_SCHEMA_VERSION,
        "policy_id": INTERNAL_SEGMENTATION_POLICY_ID,
        "mode": "evidence_only_no_mutation",
        "task_fingerprint_sha256": str(plan["task_fingerprint_sha256"]),
        "source_srt_sha256": str(plan["source_srt_sha256"]),
        "final_audio_sha256": str(plan["final_audio_sha256"]),
        "report_sha256": str(plan["report_sha256"]),
        "plan_sha256": _sha_json(plan),
        "calibration_artifacts": dict(sorted(calibration_artifacts.items())),
        "records": records,
    }


def _selective_calibration_uncertainty_floor_ms(artifact: Mapping[str, Any]) -> int:
    summary = artifact.get("summary")
    if not isinstance(summary, Mapping) or not isinstance(summary.get("holdout"), Mapping):
        raise InternalSegmentationError("selective consensus calibration has no holdout summary")
    try:
        p90_frames = float(summary["holdout"].get("p90_effective_error_frames"))
    except (TypeError, ValueError) as exc:
        raise InternalSegmentationError("selective consensus holdout P90 is invalid") from exc
    if p90_frames < 0:
        raise InternalSegmentationError("selective consensus holdout P90 must be nonnegative")
    return int(ceil(p90_frames * 1000.0 / 30.0))


def _selective_source_map(artifact: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    sources = artifact.get("backend_sources")
    if not isinstance(sources, list) or len(sources) != 2 or any(not isinstance(row, Mapping) for row in sources):
        raise InternalSegmentationError("selective consensus calibration backend sources are invalid")
    by_profile = {str(row.get("backend_profile_id") or ""): row for row in sources}
    if "" in by_profile or len(by_profile) != 2:
        raise InternalSegmentationError("selective consensus calibration backend profile IDs are invalid")
    return by_profile


def _validate_selective_raw_point(
    raw: Mapping[str, Any],
    *,
    job: Mapping[str, Any],
    source_by_profile: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    family = str(raw.get("family") or "").strip()
    correlation_group = str(raw.get("correlation_group") or "").strip()
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
    language = str(raw.get("language") or "").strip()
    audio_basis = str(raw.get("audio_basis") or "").strip()
    window_policy_id = str(raw.get("window_policy_id") or "").strip()
    if family not in DIRECT_INTERNAL_ALIGNMENT_FAMILIES or audio_basis != "final_mix":
        raise InternalSegmentationError("selective internal evidence accepts direct final-mix alignment families only")
    if not all((correlation_group, backend_id, backend_version, backend_profile_id, model_id, model_revision, implementation_revision, adapter_contract_revision, lexical_id, lexical_revision, language, window_policy_id)):
        raise InternalSegmentationError("selective internal evidence metadata is incomplete")
    source = source_by_profile.get(backend_profile_id)
    if source is None:
        raise InternalSegmentationError("selective internal evidence backend profile is not calibrated by the joint artifact")
    for key, actual in (
        ("family", family),
        ("correlation_group", correlation_group),
        ("backend_id", backend_id),
        ("backend_version", backend_version),
        ("model_id", model_id),
        ("model_revision", model_revision),
        ("implementation_revision", implementation_revision),
        ("adapter_contract_revision", adapter_contract_revision),
        ("window_policy_id", window_policy_id),
        ("lexical_id", lexical_id),
        ("lexical_revision", lexical_revision),
        ("language", language),
        ("audio_basis", audio_basis),
    ):
        if str(source.get(key) or "") != actual:
            raise InternalSegmentationError(f"selective internal evidence {key} differs from joint calibration source")
    try:
        expected_units = segment_units_from_canonical_segments(job["canonical_segments"], language=language)
    except (KeyError, AlignmentLexicalError) as exc:
        raise InternalSegmentationError("internal job lexical preparation is unavailable for selective evidence") from exc
    if len(lexical_units_sha256) != 64 or lexical_units_sha256 != _sha_json(expected_units):
        raise InternalSegmentationError("selective internal evidence lexical-unit SHA does not match canonical job")
    evidence_sha256 = str(raw.get("evidence_sha256") or "").strip()
    if len(evidence_sha256) != 64:
        raise InternalSegmentationError("selective internal evidence must carry an evidence SHA")
    if bool(raw.get("calibration_passed", False)) or str(raw.get("calibration_sha256") or "").strip() or raw.get("calibration_artifact") is not None:
        raise InternalSegmentationError("selective raw evidence must not claim individual backend calibration")
    return {
        "family": family,
        "correlation_group": correlation_group,
        "boundary_ms": _nonnegative_int(raw.get("boundary_ms"), label="selective internal boundary_ms"),
        "uncertainty_ms": _nonnegative_int(raw.get("uncertainty_ms", 0), label="selective internal uncertainty_ms"),
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
        "language": language,
        "audio_basis": "final_mix",
        "window_policy_id": window_policy_id,
        "evidence_sha256": evidence_sha256,
        "calibration_passed": False,
        "calibration_sha256": "",
        "raw_confidence": raw.get("raw_confidence", raw.get("confidence")),
    }


def build_internal_selective_consensus_evidence(
    *,
    plan: Mapping[str, Any],
    evidence_by_boundary_id: Mapping[str, Sequence[Mapping[str, Any]]],
    selective_consensus_calibration: Mapping[str, Any],
    source_calibrations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind raw direct-aligner points to one authoritative joint selective rule."""

    if plan.get("schema_version") != INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION:
        raise InternalSegmentationError("unsupported internal segmentation plan schema")
    if not internal_selective_consensus_calibration_is_authoritative(
        selective_consensus_calibration,
        expected_final_audio_sha256=str(plan.get("final_audio_sha256") or ""),
        source_calibrations=source_calibrations,
    ):
        raise InternalSegmentationError("selective consensus calibration is not authoritative for this final mix")
    raw_jobs = plan.get("jobs")
    if not isinstance(raw_jobs, list):
        raise InternalSegmentationError("internal segmentation plan jobs must be a list")
    jobs = {str(job.get("boundary_id") or ""): job for job in raw_jobs if isinstance(job, Mapping)}
    if "" in jobs or len(jobs) != len(raw_jobs):
        raise InternalSegmentationError("internal boundary IDs must be unique/non-empty")
    unknown = sorted(set(evidence_by_boundary_id) - set(jobs))
    if unknown:
        raise InternalSegmentationError("selective internal evidence references unknown boundary_id")

    calibration_sha = str(selective_consensus_calibration.get("artifact_sha256") or "")
    source_by_profile = _selective_source_map(selective_consensus_calibration)
    source_catalog: dict[str, dict[str, Any]] = {}
    for artifact in source_calibrations:
        claimed = str(artifact.get("artifact_sha256") or "").strip()
        if len(claimed) != 64:
            raise InternalSegmentationError("selective source calibration artifact SHA is invalid")
        source_catalog[claimed] = dict(artifact)
    expected_source_shas = {str(row.get("source_calibration_artifact_sha256") or "") for row in source_by_profile.values()}
    if set(source_catalog) != expected_source_shas:
        raise InternalSegmentationError("selective source calibration catalog differs from joint calibration")

    records: list[dict[str, Any]] = []
    for boundary_id in sorted(jobs):
        job = jobs[boundary_id]
        normalized: list[dict[str, Any]] = []
        seen_profiles: set[str] = set()
        seen_families: set[str] = set()
        for raw in evidence_by_boundary_id.get(boundary_id, ()):
            if not isinstance(raw, Mapping):
                raise InternalSegmentationError("selective internal evidence point must be an object")
            point = _validate_selective_raw_point(raw, job=job, source_by_profile=source_by_profile)
            profile_id = str(point["backend_profile_id"])
            family = str(point["family"])
            if profile_id in seen_profiles or family in seen_families:
                raise InternalSegmentationError("selective internal evidence duplicates backend profile/family")
            seen_profiles.add(profile_id)
            seen_families.add(family)
            normalized.append(point)
        records.append(
            {
                "boundary_id": boundary_id,
                "cue_number": int(job["cue_number"]),
                "boundary_kind": "internal",
                "boundary_index": int(job["boundary_index"]),
                "points": normalized,
            }
        )
    return {
        "schema_version": INTERNAL_SEGMENTATION_SELECTIVE_EVIDENCE_SCHEMA_VERSION,
        "policy_id": INTERNAL_SEGMENTATION_POLICY_ID,
        "mode": "selective_consensus_evidence_only_no_mutation",
        "task_fingerprint_sha256": str(plan["task_fingerprint_sha256"]),
        "source_srt_sha256": str(plan["source_srt_sha256"]),
        "final_audio_sha256": str(plan["final_audio_sha256"]),
        "report_sha256": str(plan["report_sha256"]),
        "plan_sha256": _sha_json(plan),
        "selective_consensus_calibration_sha256": calibration_sha,
        "selective_consensus_calibration_artifact": dict(selective_consensus_calibration),
        "source_calibration_artifacts": dict(sorted(source_catalog.items())),
        "records": records,
    }


def _adjudicate_internal_selective_consensus(
    *,
    plan: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    raw_jobs = plan.get("jobs")
    raw_records = evidence.get("records")
    calibration = evidence.get("selective_consensus_calibration_artifact")
    source_catalog = evidence.get("source_calibration_artifacts")
    if not isinstance(raw_jobs, list) or not isinstance(raw_records, list) or not isinstance(calibration, Mapping) or not isinstance(source_catalog, Mapping):
        raise InternalSegmentationError("selective internal segmentation artifacts are malformed")
    claimed_sha = str(evidence.get("selective_consensus_calibration_sha256") or "").strip()
    if claimed_sha != str(calibration.get("artifact_sha256") or ""):
        raise InternalSegmentationError("selective consensus calibration SHA does not match evidence")
    sources = _selective_source_map(calibration)
    expected_source_shas = {str(row.get("source_calibration_artifact_sha256") or "") for row in sources.values()}
    if set(source_catalog) != expected_source_shas or any(not isinstance(value, Mapping) for value in source_catalog.values()):
        raise InternalSegmentationError("selective source calibration catalog is incomplete")
    source_calibrations = [source_catalog[sha] for sha in sorted(source_catalog)]
    if not internal_selective_consensus_calibration_is_authoritative(
        calibration,
        expected_final_audio_sha256=str(plan.get("final_audio_sha256") or ""),
        source_calibrations=source_calibrations,
    ):
        raise InternalSegmentationError("selective consensus calibration is no longer authoritative")
    uncertainty_floor_ms = _selective_calibration_uncertainty_floor_ms(calibration)
    if uncertainty_floor_ms > MAX_INTERNAL_EVIDENCE_UNCERTAINTY_MS:
        raise InternalSegmentationError("joint selector calibration uncertainty exceeds internal policy")

    job_ids = [str(row.get("boundary_id") or "").strip() for row in raw_jobs if isinstance(row, Mapping)]
    record_ids = [str(row.get("boundary_id") or "").strip() for row in raw_records if isinstance(row, Mapping)]
    if len(job_ids) != len(raw_jobs) or len(record_ids) != len(raw_records) or any(not value for value in job_ids + record_ids):
        raise InternalSegmentationError("selective internal job/evidence IDs are invalid")
    if len(set(job_ids)) != len(job_ids) or len(set(record_ids)) != len(record_ids):
        raise InternalSegmentationError("selective internal job/evidence IDs are duplicated")
    jobs = dict(zip(job_ids, raw_jobs))
    records = dict(zip(record_ids, raw_records))
    if set(jobs) != set(records):
        raise InternalSegmentationError("selective internal evidence record set does not match plan")

    decisions: list[dict[str, Any]] = []
    for boundary_id in sorted(jobs):
        job = jobs[boundary_id]
        record = records[boundary_id]
        if int(record.get("cue_number", -1)) != int(job.get("cue_number", -2)) or str(record.get("boundary_kind") or "") != "internal" or int(record.get("boundary_index", -1)) != int(job.get("boundary_index", -2)):
            raise InternalSegmentationError("selective internal evidence identity does not match plan job")
        raw_points = record.get("points")
        if not isinstance(raw_points, list) or any(not isinstance(point, Mapping) for point in raw_points):
            raise InternalSegmentationError("selective internal evidence points are malformed")
        points = [_validate_selective_raw_point(point, job=job, source_by_profile=sources) for point in raw_points]
        eligible = [point for point in points if int(point["uncertainty_ms"]) <= MAX_INTERNAL_EVIDENCE_UNCERTAINTY_MS]
        families = {str(point["family"]) for point in eligible}
        groups = {str(point["correlation_group"]) for point in eligible}
        backend_ids = {str(point["backend_id"]) for point in eligible}
        profiles = {str(point["backend_profile_id"]) for point in eligible}
        values = [int(point["boundary_ms"]) for point in eligible]
        spread = max(values) - min(values) if len(values) == 2 else None
        row_start, row_end = (int(value) for value in job["row_interval_ms"])
        language_values = {str(point["language"]) for point in eligible}
        lexical_prior_ms: int | None = None
        selected_profile_id: str | None = None
        selector_reason = "requires_two_direct_aligners"
        selected: int | None = None
        if len(eligible) == 2 and len(language_values) == 1:
            language = next(iter(language_values))
            try:
                units = segment_units_from_canonical_segments(
                    job["canonical_segments"],
                    language=language,
                )
            except (KeyError, AlignmentLexicalError) as exc:
                raise InternalSegmentationError(
                    "joint selector runtime lexical prior cannot be reconstructed"
                ) from exc
            boundary_index = int(job["boundary_index"])
            left_units = sum(len(segment) for segment in units[:boundary_index])
            total_units = sum(len(segment) for segment in units)
            if not 0 < left_units < total_units:
                raise InternalSegmentationError("joint selector runtime lexical ratio is invalid")
            lexical_prior_ms = int(
                round(row_start + (row_end - row_start) * left_units / total_units)
            )
            predictions = {
                str(point["backend_profile_id"]): int(point["boundary_ms"])
                for point in eligible
            }
            selected, selected_profile_id, selector_reason = select_nearest_lexical_prior_prediction(
                predictions,
                lexical_prior_ms=lexical_prior_ms,
            )
        selected_for_decision = int(job["routing_prior_ms"]) if selected is None else int(selected)
        safe_position = (
            selected is not None
            and row_start + MIN_MATERIALIZED_SEGMENT_MS
            <= selected_for_decision
            <= row_end - MIN_MATERIALIZED_SEGMENT_MS
        )
        risks = list(job.get("topology_risk_flags") or [])
        authorized = bool(
            len(eligible) == 2
            and len(families) == 2
            and DIRECT_INTERNAL_ALIGNMENT_FAMILIES.issubset(families)
            and len(groups) == 2
            and len(backend_ids) == 2
            and len(profiles) == 2
            and len(language_values) == 1
            and selected is not None
            and safe_position
            and not risks
        )
        if authorized:
            action = "auto_internal_split"
            reason = (
                "pre-frozen human-gold calibrated joint selector chose the direct aligner "
                "nearest the independently recomputed lexical-ratio prior"
            )
        else:
            action = "keep_unsplit"
            reason_parts: list[str] = []
            if len(eligible) != 2 or len(families) != 2 or not DIRECT_INTERNAL_ALIGNMENT_FAMILIES.issubset(families):
                reason_parts.append("two joint-selector direct alignment families are not eligible")
            if len(groups) < 2 or len(backend_ids) < 2 or len(profiles) < 2:
                reason_parts.append("joint-selector alignment evidence is not independent")
            if len(language_values) != 1:
                reason_parts.append("joint-selector evidence language scope is inconsistent")
            if selected is None:
                reason_parts.append("frozen joint selector could not select a direct prediction")
            if selected is not None and not safe_position:
                reason_parts.append("joint-selector candidate would create an edge-short segment")
            if risks:
                reason_parts.append("cue has overlap/crossfade topology risk")
            if not reason_parts:
                reason_parts.append("insufficient evidence for joint lexical selector authority")
            reason = "; ".join(reason_parts)
        decisions.append(
            {
                "boundary_id": boundary_id,
                "cue_number": int(job["cue_number"]),
                "boundary_kind": "internal",
                "boundary_index": int(job["boundary_index"]),
                "selected_ms": selected_for_decision,
                "action": action,
                "automatic_mutation_allowed": authorized,
                "audio_family_count": len(families),
                "correlation_group_count": len(groups),
                "backend_count": len(backend_ids),
                "audio_spread_ms": spread,
                "lexical_ratio_prior_ms": lexical_prior_ms,
                "selected_backend_profile_id": selected_profile_id,
                "selector_reason": selector_reason,
                "evidence_basis": sorted(families),
                "authority_mode": "human_gold_joint_lexical_selector_v1",
                "selective_consensus_calibration_sha256": claimed_sha,
                "selective_consensus_uncertainty_floor_ms": uncertainty_floor_ms,
                "reason": reason,
            }
        )
    return {
        "schema_version": INTERNAL_SEGMENTATION_DECISION_SCHEMA_VERSION,
        "policy_id": INTERNAL_SEGMENTATION_POLICY_ID,
        "mode": "decision_only",
        "task_fingerprint_sha256": str(plan["task_fingerprint_sha256"]),
        "source_srt_sha256": str(plan["source_srt_sha256"]),
        "final_audio_sha256": str(plan["final_audio_sha256"]),
        "report_sha256": str(plan["report_sha256"]),
        "plan_sha256": _sha_json(plan),
        "evidence_sha256": _sha_json(evidence),
        "summary": {
            "internal_boundary_count": len(decisions),
            "automatic_split_boundary_count": sum(bool(row["automatic_mutation_allowed"]) for row in decisions),
            "authority_mode": "human_gold_joint_lexical_selector_v1",
        },
        "decisions": decisions,
    }


def _verify_point_against_catalog(
    point: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> dict[str, Any]:
    claimed_sha = str(point.get("calibration_sha256") or "").strip()
    artifact = catalog.get(claimed_sha)
    if not isinstance(artifact, Mapping):
        raise InternalSegmentationError("internal evidence calibration is missing from catalog")
    if len(str(point.get("lexical_units_sha256") or "")) != 64:
        raise InternalSegmentationError("internal evidence lexical-unit SHA is invalid")
    if len(str(point.get("evidence_sha256") or "")) != 64:
        raise InternalSegmentationError("internal evidence SHA is invalid")
    expected_scope = {
        "boundary_kind": "internal",
        "audio_basis": "final_mix",
        "language": str(point.get("language") or ""),
        "backend_version": str(point.get("backend_version") or ""),
        "backend_profile_id": str(point.get("backend_profile_id") or ""),
        "window_policy_id": str(point.get("window_policy_id") or ""),
        "model_id": str(point.get("model_id") or ""),
        "model_revision": str(point.get("model_revision") or ""),
        "implementation_revision": str(point.get("implementation_revision") or ""),
        "adapter_contract_revision": str(point.get("adapter_contract_revision") or ""),
        "lexical_id": str(point.get("lexical_id") or ""),
        "lexical_revision": str(point.get("lexical_revision") or ""),
    }
    if str(artifact.get("artifact_sha256") or "") != claimed_sha or not production_calibration_artifact_is_authoritative(
        artifact,
        backend_id=str(point.get("backend_id") or ""),
        family=str(point.get("family") or ""),
        correlation_group=str(point.get("correlation_group") or ""),
        expected_scope=expected_scope,
    ):
        raise InternalSegmentationError("internal evidence contains an unverifiable calibration claim")
    sanitized = dict(point)
    floor_ms = _calibration_uncertainty_floor_ms(artifact)
    sanitized["uncertainty_ms"] = max(
        _nonnegative_int(point.get("uncertainty_ms", 0), label="internal uncertainty_ms"),
        floor_ms,
    )
    sanitized["calibration_uncertainty_floor_ms"] = floor_ms
    return sanitized


def adjudicate_internal_segmentation(
    *,
    plan: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Authorize an internal split only from two calibrated direct aligners."""

    if plan.get("schema_version") != INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION:
        raise InternalSegmentationError("unsupported internal segmentation plan schema")
    evidence_schema = str(evidence.get("schema_version") or "")
    if evidence_schema not in {
        INTERNAL_SEGMENTATION_EVIDENCE_SCHEMA_VERSION,
        INTERNAL_SEGMENTATION_SELECTIVE_EVIDENCE_SCHEMA_VERSION,
    }:
        raise InternalSegmentationError("unsupported internal segmentation evidence schema")
    if str(evidence.get("plan_sha256") or "") != _sha_json(plan):
        raise InternalSegmentationError("internal evidence belongs to another plan")
    for key in ("task_fingerprint_sha256", "source_srt_sha256", "final_audio_sha256", "report_sha256"):
        if str(evidence.get(key) or "") != str(plan.get(key) or ""):
            raise InternalSegmentationError(f"internal evidence {key} mismatch")
    if evidence_schema == INTERNAL_SEGMENTATION_SELECTIVE_EVIDENCE_SCHEMA_VERSION:
        return _adjudicate_internal_selective_consensus(plan=plan, evidence=evidence)

    raw_jobs = plan.get("jobs")
    raw_records = evidence.get("records")
    catalog = evidence.get("calibration_artifacts")
    if not isinstance(raw_jobs, list) or not isinstance(raw_records, list) or not isinstance(catalog, Mapping):
        raise InternalSegmentationError("internal segmentation artifacts are malformed")
    if any(not isinstance(row, Mapping) for row in raw_jobs):
        raise InternalSegmentationError("internal segmentation job must be an object")
    if any(not isinstance(row, Mapping) for row in raw_records):
        raise InternalSegmentationError("internal evidence record must be an object")
    job_ids = [str(row.get("boundary_id") or "").strip() for row in raw_jobs]
    record_ids = [str(row.get("boundary_id") or "").strip() for row in raw_records]
    if any(not value for value in job_ids) or len(set(job_ids)) != len(job_ids):
        raise InternalSegmentationError("internal plan boundary IDs must be unique/non-empty")
    if any(not value for value in record_ids) or len(set(record_ids)) != len(record_ids):
        raise InternalSegmentationError("internal evidence boundary IDs must be unique/non-empty")
    jobs = dict(zip(job_ids, raw_jobs))
    records = dict(zip(record_ids, raw_records))
    if set(jobs) != set(records):
        raise InternalSegmentationError("internal evidence record set does not match plan")

    decisions: list[dict[str, Any]] = []
    for boundary_id in sorted(jobs):
        job = jobs[boundary_id]
        record = records[boundary_id]
        if (
            int(record.get("cue_number", -1)) != int(job.get("cue_number", -2))
            or str(record.get("boundary_kind") or "") != "internal"
            or int(record.get("boundary_index", -1)) != int(job.get("boundary_index", -2))
        ):
            raise InternalSegmentationError("internal evidence record identity does not match plan job")
        points_raw = record.get("points")
        if not isinstance(points_raw, list):
            raise InternalSegmentationError("internal evidence points must be a list")
        if any(not isinstance(point, Mapping) for point in points_raw):
            raise InternalSegmentationError("internal evidence point must be an object")
        points = [_verify_point_against_catalog(point, catalog) for point in points_raw]
        for point in points:
            try:
                expected_units = segment_units_from_canonical_segments(
                    job["canonical_segments"], language=str(point.get("language") or "")
                )
            except (KeyError, AlignmentLexicalError) as exc:
                raise InternalSegmentationError(
                    "internal job lexical preparation is unavailable during adjudication"
                ) from exc
            if str(point.get("lexical_units_sha256") or "") != _sha_json(expected_units):
                raise InternalSegmentationError(
                    "internal evidence lexical-unit SHA does not match the canonical job"
                )
        eligible = [
            point
            for point in points
            if str(point.get("family") or "") in DIRECT_INTERNAL_ALIGNMENT_FAMILIES
            and str(point.get("audio_basis") or "") == "final_mix"
            and int(point["uncertainty_ms"]) <= MAX_INTERNAL_EVIDENCE_UNCERTAINTY_MS
        ]
        eligible_families = {str(point["family"]) for point in eligible}
        eligible_groups = {str(point["correlation_group"]) for point in eligible}
        eligible_backend_ids = {str(point["backend_id"]) for point in eligible if str(point.get("backend_id") or "")}
        values = [int(point["boundary_ms"]) for point in eligible]
        spread = max(values) - min(values) if values else None
        row_start, row_end = (int(value) for value in job["row_interval_ms"])
        selected = int(round(float(median(values)))) if values else int(job["routing_prior_ms"])
        risks = list(job.get("topology_risk_flags") or [])
        safe_position = (
            row_start + MIN_MATERIALIZED_SEGMENT_MS
            <= selected
            <= row_end - MIN_MATERIALIZED_SEGMENT_MS
        )
        authorized = bool(
            len(eligible_families) >= 2
            and len(eligible_groups) >= 2
            and len(eligible_backend_ids) >= 2
            and DIRECT_INTERNAL_ALIGNMENT_FAMILIES.issubset(eligible_families)
            and spread is not None
            and spread <= MAX_INTERNAL_ALIGNMENT_SPREAD_MS
            and safe_position
            and not risks
        )
        if authorized:
            action = "auto_internal_split"
            reason = "two independently calibrated direct final-mix aligners agree within policy"
        else:
            action = "keep_unsplit"
            reason_parts: list[str] = []
            if len(eligible_families) < 2 or not DIRECT_INTERNAL_ALIGNMENT_FAMILIES.issubset(eligible_families):
                reason_parts.append("two required direct alignment families are not eligible")
            if len(eligible_groups) < 2:
                reason_parts.append("alignment evidence correlation groups are not independent")
            if len(eligible_backend_ids) < 2:
                reason_parts.append("alignment evidence does not come from two distinct backends")
            if spread is not None and spread > MAX_INTERNAL_ALIGNMENT_SPREAD_MS:
                reason_parts.append("direct aligners disagree beyond spread policy")
            if not safe_position:
                reason_parts.append("candidate would create an edge-short segment")
            if risks:
                reason_parts.append("cue has overlap/crossfade topology risk")
            if not reason_parts:
                reason_parts.append("insufficient calibrated direct final-mix evidence")
            reason = "; ".join(reason_parts)
        decisions.append(
            {
                "boundary_id": boundary_id,
                "cue_number": int(job["cue_number"]),
                "boundary_kind": "internal",
                "boundary_index": int(job["boundary_index"]),
                "selected_ms": selected,
                "action": action,
                "automatic_mutation_allowed": authorized,
                "audio_family_count": len(eligible_families),
                "correlation_group_count": len(eligible_groups),
                "backend_count": len(eligible_backend_ids),
                "audio_spread_ms": spread,
                "evidence_basis": sorted(eligible_families),
                "reason": reason,
            }
        )

    return {
        "schema_version": INTERNAL_SEGMENTATION_DECISION_SCHEMA_VERSION,
        "policy_id": INTERNAL_SEGMENTATION_POLICY_ID,
        "mode": "decision_only",
        "task_fingerprint_sha256": str(plan["task_fingerprint_sha256"]),
        "source_srt_sha256": str(plan["source_srt_sha256"]),
        "final_audio_sha256": str(plan["final_audio_sha256"]),
        "report_sha256": str(plan["report_sha256"]),
        "plan_sha256": _sha_json(plan),
        "evidence_sha256": _sha_json(evidence),
        "summary": {
            "internal_boundary_count": len(decisions),
            "automatic_split_boundary_count": sum(bool(row["automatic_mutation_allowed"]) for row in decisions),
        },
        "decisions": decisions,
    }


def apply_internal_segmentation_to_rows(
    *,
    report_rows: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
    evidence: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Materialize only cues whose complete internal-boundary set is authorized.

    Decisions are not trusted as standalone mutation authority.  The exact
    evidence artifact is re-adjudicated here and the caller-supplied decisions
    must match the deterministic result byte-for-byte at the data level.
    """

    if plan.get("schema_version") != INTERNAL_SEGMENTATION_PLAN_SCHEMA_VERSION:
        raise InternalSegmentationError("unsupported internal segmentation plan schema")
    if decisions.get("schema_version") != INTERNAL_SEGMENTATION_DECISION_SCHEMA_VERSION:
        raise InternalSegmentationError("unsupported internal segmentation decision schema")
    if str(decisions.get("plan_sha256") or "") != _sha_json(plan):
        raise InternalSegmentationError("internal decisions belong to another plan")
    recomputed = adjudicate_internal_segmentation(plan=plan, evidence=evidence)
    if dict(decisions) != recomputed:
        raise InternalSegmentationError(
            "internal decisions do not match fresh adjudication of the bound evidence"
        )

    raw_jobs = plan.get("jobs")
    raw_decisions = decisions.get("decisions")
    if not isinstance(raw_jobs, list) or not isinstance(raw_decisions, list):
        raise InternalSegmentationError("internal segmentation artifacts are malformed")
    if any(not isinstance(job, Mapping) for job in raw_jobs):
        raise InternalSegmentationError("internal segmentation job must be an object")
    if any(not isinstance(row, Mapping) for row in raw_decisions):
        raise InternalSegmentationError("internal segmentation decision must be an object")
    job_ids = [str(job.get("boundary_id") or "").strip() for job in raw_jobs]
    decision_ids = [str(row.get("boundary_id") or "").strip() for row in raw_decisions]
    if any(not value for value in job_ids) or len(set(job_ids)) != len(job_ids):
        raise InternalSegmentationError("internal plan boundary IDs must be unique/non-empty")
    if any(not value for value in decision_ids) or len(set(decision_ids)) != len(decision_ids):
        raise InternalSegmentationError("internal decision boundary IDs must be unique/non-empty")
    jobs_by_id = dict(zip(job_ids, raw_jobs))
    decisions_by_id = dict(zip(decision_ids, raw_decisions))
    if set(decisions_by_id) != set(jobs_by_id):
        raise InternalSegmentationError("internal decision set does not match plan")
    jobs_by_cue: dict[int, list[Mapping[str, Any]]] = {}
    for boundary_id, job in jobs_by_id.items():
        if str(job.get("boundary_kind") or "") != "internal":
            raise InternalSegmentationError("internal segmentation job has wrong boundary_kind")
        decision = decisions_by_id[boundary_id]
        if (
            int(decision.get("cue_number", -1)) != int(job.get("cue_number", -2))
            or str(decision.get("boundary_kind") or "") != "internal"
            or int(decision.get("boundary_index", -1)) != int(job.get("boundary_index", -2))
        ):
            raise InternalSegmentationError("internal decision identity does not match plan job")
        jobs_by_cue.setdefault(int(job["cue_number"]), []).append(job)

    output: list[dict[str, Any]] = []
    rows_by_cue: dict[int, list[Mapping[str, Any]]] = {}
    passthrough: list[Mapping[str, Any]] = []
    for row in report_rows:
        raw_number = str(row.get("original_cue") or "").strip()
        if raw_number.isdigit():
            rows_by_cue.setdefault(int(raw_number), []).append(row)
        else:
            passthrough.append(row)

    handled: set[int] = set()
    for row in report_rows:
        raw_number = str(row.get("original_cue") or "").strip()
        if not raw_number.isdigit():
            output.append(dict(row))
            continue
        number = int(raw_number)
        if number in handled:
            continue
        handled.add(number)
        cue_rows = rows_by_cue[number]
        cue_jobs = sorted(jobs_by_cue.get(number, []), key=lambda item: int(item["boundary_index"]))
        if not cue_jobs or len(cue_rows) != 1:
            output.extend(dict(item) for item in cue_rows)
            continue
        source_row = cue_rows[0]
        row_fingerprint = _row_fingerprint(source_row)
        if any(str(job.get("row_fingerprint_sha256") or "") != row_fingerprint for job in cue_jobs):
            raise InternalSegmentationError(f"cue {number} changed after internal segmentation planning")
        segments = _parse_segments(source_row)
        if len(cue_jobs) != len(segments) - 1:
            raise InternalSegmentationError("internal job count does not match canonical segments")
        expected_indices = list(range(1, len(segments)))
        actual_indices = [int(job.get("boundary_index", -1)) for job in cue_jobs]
        if actual_indices != expected_indices:
            raise InternalSegmentationError("internal job indices do not exactly cover canonical segment boundaries")
        for job, boundary_index in zip(cue_jobs, expected_indices):
            left_sha = str((job.get("left_segment") or {}).get("segment_sha256") or "")
            right_sha = str((job.get("right_segment") or {}).get("segment_sha256") or "")
            if (
                left_sha != str(segments[boundary_index - 1]["segment_sha256"])
                or right_sha != str(segments[boundary_index]["segment_sha256"])
                or list(job.get("row_interval_ms") or [])
                != [int(source_row["start_ms"]), int(source_row["end_ms"])]
            ):
                raise InternalSegmentationError("internal job topology does not match canonical segments")
        cue_decisions = [decisions_by_id[str(job["boundary_id"])] for job in cue_jobs]
        if not all(bool(decision.get("automatic_mutation_allowed")) and decision.get("action") == "auto_internal_split" for decision in cue_decisions):
            output.append(dict(source_row))
            continue

        boundaries = [
            _nonnegative_int(decision.get("selected_ms"), label="selected internal boundary")
            for decision in cue_decisions
        ]
        start = _nonnegative_int(source_row.get("start_ms"), label="row start_ms")
        end = _nonnegative_int(source_row.get("end_ms"), label="row end_ms")
        edges = [start, *boundaries, end]
        if edges != sorted(edges) or len(set(edges)) != len(edges):
            raise InternalSegmentationError("authorized internal boundaries are not strictly monotonic")
        durations = [right - left for left, right in zip(edges, edges[1:])]
        if any(duration < MIN_MATERIALIZED_SEGMENT_MS for duration in durations):
            raise InternalSegmentationError("authorized internal split creates an implausibly short segment")

        authority_details = ";".join(str(job["boundary_id"]) for job in cue_jobs)
        authority_mode = str((decisions.get("summary") or {}).get("authority_mode") or "")
        selective_authority = authority_mode == "human_gold_joint_lexical_selector_v1"
        for index, segment in enumerate(segments):
            child = dict(source_row)
            child["kind"] = "split"
            child["start_ms"] = edges[index]
            child["end_ms"] = edges[index + 1]
            child["text"] = str(segment["text"])
            child["lrc_indices"] = str(segment["lrc_index"])
            child["canonical_segments_json"] = json.dumps(
                [
                    {
                        "track_index": int(segment["track_index"]),
                        "lrc_index": int(segment["lrc_index"]),
                        "text": str(segment["text"]),
                        "projected_ms": int(segment["projected_ms"]),
                    }
                ],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            child["status"] = "audio_verified_internal_split"
            child["confidence"] = "high"
            child["segmentation_authority"] = (
                "audio_verified_internal_joint_lexical_selector_v1"
                if selective_authority
                else "audio_verified_internal_segmentation_v1"
            )
            child["internal_boundary_authority_details"] = authority_details
            child["split_part"] = index + 1
            child["split_part_count"] = len(segments)
            child["evidence"] = str(child.get("evidence") or "") + (
                "+audio_verified_internal_joint_lexical_selector_v1"
                if selective_authority
                else "+audio_verified_internal_segmentation_v1"
            )
            output.append(child)

    return output
