"""Strict reference-timeline retiming for independently rendered audio references."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class ReferenceRetimeError(ValueError):
    """Raised when a reference retime would weaken timing authority."""


def normalize_offset_segments(rows: object) -> tuple[dict[str, int], ...]:
    if not isinstance(rows, list) or not rows:
        raise ReferenceRetimeError("reference retime segments must be a non-empty list")
    normalized: list[dict[str, int]] = []
    last_start: int | None = None
    last_offset: int | None = None
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ReferenceRetimeError(f"reference retime segment {position} must be an object")
        start = row.get("reference_start_ms")
        offset = row.get("offset_ms")
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            raise ReferenceRetimeError(f"reference retime segment {position} has invalid reference_start_ms")
        if not isinstance(offset, int) or isinstance(offset, bool):
            raise ReferenceRetimeError(f"reference retime segment {position} has invalid offset_ms")
        if last_start is not None and start <= last_start:
            raise ReferenceRetimeError("reference retime segment starts must be strictly increasing")
        if last_offset is not None and offset < last_offset:
            raise ReferenceRetimeError("reference retime offsets must be non-decreasing")
        normalized.append({"reference_start_ms": start, "offset_ms": offset})
        last_start = start
        last_offset = offset
    if normalized[0]["reference_start_ms"] != 0:
        raise ReferenceRetimeError("reference retime segments must start at 0ms")
    return tuple(normalized)


def normalize_retained_segments(rows: object) -> tuple[dict[str, int | None], ...]:
    """Normalize a monotone splice map made of retained reference intervals.

    Each interval maps with slope 1 from ``reference_start_ms`` to
    ``target_start_ms``. Gaps between reference intervals are explicitly
    removed material. Target intervals may touch or have gaps, but must never
    overlap or move backward. An open-ended interval is allowed only last.
    """

    if not isinstance(rows, list) or not rows:
        raise ReferenceRetimeError("reference retained_segments must be a non-empty list")
    normalized: list[dict[str, int | None]] = []
    previous_reference_end: int | None = None
    previous_target_end: int | None = None
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ReferenceRetimeError(f"reference retained segment {position} must be an object")
        start = row.get("reference_start_ms")
        end = row.get("reference_end_ms")
        target_start = row.get("target_start_ms")
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            raise ReferenceRetimeError(f"reference retained segment {position} has invalid reference_start_ms")
        if end is not None and (
            not isinstance(end, int) or isinstance(end, bool) or end <= start
        ):
            raise ReferenceRetimeError(f"reference retained segment {position} has invalid reference_end_ms")
        if not isinstance(target_start, int) or isinstance(target_start, bool):
            raise ReferenceRetimeError(f"reference retained segment {position} has invalid target_start_ms")
        if previous_reference_end is not None and start < previous_reference_end:
            raise ReferenceRetimeError("reference retained segments must not overlap")
        if previous_reference_end is None and normalized:
            raise ReferenceRetimeError("an open-ended retained segment must be last")
        if previous_target_end is not None and target_start < previous_target_end:
            raise ReferenceRetimeError("reference retained segments must not overlap or move backward in target time")
        normalized.append(
            {
                "reference_start_ms": start,
                "reference_end_ms": end,
                "target_start_ms": target_start,
            }
        )
        previous_reference_end = end
        previous_target_end = target_start + (end - start) if end is not None else None
    if normalized[0]["reference_start_ms"] != 0:
        raise ReferenceRetimeError("reference retained_segments must start at 0ms")
    return tuple(normalized)


def map_reference_time_ms(value: int, segments: tuple[dict[str, int], ...]) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ReferenceRetimeError("reference time must be a JSON integer")
    if value < 0:
        raise ReferenceRetimeError("reference time must be non-negative")
    active = segments[0]
    for row in segments[1:]:
        if value < row["reference_start_ms"]:
            break
        active = row
    return value + active["offset_ms"]


def _validate_reference_line(raw: object, position: int, last_index: int) -> tuple[int, int, int | None]:
    if not isinstance(raw, dict):
        raise ReferenceRetimeError(f"reference timeline line {position} must be an object")
    index = raw.get("canonical_line_index")
    start = raw.get("mix_start_ms")
    end = raw.get("mix_end_ms")
    if not isinstance(index, int) or isinstance(index, bool) or index <= last_index:
        raise ReferenceRetimeError("reference canonical line indices must be strictly increasing")
    if not isinstance(start, int) or isinstance(start, bool):
        raise ReferenceRetimeError(f"reference timeline line {position} has invalid mix_start_ms")
    if end is not None and (
        not isinstance(end, int) or isinstance(end, bool) or end <= start
    ):
        raise ReferenceRetimeError(f"reference timeline line {position} has invalid mix_end_ms")
    tokens = raw.get("tokens")
    if tokens not in (None, []):
        raise ReferenceRetimeError(
            "reference retime currently refuses token-timed lines; token timing must be retimed explicitly"
        )
    if start < 0:
        raise ReferenceRetimeError("reference retime refuses negative reference cue times")
    return index, start, end


def _finalize_result(
    reference_result: dict[str, Any],
    retimed_lines: list[dict[str, Any]],
    *,
    target_window_start_ms: int,
    target_window_end_ms: int,
    mode: str,
) -> dict[str, Any]:
    if not retimed_lines:
        raise ReferenceRetimeError("reference retime produced no canonical lines")
    result = deepcopy(reference_result)
    result["window"] = {"start_ms": target_window_start_ms, "end_ms": target_window_end_ms}
    result["line_count"] = len(retimed_lines)
    result["lines"] = retimed_lines
    result["reference_retimed"] = True
    result["reference_retime_mode"] = mode
    result["projection_issues"] = []
    return result


def retime_reference_result(
    reference_result: dict[str, Any],
    *,
    target_window_start_ms: int,
    target_window_end_ms: int,
    segments: tuple[dict[str, int], ...],
) -> dict[str, Any]:
    if target_window_start_ms < 0 or target_window_end_ms <= target_window_start_ms:
        raise ReferenceRetimeError("target reference-retime window is invalid")
    lines = reference_result.get("lines")
    if not isinstance(lines, list):
        raise ReferenceRetimeError("reference timeline result has invalid lines")

    retimed_lines: list[dict[str, Any]] = []
    last_index = -1
    for position, raw in enumerate(lines, start=1):
        index, start, end = _validate_reference_line(raw, position, last_index)
        mapped_start = map_reference_time_ms(start, segments)
        mapped_end = map_reference_time_ms(end, segments) if end is not None else None
        if mapped_end is not None and mapped_end <= mapped_start:
            raise ReferenceRetimeError("reference retime produced a non-positive cue duration")
        if mapped_start >= target_window_end_ms or (
            mapped_end is not None and mapped_end <= target_window_start_ms
        ):
            last_index = index
            continue
        mapped_start = max(mapped_start, target_window_start_ms)
        if mapped_end is not None:
            mapped_end = min(mapped_end, target_window_end_ms)
            if mapped_end <= mapped_start:
                last_index = index
                continue
        row = deepcopy(raw)
        row["mix_start_ms"] = mapped_start
        row["mix_end_ms"] = mapped_end
        retimed_lines.append(row)
        last_index = index

    return _finalize_result(
        reference_result,
        retimed_lines,
        target_window_start_ms=target_window_start_ms,
        target_window_end_ms=target_window_end_ms,
        mode="offset_segments",
    )


def retime_reference_result_with_retained_segments(
    reference_result: dict[str, Any],
    *,
    target_window_start_ms: int,
    target_window_end_ms: int,
    retained_segments: tuple[dict[str, int | None], ...],
) -> dict[str, Any]:
    """Project canonical lines through an explicit retained/spliced reference map.

    A canonical line is clipped to the retained interval that contains its
    surviving audio. Lines entirely inside a removed reference gap are dropped.
    If one line survives in more than one retained segment, the operation fails
    closed because a single canonical cue cannot represent disjoint audio
    without an explicit split policy.
    """

    if target_window_start_ms < 0 or target_window_end_ms <= target_window_start_ms:
        raise ReferenceRetimeError("target reference-retime window is invalid")
    lines = reference_result.get("lines")
    if not isinstance(lines, list):
        raise ReferenceRetimeError("reference timeline result has invalid lines")

    retimed_lines: list[dict[str, Any]] = []
    last_index = -1
    for position, raw in enumerate(lines, start=1):
        index, start, end = _validate_reference_line(raw, position, last_index)
        surviving: list[tuple[int, int | None, dict[str, int | None]]] = []
        for segment in retained_segments:
            ref_start = int(segment["reference_start_ms"])
            ref_end_value = segment["reference_end_ms"]
            ref_end = int(ref_end_value) if ref_end_value is not None else None
            overlap_start = max(start, ref_start)
            if end is None and ref_end is None:
                overlap_end = None
            elif end is None:
                overlap_end = ref_end
            elif ref_end is None:
                overlap_end = end
            else:
                overlap_end = min(end, ref_end)
            if overlap_end is not None and overlap_end <= overlap_start:
                continue
            if ref_end is not None and overlap_start >= ref_end:
                continue
            surviving.append((overlap_start, overlap_end, segment))
        if len(surviving) > 1:
            raise ReferenceRetimeError(
                f"reference timeline line {position} spans multiple retained splice segments; explicit split policy required"
            )
        if not surviving:
            last_index = index
            continue
        overlap_start, overlap_end, segment = surviving[0]
        ref_start = int(segment["reference_start_ms"])
        target_start = int(segment["target_start_ms"])
        mapped_start = target_start + (overlap_start - ref_start)
        mapped_end = (
            target_start + (overlap_end - ref_start)
            if overlap_end is not None
            else None
        )
        if mapped_end is not None and mapped_end <= mapped_start:
            raise ReferenceRetimeError("reference splice retime produced a non-positive cue duration")
        if mapped_start >= target_window_end_ms or (
            mapped_end is not None and mapped_end <= target_window_start_ms
        ):
            last_index = index
            continue
        mapped_start = max(mapped_start, target_window_start_ms)
        if mapped_end is not None:
            mapped_end = min(mapped_end, target_window_end_ms)
            if mapped_end <= mapped_start:
                last_index = index
                continue
        row = deepcopy(raw)
        row["mix_start_ms"] = mapped_start
        row["mix_end_ms"] = mapped_end
        if overlap_start != start or overlap_end != end:
            row["reference_splice_clipped"] = True
        retimed_lines.append(row)
        last_index = index

    return _finalize_result(
        reference_result,
        retimed_lines,
        target_window_start_ms=target_window_start_ms,
        target_window_end_ms=target_window_end_ms,
        mode="retained_segments",
    )
