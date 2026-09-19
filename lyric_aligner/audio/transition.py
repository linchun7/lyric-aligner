"""Conservative adjacent-track transition and overlap evidence.

This layer never confirms simultaneous vocals. It combines already fingerprinted
per-occurrence coarse audio evidence near a nominal boundary and emits review
candidates when both tracks are independently supported in the same mix time.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ActivityWindow:
    occurrence_id: str
    start: float
    end: float
    fused_score: float
    margin: float
    feature_agreement: int
    strong: bool
    ambiguous: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransitionCandidate:
    candidate_id: str
    type: str
    status: str
    start: float
    end: float
    occurrences: tuple[str, str]
    reason: str
    left_score: float
    right_score: float

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["occurrences"] = list(self.occurrences)
        return payload


def transition_candidate_id(
    candidate_type: str,
    left_occurrence_id: str,
    right_occurrence_id: str,
    start: float,
    end: float,
) -> str:
    """Return a deterministic ID for one interval within an exact algorithm run."""

    core = {
        "type": str(candidate_type),
        "left_occurrence_id": str(left_occurrence_id),
        "right_occurrence_id": str(right_occurrence_id),
        "start_ms": int(round(float(start) * 1000.0)),
        "end_ms": int(round(float(end) * 1000.0)),
    }
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def transition_search_interval(
    nominal_boundary: float,
    *,
    mix_duration: float,
    margin_seconds: float = 10.0,
) -> tuple[float, float]:
    if mix_duration <= 0 or margin_seconds <= 0:
        raise ValueError("mix_duration and margin_seconds must be positive")
    return (
        max(0.0, nominal_boundary - margin_seconds),
        min(mix_duration, nominal_boundary + margin_seconds),
    )


def _activity_windows(
    payload: dict[str, Any],
    *,
    min_fused_score: float,
    min_margin: float,
    minimum_feature_agreement: int,
) -> list[ActivityWindow]:
    occurrence_id = str(payload.get("occurrence_id") or "")
    result = payload.get("result", payload)
    windows = result.get("windows", [])
    rows: list[ActivityWindow] = []
    for window in windows:
        top1 = window.get("top1") or {}
        fused = float(top1.get("fused_score", 0.0))
        agreement = int(top1.get("feature_agreement", 0))
        margin = float(window.get("margin", 0.0))
        ambiguous = bool(window.get("ambiguous", False))
        strong = (
            fused >= min_fused_score
            and agreement >= minimum_feature_agreement
            and margin >= min_margin
            and not ambiguous
        )
        rows.append(
            ActivityWindow(
                occurrence_id=occurrence_id,
                start=float(window["mix_start"]),
                end=float(window["mix_end"]),
                fused_score=fused,
                margin=margin,
                feature_agreement=agreement,
                strong=strong,
                ambiguous=ambiguous,
            )
        )
    return rows


def _merge_intervals(
    rows: list[tuple[float, float, float, float]],
    *,
    max_gap: float,
) -> list[tuple[float, float, float, float]]:
    if not rows:
        return []
    ordered = sorted(rows)
    output: list[tuple[float, float, float, float]] = []
    start, end, left_score, right_score = ordered[0]
    left_scores = [left_score]
    right_scores = [right_score]
    for row_start, row_end, row_left, row_right in ordered[1:]:
        if row_start <= end + max_gap:
            end = max(end, row_end)
            left_scores.append(row_left)
            right_scores.append(row_right)
        else:
            output.append(
                (
                    start,
                    end,
                    sum(left_scores) / len(left_scores),
                    sum(right_scores) / len(right_scores),
                )
            )
            start, end = row_start, row_end
            left_scores = [row_left]
            right_scores = [row_right]
    output.append(
        (
            start,
            end,
            sum(left_scores) / len(left_scores),
            sum(right_scores) / len(right_scores),
        )
    )
    return output


def probe_adjacent_transition(
    left_payload: dict[str, Any],
    right_payload: dict[str, Any],
    *,
    min_fused_score: float = 0.72,
    min_margin: float = 0.02,
    minimum_feature_agreement: int = 2,
    minimum_overlap_seconds: float = 0.75,
    merge_gap_seconds: float = 0.35,
) -> dict[str, Any]:
    left_id = str(left_payload.get("occurrence_id") or "")
    right_id = str(right_payload.get("occurrence_id") or "")
    if not left_id or not right_id or left_id == right_id:
        raise ValueError("transition probe requires two distinct occurrence_id values")

    left = _activity_windows(
        left_payload,
        min_fused_score=min_fused_score,
        min_margin=min_margin,
        minimum_feature_agreement=minimum_feature_agreement,
    )
    right = _activity_windows(
        right_payload,
        min_fused_score=min_fused_score,
        min_margin=min_margin,
        minimum_feature_agreement=minimum_feature_agreement,
    )
    if not left or not right:
        raise ValueError("transition probe requires coarse windows for both occurrences")

    simultaneous: list[tuple[float, float, float, float]] = []
    uncertain: list[tuple[float, float]] = []
    for left_window in left:
        for right_window in right:
            start = max(left_window.start, right_window.start)
            end = min(left_window.end, right_window.end)
            if end <= start:
                continue
            if left_window.strong and right_window.strong:
                simultaneous.append(
                    (start, end, left_window.fused_score, right_window.fused_score)
                )
            elif (
                left_window.fused_score >= min_fused_score
                and right_window.fused_score >= min_fused_score
                and (left_window.ambiguous or right_window.ambiguous)
            ):
                uncertain.append((start, end))

    merged = _merge_intervals(simultaneous, max_gap=merge_gap_seconds)
    candidates = [
        TransitionCandidate(
            candidate_id=transition_candidate_id(
                "cross_track_overlap_candidate", left_id, right_id, start, end
            ),
            type="cross_track_overlap_candidate",
            status="review",
            start=start,
            end=end,
            occurrences=(left_id, right_id),
            reason=(
                "both adjacent TrackOccurrences have strong independent source-audio "
                "evidence in the same mix interval; confirm/reject before release"
            ),
            left_score=left_score,
            right_score=right_score,
        ).to_dict()
        for start, end, left_score, right_score in merged
        if end - start >= minimum_overlap_seconds
    ]

    uncertain_merged: list[list[float]] = []
    for start, end in sorted(uncertain):
        if uncertain_merged and start <= uncertain_merged[-1][1] + merge_gap_seconds:
            uncertain_merged[-1][1] = max(uncertain_merged[-1][1], end)
        else:
            uncertain_merged.append([start, end])

    uncertain_rows = [
        {
            "candidate_id": transition_candidate_id(
                "transition_ambiguity", left_id, right_id, start, end
            ),
            "start": start,
            "end": end,
            "reason": "high audio score but ambiguous source occurrence",
        }
        for start, end in uncertain_merged
    ]

    return {
        "left_occurrence_id": left_id,
        "right_occurrence_id": right_id,
        "activity_thresholds": {
            "min_fused_score": min_fused_score,
            "min_margin": min_margin,
            "minimum_feature_agreement": minimum_feature_agreement,
            "minimum_overlap_seconds": minimum_overlap_seconds,
        },
        "left_activity": [row.to_dict() for row in left],
        "right_activity": [row.to_dict() for row in right],
        "overlap_candidates": candidates,
        "uncertain_intervals": uncertain_rows,
        "blocked": bool(candidates or uncertain_rows),
        "status": (
            "review_required"
            if (candidates or uncertain_rows)
            else "clear_sequential_or_no_overlap"
        ),
    }
