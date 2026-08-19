"""Fail-closed partial SRT timing preview from projected forced evidence.

Canonical lyrics remain text/order truth only.  Source-line timing comes from
P7 external forced-alignment evidence after P8 projects it through the exact
Source-to-Mix AFFINE / PIECEWISE_RATE / CUT_AWARE mapping.  This module keeps
all unselected editor cues immutable and never grants automatic timing or
release authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from lyric_aligner.contracts.artifacts import (
    ARTIFACT_SCHEMA_VERSION,
    canonical_json_sha256,
    sha256_file,
    validate_artifact_output,
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
_FORCED_MIX_MODE = "forced_alignment_mix_projection"
_FORCED_MIX_STAGE = "forced_alignment_mix_projection"
_FORCED_MIX_ROLE = "forced_alignment_mix_evidence"
_TIMING_RE = re.compile(
    r"^(?P<leading>\s*)"
    r"(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2})(?P<ssep>[,.])(?P<sms>\d{3})"
    r"(?P<arrow>\s*-->\s*)"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2})(?P<esep>[,.])(?P<ems>\d{3})"
    r"(?P<suffix>\s+.*)?$"
)


class PartialTimelineRepairError(ValueError):
    """Raised when a partial-timeline preview cannot be constructed safely."""


def _read_utf8(path: Path) -> tuple[str, bool]:
    payload = path.read_bytes()
    had_bom = payload.startswith(_UTF8_BOM)
    try:
        return payload.decode("utf-8-sig"), had_bom
    except UnicodeDecodeError as exc:
        raise PartialTimelineRepairError(f"{path} is not valid UTF-8") from exc


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise PartialTimelineRepairError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PartialTimelineRepairError(f"{label} must be a JSON object")
    return payload


def _sha_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


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
            raise PartialTimelineRepairError(f"duplicate numeric SRT cue number: {number}")
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


def validate_forced_mix_artifact(
    evidence_path: Path,
    evidence: dict[str, Any],
    artifact: dict[str, Any],
) -> str:
    """Validate the P8 artifact self-signature and exact evidence materialization."""

    if artifact.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise PartialTimelineRepairError("forced mix artifact schema_version mismatch")
    if artifact.get("stage") != _FORCED_MIX_STAGE:
        raise PartialTimelineRepairError("forced mix artifact stage mismatch")
    artifact_id = str(artifact.get("artifact_id") or "").strip()
    unsigned = {key: value for key, value in artifact.items() if key != "artifact_id"}
    if not artifact_id or artifact_id != canonical_json_sha256(unsigned):
        raise PartialTimelineRepairError("forced mix artifact_id is invalid")
    output_issues = validate_artifact_output(
        artifact,
        role=_FORCED_MIX_ROLE,
        path=evidence_path,
    )
    if output_issues:
        raise PartialTimelineRepairError("; ".join(output_issues))
    if artifact.get("task_fingerprint_sha256") != evidence.get("task_fingerprint_sha256"):
        raise PartialTimelineRepairError("forced mix artifact task fingerprint mismatch")
    if artifact.get("algorithm_version") != evidence.get("algorithm_version"):
        raise PartialTimelineRepairError("forced mix artifact algorithm version mismatch")
    config = artifact.get("normalized_config")
    if not isinstance(config, dict):
        raise PartialTimelineRepairError("forced mix artifact normalized_config is invalid")
    for key in (
        "source_run_artifact_id",
        "source_forced_alignment_artifact_id",
    ):
        if str(config.get(key) or "") != str(evidence.get(key) or ""):
            raise PartialTimelineRepairError(
                f"forced mix artifact {key} mismatch"
            )
    return artifact_id


def index_forced_mix_evidence(
    evidence: dict[str, Any],
    canonical_lines: Sequence[TimedCanonicalLine],
    *,
    expected_occurrence_id: str,
) -> tuple[dict[int, dict[str, Any]], dict[str, str]]:
    """Index one occurrence of already-projected P8 acoustic evidence."""

    expected = str(expected_occurrence_id or "").strip()
    if not expected:
        raise PartialTimelineRepairError("expected occurrence ID must not be empty")
    if str(evidence.get("schema_version") or "") != "1.0":
        raise PartialTimelineRepairError("forced mix evidence schema_version mismatch")
    if evidence.get("mode") != _FORCED_MIX_MODE:
        raise PartialTimelineRepairError("input is not forced mix projection evidence")
    if evidence.get("source_evidence_backend") != "external_forced_aligner":
        raise PartialTimelineRepairError(
            "forced mix evidence backend must be external_forced_aligner"
        )
    if evidence.get("canonical_text_authority") != "canonical_lyrics_only":
        raise PartialTimelineRepairError("forced mix canonical text authority mismatch")
    if evidence.get("primary_timing_authority") != "source_to_mix_only":
        raise PartialTimelineRepairError("forced mix primary timing authority mismatch")
    if evidence.get("forced_alignment_authority") != "auxiliary_acoustic_evidence_only":
        raise PartialTimelineRepairError("forced alignment authority mismatch")

    algorithm_version = str(evidence.get("algorithm_version") or "").strip()
    task_fingerprint = str(evidence.get("task_fingerprint_sha256") or "").strip()
    source_run_artifact_id = str(evidence.get("source_run_artifact_id") or "").strip()
    source_forced_artifact_id = str(
        evidence.get("source_forced_alignment_artifact_id") or ""
    ).strip()
    if not all(
        (algorithm_version, task_fingerprint, source_run_artifact_id, source_forced_artifact_id)
    ):
        raise PartialTimelineRepairError(
            "forced mix evidence is missing production lineage identity"
        )

    jobs = evidence.get("jobs")
    if not isinstance(jobs, list):
        raise PartialTimelineRepairError("forced mix evidence jobs must be a list")
    result: dict[int, dict[str, Any]] = {}
    matching_occurrence = False
    for raw in jobs:
        if not isinstance(raw, dict):
            raise PartialTimelineRepairError("forced mix evidence job must be an object")
        if str(raw.get("occurrence_id") or "").strip() != expected:
            continue
        matching_occurrence = True
        try:
            canonical_index = int(raw["canonical_line_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PartialTimelineRepairError(
                "forced mix canonical_line_index is invalid"
            ) from exc
        if canonical_index < 0 or canonical_index >= len(canonical_lines):
            raise PartialTimelineRepairError(
                "forced mix canonical_line_index is outside canonical lyric"
            )
        if canonical_index in result:
            raise PartialTimelineRepairError(
                "forced mix evidence has duplicate canonical line identity"
            )
        expected_text_sha = _sha_text(canonical_lines[canonical_index].text)
        if str(raw.get("canonical_text_sha256") or "").lower() != expected_text_sha:
            raise PartialTimelineRepairError(
                "forced mix canonical text identity mismatch"
            )
        projection_status = str(raw.get("projection_status") or "")
        if projection_status not in {"projected", "unprojectable"}:
            raise PartialTimelineRepairError(
                "forced mix projection_status must be projected or unprojectable"
            )
        mix_start = raw.get("mix_start_ms")
        mix_end = raw.get("mix_end_ms")
        if projection_status == "projected":
            try:
                mix_start = int(round(float(mix_start)))
                mix_end = int(round(float(mix_end)))
            except (TypeError, ValueError) as exc:
                raise PartialTimelineRepairError(
                    "projected forced mix boundary is invalid"
                ) from exc
            if mix_start < 0 or mix_end <= mix_start:
                raise PartialTimelineRepairError(
                    "projected forced mix boundary is not monotonic"
                )
        elif mix_start is not None or mix_end is not None:
            raise PartialTimelineRepairError(
                "unprojectable forced mix line must not contain mix boundary"
            )
        result[canonical_index] = {
            **raw,
            "mix_start_ms": mix_start,
            "mix_end_ms": mix_end,
        }

    if not matching_occurrence:
        raise PartialTimelineRepairError(
            f"forced mix evidence has no jobs for occurrence {expected!r}"
        )
    return result, {
        "occurrence_id": expected,
        "algorithm_version": algorithm_version,
        "task_fingerprint_sha256": task_fingerprint,
        "source_run_artifact_id": source_run_artifact_id,
        "source_forced_alignment_artifact_id": source_forced_artifact_id,
    }


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
        raise PartialTimelineRepairError(f"selected SRT cue does not exist: {missing[0]}")
    return {number_map[number] for number in numbers}, number_map


def build_partial_timeline_preview(
    source_text: str,
    canonical_lines: Sequence[TimedCanonicalLine],
    forced_mix_evidence: dict[str, Any],
    *,
    expected_occurrence_id: str,
    repair_cue_numbers: Sequence[int],
    text_match_threshold: float = 0.86,
) -> tuple[str, dict[str, Any]]:
    """Build a non-releaseable preview from selected P8 projected line evidence."""

    if not 0.75 <= text_match_threshold <= 1.0:
        raise PartialTimelineRepairError(
            "text_match_threshold must be between 0.75 and 1.0"
        )
    if not canonical_lines:
        raise PartialTimelineRepairError("canonical lyric has no lines")

    forced_by_line, forced_identity = index_forced_mix_evidence(
        forced_mix_evidence,
        canonical_lines,
        expected_occurrence_id=expected_occurrence_id,
    )
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
            "text_match_score": None,
            "forced_source_start_ms": None,
            "forced_source_end_ms": None,
            "forced_line_confidence": None,
            "projection_status": None,
            "projection_reason": None,
            "cut_aware_segment_index": None,
            "suggested_start_ms": None,
            "suggested_end_ms": None,
            "delta_start_ms": None,
            "delta_end_ms": None,
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

        forced = forced_by_line.get(canonical_index)
        if forced is None:
            decision["reason"] = "forced_mix_evidence_missing_for_canonical_line"
            decisions_by_ordinal[cue_ordinal] = decision
            continue
        decision["forced_source_start_ms"] = forced.get("line_source_start_ms")
        decision["forced_source_end_ms"] = forced.get("line_source_end_ms")
        decision["forced_line_confidence"] = forced.get("line_confidence")
        decision["projection_status"] = forced.get("projection_status")
        decision["projection_reason"] = forced.get("projection_reason")
        decision["cut_aware_segment_index"] = forced.get("cut_aware_segment_index")
        if forced.get("projection_status") != "projected":
            decision["reason"] = "forced_mix_evidence_unprojectable"
            decisions_by_ordinal[cue_ordinal] = decision
            continue

        suggested_start = int(forced["mix_start_ms"])
        suggested_end = int(forced["mix_end_ms"])
        decision["suggested_start_ms"] = suggested_start
        decision["suggested_end_ms"] = suggested_end
        decision["delta_start_ms"] = suggested_start - original_start
        decision["delta_end_ms"] = suggested_end - original_end
        if suggested_start == original_start and suggested_end == original_end:
            decision["action"] = "unchanged"
            decision["reason"] = "forced_mix_evidence_matches_existing_timing"
        else:
            decision["action"] = "propose"
            decision["reason"] = "projected_forced_evidence_one_to_one_timing_preview"
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
        left_interval = initial_proposals.get(left_ordinal, original_intervals[left_ordinal])
        right_interval = initial_proposals.get(right_ordinal, original_intervals[right_ordinal])
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
            raise AssertionError("partial timing preview changed SRT numbering or text")
        if before.ordinal not in timing_replacements and before.timing != after.timing:
            raise AssertionError("partial timing preview changed an unselected cue")

    decisions = [decisions_by_ordinal[ordinal] for ordinal in sorted(selected)]
    review_count = sum(row["action"] == "review" for row in decisions)
    proposed_count = sum(row["action"] == "propose" for row in decisions)
    unchanged_count = sum(row["action"] == "unchanged" for row in decisions)
    report = {
        "schema_version": "1.0",
        "mode": "partial_timeline_repair_preview",
        "status": "review_required" if review_count else "preview_ready",
        "releaseable": False,
        "automatic_timing_change_allowed": False,
        "timing_authority": "source_to_mix_only",
        "timing_evidence": "projected_external_forced_alignment_auxiliary",
        "text_authority": "canonical_lyrics_only",
        "canonical_timestamp_authority": "none",
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
        "forced_mix_identity": forced_identity,
        "decisions": decisions,
        "safety": (
            "preview only: LRC timestamps never define timing; selected cues require "
            "unique one-cue/one-line text identity plus P8 projected forced evidence; "
            "CUT_AWARE unprojectable lines stay review; unselected timing is immutable; "
            "automatic release remains disabled"
        ),
    }
    return preview_text, report


def write_partial_timeline_preview(
    source_srt: Path,
    canonical_lrc: Path,
    forced_mix_evidence_path: Path,
    forced_mix_artifact_path: Path,
    *,
    expected_occurrence_id: str,
    repair_cue_numbers: Sequence[int],
    report_path: Path,
    preview_out: Path | None = None,
    text_match_threshold: float = 0.86,
) -> dict[str, Any]:
    source_text, had_bom = _read_utf8(source_srt)
    canonical_lines = parse_canonical_lyrics(canonical_lrc)
    forced_mix_evidence = _load_json(
        forced_mix_evidence_path, label="forced mix evidence"
    )
    forced_mix_artifact = _load_json(
        forced_mix_artifact_path, label="forced mix artifact"
    )
    artifact_id = validate_forced_mix_artifact(
        forced_mix_evidence_path,
        forced_mix_evidence,
        forced_mix_artifact,
    )
    preview_text, report = build_partial_timeline_preview(
        source_text,
        canonical_lines,
        forced_mix_evidence,
        expected_occurrence_id=expected_occurrence_id,
        repair_cue_numbers=repair_cue_numbers,
        text_match_threshold=text_match_threshold,
    )
    report["inputs"] = {
        "source_srt_sha256": sha256_file(source_srt),
        "canonical_lrc_sha256": sha256_file(canonical_lrc),
        "forced_mix_evidence_sha256": sha256_file(forced_mix_evidence_path),
        "forced_mix_artifact_sha256": sha256_file(forced_mix_artifact_path),
        "forced_mix_artifact_id": artifact_id,
        "forced_mix_identity": report["forced_mix_identity"],
    }

    if preview_out is not None:
        preview_out.parent.mkdir(parents=True, exist_ok=True)
        payload = preview_text.encode("utf-8")
        if had_bom:
            payload = _UTF8_BOM + payload
        preview_out.write_bytes(payload)
        report["preview_srt_sha256"] = sha256_file(preview_out)
    else:
        report["preview_srt_sha256"] = None

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report
