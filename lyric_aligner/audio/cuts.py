"""Confirmed middle-cut localization and discontinuous Source-to-Mix reconstruction.

A cut is not represented by forcing the continuous TimeWarp to fit across a
source-position jump. After a discontinuity is human-confirmed, this module
locates the local mix switch with harmonic Chroma/MFCC evidence, then fits one
continuous TimeWarp segment on each retained side. The resulting serialized
mapping contains explicit source gaps and can therefore omit cut lyrics without
pretending source time is continuous.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import librosa
import numpy as np

from lyric_aligner.audio.coarse_mapper import retrieve_coarse_window
from lyric_aligner.audio.features import extract_harmonic_features
from lyric_aligner.audio.timewarp import AlignmentAnchor, select_timewarp
from lyric_aligner.config import CutBoundaryConfig, TimeWarpConfig
from lyric_aligner.timeline.projector import source_time_at_mix


class CutRebuildError(ValueError):
    """Raised when a confirmed discontinuity cannot be rebuilt safely."""


@dataclass(frozen=True)
class LocalizedCutBoundary:
    candidate_id: str
    issue_id: str
    cut_mix_time: float
    localized_source_gap_start: float
    localized_source_gap_end: float
    left_score: float
    right_score: float
    left_margin: float
    right_margin: float
    boundary_margin: float
    left_feature_agreement: int
    right_feature_agreement: int
    search_start: float
    search_end: float

    @property
    def source_gap_seconds(self) -> float:
        return self.localized_source_gap_end - self.localized_source_gap_start

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "source_gap_seconds": self.source_gap_seconds}


def discontinuity_candidate_id(occurrence_id: str, candidate: dict[str, Any]) -> str:
    """Stable identity for one physical source-position jump candidate."""

    try:
        core = {
            "occurrence_id": str(occurrence_id),
            "mix_before_ms": int(round(float(candidate["mix_before"]) * 1000.0)),
            "mix_after_ms": int(round(float(candidate["mix_after"]) * 1000.0)),
            "source_before_ms": int(round(float(candidate["source_before"]) * 1000.0)),
            "source_after_ms": int(round(float(candidate["source_after"]) * 1000.0)),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise CutRebuildError("invalid discontinuity candidate") from exc
    if not core["occurrence_id"]:
        raise CutRebuildError("discontinuity candidate requires occurrence_id")
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _slope_grid(center: float, config: CutBoundaryConfig) -> tuple[float, ...]:
    low = max(0.20, center - config.slope_radius)
    high = center + config.slope_radius
    values = np.arange(low, high + config.slope_step * 0.5, config.slope_step)
    if not len(values):
        return (max(0.20, center),)
    return tuple(float(value) for value in values)


def _point_slope(point: dict[str, Any]) -> float:
    for key in ("refined_slope", "estimated_slope"):
        try:
            value = float(point[key])
        except (KeyError, TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 1.0


def path_points(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one serialized alignment path from a Coarse/Fine payload."""

    result = payload.get("result", payload)
    path = result.get("path")
    if not isinstance(path, list) or not path:
        raise CutRebuildError("alignment payload has no path")
    return [dict(row) for row in path]


def effective_path(
    coarse_payload: dict[str, Any], fine_payload: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Use Fine path only when Fine actually applied; otherwise preserve Coarse."""

    if fine_payload is not None:
        fine = fine_payload.get("result", {})
        if bool(fine.get("applied")) and isinstance(fine.get("path"), list):
            return path_points(fine_payload)
    return path_points(coarse_payload)


def _anchor_values(
    point: dict[str, Any],
) -> tuple[float, float, float, dict[str, float]]:
    try:
        mix = float(point["mix_center"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CutRebuildError("alignment path point is missing mix_center") from exc
    source = None
    for key in ("refined_source_center", "source_center"):
        try:
            source = float(point[key])
            break
        except (KeyError, TypeError, ValueError):
            continue
    if source is None:
        raise CutRebuildError("alignment path point is missing source center")
    confidence = 0.0
    for key in ("refined_score", "fused_score"):
        try:
            confidence = max(confidence, float(point[key]))
        except (KeyError, TypeError, ValueError):
            pass
    scores_raw = point.get("feature_scores")
    scores = (
        {str(key): float(value) for key, value in scores_raw.items()}
        if isinstance(scores_raw, dict)
        else {}
    )
    if not scores and confidence > 0:
        scores = {"alignment": confidence, "secondary": confidence}
    return mix, source, max(1e-6, confidence), scores


def alignment_anchors(path: Sequence[dict[str, Any]]) -> list[AlignmentAnchor]:
    anchors: list[AlignmentAnchor] = []
    for point in path:
        mix, source, confidence, scores = _anchor_values(point)
        anchors.append(
            AlignmentAnchor(
                mix_time=mix,
                source_time=source,
                confidence=confidence,
                feature_scores=scores,
            )
        )
    anchors.sort(key=lambda row: row.mix_time)
    if len(anchors) < 3:
        raise CutRebuildError("cut rebuild requires at least three alignment anchors")
    return anchors


def _neighbor_points(
    path: Sequence[dict[str, Any]], mix_before: float, mix_after: float
) -> tuple[dict[str, Any], dict[str, Any]]:
    ordered = sorted(path, key=lambda row: float(row.get("mix_center", -1.0)))
    left = [
        row
        for row in ordered
        if float(row.get("mix_center", -1.0)) <= mix_before + 1e-6
    ]
    right = [
        row
        for row in ordered
        if float(row.get("mix_center", 1e18)) >= mix_after - 1e-6
    ]
    if not left or not right:
        raise CutRebuildError(
            "cannot find alignment anchors around confirmed discontinuity"
        )
    return left[-1], right[0]


def _candidate_times(start: float, end: float, step: float) -> list[float]:
    if end <= start or step <= 0:
        raise CutRebuildError("invalid cut-boundary search interval")
    values = np.arange(start, end + step * 0.5, step)
    return [float(value) for value in values if start <= value <= end]


def locate_cut_boundary(
    *,
    mix_audio: Path,
    source_audio: Path,
    candidate_id: str,
    issue_id: str,
    discontinuity: dict[str, Any],
    effective_alignment_path: Sequence[dict[str, Any]],
    config: CutBoundaryConfig,
) -> LocalizedCutBoundary:
    """Localize the mix switch inside a confirmed coarse discontinuity interval."""

    if not candidate_id or not issue_id:
        raise CutRebuildError("cut localization requires candidate_id and issue_id")
    try:
        mix_before = float(discontinuity["mix_before"])
        mix_after = float(discontinuity["mix_after"])
        source_before = float(discontinuity["source_before"])
        source_after = float(discontinuity["source_after"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CutRebuildError("confirmed discontinuity is malformed") from exc
    if mix_after <= mix_before or source_after <= source_before:
        raise CutRebuildError("confirmed discontinuity has invalid direction/order")

    left_point, right_point = _neighbor_points(
        effective_alignment_path, mix_before, mix_after
    )
    left_slope = _point_slope(left_point)
    right_slope = _point_slope(right_point)

    mix_duration = float(librosa.get_duration(path=str(mix_audio)))
    local_start = max(0.0, mix_before - config.context_seconds)
    local_end = min(mix_duration, mix_after + config.context_seconds)
    if local_end - local_start < config.context_seconds * 2.0:
        raise CutRebuildError(
            "confirmed cut is too close to mix edge for local boundary evidence"
        )

    mix_audio_data, _ = librosa.load(
        mix_audio,
        sr=config.sr,
        mono=True,
        offset=local_start,
        duration=local_end - local_start,
    )
    source_audio_data, _ = librosa.load(source_audio, sr=config.sr, mono=True)
    mix_features = extract_harmonic_features(
        mix_audio_data,
        sr=config.sr,
        hop_length=config.hop_length,
        offset_seconds=local_start,
    )
    source_features = extract_harmonic_features(
        source_audio_data,
        sr=config.sr,
        hop_length=config.hop_length,
        offset_seconds=0.0,
    )

    scored: list[dict[str, Any]] = []
    for cut_time in _candidate_times(
        mix_before, mix_after, config.candidate_step_seconds
    ):
        left_start = cut_time - config.context_seconds
        right_end = cut_time + config.context_seconds
        if left_start < local_start or right_end > local_end:
            continue
        left_query = mix_features.slice_absolute_window(left_start, cut_time)
        right_query = mix_features.slice_absolute_window(cut_time, right_end)
        if left_query.frame_count < 4 or right_query.frame_count < 4:
            continue

        expected_left_end = source_before + left_slope * (cut_time - mix_before)
        expected_left_start = (
            expected_left_end - config.context_seconds * left_slope
        )
        expected_right_start = source_after - right_slope * (mix_after - cut_time)
        left_search_start = max(
            0.0, expected_left_start - config.source_radius_seconds
        )
        left_search_end = min(
            source_features.end_time,
            expected_left_start
            + config.context_seconds * left_slope
            + config.source_radius_seconds,
        )
        right_search_start = max(
            0.0, expected_right_start - config.source_radius_seconds
        )
        right_search_end = min(
            source_features.end_time,
            expected_right_start
            + config.context_seconds * right_slope
            + config.source_radius_seconds,
        )
        try:
            left_result = retrieve_coarse_window(
                left_query,
                source_features,
                slope_grid=_slope_grid(left_slope, config),
                source_search_start=left_search_start,
                source_search_end=left_search_end,
                candidate_step_seconds=config.candidate_step_seconds,
                top_k=4,
                min_margin=config.min_side_margin,
            )
            right_result = retrieve_coarse_window(
                right_query,
                source_features,
                slope_grid=_slope_grid(right_slope, config),
                source_search_start=right_search_start,
                source_search_end=right_search_end,
                candidate_step_seconds=config.candidate_step_seconds,
                top_k=4,
                min_margin=config.min_side_margin,
            )
        except ValueError:
            continue
        left_top = left_result.candidates[0]
        right_top = right_result.candidates[0]
        if (
            left_top.fused_score < config.min_side_score
            or right_top.fused_score < config.min_side_score
            or left_top.feature_agreement < config.minimum_feature_agreement
            or right_top.feature_agreement < config.minimum_feature_agreement
            or left_result.margin < config.min_side_margin
            or right_result.margin < config.min_side_margin
            or left_result.ambiguous
            or right_result.ambiguous
        ):
            continue
        scored.append(
            {
                "cut_time": cut_time,
                "joint": min(left_top.fused_score, right_top.fused_score),
                "left": left_top,
                "right": right_top,
                "left_margin": left_result.margin,
                "right_margin": right_result.margin,
            }
        )

    if not scored:
        raise CutRebuildError(
            "no cut boundary candidate has strong two-sided harmonic evidence"
        )
    scored.sort(
        key=lambda row: (
            row["joint"], row["left_margin"] + row["right_margin"]
        ),
        reverse=True,
    )
    best = scored[0]
    separated = [
        row
        for row in scored[1:]
        if abs(float(row["cut_time"]) - float(best["cut_time"]))
        >= max(0.15, config.candidate_step_seconds * 3.0)
    ]
    second_joint = float(separated[0]["joint"]) if separated else 0.0
    boundary_margin = float(best["joint"]) - second_joint
    if boundary_margin < config.min_boundary_margin:
        raise CutRebuildError(
            "cut boundary localization is ambiguous: "
            f"margin={boundary_margin:.4f} < {config.min_boundary_margin:.4f}"
        )

    left_top = best["left"]
    right_top = best["right"]
    gap_start = float(left_top.source_end)
    gap_end = float(right_top.source_start)
    if gap_end - gap_start < config.min_source_gap_seconds:
        raise CutRebuildError(
            "localized source gap is too small/non-forward for a confirmed middle cut"
        )
    return LocalizedCutBoundary(
        candidate_id=candidate_id,
        issue_id=issue_id,
        cut_mix_time=float(best["cut_time"]),
        localized_source_gap_start=gap_start,
        localized_source_gap_end=gap_end,
        left_score=float(left_top.fused_score),
        right_score=float(right_top.fused_score),
        left_margin=float(best["left_margin"]),
        right_margin=float(best["right_margin"]),
        boundary_margin=boundary_margin,
        left_feature_agreement=int(left_top.feature_agreement),
        right_feature_agreement=int(right_top.feature_agreement),
        search_start=mix_before,
        search_end=mix_after,
    )


def _synthetic_boundary_anchor(
    mix_time: float, source_time: float, confidence: float
) -> AlignmentAnchor:
    confidence = max(0.50, min(1.0, float(confidence)))
    return AlignmentAnchor(
        mix_time=float(mix_time),
        source_time=float(source_time),
        confidence=confidence,
        feature_scores={"chroma": confidence, "mfcc": confidence},
    )


def _select_segment_timewarp(
    anchors: Sequence[AlignmentAnchor],
    *,
    slope_prior: float | None,
    config: TimeWarpConfig,
) -> dict[str, Any]:
    """Call the repository's current TimeWarp API with the versioned profile."""

    return select_timewarp(
        anchors,
        bpm_prior=slope_prior,
        bpm_prior_strength=config.bpm_prior_strength,
        middle_cut="false",
        max_continuous_rate=config.max_continuous_rate,
        min_excess_source_jump=config.min_excess_source_jump,
        min_piecewise_improvement=config.min_piecewise_improvement,
        minimum_feature_families=config.minimum_feature_families,
        drift_threshold=config.drift_threshold,
        residual_threshold=config.residual_threshold,
        complexity_penalty=config.complexity_penalty,
    )


def _deduplicate_segment_anchors(
    anchors: Sequence[AlignmentAnchor],
    *,
    left_edge: float,
    right_edge: float,
    left_boundary_source: float | None,
    right_boundary_source: float | None,
) -> list[AlignmentAnchor]:
    """Keep exactly one source hypothesis per mix timestamp inside one segment."""

    by_mix: dict[int, AlignmentAnchor] = {}
    for anchor in anchors:
        mix_ms = int(round(anchor.mix_time * 1000.0))
        existing = by_mix.get(mix_ms)
        if existing is None or anchor.confidence > existing.confidence:
            by_mix[mix_ms] = anchor

    if left_boundary_source is not None:
        synthetic = _synthetic_boundary_anchor(
            left_edge, left_boundary_source, 1.0
        )
        by_mix[int(round(left_edge * 1000.0))] = synthetic
    if right_boundary_source is not None:
        synthetic = _synthetic_boundary_anchor(
            right_edge, right_boundary_source, 1.0
        )
        by_mix[int(round(right_edge * 1000.0))] = synthetic

    return sorted(by_mix.values(), key=lambda row: row.mix_time)


def build_cut_aware_timewarp(
    *,
    alignment_path: Sequence[dict[str, Any]],
    localized_boundaries: Sequence[LocalizedCutBoundary],
    mix_start: float,
    mix_end: float,
    timewarp_config: TimeWarpConfig,
) -> dict[str, Any]:
    """Fit continuous TimeWarp segments separated by explicit source jumps."""

    if mix_end <= mix_start:
        raise CutRebuildError("cut-aware mapping requires a positive mix interval")
    if not localized_boundaries:
        raise CutRebuildError("cut-aware mapping requires at least one localized cut")
    boundaries = sorted(localized_boundaries, key=lambda row: row.cut_mix_time)
    if any(
        right.cut_mix_time <= left.cut_mix_time
        for left, right in zip(boundaries, boundaries[1:])
    ):
        raise CutRebuildError(
            "localized cut boundaries must be strictly increasing"
        )
    if (
        boundaries[0].cut_mix_time <= mix_start
        or boundaries[-1].cut_mix_time >= mix_end
    ):
        raise CutRebuildError(
            "localized cut must remain inside the occurrence interval"
        )
    anchors = alignment_anchors(alignment_path)

    segment_edges = (
        [mix_start]
        + [row.cut_mix_time for row in boundaries]
        + [mix_end]
    )
    segments: list[dict[str, Any]] = []
    for index in range(len(segment_edges) - 1):
        left_edge = segment_edges[index]
        right_edge = segment_edges[index + 1]
        segment_anchors = [
            anchor
            for anchor in anchors
            if left_edge - 1e-6 <= anchor.mix_time <= right_edge + 1e-6
        ]

        left_boundary = boundaries[index - 1] if index > 0 else None
        right_boundary = boundaries[index] if index < len(boundaries) else None
        segment_anchors = _deduplicate_segment_anchors(
            segment_anchors,
            left_edge=left_edge,
            right_edge=right_edge,
            left_boundary_source=(
                left_boundary.localized_source_gap_end
                if left_boundary is not None
                else None
            ),
            right_boundary_source=(
                right_boundary.localized_source_gap_start
                if right_boundary is not None
                else None
            ),
        )
        if len(segment_anchors) < 3:
            raise CutRebuildError(
                f"cut-aware segment {index} has insufficient anchors "
                f"({len(segment_anchors)})"
            )

        local_rates = [
            (right.source_time - left.source_time)
            / (right.mix_time - left.mix_time)
            for left, right in zip(segment_anchors, segment_anchors[1:])
            if right.mix_time > left.mix_time
            and right.source_time > left.source_time
        ]
        slope_prior = (
            float(np.median(local_rates)) if local_rates else None
        )
        if slope_prior is not None and not np.isfinite(slope_prior):
            slope_prior = None

        selected = _select_segment_timewarp(
            segment_anchors,
            slope_prior=slope_prior,
            config=timewarp_config,
        )
        if bool(selected.get("blocked")):
            raise CutRebuildError(
                f"cut-aware segment {index} remains blocked: "
                f"{selected.get('selection', 'UNKNOWN')}"
            )
        mapping = selected.get("mapping")
        if not isinstance(mapping, dict):
            raise CutRebuildError(
                f"cut-aware segment {index} returned no mapping"
            )
        source_start = source_time_at_mix(mapping, left_edge)
        source_end = source_time_at_mix(mapping, right_edge)
        if source_end <= source_start:
            raise CutRebuildError(
                f"cut-aware segment {index} is not monotonic"
            )
        diagnostics = mapping.get("diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        segments.append(
            {
                "index": index,
                "mix_start": left_edge,
                "mix_end": right_edge,
                "source_start": source_start,
                "source_end": source_end,
                "model": str(mapping.get("mode") or ""),
                "selection": str(selected.get("selection") or ""),
                "mapping": mapping,
                "diagnostics": diagnostics,
                "anchor_count": len(segment_anchors),
            }
        )

    cuts: list[dict[str, Any]] = []
    for index, boundary in enumerate(boundaries):
        left_segment = segments[index]
        right_segment = segments[index + 1]
        source_gap_start = float(left_segment["source_end"])
        source_gap_end = float(right_segment["source_start"])
        if source_gap_end <= source_gap_start:
            raise CutRebuildError(
                "cut-aware segment mappings do not preserve a forward source gap"
            )
        if source_gap_end - source_gap_start < 0.05:
            raise CutRebuildError("cut-aware mapped source gap is too small")
        cuts.append(
            {
                **boundary.to_dict(),
                "left_segment_index": index,
                "right_segment_index": index + 1,
                "source_gap_start": source_gap_start,
                "source_gap_end": source_gap_end,
                "mapped_source_gap_seconds": source_gap_end - source_gap_start,
            }
        )

    return {
        "kind": "CUT_AWARE",
        "mix_start": mix_start,
        "mix_end": mix_end,
        "segments": segments,
        "cuts": cuts,
    }
