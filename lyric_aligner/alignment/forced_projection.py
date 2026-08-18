"""Project source-side forced-alignment evidence into edited-mix time.

Continuous AFFINE/PIECEWISE_RATE mappings use the same analytical inverse used
by canonical timeline projection. CUT_AWARE mappings project only intervals
fully contained in one retained source segment; evidence crossing a confirmed
source gap is explicitly marked unprojectable rather than bridged into a fake
single mix interval.
"""

from __future__ import annotations

import math
from typing import Any

from lyric_aligner.timeline.projector import (
    TimelineProjectionError,
    mix_time_for_source,
)


FORCED_MIX_PROJECTION_SCHEMA_VERSION = "1.0"


class ForcedMixProjectionError(ValueError):
    """Raised when forced evidence or Source-to-Mix mapping is inconsistent."""


def _finite_ms(value: Any, *, label: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ForcedMixProjectionError(f"{label} is invalid") from exc
    if not math.isfinite(number):
        raise ForcedMixProjectionError(f"{label} must be finite")
    return int(round(number))


def _project_continuous_ms(mapping: dict[str, Any], source_ms: int) -> int:
    try:
        value = mix_time_for_source(mapping, source_ms / 1000.0)
    except TimelineProjectionError as exc:
        raise ForcedMixProjectionError(str(exc)) from exc
    if not math.isfinite(value):
        raise ForcedMixProjectionError("Source-to-Mix projection is non-finite")
    return int(round(value * 1000.0))


def _cut_segments(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    if mapping.get("kind") != "CUT_AWARE":
        raise ForcedMixProjectionError("mapping is not CUT_AWARE")
    raw = mapping.get("segments")
    if not isinstance(raw, list) or not raw:
        raise ForcedMixProjectionError("CUT_AWARE mapping has no segments")
    output: list[dict[str, Any]] = []
    previous_mix_end: float | None = None
    previous_source_end: float | None = None
    for expected_index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ForcedMixProjectionError("CUT_AWARE segment must be an object")
        try:
            index = int(row["index"])
            mix_start = float(row["mix_start"])
            mix_end = float(row["mix_end"])
            source_start = float(row["source_start"])
            source_end = float(row["source_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ForcedMixProjectionError(
                "CUT_AWARE segment coordinates are invalid"
            ) from exc
        if index != expected_index:
            raise ForcedMixProjectionError("CUT_AWARE segment indices are not contiguous")
        if not all(
            math.isfinite(value)
            for value in (mix_start, mix_end, source_start, source_end)
        ):
            raise ForcedMixProjectionError("CUT_AWARE segment coordinate is non-finite")
        if mix_end <= mix_start or source_end <= source_start:
            raise ForcedMixProjectionError("CUT_AWARE segment is not monotonic")
        if not isinstance(row.get("mapping"), dict):
            raise ForcedMixProjectionError("CUT_AWARE segment has no continuous mapping")
        if previous_mix_end is not None and abs(mix_start - previous_mix_end) > 1e-3:
            raise ForcedMixProjectionError("CUT_AWARE mix segments do not meet")
        if previous_source_end is not None and source_start <= previous_source_end:
            raise ForcedMixProjectionError(
                "CUT_AWARE source segments do not preserve a forward gap"
            )
        output.append(row)
        previous_mix_end = mix_end
        previous_source_end = source_end
    return output


def _segment_for_source(
    segments: list[dict[str, Any]], source_ms: int
) -> dict[str, Any] | None:
    source_seconds = source_ms / 1000.0
    for segment in segments:
        if (
            float(segment["source_start"]) - 1e-6
            <= source_seconds
            <= float(segment["source_end"]) + 1e-6
        ):
            return segment
    return None


def _project_cut_point(segment: dict[str, Any], source_ms: int) -> int:
    projected = _project_continuous_ms(segment["mapping"], source_ms)
    mix_start_ms = int(round(float(segment["mix_start"]) * 1000.0))
    mix_end_ms = int(round(float(segment["mix_end"]) * 1000.0))
    if projected < mix_start_ms - 50 or projected > mix_end_ms + 50:
        raise ForcedMixProjectionError(
            "source timestamp projects outside retained CUT_AWARE segment"
        )
    return min(max(projected, mix_start_ms), mix_end_ms)


def _project_interval(
    mapping: dict[str, Any],
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    if end_ms <= start_ms:
        raise ForcedMixProjectionError("forced-alignment interval is not monotonic")
    if mapping.get("kind") != "CUT_AWARE":
        mix_start = _project_continuous_ms(mapping, start_ms)
        mix_end = _project_continuous_ms(mapping, end_ms)
        if mix_end <= mix_start:
            raise ForcedMixProjectionError("projected mix interval is not monotonic")
        return {
            "projection_status": "projected",
            "projection_reason": None,
            "cut_aware_segment_index": None,
            "mix_start_ms": mix_start,
            "mix_end_ms": mix_end,
        }

    segments = _cut_segments(mapping)
    start_segment = _segment_for_source(segments, start_ms)
    end_segment = _segment_for_source(segments, end_ms)
    if start_segment is None or end_segment is None:
        return {
            "projection_status": "unprojectable",
            "projection_reason": "source_boundary_in_confirmed_gap_or_outside_retained_range",
            "cut_aware_segment_index": None,
            "mix_start_ms": None,
            "mix_end_ms": None,
        }
    if int(start_segment["index"]) != int(end_segment["index"]):
        return {
            "projection_status": "unprojectable",
            "projection_reason": "source_interval_crosses_confirmed_cut",
            "cut_aware_segment_index": None,
            "mix_start_ms": None,
            "mix_end_ms": None,
        }
    mix_start = _project_cut_point(start_segment, start_ms)
    mix_end = _project_cut_point(end_segment, end_ms)
    if mix_end <= mix_start:
        raise ForcedMixProjectionError("cut-aware projected interval is not monotonic")
    return {
        "projection_status": "projected",
        "projection_reason": None,
        "cut_aware_segment_index": int(start_segment["index"]),
        "mix_start_ms": mix_start,
        "mix_end_ms": mix_end,
    }


def project_forced_alignment_to_mix(
    *,
    forced_evidence: dict[str, Any],
    mappings_by_occurrence: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Project P7 source evidence into mix time without changing authority."""

    if str(forced_evidence.get("backend") or "") != "external_forced_aligner":
        raise ForcedMixProjectionError(
            "forced evidence backend must be external_forced_aligner"
        )
    raw_jobs = forced_evidence.get("jobs")
    if not isinstance(raw_jobs, list):
        raise ForcedMixProjectionError("forced evidence jobs must be a list")

    output_jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    projected_line_count = 0
    unprojectable_line_count = 0
    projected_span_count = 0
    unprojectable_span_count = 0

    for raw in raw_jobs:
        if not isinstance(raw, dict):
            raise ForcedMixProjectionError("forced evidence job must be an object")
        job_id = str(raw.get("job_id") or "").strip()
        occurrence_id = str(raw.get("occurrence_id") or "").strip()
        if not job_id or job_id in seen_ids:
            raise ForcedMixProjectionError(
                "forced evidence job IDs must be unique/non-empty"
            )
        seen_ids.add(job_id)
        if not occurrence_id:
            raise ForcedMixProjectionError("forced evidence job has no occurrence_id")
        mapping = mappings_by_occurrence.get(occurrence_id)
        if not isinstance(mapping, dict):
            raise ForcedMixProjectionError(
                f"no Source-to-Mix mapping for occurrence {occurrence_id}"
            )

        line_source_start = _finite_ms(
            raw.get("line_source_start_ms"), label="line_source_start_ms"
        )
        line_source_end = _finite_ms(
            raw.get("line_source_end_ms"), label="line_source_end_ms"
        )
        line_projection = _project_interval(
            mapping, line_source_start, line_source_end
        )
        if line_projection["projection_status"] == "projected":
            projected_line_count += 1
        else:
            unprojectable_line_count += 1

        raw_spans = raw.get("spans") or []
        if not isinstance(raw_spans, list):
            raise ForcedMixProjectionError("forced evidence spans must be a list")
        spans: list[dict[str, Any]] = []
        for span in raw_spans:
            if not isinstance(span, dict):
                raise ForcedMixProjectionError("forced evidence span must be an object")
            source_start = _finite_ms(
                span.get("source_start_ms"), label="span source_start_ms"
            )
            source_end = _finite_ms(
                span.get("source_end_ms"), label="span source_end_ms"
            )
            projection = _project_interval(mapping, source_start, source_end)
            if projection["projection_status"] == "projected":
                projected_span_count += 1
            else:
                unprojectable_span_count += 1
            spans.append(
                {
                    "span_index": int(span.get("span_index", len(spans))),
                    "char_start": int(span["char_start"]),
                    "char_end": int(span["char_end"]),
                    "canonical_fragment_sha256": str(
                        span.get("canonical_fragment_sha256") or ""
                    ),
                    "source_start_ms": source_start,
                    "source_end_ms": source_end,
                    "confidence": span.get("confidence"),
                    **projection,
                }
            )

        output_jobs.append(
            {
                "job_id": job_id,
                "occurrence_id": occurrence_id,
                "track_id": str(raw.get("track_id") or ""),
                "ordinal": int(raw.get("ordinal", -1)),
                "canonical_line_index": int(raw["canonical_line_index"]),
                "canonical_text_sha256": str(
                    raw.get("canonical_text_sha256") or ""
                ),
                "source_audio_sha256": str(raw.get("source_audio_sha256") or ""),
                "source_window_ms": raw.get("source_window_ms"),
                "line_source_start_ms": line_source_start,
                "line_source_end_ms": line_source_end,
                "line_confidence": raw.get("line_confidence"),
                "backend_id": str(raw.get("backend_id") or ""),
                "backend_version": str(raw.get("backend_version") or ""),
                "model_id": str(raw.get("model_id") or ""),
                "model_revision": str(raw.get("model_revision") or ""),
                **line_projection,
                "span_count": len(spans),
                "spans": spans,
            }
        )

    return {
        "schema_version": FORCED_MIX_PROJECTION_SCHEMA_VERSION,
        "mode": "forced_alignment_mix_projection",
        "source_evidence_backend": "external_forced_aligner",
        "canonical_text_authority": "canonical_lyrics_only",
        "primary_timing_authority": "source_to_mix_only",
        "forced_alignment_authority": "auxiliary_acoustic_evidence_only",
        "job_count": len(output_jobs),
        "projected_line_count": projected_line_count,
        "unprojectable_line_count": unprojectable_line_count,
        "projected_span_count": projected_span_count,
        "unprojectable_span_count": unprojectable_span_count,
        "jobs": output_jobs,
        "safety": (
            "CUT_AWARE intervals crossing confirmed source gaps are not bridged "
            "into a single mix-time boundary"
        ),
    }
