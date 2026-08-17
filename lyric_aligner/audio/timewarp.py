"""Affine-first and continuous piecewise-rate Source-to-Mix mapping."""

from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

import numpy as np


@dataclass(frozen=True)
class AlignmentAnchor:
    mix_time: float
    source_time: float
    confidence: float = 1.0
    feature_scores: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class MappingDiagnostics:
    anchor_count: int
    inlier_count: int
    coverage: float
    median_residual: float
    p95_residual: float
    drift_early: float
    drift_middle: float
    drift_late: float
    drift_span: float
    feature_agreement: dict[str, float]
    independent_feature_count: int
    bpm_prior: float | None
    bpm_prior_delta: float | None


@dataclass(frozen=True)
class TimeWarpModel:
    mode: str
    intercept: float
    base_slope: float
    breakpoints: tuple[float, ...]
    slope_deltas: tuple[float, ...]
    diagnostics: MappingDiagnostics
    objective: float

    def source_time(self, mix_time: float) -> float:
        value = self.intercept + self.base_slope * mix_time
        for breakpoint, delta in zip(self.breakpoints, self.slope_deltas):
            value += delta * max(0.0, mix_time - breakpoint)
        return value

    def local_slope(self, mix_time: float) -> float:
        value = self.base_slope
        for breakpoint, delta in zip(self.breakpoints, self.slope_deltas):
            if mix_time >= breakpoint:
                value += delta
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "intercept": self.intercept,
            "base_slope": self.base_slope,
            "breakpoints": list(self.breakpoints),
            "slope_deltas": list(self.slope_deltas),
            "diagnostics": asdict(self.diagnostics),
            "objective": self.objective,
        }


@dataclass(frozen=True)
class DiscontinuityCandidate:
    type: str
    status: str
    mix_before: float
    mix_after: float
    source_before: float
    source_after: float
    observed_rate: float
    excess_source_jump: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ordered(anchors: Iterable[AlignmentAnchor]) -> list[AlignmentAnchor]:
    rows = sorted(anchors, key=lambda item: item.mix_time)
    if len(rows) < 3:
        raise ValueError("TimeWarp requires at least three anchors")
    if any(right.mix_time <= left.mix_time for left, right in zip(rows, rows[1:])):
        raise ValueError("mix anchor times must be strictly increasing")
    return rows


def _design_matrix(mix: np.ndarray, breakpoints: tuple[float, ...]) -> np.ndarray:
    columns = [np.ones_like(mix), mix]
    columns.extend(np.maximum(0.0, mix - breakpoint) for breakpoint in breakpoints)
    return np.column_stack(columns)


def _weighted_lstsq(
    matrix: np.ndarray,
    source: np.ndarray,
    weights: np.ndarray,
    *,
    bpm_prior: float | None,
    bpm_prior_strength: float,
) -> np.ndarray:
    left = matrix * np.sqrt(weights)[:, None]
    right = source * np.sqrt(weights)
    if bpm_prior is not None and bpm_prior_strength > 0:
        prior = np.zeros(matrix.shape[1], dtype=np.float64)
        prior[1] = 1.0
        left = np.vstack([left, math.sqrt(bpm_prior_strength) * prior])
        right = np.append(right, math.sqrt(bpm_prior_strength) * bpm_prior)
    return np.linalg.lstsq(left, right, rcond=None)[0]


def _robust_coefficients(
    rows: list[AlignmentAnchor],
    *,
    breakpoints: tuple[float, ...] = (),
    bpm_prior: float | None,
    bpm_prior_strength: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mix = np.asarray([row.mix_time for row in rows], dtype=np.float64)
    source = np.asarray([row.source_time for row in rows], dtype=np.float64)
    base_weights = np.asarray(
        [max(0.05, min(1.0, row.confidence)) for row in rows], dtype=np.float64
    )
    matrix = _design_matrix(mix, breakpoints)
    weights = base_weights.copy()
    coefficients = _weighted_lstsq(
        matrix,
        source,
        weights,
        bpm_prior=bpm_prior,
        bpm_prior_strength=bpm_prior_strength,
    )
    for _ in range(4):
        residual = source - matrix @ coefficients
        scale = max(1e-6, float(np.median(np.abs(residual))))
        robust = 1.0 / np.maximum(1.0, np.abs(residual) / (2.5 * scale))
        weights = base_weights * robust
        coefficients = _weighted_lstsq(
            matrix,
            source,
            weights,
            bpm_prior=bpm_prior,
            bpm_prior_strength=bpm_prior_strength,
        )
    residual = source - matrix @ coefficients
    scale = max(1e-6, float(np.median(np.abs(residual))))
    inliers = np.abs(residual) <= max(0.35, 3.5 * scale)
    return coefficients, residual, inliers


def _bucket_mean(values: np.ndarray, start: int, end: int) -> float:
    subset = values[start:end]
    return float(np.mean(subset)) if subset.size else 0.0


def _feature_agreement(rows: list[AlignmentAnchor]) -> tuple[dict[str, float], int]:
    names = sorted({name for row in rows for name in row.feature_scores})
    summary: dict[str, float] = {}
    for name in names:
        values = [float(row.feature_scores[name]) for row in rows if name in row.feature_scores]
        if values:
            summary[name] = float(np.mean(values))
    independent = sum(value >= 0.55 for value in summary.values())
    return summary, independent


def _diagnostics(
    rows: list[AlignmentAnchor],
    residual: np.ndarray,
    inliers: np.ndarray,
    *,
    slope: float,
    bpm_prior: float | None,
) -> MappingDiagnostics:
    absolute = np.abs(residual)
    count = len(rows)
    third = max(1, count // 3)
    early = _bucket_mean(residual, 0, third)
    middle = _bucket_mean(residual, third, min(count, third * 2))
    late = _bucket_mean(residual, min(count, third * 2), count)
    agreement, independent = _feature_agreement(rows)
    return MappingDiagnostics(
        anchor_count=count,
        inlier_count=int(np.sum(inliers)),
        coverage=float(np.mean(inliers)),
        median_residual=float(np.median(absolute)),
        p95_residual=float(np.percentile(absolute, 95)),
        drift_early=early,
        drift_middle=middle,
        drift_late=late,
        drift_span=max(early, middle, late) - min(early, middle, late),
        feature_agreement=agreement,
        independent_feature_count=independent,
        bpm_prior=bpm_prior,
        bpm_prior_delta=None if bpm_prior is None else abs(slope - bpm_prior),
    )


def _objective(diagnostics: MappingDiagnostics, breakpoint_count: int, complexity_penalty: float) -> float:
    return (
        diagnostics.median_residual
        + 0.30 * diagnostics.p95_residual
        + 0.20 * diagnostics.drift_span
        + complexity_penalty * breakpoint_count
    )


def fit_affine(
    anchors: Iterable[AlignmentAnchor],
    *,
    bpm_prior: float | None = None,
    bpm_prior_strength: float = 0.02,
) -> TimeWarpModel:
    rows = _ordered(anchors)
    coefficients, residual, inliers = _robust_coefficients(
        rows,
        bpm_prior=bpm_prior,
        bpm_prior_strength=bpm_prior_strength,
    )
    intercept, slope = (float(value) for value in coefficients[:2])
    diagnostics = _diagnostics(rows, residual, inliers, slope=slope, bpm_prior=bpm_prior)
    return TimeWarpModel(
        mode="AFFINE",
        intercept=intercept,
        base_slope=slope,
        breakpoints=(),
        slope_deltas=(),
        diagnostics=diagnostics,
        objective=_objective(diagnostics, 0, 0.0),
    )


def _candidate_breakpoints(rows: list[AlignmentAnchor], min_segment_points: int) -> list[float]:
    return [
        rows[index].mix_time
        for index in range(min_segment_points, len(rows) - min_segment_points + 1)
    ]


def fit_piecewise_rate(
    anchors: Iterable[AlignmentAnchor],
    *,
    bpm_prior: float | None = None,
    bpm_prior_strength: float = 0.02,
    max_breakpoints: int = 2,
    min_segment_points: int = 3,
    complexity_penalty: float = 0.035,
) -> TimeWarpModel | None:
    rows = _ordered(anchors)
    candidates = _candidate_breakpoints(rows, min_segment_points)
    best: TimeWarpModel | None = None
    maximum = min(max_breakpoints, 2, max(0, len(candidates)))
    for count in range(1, maximum + 1):
        for breaks in itertools.combinations(candidates, count):
            boundaries = (-math.inf, *breaks, math.inf)
            segment_counts = [
                sum(boundaries[index] <= row.mix_time < boundaries[index + 1] for row in rows)
                for index in range(len(boundaries) - 1)
            ]
            if min(segment_counts) < min_segment_points:
                continue
            coefficients, residual, inliers = _robust_coefficients(
                rows,
                breakpoints=tuple(breaks),
                bpm_prior=bpm_prior,
                bpm_prior_strength=bpm_prior_strength,
            )
            intercept = float(coefficients[0])
            base_slope = float(coefficients[1])
            deltas = tuple(float(value) for value in coefficients[2:])
            slopes = [base_slope]
            for delta in deltas:
                slopes.append(slopes[-1] + delta)
            if any(slope <= 0 for slope in slopes):
                continue
            diagnostics = _diagnostics(
                rows, residual, inliers, slope=base_slope, bpm_prior=bpm_prior
            )
            model = TimeWarpModel(
                mode="PIECEWISE_RATE",
                intercept=intercept,
                base_slope=base_slope,
                breakpoints=tuple(float(value) for value in breaks),
                slope_deltas=deltas,
                diagnostics=diagnostics,
                objective=_objective(diagnostics, count, complexity_penalty),
            )
            if best is None or model.objective < best.objective:
                best = model
    return best


def detect_discontinuities(
    anchors: Iterable[AlignmentAnchor],
    *,
    middle_cut: str = "false",
    max_continuous_rate: float = 2.0,
    min_excess_source_jump: float = 1.5,
) -> list[DiscontinuityCandidate]:
    rows = _ordered(anchors)
    if middle_cut not in {"false", "true", "unknown"}:
        raise ValueError("middle_cut must be false, true, or unknown")
    candidates: list[DiscontinuityCandidate] = []
    for left, right in zip(rows, rows[1:]):
        mix_delta = right.mix_time - left.mix_time
        source_delta = right.source_time - left.source_time
        if source_delta < 0:
            candidates.append(
                DiscontinuityCandidate(
                    type="backward_source_jump",
                    status="block",
                    mix_before=left.mix_time,
                    mix_after=right.mix_time,
                    source_before=left.source_time,
                    source_after=right.source_time,
                    observed_rate=source_delta / mix_delta,
                    excess_source_jump=abs(source_delta),
                    reason="source timeline moved backward; reorder is not enabled by default",
                )
            )
            continue
        observed_rate = source_delta / mix_delta
        excess = source_delta - max_continuous_rate * mix_delta
        if observed_rate <= max_continuous_rate or excess < min_excess_source_jump:
            continue
        if middle_cut == "false":
            kind = "unexpected_middle_discontinuity"
            status = "block"
        elif middle_cut == "true":
            kind = "declared_middle_cut_candidate"
            status = "review"
        else:
            kind = "possible_middle_cut_candidate"
            status = "review"
        candidates.append(
            DiscontinuityCandidate(
                type=kind,
                status=status,
                mix_before=left.mix_time,
                mix_after=right.mix_time,
                source_before=left.source_time,
                source_after=right.source_time,
                observed_rate=observed_rate,
                excess_source_jump=excess,
                reason="source position jump exceeds the allowed continuous-rate envelope; never auto-confirm a cut",
            )
        )
    return candidates


def select_timewarp(
    anchors: Iterable[AlignmentAnchor],
    *,
    bpm_prior: float | None = None,
    bpm_prior_strength: float = 0.02,
    middle_cut: str = "false",
    max_continuous_rate: float = 2.0,
    min_excess_source_jump: float = 1.5,
    min_piecewise_improvement: float = 0.25,
    minimum_feature_families: int = 2,
    drift_threshold: float = 0.30,
    residual_threshold: float = 0.25,
    complexity_penalty: float = 0.035,
) -> dict[str, Any]:
    rows = _ordered(anchors)
    discontinuities = detect_discontinuities(
        rows,
        middle_cut=middle_cut,
        max_continuous_rate=max_continuous_rate,
        min_excess_source_jump=min_excess_source_jump,
    )
    affine = fit_affine(
        rows,
        bpm_prior=bpm_prior,
        bpm_prior_strength=bpm_prior_strength,
    )

    if discontinuities:
        return {
            "mapping": affine.to_dict(),
            "selection": "AFFINE_WITH_DISCONTINUITY_REVIEW",
            "escalated": False,
            "discontinuities": [item.to_dict() for item in discontinuities],
            "blocked": True,
        }

    needs_complexity = (
        affine.diagnostics.median_residual >= residual_threshold
        or affine.diagnostics.drift_span >= drift_threshold
        or affine.diagnostics.coverage < 0.85
    )
    if not needs_complexity:
        return {
            "mapping": affine.to_dict(),
            "selection": "AFFINE_ACCEPTED",
            "escalated": False,
            "discontinuities": [],
            "blocked": False,
        }

    piecewise = fit_piecewise_rate(
        rows,
        bpm_prior=bpm_prior,
        bpm_prior_strength=bpm_prior_strength,
        complexity_penalty=complexity_penalty,
    )
    if piecewise is None:
        return {
            "mapping": affine.to_dict(),
            "selection": "AFFINE_UNRESOLVED",
            "escalated": False,
            "discontinuities": [],
            "blocked": True,
        }

    denominator = max(affine.objective, 1e-9)
    improvement = (affine.objective - piecewise.objective) / denominator
    independently_supported = (
        piecewise.diagnostics.independent_feature_count >= minimum_feature_families
    )
    if improvement >= min_piecewise_improvement and independently_supported:
        return {
            "mapping": piecewise.to_dict(),
            "selection": "PIECEWISE_RATE_ACCEPTED",
            "escalated": True,
            "improvement": improvement,
            "discontinuities": [],
            "blocked": False,
        }
    return {
        "mapping": affine.to_dict(),
        "candidate_mapping": piecewise.to_dict(),
        "selection": "PIECEWISE_RATE_NOT_JUSTIFIED",
        "escalated": False,
        "improvement": improvement,
        "discontinuities": [],
        "blocked": True,
        "reason": (
            "piecewise improvement or independent feature support is insufficient; "
            "do not increase model complexity just to reduce residual"
        ),
    }
