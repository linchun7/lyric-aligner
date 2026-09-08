"""Source-side multi-line lyric packet candidates.

This module locates a short canonical packet (the target cue plus adjacent
canonical lines) inside already observed source-ASR word timestamps.  It does
not decode audio, does not use LRC timestamps, and does not act as forced
phoneme alignment.  Its output is an auditable *observed transcript* proposal
which a later evidence/selection stage may choose to consume.

The important safety property is that a repeated short target line is never
selected from its first textual occurrence.  Only a complete ordered context
packet may select a candidate, and two surviving source occurrences remain
ambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
import json
import math
import unicodedata
from typing import Any, Mapping, Sequence

from lyric_aligner.alignment.asr_executor import WORD_MATCH_POLICY_ID
from lyric_aligner.text.bijective_han import POLICY_ID as BIJECTIVE_HAN_POLICY_ID
from lyric_aligner.text.bijective_han import fold_unambiguous_han


SOURCE_PACKET_SCHEMA_VERSION = "source-packet-candidates-1.1"
SOURCE_PACKET_POLICY_ID = "source-multiline-context-packet-2026-09-08-v3-tolerant-context-coverage"
EXACT_TARGET_POLICY = "exact_target_only_v1"
BOUNDED_TARGET_POLICY = "exact_or_outer_anchored_bounded_edit_v1"
SOURCE_PACKET_BOUNDED_POLICY_ID = "source-multiline-context-packet-2026-09-08-v5-outer-anchored-target-edit-bounded-search"
# Fixed lexical eligibility only.  This is not calibrated against a song,
# language, time error, editor artifact, or hidden/gold boundary labels.
SOURCE_PACKET_CONTEXT_MIN_MATCH_RATIO = 0.80
# The bounded-target policy has fixed work limits.  They are part of its
# cache identity below: changing one is a policy change, not an invisible
# performance tweak.  The limits cover every character-DP matrix (including
# unchanged-context checks), and every possible target span considered.
SOURCE_PACKET_MAX_ALIGNMENT_DP_CELLS = 250_000
SOURCE_PACKET_MAX_TOTAL_DP_CELLS = 2_000_000
SOURCE_PACKET_MAX_SEARCH_SPANS = 512
SOURCE_PACKET_MAX_PARTIAL_MATCHER_CELLS = 250_000
SOURCE_PACKET_NORMALIZATION_ID = (
    "nfkc-casefold-alnum+"
    f"{BIJECTIVE_HAN_POLICY_ID}+compatible-with:{WORD_MATCH_POLICY_ID}"
)
SOURCE_PACKET_AUTHORITY = (
    "observed_transcript_alignment_only_not_forced_phoneme_or_production_authority"
)


class SourcePacketError(ValueError):
    """Raised for malformed packet inputs before any matching is attempted."""


class _SourcePacketResourceLimit(RuntimeError):
    """Internal fail-closed signal for an incomplete bounded-policy search."""

    def __init__(self, limit: str) -> None:
        super().__init__(limit)
        self.limit = limit


@dataclass
class _SearchBudget:
    """Account bounded-policy search work before allocating any DP matrix.

    A budget failure means unexamined source occurrences may exist.  The
    caller must consequently discard tentative candidates rather than select
    the best result seen so far.
    """

    # A cell is one logical (canonical, observed) DP-grid position, not the
    # physical storage of both Python matrices used by _edit_align.
    max_alignment_dp_cells: int = SOURCE_PACKET_MAX_ALIGNMENT_DP_CELLS
    max_total_dp_cells: int = SOURCE_PACKET_MAX_TOTAL_DP_CELLS
    max_search_spans: int = SOURCE_PACKET_MAX_SEARCH_SPANS
    max_partial_matcher_cells: int = SOURCE_PACKET_MAX_PARTIAL_MATCHER_CELLS
    dp_cells_used: int = 0
    search_spans_examined: int = 0
    context_dp_calls: int = 0
    target_dp_calls: int = 0
    partial_matcher_cells_estimated: int = 0
    partial_matcher_calls: int = 0

    def consume_dp_cells(self, cells: int, *, kind: str) -> None:
        if kind not in {"context", "target"}:
            raise SourcePacketError("unsupported bounded-search DP kind")
        if cells > self.max_alignment_dp_cells:
            raise _SourcePacketResourceLimit("max_alignment_dp_cells")
        if self.dp_cells_used + cells > self.max_total_dp_cells:
            raise _SourcePacketResourceLimit("max_total_dp_cells")
        self.dp_cells_used += cells
        if kind == "context":
            self.context_dp_calls += 1
        else:
            self.target_dp_calls += 1

    def consume_search_span(self) -> None:
        if self.search_spans_examined >= self.max_search_spans:
            raise _SourcePacketResourceLimit("max_search_spans")
        self.search_spans_examined += 1

    def consume_partial_matcher_cells(self, cells: int) -> None:
        # SequenceMatcher is not a DP implementation, but this conservative
        # input-pair upper bound prevents its known repeated-character
        # quadratic path from bypassing the bounded-policy search contract.
        if cells > self.max_partial_matcher_cells:
            raise _SourcePacketResourceLimit("max_partial_matcher_cells")
        self.partial_matcher_cells_estimated += cells
        self.partial_matcher_calls += 1

    def coverage(self) -> dict[str, int]:
        return {
            "max_alignment_dp_cells": self.max_alignment_dp_cells,
            "max_total_dp_cells": self.max_total_dp_cells,
            "max_search_spans": self.max_search_spans,
            "dp_cells_used": self.dp_cells_used,
            "search_spans_examined": self.search_spans_examined,
            "context_dp_calls": self.context_dp_calls,
            "target_dp_calls": self.target_dp_calls,
            "max_partial_matcher_cells": self.max_partial_matcher_cells,
            "partial_matcher_cells_estimated": self.partial_matcher_cells_estimated,
            "partial_matcher_calls": self.partial_matcher_calls,
        }


def _bounded_search_limits() -> dict[str, int]:
    """Return the identity-bound limits without mutable runtime counters."""

    return {
        "max_alignment_dp_cells": SOURCE_PACKET_MAX_ALIGNMENT_DP_CELLS,
        "max_total_dp_cells": SOURCE_PACKET_MAX_TOTAL_DP_CELLS,
        "max_search_spans": SOURCE_PACKET_MAX_SEARCH_SPANS,
        "max_partial_matcher_cells": SOURCE_PACKET_MAX_PARTIAL_MATCHER_CELLS,
    }


def _new_bounded_search_budget() -> _SearchBudget:
    """Read fixed policy limits at call time for controlled test injection."""

    return _SearchBudget(**_bounded_search_limits())


@dataclass(frozen=True)
class _CanonicalLine:
    index: int
    text: str
    normalized: str
    normalized_chars: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _ObservedWord:
    index: int
    original_indices: tuple[int, ...]
    window_ids: tuple[str, ...]
    text: str
    normalized: str
    normalized_chars: tuple[tuple[str, int], ...]
    start_ms: int | None
    end_ms: int | None


@dataclass(frozen=True)
class _StreamCharacter:
    character: str
    observed_word_index: int
    observed_char_index: int
    observed_normalized_char_index: int


@dataclass(frozen=True)
class _PacketCharacter:
    character: str
    line: _CanonicalLine
    raw_index: int
    normalized_char_index: int
    packet_index: int


@dataclass(frozen=True)
class _EditOperation:
    operation: str
    canonical: _PacketCharacter | None
    observed_stream_index: int | None


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_sha(payload: Mapping[str, Any]) -> str:
    return _sha(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _mapping_value(row: Any, field: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(field)
    return getattr(row, field, None)


def _integer(value: Any, *, label: str, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise SourcePacketError(f"{label} must be an integer")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SourcePacketError(f"{label} must be an integer") from exc
    if not math.isfinite(number) or int(round(number)) != number:
        raise SourcePacketError(f"{label} must be a finite integer")
    return int(number)


def _normalized_with_trace(text: str) -> tuple[str, tuple[tuple[str, int], ...]]:
    """Mirror the ASR lexical comparison normalizer while retaining raw offsets.

    The normalizer is deliberately script-neutral: NFKC/casefold/alphanumeric
    filtering works for Han, Hangul, Kana and Latin.  The final Han fold is
    one-to-one only, so it is safe for lexical comparison but never emitted as
    rewritten canonical text.
    """

    if not isinstance(text, str):
        raise SourcePacketError("text must be a string")
    traced: list[tuple[str, int]] = []
    for raw_index, raw_character in enumerate(text):
        normalized = unicodedata.normalize("NFKC", raw_character).casefold()
        normalized = fold_unambiguous_han(normalized)
        for character in normalized:
            if character.isalnum():
                traced.append((character, raw_index))
    return "".join(character for character, _ in traced), tuple(traced)


def _parse_canonical_lines(canonical_lines: Sequence[Any]) -> tuple[_CanonicalLine, ...]:
    if isinstance(canonical_lines, (str, bytes)) or not isinstance(canonical_lines, Sequence):
        raise SourcePacketError("canonical_lines must be an ordered sequence")
    lines: list[_CanonicalLine] = []
    seen: set[int] = set()
    previous = None
    for raw in canonical_lines:
        index = _integer(_mapping_value(raw, "canonical_line_index"), label="canonical_line_index")
        text = _mapping_value(raw, "text")
        if not isinstance(text, str) or not text:
            raise SourcePacketError("canonical line text must be non-empty")
        if index in seen or (previous is not None and index <= previous):
            raise SourcePacketError("canonical_lines must have unique increasing canonical_line_index values")
        normalized, trace = _normalized_with_trace(text)
        if not normalized:
            raise SourcePacketError("canonical line has no lexical characters")
        lines.append(_CanonicalLine(index, text, normalized, trace))
        seen.add(index)
        previous = index
    if not lines:
        raise SourcePacketError("canonical_lines must not be empty")
    return tuple(lines)


def _parse_domain(source_search_domain: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(source_search_domain, Mapping):
        raise SourcePacketError("source_search_domain must be an object")
    start = _integer(source_search_domain.get("start_ms"), label="source_search_domain.start_ms")
    end = _integer(source_search_domain.get("end_ms"), label="source_search_domain.end_ms")
    if start is None or end is None or start < 0 or end <= start:
        raise SourcePacketError("source_search_domain must be a nonnegative nonempty interval")
    domain_id = source_search_domain.get("domain_id")
    if domain_id is not None and not isinstance(domain_id, str):
        raise SourcePacketError("source_search_domain.domain_id must be a string when supplied")
    return {"start_ms": start, "end_ms": end, "domain_id": domain_id or None}


def _parse_target_ranges(
    target_cue: Mapping[str, Any],
    lines_by_index: Mapping[int, _CanonicalLine],
) -> tuple[tuple[dict[str, int], ...], tuple[int, ...]]:
    if not isinstance(target_cue, Mapping):
        raise SourcePacketError("target_cue must be an object")
    raw_ranges = target_cue.get("canonical_character_ranges")
    if not isinstance(raw_ranges, list) or not raw_ranges:
        raise SourcePacketError("target_cue.canonical_character_ranges must be a non-empty list")
    parsed: list[dict[str, int]] = []
    claimed: set[tuple[int, int]] = set()
    for raw in raw_ranges:
        if not isinstance(raw, Mapping):
            raise SourcePacketError("canonical character range must be an object")
        line_index = _integer(raw.get("canonical_line_index"), label="range.canonical_line_index")
        start = _integer(raw.get("start_char"), label="range.start_char")
        end = _integer(raw.get("end_char"), label="range.end_char")
        line = lines_by_index.get(line_index)
        if line is None or start is None or end is None or start < 0 or end <= start or end > len(line.text):
            raise SourcePacketError("canonical character range is outside canonical text")
        for character_index in range(start, end):
            key = (line_index, character_index)
            if key in claimed:
                raise SourcePacketError("canonical character ranges must not overlap")
            claimed.add(key)
        parsed.append({"canonical_line_index": line_index, "start_char": start, "end_char": end})
    positions = tuple(
        position
        for position, line in enumerate(lines_by_index.values())
        if any(item["canonical_line_index"] == line.index for item in parsed)
    )
    if not positions:
        raise SourcePacketError("target cue does not claim canonical characters")
    has_lexical = any(
        line_index == line.index and start <= raw_index < end
        for line_index, start, end in (
            (item["canonical_line_index"], item["start_char"], item["end_char"])
            for item in parsed
        )
        for line in lines_by_index.values()
        for _, raw_index in line.normalized_chars
    )
    if not has_lexical:
        raise SourcePacketError("target cue has no lexical canonical characters")
    return tuple(parsed), positions


def _inside_domain(word: _ObservedWord, domain: Mapping[str, Any]) -> bool:
    """Keep unknown-time words, but never borrow an explicitly outside word."""

    for value in (word.start_ms, word.end_ms):
        if value is not None and not (int(domain["start_ms"]) <= value <= int(domain["end_ms"])):
            return False
    return True


def _parse_observed_words(
    observed_words: Sequence[Any],
    domain: Mapping[str, Any],
) -> tuple[_ObservedWord, ...]:
    if isinstance(observed_words, (str, bytes)) or not isinstance(observed_words, Sequence):
        raise SourcePacketError("observed_words must be a sequence")
    raw_words: list[_ObservedWord] = []
    for input_index, raw in enumerate(observed_words):
        text = _mapping_value(raw, "text")
        if not isinstance(text, str):
            raise SourcePacketError("observed word text must be a string")
        normalized, trace = _normalized_with_trace(text)
        if not normalized:
            continue
        start = _integer(_mapping_value(raw, "start_ms"), label="observed start_ms", allow_none=True)
        end = _integer(_mapping_value(raw, "end_ms"), label="observed end_ms", allow_none=True)
        if start is not None and start < 0 or end is not None and end < 0:
            raise SourcePacketError("observed word time must be nonnegative")
        if start is not None and end is not None and end < start:
            raise SourcePacketError("observed word end_ms must not precede start_ms")
        window = _mapping_value(raw, "window_id")
        if window is not None and not isinstance(window, str):
            raise SourcePacketError("observed word window_id must be a string when supplied")
        word = _ObservedWord(
            index=input_index,
            original_indices=(input_index,),
            window_ids=(window,) if window else (),
            text=text,
            normalized=normalized,
            normalized_chars=trace,
            start_ms=start,
            end_ms=end,
        )
        if _inside_domain(word, domain):
            raw_words.append(word)

    # Timestamped source windows may arrive in arbitrary window order, so sort
    # a fully timed collection.  Once a source observation lacks a start,
    # reordering it against its neighbours would invent transcript order; keep
    # the producer's order in that case.  Missing timing can still establish
    # text identity but can never establish the corresponding outer endpoint.
    if all(word.start_ms is not None for word in raw_words):
        raw_words.sort(
            key=lambda row: (row.start_ms, row.end_ms if row.end_ms is not None else row.index, row.index)
        )
    merged: list[_ObservedWord] = []
    by_identity: dict[tuple[int, int, str], list[int]] = {}
    for word in raw_words:
        # A repeated lexical token is only an overlapping-window duplicate when
        # both timestamps establish a positive source interval and the two
        # producers name different windows.  Do not collapse repeated `na na`
        # from the same source window, or an untimed/zero-duration observation:
        # both still carry text order even though neither can prove an edge.
        if (
            word.start_ms is None
            or word.end_ms is None
            or word.end_ms <= word.start_ms
            or not word.window_ids
        ):
            merged.append(word)
            continue
        identity = (word.start_ms, word.end_ms, word.normalized)
        previous_index = next(
            (
                index
                for index in by_identity.get(identity, ())
                if merged[index].window_ids
                and set(merged[index].window_ids).isdisjoint(word.window_ids)
            ),
            None,
        )
        if previous_index is None:
            by_identity.setdefault(identity, []).append(len(merged))
            merged.append(word)
            continue
        previous = merged[previous_index]
        # Identical observations from overlapping windows are one transcript
        # token.  Same text at a different source time deliberately has a
        # different key and stays as a distinct repeat candidate.
        merged[previous_index] = _ObservedWord(
            index=previous.index,
            original_indices=tuple(sorted((*previous.original_indices, *word.original_indices))),
            window_ids=tuple(sorted(set((*previous.window_ids, *word.window_ids)))),
            text=previous.text,
            normalized=previous.normalized,
            normalized_chars=previous.normalized_chars,
            start_ms=previous.start_ms,
            end_ms=previous.end_ms,
        )
    return tuple(
        _ObservedWord(
            index=index,
            original_indices=word.original_indices,
            window_ids=word.window_ids,
            text=word.text,
            normalized=word.normalized,
            normalized_chars=word.normalized_chars,
            start_ms=word.start_ms,
            end_ms=word.end_ms,
        )
        for index, word in enumerate(merged)
    )


def _stream(words: Sequence[_ObservedWord]) -> tuple[str, tuple[_StreamCharacter, ...]]:
    characters: list[_StreamCharacter] = []
    for word in words:
        for normalized_index, (character, raw_index) in enumerate(word.normalized_chars):
            characters.append(_StreamCharacter(character, word.index, raw_index, normalized_index))
    return "".join(item.character for item in characters), tuple(characters)


def _all_occurrences(haystack: str, needle: str) -> tuple[int, ...]:
    if not needle:
        return ()
    found: list[int] = []
    start = 0
    while True:
        position = haystack.find(needle, start)
        if position < 0:
            return tuple(found)
        found.append(position)
        start = position + 1


def _positive_start(word: _ObservedWord) -> int | None:
    return word.start_ms if word.start_ms is not None and word.end_ms is not None and word.end_ms > word.start_ms else None


def _positive_end(word: _ObservedWord) -> int | None:
    return word.end_ms if word.start_ms is not None and word.end_ms is not None and word.end_ms > word.start_ms else None


def _boundary_reason(word: _ObservedWord, *, boundary: str) -> str:
    value = _positive_start(word) if boundary == "start" else _positive_end(word)
    if value is not None:
        return "observed_positive_duration_word"
    if word.start_ms is None or word.end_ms is None:
        return "unknown_missing_word_endpoint"
    return "unknown_zero_duration_word"


def _packet_characters(packet: Sequence[_CanonicalLine]) -> tuple[_PacketCharacter, ...]:
    """Flatten the packet without losing canonical raw-character ownership."""

    flattened: list[_PacketCharacter] = []
    for line in packet:
        for normalized_index, (character, raw_index) in enumerate(line.normalized_chars):
            flattened.append(
                _PacketCharacter(
                    character=character,
                    line=line,
                    raw_index=raw_index,
                    normalized_char_index=normalized_index,
                    packet_index=len(flattened),
                )
            )
    return tuple(flattened)


def _canonical_ref_payload(ref: _PacketCharacter) -> dict[str, Any]:
    return {
        "canonical_line_index": ref.line.index,
        "canonical_char_index": ref.raw_index,
        "canonical_character": ref.line.text[ref.raw_index],
        "canonical_normalized_character": ref.character,
        "canonical_normalized_char_index": ref.normalized_char_index,
        "packet_character_index": ref.packet_index,
    }


def _observed_ref_payload(
    stream_index: int,
    stream_characters: Sequence[_StreamCharacter],
    words: Sequence[_ObservedWord],
) -> dict[str, Any]:
    observed = stream_characters[stream_index]
    word = words[observed.observed_word_index]
    return {
        "observed_stream_character_index": stream_index,
        "observed_word_index": observed.observed_word_index,
        "observed_observation_indices": list(word.original_indices),
        "observed_window_ids": list(word.window_ids),
        "observed_char_index": observed.observed_char_index,
        "observed_normalized_char_index": observed.observed_normalized_char_index,
        "observed_character": word.text[observed.observed_char_index],
        "observed_normalized_character": observed.character,
    }


def _trace_operation_payload(
    operation: _EditOperation,
    stream_characters: Sequence[_StreamCharacter],
    words: Sequence[_ObservedWord],
) -> dict[str, Any]:
    return {
        "operation": operation.operation,
        "canonical": _canonical_ref_payload(operation.canonical) if operation.canonical else None,
        "observed": (
            _observed_ref_payload(operation.observed_stream_index, stream_characters, words)
            if operation.observed_stream_index is not None
            else None
        ),
    }


def _edit_summary(operations: Sequence[_EditOperation]) -> dict[str, int]:
    edit_runs = 0
    in_edit = False
    for operation in operations:
        is_edit = operation.operation != "equal"
        if is_edit and not in_edit:
            edit_runs += 1
        in_edit = is_edit
    return {
        "edit_distance": sum(operation.operation != "equal" for operation in operations),
        "edit_run_count": edit_runs,
        "matched_characters": sum(operation.operation == "equal" for operation in operations),
    }


def _edit_align(
    canonical: Sequence[_PacketCharacter],
    observed_indices: Sequence[int],
    stream_characters: Sequence[_StreamCharacter],
    *,
    side: str,
    budget: _SearchBudget | None = None,
) -> tuple[_EditOperation, ...]:
    """Return a bounded monotonic character edit trace.

    ``left`` aligns a canonical suffix ending at the exact target anchor; its
    leading observed window is free because it may belong to earlier lyrics.
    ``right`` aligns a canonical prefix immediately after the anchor; trailing
    observed window text is free for the same reason.  Neither form is timed
    and neither allows a target character to be fabricated by an edit.
    """

    if side not in {"left", "right"}:
        raise SourcePacketError("unsupported edit-alignment side")
    m, n = len(canonical), len(observed_indices)
    # Account before either matrix allocation.  A bounded-policy caller must
    # never discover its work cap only after it has already committed to a
    # partial set of source occurrences.
    if budget is not None:
        budget.consume_dp_cells((m + 1) * (n + 1), kind="context")
    costs = [[0] * (n + 1) for _ in range(m + 1)]
    moves: list[list[str | None]] = [[None] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        costs[i][0] = i
        moves[i][0] = "delete_canonical"
    for j in range(1, n + 1):
        costs[0][j] = 0 if side == "left" else j
        moves[0][j] = "free_prefix" if side == "left" else "insert_observed"
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            equal = canonical[i - 1].character == stream_characters[observed_indices[j - 1]].character
            diagonal = costs[i - 1][j - 1] + (0 if equal else 1)
            delete = costs[i - 1][j] + 1
            insert = costs[i][j - 1] + 1
            # Prefer a lexical equality, then a monotonic replacement, then
            # deletes/inserts.  This stable rule makes trace identities
            # reproducible without asserting a language reliability order.
            choices = [
                (diagonal, "equal" if equal else "replace"),
                (delete, "delete_canonical"),
                (insert, "insert_observed"),
            ]
            costs[i][j], moves[i][j] = min(
                choices,
                key=lambda item: (item[0], {"equal": 0, "replace": 1, "delete_canonical": 2, "insert_observed": 3}[item[1]]),
            )
    if side == "left":
        i, j = m, n
    else:
        i = m
        j = min(range(n + 1), key=lambda value: (costs[m][value], value))
    reverse: list[_EditOperation] = []
    while i or j:
        move = moves[i][j]
        if i == 0 and side == "left":
            break
        if move in {"equal", "replace"}:
            reverse.append(_EditOperation(move, canonical[i - 1], observed_indices[j - 1]))
            i -= 1
            j -= 1
        elif move == "delete_canonical":
            reverse.append(_EditOperation(move, canonical[i - 1], None))
            i -= 1
        elif move == "insert_observed":
            reverse.append(_EditOperation(move, None, observed_indices[j - 1]))
            j -= 1
        else:
            break
    reverse.reverse()
    return tuple(reverse)


def _align_context_side(
    canonical: Sequence[_PacketCharacter],
    *,
    anchor_start: int,
    anchor_end: int,
    stream_characters: Sequence[_StreamCharacter],
    side: str,
    budget: _SearchBudget | None = None,
) -> tuple[tuple[_EditOperation, ...], dict[str, Any]]:
    if not canonical:
        return (), {
            "applicable": False,
            "canonical_character_count": 0,
            "source_character_count": 0,
            "matched_characters": 0,
            "matched_character_ratio": None,
            "edit_distance": 0,
            "edit_run_count": 0,
            "trace": [],
        }
    # A bounded character window permits a missing/replaced neighbouring word
    # without scanning the entire recording per target occurrence.  The cap is
    # a fixed matcher bound, not an audio-duration or song-specific threshold.
    tolerance = min(16, max(2, len(canonical) // 3))
    if side == "left":
        window_start = max(0, anchor_start - len(canonical) - tolerance)
        indices = tuple(range(window_start, anchor_start))
    elif side == "right":
        window_end = min(len(stream_characters), anchor_end + len(canonical) + tolerance)
        indices = tuple(range(anchor_end, window_end))
    else:
        raise SourcePacketError("unsupported context side")
    operations = _edit_align(canonical, indices, stream_characters, side=side, budget=budget)
    metrics = _edit_summary(operations)
    return operations, {
        "applicable": True,
        "canonical_character_count": len(canonical),
        "source_character_count": len(indices),
        **metrics,
        "matched_character_ratio": metrics["matched_characters"] / len(canonical),
        "trace": [],
    }


def _boundary_for_ref(
    ref: _PacketCharacter,
    mappings: Mapping[tuple[int, int, int], int],
    stream_characters: Sequence[_StreamCharacter],
    words: Sequence[_ObservedWord],
    *,
    boundary: str,
) -> tuple[int | None, str]:
    stream_index = mappings.get((ref.line.index, ref.raw_index, ref.normalized_char_index))
    if stream_index is None:
        return None, "unmatched_observed_transcript"
    observed = stream_characters[stream_index]
    word = words[observed.observed_word_index]
    # Observed word timestamps have no demonstrated sub-word clock.  A cue
    # that starts/ends inside ``foobar`` cannot inherit the whole word's edge
    # just because its text contains ``foo`` or ``bar``.
    if boundary == "start" and observed.observed_normalized_char_index != 0:
        return None, "unknown_cue_edge_inside_observed_word"
    if boundary == "end" and observed.observed_normalized_char_index != len(word.normalized_chars) - 1:
        return None, "unknown_cue_edge_inside_observed_word"
    return (
        _positive_start(word) if boundary == "start" else _positive_end(word),
        _boundary_reason(word, boundary=boundary),
    )


def _candidate_from_trace(
    *,
    packet: Sequence[_CanonicalLine],
    packet_characters: Sequence[_PacketCharacter],
    target_characters: Sequence[_PacketCharacter],
    target_ranges: Sequence[Mapping[str, int]],
    stream_characters: Sequence[_StreamCharacter],
    words: Sequence[_ObservedWord],
    operations: Sequence[_EditOperation],
    context: Mapping[str, Any],
    match_kind: str,
    cache_key_sha256: str,
    anchor_start: int | None,
) -> dict[str, Any]:
    mappings: dict[tuple[int, int, int], int] = {}
    for operation in operations:
        if operation.operation == "equal" and operation.canonical is not None and operation.observed_stream_index is not None:
            mappings[(
                operation.canonical.line.index,
                operation.canonical.raw_index,
                operation.canonical.normalized_char_index,
            )] = operation.observed_stream_index

    def character_row(ref: _PacketCharacter) -> dict[str, Any]:
        row = _canonical_ref_payload(ref)
        stream_index = mappings.get((ref.line.index, ref.raw_index, ref.normalized_char_index))
        if stream_index is None:
            return {**row, "match_status": "unmatched_observed_transcript", "observed_match": None}
        return {**row, "match_status": "matched", "observed_match": _observed_ref_payload(stream_index, stream_characters, words)}

    lines: list[dict[str, Any]] = []
    by_raw: dict[tuple[int, int], list[_PacketCharacter]] = {}
    reverse: list[dict[str, Any]] = []
    for ref in packet_characters:
        by_raw.setdefault((ref.line.index, ref.raw_index), []).append(ref)
        stream_index = mappings.get((ref.line.index, ref.raw_index, ref.normalized_char_index))
        if stream_index is not None:
            reverse.append({
                **_observed_ref_payload(stream_index, stream_characters, words),
                "canonical_line_index": ref.line.index,
                "canonical_char_index": ref.raw_index,
                "canonical_normalized_char_index": ref.normalized_char_index,
            })
    for line in packet:
        refs = [ref for ref in packet_characters if ref.line.index == line.index]
        start, start_reason = _boundary_for_ref(refs[0], mappings, stream_characters, words, boundary="start")
        end, end_reason = _boundary_for_ref(refs[-1], mappings, stream_characters, words, boundary="end")
        lines.append({
            "canonical_line_index": line.index,
            "canonical_text_sha256": _sha(line.text),
            "normalized_text_sha256": _sha(line.normalized),
            "start_ms": start,
            "end_ms": end,
            "start_reason": start_reason,
            "end_reason": end_reason,
            "character_attribution": [character_row(ref) for ref in refs],
        })

    target_attribution: list[dict[str, Any]] = []
    for item in target_ranges:
        line = next(line for line in packet if line.index == int(item["canonical_line_index"]))
        for raw_index in range(int(item["start_char"]), int(item["end_char"])):
            refs = by_raw.get((line.index, raw_index), [])
            matches = [
                _observed_ref_payload(mappings[(ref.line.index, ref.raw_index, ref.normalized_char_index)], stream_characters, words)
                for ref in refs
                if (ref.line.index, ref.raw_index, ref.normalized_char_index) in mappings
            ]
            status = (
                "nonlexical_not_aligned" if not refs
                else "matched" if len(matches) == len(refs)
                else "partially_matched_observed_transcript" if matches
                else "unmatched_observed_transcript"
            )
            target_attribution.append({
                "canonical_line_index": line.index,
                "canonical_char_index": raw_index,
                "canonical_character": line.text[raw_index],
                "match_status": status,
                "observed_matches": matches,
            })

    target_start, target_start_reason = _boundary_for_ref(
        target_characters[0], mappings, stream_characters, words, boundary="start"
    )
    target_end, target_end_reason = _boundary_for_ref(
        target_characters[-1], mappings, stream_characters, words, boundary="end"
    )
    packet_start, packet_start_reason = _boundary_for_ref(
        packet_characters[0], mappings, stream_characters, words, boundary="start"
    )
    packet_end, packet_end_reason = _boundary_for_ref(
        packet_characters[-1], mappings, stream_characters, words, boundary="end"
    )
    serialized_context = dict(context)
    serialized_context["trace"] = [
        _trace_operation_payload(operation, stream_characters, words) for operation in operations
    ]
    canonical_to_observed = [
        {
            **_canonical_ref_payload(operation.canonical),
            "observed": _observed_ref_payload(operation.observed_stream_index, stream_characters, words),
        }
        for operation in operations
        if operation.operation == "equal" and operation.canonical is not None and operation.observed_stream_index is not None
    ]
    edit_distance = int(context.get("context_edit_distance", context.get("edit_distance", 0)))
    matched = int(context.get("context_matched_characters", context.get("matched_characters", 0)))
    candidate = {
        "rank": 0,
        "match_kind": match_kind,
        "score": matched / max(1, matched + edit_distance),
        # This is the cue-owned range, never the surrounding packet range.
        "source_interval_ms": [target_start, target_end],
        "source_interval_start_reason": target_start_reason,
        "source_interval_end_reason": target_end_reason,
        "packet_source_interval_ms": [packet_start, packet_end],
        "packet_source_interval_start_reason": packet_start_reason,
        "packet_source_interval_end_reason": packet_end_reason,
        "lines": lines,
        "target_cue_character_attribution": target_attribution,
        "canonical_to_observed_characters": canonical_to_observed,
        "observed_to_canonical_characters": reverse,
        "context_alignment": serialized_context,
        "full_context_disambiguated": False,
        "context_disambiguation_status": "not_selected",
        "_anchor_start": -1 if anchor_start is None else anchor_start,
        "_sort_key": (
            edit_distance,
            -matched,
            int(context.get("context_edit_runs", context.get("edit_run_count", 0))),
            -1 if anchor_start is None else anchor_start,
        ),
    }
    return candidate


def _packet_identity(
    *,
    packet: Sequence[_CanonicalLine],
    target_ranges: Sequence[Mapping[str, int]],
    source_audio_sha256: str,
    lyric_version: str,
    source_search_domain: Mapping[str, Any],
    model_id: str,
    normalization_id: str,
    target_alignment_policy: str = EXACT_TARGET_POLICY,
) -> dict[str, Any]:
    if target_alignment_policy not in {EXACT_TARGET_POLICY, BOUNDED_TARGET_POLICY}:
        raise SourcePacketError("unsupported target_alignment_policy")
    return {
        "schema_version": SOURCE_PACKET_SCHEMA_VERSION if target_alignment_policy == EXACT_TARGET_POLICY else "source-packet-candidates-1.2",
        "policy_id": SOURCE_PACKET_POLICY_ID if target_alignment_policy == EXACT_TARGET_POLICY else SOURCE_PACKET_BOUNDED_POLICY_ID,
        **(
            {
                "target_alignment_policy": target_alignment_policy,
                "target_alignment_resource_limits": _bounded_search_limits(),
            }
            if target_alignment_policy != EXACT_TARGET_POLICY
            else {}
        ),
        "source_audio_sha256": source_audio_sha256,
        "lyric_version": lyric_version,
        "canonical_packet": [
            {
                "canonical_line_index": line.index,
                "text_sha256": _sha(line.text),
                "normalized_text_sha256": _sha(line.normalized),
            }
            for line in packet
        ],
        "target_cue_canonical_character_domain": [dict(item) for item in target_ranges],
        "source_search_domain": dict(source_search_domain),
        "model_id": model_id,
        "normalization_id": normalization_id,
    }


def source_packet_cache_key(
    *,
    canonical_lines: Sequence[Any],
    target_cue: Mapping[str, Any],
    source_audio_sha256: str,
    lyric_version: str,
    source_search_domain: Mapping[str, Any],
    model_id: str,
    normalization_id: str = SOURCE_PACKET_NORMALIZATION_ID,
    context_radius: int = 2,
    target_alignment_policy: str = EXACT_TARGET_POLICY,
) -> str:
    """Return the identity key before looking at any observed ASR output."""

    lines = _parse_canonical_lines(canonical_lines)
    if isinstance(context_radius, bool) or not isinstance(context_radius, int) or context_radius < 0:
        raise SourcePacketError("context_radius must be a nonnegative integer")
    if not isinstance(source_audio_sha256, str) or not source_audio_sha256.strip():
        raise SourcePacketError("source_audio_sha256 must be non-empty")
    if not isinstance(lyric_version, str) or not lyric_version.strip():
        raise SourcePacketError("lyric_version must be non-empty")
    if not isinstance(model_id, str) or not model_id.strip():
        raise SourcePacketError("model_id must be non-empty")
    if normalization_id != SOURCE_PACKET_NORMALIZATION_ID:
        raise SourcePacketError("unsupported normalization_id for source packet matching")
    domain = _parse_domain(source_search_domain)
    by_index = {line.index: line for line in lines}
    ranges, target_positions = _parse_target_ranges(target_cue, by_index)
    packet = lines[
        max(0, min(target_positions) - context_radius):min(len(lines), max(target_positions) + context_radius + 1)
    ]
    return _stable_sha(
        _packet_identity(
            packet=packet,
            target_ranges=ranges,
            source_audio_sha256=source_audio_sha256,
            lyric_version=lyric_version,
            source_search_domain=domain,
            model_id=model_id,
            normalization_id=normalization_id,
            target_alignment_policy=target_alignment_policy,
        )
    )


def _outer_anchored_target_candidates(
    *, packet, packet_characters, target_characters, target_ranges,
    stream_characters, words, observed_text, cache_key_sha256, budget: _SearchBudget,
):
    """Recover a single internal transcription edit without inventing edges.

    Span endpoints are actual positive-duration word edges. Both surrounding
    sides retain the exact matcher's qualification. The target lattice checks
    all paths within one edit of the minimum before claiming either endpoint;
    its chosen traceback is diagnostic, never an ambiguity tie breaker.
    """
    from lyric_aligner.alignment.anchored_edit import align_anchored_characters

    target_text = "".join(ref.character for ref in target_characters)
    size = len(target_text)
    tolerance = min(8, max(2, math.ceil(size * .2)))
    left_refs = packet_characters[:target_characters[0].packet_index]
    right_refs = packet_characters[target_characters[-1].packet_index + 1:]
    if not left_refs and not right_refs:
        return []

    def supported(context):
        return not context["applicable"] or (
            context["matched_character_ratio"] >= SOURCE_PACKET_CONTEXT_MIN_MATCH_RATIO
            and context["edit_run_count"] <= 1
        )

    ends = {
        index + 1 for index, ref in enumerate(stream_characters)
        if ref.character == target_text[-1]
        and ref.observed_normalized_char_index == len(words[ref.observed_word_index].normalized_chars) - 1
        and _positive_end(words[ref.observed_word_index]) is not None
    }
    right_cache = {}
    candidates = []
    for start, ref in enumerate(stream_characters):
        if (ref.character != target_text[0] or ref.observed_normalized_char_index != 0
                or _positive_start(words[ref.observed_word_index]) is None):
            continue
        left_ops, left_context = _align_context_side(left_refs, anchor_start=start,
            anchor_end=start, stream_characters=stream_characters, side="left", budget=budget)
        if not supported(left_context):
            continue
        for end in range(start + max(1, size - tolerance), min(len(stream_characters), start + size + tolerance) + 1):
            if end not in ends:
                continue
            # This is a possible full target span.  Count it before its
            # right-context or target lattice work so any unexamined span
            # invalidates the whole packet result.
            budget.consume_search_span()
            if end not in right_cache:
                right_cache[end] = _align_context_side(right_refs, anchor_start=end,
                    anchor_end=end, stream_characters=stream_characters, side="right", budget=budget)
            right_ops, right_context = right_cache[end]
            if not supported(right_context):
                continue
            budget.consume_dp_cells((size + 1) * (end - start + 1), kind="target")
            aligned = align_anchored_characters(
                target_text,
                observed_text[start:end],
                max_cells=budget.max_alignment_dp_cells,
            )
            if aligned["status"] != "ok":
                continue
            consensus = aligned["consensus_observed_indices"]
            if consensus[0] != 0 or consensus[-1] != end - start - 1:
                continue
            trace = aligned["diagnostic_trace"]
            matched = sum(item["operation"] == "equal" for item in trace)
            edit_runs = sum(item["operation"] != "equal" and (i == 0 or trace[i-1]["operation"] == "equal")
                for i, item in enumerate(trace))
            if matched / size < SOURCE_PACKET_CONTEXT_MIN_MATCH_RATIO or edit_runs != 1:
                continue
            if trace[0]["operation"] != "equal" or trace[-1]["operation"] != "equal":
                continue

            target_ops = []
            names = {"substitute": "replace", "insert": "insert_observed", "delete": "delete_canonical"}
            for item in trace:
                ci, oi = item["canonical_index"], item["observed_index"]
                operation = names.get(item["operation"], item["operation"])
                if operation == "equal" and consensus[ci] != oi:
                    # Keep the possible equality in the diagnostic trace below,
                    # but do not claim unique raw-character ownership for it.
                    operation, oi = "ambiguous_alignment", None
                target_ops.append(_EditOperation(operation,
                    target_characters[ci] if ci is not None else None,
                    start + oi if oi is not None else None))
            context_metrics = _edit_summary((*left_ops, *right_ops))
            candidate = _candidate_from_trace(packet=packet, packet_characters=packet_characters,
                target_characters=target_characters, target_ranges=target_ranges,
                stream_characters=stream_characters, words=words,
                operations=(*left_ops, *target_ops, *right_ops),
                context={"mode": "outer_anchored_bounded_target_edit_with_unchanged_context_v1",
                    "target_anchor_exact": False,
                    "left_context": left_context, "right_context": right_context,
                    "context_edit_distance": context_metrics["edit_distance"],
                    "context_edit_runs": context_metrics["edit_run_count"],
                    "context_matched_characters": context_metrics["matched_characters"],
                    "independent_context_support": True,
                    "target_alignment": {"policy": BOUNDED_TARGET_POLICY,
                        "observed_character_range": [start, end],
                        "minimum_edit_distance": aligned["minimum_edit_distance"],
                        "matched_character_ratio": matched / size, "edit_run_count": edit_runs,
                        "length_difference_limit": tolerance,
                        "minimum_matched_character_ratio": SOURCE_PACKET_CONTEXT_MIN_MATCH_RATIO,
                        "ambiguity_slack": aligned["ambiguity_slack"],
                        "outer_edges_consensus": True, "calibrated": False,
                        "consensus_observed_indices": consensus,
                        "character_states": aligned["character_states"],
                        "diagnostic_trace": trace}},
                match_kind="outer_anchored_bounded_target_edit",
                cache_key_sha256=cache_key_sha256, anchor_start=start)
            candidates.append(candidate)
    return candidates


def build_source_packet_candidates(
    *,
    canonical_lines: Sequence[Any],
    observed_words: Sequence[Any],
    target_cue: Mapping[str, Any],
    source_audio_sha256: str,
    lyric_version: str,
    source_search_domain: Mapping[str, Any],
    model_id: str,
    normalization_id: str = SOURCE_PACKET_NORMALIZATION_ID,
    context_radius: int = 2,
    n_best: int = 3,
    target_alignment_policy: str = EXACT_TARGET_POLICY,
) -> dict[str, Any]:
    """Build auditable source-ASR packet proposals without LRC time.

    The target must be observed as an exact lexical anchor before a candidate
    can be selected.  Its surrounding canonical lines are then compared with
    a bounded monotonic edit trace.  This keeps one neighbour ASR mistake from
    discarding an otherwise useful anchor, while preserving every actual
    observed-to-canonical equality and withholding an endpoint that is not
    demonstrated by a word edge.

    The explicit bounded-target policy additionally permits one internal
    transcription edit when both actual word edges have path consensus and
    the original context requirements still hold. The default exact policy
    retains its historical behavior and cache identity.
    """

    if isinstance(n_best, bool) or not isinstance(n_best, int) or n_best < 1:
        raise SourcePacketError("n_best must be a positive integer")
    lines = _parse_canonical_lines(canonical_lines)
    by_index = {line.index: line for line in lines}
    domain = _parse_domain(source_search_domain)
    ranges, target_positions = _parse_target_ranges(target_cue, by_index)
    cache_key_sha256 = source_packet_cache_key(
        canonical_lines=canonical_lines,
        target_cue=target_cue,
        source_audio_sha256=source_audio_sha256,
        lyric_version=lyric_version,
        source_search_domain=domain,
        model_id=model_id,
        normalization_id=normalization_id,
        context_radius=context_radius,
        target_alignment_policy=target_alignment_policy,
    )
    packet_start = max(0, min(target_positions) - context_radius)
    packet_end = min(len(lines), max(target_positions) + context_radius + 1)
    packet = lines[packet_start:packet_end]
    packet_characters = _packet_characters(packet)
    target_keys = {
        (int(item["canonical_line_index"]), raw_index)
        for item in ranges
        for raw_index in range(int(item["start_char"]), int(item["end_char"]))
    }
    target_characters = tuple(
        ref for ref in packet_characters if (ref.line.index, ref.raw_index) in target_keys
    )
    target_text = "".join(ref.character for ref in target_characters)
    target_positions_in_packet = [ref.packet_index for ref in target_characters]
    target_is_contiguous = target_positions_in_packet == list(
        range(target_positions_in_packet[0], target_positions_in_packet[-1] + 1)
    )
    packet_text = "".join(ref.character for ref in packet_characters)
    words = _parse_observed_words(observed_words, domain)
    observed_text, stream_characters = _stream(words)
    whole_canonical_text = "".join(line.normalized for line in lines)
    canonical_packet_duplicate_count = len(_all_occurrences(whole_canonical_text, packet_text))

    exact_candidates: list[dict[str, Any]] = []
    exact_target_anchors = _all_occurrences(observed_text, target_text) if target_is_contiguous else ()
    target_start_packet_index = target_characters[0].packet_index
    target_end_packet_index = target_characters[-1].packet_index + 1
    left_canonical = packet_characters[:target_start_packet_index]
    right_canonical = packet_characters[target_end_packet_index:]
    search_budget = _new_bounded_search_budget() if target_alignment_policy == BOUNDED_TARGET_POLICY else None
    resource_limit: str | None = None
    rescued_candidates: list[dict[str, Any]] = []
    try:
        for anchor_start in exact_target_anchors:
            if search_budget is not None:
                search_budget.consume_search_span()
            anchor_end = anchor_start + len(target_characters)
            left_operations, left_context = _align_context_side(
                left_canonical,
                anchor_start=anchor_start,
                anchor_end=anchor_end,
                stream_characters=stream_characters,
                side="left",
                budget=search_budget,
            )
            right_operations, right_context = _align_context_side(
                right_canonical,
                anchor_start=anchor_start,
                anchor_end=anchor_end,
                stream_characters=stream_characters,
                side="right",
                budget=search_budget,
            )
            target_operations = tuple(
                _EditOperation("equal", ref, anchor_start + offset)
                for offset, ref in enumerate(target_characters)
            )
            combined = (*left_operations, *target_operations, *right_operations)
            context_metrics = _edit_summary((*left_operations, *right_operations))
            left_context["trace"] = [
                _trace_operation_payload(operation, stream_characters, words) for operation in left_operations
            ]
            right_context["trace"] = [
                _trace_operation_payload(operation, stream_characters, words) for operation in right_operations
            ]
            side_contexts = [item for item in (left_context, right_context) if item["applicable"]]
            sufficient = bool(side_contexts) and all(
                float(item["matched_character_ratio"]) >= SOURCE_PACKET_CONTEXT_MIN_MATCH_RATIO
                and int(item["edit_run_count"]) <= 1
                for item in side_contexts
            )
            exact_candidates.append(
                _candidate_from_trace(
                    packet=packet,
                    packet_characters=packet_characters,
                    target_characters=target_characters,
                    target_ranges=ranges,
                    stream_characters=stream_characters,
                    words=words,
                    operations=combined,
                    context={
                        "mode": "target_anchor_tolerant_neighborhood_monotonic_edit_v1",
                        "target_anchor_exact": True,
                        "target_anchor_observed_character_range": [anchor_start, anchor_end],
                        "left_context": left_context,
                        "right_context": right_context,
                        "context_edit_distance": context_metrics["edit_distance"],
                        "context_edit_runs": context_metrics["edit_run_count"],
                        "context_matched_characters": context_metrics["matched_characters"],
                        "lexical_eligibility": {
                            "minimum_context_matched_character_ratio": SOURCE_PACKET_CONTEXT_MIN_MATCH_RATIO,
                            "calibrated": False,
                            "basis": "fixed_generic_lexical_coverage_and_edit_runs",
                        },
                        "independent_context_support": sufficient,
                    },
                    match_kind=(
                        "exact_full_context"
                        if context_metrics["edit_distance"] == 0
                        else "exact_target_tolerant_context"
                    ),
                    cache_key_sha256=cache_key_sha256,
                    anchor_start=anchor_start,
                )
            )

        exact_context_candidates = [
            candidate for candidate in exact_candidates
            if candidate["context_alignment"]["independent_context_support"]
        ]
        # A bare exact target has no authority to suppress a different source
        # occurrence whose two unchanged neighbours support the bounded edit.
        if (
            target_is_contiguous
            and target_alignment_policy == BOUNDED_TARGET_POLICY
            and not exact_context_candidates
        ):
            assert search_budget is not None
            rescued_candidates = _outer_anchored_target_candidates(
                packet=packet,
                packet_characters=packet_characters,
                target_characters=target_characters,
                target_ranges=ranges,
                stream_characters=stream_characters,
                words=words,
                observed_text=observed_text,
                cache_key_sha256=cache_key_sha256,
                budget=search_budget,
            )
    except _SourcePacketResourceLimit as exc:
        # An unvisited anchor/span could be an equally good or better source
        # occurrence.  Do not return an apparently complete partial search.
        resource_limit = exc.limit
        exact_candidates = []
        rescued_candidates = []
        exact_context_candidates = []
    else:
        exact_context_candidates = [
            candidate for candidate in exact_candidates
            if candidate["context_alignment"]["independent_context_support"]
        ]
    partial_candidates: list[dict[str, Any]] = []
    if resource_limit is None and not exact_candidates and not rescued_candidates:
        try:
            if search_budget is not None:
                search_budget.consume_partial_matcher_cells(
                    (len(target_text) + 1) * (len(observed_text) + 1)
                )
            matcher = SequenceMatcher(a=target_text, b=observed_text, autojunk=False)
            for block in matcher.get_matching_blocks():
                if not block.size:
                    continue
                operations = tuple(
                    _EditOperation("equal", target_characters[block.a + offset], block.b + offset)
                    for offset in range(block.size)
                )
                partial_candidates.append(
                    _candidate_from_trace(
                        packet=packet,
                        packet_characters=packet_characters,
                        target_characters=target_characters,
                        target_ranges=ranges,
                        stream_characters=stream_characters,
                        words=words,
                        operations=operations,
                        context={
                            "mode": "partial_target_observed_matching_block_v1",
                            "target_anchor_exact": False,
                            "target_matching_block": {
                                "canonical_character_range": [block.a, block.a + block.size],
                                "observed_character_range": [block.b, block.b + block.size],
                            },
                            "context_edit_distance": len(target_characters) - block.size,
                            "context_edit_runs": 1,
                            "context_matched_characters": block.size,
                            "independent_context_support": False,
                        },
                        match_kind="partial_target_observed_match",
                        cache_key_sha256=cache_key_sha256,
                        anchor_start=block.b,
                    )
                )
        except _SourcePacketResourceLimit as exc:
            # Bounded policy still attempts normal partial diagnostics when
            # affordable.  For an oversized input pair, its result would be
            # incomplete/operationally unsafe, so withhold it explicitly.
            resource_limit = exc.limit
            partial_candidates = []

    if resource_limit is not None:
        all_candidates: list[dict[str, Any]] = []
    elif rescued_candidates:
        # Keep orphan exact matches in the diagnostic ledger, but a
        # context-qualified rescue comes first and cannot be masked by them.
        all_candidates = [*rescued_candidates, *exact_candidates]
    else:
        all_candidates = exact_candidates or partial_candidates
    # list.sort temporarily empties the list it sorts.  ``all_candidates``
    # aliases ``exact_candidates`` in the non-rescue path, so snapshot the
    # group flags before calculating any sort keys.
    has_exact_candidates = bool(exact_candidates)
    has_rescued_candidates = bool(rescued_candidates)

    def candidate_rank_key(candidate: Mapping[str, Any]) -> tuple[int, tuple[Any, ...]]:
        # New bounded-policy candidates that independently meet the
        # neighbourhood rule must remain visible at n_best=1.  Exact-only
        # policy deliberately retains its historical all-anchor order.
        if has_rescued_candidates:
            priority = int(candidate["match_kind"] != "outer_anchored_bounded_target_edit")
        elif (
            target_alignment_policy == BOUNDED_TARGET_POLICY
            and has_exact_candidates
            and candidate["context_alignment"]["independent_context_support"]
        ):
            priority = 0
        else:
            priority = 1
        return priority, candidate["_sort_key"]

    all_candidates.sort(key=candidate_rank_key)
    reason = "not_covered"
    selected_candidate_id: str | None = None
    selected_candidate: dict[str, Any] | None = None
    if resource_limit is not None:
        reason = "source_packet_resource_limit"
    elif rescued_candidates:
        if canonical_packet_duplicate_count > 1:
            reason = "ambiguous_canonical_packet_identity"
        elif len(rescued_candidates) != 1:
            reason = "ambiguous_outer_anchored_target_edit"
        else:
            selected_candidate = rescued_candidates[0]
            reason = "unique_outer_anchored_bounded_target_edit"
    elif exact_candidates:
        # Under the bounded policy, rank and disambiguate only occurrences
        # that independently satisfy the unchanged-neighbour requirement.
        # Exact-only mode retains its historical all-anchor ordering.
        selection_pool = (
            sorted(exact_context_candidates, key=lambda candidate: candidate["_sort_key"])
            if target_alignment_policy == BOUNDED_TARGET_POLICY
            else exact_candidates
        )
        best = selection_pool[0] if selection_pool else None
        runner_up = selection_pool[1] if len(selection_pool) > 1 else None
        if canonical_packet_duplicate_count > 1:
            reason = "ambiguous_canonical_packet_identity"
        elif best is None or not best["context_alignment"]["independent_context_support"]:
            reason = "partial_context"
        elif runner_up is not None and (
            runner_up["context_alignment"]["context_edit_distance"]
            - best["context_alignment"]["context_edit_distance"]
            < 2
        ):
            # A one-edit lead is too weak to prove that two repeated anchors
            # are different source occurrences.  Keep both in the ledger.
            reason = "ambiguous_context_match"
        else:
            selected_candidate = best
            reason = (
                "unique_complete_context_match"
                if best["context_alignment"]["context_edit_distance"] == 0
                else "unique_monotonic_context_support"
            )
    elif partial_candidates:
        reason = "partial_context"

    returned_candidates = all_candidates[:n_best]
    for rank, candidate in enumerate(returned_candidates, start=1):
        candidate["rank"] = rank
        candidate["candidate_id"] = _sha(
            f"{cache_key_sha256}:{candidate['match_kind']}:{candidate['_anchor_start']}:"
            f"{candidate['source_interval_ms']!r}:{rank}"
        )
        if candidate is selected_candidate:
            candidate["full_context_disambiguated"] = True
            candidate["context_disambiguation_status"] = (
                reason if rescued_candidates else "unique_monotonic_context_support"
            )
            selected_candidate_id = candidate["candidate_id"]
        elif reason in {"ambiguous_context_match", "ambiguous_canonical_packet_identity"}:
            candidate["context_disambiguation_status"] = reason
        else:
            candidate["context_disambiguation_status"] = reason
        candidate.pop("_anchor_start", None)
        candidate.pop("_sort_key", None)
    identity = _packet_identity(
        packet=packet,
        target_ranges=ranges,
        source_audio_sha256=source_audio_sha256,
        lyric_version=lyric_version,
        source_search_domain=domain,
        model_id=model_id,
        normalization_id=normalization_id,
        target_alignment_policy=target_alignment_policy,
    )
    return {
        "schema_version": identity["schema_version"],
        "policy_id": identity["policy_id"],
        "authority": SOURCE_PACKET_AUTHORITY,
        "automatic_production_mutation_allowed": False,
        "forced_phoneme_alignment_performed": False,
        "cache_key_sha256": cache_key_sha256,
        "cache_identity": identity,
        "packet": [
            {
                "canonical_line_index": line.index,
                "canonical_text_sha256": _sha(line.text),
                "normalized_text_sha256": _sha(line.normalized),
            }
            for line in packet
        ],
        "target_cue": {
            "cue_id": target_cue.get("cue_id"),
            "canonical_character_ranges": [dict(item) for item in ranges],
        },
        "coverage": {
            "source_search_domain": dict(domain),
            "observed_word_count_after_domain_filter": len(words),
            "observed_untimed_word_count": sum(
                word.start_ms is None or word.end_ms is None for word in words
            ),
            "observed_zero_duration_word_count": sum(
                word.start_ms is not None and word.start_ms == word.end_ms for word in words
            ),
            "exact_full_context_match_count": sum(
                item["match_kind"] == "exact_full_context" for item in exact_candidates
            ),
            "target_text_match_count": len(exact_target_anchors),
            "canonical_packet_duplicate_count": canonical_packet_duplicate_count,
            "target_domain_is_contiguous": target_is_contiguous,
            "candidate_count_before_truncation": len(all_candidates),
            **({"outer_anchored_target_candidate_count": len(rescued_candidates)}
               if target_alignment_policy == BOUNDED_TARGET_POLICY else {}),
            **(
                {
                    "bounded_target_search": {
                        "status": "resource_limited" if resource_limit is not None else "complete",
                        "resource_limit": resource_limit,
                        "partial_target_matching_skipped_due_to_resource_limit": resource_limit is not None,
                        **(search_budget.coverage() if search_budget is not None else {}),
                    },
                }
                if target_alignment_policy == BOUNDED_TARGET_POLICY
                else {}
            ),
        },
        "selected_candidate_id": selected_candidate_id,
        "selection_reason": reason,
        "candidates_truncated": len(all_candidates) > len(returned_candidates),
        "candidates": returned_candidates,
    }


__all__ = [
    "EXACT_TARGET_POLICY",
    "BOUNDED_TARGET_POLICY",
    "SOURCE_PACKET_BOUNDED_POLICY_ID",
    "SOURCE_PACKET_AUTHORITY",
    "SOURCE_PACKET_CONTEXT_MIN_MATCH_RATIO",
    "SOURCE_PACKET_NORMALIZATION_ID",
    "SOURCE_PACKET_POLICY_ID",
    "SOURCE_PACKET_SCHEMA_VERSION",
    "SourcePacketError",
    "build_source_packet_candidates",
    "source_packet_cache_key",
]
