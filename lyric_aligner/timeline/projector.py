"""Project canonical source-time lyrics onto the edited-mix timeline.

This is the first production v4 timeline truth. It consumes an already selected
TrackAsset canonical lyric stream plus a monotonic Source-to-Mix TimeWarp. It
never re-resolves assets and never uses editor text as canonical truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from lyric_aligner.assets.bindings import ResolvedAssetBinding
from lyric_aligner.text.canonical_lyrics import CanonicalLine, parse_canonical_lyrics


class TimelineProjectionError(ValueError):
    """Raised when a serialized TimeWarp cannot safely project canonical lyrics."""


@dataclass(frozen=True)
class ProjectionWindow:
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise TimelineProjectionError("invalid projection window")


def _mapping_parts(
    mapping: dict[str, Any],
) -> tuple[float, float, tuple[float, ...], tuple[float, ...]]:
    try:
        intercept = float(mapping["intercept"])
        base_slope = float(mapping["base_slope"])
        breakpoints = tuple(float(value) for value in mapping.get("breakpoints", []))
        deltas = tuple(float(value) for value in mapping.get("slope_deltas", []))
    except (KeyError, TypeError, ValueError) as exc:
        raise TimelineProjectionError("invalid serialized TimeWarp mapping") from exc
    if base_slope <= 0:
        raise TimelineProjectionError("TimeWarp base_slope must be positive")
    if len(breakpoints) != len(deltas):
        raise TimelineProjectionError("TimeWarp breakpoints/slope_deltas length mismatch")
    if any(right <= left for left, right in zip(breakpoints, breakpoints[1:])):
        raise TimelineProjectionError("TimeWarp breakpoints must be strictly increasing")
    slope = base_slope
    for delta in deltas:
        slope += delta
        if slope <= 0:
            raise TimelineProjectionError("TimeWarp local slope must remain positive")
    return intercept, base_slope, breakpoints, deltas


def source_time_at_mix(mapping: dict[str, Any], mix_time_seconds: float) -> float:
    intercept, base_slope, breakpoints, deltas = _mapping_parts(mapping)
    value = intercept + base_slope * float(mix_time_seconds)
    for breakpoint, delta in zip(breakpoints, deltas):
        value += delta * max(0.0, float(mix_time_seconds) - breakpoint)
    return value


def mix_time_for_source(mapping: dict[str, Any], source_time_seconds: float) -> float:
    """Invert a continuous positive-slope hinge TimeWarp analytically."""

    intercept, base_slope, breakpoints, deltas = _mapping_parts(mapping)
    target = float(source_time_seconds)
    if not breakpoints:
        return (target - intercept) / base_slope

    first_source = source_time_at_mix(mapping, breakpoints[0])
    if target <= first_source:
        return (target - intercept) / base_slope

    left_mix = breakpoints[0]
    left_source = first_source
    slope = base_slope + deltas[0]
    for index in range(1, len(breakpoints)):
        right_mix = breakpoints[index]
        right_source = source_time_at_mix(mapping, right_mix)
        if target <= right_source:
            return left_mix + (target - left_source) / slope
        left_mix = right_mix
        left_source = right_source
        slope += deltas[index]
    return left_mix + (target - left_source) / slope


def _project_ms(mapping: dict[str, Any], source_ms: int) -> int:
    return int(
        round(mix_time_for_source(mapping, float(source_ms) / 1000.0) * 1000.0)
    )


def _line_source_bounds(
    lines: list[CanonicalLine], index: int
) -> tuple[int, int | None, str]:
    line = lines[index]
    if line.tokens:
        start = line.tokens[0].start_ms
        token_end = next(
            (
                token.end_ms
                for token in reversed(line.tokens)
                if token.end_ms is not None
            ),
            None,
        )
        if token_end is not None and token_end > start:
            return start, token_end, "word_timing"
        if index + 1 < len(lines):
            return start, lines[index + 1].time_ms, "next_line_start"
        return start, None, "open_end"
    if index + 1 < len(lines):
        return line.time_ms, lines[index + 1].time_ms, "next_line_start"
    return line.time_ms, None, "open_end"


def _coarse_projection_authority(
    coarse_result: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a fail-closed terminal projection bound from coarse evidence.

    A bounded terminal disconnect means the monotonic path proved only a prefix
    of the requested occurrence interval. The final selected retrieval window
    still provides evidence through its own mix_end, but later retrieval rows do
    not grant ordinary timeline-projection authority.
    """

    coverage = coarse_result.get("path_coverage")
    if not isinstance(coverage, dict):
        return None
    status = str(coverage.get("status") or "")
    if status != "bounded_terminal_disconnect":
        return None

    windows = coarse_result.get("windows")
    path = coarse_result.get("path")
    if not isinstance(windows, list) or not isinstance(path, list):
        raise TimelineProjectionError(
            "bounded coarse coverage requires serialized windows and path"
        )
    try:
        selected_count = int(coverage["selected_window_count"])
        retrieved_count = int(coverage["retrieved_window_count"])
        excluded_count = int(coverage["excluded_trailing_window_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TimelineProjectionError(
            "invalid bounded coarse path_coverage counts"
        ) from exc
    if (
        retrieved_count != len(windows)
        or selected_count != len(path)
        or excluded_count != retrieved_count - selected_count
        or selected_count < 1
        or excluded_count < 1
        or selected_count >= retrieved_count
    ):
        raise TimelineProjectionError(
            "bounded coarse path_coverage does not match serialized evidence"
        )
    last_proven_window = windows[selected_count - 1]
    if not isinstance(last_proven_window, dict):
        raise TimelineProjectionError("invalid final proven coarse window")
    try:
        authority_end_ms = int(round(float(last_proven_window["mix_end"]) * 1000.0))
    except (KeyError, TypeError, ValueError) as exc:
        raise TimelineProjectionError("invalid final proven coarse mix_end") from exc
    if authority_end_ms <= 0:
        raise TimelineProjectionError("projection authority end must be positive")
    return {
        "status": "bounded_terminal_disconnect",
        "mix_end_ms": authority_end_ms,
        "selected_window_count": selected_count,
        "excluded_trailing_window_count": excluded_count,
    }


def _mapping_projection_authority(
    mapping: dict[str, Any],
) -> dict[str, Any] | None:
    authority = mapping.get("projection_authority")
    if authority is None:
        return None
    if not isinstance(authority, dict):
        raise TimelineProjectionError("invalid projection_authority payload")
    if str(authority.get("status") or "") != "bounded_terminal_disconnect":
        raise TimelineProjectionError("unsupported projection_authority status")
    try:
        mix_end_ms = int(authority["mix_end_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TimelineProjectionError("invalid projection_authority mix_end_ms") from exc
    if mix_end_ms <= 0:
        raise TimelineProjectionError("projection_authority mix_end_ms must be positive")
    return {**authority, "mix_end_ms": mix_end_ms}


def _project_canonical_lines_with_coverage(
    lines: Iterable[CanonicalLine],
    mapping: dict[str, Any],
    *,
    window: ProjectionWindow | None = None,
) -> tuple[list[dict[str, Any]], int]:
    rows = list(lines)
    projected: list[dict[str, Any]] = []
    authority = _mapping_projection_authority(mapping)
    authority_end_ms = None if authority is None else int(authority["mix_end_ms"])
    authority_omitted_line_count = 0

    for index, line in enumerate(rows):
        source_start_ms, source_end_ms, end_basis = _line_source_bounds(rows, index)
        mix_start_ms = _project_ms(mapping, source_start_ms)
        mix_end_ms = (
            _project_ms(mapping, source_end_ms)
            if source_end_ms is not None
            else None
        )

        if window is not None:
            effective_end = mix_end_ms if mix_end_ms is not None else mix_start_ms + 1
            if effective_end <= window.start_ms or mix_start_ms >= window.end_ms:
                continue

        tokens: list[dict[str, Any]] = []
        for token in line.tokens:
            token_start = _project_ms(mapping, token.start_ms)
            token_end = (
                _project_ms(mapping, token.end_ms)
                if token.end_ms is not None
                else None
            )
            tokens.append(
                {
                    "text": token.text,
                    "source_start_ms": token.start_ms,
                    "source_end_ms": token.end_ms,
                    "mix_start_ms": token_start,
                    "mix_end_ms": token_end,
                }
            )

        if authority_end_ms is not None:
            crosses_authority = (
                mix_start_ms >= authority_end_ms
                or mix_end_ms is None
                or mix_end_ms > authority_end_ms
                or any(
                    token["mix_start_ms"] >= authority_end_ms
                    or (
                        token["mix_end_ms"] is not None
                        and token["mix_end_ms"] > authority_end_ms
                    )
                    for token in tokens
                )
            )
            if crosses_authority:
                authority_omitted_line_count += 1
                continue

        projected.append(
            {
                "canonical_line_index": line.index,
                "text": line.text,
                "timing_format": line.timing_format,
                "source_start_ms": source_start_ms,
                "source_end_ms": source_end_ms,
                "mix_start_ms": mix_start_ms,
                "mix_end_ms": mix_end_ms,
                "end_basis": end_basis,
                "tokens": tokens,
            }
        )
    return projected, authority_omitted_line_count


def project_canonical_lines(
    lines: Iterable[CanonicalLine],
    mapping: dict[str, Any],
    *,
    window: ProjectionWindow | None = None,
) -> list[dict[str, Any]]:
    """Project line/token timing, respecting any proven terminal authority cap."""

    projected, _ = _project_canonical_lines_with_coverage(
        lines,
        mapping,
        window=window,
    )
    return projected


def project_binding_timeline(
    binding: ResolvedAssetBinding,
    mapping: dict[str, Any],
    *,
    window: ProjectionWindow | None = None,
) -> dict[str, Any]:
    lines = parse_canonical_lyrics(
        Path(binding.canonical_lyric_path),
        original_index_by_timestamp=binding.original_index_by_timestamp,
    )
    projected, authority_omitted_line_count = _project_canonical_lines_with_coverage(
        lines,
        mapping,
        window=window,
    )
    payload = {
        "occurrence_id": binding.occurrence_id,
        "ordinal": binding.ordinal,
        "track_id": binding.track_id,
        "artist": binding.artist,
        "title": binding.title,
        "language_profile": binding.language_profile,
        "canonical_selection_sha256": binding.canonical_selection_sha256,
        "window": None
        if window is None
        else {"start_ms": window.start_ms, "end_ms": window.end_ms},
        "line_count": len(projected),
        "lines": projected,
    }
    authority = _mapping_projection_authority(mapping)
    if authority is not None:
        payload["projection_coverage"] = {
            **authority,
            "requested_window_end_ms": None if window is None else window.end_ms,
            "authority_omitted_line_count": authority_omitted_line_count,
        }
    return payload


def effective_timewarp(
    coarse_payload: dict[str, Any],
    fine_payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool, str]:
    """Choose the production mapping and attach coarse projection authority."""

    coarse = coarse_payload.get("result", {})
    if not isinstance(coarse, dict):
        raise TimelineProjectionError("invalid coarse alignment payload")
    authority = _coarse_projection_authority(coarse)

    if fine_payload is not None:
        fine = fine_payload.get("result", {})
        if bool(fine.get("applied")) and isinstance(fine.get("timewarp"), dict):
            timewarp = fine["timewarp"]
            mapping = timewarp.get("mapping")
            if not isinstance(mapping, dict):
                raise TimelineProjectionError("fine TimeWarp has no mapping")
            effective_mapping = dict(mapping)
            if authority is not None:
                effective_mapping["projection_authority"] = authority
            return effective_mapping, bool(timewarp.get("blocked", False)), "fine"

    timewarp = coarse.get("timewarp")
    if not isinstance(timewarp, dict):
        raise TimelineProjectionError("coarse alignment has no TimeWarp")
    mapping = timewarp.get("mapping")
    if not isinstance(mapping, dict):
        raise TimelineProjectionError("coarse TimeWarp has no mapping")
    effective_mapping = dict(mapping)
    if authority is not None:
        effective_mapping["projection_authority"] = authority
    return effective_mapping, bool(timewarp.get("blocked", False)), "coarse"
