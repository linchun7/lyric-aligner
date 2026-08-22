"""Region-reusing local acoustic executor for Pro v1.2.

Nearby Smart review cues share one decoded/featured mix region.  Each cue still
keeps its own source window and local retrieval query, so batching reduces work
without collapsing cue identity or widening timing authority.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Mapping

import librosa
import numpy as np

from lyric_aligner.alignment.local_acoustic_match import (
    LocalAcousticMatchConfig,
    LocalAcousticMatchError,
)
from lyric_aligner.audio.features import extract_harmonic_features, retrieve_coarse_window

LOCAL_ACOUSTIC_V11_SCHEMA_VERSION = "1.4"


def _window(row: Mapping[str, Any], key: str) -> tuple[int, int]:
    raw = row.get(key)
    if not isinstance(raw, list) or len(raw) != 2:
        raise LocalAcousticMatchError(f"job has no valid {key}")
    try:
        start, end = int(raw[0]), int(raw[1])
    except (TypeError, ValueError) as exc:
        raise LocalAcousticMatchError(f"job {key} is invalid") from exc
    if start < 0 or end <= start:
        raise LocalAcousticMatchError(f"job {key} is invalid")
    return start, end


def _rate(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0.5 <= number <= 2.0:
        return None
    return number


def _slopes(rate_prior: float | None, config: LocalAcousticMatchConfig) -> list[float]:
    if rate_prior is None:
        minimum = config.no_prior_min_slope
        maximum = config.no_prior_max_slope
        step = config.no_prior_step
    else:
        minimum = max(0.45, rate_prior - config.slope_radius)
        maximum = min(2.2, rate_prior + config.slope_radius)
        step = config.slope_step
    values: list[float] = []
    current = minimum
    while current <= maximum + step / 2:
        values.append(round(current, 6))
        current += step
    if rate_prior is not None:
        values.append(round(rate_prior, 6))
    return sorted(set(values))


def _slope_search_metadata(
    slopes: list[float],
    estimated_slope: float,
    *,
    step: float,
) -> tuple[float, float, bool]:
    """Describe the bounded search and reject endpoint optima as authority.

    A best candidate at (or within half a grid step of) either endpoint only
    proves that bounded retrieval found a local candidate.  It does not prove
    that the true rate optimum lies inside the searched interval.
    """

    if not slopes:
        raise LocalAcousticMatchError("slope search produced no candidates")
    minimum = float(min(slopes))
    maximum = float(max(slopes))
    boundary_margin = max(float(step) / 2.0, 1e-6)
    boundary_hit = (
        float(estimated_slope) <= minimum + boundary_margin
        or float(estimated_slope) >= maximum - boundary_margin
    )
    return minimum, maximum, boundary_hit


def _source_search_metadata(
    *,
    source_window_start_ms: int,
    source_duration_seconds: float,
    query_duration_seconds: float,
    estimated_slope: float,
    matched_source_start_seconds: float,
    boundary_margin_seconds: float,
) -> tuple[int, int, bool]:
    """Describe the valid local source-start interval for the winning slope."""

    relative_min = 0.0
    relative_max = max(
        0.0,
        float(source_duration_seconds)
        - float(query_duration_seconds) * float(estimated_slope),
    )
    boundary_hit = (
        float(matched_source_start_seconds)
        <= relative_min + float(boundary_margin_seconds)
        or float(matched_source_start_seconds)
        >= relative_max - float(boundary_margin_seconds)
    )
    return (
        source_window_start_ms + int(round(relative_min * 1000.0)),
        source_window_start_ms + int(round(relative_max * 1000.0)),
        boundary_hit,
    )


def _default_loader(path: Path, *, sr: int, start_ms: int, end_ms: int) -> np.ndarray:
    try:
        audio, _ = librosa.load(
            path,
            sr=sr,
            mono=True,
            offset=start_ms / 1000.0,
            duration=(end_ms - start_ms) / 1000.0,
        )
    except Exception as exc:
        raise LocalAcousticMatchError(f"cannot decode bounded audio {path}: {exc}") from exc
    return np.asarray(audio, dtype=np.float32)


def execute_region_source_match_jobs(
    *,
    mix_audio_path: Path,
    plan: Mapping[str, Any],
    source_audio_by_source_ordinal: Mapping[int, Path],
    config: LocalAcousticMatchConfig | None = None,
    audio_loader: Callable[..., np.ndarray] | None = None,
) -> dict[str, Any]:
    """Execute Pro acoustic jobs while reusing mix features per merged region."""

    config = config or LocalAcousticMatchConfig()
    config.validate()
    if plan.get("mode") != "plan_only" or plan.get("backend_execution_performed") is not False:
        raise LocalAcousticMatchError("input is not an unexecuted plan")
    if not mix_audio_path.is_file():
        raise LocalAcousticMatchError(f"mix audio does not exist: {mix_audio_path}")
    raw_jobs = plan.get("jobs")
    if not isinstance(raw_jobs, list):
        raise LocalAcousticMatchError("plan jobs must be a list")
    jobs = [
        row
        for row in raw_jobs
        if isinstance(row, Mapping)
        and "source_local_acoustic_match" in (row.get("requested_capabilities") or [])
    ]
    loader = audio_loader or _default_loader

    grouped: dict[tuple[str, int, int], list[Mapping[str, Any]]] = {}
    for job in jobs:
        mix_start, mix_end = _window(job, "mix_window_ms")
        raw_region = job.get("region_mix_window_ms")
        if isinstance(raw_region, list) and len(raw_region) == 2:
            region_start, region_end = _window(job, "region_mix_window_ms")
            if region_start > mix_start or region_end < mix_end:
                raise LocalAcousticMatchError("region does not contain job mix window")
        else:
            region_start, region_end = mix_start, mix_end
        region_id = str(job.get("region_id") or f"legacy-{region_start}-{region_end}")
        grouped.setdefault((region_id, region_start, region_end), []).append(job)

    results: list[dict[str, Any]] = []
    mix_feature_region_count = 0
    for (region_id, region_start_ms, region_end_ms), region_jobs in sorted(grouped.items()):
        mix_audio = loader(
            mix_audio_path,
            sr=config.sr,
            start_ms=region_start_ms,
            end_ms=region_end_ms,
        )
        if len(mix_audio) < config.sr * 2:
            raise LocalAcousticMatchError(f"region {region_id} mix audio is too short")
        mix_features = extract_harmonic_features(
            mix_audio,
            sr=config.sr,
            hop_length=config.hop_length,
        )
        mix_feature_region_count += 1

        for job in region_jobs:
            job_id = str(job.get("job_id") or "").strip()
            if not job_id:
                raise LocalAcousticMatchError("local acoustic job is missing job_id")
            try:
                source_ordinal = int(job["source_ordinal"])
            except (KeyError, TypeError, ValueError) as exc:
                raise LocalAcousticMatchError(f"job {job_id} has no source ordinal") from exc
            source_path = source_audio_by_source_ordinal.get(source_ordinal)
            if source_path is None or not source_path.is_file():
                raise LocalAcousticMatchError(
                    f"job {job_id} source audio missing for source ordinal {source_ordinal}"
                )

            mix_start_ms, mix_end_ms = _window(job, "mix_window_ms")
            source_start_ms, source_end_ms = _window(job, "source_window_ms")
            expected_raw = job.get("expected_source_time_ms")
            if expected_raw is None:
                raise LocalAcousticMatchError(f"job {job_id} has no expected source time")
            try:
                expected_source_ms = int(expected_raw)
            except (TypeError, ValueError) as exc:
                raise LocalAcousticMatchError(
                    f"job {job_id} expected source time is invalid"
                ) from exc
            if not source_start_ms <= expected_source_ms <= source_end_ms:
                raise LocalAcousticMatchError(
                    f"job {job_id} expected source time is outside source window"
                )

            source_audio = loader(
                source_path,
                sr=config.sr,
                start_ms=source_start_ms,
                end_ms=source_end_ms,
            )
            if len(source_audio) < config.sr * 2:
                raise LocalAcousticMatchError(f"job {job_id} bounded source audio is too short")
            source_features = extract_harmonic_features(
                source_audio,
                sr=config.sr,
                hop_length=config.hop_length,
            )

            local_mix_start = (mix_start_ms - region_start_ms) / 1000.0
            local_mix_end = (mix_end_ms - region_start_ms) / 1000.0
            rate_prior = _rate(job.get("rate_prior"))
            slope_candidates = _slopes(rate_prior, config)
            retrieval = retrieve_coarse_window(
                mix_features,
                source_features,
                mix_start=local_mix_start,
                mix_end=local_mix_end,
                slopes=slope_candidates,
                source_search_start=0.0,
                source_search_end=source_features.duration_seconds,
                candidate_step_seconds=config.candidate_step_seconds,
                top_k=5,
                nms_separation_seconds=0.20,
                min_score=config.min_score,
                min_margin=config.min_margin,
            )
            best = retrieval.top1
            matched_source_start_ms = source_start_ms + int(round(best.source_start * 1000.0))
            predicted_mix_start_ms = mix_start_ms + int(
                round((expected_source_ms - matched_source_start_ms) / best.estimated_slope)
            )
            editor_start = job.get("editor_cue_start_ms")
            residual = None if editor_start is None else int(editor_start) - predicted_mix_start_ms
            reliable = not retrieval.ambiguous and best.feature_agreement >= 2
            slope_step = config.slope_step if rate_prior is not None else config.no_prior_step
            slope_min, slope_max, slope_boundary_hit = _slope_search_metadata(
                slope_candidates,
                float(best.estimated_slope),
                step=slope_step,
            )
            source_search_min_ms, source_search_max_ms, source_boundary_hit = (
                _source_search_metadata(
                    source_window_start_ms=source_start_ms,
                    source_duration_seconds=source_features.duration_seconds,
                    query_duration_seconds=local_mix_end - local_mix_start,
                    estimated_slope=float(best.estimated_slope),
                    matched_source_start_seconds=float(best.source_start),
                    boundary_margin_seconds=config.source_boundary_margin_seconds,
                )
            )
            timing_fusion_eligible = (
                reliable and not slope_boundary_hit and not source_boundary_hit
            )
            acoustic_shift = (
                None if editor_start is None else predicted_mix_start_ms - int(editor_start)
            )
            results.append(
                {
                    "job_id": job_id,
                    "region_id": region_id,
                    "source_ordinal": source_ordinal,
                    "cue_ordinal": job.get("cue_ordinal"),
                    "mix_window_ms": [mix_start_ms, mix_end_ms],
                    "region_mix_window_ms": [region_start_ms, region_end_ms],
                    "source_window_ms": [source_start_ms, source_end_ms],
                    "rate_prior": rate_prior,
                    "estimated_slope": round(float(best.estimated_slope), 6),
                    "slope_search_min": round(slope_min, 6),
                    "slope_search_max": round(slope_max, 6),
                    "slope_search_boundary_hit": slope_boundary_hit,
                    "source_search_min_ms": source_search_min_ms,
                    "source_search_max_ms": source_search_max_ms,
                    "source_search_boundary_hit": source_boundary_hit,
                    "fused_score": round(float(best.fused_score), 6),
                    "chroma_score": round(float(best.chroma_score), 6),
                    "mfcc_score": round(float(best.mfcc_score), 6),
                    "feature_agreement": int(best.feature_agreement),
                    "margin": round(float(retrieval.margin), 6),
                    "ambiguous": bool(retrieval.ambiguous),
                    "local_match_gate_passed": reliable,
                    "local_match_status": (
                        "gate_passed_unadjudicated"
                        if reliable
                        else "ambiguous_or_feature_disagreement"
                    ),
                    "reliability_semantics": (
                        "local_retrieval_gate_only_not_timing_authority"
                    ),
                    "timing_fusion_evidence_eligible": timing_fusion_eligible,
                    "timing_fusion_evidence_status": (
                        "eligible_bounded_interior_optimum"
                        if timing_fusion_eligible
                        else "diagnostic_search_boundary_limited"
                        if reliable and (slope_boundary_hit or source_boundary_hit)
                        else "retrieval_gate_failed"
                    ),
                    # Legacy alias retained for artifact readers.  New Pro
                    # decision code consumes local_match_gate_passed instead.
                    "reliable_local_match": reliable,
                    "matched_source_start_ms": matched_source_start_ms,
                    "expected_source_time_ms": expected_source_ms,
                    "predicted_mix_start_ms": predicted_mix_start_ms,
                    "editor_start_residual_ms": residual,
                    "acoustic_shift_ms": acoustic_shift,
                    "shadow_evidence_only": bool(job.get("shadow_evidence_only", False)),
                    "boundary_competitor_for_job_id": job.get("boundary_competitor_for_job_id"),
                    "boundary_role": job.get("boundary_role"),
                    "automatic_timing_change_allowed": False,
                    "automatic_text_change_allowed": False,
                    "timing_mutation_performed": False,
                }
            )

    return {
        "schema_version": LOCAL_ACOUSTIC_V11_SCHEMA_VERSION,
        "backend": "bounded_source_mix_harmonic_retrieval",
        "execution_policy": "merged_mix_regions_individual_source_queries",
        "config": config.to_dict(),
        "job_count": len(results),
        "mix_feature_region_count": mix_feature_region_count,
        "automatic_timing_change_allowed": False,
        "automatic_text_change_allowed": False,
        "timing_mutation_performed": False,
        "jobs": results,
    }
