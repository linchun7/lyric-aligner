"""Compose independently materialized cut and overlap canonical timelines.

The cut and overlap materializers remain independent. This layer only combines
already-validated results derived from the same review artifact. A confirmed
overlap interval is not automatically composable when it crosses a localized
cut boundary for the same occurrence. Overlap-projected canonical lines must
also prove that their source intervals do not intersect an explicit cut source
gap; otherwise composition remains fail-closed.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from lyric_aligner.timeline.overlap import (
    ConfirmedOverlapRegion,
    merge_primary_with_overlap_lines,
)


class TimelineCompositionError(ValueError):
    """Raised when cut/overlap materializations cannot be safely combined."""


def cut_boundary_times_ms(timeline_result: dict[str, Any]) -> tuple[int, ...]:
    cuts = timeline_result.get("cuts")
    if cuts in (None, []):
        return ()
    if not isinstance(cuts, list):
        raise TimelineCompositionError("cut-aware timeline cuts must be a list")
    values: list[int] = []
    for cut in cuts:
        if not isinstance(cut, dict):
            raise TimelineCompositionError("cut-aware timeline cut must be an object")
        try:
            value = int(round(float(cut["cut_mix_time"]) * 1000.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise TimelineCompositionError("cut-aware timeline has invalid cut_mix_time") from exc
        values.append(value)
    if values != sorted(values) or len(values) != len(set(values)):
        raise TimelineCompositionError(
            "cut-aware timeline cut boundaries must be unique/increasing"
        )
    return tuple(values)


def cut_source_gaps_ms(
    timeline_result: dict[str, Any],
) -> tuple[tuple[int, int], ...]:
    cuts = timeline_result.get("cuts")
    if cuts in (None, []):
        return ()
    if not isinstance(cuts, list):
        raise TimelineCompositionError("cut-aware timeline cuts must be a list")
    output: list[tuple[int, int]] = []
    for cut in cuts:
        if not isinstance(cut, dict):
            raise TimelineCompositionError("cut-aware timeline cut must be an object")
        try:
            start = int(round(float(cut["source_gap_start"]) * 1000.0))
            end = int(round(float(cut["source_gap_end"]) * 1000.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise TimelineCompositionError(
                "cut-aware timeline has invalid source gap"
            ) from exc
        if start < 0 or end <= start:
            raise TimelineCompositionError("cut-aware timeline source gap is invalid")
        output.append((start, end))
    if output != sorted(output) or any(
        right[0] < left[1] for left, right in zip(output, output[1:])
    ):
        raise TimelineCompositionError(
            "cut-aware timeline source gaps must be increasing/non-overlapping"
        )
    return tuple(output)


def regions_for_occurrence(
    regions: Iterable[ConfirmedOverlapRegion], occurrence_id: str
) -> list[ConfirmedOverlapRegion]:
    return [
        region
        for region in regions
        if occurrence_id in {region.left_occurrence_id, region.right_occurrence_id}
    ]


def validate_cut_overlap_disjoint(
    *,
    cut_timeline_result: dict[str, Any],
    occurrence_id: str,
    regions: Iterable[ConfirmedOverlapRegion],
) -> None:
    """Reject a combined timeline when an overlap interval crosses a cut boundary."""

    relevant = regions_for_occurrence(regions, occurrence_id)
    if not relevant:
        return
    for boundary_ms in cut_boundary_times_ms(cut_timeline_result):
        for region in relevant:
            if region.start_ms <= boundary_ms <= region.end_ms:
                raise TimelineCompositionError(
                    "confirmed overlap intersects a localized cut boundary; "
                    "joint acoustic composition is required"
                )


def overlap_delta_lines(
    overlap_timeline_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return only lines that were added/extended by overlap recomposition."""

    lines = overlap_timeline_result.get("lines")
    if not isinstance(lines, list):
        raise TimelineCompositionError("overlap timeline has no line list")
    output: list[dict[str, Any]] = []
    for line in lines:
        if not isinstance(line, dict):
            raise TimelineCompositionError("overlap timeline line must be an object")
        if (
            line.get("overlap_region_id")
            or line.get("overlap_region_ids")
            or line.get("overlap_candidate_id")
            or line.get("overlap_candidate_ids")
            or bool(line.get("overlap_recomposed"))
        ):
            output.append(deepcopy(line))
    return output


def validate_overlap_deltas_outside_cut_gaps(
    *,
    cut_timeline_result: dict[str, Any],
    delta_lines: Iterable[dict[str, Any]],
) -> None:
    """Do not reintroduce canonical source material removed by a cut.

    Boundary-local overlap projection retains canonical source timestamps from
    the projector. Composition requires that provenance. A finite source
    interval may touch a gap boundary but must not intersect the gap. An open
    interval is only safe when it starts after every relevant gap; otherwise its
    survival across the gap cannot be proven.
    """

    gaps = cut_source_gaps_ms(cut_timeline_result)
    if not gaps:
        return
    for line in delta_lines:
        try:
            source_start = int(line["source_start_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TimelineCompositionError(
                "overlap delta is missing canonical source_start_ms"
            ) from exc
        source_end_raw = line.get("source_end_ms")
        if source_end_raw is None:
            for gap_start, gap_end in gaps:
                if source_start < gap_end:
                    raise TimelineCompositionError(
                        "open overlap delta source interval may cross a confirmed cut gap"
                    )
            continue
        try:
            source_end = int(source_end_raw)
        except (TypeError, ValueError) as exc:
            raise TimelineCompositionError(
                "overlap delta has invalid canonical source_end_ms"
            ) from exc
        if source_end <= source_start:
            raise TimelineCompositionError(
                "overlap delta canonical source interval is invalid"
            )
        for gap_start, gap_end in gaps:
            if max(source_start, gap_start) < min(source_end, gap_end):
                raise TimelineCompositionError(
                    "overlap delta source interval intersects a confirmed cut gap"
                )


def compose_cut_and_overlap_result(
    *,
    cut_timeline_result: dict[str, Any],
    overlap_timeline_result: dict[str, Any],
    occurrence_id: str,
    regions: Iterable[ConfirmedOverlapRegion],
) -> dict[str, Any]:
    """Merge overlap-only delta lines onto the cut-aware canonical timeline."""

    if cut_timeline_result.get("cut_aware") is not True:
        raise TimelineCompositionError("combined cut/overlap base is not cut-aware")
    if str(cut_timeline_result.get("occurrence_id") or "") != occurrence_id:
        raise TimelineCompositionError("cut timeline occurrence identity mismatch")
    if str(overlap_timeline_result.get("occurrence_id") or "") != occurrence_id:
        raise TimelineCompositionError("overlap timeline occurrence identity mismatch")
    if str(cut_timeline_result.get("track_id") or "") != str(
        overlap_timeline_result.get("track_id") or ""
    ):
        raise TimelineCompositionError("cut/overlap timeline track identity mismatch")
    if str(cut_timeline_result.get("canonical_selection_sha256") or "") != str(
        overlap_timeline_result.get("canonical_selection_sha256") or ""
    ):
        raise TimelineCompositionError(
            "cut/overlap timeline canonical selection identity mismatch"
        )

    relevant = regions_for_occurrence(regions, occurrence_id)
    validate_cut_overlap_disjoint(
        cut_timeline_result=cut_timeline_result,
        occurrence_id=occurrence_id,
        regions=relevant,
    )
    deltas = overlap_delta_lines(overlap_timeline_result)
    validate_overlap_deltas_outside_cut_gaps(
        cut_timeline_result=cut_timeline_result,
        delta_lines=deltas,
    )
    merged = merge_primary_with_overlap_lines(
        cut_timeline_result,
        deltas,
        regions=relevant,
    )
    merged["cut_aware"] = True
    merged["combined_recomposition"] = {
        "cut_boundary_count": len(cut_boundary_times_ms(cut_timeline_result)),
        "overlap_region_ids": [region.region_id for region in relevant],
        "overlap_delta_line_count": len(deltas),
    }
    return merged
