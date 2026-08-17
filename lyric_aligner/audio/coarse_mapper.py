"""Multi-window coarse audio mapping feeding the v4 TimeWarp solver."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from lyric_aligner.audio.features import (
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
    return (
        5.0 * candidate.fused_score
        + 0.30 * candidate.feature_agreement
        - 0.08 * rank
    )


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

    # Rate changes are allowed; penalize disagreement rather than requiring a
    # globally constant slope. This keeps 1.08->1.17->1.43 viable.
    return -1.25 * abs(observed_rate - expected_rate)


def select_monotonic_candidate_path(
    results: list[RetrievalResult],
    *,
    middle_cut: str = "false",
    max_continuous_rate: float = 2.0,
) -> list[PathPoint]:
    if middle_cut not in {"false", "true", "unknown"}:
        raise ValueError("middle_cut must be false, true, or unknown")
    if not results:
        return []
    ordered = sorted(results, key=lambda item: item.mix_center)
    if any(
        right.mix_center <= left.mix_center
        for left, right in zip(ordered, ordered[1:])
    ):
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
                    score = (
                        scores[-1][previous_index]
                        + transition
                        + _emission(candidate, candidate_index)
                    )
                    if score > best_score:
                        best_score = score
                        best_parent = previous_index
                row_scores[candidate_index] = best_score
                row_parents[candidate_index] = best_parent
        if not any(np.isfinite(value) for value in row_scores):
            raise ValueError(
                f"no monotonic coarse candidate path survives at mix window {result.mix_center:.3f}s"
            )
        scores.append(row_scores)
        parents.append(row_parents)

    index = int(np.argmax(scores[-1]))
    chosen: list[tuple[RetrievalResult, RetrievalCandidate, int]] = []
    for window_index in range(len(ordered) - 1, -1, -1):
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
    """Restore global mix coordinates after interval-local feature extraction."""

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


def build_coarse_timewarp(
    mix_audio: np.ndarray,
    source_audio: np.ndarray,
    *,
    sr: int,
    mix_start: float,
    mix_end: float,
    bpm_prior: float | None = None,
    middle_cut: str = "false",
    feature_hop_length: int = 2048,
    window_seconds: float = 6.0,
    step_seconds: float = 3.0,
    candidate_step_seconds: float = 0.75,
    slope_minimum: float = 0.65,
    slope_maximum: float = 1.80,
    slope_step: float = 0.10,
    min_score: float = 0.72,
    min_margin: float = 0.035,
) -> dict[str, Any]:
    mix_duration = len(mix_audio) / sr
    if mix_start < 0 or mix_end > mix_duration or mix_end <= mix_start:
        raise ValueError("coarse mapping interval is outside mix audio")

    # Do not HPSS/chroma/MFCC the full 40-60 minute mix once per song.  Extract
    # only the occurrence/transition interval; result coordinates are restored
    # to global mix time below.  Source features remain full-song because
    # retrieval must locate the surviving source region.
    sample_start = max(0, int(np.floor(mix_start * sr)))
    sample_end = min(len(mix_audio), int(np.ceil(mix_end * sr)))
    local_mix_audio = np.asarray(mix_audio[sample_start:sample_end], dtype=np.float32)
    local_offset = sample_start / sr
    local_duration = len(local_mix_audio) / sr

    mix_features = extract_harmonic_features(
        local_mix_audio, sr=sr, hop_length=feature_hop_length
    )
    source_features = extract_harmonic_features(
        source_audio, sr=sr, hop_length=feature_hop_length
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
    for start, end in _windows(
        local_search_start,
        local_search_end,
        window_seconds,
        step_seconds,
    ):
        local_result = retrieve_coarse_window(
            mix_features,
            source_features,
            mix_start=start,
            mix_end=end,
            slopes=slopes,
            bpm_prior=bpm_prior,
            candidate_step_seconds=candidate_step_seconds,
            min_score=min_score,
            min_margin=min_margin,
        )
        results.append(_absolute_result(local_result, local_offset))
    if len(results) < 2:
        raise ValueError("coarse mapping requires at least two retrieval windows")

    path = select_monotonic_candidate_path(results, middle_cut=middle_cut)
    anchors = [
        AlignmentAnchor(
            mix_time=point.mix_center,
            source_time=point.source_center,
            confidence=max(0.05, point.fused_score),
            feature_scores={
                "chroma": point.chroma_score,
                "mfcc": point.mfcc_score,
            },
        )
        for point in path
    ]
    mapping = select_timewarp(
        anchors,
        bpm_prior=bpm_prior,
        middle_cut=middle_cut,
    )
    return {
        "stage": "coarse_timewarp",
        "mix_interval": [mix_start, mix_end],
        "feature_scope": {
            "mix_feature_start": local_offset,
            "mix_feature_end": local_offset + local_duration,
            "full_mix_duration": mix_duration,
        },
        "feature_config": {
            "sr": sr,
            "hop_length": feature_hop_length,
            "window_seconds": window_seconds,
            "step_seconds": step_seconds,
            "candidate_step_seconds": candidate_step_seconds,
        },
        "slope_search": {
            "minimum": slope_minimum,
            "maximum": slope_maximum,
            "step": slope_step,
            "bpm_prior": bpm_prior,
        },
        "windows": [result.to_dict() for result in results],
        "path": [point.to_dict() for point in path],
        "timewarp": mapping,
    }
