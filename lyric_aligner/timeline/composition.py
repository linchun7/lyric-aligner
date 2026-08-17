"""Compose independently materialized cut and overlap canonical timelines.

The cut and overlap materializers remain independent. This layer only combines
already-validated results derived from the same review artifact. A confirmed
overlap interval is not automatically composable when it crosses a localized
cut boundary for the same occurrence; that case needs a joint acoustic model
and remains fail-closed.
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
        raise TimelineCompositionError("cut-aware timeline cut boundaries must be unique/increasing")
    return tuple(values)


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


def overlap_delta_lines(overlap_timeline_result: dict[str, Any]) -> list[dict[str, Any]]:
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
