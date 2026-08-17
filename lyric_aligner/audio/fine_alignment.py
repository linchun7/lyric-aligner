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
    if len(windows) != len(path) or len(path) < 2:
        raise ValueError("fine alignment requires matching coarse windows and path points")

    mix_features = extract_harmonic_features(mix_audio, sr=sr, hop_length=hop_length)
    source_features = extract_harmonic_features(source_audio, sr=sr, hop_length=hop_length)
    fine_points: list[FinePoint] = []
    unresolved = 0
    for window, point in zip(windows, path):
        mix_start = float(window["mix_start"])
        mix_end = float(window["mix_end"])
        mix_duration = mix_end - mix_start
        coarse_center = float(point["source_center"])
        coarse_slope = float(point["estimated_slope"])
        source_duration = mix_duration * coarse_slope
        expected_start = coarse_center - source_duration / 2.0
        search_start = max(0.0, expected_start - source_radius_seconds)
        search_end = min(
            source_features.duration_seconds,
            expected_start + source_duration + source_radius_seconds,
        )
        retrieval = retrieve_coarse_window(
            mix_features,
            source_features,
            mix_start=mix_start,
            mix_end=mix_end,
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
            feature_scores={
                "chroma": point.chroma_score,
                "mfcc": point.mfcc_score,
            },
        )
        for point in fine_points
    ]
    timewarp = select_timewarp(
        anchors,
        bpm_prior=bpm_prior,
        middle_cut=middle_cut,
    )
    if unresolved:
        timewarp = {**timewarp, "blocked": True}
    return {
        "stage": "fine_alignment",
        "applied": True,
        "reasons": reasons or ["forced"],
        "status": "review_required" if (unresolved or timewarp["blocked"]) else "refined",
        "unresolved_window_count": unresolved,
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
        "path": [point.to_dict() for point in fine_points],
        "timewarp": timewarp,
    }
