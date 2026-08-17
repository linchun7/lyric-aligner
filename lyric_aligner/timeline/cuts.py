"""Project canonical lyrics through explicit cut-aware Source-to-Mix mappings.

Line-LRC text is never guessed across a confirmed source gap. Word-timed
Enhanced LRC/QRC can safely materialize canonical fragments when complete tokens
survive on either side of a cut. A token itself intersected by a cut remains a
review issue because the audible canonical text is not provable from token-level
metadata alone.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from lyric_aligner.assets.bindings import ResolvedAssetBinding
from lyric_aligner.text.canonical_lyrics import CanonicalLine, CanonicalToken, parse_canonical_lyrics
from lyric_aligner.timeline.projector import mix_time_for_source


class CutTimelineProjectionError(ValueError):
    """Raised when a cut-aware mapping/timeline cannot be interpreted safely."""


def _segments(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    if mapping.get("kind") != "CUT_AWARE":
        raise CutTimelineProjectionError("cut timeline requires CUT_AWARE mapping")
    rows = mapping.get("segments")
    if not isinstance(rows, list) or not rows:
        raise CutTimelineProjectionError("CUT_AWARE mapping has no segments")
    output: list[dict[str, Any]] = []
    previous_mix_end = None
    previous_source_end = None
    for expected_index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CutTimelineProjectionError("CUT_AWARE segment must be an object")
        try:
            index = int(row["index"])
            mix_start = float(row["mix_start"])
            mix_end = float(row["mix_end"])
            source_start = float(row["source_start"])
            source_end = float(row["source_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CutTimelineProjectionError("CUT_AWARE segment coordinates are invalid") from exc
        if index != expected_index:
            raise CutTimelineProjectionError("CUT_AWARE segment indices must be contiguous")
        if mix_end <= mix_start or source_end <= source_start:
            raise CutTimelineProjectionError("CUT_AWARE segment is not monotonic")
        if not isinstance(row.get("mapping"), dict):
            raise CutTimelineProjectionError("CUT_AWARE segment has no continuous mapping")
        if previous_mix_end is not None and abs(mix_start - previous_mix_end) > 1e-3:
            raise CutTimelineProjectionError("CUT_AWARE mix segments must meet at cut boundaries")
        if previous_source_end is not None and source_start <= previous_source_end:
            raise CutTimelineProjectionError("CUT_AWARE source segments must preserve forward gaps")
        output.append(row)
        previous_mix_end = mix_end
        previous_source_end = source_end
    return output


def _cuts(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    rows = mapping.get("cuts")
    if not isinstance(rows, list):
        raise CutTimelineProjectionError("CUT_AWARE mapping cuts must be a list")
    return rows


def _segment_for_source(segments: list[dict[str, Any]], source_seconds: float) -> dict[str, Any] | None:
    for segment in segments:
        start = float(segment["source_start"])
        end = float(segment["source_end"])
        if start - 1e-6 <= source_seconds <= end + 1e-6:
            return segment
    return None


def _gap_for_source(mapping: dict[str, Any], source_seconds: float) -> dict[str, Any] | None:
    for cut in _cuts(mapping):
        gap_start = float(cut["source_gap_start"])
        gap_end = float(cut["source_gap_end"])
        if gap_start - 1e-6 <= source_seconds < gap_end - 1e-6:
            return cut
    return None


def _project_source(segment: dict[str, Any], source_seconds: float) -> float:
    value = mix_time_for_source(segment["mapping"], source_seconds)
    if value < float(segment["mix_start"]) - 0.05 or value > float(segment["mix_end"]) + 0.05:
        raise CutTimelineProjectionError("source timestamp projects outside its cut-aware segment")
    return min(max(value, float(segment["mix_start"])), float(segment["mix_end"]))


def _line_source_bounds(lines: list[CanonicalLine], index: int) -> tuple[int, int | None, str]:
    line = lines[index]
    if line.tokens:
        start = line.tokens[0].start_ms
        token_end = next(
            (token.end_ms for token in reversed(line.tokens) if token.end_ms is not None),
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


def _gap_intersections(mapping: dict[str, Any], start_ms: int, end_ms: int | None) -> list[dict[str, Any]]:
    if end_ms is None:
        return []
    start = start_ms / 1000.0
    end = end_ms / 1000.0
    output: list[dict[str, Any]] = []
    for cut in _cuts(mapping):
        gap_start = float(cut["source_gap_start"])
        gap_end = float(cut["source_gap_end"])
        if max(start, gap_start) < min(end, gap_end):
            output.append(cut)
    return output


def _token_interval(token: CanonicalToken) -> tuple[float, float | None]:
    start = token.start_ms / 1000.0
    end = token.end_ms / 1000.0 if token.end_ms is not None else None
    return start, end


def _token_crosses_gap(token: CanonicalToken, mapping: dict[str, Any]) -> bool:
    start, end = _token_interval(token)
    if end is None:
        return False
    for cut in _cuts(mapping):
        gap_start = float(cut["source_gap_start"])
        gap_end = float(cut["source_gap_end"])
        if start < gap_start < end or start < gap_end < end:
            return True
    return False


def _project_token(token: CanonicalToken, segment: dict[str, Any]) -> dict[str, Any]:
    start_s = token.start_ms / 1000.0
    mix_start = _project_source(segment, start_s)
    mix_end = None
    if token.end_ms is not None:
        end_s = token.end_ms / 1000.0
        end_segment = _segment_for_source([segment], end_s)
        if end_segment is not None:
            mix_end = _project_source(segment, end_s)
    return {
        "text": token.text,
        "source_start_ms": token.start_ms,
        "source_end_ms": token.end_ms,
        "mix_start_ms": int(round(mix_start * 1000.0)),
        "mix_end_ms": int(round(mix_end * 1000.0)) if mix_end is not None else None,
    }


def _word_timed_fragments(
    line: CanonicalLine,
    mapping: dict[str, Any],
    segments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: list[tuple[int, list[CanonicalToken]]] = []
    issues: list[dict[str, Any]] = []
    for token_index, token in enumerate(line.tokens):
        if _token_crosses_gap(token, mapping):
            issues.append(
                {
                    "kind": "canonical_fragment",
                    "code": "token_intersects_confirmed_cut",
                    "canonical_line_index": line.index,
                    "token_index": token_index,
                    "token_text": token.text,
                    "status": "review",
                    "reason": "confirmed cut intersects a timed canonical token; exact audible text is unresolved",
                }
            )
            continue
        segment = _segment_for_source(segments, token.start_ms / 1000.0)
        if segment is None:
            continue
        segment_index = int(segment["index"])
        if groups and groups[-1][0] == segment_index:
            groups[-1][1].append(token)
        else:
            groups.append((segment_index, [token]))

    fragments: list[dict[str, Any]] = []
    kept_count = sum(len(tokens) for _, tokens in groups)
    for fragment_index, (segment_index, tokens) in enumerate(groups):
        segment = segments[segment_index]
        projected_tokens = [_project_token(token, segment) for token in tokens]
        text = "".join(token.text for token in tokens).strip()
        if not text:
            continue
        first = projected_tokens[0]
        last = projected_tokens[-1]
        source_start_ms = tokens[0].start_ms
        source_end_ms = tokens[-1].end_ms
        mix_start_ms = int(first["mix_start_ms"])
        mix_end_ms = last["mix_end_ms"]
        full_line_survives = kept_count == len(line.tokens) and len(groups) == 1
        fragments.append(
            {
                "canonical_line_index": line.index,
                "fragment_index": fragment_index,
                "text": line.text if full_line_survives else text,
                "canonical_full_text": line.text,
                "canonical_fragment": not full_line_survives,
                "timing_format": line.timing_format,
                "source_start_ms": source_start_ms,
                "source_end_ms": source_end_ms,
                "mix_start_ms": mix_start_ms,
                "mix_end_ms": mix_end_ms,
                "end_basis": "cut_aware_word_timing",
                "cut_aware_segment_index": segment_index,
                "tokens": projected_tokens,
            }
        )
    return fragments, issues


def project_cut_aware_lines(
    lines: list[CanonicalLine],
    mapping: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    segments = _segments(mapping)
    projected: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []

    for index, line in enumerate(lines):
        source_start_ms, source_end_ms, end_basis = _line_source_bounds(lines, index)
        if line.tokens:
            fragments, fragment_issues = _word_timed_fragments(line, mapping, segments)
            projected.extend(fragments)
            issues.extend(fragment_issues)
            if not fragments and not fragment_issues:
                omitted.append(
                    {
                        "canonical_line_index": line.index,
                        "text": line.text,
                        "reason": "all_word_timed_tokens_removed_by_confirmed_cut",
                    }
                )
            continue

        start_seconds = source_start_ms / 1000.0
        start_segment = _segment_for_source(segments, start_seconds)
        if start_segment is None:
            containing_gap = _gap_for_source(mapping, start_seconds)
            if containing_gap is not None:
                gap_end = float(containing_gap["source_gap_end"])
                if source_end_ms is not None and source_end_ms / 1000.0 <= gap_end + 1e-6:
                    omitted.append(
                        {
                            "canonical_line_index": line.index,
                            "text": line.text,
                            "reason": "entire_line_interval_removed_by_confirmed_cut",
                        }
                    )
                else:
                    issues.append(
                        {
                            "kind": "canonical_fragment",
                            "code": "line_lrc_starts_in_confirmed_cut",
                            "canonical_line_index": line.index,
                            "text": line.text,
                            "status": "review",
                            "reason": (
                                "line-LRC starts inside a confirmed source gap but extends beyond "
                                "the gap or has no finite end; surviving canonical characters cannot "
                                "be inferred safely"
                            ),
                            "cut_candidate_ids": [str(containing_gap.get("candidate_id") or "")],
                        }
                    )
            else:
                omitted.append(
                    {
                        "canonical_line_index": line.index,
                        "text": line.text,
                        "reason": "line_outside_retained_source_range",
                    }
                )
            continue

        crossings = _gap_intersections(mapping, source_start_ms, source_end_ms)
        if crossings:
            issues.append(
                {
                    "kind": "canonical_fragment",
                    "code": "line_lrc_intersects_confirmed_cut",
                    "canonical_line_index": line.index,
                    "text": line.text,
                    "status": "review",
                    "reason": (
                        "line-LRC interval intersects a confirmed cut but has no token timing; "
                        "audible canonical fragment cannot be inferred safely"
                    ),
                    "cut_candidate_ids": [str(cut.get("candidate_id") or "") for cut in crossings],
                }
            )
            continue

        mix_start = _project_source(start_segment, start_seconds)
        mix_end = None
        if source_end_ms is not None:
            end_segment = _segment_for_source(segments, source_end_ms / 1000.0)
            if end_segment is None or int(end_segment["index"]) != int(start_segment["index"]):
                issues.append(
                    {
                        "kind": "canonical_fragment",
                        "code": "line_lrc_crosses_cut_segment",
                        "canonical_line_index": line.index,
                        "text": line.text,
                        "status": "review",
                        "reason": "line-LRC end is not in the same retained cut-aware segment",
                    }
                )
                continue
            mix_end = _project_source(end_segment, source_end_ms / 1000.0)
        projected.append(
            {
                "canonical_line_index": line.index,
                "text": line.text,
                "timing_format": line.timing_format,
                "source_start_ms": source_start_ms,
                "source_end_ms": source_end_ms,
                "mix_start_ms": int(round(mix_start * 1000.0)),
                "mix_end_ms": int(round(mix_end * 1000.0)) if mix_end is not None else None,
                "end_basis": f"cut_aware_{end_basis}",
                "cut_aware_segment_index": int(start_segment["index"]),
                "tokens": [],
            }
        )

    projected.sort(
        key=lambda row: (
            int(row.get("mix_start_ms", -1)),
            int(row.get("canonical_line_index", -1)),
            int(row.get("fragment_index", 0)),
        )
    )
    return projected, issues, omitted


def project_binding_cut_timeline(
    binding: ResolvedAssetBinding,
    mapping: dict[str, Any],
) -> dict[str, Any]:
    lines = parse_canonical_lyrics(
        Path(binding.canonical_lyric_path),
        original_index_by_timestamp=binding.original_index_by_timestamp,
    )
    projected, issues, omitted = project_cut_aware_lines(lines, mapping)
    segments = _segments(mapping)
    window = {
        "start_ms": int(round(float(segments[0]["mix_start"]) * 1000.0)),
        "end_ms": int(round(float(segments[-1]["mix_end"]) * 1000.0)),
    }
    return {
        "occurrence_id": binding.occurrence_id,
        "ordinal": binding.ordinal,
        "track_id": binding.track_id,
        "artist": binding.artist,
        "title": binding.title,
        "language_profile": binding.language_profile,
        "canonical_selection_sha256": binding.canonical_selection_sha256,
        "window": window,
        "line_count": len(projected),
        "lines": projected,
        "cut_aware": True,
        "cuts": deepcopy(_cuts(mapping)),
        "omitted_lines": omitted,
        "projection_issues": issues,
    }
