"""Independent semantic timing checks for v4 subtitles.

Editor SRT is only one auxiliary witness. It is never canonical text truth and
must not be treated as the sole release timing authority. Release-grade Max QA
can additionally consume projected forced-alignment / ASR evidence in mix time.
Candidate text may be resegmented or corrected, so the editor matcher supports
one source cue spanning several candidate cues and remains track-local and
monotonic to avoid matching a chorus against a later repeat.
"""

from __future__ import annotations

import difflib
import statistics
import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from lyric_aligner.srt import Cue


DEFAULT_MIN_SCORE = 0.72
DEFAULT_MAX_SPAN = 6
DEFAULT_TIME_SEARCH_MS = 20_000
DEFAULT_MAX_MEDIAN_ABS_ERROR_MS = 1_500
DEFAULT_LARGE_ERROR_MS = 2_500
DEFAULT_MAX_LARGE_ERROR_FRACTION = 0.25
DEFAULT_MIN_MATCH_FRACTION = 0.35
DEFAULT_MIN_AUDIO_ANCHOR_FRACTION = 0.05
DEFAULT_MIN_AUDIO_ANCHORS_PER_TRACK = 3
DEFAULT_AUDIO_FAMILY_CONFLICT_MS = 2_000
DEFAULT_MIN_FORCED_CONFIDENCE = 0.50
DEFAULT_MIN_ASR_SUPPORT_SCORE = 0.72
DEFAULT_EDITOR_RELIABLE_MATCH_FRACTION = 0.45
EDITOR_MATCH_POLICY_ID = "editor-semantic-disjoint-onsets-1.0"


@dataclass(frozen=True)
class TrackWindow:
    ordinal: int
    label: str
    start_ms: int
    end_ms: int


def _is_layout(char: str) -> bool:
    category = unicodedata.category(char)
    return category[0] in {"P", "Z", "C"}


def normalize_for_semantic_match(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if not _is_layout(char))


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    if left in right or right in left:
        coverage = min(len(left), len(right)) / max(len(left), len(right))
        ratio = max(ratio, 0.78 + 0.22 * coverage)
    return min(1.0, float(ratio))


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        raise ValueError("percentile requires values")
    ordered = sorted(float(value) for value in values)
    position = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction))))
    return ordered[position]


def parse_song_windows(song_list: Path, *, content_end_ms: int) -> list[TrackWindow]:
    rows: list[tuple[int, str]] = []
    for raw in song_list.read_text(encoding="utf-8-sig").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"invalid song-list row: {raw!r}")
        clock, label = parts
        time_parts = clock.split(":")
        if len(time_parts) == 2:
            minute, second = time_parts
            start_ms = (int(minute) * 60 + int(second)) * 1000
        elif len(time_parts) == 3:
            hour, minute, second = time_parts
            start_ms = (int(hour) * 3600 + int(minute) * 60 + int(second)) * 1000
        else:
            raise ValueError(f"invalid song-list clock: {clock!r}")
        rows.append((start_ms, label.strip()))
    if not rows:
        raise ValueError("song list is empty")
    if any(right[0] <= left[0] for left, right in zip(rows, rows[1:])):
        raise ValueError("song-list starts must be strictly increasing")
    if content_end_ms <= rows[-1][0]:
        raise ValueError("content end must follow the last song start")
    return [
        TrackWindow(
            ordinal=index + 1,
            label=label,
            start_ms=start_ms,
            end_ms=rows[index + 1][0] if index + 1 < len(rows) else content_end_ms,
        )
        for index, (start_ms, label) in enumerate(rows)
    ]


def _track_cues(cues: Iterable[Cue], window: TrackWindow) -> list[Cue]:
    return [cue for cue in cues if window.start_ms <= cue.start_ms < window.end_ms]


def _eligible_source(cues: Sequence[Cue]) -> list[Cue]:
    return [cue for cue in cues if len(normalize_for_semantic_match(cue.text)) >= 3]


def match_track_semantics(
    source: Sequence[Cue],
    candidate: Sequence[Cue],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    max_span: int = DEFAULT_MAX_SPAN,
    time_search_ms: int = DEFAULT_TIME_SEARCH_MS,
) -> list[dict]:
    """Return monotonic source->candidate semantic onset matches.

    Candidate cues may be split more finely than the editor cue, so up to
    ``max_span`` adjacent candidate texts are concatenated.  Search is bounded
    in time to prevent a repeated chorus from satisfying an earlier cue.
    Each candidate span supplies only one onset: a merged candidate does not
    reveal the onset of its second editor fragment without word timing.
    """

    eligible = _eligible_source(source)
    matches: list[dict] = []
    cursor = 0
    for source_cue in eligible:
        source_text = normalize_for_semantic_match(source_cue.text)
        best: tuple[float, int, int, Cue, str] | None = None
        for index in range(cursor, len(candidate)):
            first = candidate[index]
            if first.start_ms < source_cue.start_ms - time_search_ms:
                continue
            if first.start_ms > source_cue.start_ms + time_search_ms:
                break
            combined = ""
            for span in range(1, max_span + 1):
                stop = index + span
                if stop > len(candidate):
                    break
                combined += normalize_for_semantic_match(candidate[stop - 1].text)
                if not combined:
                    continue
                score = _similarity(source_text, combined)
                # Prefer an equally good earlier/shorter span.  The tiny penalty
                # cannot turn a weak lexical match into a passing one.
                ranked = score - 0.0005 * (index - cursor) - 0.0002 * (span - 1)
                if best is None or ranked > best[0]:
                    best = (ranked, index, span, first, combined)
        if best is None:
            continue
        ranked, index, span, first, combined = best
        score = _similarity(source_text, combined)
        if score < min_score:
            continue
        matches.append(
            {
                "source_number": source_cue.number,
                "source_start_ms": source_cue.start_ms,
                "source_text": source_cue.text,
                "candidate_number": first.number,
                "candidate_start_ms": first.start_ms,
                "candidate_span": span,
                "candidate_text": " / ".join(
                    cue.text for cue in candidate[index : index + span]
                ),
                "score": round(score, 6),
                "delta_ms": int(first.start_ms - source_cue.start_ms),
            }
        )
        # Consume the complete span. Reusing its onset for a later source cue
        # invents a second timing witness and collapses consecutive repeats.
        cursor = index + span
    return matches


def audit_semantic_sync(
    source_cues: Sequence[Cue],
    candidate_cues: Sequence[Cue],
    windows: Sequence[TrackWindow],
    *,
    max_median_abs_error_ms: int = DEFAULT_MAX_MEDIAN_ABS_ERROR_MS,
    large_error_ms: int = DEFAULT_LARGE_ERROR_MS,
    max_large_error_fraction: float = DEFAULT_MAX_LARGE_ERROR_FRACTION,
    min_match_fraction: float = DEFAULT_MIN_MATCH_FRACTION,
) -> dict:
    tracks: list[dict] = []
    errors: list[dict] = []
    for window in windows:
        source = _track_cues(source_cues, window)
        candidate = _track_cues(candidate_cues, window)
        eligible = _eligible_source(source)
        matches = match_track_semantics(source, candidate)
        match_fraction = len(matches) / len(eligible) if eligible else 0.0
        minimum_matches = min(5, max(2, (len(eligible) + 4) // 5)) if eligible else 0
        deltas = [int(row["delta_ms"]) for row in matches]
        abs_deltas = [abs(value) for value in deltas]
        median_abs = float(statistics.median(abs_deltas)) if abs_deltas else None
        median_signed = float(statistics.median(deltas)) if deltas else None
        p90_abs = _percentile(abs_deltas, 0.90) if abs_deltas else None
        large_count = sum(value > large_error_ms for value in abs_deltas)
        large_fraction = large_count / len(abs_deltas) if abs_deltas else 1.0
        track_errors: list[str] = []
        if len(matches) < minimum_matches or match_fraction < min_match_fraction:
            track_errors.append("insufficient_semantic_anchor_coverage")
        if median_abs is None or median_abs > max_median_abs_error_ms:
            track_errors.append("median_semantic_timing_error_exceeded")
        if matches and large_fraction > max_large_error_fraction:
            track_errors.append("large_semantic_timing_error_fraction_exceeded")
        track_result = {
            "ordinal": window.ordinal,
            "track": window.label,
            "window_start_ms": window.start_ms,
            "window_end_ms": window.end_ms,
            "eligible_source_cue_count": len(eligible),
            "candidate_cue_count": len(candidate),
            "match_count": len(matches),
            "match_fraction": round(match_fraction, 6),
            "median_signed_delta_ms": median_signed,
            "median_abs_delta_ms": median_abs,
            "p90_abs_delta_ms": p90_abs,
            "large_error_threshold_ms": large_error_ms,
            "large_error_count": large_count,
            "large_error_fraction": round(large_fraction, 6),
            "passed": not track_errors,
            "errors": track_errors,
            "matches": matches,
        }
        tracks.append(track_result)
        if track_errors:
            errors.append(
                {
                    "ordinal": window.ordinal,
                    "track": window.label,
                    "errors": track_errors,
                    "median_abs_delta_ms": median_abs,
                    "large_error_fraction": round(large_fraction, 6),
                    "match_fraction": round(match_fraction, 6),
                }
            )
    return {
        "matcher_policy_id": EDITOR_MATCH_POLICY_ID,
        "passed": not errors,
        "track_count": len(tracks),
        "failed_track_count": len(errors),
        "thresholds": {
            "max_median_abs_error_ms": max_median_abs_error_ms,
            "large_error_ms": large_error_ms,
            "max_large_error_fraction": max_large_error_fraction,
            "min_match_fraction": min_match_fraction,
        },
        "errors": errors,
        "tracks": tracks,
    }


def editor_reliability_by_ordinal(
    audit: dict,
    *,
    min_match_fraction: float = DEFAULT_EDITOR_RELIABLE_MATCH_FRACTION,
) -> dict[int, bool]:
    """Return lexical editor-witness reliability without granting timing authority."""

    rows = audit.get("tracks")
    if not isinstance(rows, list):
        raise ValueError("editor semantic audit has no track rows")
    result: dict[int, bool] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("editor semantic audit track row is invalid")
        ordinal = int(row["ordinal"])
        eligible = int(row.get("eligible_source_cue_count") or 0)
        matches = int(row.get("match_count") or 0)
        fraction = float(row.get("match_fraction") or 0.0)
        minimum_matches = min(5, max(2, (eligible + 4) // 5)) if eligible else 0
        result[ordinal] = bool(
            eligible > 0
            and matches >= minimum_matches
            and fraction >= min_match_fraction
        )
    return result


def _boundary_start(family: dict) -> int | None:
    boundary = family.get("boundary_ms")
    if not isinstance(boundary, list) or len(boundary) != 2:
        return None
    try:
        start = int(boundary[0])
        end = int(boundary[1])
    except (TypeError, ValueError):
        return None
    if start < 0 or end <= start:
        return None
    return start


def _line_audio_anchor(
    line: dict,
    *,
    min_forced_confidence: float,
    min_asr_support_score: float,
    conflict_ms: int,
) -> dict:
    families = line.get("families")
    if not isinstance(families, list):
        raise ValueError("fusion line has no evidence families")
    forced_start: int | None = None
    asr_start: int | None = None
    for family in families:
        if not isinstance(family, dict):
            continue
        name = str(family.get("family") or "")
        onset = family.get('canonical_onset') if name == 'asr' else None
        if isinstance(onset, dict):
            start = onset.get('start_ms')
            score = onset.get('support_score')
            if (type(start) is int and start >= 0 and type(score) in (int, float)
                    and math.isfinite(score) and score >= min_asr_support_score
                    and onset.get('basis') == 'canonical_prefix_word_span'
                    and onset.get('canonical_start_covered') is True
                    and onset.get('canonical_match_ambiguous') is False):
                asr_start = start
            continue
        if name == 'asr' and 'canonical_onset' in family:
            continue
        if family.get("available") is not True:
            continue
        start = _boundary_start(family)
        if start is None:
            continue
        if name == "forced_alignment":
            confidence = family.get("line_confidence")
            if confidence is not None and float(confidence) < min_forced_confidence:
                continue
            forced_start = start
        elif name == "asr":
            if family.get('canonical_start_covered') is not True:
                continue
            if family.get("boundary_basis") != "canonical_word_span":
                continue
            support = family.get("canonical_match_support_score")
            if support is None or float(support) < min_asr_support_score:
                continue
            asr_start = start
    if forced_start is not None and asr_start is not None:
        disagreement = abs(forced_start - asr_start)
        if disagreement > conflict_ms:
            return {
                "available": False,
                "conflict": True,
                "forced_start_ms": forced_start,
                "asr_start_ms": asr_start,
                "disagreement_ms": disagreement,
            }
        # Forced alignment uses the known canonical text and is the stronger
        # semantic family; ASR agreement is retained as corroboration.
        return {
            "available": True,
            "conflict": False,
            "basis": "forced_alignment+asr",
            "start_ms": forced_start,
            "forced": True,
            "asr": True,
            "disagreement_ms": disagreement,
        }
    if forced_start is not None:
        return {
            "available": True,
            "conflict": False,
            "basis": "forced_alignment",
            "start_ms": forced_start,
            "forced": True,
            "asr": False,
            "disagreement_ms": None,
        }
    if asr_start is not None:
        return {
            "available": True,
            "conflict": False,
            "basis": "asr",
            "start_ms": asr_start,
            "forced": False,
            "asr": True,
            "disagreement_ms": None,
        }
    return {
        "available": False,
        "conflict": False,
        "basis": "none",
        "forced": False,
        "asr": False,
        "disagreement_ms": None,
    }


def _final_onset_index(rows: Sequence[dict]) -> dict[tuple[str, int], int]:
    index: dict[tuple[str, int], int] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("final audit row is invalid")
        occurrence_id = str(row.get("occurrence_id") or "").strip()
        raw_line_index = row.get("canonical_line_index")
        raw_start = row.get("start_ms")
        if not occurrence_id or raw_line_index in (None, "") or raw_start in (None, ""):
            continue
        key = (occurrence_id, int(raw_line_index))
        start = int(raw_start)
        previous = index.get(key)
        if previous is None or start < previous:
            index[key] = start
    return index


def _timing_layer_summary(
    track_rows: Sequence[dict],
    *,
    prefix: str,
    max_median_abs_error_ms: int,
    large_error_ms: int,
    max_large_error_fraction: float,
    min_audio_anchor_fraction: float,
) -> dict:
    tracks: list[dict] = []
    errors: list[dict] = []
    for row in track_rows:
        track_errors = list(row["shared_errors"]) + list(row.get(f"{prefix}_errors", []))
        deltas = list(row[f"{prefix}_deltas_ms"])
        abs_deltas = [abs(value) for value in deltas]
        median_abs = float(statistics.median(abs_deltas)) if abs_deltas else None
        median_signed = float(statistics.median(deltas)) if deltas else None
        p90_abs = _percentile(abs_deltas, 0.90) if abs_deltas else None
        large_count = sum(value > large_error_ms for value in abs_deltas)
        large_fraction = large_count / len(abs_deltas) if abs_deltas else 1.0
        if len(deltas) < row["minimum_audio_anchor_count"]:
            track_errors.append(f"insufficient_{prefix}_audio_anchor_coverage")
        if median_abs is None or median_abs > max_median_abs_error_ms:
            track_errors.append(f"median_{prefix}_audio_timing_error_exceeded")
        if deltas and large_fraction > max_large_error_fraction:
            track_errors.append(f"large_{prefix}_audio_timing_error_fraction_exceeded")
        result = {
            "ordinal": row["ordinal"],
            "track_id": row["track_id"],
            "canonical_line_count": row["canonical_line_count"],
            "audio_anchor_count": row["audio_anchor_count"],
            "audio_anchor_fraction": row["audio_anchor_fraction"],
            "forced_anchor_count": row["forced_anchor_count"],
            "asr_anchor_count": row["asr_anchor_count"],
            "evidence_basis": row["evidence_basis"],
            "editor_witness_reliable": row["editor_witness_reliable"],
            "verified_source_clock_authority": bool(row.get("verified_source_clock_authority")),
            "authority_holdout_anchor_count": int(row.get("authority_holdout_anchor_count") or 0),
            "authority_policy_id": row.get("authority_policy_id"),
            "authority_timeline_map_binding": row.get("authority_timeline_map_binding"),
            "authority_current_audio_match_count": int(row.get("authority_current_audio_match_count") or 0),
            "authority_final_projection_match_count": int(row.get("authority_final_projection_match_count") or 0),
            "match_count": len(deltas),
            "median_signed_delta_ms": median_signed,
            "median_abs_delta_ms": median_abs,
            "p90_abs_delta_ms": p90_abs,
            "large_error_threshold_ms": large_error_ms,
            "large_error_count": large_count,
            "large_error_fraction": round(large_fraction, 6),
            "passed": not track_errors,
            "errors": track_errors,
        }
        tracks.append(result)
        if track_errors:
            errors.append(
                {
                    "ordinal": row["ordinal"],
                    "track_id": row["track_id"],
                    "errors": track_errors,
                    "median_abs_delta_ms": median_abs,
                    "large_error_fraction": round(large_fraction, 6),
                    "audio_anchor_fraction": row["audio_anchor_fraction"],
                }
            )
    return {
        "passed": not errors,
        "track_count": len(tracks),
        "failed_track_count": len(errors),
        "thresholds": {
            "max_median_abs_error_ms": max_median_abs_error_ms,
            "large_error_ms": large_error_ms,
            "max_large_error_fraction": max_large_error_fraction,
            "min_audio_anchor_fraction": min_audio_anchor_fraction,
        },
        "errors": errors,
        "tracks": tracks,
    }


def audit_independent_audio_sync(
    fusion: dict,
    final_rows: Sequence[dict],
    *,
    editor_projection_audit: dict,
    editor_final_audit: dict,
    max_median_abs_error_ms: int = DEFAULT_MAX_MEDIAN_ABS_ERROR_MS,
    large_error_ms: int = DEFAULT_LARGE_ERROR_MS,
    max_large_error_fraction: float = DEFAULT_MAX_LARGE_ERROR_FRACTION,
    min_audio_anchor_fraction: float = DEFAULT_MIN_AUDIO_ANCHOR_FRACTION,
    min_audio_anchors_per_track: int = DEFAULT_MIN_AUDIO_ANCHORS_PER_TRACK,
    audio_family_conflict_ms: int = DEFAULT_AUDIO_FAMILY_CONFLICT_MS,
    min_forced_confidence: float = DEFAULT_MIN_FORCED_CONFIDENCE,
    min_asr_support_score: float = DEFAULT_MIN_ASR_SUPPORT_SCORE,
    verified_source_clock_authority_by_ordinal: Mapping[int, Mapping[str, object]] | None = None,
) -> dict:
    """Audit projection/final timing against independent audio-semantic evidence.

    Forced alignment projected into mix time is sufficient by itself when it has
    adequate per-track coverage. ASR-only evidence is weaker and may authorize a
    track only when the editor witness is lexically reliable and both editor
    projection/final timing audits also pass. This prevents poor Korean/Japanese
    editor recognition from becoming an accidental timing authority.
    """

    if fusion.get("mode") != "shadow_only":
        raise ValueError("semantic sync requires shadow evidence fusion")
    lines = fusion.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("semantic sync fusion has no lines")
    editor_reliable = editor_reliability_by_ordinal(editor_final_audit)
    editor_projection_tracks = {
        int(row["ordinal"]): row
        for row in editor_projection_audit.get("tracks", [])
        if isinstance(row, dict)
    }
    editor_final_tracks = {
        int(row["ordinal"]): row
        for row in editor_final_audit.get("tracks", [])
        if isinstance(row, dict)
    }
    final_onsets = _final_onset_index(final_rows)
    authority_by_ordinal = verified_source_clock_authority_by_ordinal or {}
    grouped: dict[int, dict] = {}
    for line in lines:
        if not isinstance(line, dict):
            raise ValueError("semantic sync fusion line is invalid")
        ordinal = int(line["ordinal"])
        occurrence_id = str(line.get("occurrence_id") or "").strip()
        track_id = str(line.get("track_id") or "").strip()
        line_index = int(line["canonical_line_index"])
        if not occurrence_id or not track_id:
            raise ValueError("semantic sync fusion line lacks track identity")
        row = grouped.setdefault(
            ordinal,
            {
                "ordinal": ordinal,
                "track_id": track_id,
                "canonical_line_count": 0,
                "audio_anchor_count": 0,
                "forced_anchor_count": 0,
                "asr_anchor_count": 0,
                "audio_conflict_count": 0,
                "projection_deltas_ms": [],
                "final_deltas_ms": [],
                "source_final_deltas_ms": [],
                "authority_audio_deltas_ms": [],
                "shared_errors": [],
            },
        )
        if row["track_id"] != track_id:
            raise ValueError("one ordinal maps to multiple track IDs")
        row["canonical_line_count"] += 1
        authority = authority_by_ordinal.get(ordinal)
        source_boundary = line.get("source_timeline_boundary_ms")
        final_start = final_onsets.get((occurrence_id, line_index))
        if authority is not None:
            if str(authority.get("track_id") or "") != track_id or str(authority.get("occurrence_id") or "") != occurrence_id:
                raise ValueError(f"verified source-clock authority identity mismatch for ordinal {ordinal}")
            if isinstance(source_boundary, list) and len(source_boundary) == 2 and final_start is not None:
                row["source_final_deltas_ms"].append(final_start - int(source_boundary[0]))
        anchor = _line_audio_anchor(
            line,
            min_forced_confidence=min_forced_confidence,
            min_asr_support_score=min_asr_support_score,
            conflict_ms=audio_family_conflict_ms,
        )
        if anchor["conflict"]:
            row["audio_conflict_count"] += 1
            continue
        if not anchor["available"]:
            continue
        row["audio_anchor_count"] += 1
        row["forced_anchor_count"] += int(bool(anchor["forced"]))
        row["asr_anchor_count"] += int(bool(anchor["asr"]))
        audio_start = int(anchor["start_ms"])
        source_boundary = line.get("source_timeline_boundary_ms")
        if isinstance(source_boundary, list) and len(source_boundary) == 2:
            row["projection_deltas_ms"].append(int(source_boundary[0]) - audio_start)
            if authority is not None:
                row["authority_audio_deltas_ms"].append(int(source_boundary[0]) - audio_start)
        final_start = final_onsets.get((occurrence_id, line_index))
        if final_start is not None:
            row["final_deltas_ms"].append(final_start - audio_start)

    track_rows: list[dict] = []
    for ordinal in sorted(grouped):
        row = grouped[ordinal]
        count = int(row["canonical_line_count"])
        minimum = min(count, max(2, min_audio_anchors_per_track)) if count else 0
        fraction = row["audio_anchor_count"] / count if count else 0.0
        forced_fraction = row["forced_anchor_count"] / count if count else 0.0
        asr_fraction = row["asr_anchor_count"] / count if count else 0.0
        reliable = bool(editor_reliable.get(ordinal, False))
        shared_errors: list[str] = []
        projection_errors: list[str] = []
        final_errors: list[str] = []
        authority = authority_by_ordinal.get(ordinal)
        if authority is not None:
            holdout_errors = authority.get("holdout_signed_errors_ms")
            if not isinstance(holdout_errors, list) or len(holdout_errors) < minimum:
                raise ValueError(f"verified source-clock authority lacks holdout anchors for ordinal {ordinal}")
            row["projection_deltas_ms"] = [int(value) for value in holdout_errors]
            row["final_deltas_ms"] = list(row["source_final_deltas_ms"])
            row["verified_source_clock_authority"] = True
            row["authority_holdout_anchor_count"] = int(authority.get("holdout_anchor_count") or 0)
            row["authority_policy_id"] = str(authority.get("policy_id") or "")
            row["authority_timeline_map_binding"] = str(
                authority.get("timeline_map_binding") or ""
            )
            rebuttal = [abs(int(value)) for value in row["authority_audio_deltas_ms"]]
            if len(rebuttal) >= minimum:
                rebuttal_median = float(statistics.median(rebuttal))
                rebuttal_large_fraction = sum(value > large_error_ms for value in rebuttal) / len(rebuttal)
                if rebuttal_median > max_median_abs_error_ms or rebuttal_large_fraction > max_large_error_fraction:
                    shared_errors.append("verified_source_clock_current_audio_rebuttal")
        else:
            row["verified_source_clock_authority"] = False
            row["authority_holdout_anchor_count"] = 0
            row["authority_policy_id"] = None
            row["authority_timeline_map_binding"] = None
        if row["audio_conflict_count"]:
            shared_errors.append("independent_audio_family_conflict")
        if (
            authority is not None
            or (
                row["forced_anchor_count"] >= minimum
                and forced_fraction >= min_audio_anchor_fraction
            )
        ):
            basis = "verified_source_clock_final_mix_holdout" if authority is not None else "forced_alignment"
        elif (
            reliable
            and row["asr_anchor_count"] >= minimum
            and asr_fraction >= min_audio_anchor_fraction
        ):
            basis = "asr_plus_reliable_editor"
            projection_editor = editor_projection_tracks.get(ordinal)
            final_editor = editor_final_tracks.get(ordinal)
            if not projection_editor or projection_editor.get("passed") is not True:
                projection_errors.append("asr_fallback_editor_projection_disagrees")
            if not final_editor or final_editor.get("passed") is not True:
                final_errors.append("asr_fallback_editor_final_disagrees")
        else:
            basis = "insufficient"
            shared_errors.append("insufficient_independent_audio_semantic_evidence")
        if authority is None and fraction < min_audio_anchor_fraction:
            shared_errors.append("insufficient_audio_anchor_fraction")
        row.update(
            {
                "minimum_audio_anchor_count": minimum,
                "audio_anchor_fraction": round(fraction, 6),
                "forced_anchor_fraction": round(forced_fraction, 6),
                "asr_anchor_fraction": round(asr_fraction, 6),
                "verified_source_clock_authority": bool(row.get("verified_source_clock_authority")),
                "authority_holdout_anchor_count": int(row.get("authority_holdout_anchor_count") or 0),
                "authority_policy_id": row.get("authority_policy_id"),
                "authority_timeline_map_binding": row.get("authority_timeline_map_binding"),
                "authority_current_audio_match_count": len(row.get("authority_audio_deltas_ms", [])),
                "authority_final_projection_match_count": len(row.get("source_final_deltas_ms", [])),
                "editor_witness_reliable": reliable,
                "evidence_basis": basis,
                "shared_errors": shared_errors,
                "projection_errors": projection_errors,
                "final_errors": final_errors,
            }
        )
        track_rows.append(row)

    projection = _timing_layer_summary(
        track_rows,
        prefix="projection",
        max_median_abs_error_ms=max_median_abs_error_ms,
        large_error_ms=large_error_ms,
        max_large_error_fraction=max_large_error_fraction,
        min_audio_anchor_fraction=min_audio_anchor_fraction,
    )
    final = _timing_layer_summary(
        track_rows,
        prefix="final",
        max_median_abs_error_ms=max_median_abs_error_ms,
        large_error_ms=large_error_ms,
        max_large_error_fraction=max_large_error_fraction,
        min_audio_anchor_fraction=min_audio_anchor_fraction,
    )
    return {
        "passed": bool(projection["passed"] and final["passed"]),
        "policy": (
            "verified_source_clock_or_forced_alignment_or_asr_plus_reliable_editor_v2"
            if authority_by_ordinal
            else "forced_alignment_or_asr_plus_reliable_editor_v1"
        ),
        "diagnostics_policy": "semantic-layer-errors-1.0",
        "projection_sync": projection,
        "final_sync": final,
        "tracks": track_rows,
    }
