"""Typed structural-event truth/prediction matching for strict private evaluation.

This module is evaluation-only. Expected events are immutable ground truth;
predicted events never contribute to ground-truth identity. The representation is
intentionally small and privacy-safe: only event kind and mix-time location are
accepted.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable


POINT_EVENT_KINDS = frozenset({"hard_cut", "same_track_splice", "sequential_transition"})
INTERVAL_EVENT_KINDS = frozenset(
    {"crossfade", "true_overlap", "piecewise_rate", "reorder", "detached_tail"}
)
STRUCTURAL_EVENT_KINDS = POINT_EVENT_KINDS | INTERVAL_EVENT_KINDS


class StructuralEventError(ValueError):
    """Raised when structural-event evaluation metadata is malformed."""


@dataclass(frozen=True)
class StructuralEvent:
    kind: str
    shape: str
    time_ms: float | None = None
    start_ms: float | None = None
    end_ms: float | None = None

    def identity(self) -> dict[str, Any]:
        if self.shape == "point":
            return {"kind": self.kind, "time_ms": self.time_ms}
        return {"kind": self.kind, "start_ms": self.start_ms, "end_ms": self.end_ms}


def _finite(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StructuralEventError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise StructuralEventError(f"{label} must be finite")
    return number


def normalize_structural_events(case: dict[str, Any], key: str) -> tuple[StructuralEvent, ...]:
    raw = case.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise StructuralEventError(f"{key} must be a list")

    events: list[StructuralEvent] = []
    seen: set[tuple[Any, ...]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise StructuralEventError(f"{key}[{index}] must be an object")
        kind = str(item.get("kind") or "").strip()
        if kind not in STRUCTURAL_EVENT_KINDS:
            raise StructuralEventError(
                f"{key}[{index}] unsupported kind {kind!r}; expected one of "
                + ", ".join(sorted(STRUCTURAL_EVENT_KINDS))
            )
        if kind in POINT_EVENT_KINDS:
            if set(item) != {"kind", "time_ms"}:
                raise StructuralEventError(
                    f"{key}[{index}] point event requires exactly kind,time_ms"
                )
            time_ms = _finite(item["time_ms"], label=f"{key}[{index}].time_ms")
            if time_ms < 0:
                raise StructuralEventError(f"{key}[{index}].time_ms must be >= 0")
            event = StructuralEvent(kind=kind, shape="point", time_ms=time_ms)
            identity = (kind, "point", time_ms)
        else:
            if set(item) != {"kind", "start_ms", "end_ms"}:
                raise StructuralEventError(
                    f"{key}[{index}] interval event requires exactly kind,start_ms,end_ms"
                )
            start_ms = _finite(item["start_ms"], label=f"{key}[{index}].start_ms")
            end_ms = _finite(item["end_ms"], label=f"{key}[{index}].end_ms")
            if start_ms < 0 or end_ms <= start_ms:
                raise StructuralEventError(
                    f"{key}[{index}] interval requires 0 <= start_ms < end_ms"
                )
            event = StructuralEvent(
                kind=kind, shape="interval", start_ms=start_ms, end_ms=end_ms
            )
            identity = (kind, "interval", start_ms, end_ms)
        if identity in seen:
            raise StructuralEventError(f"{key}[{index}] duplicates another event")
        seen.add(identity)
        events.append(event)
    return tuple(sorted(events, key=lambda e: (e.kind, e.start_ms or e.time_ms or 0.0, e.end_ms or 0.0)))


def structural_event_truth_identity(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [event.identity() for event in normalize_structural_events(case, "expected_structural_events")]


def validate_structural_event_case(case: dict[str, Any]) -> None:
    expected = normalize_structural_events(case, "expected_structural_events")
    predicted = normalize_structural_events(case, "predicted_structural_events")
    tolerance = _finite(
        case.get("structural_event_tolerance_ms", 500.0),
        label="structural_event_tolerance_ms",
    )
    if tolerance < 0:
        raise StructuralEventError("structural_event_tolerance_ms must be >= 0")
    min_iou = _finite(
        case.get("structural_event_min_iou", 0.5),
        label="structural_event_min_iou",
    )
    if not 0.0 <= min_iou <= 1.0:
        raise StructuralEventError("structural_event_min_iou must be between 0 and 1")
    # Normalize both sides even when empty; the assignments make the intent explicit.
    _ = expected, predicted


def _point_matches(expected: list[StructuralEvent], predicted: list[StructuralEvent], tolerance: float) -> tuple[int, list[float]]:
    truth = sorted(float(event.time_ms) for event in expected if event.time_ms is not None)
    pred = sorted(float(event.time_ms) for event in predicted if event.time_ms is not None)
    n, m = len(truth), len(pred)
    score: list[list[tuple[int, float] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    parent: list[list[tuple[int, int, str, float | None] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    score[0][0] = (0, 0.0)

    def better(candidate: tuple[int, float], current: tuple[int, float] | None) -> bool:
        return current is None or candidate[0] > current[0] or (
            candidate[0] == current[0] and candidate[1] < current[1] - 1e-12
        )

    for i in range(n + 1):
        for j in range(m + 1):
            current = score[i][j]
            if current is None:
                continue
            if i < n and better(current, score[i + 1][j]):
                score[i + 1][j] = current
                parent[i + 1][j] = (i, j, "skip_truth", None)
            if j < m and better(current, score[i][j + 1]):
                score[i][j + 1] = current
                parent[i][j + 1] = (i, j, "skip_pred", None)
            if i < n and j < m:
                error = abs(truth[i] - pred[j])
                if error <= tolerance:
                    candidate = (current[0] + 1, current[1] + error)
                    if better(candidate, score[i + 1][j + 1]):
                        score[i + 1][j + 1] = candidate
                        parent[i + 1][j + 1] = (i, j, "match", error)

    errors: list[float] = []
    i, j = n, m
    while i or j:
        step = parent[i][j]
        if step is None:
            if i:
                i -= 1
            elif j:
                j -= 1
            continue
        previous_i, previous_j, action, error = step
        if action == "match" and error is not None:
            errors.append(float(error))
        i, j = previous_i, previous_j
    errors.reverse()
    return len(errors), errors


def _interval_iou(left: StructuralEvent, right: StructuralEvent) -> float:
    assert left.start_ms is not None and left.end_ms is not None
    assert right.start_ms is not None and right.end_ms is not None
    intersection = max(0.0, min(left.end_ms, right.end_ms) - max(left.start_ms, right.start_ms))
    union = max(left.end_ms, right.end_ms) - min(left.start_ms, right.start_ms)
    return intersection / union if union > 0 else 0.0


def _interval_matches(expected: list[StructuralEvent], predicted: list[StructuralEvent], min_iou: float) -> tuple[int, list[float]]:
    truth = sorted(expected, key=lambda event: (float(event.start_ms or 0.0), float(event.end_ms or 0.0)))
    pred = sorted(predicted, key=lambda event: (float(event.start_ms or 0.0), float(event.end_ms or 0.0)))
    n, m = len(truth), len(pred)
    score: list[list[tuple[int, float] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    parent: list[list[tuple[int, int, str, float | None] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    score[0][0] = (0, 0.0)

    def better(candidate: tuple[int, float], current: tuple[int, float] | None) -> bool:
        # Maximize matches, then maximize total IoU (stored as negative cost).
        return current is None or candidate[0] > current[0] or (
            candidate[0] == current[0] and candidate[1] < current[1] - 1e-12
        )

    for i in range(n + 1):
        for j in range(m + 1):
            current = score[i][j]
            if current is None:
                continue
            if i < n and better(current, score[i + 1][j]):
                score[i + 1][j] = current
                parent[i + 1][j] = (i, j, "skip_truth", None)
            if j < m and better(current, score[i][j + 1]):
                score[i][j + 1] = current
                parent[i][j + 1] = (i, j, "skip_pred", None)
            if i < n and j < m:
                iou = _interval_iou(truth[i], pred[j])
                if iou + 1e-12 >= min_iou:
                    candidate = (current[0] + 1, current[1] - iou)
                    if better(candidate, score[i + 1][j + 1]):
                        score[i + 1][j + 1] = candidate
                        parent[i + 1][j + 1] = (i, j, "match", iou)

    ious: list[float] = []
    i, j = n, m
    while i or j:
        step = parent[i][j]
        if step is None:
            if i:
                i -= 1
            elif j:
                j -= 1
            continue
        previous_i, previous_j, action, iou = step
        if action == "match" and iou is not None:
            ious.append(float(iou))
        i, j = previous_i, previous_j
    ious.reverse()
    return len(ious), ious


def structural_event_metrics(
    cases: Iterable[dict[str, Any]], *, kind_filter: str | None = None
) -> dict[str, Any]:
    if kind_filter is not None and kind_filter not in STRUCTURAL_EVENT_KINDS:
        # `none` and legacy `unspecified` are useful case scopes: they evaluate
        # any predicted structural event as a case-level false positive rather
        # than pretending either label is an event kind.
        if kind_filter not in {"none", "unspecified"}:
            raise StructuralEventError(f"unsupported structural event filter {kind_filter!r}")
        kind_filter = None

    expected_count = predicted_count = match_count = 0
    point_errors: list[float] = []
    interval_ious: list[float] = []
    annotated_case_count = 0
    clean_case_count = clean_case_match_count = 0

    for case in cases:
        validate_structural_event_case(case)
        expected = list(normalize_structural_events(case, "expected_structural_events"))
        predicted = list(normalize_structural_events(case, "predicted_structural_events"))
        if kind_filter is not None:
            expected = [event for event in expected if event.kind == kind_filter]
            predicted = [event for event in predicted if event.kind == kind_filter]
        annotated = (
            "expected_structural_events" in case
            or "predicted_structural_events" in case
        )
        if annotated:
            annotated_case_count += 1
            if not expected:
                clean_case_count += 1
                if not predicted:
                    clean_case_match_count += 1

        expected_count += len(expected)
        predicted_count += len(predicted)
        by_kind_expected: dict[str, list[StructuralEvent]] = defaultdict(list)
        by_kind_predicted: dict[str, list[StructuralEvent]] = defaultdict(list)
        for event in expected:
            by_kind_expected[event.kind].append(event)
        for event in predicted:
            by_kind_predicted[event.kind].append(event)
        for kind in sorted(set(by_kind_expected) | set(by_kind_predicted)):
            truth = by_kind_expected[kind]
            pred = by_kind_predicted[kind]
            if kind in POINT_EVENT_KINDS:
                matches, errors = _point_matches(
                    truth,
                    pred,
                    float(case.get("structural_event_tolerance_ms", 500.0)),
                )
                point_errors.extend(errors)
            else:
                matches, ious = _interval_matches(
                    truth,
                    pred,
                    float(case.get("structural_event_min_iou", 0.5)),
                )
                interval_ious.extend(ious)
            match_count += matches

    precision = match_count / predicted_count if predicted_count else 0.0
    recall = match_count / expected_count if expected_count else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "structural_event_annotation_case_count": annotated_case_count,
        "structural_event_expected_count": expected_count,
        "structural_event_predicted_count": predicted_count,
        "structural_event_match_count": match_count,
        "structural_event_false_positive_count": predicted_count - match_count,
        "structural_event_miss_count": expected_count - match_count,
        "structural_event_precision": round(precision, 6),
        "structural_event_recall": round(recall, 6),
        "structural_event_f1": round(f1, 6),
        "structural_event_clean_case_count": clean_case_count,
        "structural_event_clean_case_rate": round(
            clean_case_match_count / clean_case_count, 6
        )
        if clean_case_count
        else 0.0,
        "structural_event_point_mae_ms": round(sum(point_errors) / len(point_errors), 3)
        if point_errors
        else 0.0,
        "structural_event_interval_mean_iou": round(
            sum(interval_ious) / len(interval_ious), 6
        )
        if interval_ious
        else 0.0,
    }
