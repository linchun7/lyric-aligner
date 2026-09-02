"""Optional prepared-stem evidence for same-track splice diagnostics.

This module is deliberately diagnostic-only.  It never changes the authoritative
Source-to-Mix mapping, review decisions, timelines, or release state.  A prepared
stem is a user-provided, pre-mix rendition of one occurrence (for example an
already tempo-adjusted single-song export).  When such a stem exists, it can be
stronger structural evidence than the original commercial master because it
shares the production time base with the final mix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from scipy.signal import correlate


@dataclass(frozen=True)
class PreparedStemConfig:
    ncc_window_seconds: float = 0.60
    scan_step_seconds: float = 0.50
    source_search_back_seconds: float = 12.0
    source_search_forward_seconds: float = 22.0
    ncc_candidate_step_seconds: float = 0.005
    ncc_candidate_min_separation_seconds: float = 0.35
    ncc_top_k: int = 5
    lag_cluster_tolerance_seconds: float = 0.08
    lag_candidate_min_score: float = 0.75
    mode_min_support_centers: int = 5
    mode_min_median_ncc: float = 0.78
    mode_min_separation_seconds: float = 2.0
    handoff_max_gap_seconds: float = 1.0
    handoff_max_overlap_seconds: float = 1.0
    verification_span_seconds: float = 3.0
    verification_window_seconds: float = 0.12
    verification_step_seconds: float = 0.01
    verification_min_r2: float = 0.95
    verification_min_minor_share: float = 0.10
    verification_min_dual_count: int = 5
    verification_min_dual_span_seconds: float = 0.20

    def validate(self) -> None:
        positive = {
            "ncc_window_seconds": self.ncc_window_seconds,
            "scan_step_seconds": self.scan_step_seconds,
            "source_search_back_seconds": self.source_search_back_seconds,
            "source_search_forward_seconds": self.source_search_forward_seconds,
            "ncc_candidate_step_seconds": self.ncc_candidate_step_seconds,
            "ncc_candidate_min_separation_seconds": self.ncc_candidate_min_separation_seconds,
            "lag_cluster_tolerance_seconds": self.lag_cluster_tolerance_seconds,
            "mode_min_separation_seconds": self.mode_min_separation_seconds,
            "verification_span_seconds": self.verification_span_seconds,
            "verification_window_seconds": self.verification_window_seconds,
            "verification_step_seconds": self.verification_step_seconds,
        }
        for name, value in positive.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.ncc_top_k < 2:
            raise ValueError("ncc_top_k must be >= 2")
        if self.mode_min_support_centers < 2:
            raise ValueError("mode_min_support_centers must be >= 2")
        if self.verification_min_dual_count < 2:
            raise ValueError("verification_min_dual_count must be >= 2")
        for name in (
            "lag_candidate_min_score",
            "mode_min_median_ncc",
            "verification_min_r2",
            "verification_min_minor_share",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.handoff_max_gap_seconds < 0 or self.handoff_max_overlap_seconds < 0:
            raise ValueError("handoff gap/overlap allowances must be non-negative")


@dataclass(frozen=True)
class LagObservation:
    mix_center: float
    source_center: float
    offset: float
    score: float


@dataclass(frozen=True)
class LagMode:
    offset: float
    support_center_count: int
    first_mix_center: float
    last_mix_center: float
    median_score: float
    max_score: float

    def to_dict(self) -> dict:
        return {
            "offset_seconds": self.offset,
            "support_center_count": self.support_center_count,
            "first_mix_center": self.first_mix_center,
            "last_mix_center": self.last_mix_center,
            "median_score": self.median_score,
            "max_score": self.max_score,
        }


def _segment(array: np.ndarray, *, center_seconds: float, window_seconds: float, sample_rate: int) -> np.ndarray | None:
    half = int(round(window_seconds * sample_rate / 2.0))
    center = int(round(center_seconds * sample_rate))
    if half <= 0 or center - half < 0 or center + half > len(array):
        return None
    return np.asarray(array[center - half : center + half], dtype=np.float64)


def _ncc_candidates(
    stem: np.ndarray,
    mix: np.ndarray,
    *,
    mix_center: float,
    occurrence_start: float,
    sample_rate: int,
    config: PreparedStemConfig,
) -> list[LagObservation]:
    query = _segment(
        mix,
        center_seconds=mix_center,
        window_seconds=config.ncc_window_seconds,
        sample_rate=sample_rate,
    )
    if query is None:
        return []
    query = query - float(np.mean(query))
    query_energy = float(np.sum(query * query))
    if query_energy <= 1e-12:
        return []

    local_center = mix_center - occurrence_start
    source_low = max(
        config.ncc_window_seconds / 2.0,
        local_center - config.source_search_back_seconds,
    )
    source_high = min(
        len(stem) / sample_rate - config.ncc_window_seconds / 2.0,
        local_center + config.source_search_forward_seconds,
    )
    if source_high <= source_low:
        return []

    n = len(query)
    raw_start = max(
        0,
        int(round((source_low - config.ncc_window_seconds / 2.0) * sample_rate)),
    )
    raw_end = min(
        len(stem),
        int(round((source_high + config.ncc_window_seconds / 2.0) * sample_rate)),
    )
    search = np.asarray(stem[raw_start:raw_end], dtype=np.float64)
    if len(search) < n:
        return []

    cross = correlate(search, query, mode="valid", method="fft")
    cumulative = np.concatenate([[0.0], np.cumsum(search)])
    cumulative_sq = np.concatenate([[0.0], np.cumsum(search * search)])
    starts = np.arange(len(cross), dtype=np.int64)
    sums = cumulative[starts + n] - cumulative[starts]
    sums_sq = cumulative_sq[starts + n] - cumulative_sq[starts]
    energy = np.maximum(sums_sq - sums * sums / n, 1e-12)
    scores = cross / np.sqrt(energy * query_energy)

    stride = max(1, int(round(config.ncc_candidate_step_seconds * sample_rate)))
    sampled = np.arange(0, len(scores), stride, dtype=np.int64)
    order = sampled[np.argsort(scores[sampled])[::-1]]
    minimum_separation = int(
        round(config.ncc_candidate_min_separation_seconds * sample_rate)
    )
    chosen: list[int] = []
    result: list[LagObservation] = []
    for raw_position in order:
        position = int(raw_position)
        if any(abs(position - prior) < minimum_separation for prior in chosen):
            continue
        source_center = (raw_start + position + n / 2.0) / sample_rate
        result.append(
            LagObservation(
                mix_center=float(mix_center),
                source_center=float(source_center),
                offset=float(source_center - local_center),
                score=float(scores[position]),
            )
        )
        chosen.append(position)
        if len(result) >= config.ncc_top_k:
            break
    return result


def _cluster_modes(
    observations: Iterable[LagObservation], config: PreparedStemConfig
) -> list[LagMode]:
    rows = sorted(
        [row for row in observations if row.score >= config.lag_candidate_min_score],
        key=lambda row: row.offset,
    )
    clusters: list[list[LagObservation]] = []
    for row in rows:
        if not clusters:
            clusters.append([row])
            continue
        median = float(np.median([item.offset for item in clusters[-1]]))
        if abs(row.offset - median) > config.lag_cluster_tolerance_seconds:
            clusters.append([row])
        else:
            clusters[-1].append(row)

    modes: list[LagMode] = []
    for cluster in clusters:
        centers = sorted({round(item.mix_center, 6) for item in cluster})
        modes.append(
            LagMode(
                offset=float(np.median([item.offset for item in cluster])),
                support_center_count=len(centers),
                first_mix_center=float(min(centers)),
                last_mix_center=float(max(centers)),
                median_score=float(np.median([item.score for item in cluster])),
                max_score=float(max(item.score for item in cluster)),
            )
        )
    return sorted(
        modes,
        key=lambda mode: (mode.support_center_count, mode.median_score),
        reverse=True,
    )


def discover_lag_modes(
    mix: np.ndarray,
    stem: np.ndarray,
    *,
    sample_rate: int,
    occurrence_start: float,
    occurrence_end: float,
    config: PreparedStemConfig | None = None,
) -> list[LagMode]:
    config = config or PreparedStemConfig()
    config.validate()
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if occurrence_start < 0 or occurrence_end <= occurrence_start:
        raise ValueError("invalid occurrence interval")
    duration = len(mix) / sample_rate
    if occurrence_end > duration + 1e-9:
        raise ValueError("occurrence interval exceeds mix duration")

    half = config.ncc_window_seconds / 2.0
    first_center = max(occurrence_start + half, occurrence_start + config.scan_step_seconds)
    last_center = occurrence_end - half
    observations: list[LagObservation] = []
    center = first_center
    while center <= last_center + 1e-9:
        observations.extend(
            _ncc_candidates(
                stem,
                mix,
                mix_center=center,
                occurrence_start=occurrence_start,
                sample_rate=sample_rate,
                config=config,
            )
        )
        center += config.scan_step_seconds
    return _cluster_modes(observations, config)


def _mode_supported(mode: LagMode, config: PreparedStemConfig) -> bool:
    return (
        mode.support_center_count >= config.mode_min_support_centers
        and mode.median_score >= config.mode_min_median_ncc
    )


def _handoff_pair(
    modes: Iterable[LagMode], config: PreparedStemConfig
) -> tuple[LagMode, LagMode] | None:
    supported = [mode for mode in modes if _mode_supported(mode, config)]
    candidates: list[tuple[tuple[float, ...], LagMode, LagMode]] = []
    for first in supported:
        for second in supported:
            if first is second:
                continue
            if abs(second.offset - first.offset) < config.mode_min_separation_seconds:
                continue
            if second.first_mix_center <= first.first_mix_center:
                continue
            handoff_gap = second.first_mix_center - first.last_mix_center
            if handoff_gap > config.handoff_max_gap_seconds:
                continue
            if handoff_gap < -config.handoff_max_overlap_seconds:
                continue
            score = (
                min(first.support_center_count, second.support_center_count),
                min(first.median_score, second.median_score),
                -abs(handoff_gap),
                abs(second.offset - first.offset),
            )
            candidates.append((score, first, second))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, first, second = candidates[0]
    return first, second


def _fit_two_sources(
    mix_segment: np.ndarray, first: np.ndarray, second: np.ndarray
) -> tuple[float, list[float], list[float]]:
    n = min(len(mix_segment), len(first), len(second))
    y = np.asarray(mix_segment[:n], dtype=np.float64)
    a = np.asarray(first[:n], dtype=np.float64)
    b = np.asarray(second[:n], dtype=np.float64)
    matrix = np.column_stack([a, b, np.ones(n)])
    coefficients, *_ = np.linalg.lstsq(matrix, y, rcond=None)
    prediction = matrix @ coefficients
    residual = float(np.sum((y - prediction) ** 2))
    total = float(np.sum((y - float(np.mean(y))) ** 2))
    r2 = 1.0 - residual / total if total > 1e-12 else 0.0
    audio_coefficients = [float(coefficients[0]), float(coefficients[1])]
    positive = np.maximum(np.asarray(audio_coefficients, dtype=np.float64), 0.0)
    denominator = float(np.sum(positive))
    shares = (
        [float(value / denominator) for value in positive]
        if denominator > 1e-12
        else [0.0, 0.0]
    )
    return float(r2), audio_coefficients, shares


def diagnose_same_track_splice(
    mix: np.ndarray,
    stem: np.ndarray,
    *,
    sample_rate: int,
    occurrence_start: float,
    occurrence_end: float,
    config: PreparedStemConfig | None = None,
) -> dict:
    """Return diagnostic evidence; never mutate production state."""

    config = config or PreparedStemConfig()
    config.validate()
    modes = discover_lag_modes(
        mix,
        stem,
        sample_rate=sample_rate,
        occurrence_start=occurrence_start,
        occurrence_end=occurrence_end,
        config=config,
    )
    pair = _handoff_pair(modes, config)
    base = {
        "schema_version": "prepared-stem-splice-diagnostic-1.0",
        "diagnostic_only": True,
        "automatic_timing_change_allowed": False,
        "negative_result_is_clear_authority": False,
        "modes": [mode.to_dict() for mode in modes],
    }
    if pair is None:
        return {
            **base,
            "status": "inconclusive",
            "splice_supported": False,
            "reason": "no two supported lag modes form a local temporal handoff; this does not prove the absence of a splice",
        }

    first, second = pair
    handoff_center = (first.last_mix_center + second.first_mix_center) / 2.0
    verification_start = max(
        occurrence_start,
        handoff_center - config.verification_span_seconds / 2.0,
    )
    verification_end = min(
        occurrence_end,
        handoff_center + config.verification_span_seconds / 2.0,
    )
    rows: list[dict] = []
    center = verification_start + config.verification_window_seconds / 2.0
    last = verification_end - config.verification_window_seconds / 2.0
    while center <= last + 1e-9:
        mix_segment = _segment(
            mix,
            center_seconds=center,
            window_seconds=config.verification_window_seconds,
            sample_rate=sample_rate,
        )
        local_center = center - occurrence_start
        first_segment = _segment(
            stem,
            center_seconds=local_center + first.offset,
            window_seconds=config.verification_window_seconds,
            sample_rate=sample_rate,
        )
        second_segment = _segment(
            stem,
            center_seconds=local_center + second.offset,
            window_seconds=config.verification_window_seconds,
            sample_rate=sample_rate,
        )
        if mix_segment is not None and first_segment is not None and second_segment is not None:
            r2, coefficients, shares = _fit_two_sources(
                mix_segment, first_segment, second_segment
            )
            rows.append(
                {
                    "mix_center": round(float(center), 6),
                    "r2": float(r2),
                    "coefficients": coefficients,
                    "shares": shares,
                }
            )
        center += config.verification_step_seconds

    high_fit = [row for row in rows if row["r2"] >= config.verification_min_r2]
    dual = [
        row
        for row in high_fit
        if row["coefficients"][0] > 0.0
        and row["coefficients"][1] > 0.0
        and min(row["shares"]) >= config.verification_min_minor_share
    ]
    dual_span = (
        max(row["mix_center"] for row in dual) - min(row["mix_center"] for row in dual)
        if dual
        else 0.0
    )
    supported = (
        len(dual) >= config.verification_min_dual_count
        and dual_span >= config.verification_min_dual_span_seconds
    )
    crossover = (
        min(dual, key=lambda row: abs(row["shares"][0] - row["shares"][1]))
        if supported
        else None
    )
    return {
        **base,
        "status": "splice_supported" if supported else "inconclusive",
        "splice_supported": bool(supported),
        "lag_pair_seconds": [first.offset, second.offset],
        "handoff_center": handoff_center,
        "verification": {
            "high_fit_count": len(high_fit),
            "dual_positive_count": len(dual),
            "dual_positive_span_seconds": float(dual_span),
            "max_r2": max((row["r2"] for row in rows), default=None),
            "max_minor_share_high_fit": max(
                (min(row["shares"]) for row in high_fit), default=0.0
            ),
        },
        "crossover": (
            None
            if crossover is None
            else {
                "mix_time_seconds": float(crossover["mix_center"]),
                "r2": float(crossover["r2"]),
                "shares": [float(value) for value in crossover["shares"]],
            }
        ),
    }
