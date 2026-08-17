"""Affine-first Source-to-Mix TimeWarp selection.

This module operates on already-produced audio anchors. Feature extraction and
candidate retrieval are separate concerns: this layer decides whether one
continuous affine rate explains a track, whether a continuous piecewise-rate
model is justified, and whether the anchor path contains a source-position
jump that must be reviewed as a possible middle cut.
"""

from __future__ import annotations

import itertools
import math
import statistics
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
    early_drift: float
    middle_drift: float
    late_drift: float
    drift_span: float
    independent_feature_count: int
    feature_agreement: float
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

    def predict(self, mix_time: float) -> float:
        value = self.intercept + self.base_slope * mix_time
        for breakpoint, delta in zip(self.breakpoints, self.slope_deltas):
            value += delta * max(0.0, mix_time - breakpoint)
        return value

    @property
    def segment_slopes(self) -> tuple[float, ...]:
        slopes = [self.base_slope]
        current = self.base_slope
        for delta in self.slope_deltas:
            current += delta
            slopes.append(current)
        return tuple(slopes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "intercept": self.intercept,
            "base_slope": self.base_slope,
            "breakpoints": list(self.breakpoints),
            "slope_deltas": list(self.slope_deltas),
            "segment_slopes": list(self.segment_slopes),
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


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    return float(np.quantile(np.asarray(values, dtype=np.float64), fraction))


def _ordered(anchors: Iterable[AlignmentAnchor]) -> list[AlignmentAnchor]:
    rows = sorted(anchors, key=lambda item: item.mix_time)
    if len(rows) < 2:
        raise ValueError("TimeWarp requires at least two anchors")
    if any(right.mix_time <= left.mix_time for left, right in zip(rows, rows[1:])):
        raise ValueError("anchor mix_time values must be strictly increasing")
    return rows


def _design_matrix(x: np.ndarray, breakpoints: tuple[float, ...]) -> np.ndarray:
    columns = [np.ones_like(x), x]
    columns.extend(np.maximum(0.0, x - value) for value in breakpoints)
    return np.column_stack(columns)


def _weighted_fit(
    rows: list[AlignmentAnchor],
    *,
    breakpoints: tuple[float, ...] = (),
    bpm_prior: float | None = None,
    bpm_prior_strength: float = 0.02,
    mask: np.ndarray | None = None,
) -> np.ndarray:
    x = np.asarray([row.mix_time for row in rows], dtype=np.float64)
    y = np.asarray([row.source_time for row in rows], dtype=np.float64)
    design = _design_matrix(x, breakpoints)
    weights = np.asarray([max(0.05, row.confidence) for row in rows], dtype=np.float64)
    if mask is not None:
        weights = weights * mask.astype(np.float64)

    weighted_design = design * np.sqrt(weights)[:, None]
    weighted_y = y * np.sqrt(weights)

    if bpm_prior is not None and bpm_prior_strength > 0:
        # Soft slope regularization only. Scale by actual data energy so the
        # prior can break weak ties but cannot lock the fitted slope.
        data_energy = float(np.sum(weights * (x - np.average(x, weights=weights)) ** 2))
        lam = max(0.0, bpm_prior_strength) * max(data_energy, 1.0)
        prior_row = np.zeros(design.shape[1], dtype=np.float64)
        prior_row[1] = math.sqrt(lam)
        weighted_design = np.vstack([weighted_design, prior_row])
        weighted_y = np.concatenate([weighted_y, [math.sqrt(lam) * bpm_prior]])

    coefficients, *_ = np.linalg.lstsq(weighted_design, weighted_y, rcond=None)
    return coefficients


def _predict_array(
    rows: list[AlignmentAnchor], coefficients: np.ndarray, breakpoints: tuple[float, ...]
) -> np.ndarray:
    x = np.asarray([row.mix_time for row in rows], dtype=np.float64)
    return _design_matrix(x, breakpoints) @ coefficients


def _robust_coefficients(
    rows: list[AlignmentAnchor],
    *,
    breakpoints: tuple[float, ...] = (),
    bpm_prior: float | None = None,
    bpm_prior_strength: float = 0.02,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    coefficients = _weighted_fit(
        rows,
        breakpoints=breakpoints,
        bpm_prior=bpm_prior,
        bpm_prior_strength=bpm_prior_strength,
    )
    predicted = _predict_array(rows, coefficients, breakpoints)
    actual = np.asarray([row.source_time for row in rows], dtype=np.float64)
    residual = actual - predicted
    median = float(np.median(residual))
    mad = float(np.median(np.abs(residual - median)))
    threshold = max(0.20, 3.5 * 1.4826 * mad)
    inliers = np.abs(residual - median) <= threshold
    minimum = max(2 + len(breakpoints), math.ceil(len(rows) * 0.55))
    if int(np.sum(inliers)) >= minimum and not bool(np.all(inliers)):
        coefficients = _weighted_fit(
            rows,
            breakpoints=breakpoints,
            bpm_prior=bpm_prior,
            bpm_prior_strength=bpm_prior_strength,
            mask=inliers,
        )
        predicted = _predict_array(rows, coefficients, breakpoints)
        residual = actual - predicted
    else:
        inliers = np.ones(len(rows), dtype=bool)
    return coefficients, residual, inliers


def _drift_regions(residuals: list[float]) -> tuple[float, float, float, float]:
    chunks = np.array_split(np.asarray(residuals, dtype=np.float64), 3)
    medians = [float(np.median(chunk)) if len(chunk) else 0.0 for chunk in chunks]
    return medians[0], medians[1], medians[2], max(medians) - min(medians)


def _feature_summary(rows: list[AlignmentAnchor], threshold: float = 0.50) -> tuple[int, float]:
    families = sorted({name for row in rows for name in row.feature_scores})
    supported = 0
    agreements: list[float] = []
    for family in families:
        values = [row.feature_scores.get(family, 0.0) for row in rows]
        support_ratio = sum(value >= threshold for value in values) / len(rows)
        if support_ratio >= 0.60:
            supported += 1
        agreements.append(support_ratio)
    return supported, (statistics.fmean(agreements) if agreements else 1.0)


def _diagnostics(
    rows: list[AlignmentAnchor],
    residual: np.ndarray,
    inliers: np.ndarray,
    *,
    slope: float,
    bpm_prior: float | None,
) -> MappingDiagnostics:
    abs_residual = [abs(float(value)) for value in residual]
    early, middle, late, drift_span = _drift_regions([float(value) for value in residual])
    feature_count, feature_agreement = _feature_summary(rows)
    return MappingDiagnostics(
        anchor_count=len(rows),
        inlier_count=int(np.sum(inliers)),
        coverage=float(np.sum(inliers) / len(rows)),
        median_residual=float(statistics.median(abs_residual)),
        p95_residual=_percentile(abs_residual, 0.95),
        early_drift=early,
        middle_drift=middle,
        late_drift=late,
        drift_span=drift_span,
        independent_feature_count=feature_count,
        feature_agreement=float(feature_agreement),
        bpm_prior=bpm_prior,
        bpm_prior_delta=(abs(slope - bpm_prior) if bpm_prior is not None else None),
    )


def _objective(diagnostics: MappingDiagnostics, breakpoint_count: int, complexity_penalty: float) -> float:
    return (
        diagnostics.median_residual
        + 0.30 * diagnostics.p95_residual
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
    min_piecewise_improvement: float = 0.25,
    minimum_feature_families: int = 2,
    drift_threshold: float = 0.30,
    residual_threshold: float = 0.25,
    complexity_penalty: float = 0.035,
) -> dict[str, Any]:
    rows = _ordered(anchors)
    discontinuities = detect_discontinuities(rows, middle_cut=middle_cut)
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
