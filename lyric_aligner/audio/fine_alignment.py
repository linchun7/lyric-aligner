"""Selective high-resolution refinement for uncertain coarse audio mappings."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from lyric_aligner.audio.features import extract_harmonic_features, retrieve_coarse_window
from lyric_aligner.audio.timewarp import AlignmentAnchor, select_timewarp


@dataclass(frozen=True)
class FinePoint:
    mix_center: float
    coarse_source_center: float
    refined_source_center: float
    coarse_slope: float
    refined_slope: float
    fused_score: float
    chroma_score: float
    mfcc_score: float
    margin: float
    feature_agreement: int
    ambiguous: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fine_alignment_reasons(coarse_payload: dict[str, Any]) -> list[str]:
    result = coarse_payload.get("result", coarse_payload)
    timewarp = result.get("timewarp", {})
    reasons: list[str] = []
    if bool(timewarp.get("blocked", False)):
        reasons.append("coarse_timewarp_blocked")
    selection = str(timewarp.get("selection", ""))
    if selection and selection != "AFFINE_ACCEPTED":
        reasons.append(f"coarse_selection:{selection}")
    ambiguous = sum(bool(window.get("ambiguous", False)) for window in result.get("windows", []))
    if ambiguous:
        reasons.append(f"ambiguous_windows:{ambiguous}")
    return reasons


def should_run_fine_alignment(coarse_payload: dict[str, Any], *, force: bool = False) -> bool:
    return force or bool(fine_alignment_reasons(coarse_payload))


def _slope_candidates(center: float, radius: float, step: float) -> list[float]:
    if center <= 0 or radius <= 0 or step <= 0:
        raise ValueError("invalid fine slope search settings")
    minimum = max(0.45, center - radius)
    maximum = min(2.20, center + radius)
    values = set(float(value) for value in np.arange(minimum, maximum + step / 2, step))
    values.add(float(center))
    return sorted(round(value, 6) for value in values)


def refine_coarse_mapping(
    mix_audio: np.ndarray,
    source_audio: np.ndarray,
    coarse_payload: dict[str, Any],
    *,
    sr: int,
    mix_audio_start: float = 0.0,
    full_mix_duration: float | None = None,
    force: bool = False,
    hop_length: int = 256,
    source_radius_seconds: float = 1.25,
    slope_radius: float = 0.08,
    slope_step: float = 0.02,
    candidate_step_seconds: float = 0.05,
    min_score: float = 0.62,
    min_margin: float = 0.012,
    bpm_prior: float | None = None,
    middle_cut: str = "false",
    bpm_prior_strength: float = 0.02,
    max_continuous_rate: float = 2.0,
    min_excess_source_jump: float = 1.5,
    min_piecewise_improvement: float = 0.25,
    minimum_feature_families: int = 2,
    drift_threshold: float = 0.30,
    residual_threshold: float = 0.25,
    complexity_penalty: float = 0.035,
) -> dict[str, Any]:
    reasons = fine_alignment_reasons(coarse_payload)
    if not force and not reasons:
        return {
            "stage": "fine_alignment",
            "applied": False,
            "reasons": [],
            "status": "skipped_clean_affine",
        }

    result = coarse_payload.get("result", coarse_payload)
    windows = list(result.get("windows", []))
    path = list(result.get("path", []))
    if len(windows) < len(path) or len(path) < 2:
        raise ValueError("fine alignment requires a coarse window for every path point")
    windows = windows[: len(path)]
    if any(
        abs(float(window["mix_center"]) - float(point["mix_center"])) > 1e-6
        for window, point in zip(windows, path)
    ):
        raise ValueError("fine alignment requires path points to match the coarse window prefix")

    # Fine alignment is deliberately local. Do not compute 16 kHz / hop-256
    # features for the entire 40-60 minute mix when only a few coarse windows
    # need refinement. The caller may now provide only the bounded audio buffer
    # that contains those absolute mix-time windows.
    global_start = min(float(window["mix_start"]) for window in windows)
    global_end = max(float(window["mix_end"]) for window in windows)
    if sr <= 0:
        raise ValueError("sample rate must be positive")
    buffer_start = float(mix_audio_start)
    if buffer_start < 0:
        raise ValueError("mix_audio_start must be non-negative")
    buffer_duration = len(mix_audio) / sr
    buffer_end = buffer_start + buffer_duration
    mix_duration = buffer_end if full_mix_duration is None else float(full_mix_duration)
    tolerance = max(0.1, 1.0 / sr + 1e-9)
    if mix_duration <= 0 or mix_duration + tolerance < buffer_end:
        raise ValueError("full_mix_duration is shorter than supplied mix audio buffer")
    if (
        global_start < buffer_start - tolerance
        or global_end > buffer_end + tolerance
        or global_start < 0
        or global_end > mix_duration + tolerance
        or global_end <= global_start
    ):
        raise ValueError("fine windows are outside supplied mix audio buffer")
    sample_start = max(0, int(np.floor((global_start - buffer_start) * sr)))
    sample_end = min(len(mix_audio), int(np.ceil((global_end - buffer_start) * sr)))
    local_mix_audio = np.asarray(mix_audio[sample_start:sample_end], dtype=np.float32)
    local_offset = buffer_start + sample_start / sr

    mix_features = extract_harmonic_features(local_mix_audio, sr=sr, hop_length=hop_length)
    source_features = extract_harmonic_features(source_audio, sr=sr, hop_length=hop_length)
    fine_points: list[FinePoint] = []
    unresolved = 0
    for window, point in zip(windows, path):
        mix_start = float(window["mix_start"])
        mix_end = float(window["mix_end"])
        local_mix_start = mix_start - local_offset
        local_mix_end = mix_end - local_offset
        mix_window_duration = mix_end - mix_start
        coarse_center = float(point["source_center"])
        coarse_slope = float(point["estimated_slope"])
        source_duration = mix_window_duration * coarse_slope
        expected_start = coarse_center - source_duration / 2.0
        search_start = max(0.0, expected_start - source_radius_seconds)
        search_end = min(
            source_features.duration_seconds,
            expected_start + source_duration + source_radius_seconds,
        )
        retrieval = retrieve_coarse_window(
            mix_features,
            source_features,
            mix_start=local_mix_start,
            mix_end=local_mix_end,
            slopes=_slope_candidates(coarse_slope, slope_radius, slope_step),
            source_search_start=search_start,
            source_search_end=search_end,
            candidate_step_seconds=candidate_step_seconds,
            top_k=5,
            nms_separation_seconds=0.20,
            min_score=min_score,
            min_margin=min_margin,
        )
        selected = retrieval.top1
        if selected.fused_score < min_score or selected.feature_agreement < 1:
            unresolved += 1
            refined_center = coarse_center
            refined_slope = coarse_slope
            score = float(point["fused_score"])
            chroma_score = float(point.get("chroma_score", 0.0))
            mfcc_score = float(point.get("mfcc_score", 0.0))
            margin = 0.0
            agreement = int(point.get("feature_agreement", 0))
            ambiguous = True
        else:
            refined_center = selected.source_center
            refined_slope = selected.estimated_slope
            score = selected.fused_score
            chroma_score = selected.chroma_score
            mfcc_score = selected.mfcc_score
            margin = retrieval.margin
            agreement = selected.feature_agreement
            ambiguous = retrieval.ambiguous
        fine_points.append(
            FinePoint(
                mix_center=float(point["mix_center"]),
                coarse_source_center=coarse_center,
                refined_source_center=refined_center,
                coarse_slope=coarse_slope,
                refined_slope=refined_slope,
                fused_score=score,
                chroma_score=chroma_score,
                mfcc_score=mfcc_score,
                margin=margin,
                feature_agreement=agreement,
                ambiguous=ambiguous,
            )
        )

    anchors = [
        AlignmentAnchor(
            mix_time=point.mix_center,
            source_time=point.refined_source_center,
            confidence=max(0.05, point.fused_score),
            feature_scores={"chroma": point.chroma_score, "mfcc": point.mfcc_score},
        )
        for point in fine_points
    ]
    timewarp = select_timewarp(
        anchors,
        bpm_prior=bpm_prior,
        bpm_prior_strength=bpm_prior_strength,
        middle_cut=middle_cut,
        max_continuous_rate=max_continuous_rate,
        min_excess_source_jump=min_excess_source_jump,
        min_piecewise_improvement=min_piecewise_improvement,
        minimum_feature_families=minimum_feature_families,
        drift_threshold=drift_threshold,
        residual_threshold=residual_threshold,
        complexity_penalty=complexity_penalty,
    )
    if unresolved:
        timewarp = {**timewarp, "blocked": True}
    return {
        "stage": "fine_alignment",
        "applied": True,
        "reasons": reasons or ["forced"],
        "status": "review_required" if (unresolved or timewarp["blocked"]) else "refined",
        "unresolved_window_count": unresolved,
        "feature_scope": {
            "mix_feature_start": local_offset,
            "mix_feature_end": min(mix_duration, local_offset + len(local_mix_audio) / sr),
            "full_mix_duration": mix_duration,
        },
        "config": {
            "sr": sr,
            "hop_length": hop_length,
            "source_radius_seconds": source_radius_seconds,
            "slope_radius": slope_radius,
            "slope_step": slope_step,
            "candidate_step_seconds": candidate_step_seconds,
            "min_score": min_score,
            "min_margin": min_margin,
        },
        "timewarp_config": {
            "bpm_prior_strength": bpm_prior_strength,
            "max_continuous_rate": max_continuous_rate,
            "min_excess_source_jump": min_excess_source_jump,
            "min_piecewise_improvement": min_piecewise_improvement,
            "minimum_feature_families": minimum_feature_families,
            "drift_threshold": drift_threshold,
            "residual_threshold": residual_threshold,
            "complexity_penalty": complexity_penalty,
        },
        "path": [point.to_dict() for point in fine_points],
        "timewarp": timewarp,
    }
