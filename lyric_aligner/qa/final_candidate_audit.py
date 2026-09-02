"""Read-only quality audit for a materialized v4 subtitle candidate.

This module never grants release, timing, text, or segmentation authority.
"""

from __future__ import annotations

import statistics
from typing import Any, Mapping, Sequence

from lyric_aligner.srt import Cue


class FinalCandidateAuditError(ValueError):
    """Raised when audit inputs are malformed or internally inconsistent."""


def _int_field(row: Mapping[str, Any], name: str, *, position: int) -> int:
    try:
        return int(row[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise FinalCandidateAuditError(f"audit row {position} has invalid {name}") from exc


def _percentile(values: Sequence[int], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    point = (len(ordered) - 1) * q
    lower = int(point)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = point - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _confirmed_overlap_contains(
    *,
    left_occurrence_id: str,
    right_occurrence_id: str,
    start_ms: int,
    end_ms: int,
    regions: Sequence[Mapping[str, Any]],
) -> bool:
    pair = {left_occurrence_id, right_occurrence_id}
    for region in regions:
        if not isinstance(region, Mapping):
            continue
        region_pair = {
            str(region.get("left_occurrence_id") or ""),
            str(region.get("right_occurrence_id") or ""),
        }
        if region_pair != pair:
            continue
        try:
            region_start = int(region["start_ms"])
            region_end = int(region["end_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        if region_start <= start_ms and end_ms <= region_end:
            return True
    return False


def audit_final_candidate(
    cues: Sequence[Cue],
    audit_rows: Sequence[Mapping[str, Any]],
    *,
    occurrence_windows: Mapping[str, tuple[int, int]],
    confirmed_overlap_regions: Sequence[Mapping[str, Any]] = (),
    content_end_ms: int | None = None,
    long_hold_threshold_ms: int = 6000,
    extreme_hold_threshold_ms: int = 8000,
) -> dict[str, Any]:
    """Audit final cue geometry without changing any production authority."""

    if not cues:
        raise FinalCandidateAuditError("final candidate has no cues")
    if len(cues) != len(audit_rows):
        raise FinalCandidateAuditError(
            f"final SRT/report count mismatch: srt={len(cues)}, report={len(audit_rows)}"
        )
    if long_hold_threshold_ms < 1000:
        raise FinalCandidateAuditError("long_hold_threshold_ms must be at least 1000")
    if extreme_hold_threshold_ms < long_hold_threshold_ms:
        raise FinalCandidateAuditError(
            "extreme_hold_threshold_ms must be >= long_hold_threshold_ms"
        )

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    durations: list[int] = []
    short_rows: list[dict[str, Any]] = []
    long_rows: list[dict[str, Any]] = []
    extreme_rows: list[dict[str, Any]] = []
    per_occurrence: dict[str, dict[str, Any]] = {}
    previous_start = -1

    for position, (cue, row) in enumerate(zip(cues, audit_rows), start=1):
        occurrence_id = str(row.get("occurrence_id") or "").strip()
        if not occurrence_id:
            raise FinalCandidateAuditError(f"audit row {position} is missing occurrence_id")
        ordinal = _int_field(row, "ordinal", position=position)
        line_index = _int_field(row, "canonical_line_index", position=position)
        duration = cue.end_ms - cue.start_ms
        durations.append(duration)

        if cue.start_ms < previous_start:
            errors.append({
                "kind": "nonmonotonic_final_file_order",
                "position": position,
                "start_ms": cue.start_ms,
                "previous_start_ms": previous_start,
            })
        previous_start = cue.start_ms
        if duration <= 0:
            errors.append({
                "kind": "nonpositive_cue_duration",
                "position": position,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
            })

        window = occurrence_windows.get(occurrence_id)
        if window is None:
            raise FinalCandidateAuditError(f"no authoritative occurrence window for {occurrence_id}")
        window_start, window_end = window
        if cue.start_ms < window_start or cue.end_ms > window_end:
            errors.append({
                "kind": "occurrence_window_violation",
                "position": position,
                "occurrence_id": occurrence_id,
                "window_start_ms": window_start,
                "window_end_ms": window_end,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
            })

        detail = {
            "position": position,
            "occurrence_id": occurrence_id,
            "ordinal": ordinal,
            "canonical_line_index": line_index,
            "start_ms": cue.start_ms,
            "end_ms": cue.end_ms,
            "duration_ms": duration,
            "end_basis": str(row.get("end_basis") or ""),
            "text": cue.text,
        }
        if duration < 500:
            short_rows.append(detail)
        if duration > long_hold_threshold_ms:
            long_rows.append(detail)
        if duration >= extreme_hold_threshold_ms:
            extreme_rows.append(detail)

        summary = per_occurrence.setdefault(occurrence_id, {
            "occurrence_id": occurrence_id,
            "ordinal": ordinal,
            "cue_count": 0,
            "first_start_ms": cue.start_ms,
            "last_end_ms": cue.end_ms,
            "min_duration_ms": duration,
            "max_duration_ms": duration,
        })
        if summary["ordinal"] != ordinal:
            raise FinalCandidateAuditError(f"occurrence {occurrence_id} has inconsistent ordinal values")
        summary["cue_count"] += 1
        summary["first_start_ms"] = min(summary["first_start_ms"], cue.start_ms)
        summary["last_end_ms"] = max(summary["last_end_ms"], cue.end_ms)
        summary["min_duration_ms"] = min(summary["min_duration_ms"], duration)
        summary["max_duration_ms"] = max(summary["max_duration_ms"], duration)

    allowed_overlaps: list[dict[str, Any]] = []
    unconfirmed_overlaps: list[dict[str, Any]] = []
    for index in range(len(cues) - 1):
        left = cues[index]
        right = cues[index + 1]
        overlap_start = max(left.start_ms, right.start_ms)
        overlap_end = min(left.end_ms, right.end_ms)
        if overlap_end <= overlap_start:
            continue
        left_occurrence_id = str(audit_rows[index].get("occurrence_id") or "").strip()
        right_occurrence_id = str(audit_rows[index + 1].get("occurrence_id") or "").strip()
        detail = {
            "left_position": index + 1,
            "right_position": index + 2,
            "left_occurrence_id": left_occurrence_id,
            "right_occurrence_id": right_occurrence_id,
            "start_ms": overlap_start,
            "end_ms": overlap_end,
            "overlap_ms": overlap_end - overlap_start,
        }
        if left_occurrence_id != right_occurrence_id and _confirmed_overlap_contains(
            left_occurrence_id=left_occurrence_id,
            right_occurrence_id=right_occurrence_id,
            start_ms=overlap_start,
            end_ms=overlap_end,
            regions=confirmed_overlap_regions,
        ):
            allowed_overlaps.append(detail)
        else:
            reason = "same_occurrence_overlap" if left_occurrence_id == right_occurrence_id else "no_confirmed_overlap_region"
            unconfirmed_overlaps.append({**detail, "reason": reason})

    # Adjacent-only checks miss a long cue that spans across two or more later cues.
    # Scan non-adjacent pairs as well; final files are small enough that the explicit
    # O(n^2) diagnostic is preferable to relying on ordering assumptions here.
    for left_index in range(max(0, len(cues) - 2)):
        left = cues[left_index]
        left_occurrence_id = str(
            audit_rows[left_index].get("occurrence_id") or ""
        ).strip()
        for right_index in range(left_index + 2, len(cues)):
            right = cues[right_index]
            overlap_start = max(left.start_ms, right.start_ms)
            overlap_end = min(left.end_ms, right.end_ms)
            if overlap_end <= overlap_start:
                continue
            right_occurrence_id = str(
                audit_rows[right_index].get("occurrence_id") or ""
            ).strip()
            detail = {
                "left_position": left_index + 1,
                "right_position": right_index + 1,
                "left_occurrence_id": left_occurrence_id,
                "right_occurrence_id": right_occurrence_id,
                "start_ms": overlap_start,
                "end_ms": overlap_end,
                "overlap_ms": overlap_end - overlap_start,
            }
            if left_occurrence_id != right_occurrence_id and _confirmed_overlap_contains(
                left_occurrence_id=left_occurrence_id,
                right_occurrence_id=right_occurrence_id,
                start_ms=overlap_start,
                end_ms=overlap_end,
                regions=confirmed_overlap_regions,
            ):
                allowed_overlaps.append(detail)
            else:
                reason = (
                    "same_occurrence_overlap"
                    if left_occurrence_id == right_occurrence_id
                    else "no_confirmed_overlap_region"
                )
                unconfirmed_overlaps.append({**detail, "reason": reason})

    errors.extend({"kind": "unconfirmed_cue_overlap", **row} for row in unconfirmed_overlaps)

    last_end_ms = max(cue.end_ms for cue in cues)
    if content_end_ms is not None and last_end_ms > content_end_ms:
        errors.append({
            "kind": "content_end_violation",
            "last_end_ms": last_end_ms,
            "content_end_ms": content_end_ms,
            "excess_ms": last_end_ms - content_end_ms,
        })
    if long_rows:
        warnings.append({"kind": "long_display_holds", "count": len(long_rows), "threshold_ms": long_hold_threshold_ms})
    if extreme_rows:
        warnings.append({"kind": "extreme_display_holds", "count": len(extreme_rows), "threshold_ms": extreme_hold_threshold_ms})

    return {
        "schema_version": "final-candidate-audit-0.1",
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "cue_count": len(cues),
        "first_start_ms": min(cue.start_ms for cue in cues),
        "last_end_ms": last_end_ms,
        "content_end_ms": content_end_ms,
        "last_end_minus_content_end_ms": None if content_end_ms is None else last_end_ms - content_end_ms,
        "duration": {
            "min_ms": min(durations),
            "median_ms": float(statistics.median(durations)),
            "p95_ms": float(_percentile(durations, 0.95)),
            "max_ms": max(durations),
            "under_500_count": len(short_rows),
            "over_long_hold_count": len(long_rows),
            "at_or_over_extreme_hold_count": len(extreme_rows),
        },
        "short_rows": short_rows,
        "long_rows": long_rows,
        "extreme_rows": extreme_rows,
        "confirmed_overlap_cue_intersections": allowed_overlaps,
        "unconfirmed_overlap_cue_intersections": unconfirmed_overlaps,
        "per_occurrence": sorted(
            per_occurrence.values(),
            key=lambda item: (item["ordinal"], item["occurrence_id"]),
        ),
    }
