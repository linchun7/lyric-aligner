"""Conservative experimental source anchors from exact lexical observations.

This module does not build source packets, change their qualification, or
produce subtitle intervals.  It only finds unusually strong source-side
observations: a complete canonical English line of at least five units that is
unique in both the complete canonical stream and the unfiltered observed-word
stream.  A result remains bounded by two already-qualified source anchors.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from lyric_aligner.text.alignment_lexical import AlignmentLexicalError, english_units


SOURCE_EXACT_ANCHORS_POLICY_ID = "source-exact-word-run-legacy-bracket-v1"
SOURCE_EXACT_ANCHORS_SCHEMA_VERSION = "source-exact-anchors-1.0"
SOURCE_EXACT_ANCHORS_MIN_UNITS = 5
SOURCE_EXACT_ANCHORS_MIN_PROBABILITY = 0.7
SOURCE_EXACT_ANCHORS_MAX_ADJACENT_GAP_MS = 1500
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_PUNCTUATION = set(" \t\r\n.,!?;:()[]{}\\\"/\\\\-–—_+&@#$%^*=~`|<>")


class SourceExactAnchorsError(ValueError):
    """The caller did not provide an auditable exact-anchor input."""


def _json_sha(value: Any) -> str:
    try:
        # The input digest has to bind malformed observations too, so that a
        # NaN probability is reported per-line rather than making the entire
        # canonical denominator disappear.  Python's spelling is deterministic
        # for these three IEEE values; they are never promoted past validation.
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=True)
    except (TypeError, ValueError) as exc:
        raise SourceExactAnchorsError("exact-anchor raw input is not canonical JSON") from exc
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _display_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def _interval(value: Any, *, label: str, require_nonnegative: bool = True) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    start, end = _number(value[0]), _number(value[1])
    if start is None or end is None or start >= end or (require_nonnegative and start < 0):
        return None
    return start, end


def _source_domain(value: Any) -> tuple[float, float]:
    if isinstance(value, Mapping):
        value = [value.get("start_ms"), value.get("end_ms")]
    result = _interval(value, label="source_domain")
    if result is None:
        raise SourceExactAnchorsError("source_domain_ms must be one finite positive interval")
    return result


def _verify_sha(value: Any, *, label: str) -> str:
    result = str(value or "").casefold()
    if _SHA256.fullmatch(result) is None:
        raise SourceExactAnchorsError(label + " must be a SHA-256 hex string")
    return result


def _english_complete(value: Any) -> list[str] | None:
    """Return one full tokenizer sequence, refusing silently dropped scripts."""
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).replace("’", "'")
    for char in normalized:
        if char.isascii() and (char.isalnum() or char == "'"):
            continue
        if char in _ALLOWED_PUNCTUATION:
            continue
        return None
    try:
        return english_units(normalized)
    except AlignmentLexicalError:
        return None


def _canonical_rows(canonical_lines: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(canonical_lines, (str, bytes)) or not isinstance(canonical_lines, Sequence):
        raise SourceExactAnchorsError("canonical_lines must be a sequence")
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in canonical_lines:
        if not isinstance(item, Mapping):
            raise SourceExactAnchorsError("canonical line must be an object")
        try:
            index = int(item["canonical_line_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceExactAnchorsError("canonical line index is invalid") from exc
        if index in seen:
            raise SourceExactAnchorsError("canonical line index is duplicated")
        text = item.get("text")
        if not isinstance(text, str):
            raise SourceExactAnchorsError("canonical line text is invalid")
        seen.add(index)
        rows.append({"canonical_line_index": index, "text": text, "lexical_units": _english_complete(text)})
    if not rows:
        raise SourceExactAnchorsError("canonical_lines must not be empty")
    if [row["canonical_line_index"] for row in rows] != sorted(seen):
        raise SourceExactAnchorsError("canonical_lines must be in increasing canonical order")
    return rows


def _observed_units(observed_words: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(observed_words, (str, bytes)) or not isinstance(observed_words, Sequence):
        raise SourceExactAnchorsError("observed_words must be a sequence")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(observed_words):
        if not isinstance(item, Mapping):
            result.append({"observed_word_index": index, "unit": None, "raw": None})
            continue
        units = _english_complete(item.get("text"))
        # An item that has zero or two lexical units is a stream barrier.  In
        # particular, never invent two timestamps by splitting an ASR item.
        result.append({"observed_word_index": index,
                       "unit": units[0] if units is not None and len(units) == 1 else None,
                       "raw": item})
    return result


def _occurrences(stream: Sequence[str | None], target: Sequence[str]) -> list[int]:
    if not target:
        return []
    length = len(target)
    return [offset for offset in range(len(stream) - length + 1)
            if list(stream[offset:offset + length]) == list(target)]


def _proof_ids(item: Mapping[str, Any]) -> list[str] | None:
    explicit = item.get("proof_ids")
    if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes)):
        values = [str(value) for value in explicit]
        if values and all(values) and len(values) == len(set(values)):
            return values
        return None
    packet, candidate, qualification = (item.get("packet_cache_key_sha256"), item.get("candidate_id"),
                                         item.get("qualification"))
    if not all(isinstance(value, str) and value for value in (packet, candidate, qualification)):
        return None
    return ["packet_cache_key_sha256:" + packet, "candidate_id:" + candidate,
            "qualification:" + qualification]


def _legacy_anchors(value: Sequence[Mapping[str, Any]], domain: tuple[float, float]) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SourceExactAnchorsError("qualified_anchors must be a sequence")
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise SourceExactAnchorsError("qualified anchor must be an object")
        try:
            index = int(item["canonical_line_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceExactAnchorsError("qualified anchor canonical_line_index is invalid") from exc
        interval = _interval(item.get("source_interval_ms"), label="qualified_anchor")
        proof_ids = _proof_ids(item)
        if index in seen or interval is None or proof_ids is None:
            raise SourceExactAnchorsError("qualified anchor interval or proof IDs are invalid")
        if not domain[0] <= interval[0] < interval[1] <= domain[1]:
            raise SourceExactAnchorsError("qualified anchor lies outside source domain")
        seen.add(index)
        result.append({"canonical_line_index": index,
                       "source_interval_ms": [_display_number(interval[0]), _display_number(interval[1])],
                       "proof_ids": proof_ids})
    result.sort(key=lambda item: int(item["canonical_line_index"]))
    return result


def _bracket(index: int, interval: tuple[float, float], legacy: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    left = [item for item in legacy if int(item["canonical_line_index"]) < index]
    right = [item for item in legacy if int(item["canonical_line_index"]) > index]
    if not left or not right:
        return None
    # The nearest canonical pair is fixed.  A malformed nearest bracket is not
    # rescued by farther legacy anchors.
    left_item, right_item = left[-1], right[0]
    left_interval = _interval(left_item["source_interval_ms"], label="legacy_left")
    right_interval = _interval(right_item["source_interval_ms"], label="legacy_right")
    assert left_interval is not None and right_interval is not None
    if not (left_interval[0] < left_interval[1] <= interval[0] < interval[1]
            <= right_interval[0] < right_interval[1]):
        return None
    return dict(left_item), dict(right_item)


def _timing_reason(items: Sequence[Mapping[str, Any]], domain: tuple[float, float]) -> tuple[tuple[float, float] | None, str | None, list[float] | None]:
    intervals: list[tuple[float, float]] = []
    probabilities: list[float] = []
    previous_end: float | None = None
    for item in items:
        probability = _number(item.get("probability"))
        if (probability is None or probability < SOURCE_EXACT_ANCHORS_MIN_PROBABILITY
                or probability > 1.0):
            return None, "observed_probability_below_threshold_or_nonfinite", None
        interval = _interval([item.get("start_ms"), item.get("end_ms")], label="observed_word")
        if interval is None:
            return None, "observed_word_time_invalid", None
        if not domain[0] <= interval[0] < interval[1] <= domain[1]:
            return None, "observed_word_outside_source_domain", None
        if previous_end is not None:
            gap = interval[0] - previous_end
            if gap < 0:
                return None, "observed_word_time_order_or_overlap_invalid", None
            if gap > SOURCE_EXACT_ANCHORS_MAX_ADJACENT_GAP_MS:
                return None, "observed_word_adjacent_gap_exceeded", None
        intervals.append(interval)
        probabilities.append(probability)
        previous_end = interval[1]
    if not intervals:
        return None, "observed_exact_sequence_empty", None
    return (intervals[0][0], intervals[-1][1]), None, probabilities


def build_exact_source_anchors(
    canonical_lines: Sequence[Mapping[str, Any]],
    observed_words: Sequence[Mapping[str, Any]],
    *,
    source_observation_sha256: str,
    source_audio_sha256: str,
    source_domain_ms: Sequence[float] | Mapping[str, float],
    qualified_anchors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build independently bounded experimental exact source anchors.

    ``qualified_anchors`` must be previously qualified source-packet anchors;
    exact matches never qualify or corroborate each other.  The nearest legacy
    anchors on both canonical sides must also bound the observed source time.
    """
    observation_sha = _verify_sha(source_observation_sha256, label="source_observation_sha256")
    audio_sha = _verify_sha(source_audio_sha256, label="source_audio_sha256")
    domain = _source_domain(source_domain_ms)
    rows = _canonical_rows(canonical_lines)
    observed = _observed_units(observed_words)
    legacy = _legacy_anchors(qualified_anchors, domain)
    canonical_indices = {int(row["canonical_line_index"]) for row in rows}
    if any(int(item["canonical_line_index"]) not in canonical_indices for item in legacy):
        raise SourceExactAnchorsError("qualified anchor is not a canonical line")
    canonical_raw_sha = _json_sha(canonical_lines)
    observed_raw_sha = _json_sha(observed_words)
    constants = {
        "policy_id": SOURCE_EXACT_ANCHORS_POLICY_ID,
        "minimum_lexical_units": SOURCE_EXACT_ANCHORS_MIN_UNITS,
        "minimum_probability": SOURCE_EXACT_ANCHORS_MIN_PROBABILITY,
        "maximum_adjacent_gap_ms": SOURCE_EXACT_ANCHORS_MAX_ADJACENT_GAP_MS,
        "canonical_uniqueness": "flattened_full_canonical_before_confidence_or_domain_filtering",
        "observed_uniqueness": "full_observed_stream_before_confidence_or_domain_filtering",
        "unsupported_observed_item": "barrier_not_deleted",
        "legacy_bracket": "nearest_old_qualified_left_right_canonical_and_source_time",
    }
    input_identity = {
        "canonical_lines_sha256": canonical_raw_sha,
        "observed_words_sha256": observed_raw_sha,
        "source_observation_sha256": observation_sha,
        "source_audio_sha256": audio_sha,
        "source_domain_ms": [_display_number(domain[0]), _display_number(domain[1])],
        "policy_constants": constants,
        "qualified_anchors": legacy,
    }
    input_identity["identity_sha256"] = _json_sha(input_identity)
    canonical_stream: list[str | None] = []
    for row in rows:
        units = row["lexical_units"]
        canonical_stream.extend(units if units is not None else [None])
    observed_stream = [item["unit"] for item in observed]
    candidates: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    for row in rows:
        index, units = int(row["canonical_line_index"]), row["lexical_units"]
        entry: dict[str, Any] = {"canonical_line_index": index, "status": "rejected"}
        if units is None:
            entry["reason"] = "canonical_line_not_complete_english_lexical_sequence"
        elif len(units) < SOURCE_EXACT_ANCHORS_MIN_UNITS:
            entry["reason"] = "canonical_line_below_minimum_lexical_units"
        else:
            canonical_hits = _occurrences(canonical_stream, units)
            if len(canonical_hits) != 1:
                entry["reason"] = "canonical_sequence_not_unique_in_full_canonical"
                entry["canonical_sequence_match_count"] = len(canonical_hits)
            else:
                observed_hits = _occurrences(observed_stream, units)
                if len(observed_hits) != 1:
                    entry["reason"] = "observed_sequence_not_unique_in_full_observed_stream"
                    entry["observed_sequence_match_count"] = len(observed_hits)
                else:
                    offset = observed_hits[0]
                    matched = observed[offset:offset + len(units)]
                    raw_items = [item["raw"] for item in matched]
                    if any(not isinstance(item, Mapping) for item in raw_items):
                        # Defensive: matching a barrier is impossible, but do
                        # not turn malformed data into a timestamp candidate.
                        entry["reason"] = "observed_exact_sequence_contains_unsupported_item"
                    else:
                        interval, reason, probabilities = _timing_reason(raw_items, domain)  # type: ignore[arg-type]
                        if reason is not None or interval is None or probabilities is None:
                            entry["reason"] = reason or "observed_exact_sequence_invalid"
                        else:
                            bracket = _bracket(index, interval, legacy)
                            if bracket is None:
                                entry["reason"] = "old_qualified_anchor_bracket_missing_or_time_invalid"
                            else:
                                candidate = {
                                    "canonical_line_index": index,
                                    "source_interval_ms": [_display_number(interval[0]), _display_number(interval[1])],
                                    "observed_word_indices": [int(item["observed_word_index"]) for item in matched],
                                    "lexical_units": list(units),
                                    "probabilities": probabilities,
                                    # Keep the exact per-word clock evidence
                                    # beside the raw-observation SHA binding.
                                    # This is never split or normalized into
                                    # separately timed pseudo-words.
                                    "observed_word_evidence": [{
                                        "observed_word_index": int(item["observed_word_index"]),
                                        "text": str(item["raw"]["text"]),
                                        "start_ms": _display_number(float(item["raw"]["start_ms"])),
                                        "end_ms": _display_number(float(item["raw"]["end_ms"])),
                                        "probability": probabilities[position],
                                    } for position, item in enumerate(matched)],
                                    "legacy_bracket": {"left": bracket[0], "right": bracket[1]},
                                }
                                candidate["anchor_identity_sha256"] = _json_sha({
                                    "input_identity_sha256": input_identity["identity_sha256"], "anchor": candidate,
                                })
                                candidates.append(candidate)
                                entry.update({"status": "qualified_pending_cross_line_order",
                                              "anchor_identity_sha256": candidate["anchor_identity_sha256"]})
        ledger.append(entry)
    # Exact matches are never used to select each other.  If independently
    # qualified candidates conflict in source order, every participant loses.
    conflicts: set[int] = set()
    for left_offset, left in enumerate(candidates):
        left_interval = _interval(left["source_interval_ms"], label="candidate")
        assert left_interval is not None
        for right_offset in range(left_offset + 1, len(candidates)):
            right = candidates[right_offset]
            right_interval = _interval(right["source_interval_ms"], label="candidate")
            assert right_interval is not None
            if int(left["canonical_line_index"]) < int(right["canonical_line_index"]) and left_interval[1] > right_interval[0]:
                conflicts.update({left_offset, right_offset})
    anchors = [candidate for offset, candidate in enumerate(candidates) if offset not in conflicts]
    ledger_by_index = {int(item["canonical_line_index"]): item for item in ledger}
    for offset, candidate in enumerate(candidates):
        entry = ledger_by_index[int(candidate["canonical_line_index"])]
        if offset in conflicts:
            entry.update({"status": "rejected", "reason": "cross_line_source_order_or_overlap_conflict"})
        else:
            entry["status"] = "qualified"
    return {
        "schema_version": SOURCE_EXACT_ANCHORS_SCHEMA_VERSION,
        "policy_id": SOURCE_EXACT_ANCHORS_POLICY_ID,
        "authority": "experimental_source_anchor_only_not_subtitle_authority",
        "automatic_production_mutation_allowed": False,
        "source_observation_sha256": observation_sha,
        "source_audio_sha256": audio_sha,
        "source_domain_ms": [_display_number(domain[0]), _display_number(domain[1])],
        "input_identity": input_identity,
        "anchors": anchors,
        "ledger": ledger,
        "counts": {
            "canonical_line_denominator": len(rows),
            "qualified_anchor_count": len(anchors),
            "rejected_anchor_count": len(rows) - len(anchors),
        },
    }


__all__ = [
    "SOURCE_EXACT_ANCHORS_MAX_ADJACENT_GAP_MS",
    "SOURCE_EXACT_ANCHORS_MIN_PROBABILITY",
    "SOURCE_EXACT_ANCHORS_MIN_UNITS",
    "SOURCE_EXACT_ANCHORS_POLICY_ID",
    "SOURCE_EXACT_ANCHORS_SCHEMA_VERSION",
    "SourceExactAnchorsError",
    "build_exact_source_anchors",
]
