"""Production lexical-floor audit against already-resolved canonical occurrences.

This is deliberately independent of timing authority. It verifies that every lexical
character materialized by a production candidate is exactly owned by the canonical
evaluation stream and that every canonical character selected for the final mix is
covered once. It does not re-resolve raw LRC files or claim the upstream canonical
selection itself is infallible.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from lyric_aligner.text_repair import _normalize_for_match, read_srt

SCHEMA_VERSION = "production-lexical-floor-audit-1.0"
POLICY_ID = "resolved-canonical-character-floor-1.0"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _int_field(raw: Any, field: str, position: int, *, allow_blank: bool = False) -> int | None:
    if allow_blank and raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"row {position} has invalid {field}") from exc
    if value < 0:
        raise ValueError(f"row {position} has negative {field}")
    return value


def _canonical_layout(
    rows: Sequence[Mapping[str, str]],
) -> tuple[
    dict[tuple[str, int], str],
    dict[str, str],
    dict[tuple[str, int], tuple[int, int]],
    dict[str, list[int]],
]:
    identities: dict[tuple[str, int], str] = {}
    by_occurrence: dict[str, list[tuple[int, str]]] = {}
    for position, row in enumerate(rows, start=1):
        occurrence_id = str(row.get("occurrence_id") or "").strip()
        if not occurrence_id:
            raise ValueError(f"canonical row {position} is missing occurrence_id")
        line_index = _int_field(row.get("canonical_line_index"), "canonical_line_index", position)
        assert line_index is not None
        text = str(row.get("text") or "")
        normalized = _normalize_for_match(text)
        if not normalized:
            raise ValueError(f"canonical row {position} has blank lexical text")
        key = (occurrence_id, line_index)
        if key in identities:
            raise ValueError("canonical evaluation contains duplicate occurrence/line identity")
        identities[key] = text
        by_occurrence.setdefault(occurrence_id, []).append((line_index, normalized))

    streams: dict[str, str] = {}
    spans: dict[tuple[str, int], tuple[int, int]] = {}
    ordered: dict[str, list[int]] = {}
    for occurrence_id, values in by_occurrence.items():
        values.sort(key=lambda item: item[0])
        cursor = 0
        parts: list[str] = []
        ordered[occurrence_id] = []
        for line_index, normalized in values:
            start = cursor
            cursor += len(normalized)
            spans[(occurrence_id, line_index)] = (start, cursor)
            ordered[occurrence_id].append(line_index)
            parts.append(normalized)
        streams[occurrence_id] = "".join(parts)
    return identities, streams, spans, ordered


def _parse_line_indices(row: Mapping[str, str], position: int) -> list[int]:
    multi_raw = row.get("canonical_line_indices")
    if multi_raw not in (None, ""):
        try:
            parsed = json.loads(str(multi_raw))
        except json.JSONDecodeError as exc:
            raise ValueError(f"row {position} has invalid canonical_line_indices") from exc
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(f"row {position} has invalid canonical_line_indices")
        values: list[int] = []
        for value in parsed:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"row {position} has invalid canonical_line_indices")
            values.append(value)
        if len(set(values)) != len(values) or values != sorted(values):
            raise ValueError(f"row {position} has non-monotonic canonical_line_indices")
        single = _int_field(row.get("canonical_line_index"), "canonical_line_index", position, allow_blank=True)
        if single is not None and single not in values:
            raise ValueError(f"row {position} canonical ownership fields disagree")
        return values
    single = _int_field(row.get("canonical_line_index"), "canonical_line_index", position, allow_blank=True)
    return [] if single is None else [single]


def _fallback_span(
    occurrence_id: str,
    claims: Sequence[int],
    *,
    spans: Mapping[tuple[str, int], tuple[int, int]],
    ordered: Mapping[str, Sequence[int]],
    position: int,
) -> tuple[int, int]:
    if not claims:
        raise ValueError(f"row {position} has lexical text but no canonical ownership")
    order = list(ordered.get(occurrence_id, ()))
    if not order:
        raise ValueError(f"row {position} references unknown occurrence {occurrence_id}")
    positions: list[int] = []
    for claim in claims:
        try:
            positions.append(order.index(claim))
        except ValueError as exc:
            raise ValueError(f"row {position} references unknown canonical line {claim}") from exc
    if positions != list(range(positions[0], positions[0] + len(positions))):
        raise ValueError(f"row {position} canonical_line_indices are not contiguous in occurrence")
    return spans[(occurrence_id, claims[0])][0], spans[(occurrence_id, claims[-1])][1]


def audit_resolved_lexical_floor(
    *,
    canonical_evaluation_audit: Path,
    final_srt: Path,
    final_audit: Path,
) -> dict[str, Any]:
    canonical_rows = _read_csv(canonical_evaluation_audit)
    final_rows = _read_csv(final_audit)
    _, _, _, cues = read_srt(final_srt)
    if len(cues) != len(final_rows):
        raise ValueError("final SRT cue count differs from final audit row count")
    identities, streams, line_spans, ordered = _canonical_layout(canonical_rows)

    intervals: dict[str, list[tuple[int, int, int]]] = {occurrence_id: [] for occurrence_id in streams}
    lexical_error_count = 0
    srt_audit_text_mismatch_count = 0
    unowned_lexical_cue_count = 0
    nonlexical_cue_count = 0
    diagnostics: list[dict[str, Any]] = []

    for position, (cue, row) in enumerate(zip(cues, final_rows), start=1):
        row_text = str(row.get("text") or "")
        if cue.text != row_text:
            srt_audit_text_mismatch_count += 1
            diagnostics.append({
                "position": position,
                "kind": "srt_audit_text_mismatch",
            })
        normalized = _normalize_for_match(cue.text)
        if not normalized:
            nonlexical_cue_count += 1
            continue
        occurrence_id = str(row.get("occurrence_id") or "").strip()
        if occurrence_id not in streams:
            unowned_lexical_cue_count += 1
            diagnostics.append({
                "position": position,
                "kind": "missing_or_unknown_occurrence",
                "occurrence_id": occurrence_id,
            })
            continue

        start = _int_field(row.get("canonical_content_start"), "canonical_content_start", position, allow_blank=True)
        end = _int_field(row.get("canonical_content_end"), "canonical_content_end", position, allow_blank=True)
        if (start is None) != (end is None):
            raise ValueError(f"row {position} has partial canonical content span")
        if start is None:
            claims = _parse_line_indices(row, position)
            try:
                start, end = _fallback_span(
                    occurrence_id,
                    claims,
                    spans=line_spans,
                    ordered=ordered,
                    position=position,
                )
            except ValueError as exc:
                unowned_lexical_cue_count += 1
                diagnostics.append({
                    "position": position,
                    "kind": "invalid_canonical_ownership",
                    "detail": str(exc),
                })
                continue
        assert end is not None
        stream = streams[occurrence_id]
        if not (0 <= start < end <= len(stream)):
            raise ValueError(f"row {position} canonical content span is outside occurrence stream")
        expected = stream[start:end]
        if normalized != expected:
            lexical_error_count += 1
            diagnostics.append({
                "position": position,
                "kind": "canonical_lexical_mismatch",
                "occurrence_id": occurrence_id,
                "canonical_content_start": start,
                "canonical_content_end": end,
            })
        intervals[occurrence_id].append((start, end, position))

    coverage_gap_count = 0
    coverage_overlap_count = 0
    covered_character_count = 0
    occurrence_reports: dict[str, Any] = {}
    for occurrence_id, stream in streams.items():
        cursor = 0
        local_gaps = 0
        local_overlaps = 0
        merged_covered = 0
        for start, end, position in sorted(intervals[occurrence_id]):
            if start > cursor:
                local_gaps += 1
                diagnostics.append({
                    "position": position,
                    "kind": "canonical_coverage_gap",
                    "occurrence_id": occurrence_id,
                    "gap": [cursor, start],
                })
            elif start < cursor:
                local_overlaps += 1
                diagnostics.append({
                    "position": position,
                    "kind": "canonical_coverage_overlap",
                    "occurrence_id": occurrence_id,
                    "overlap": [start, min(cursor, end)],
                })
            if end > cursor:
                merged_covered += end - max(cursor, start)
                cursor = end
        if cursor < len(stream):
            local_gaps += 1
            diagnostics.append({
                "position": None,
                "kind": "canonical_coverage_gap",
                "occurrence_id": occurrence_id,
                "gap": [cursor, len(stream)],
            })
        coverage_gap_count += local_gaps
        coverage_overlap_count += local_overlaps
        covered_character_count += merged_covered
        occurrence_reports[occurrence_id] = {
            "canonical_character_count": len(stream),
            "covered_character_count": merged_covered,
            "interval_count": len(intervals[occurrence_id]),
            "coverage_gap_count": local_gaps,
            "coverage_overlap_count": local_overlaps,
            "complete": local_gaps == 0 and local_overlaps == 0 and merged_covered == len(stream),
        }

    error_count = (
        lexical_error_count
        + srt_audit_text_mismatch_count
        + unowned_lexical_cue_count
        + coverage_gap_count
        + coverage_overlap_count
    )
    canonical_character_count = sum(len(stream) for stream in streams.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "status": "complete" if error_count == 0 else "failed",
        "publish_authority": "audit_only",
        "canonical_truth_scope": (
            "relative_to_already_resolved_canonical_evaluation; this audit does not independently prove "
            "that upstream lyric source/version/occurrence selection is correct"
        ),
        "timing_scope": "not_evaluated_here",
        "canonical_evaluation_row_count": len(canonical_rows),
        "final_cue_count": len(cues),
        "occurrence_count": len(streams),
        "canonical_character_count": canonical_character_count,
        "covered_character_count": covered_character_count,
        "lexical_error_count": lexical_error_count,
        "srt_audit_text_mismatch_count": srt_audit_text_mismatch_count,
        "unowned_lexical_cue_count": unowned_lexical_cue_count,
        "nonlexical_cue_count": nonlexical_cue_count,
        "coverage_gap_count": coverage_gap_count,
        "coverage_overlap_count": coverage_overlap_count,
        "occurrences": occurrence_reports,
        "diagnostics": diagnostics,
    }
