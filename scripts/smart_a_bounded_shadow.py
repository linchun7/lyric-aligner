"""Generic Smart v1.2.5 A-bounded text-recovery shadow.

This module is intentionally NOT wired into the production Smart pipeline.
It exists so the candidate policy can be exercised in public synthetic tests
and private clean reruns before any production policy-id or behavior change.

The shadow is text-only.  It may resolve a consecutive mapped-review region
only when local canonical boundaries are already resolved and a farther pair
of same-source A-grade timing anchors brackets the region.  Those A anchors
must come from an already-ready timing model and remain within the established
750 ms residual envelope.  Recovered decisions are capped below B timing
authority and never mutate cue count, numbering, or timing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from lyric_aligner.text_repair import (
    MatchDecision,
    SubtitleCue,
    _assign_targets,
    _normalize_for_match,
    _pair_score,
    _render_preserving_layout,
)
from lyric_aligner.timeline.anchor_repair import (
    TimedCanonicalOccurrence,
    TimingDecision,
)
from lyric_aligner.timeline.bpm_sequence_reconcile import (
    _LATIN_TOKEN_RE,
    _is_pure_vocalization,
)

_MIN_REGION_SIMILARITY = 0.80
_MIN_REGION_LENGTH_RATIO = 0.85
_MIN_REGION_NORMALIZED_CHARS = 12
_A_RESIDUAL_LIMIT_MS = 750.0
_RECOVERED_SCORE_CAP = 0.89
_REASON = "a_bounded_region_confirms_canonical_stream"


@dataclass(frozen=True)
class ABoundedShadowSummary:
    resolved_review_cue_count: int = 0
    resolved_region_count: int = 0
    materialized_text_change_count: int = 0


def _mapped_span(decision: MatchDecision | None) -> tuple[int, int] | None:
    if decision is None or decision.canonical_span is None:
        return None
    start, end = (int(decision.canonical_span[0]), int(decision.canonical_span[1]))
    if end <= start:
        return None
    return start, end


def _review_blocks(
    cues: Sequence[SubtitleCue],
    decisions_by_cue: Mapping[int, MatchDecision],
) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    for cue in cues:
        decision = decisions_by_cue.get(cue.ordinal)
        reviewing = decision is not None and decision.action == "review"
        if reviewing and start is None:
            start = cue.ordinal
        elif not reviewing and start is not None:
            blocks.append((start, cue.ordinal))
            start = None
    if start is not None:
        blocks.append((start, len(cues)))
    return blocks


def _canonical_rows(
    canonical_by_ordinal: Mapping[int, TimedCanonicalOccurrence],
    start: int,
    end: int,
) -> list[TimedCanonicalOccurrence] | None:
    rows: list[TimedCanonicalOccurrence] = []
    for ordinal in range(start, end):
        row = canonical_by_ordinal.get(ordinal)
        if row is None:
            return None
        rows.append(row)
    return rows


def _valid_a_anchor(
    item: TimingDecision,
    canonical_by_ordinal: Mapping[int, TimedCanonicalOccurrence],
    *,
    source_ordinal: int,
) -> bool:
    if (
        item.anchor_grade != "A"
        or item.model_status != "ready"
        or item.source_ordinal != source_ordinal
        or item.canonical_ordinal is None
        or item.residual_ms is None
    ):
        return False
    residual = float(item.residual_ms)
    if not math.isfinite(residual) or abs(residual) > _A_RESIDUAL_LIMIT_MS:
        return False
    occurrence = canonical_by_ordinal.get(int(item.canonical_ordinal))
    return occurrence is not None and occurrence.source_ordinal == source_ordinal


def _bracketing_a_anchors(
    timing: Sequence[TimingDecision],
    canonical_by_ordinal: Mapping[int, TimedCanonicalOccurrence],
    *,
    block_start: int,
    block_end: int,
    gap_start: int,
    gap_end: int,
    source_ordinal: int,
) -> tuple[TimingDecision, TimingDecision] | None:
    eligible = [
        item
        for item in timing
        if _valid_a_anchor(
            item,
            canonical_by_ordinal,
            source_ordinal=source_ordinal,
        )
    ]
    left = [
        item
        for item in eligible
        if item.cue_ordinal < block_start
        and int(item.canonical_ordinal) < gap_start
    ]
    right = [
        item
        for item in eligible
        if item.cue_ordinal >= block_end
        and int(item.canonical_ordinal) >= gap_end
    ]
    if not left or not right:
        return None
    return (
        max(left, key=lambda item: item.cue_ordinal),
        min(right, key=lambda item: item.cue_ordinal),
    )


def _stream_canonical_spans(
    output_texts: Sequence[str],
    rows: Sequence[TimedCanonicalOccurrence],
) -> list[tuple[int, int]] | None:
    intervals: list[tuple[int, int, TimedCanonicalOccurrence]] = []
    cursor = 0
    for row in rows:
        length = len(row.normalized)
        if length <= 0:
            return None
        intervals.append((cursor, cursor + length, row))
        cursor += length
    if cursor <= 0:
        return None

    spans: list[tuple[int, int]] = []
    output_cursor = 0
    for value in output_texts:
        length = len(_normalize_for_match(value))
        if length <= 0:
            return None
        start = output_cursor
        end = start + length
        hits = [row for left, right, row in intervals if right > start and left < end]
        if not hits:
            return None
        spans.append((hits[0].ordinal, hits[-1].ordinal + 1))
        output_cursor = end
    if output_cursor != cursor:
        return None
    return spans


def _candidate_for_block(
    block_cues: Sequence[SubtitleCue],
    block_decisions: Sequence[MatchDecision],
    gap: Sequence[TimedCanonicalOccurrence],
) -> tuple[list[str], list[tuple[int, int]]] | None:
    if not block_cues or len(block_cues) != len(block_decisions) or not gap:
        return None
    if any(item.action != "review" or _mapped_span(item) is None for item in block_decisions):
        return None
    if any(_is_pure_vocalization(cue.text) for cue in block_cues):
        return None
    if any(_is_pure_vocalization(row.text) for row in gap):
        return None

    # Multi-cue Latin/mixed text needs token-boundary-aware repartitioning.  The
    # current character-owner path is intentionally CJK-only for this tier.
    if len(block_cues) > 1 and (
        any(_LATIN_TOKEN_RE.search(cue.text) for cue in block_cues)
        or any(_LATIN_TOKEN_RE.search(row.text) for row in gap)
    ):
        return None

    source_texts = [cue.text for cue in block_cues]
    source_norm = "".join(_normalize_for_match(value) for value in source_texts)
    target_text = " ".join(row.text for row in gap)
    target_norm = "".join(row.normalized for row in gap)
    if not source_norm or not target_norm:
        return None
    if min(len(source_norm), len(target_norm)) < _MIN_REGION_NORMALIZED_CHARS:
        return None

    length_ratio = min(len(source_norm), len(target_norm)) / max(
        len(source_norm), len(target_norm)
    )
    if length_ratio < _MIN_REGION_LENGTH_RATIO:
        return None
    if _pair_score(source_norm, target_norm) < _MIN_REGION_SIMILARITY:
        return None

    assigned, insertion_reason = _assign_targets(source_texts, target_text)
    if insertion_reason is not None or len(assigned) != len(block_cues):
        return None

    rendered: list[str] = []
    for cue, content in zip(block_cues, assigned):
        if not _normalize_for_match(content):
            return None
        output, _ = _render_preserving_layout(cue.text, content)
        if not _normalize_for_match(output):
            return None
        if _normalize_for_match(output) != _normalize_for_match(content):
            return None
        rendered.append(output)

    if "".join(_normalize_for_match(value) for value in rendered) != target_norm:
        return None
    spans = _stream_canonical_spans(rendered, gap)
    if spans is None:
        return None
    return rendered, spans


def _recovered_decision(
    cue: SubtitleCue,
    original: MatchDecision,
    text: str,
    span: tuple[int, int],
) -> MatchDecision:
    return replace(
        original,
        canonical_ordinal=span[0],
        score=min(float(original.score), _RECOVERED_SCORE_CAP),
        action="unchanged" if cue.normalized == _normalize_for_match(text) else "replace",
        reason=_REASON,
        cue_span=(cue.ordinal, cue.ordinal + 1),
        canonical_span=span,
        canonical_text=text,
        output_text=text,
        edit_operations=(),
    )


def recover_mapped_reviews_from_a_bounded_shadow(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    decisions: Sequence[MatchDecision],
    timing: Sequence[TimingDecision],
) -> tuple[dict[int, str], list[MatchDecision], ABoundedShadowSummary]:
    """Resolve only regionally-proven mapped reviews; never mutate timing.

    The local resolved neighbours define the exact canonical gap that may be
    consumed.  A farther same-source A/A pair supplies independent timing
    authorization.  No unmatched cue, cross-source gap, pure vocalization,
    multi-cue Latin region, boundary insertion, low-similarity region, or weak
    timing bracket is allowed to auto-resolve.
    """

    canonical_by_ordinal = {item.ordinal: item for item in canonical}
    decisions_by_cue = {item.cue_ordinal: item for item in decisions}
    output = dict(decisions_by_cue)
    replacements: dict[int, str] = {}
    resolved_cues = 0
    resolved_regions = 0
    materialized_changes = 0

    for block_start, block_end in _review_blocks(cues, decisions_by_cue):
        if block_start <= 0 or block_end >= len(cues):
            continue
        left = decisions_by_cue.get(block_start - 1)
        right = decisions_by_cue.get(block_end)
        left_span = _mapped_span(left)
        right_span = _mapped_span(right)
        if (
            left is None
            or right is None
            or left.action == "review"
            or right.action == "review"
            or left_span is None
            or right_span is None
        ):
            continue

        gap_start = left_span[1]
        gap_end = right_span[0]
        if gap_end <= gap_start:
            continue
        gap = _canonical_rows(canonical_by_ordinal, gap_start, gap_end)
        if not gap:
            continue
        source_ordinal = gap[0].source_ordinal
        if any(row.source_ordinal != source_ordinal for row in gap):
            continue

        left_boundary_rows = _canonical_rows(
            canonical_by_ordinal, left_span[0], left_span[1]
        )
        right_boundary_rows = _canonical_rows(
            canonical_by_ordinal, right_span[0], right_span[1]
        )
        if (
            not left_boundary_rows
            or not right_boundary_rows
            or any(row.source_ordinal != source_ordinal for row in left_boundary_rows)
            or any(row.source_ordinal != source_ordinal for row in right_boundary_rows)
        ):
            continue

        block_cues = list(cues[block_start:block_end])
        block_decisions = [decisions_by_cue.get(cue.ordinal) for cue in block_cues]
        if any(item is None for item in block_decisions):
            continue
        typed_decisions = [item for item in block_decisions if item is not None]

        previous_start = gap_start
        previous_end = gap_start
        mapped_ok = True
        for item in typed_decisions:
            span = _mapped_span(item)
            if item.action != "review" or span is None:
                mapped_ok = False
                break
            start, end = span
            if not (gap_start <= start < end <= gap_end):
                mapped_ok = False
                break
            if start < previous_start or end < previous_end:
                mapped_ok = False
                break
            rows = _canonical_rows(canonical_by_ordinal, start, end)
            if not rows or any(row.source_ordinal != source_ordinal for row in rows):
                mapped_ok = False
                break
            previous_start, previous_end = start, end
        if not mapped_ok:
            continue

        anchors = _bracketing_a_anchors(
            timing,
            canonical_by_ordinal,
            block_start=block_start,
            block_end=block_end,
            gap_start=gap_start,
            gap_end=gap_end,
            source_ordinal=source_ordinal,
        )
        if anchors is None:
            continue

        candidate = _candidate_for_block(block_cues, typed_decisions, gap)
        if candidate is None:
            continue
        candidate_texts, candidate_spans = candidate

        changed_here = 0
        for cue, original, candidate_text, span in zip(
            block_cues,
            typed_decisions,
            candidate_texts,
            candidate_spans,
        ):
            decision = _recovered_decision(cue, original, candidate_text, span)
            output[cue.ordinal] = decision
            if candidate_text != cue.text:
                replacements[cue.ordinal] = candidate_text
                changed_here += 1
            resolved_cues += 1
        resolved_regions += 1
        materialized_changes += changed_here

    ordered = [output.get(item.cue_ordinal, item) for item in decisions]
    return (
        replacements,
        ordered,
        ABoundedShadowSummary(
            resolved_review_cue_count=resolved_cues,
            resolved_region_count=resolved_regions,
            materialized_text_change_count=materialized_changes,
        ),
    )
