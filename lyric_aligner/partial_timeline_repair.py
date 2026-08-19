"""Fail-closed partial SRT timing-repair preview using Source-to-Mix truth.

This module intentionally does not grant automatic timing authority.  It keeps
all unselected editor cues immutable, identifies selected cues against one
canonical lyric occurrence, and projects only safe one-cue/one-line source
intervals through the existing AFFINE / PIECEWISE_RATE / CUT_AWARE projection
semantics used by forced-alignment evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from lyric_aligner.alignment.forced_projection import (
    ForcedMixProjectionError,
    _project_interval,
)
from lyric_aligner.text.canonical_lyrics import (
    CanonicalLine as TimedCanonicalLine,
    parse_canonical_lyrics,
)
from lyric_aligner.text_repair import (
    CanonicalLine as RepairCanonicalLine,
    SubtitleCue,
    _normalize_for_match,
    align_spans,
    parse_srt_text,
)

_UTF8_BOM = b"\xef\xbb\xbf"
_TIMING_RE = re.compile(
    r"^(?P<leading>\s*)"
    r"(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2})(?P<ssep>[,.])(?P<sms>\d{3})"
    r"(?P<arrow>\s*-->\s*)"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2})(?P<esep>[,.])(?P<ems>\d{3})"
    r"(?P<suffix>\s+.*)?$"
)


class PartialTimelineRepairError(ValueError):
    """Raised when a partial-timeline preview cannot be constructed safely."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_utf8(path: Path) -> tuple[str, bool]:
    payload = path.read_bytes()
    had_bom = payload.startswith(_UTF8_BOM)
    try:
        return payload.decode("utf-8-sig"), had_bom
    except UnicodeDecodeError as exc:
        raise PartialTimelineRepairError(f"{path} is not valid UTF-8") from exc


def _timestamp_ms(hours: str, minutes: str, seconds: str, milliseconds: str) -> int:
    hour = int(hours)
    minute = int(minutes)
    second = int(seconds)
    milli = int(milliseconds)
    if minute >= 60 or second >= 60:
        raise PartialTimelineRepairError("invalid SRT timestamp component")
    return (((hour * 60) + minute) * 60 + second) * 1000 + milli


def parse_srt_timing(value: str) -> tuple[int, int]:
    match = _TIMING_RE.match(value)
    if match is None:
        raise PartialTimelineRepairError(f"invalid SRT timing line: {value!r}")
    start = _timestamp_ms(
        match.group("sh"),
        match.group("sm"),
        match.group("ss"),
        match.group("sms"),
    )
    end = _timestamp_ms(
        match.group("eh"),
        match.group("em"),
        match.group("es"),
        match.group("ems"),
    )
    if end <= start:
        raise PartialTimelineRepairError("SRT timing interval must be positive")
    return start, end


def _format_timestamp(milliseconds: int, separator: str) -> str:
    if milliseconds < 0:
        raise PartialTimelineRepairError("projected SRT timestamp is negative")
    hours, remainder = divmod(int(milliseconds), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def render_timing_like(original: str, start_ms: int, end_ms: int) -> str:
    match = _TIMING_RE.match(original)
    if match is None:
        raise PartialTimelineRepairError(f"invalid SRT timing line: {original!r}")
    if end_ms <= start_ms:
        raise PartialTimelineRepairError("projected SRT timing interval must be positive")
    return (
        match.group("leading")
        + _format_timestamp(start_ms, match.group("ssep"))
        + match.group("arrow")
        + _format_timestamp(end_ms, match.group("esep"))
        + (match.group("suffix") or "")
    )


def _canonical_source_bounds(
    lines: Sequence[TimedCanonicalLine],
    index: int,
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


def _cue_number_map(cues: Sequence[SubtitleCue]) -> dict[int, int]:
    result: dict[int, int] = {}
    for cue in cues:
        raw = cue.number.strip()
        try:
            number = int(raw)
        except ValueError as exc:
            raise PartialTimelineRepairError(
                f"partial timing repair requires numeric SRT cue numbers; got {raw!r}"
            ) from exc
        if number in result:
            raise PartialTimelineRepairError(
                f"duplicate numeric SRT cue number: {number}"
            )
        result[number] = cue.ordinal
    return result


def _adapt_canonical(lines: Sequence[TimedCanonicalLine]) -> list[RepairCanonicalLine]:
    return [
        RepairCanonicalLine(
            ordinal=index,
            source="canonical",
            text=line.text,
            normalized=_normalize_for_match(line.text),
            source_ordinal=0,
        )
        for index, line in enumerate(lines)
    ]


def _timing_text_match_allowed(
    source: str,
    target: str,
    score: float,
    threshold: float,
) -> bool:
    if not source or not target or score < threshold:
        return False
    length_ratio = min(len(source), len(target)) / max(len(source), len(target))
    if length_ratio < 0.75:
        return False
    if min(len(source), len(target)) <= 3 and score < max(0.95, threshold):
        return False
    return True


def extract_source_to_mix_mapping(
    payload: dict[str, Any],
    *,
    expected_occurrence_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract one unblocked V4 mapping and verify its occurrence identity."""

    if not isinstance(payload, dict):
        raise PartialTimelineRepairError("mapping payload must be a JSON object")
    expected = str(expected_occurrence_id or "").strip()
    if not expected:
        raise PartialTimelineRepairError("expected occurrence ID must not be empty")
    actual = str(payload.get("occurrence_id") or "").strip()
    if not actual:
        raise PartialTimelineRepairError(
            "mapping payload is not occurrence-bound; use a V4 coarse/fine/cut mapping payload"
        )
    if actual != expected:
        raise PartialTimelineRepairError(
            f"mapping occurrence mismatch: expected {expected!r}, got {actual!r}"
        )

    result = payload.get("result")
    mapping: dict[str, Any] | None = None
    mapping_source = ""
    blocked = False

    if isinstance(result, dict) and result.get("kind") == "CUT_AWARE":
        mapping = result
        mapping_source = "cut_aware_rebuild"
    elif isinstance(result, dict) and isinstance(result.get("timewarp"), dict):
        if "applied" in result and not bool(result.get("applied")):
            raise PartialTimelineRepairError(
                "fine Source-to-Mix payload is not applied"
            )
        timewarp = result["timewarp"]
        blocked = bool(timewarp.get("blocked", False))
        candidate = timewarp.get("mapping")
        if isinstance(candidate, dict):
            mapping = candidate
            mapping_source = "timewarp"
    elif isinstance(result, dict) and isinstance(result.get("mapping"), dict):
        mapping = result["mapping"]
        blocked = bool(result.get("blocked", False))
        mapping_source = "result_mapping"
    elif isinstance(payload.get("mapping"), dict):
        mapping = payload["mapping"]
        blocked = bool(payload.get("blocked", False))
        mapping_source = "top_level_mapping"

    if mapping is None:
        raise PartialTimelineRepairError(
            "mapping payload contains no supported Source-to-Mix mapping"
        )
    if blocked:
        raise PartialTimelineRepairError("Source-to-Mix mapping is blocked")

    kind = str(mapping.get("kind") or mapping.get("mode") or "").strip()
    if not kind:
        raise PartialTimelineRepairError("Source-to-Mix mapping kind is missing")
    if kind not in {"AFFINE", "PIECEWISE_RATE", "CUT_AWARE"}:
        raise PartialTimelineRepairError(
            f"unsupported Source-to-Mix mapping kind: {kind}"
        )

    identity = {
        "occurrence_id": actual,
        "track_id": str(payload.get("track_id") or ""),
        "canonical_selection_sha256": str(
            payload.get("canonical_selection_sha256") or ""
        ),
        "mapping_source": mapping_source,
        "mapping_kind": kind,
    }
    return mapping, identity


def _selected_ordinals(
    cues: Sequence[SubtitleCue],
    repair_cue_numbers: Sequence[int],
) -> tuple[set[int], dict[int, int]]:
    if not repair_cue_numbers:
        raise PartialTimelineRepairError("at least one --cue must be selected")
    numbers = [int(value) for value in repair_cue_numbers]
    if len(set(numbers)) != len(numbers):
        raise PartialTimelineRepairError("selected cue numbers must be unique")
    number_map = _cue_number_map(cues)
    missing = [number for number in numbers if number not in number_map]
    if missing:
        raise PartialTimelineRepairError(
            f"selected SRT cue does not exist: {missing[0]}"
        )
    return {number_map[number] for number in numbers}, number_map


def build_partial_timeline_preview(
    source_text: str,
    canonical_lines: Sequence[TimedCanonicalLine],
    mapping: dict[str, Any],
    *,
    repair_cue_numbers: Sequence[int],
    text_match_threshold: float = 0.86,
) -> tuple[str, dict[str, Any]]:
    """Build a non-releaseable preview changing timing only on explicit safe cues."""

    if not 0.75 <= text_match_threshold <= 1.0:
        raise PartialTimelineRepairError(
            "text_match_threshold must be between 0.75 and 1.0"
        )
    if not canonical_lines:
        raise PartialTimelineRepairError("canonical lyric has no timed lines")

    parts, cues = parse_srt_text(source_text)
    selected, number_map = _selected_ordinals(cues, repair_cue_numbers)
    number_by_ordinal = {ordinal: number for number, ordinal in number_map.items()}
    original_intervals = [parse_srt_timing(cue.timing) for cue in cues]

    repair_canonical = _adapt_canonical(canonical_lines)
    operations = align_spans(cues, repair_canonical)
    match_by_cue: dict[int, Any] = {}
    for operation in operations:
        if operation.kind != "match":
            continue
        for cue_ordinal in range(operation.cue_start, operation.cue_end):
            match_by_cue[cue_ordinal] = operation

    cue_counts = Counter(cue.normalized for cue in cues)
    canonical_counts = Counter(line.normalized for line in repair_canonical)
    decisions_by_ordinal: dict[int, dict[str, Any]] = {}

    for cue_ordinal in sorted(selected):
        cue = cues[cue_ordinal]
        original_start, original_end = original_intervals[cue_ordinal]
        decision: dict[str, Any] = {
            "cue_ordinal": cue_ordinal,
            "cue_number": number_by_ordinal[cue_ordinal],
            "action": "review",
            "reason": "unmatched_subtitle_cue",
            "text": cue.text,
            "original_start_ms": original_start,
            "original_end_ms": original_end,
            "canonical_line_index": None,
            "canonical_text": None,
            "canonical_source_start_ms": None,
            "canonical_source_end_ms": None,
            "canonical_end_basis": None,
            "suggested_start_ms": None,
            "suggested_end_ms": None,
            "delta_start_ms": None,
            "delta_end_ms": None,
            "text_match_score": None,
            "projection_status": None,
            "projection_reason": None,
            "cut_aware_segment_index": None,
        }
        operation = match_by_cue.get(cue_ordinal)
        if operation is None:
            decisions_by_ordinal[cue_ordinal] = decision
            continue
        decision["text_match_score"] = round(float(operation.score), 6)
        if (
            operation.cue_end - operation.cue_start != 1
            or operation.canonical_end - operation.canonical_start != 1
        ):
            decision["reason"] = "timing_repair_requires_one_cue_one_canonical_line"
            decisions_by_ordinal[cue_ordinal] = decision
            continue

        canonical_index = operation.canonical_start
        canonical = canonical_lines[canonical_index]
        repair_line = repair_canonical[canonical_index]
        decision["canonical_line_index"] = canonical_index
        decision["canonical_text"] = canonical.text

        if cue_counts[cue.normalized] != 1:
            decision["reason"] = "subtitle_text_occurrence_is_not_unique"
            decisions_by_ordinal[cue_ordinal] = decision
            continue
        if canonical_counts[repair_line.normalized] != 1:
            decision["reason"] = "canonical_text_occurrence_is_not_unique"
            decisions_by_ordinal[cue_ordinal] = decision
            continue
        if not _timing_text_match_allowed(
            cue.normalized,
            repair_line.normalized,
            float(operation.score),
            text_match_threshold,
        ):
            decision["reason"] = "text_identity_not_strong_enough_for_timing_preview"
            decisions_by_ordinal[cue_ordinal] = decision
            continue

        source_start, source_end, end_basis = _canonical_source_bounds(
            canonical_lines,
            canonical_index,
        )
        decision["canonical_source_start_ms"] = source_start
        decision["canonical_source_end_ms"] = source_end
        decision["canonical_end_basis"] = end_basis
        if source_end is None:
            decision["reason"] = "canonical_line_has_open_end"
            decisions_by_ordinal[cue_ordinal] = decision
            continue
        if source_end <= source_start:
            decision["reason"] = "canonical_source_interval_is_not_monotonic"
            decisions_by_ordinal[cue_ordinal] = decision
            continue

        try:
            projection = _project_interval(mapping, source_start, source_end)
        except ForcedMixProjectionError as exc:
            decision["reason"] = "source_to_mix_projection_failed"
            decision["projection_status"] = "error"
            decision["projection_reason"] = str(exc)
            decisions_by_ordinal[cue_ordinal] = decision
            continue

        decision["projection_status"] = projection.get("projection_status")
        decision["projection_reason"] = projection.get("projection_reason")
        decision["cut_aware_segment_index"] = projection.get(
            "cut_aware_segment_index"
        )
        if projection.get("projection_status") != "projected":
            decision["reason"] = "source_interval_is_unprojectable"
            decisions_by_ordinal[cue_ordinal] = decision
            continue

        suggested_start = int(projection["mix_start_ms"])
        suggested_end = int(projection["mix_end_ms"])
        if suggested_start < 0 or suggested_end <= suggested_start:
            decision["reason"] = "projected_mix_interval_is_invalid"
            decisions_by_ordinal[cue_ordinal] = decision
            continue

        decision["suggested_start_ms"] = suggested_start
        decision["suggested_end_ms"] = suggested_end
        decision["delta_start_ms"] = suggested_start - original_start
        decision["delta_end_ms"] = suggested_end - original_end
        if suggested_start == original_start and suggested_end == original_end:
            decision["action"] = "unchanged"
            decision["reason"] = "source_to_mix_matches_existing_timing"
        else:
            decision["action"] = "propose"
            decision["reason"] = "source_to_mix_one_to_one_timing_preview"
        decisions_by_ordinal[cue_ordinal] = decision

    initial_proposals = {
        ordinal: (
            int(decision["suggested_start_ms"]),
            int(decision["suggested_end_ms"]),
        )
        for ordinal, decision in decisions_by_ordinal.items()
        if decision["action"] == "propose"
    }
    conflicts: set[int] = set()
    for left_ordinal in range(len(cues) - 1):
        right_ordinal = left_ordinal + 1
        left_interval = initial_proposals.get(
            left_ordinal, original_intervals[left_ordinal]
        )
        right_interval = initial_proposals.get(
            right_ordinal, original_intervals[right_ordinal]
        )
        if left_interval[1] > right_interval[0]:
            if left_ordinal in initial_proposals:
                conflicts.add(left_ordinal)
            if right_ordinal in initial_proposals:
                conflicts.add(right_ordinal)

    for ordinal in conflicts:
        decision = decisions_by_ordinal[ordinal]
        decision["action"] = "review"
        decision["reason"] = "proposed_timing_overlaps_locked_or_selected_neighbor"

    timing_replacements: dict[int, str] = {}
    for ordinal, decision in decisions_by_ordinal.items():
        if decision["action"] != "propose":
            continue
        cue = cues[ordinal]
        timing_replacements[ordinal] = render_timing_like(
            cue.timing,
            int(decision["suggested_start_ms"]),
            int(decision["suggested_end_ms"]),
        )

    output_parts = list(parts)
    for cue in cues:
        replacement = timing_replacements.get(cue.ordinal)
        if replacement is None:
            continue
        original_block = parts[cue.raw_block_index]
        line_ending = "\r\n" if "\r\n" in original_block else "\n"
        trailing = line_ending if original_block.endswith(line_ending) else ""
        rows = original_block.splitlines()
        rows[1] = replacement
        output_parts[cue.raw_block_index] = line_ending.join(rows) + trailing
    preview_text = "".join(output_parts)

    _, preview_cues = parse_srt_text(preview_text)
    if len(preview_cues) != len(cues):
        raise AssertionError("partial timing preview changed SRT cue count")
    for before, after in zip(cues, preview_cues):
        if before.number != after.number or before.text != after.text:
            raise AssertionError(
                "partial timing preview changed SRT numbering or text"
            )
        if before.ordinal not in timing_replacements and before.timing != after.timing:
            raise AssertionError("partial timing preview changed an unselected cue")

    decisions = [decisions_by_ordinal[ordinal] for ordinal in sorted(selected)]
    review_count = sum(row["action"] == "review" for row in decisions)
    proposed_count = sum(row["action"] == "propose" for row in decisions)
    unchanged_count = sum(row["action"] == "unchanged" for row in decisions)
    status = "review_required" if review_count else "preview_ready"
    report = {
        "schema_version": "1.0",
        "mode": "partial_timeline_repair_preview",
        "status": status,
        "releaseable": False,
        "automatic_timing_change_allowed": False,
        "timing_authority": "source_to_mix_only",
        "text_authority": "canonical_lyrics_only",
        "subtitle_text_unchanged": True,
        "cue_count_unchanged": True,
        "unselected_cues_timing_unchanged": True,
        "cue_count": len(cues),
        "selected_cue_count": len(selected),
        "locked_cue_count": len(cues) - len(selected),
        "proposed_change_count": proposed_count,
        "selected_unchanged_count": unchanged_count,
        "review_count": review_count,
        "text_match_threshold": text_match_threshold,
        "mapping_kind": str(mapping.get("kind") or mapping.get("mode")),
        "decisions": decisions,
        "safety": (
            "preview only: selected cues require unique one-cue/one-line text identity; "
            "CUT_AWARE gaps/cuts are not bridged; unselected cue timing is immutable; "
            "automatic release remains disabled"
        ),
    }
    return preview_text, report


def write_partial_timeline_preview(
    source_srt: Path,
    canonical_lrc: Path,
    mapping: dict[str, Any],
    *,
    repair_cue_numbers: Sequence[int],
    report_path: Path,
    preview_out: Path | None = None,
    mapping_payload_path: Path | None = None,
    mapping_identity: dict[str, Any] | None = None,
    text_match_threshold: float = 0.86,
) -> dict[str, Any]:
    source_text, had_bom = _read_utf8(source_srt)
    canonical_lines = parse_canonical_lyrics(canonical_lrc)
    preview_text, report = build_partial_timeline_preview(
        source_text,
        canonical_lines,
        mapping,
        repair_cue_numbers=repair_cue_numbers,
        text_match_threshold=text_match_threshold,
    )
    report["inputs"] = {
        "source_srt_sha256": _sha256_file(source_srt),
        "canonical_lrc_sha256": _sha256_file(canonical_lrc),
        "mapping_payload_sha256": (
            _sha256_file(mapping_payload_path)
            if mapping_payload_path is not None
            else None
        ),
        "mapping_identity": dict(mapping_identity or {}),
    }

    if preview_out is not None:
        preview_out.parent.mkdir(parents=True, exist_ok=True)
        payload = preview_text.encode("utf-8")
        if had_bom:
            payload = _UTF8_BOM + payload
        preview_out.write_bytes(payload)
        report["preview_srt_sha256"] = _sha256_file(preview_out)
    else:
        report["preview_srt_sha256"] = None

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report
