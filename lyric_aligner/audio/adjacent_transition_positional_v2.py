"""Fail-closed, mapping-constrained positional transition shadow evidence."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Callable

from lyric_aligner.audio.features import FeatureBundle, RetrievalResult, retrieve_coarse_window
from lyric_aligner.timeline.projector import source_time_at_mix

SCHEMA_VERSION = "adjacent-transition-positional-2.0"
POLICY_ID = "adjacent-transition-positional-v2-global-coordinate-constrained"

class PositionalEvidenceError(ValueError):
    pass

@dataclass(frozen=True)
class Policy:
    support_radius_seconds: float = 12.0
    sample_step_seconds: float = 3.0
    window_seconds: float = 3.0
    window_offsets: tuple[float, ...] = (-6.0, -3.0, 0.0, 3.0, 6.0)
    source_radius_seconds: float = 2.0
    slope_radius: float = 0.06
    slope_step: float = 0.02
    strong_score: float = 0.72
    strong_margin: float = 0.012
    minimum_feature_agreement: int = 2
    max_residual_seconds: float = 1.5
    boundary_tolerance_seconds: float = 1.5

def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

def _num(value: Any, label: str) -> float:
    try: result = float(value)
    except (TypeError, ValueError) as exc: raise PositionalEvidenceError(f"{label} is not numeric") from exc
    if not math.isfinite(result): raise PositionalEvidenceError(f"{label} is not finite")
    return result

def _mapping(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result", payload)
    tw = result.get("timewarp") if isinstance(result, dict) else None
    mapping = tw.get("mapping") if isinstance(tw, dict) else None
    status = result.get("status") if isinstance(result, dict) else None
    if not isinstance(mapping, dict) or bool(tw.get("blocked")) or status not in (None, "refined"):
        raise PositionalEvidenceError("fine payload is not refined and usable")
    return mapping

def _slope(mapping: dict[str, Any], mix: float) -> float:
    base = _num(mapping["base_slope"], "base_slope")
    value = base
    for point, delta in zip(mapping.get("breakpoints", []), mapping.get("slope_deltas", [])):
        if mix >= _num(point, "breakpoint"): value += _num(delta, "slope_delta")
    if value <= 0: raise PositionalEvidenceError("mapping slope is not positive")
    return value

def _grid(center: float, policy: Policy) -> list[float]:
    return [round(center + i * policy.slope_step, 6) for i in range(-int(policy.slope_radius / policy.slope_step), int(policy.slope_radius / policy.slope_step) + 1) if center + i * policy.slope_step > 0]

def _strong(result: RetrievalResult, policy: Policy, *, expected: float, search_start: float, search_end: float, source_duration: float) -> tuple[bool, list[str]]:
    reasons = []
    candidate_start = result.top1.source_start
    candidate_end = result.top1.source_end
    query_duration = candidate_end - candidate_start
    if abs(result.top1.source_center - expected) > policy.max_residual_seconds:
        reasons.append("residual_exceeds_policy")
    if candidate_start < search_start + 1e-6 or candidate_end > search_end - 1e-6:
        reasons.append("retrieval_at_search_edge")
    if candidate_start <= 0 or candidate_end >= source_duration:
        reasons.append("retrieval_at_source_edge")
    if result.top1.fused_score < policy.strong_score: reasons.append("score")
    if result.margin < policy.strong_margin: reasons.append("margin")
    if result.top1.feature_agreement < policy.minimum_feature_agreement: reasons.append("feature_agreement")
    if result.ambiguous: reasons.append("ambiguous")
    return not reasons, reasons

def _support(path: list[dict[str, Any]], boundary: float, side: str, policy: Policy) -> dict[str, Any] | None:
    eligible = []
    for point in path:
        mix = _num(point.get("global_mix_center", point.get("mix_center")), "fine mix_center")
        if (side == "left" and mix <= boundary) or (side == "right" and mix >= boundary):
            if (not point.get("ambiguous", False)
                    and _num(point.get("fused_score", 0), "fine score") >= policy.strong_score
                    and _num(point.get("margin", 0), "fine margin") >= policy.strong_margin
                    and int(point.get("feature_agreement", 0)) >= policy.minimum_feature_agreement):
                eligible.append((abs(boundary - mix), point))
    if not eligible: return None
    distance, point = min(eligible, key=lambda row: row[0])
    selected_global_mix = _num(
        point.get("global_mix_center", point.get("mix_center")),
        "support global_mix_center",
    )
    selected_local_mix = _num(point.get("mix_center"), "support mix_center")
    return {"mix_center": selected_local_mix, "global_mix_center": selected_global_mix, "source_center": _num(point.get("refined_source_center", point.get("source_center")), "support source_center"), "distance": distance, "point": point}

def adjudicate_transition(*, issue: dict[str, Any], transition: dict[str, Any], left_fine: dict[str, Any], right_fine: dict[str, Any], mix_features: FeatureBundle, left_source_features: FeatureBundle, right_source_features: FeatureBundle, mix_feature_global_start_seconds: float = 0.0, retrieve: Callable[..., RetrievalResult] = retrieve_coarse_window, policy: Policy = Policy()) -> dict[str, Any]:
    left_id, right_id = str(transition.get("left_occurrence_id") or ""), str(transition.get("right_occurrence_id") or "")
    if issue.get("kind") != "transition_ambiguity" or issue.get("code") != "ambiguous_source_occurrence" or (issue.get("left_occurrence_id"), issue.get("right_occurrence_id")) != (left_id, right_id): raise PositionalEvidenceError("transition identity mismatch")
    boundary = _num(transition["nominal_boundary"], "nominal_boundary")
    issue_start, issue_end = _num(issue["interval_start"], "interval_start"), _num(issue["interval_end"], "interval_end")
    if not issue_start <= boundary <= issue_end: raise PositionalEvidenceError("boundary outside issue interval")
    maps = [_mapping(left_fine), _mapping(right_fine)]
    # Production Fine path `mix_center` is already absolute/global mix time.
    # Do not mutate the bound Fine payload while normalizing that identity for
    # this shadow pass; replay/hash checks must continue to see the exact input.
    paths = []
    for payload in (left_fine, right_fine):
        result = payload.get("result", payload)
        normalized_path: list[dict[str, Any]] = []
        for point in result.get("path", []):
            copied = dict(point)
            copied["global_mix_center"] = _num(
                point.get("global_mix_center", point.get("mix_center")),
                "fine mix_center",
            )
            normalized_path.append(copied)
        paths.append(normalized_path)
    supports = [_support(paths[0], boundary, "left", policy), _support(paths[1], boundary, "right", policy)]
    insufficient_support = any(row is None or row["distance"] > policy.support_radius_seconds for row in supports)
    rows = []
    for side, mapping, source in (("left", maps[0], left_source_features), ("right", maps[1], right_source_features)):
        for offset in policy.window_offsets:
            global_center = boundary + offset
            center = max(mix_feature_global_start_seconds, min(mix_feature_global_start_seconds + mix_features.duration_seconds, global_center))
            start, end = max(mix_feature_global_start_seconds, center - policy.window_seconds / 2), min(mix_feature_global_start_seconds + mix_features.duration_seconds, center + policy.window_seconds / 2)
            local_start, local_end = start - mix_feature_global_start_seconds, end - mix_feature_global_start_seconds
            expected = source_time_at_mix(mapping, global_center)
            slope = _slope(mapping, global_center)
            duration = (end - start) * slope
            # `retrieve_coarse_window` interprets source_search_start/end as the
            # boundaries of the complete source search interval.  It derives the
            # valid candidate-start ceiling by subtracting the slope-adjusted query
            # length internally.
            low = max(0.0, expected - duration / 2 - policy.source_radius_seconds)
            high = min(
                source.duration_seconds,
                expected + duration / 2 + policy.source_radius_seconds,
            )
            slopes = _grid(slope, policy)
            maximum_query_source_duration = (local_end - local_start) * max(slopes)
            if high - low + 1e-6 < maximum_query_source_duration:
                rows.append({"side": side, "mix_center": local_start + (local_end-local_start)/2, "global_mix_center": global_center, "expected_source_center": expected, "search_range": [low, high], "strong": False, "error": "search_range_cannot_contain_query"})
                continue
            try:
                result = retrieve(mix_features, source, mix_start=local_start, mix_end=local_end, slopes=slopes, source_search_start=low, source_search_end=high, top_k=4, nms_separation_seconds=.25, min_score=policy.strong_score, min_margin=policy.strong_margin)
            except ValueError as exc:
                if str(exc) != "coarse retrieval produced no candidates":
                    raise
                rows.append({"side": side, "mix_center": local_start + (local_end-local_start)/2, "global_mix_center": global_center, "expected_source_center": expected, "search_range": [low, high], "strong": False, "error": "coarse_retrieval_no_candidates"})
                continue
            strong, rejection_reasons = _strong(result, policy, expected=expected, search_start=low, search_end=high, source_duration=source.duration_seconds)
            rows.append({"side": side, "mix_center": result.mix_center, "global_mix_center": global_center, "mix_start": local_start, "mix_end": local_end, "mix_window_local": [local_start, local_end], "expected_source_center": expected, "search_range": [low, high], "local": result.to_dict(), "strong": strong, "strong_rejection_reasons": rejection_reasons, "residual": result.top1.source_center - expected})
    pre_limit = boundary - policy.boundary_tolerance_seconds
    post_limit = boundary + policy.boundary_tolerance_seconds
    pre_left = any(
        r["strong"]
        and r["side"] == "left"
        and r["global_mix_center"] <= pre_limit
        for r in rows
    )
    post_right = any(
        r["strong"]
        and r["side"] == "right"
        and r["global_mix_center"] >= post_limit
        for r in rows
    )
    paired = []
    for center in sorted({r["global_mix_center"] for r in rows}):
        pair = {r["side"]: r for r in rows if r["global_mix_center"] == center}
        if "left" in pair and "right" in pair:
            left_window, right_window = pair["left"].get("mix_window_local", []), pair["right"].get("mix_window_local", [])
            overlap_windows = bool(left_window and right_window and max(left_window[0], right_window[0]) < min(left_window[1], right_window[1]))
            paired.append({"mix_center": center, "left_strong": pair["left"]["strong"], "right_strong": pair["right"]["strong"], "overlap_like": pair["left"]["strong"] and pair["right"]["strong"] and overlap_windows})
    overlap = any(p["overlap_like"] for p in paired if abs(p["mix_center"] - boundary) <= policy.window_seconds / 2)
    pre_right = any(
        p["mix_center"] <= pre_limit and p["right_strong"] for p in paired
    )
    post_left = any(
        p["mix_center"] >= post_limit and p["left_strong"] for p in paired
    )
    clear = pre_left and post_right and not pre_right and not post_left and not overlap and not insufficient_support
    if insufficient_support:
        action = "unresolved"
    elif clear:
        action = "clear_sequential_advisory"
    elif overlap:
        action = "overlap_candidate_advisory"
    else:
        action = "unresolved"
    return {"schema_version": SCHEMA_VERSION, "policy_id": POLICY_ID, "issue_candidate_id": str(issue.get("candidate_id") or ""), "left_occurrence_id": left_id, "right_occurrence_id": right_id, "nominal_boundary": boundary, "mix_feature_global_start_seconds": mix_feature_global_start_seconds, "support": supports, "windows": rows, "paired_evidence": paired, "recommendation": {"action": action}, "authority": {"shadow_only": True, "automatic_timeline_mutation": False}, "timing_mutation_performed": False, "mapping_hashes": {"left": _sha(maps[0]), "right": _sha(maps[1])}}
