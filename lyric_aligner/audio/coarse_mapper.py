"""Multi-window coarse audio mapping feeding the v4 TimeWarp solver."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from lyric_aligner.audio.features import (
    FeatureBundle,
    RetrievalCandidate,
    RetrievalResult,
    extract_harmonic_features,
    retrieve_coarse_window,
    slope_grid,
)
from lyric_aligner.audio.timewarp import AlignmentAnchor, select_timewarp


@dataclass(frozen=True)
class PathPoint:
    mix_center: float
    source_center: float
    estimated_slope: float
    fused_score: float
    chroma_score: float
    mfcc_score: float
    feature_agreement: int
    candidate_rank: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _emission(candidate: RetrievalCandidate, rank: int) -> float:
    return 5.0 * candidate.fused_score + 0.30 * candidate.feature_agreement - 0.08 * rank


def _transition_score(
    left_result: RetrievalResult,
    left: RetrievalCandidate,
    right_result: RetrievalResult,
    right: RetrievalCandidate,
    *,
    middle_cut: str,
    max_continuous_rate: float,
) -> float | None:
    mix_delta = right_result.mix_center - left_result.mix_center
    if mix_delta <= 0:
        return None
    source_delta = right.source_center - left.source_center
    if source_delta < -0.35:
        return None
    observed_rate = source_delta / mix_delta
    expected_rate = (left.estimated_slope + right.estimated_slope) / 2.0

    if observed_rate > max_continuous_rate:
        if middle_cut == "false":
            return None
        return -3.0 - min(4.0, observed_rate - max_continuous_rate)
    if observed_rate < 0.45:
        return -2.0 - (0.45 - observed_rate) * 2.0
    return -1.25 * abs(observed_rate - expected_rate)


def select_monotonic_candidate_path(
    results: list[RetrievalResult],
    *,
    middle_cut: str = "false",
    max_continuous_rate: float = 2.0,
    max_trailing_unmatched_windows: int = 0,
) -> list[PathPoint]:
    if middle_cut not in {"false", "true", "unknown"}:
        raise ValueError("middle_cut must be false, true, or unknown")
    if not results:
        return []
    if max_trailing_unmatched_windows < 0:
        raise ValueError("max_trailing_unmatched_windows must be non-negative")
    ordered = sorted(results, key=lambda item: item.mix_center)
    if any(right.mix_center <= left.mix_center for left, right in zip(ordered, ordered[1:])):
        raise ValueError("retrieval windows must have strictly increasing centers")

    scores: list[list[float]] = []
    parents: list[list[int | None]] = []
    for window_index, result in enumerate(ordered):
        candidates = list(result.candidates)
        if not candidates:
            raise ValueError("every retrieval window requires at least one candidate")
        row_scores = [-float("inf")] * len(candidates)
        row_parents: list[int | None] = [None] * len(candidates)
        if window_index == 0:
            for candidate_index, candidate in enumerate(candidates):
                row_scores[candidate_index] = _emission(candidate, candidate_index)
        else:
            previous_result = ordered[window_index - 1]
            previous_candidates = list(previous_result.candidates)
            for candidate_index, candidate in enumerate(candidates):
                best_score = -float("inf")
                best_parent: int | None = None
                for previous_index, previous in enumerate(previous_candidates):
                    if not np.isfinite(scores[-1][previous_index]):
                        continue
                    transition = _transition_score(
                        previous_result,
                        previous,
                        result,
                        candidate,
                        middle_cut=middle_cut,
                        max_continuous_rate=max_continuous_rate,
                    )
                    if transition is None:
                        continue
                    score = scores[-1][previous_index] + transition + _emission(candidate, candidate_index)
                    if score > best_score:
                        best_score = score
                        best_parent = previous_index
                row_scores[candidate_index] = best_score
                row_parents[candidate_index] = best_parent
        if not any(np.isfinite(value) for value in row_scores):
            trailing_count = len(ordered) - window_index
            if (
                trailing_count <= max_trailing_unmatched_windows
                and len(scores) >= 3
            ):
                break
            raise ValueError(
                f"no monotonic coarse candidate path survives at mix window {result.mix_center:.3f}s"
            )
        scores.append(row_scores)
        parents.append(row_parents)

    index = int(np.argmax(scores[-1]))
    chosen: list[tuple[RetrievalResult, RetrievalCandidate, int]] = []
    for window_index in range(len(scores) - 1, -1, -1):
        result = ordered[window_index]
        candidate = result.candidates[index]
        chosen.append((result, candidate, index))
        parent = parents[window_index][index]
        if window_index > 0:
            if parent is None:
                raise ValueError("coarse candidate path backtracking failed")
            index = parent
    chosen.reverse()
    return [
        PathPoint(
            mix_center=result.mix_center,
            source_center=candidate.source_center,
            estimated_slope=candidate.estimated_slope,
            fused_score=candidate.fused_score,
            chroma_score=candidate.chroma_score,
            mfcc_score=candidate.mfcc_score,
            feature_agreement=candidate.feature_agreement,
            candidate_rank=rank,
        )
        for result, candidate, rank in chosen
    ]


def _windows(start: float, end: float, window_seconds: float, step_seconds: float):
    if end <= start or window_seconds <= 0 or step_seconds <= 0:
        raise ValueError("invalid coarse mapping interval/window")
    cursor = start
    while cursor + window_seconds <= end + 1e-9:
        yield cursor, cursor + window_seconds
        cursor += step_seconds


def _absolute_result(result: RetrievalResult, offset: float) -> RetrievalResult:
    return RetrievalResult(
        mix_start=result.mix_start + offset,
        mix_end=result.mix_end + offset,
        mix_center=result.mix_center + offset,
        top1=result.top1,
        top2=result.top2,
        candidates=result.candidates,
        margin=result.margin,
        ambiguous=result.ambiguous,
        min_score=result.min_score,
        min_margin=result.min_margin,
    )


def _source_features(
    source_audio: np.ndarray | None,
    cached: FeatureBundle | None,
    *,
    sr: int,
    hop_length: int,
) -> FeatureBundle:
    if cached is not None:
        if cached.sr != sr or cached.hop_length != hop_length:
            raise ValueError("cached source feature sampling parameters do not match coarse config")
        return cached
    if source_audio is None:
        raise ValueError("source audio is required when cached source features are unavailable")
    return extract_harmonic_features(source_audio, sr=sr, hop_length=hop_length)


def build_coarse_timewarp(
    mix_audio: np.ndarray,
    source_audio: np.ndarray | None,
    *,
    sr: int,
    mix_start: float,
    mix_end: float,
    mix_audio_start: float = 0.0,
    full_mix_duration: float | None = None,
    source_feature_bundle: FeatureBundle | None = None,
    bpm_prior: float | None = None,
    middle_cut: str = "false",
    feature_hop_length: int = 2048,
    window_seconds: float = 6.0,
    step_seconds: float = 3.0,
    candidate_step_seconds: float = 0.75,
    candidate_pool_size: int = 8,
    slope_minimum: float = 0.65,
    slope_maximum: float = 1.80,
    slope_step: float = 0.10,
    min_score: float = 0.72,
    min_margin: float = 0.035,
    bpm_prior_strength: float = 0.02,
    max_continuous_rate: float = 2.0,
    min_excess_source_jump: float = 1.5,
    min_piecewise_improvement: float = 0.25,
    minimum_feature_families: int = 2,
    drift_threshold: float = 0.30,
    residual_threshold: float = 0.25,
    complexity_penalty: float = 0.035,
    require_timewarp: bool = True,
) -> dict[str, Any]:
    if sr <= 0:
        raise ValueError("sample rate must be positive")
    if candidate_pool_size < 2:
        raise ValueError("candidate_pool_size must be >= 2")
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
        mix_start < buffer_start - tolerance
        or mix_end > buffer_end + tolerance
        or mix_start < 0
        or mix_end > mix_duration + tolerance
        or mix_end <= mix_start
    ):
        raise ValueError("coarse mapping interval is outside supplied mix audio buffer")

    sample_start = max(0, int(np.floor((mix_start - buffer_start) * sr)))
    sample_end = min(len(mix_audio), int(np.ceil((mix_end - buffer_start) * sr)))
    local_mix_audio = np.asarray(mix_audio[sample_start:sample_end], dtype=np.float32)
    local_offset = buffer_start + sample_start / sr
    local_duration = len(local_mix_audio) / sr

    mix_features = extract_harmonic_features(local_mix_audio, sr=sr, hop_length=feature_hop_length)
    source_features = _source_features(
        source_audio,
        source_feature_bundle,
        sr=sr,
        hop_length=feature_hop_length,
    )
    slopes = slope_grid(
        minimum=slope_minimum,
        maximum=slope_maximum,
        step=slope_step,
        bpm_prior=bpm_prior,
    )
    results: list[RetrievalResult] = []
    local_search_start = max(0.0, mix_start - local_offset)
    local_search_end = min(local_duration, mix_end - local_offset)
    for start, end in _windows(local_search_start, local_search_end, window_seconds, step_seconds):
        local_result = retrieve_coarse_window(
            mix_features,
            source_features,
            mix_start=start,
            mix_end=end,
            slopes=slopes,
            bpm_prior=bpm_prior,
            candidate_step_seconds=candidate_step_seconds,
            top_k=candidate_pool_size,
            min_score=min_score,
            min_margin=min_margin,
        )
        results.append(_absolute_result(local_result, local_offset))
    if len(results) < 2:
        raise ValueError("coarse mapping requires at least two retrieval windows")

    maximum_excluded_trailing_windows = max(
        1, int(math.ceil(window_seconds / step_seconds))
    )
    if require_timewarp:
        path = select_monotonic_candidate_path(
            results,
            middle_cut=middle_cut,
            max_continuous_rate=max_continuous_rate,
            max_trailing_unmatched_windows=maximum_excluded_trailing_windows,
        )
        selected_window_count = len(path)
        excluded_trailing_windows = results[selected_window_count:]
        anchors = [
            AlignmentAnchor(
                mix_time=point.mix_center,
                source_time=point.source_center,
                confidence=max(0.05, point.fused_score),
                feature_scores={"chroma": point.chroma_score, "mfcc": point.mfcc_score},
            )
            for point in path
        ]
        mapping = select_timewarp(
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
        coverage_status = (
            "bounded_terminal_disconnect"
            if excluded_trailing_windows
            else "complete"
        )
    else:
        path = []
        selected_window_count = 0
        excluded_trailing_windows = []
        coverage_status = "retrieval_only"
        mapping = {
            "mapping": None,
            "selection": "NOT_REQUESTED",
            "escalated": False,
            "discontinuities": [],
            "blocked": False,
            "reason": "transition activity consumes retrieval windows, not a continuous TimeWarp",
        }
    return {
        "stage": "coarse_timewarp",
        "mix_interval": [mix_start, mix_end],
        "feature_scope": {
            "mix_feature_start": local_offset,
            "mix_feature_end": min(mix_duration, local_offset + local_duration),
            "full_mix_duration": mix_duration,
        },
        "feature_config": {
            "sr": sr,
            "hop_length": feature_hop_length,
            "window_seconds": window_seconds,
            "step_seconds": step_seconds,
            "candidate_step_seconds": candidate_step_seconds,
            "candidate_pool_size": candidate_pool_size,
        },
        "slope_search": {
            "minimum": slope_minimum,
            "maximum": slope_maximum,
            "step": slope_step,
            "bpm_prior": bpm_prior,
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
        "path_coverage": {
            "status": coverage_status,
            "timewarp_required": require_timewarp,
            "retrieved_window_count": len(results),
            "selected_window_count": selected_window_count,
            "excluded_trailing_window_count": len(excluded_trailing_windows),
            "maximum_excluded_trailing_windows": maximum_excluded_trailing_windows,
            "excluded_mix_centers": [
                result.mix_center for result in excluded_trailing_windows
            ],
        },
        "windows": [result.to_dict() for result in results],
        "path": [point.to_dict() for point in path],
        "timewarp": mapping,
    }
