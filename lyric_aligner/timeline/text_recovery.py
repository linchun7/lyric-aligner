"""Recover low-similarity Smart text reviews from already-ready timing evidence.

This layer is intentionally narrow. Text Repair V2 remains the primary text
matcher and its similarity thresholds are unchanged. When V2 cannot trust the
editor text because ASR is badly wrong, Smart may resolve an *interior* review
block only when the immediately adjacent cues are validated canonical anchors
and an already-ready affine song model independently confirms the canonical
timing inside it.

The recovery never creates timing anchors and never changes SRT timing. It only
replaces text with canonical lyric text when canonical order + bilateral text
anchors + affine timing all agree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Sequence

from lyric_aligner.text_repair import MatchDecision, SubtitleCue
from lyric_aligner.timeline.anchor_repair import (
    SongTimingModel,
    TimedCanonicalOccurrence,
    _cue_times,
)


@dataclass(frozen=True)
class TextTimingRecoverySummary:
    resolved_cue_count: int = 0
    resolved_block_count: int = 0


def _anchor_span(decision: MatchDecision | None) -> tuple[int, int] | None:
    if decision is None or decision.action == "review":
        return None
    if decision.score < 0.92 or decision.canonical_span is None:
        return None
    start, end = decision.canonical_span
    if end - start != 1:
        return None
    return int(start), int(end)


def _review_blocks(
    cues: Sequence[SubtitleCue],
    decisions_by_cue: dict[int, MatchDecision],
) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    for cue in cues:
        decision = decisions_by_cue.get(cue.ordinal)
        is_review = decision is not None and decision.action == "review"
        if is_review and start is None:
            start = cue.ordinal
        elif not is_review and start is not None:
            blocks.append((start, cue.ordinal))
            start = None
    if start is not None:
        blocks.append((start, len(cues)))
    return blocks


def _adjacent_anchor_before(
    start: int,
    decisions_by_cue: dict[int, MatchDecision],
) -> tuple[int, MatchDecision, tuple[int, int]] | None:
    ordinal = start - 1
    if ordinal < 0:
        return None
    decision = decisions_by_cue.get(ordinal)
    span = _anchor_span(decision)
    if decision is None or span is None:
        return None
    return ordinal, decision, span


def _adjacent_anchor_after(
    end: int,
    cues: Sequence[SubtitleCue],
    decisions_by_cue: dict[int, MatchDecision],
) -> tuple[int, MatchDecision, tuple[int, int]] | None:
    ordinal = end
    if ordinal >= len(cues):
        return None
    decision = decisions_by_cue.get(ordinal)
    span = _anchor_span(decision)
    if decision is None or span is None:
        return None
    return ordinal, decision, span


def _model_index(models: Sequence[SongTimingModel]) -> dict[int, SongTimingModel]:
    return {
        item.source_ordinal: item
        for item in models
        if item.status == "ready"
        and math.isfinite(float(item.rate))
        and 0.5 <= float(item.rate) <= 2.0
    }


def _anchor_matches_model(
    cue: SubtitleCue,
    occurrence: TimedCanonicalOccurrence,
    model: SongTimingModel,
    *,
    tolerance_ms: int,
) -> bool:
    cue_start, _ = _cue_times(cue)
    predicted = model.source_to_mix_ms(occurrence.anchor_time_ms)
    return abs(cue_start - predicted) <= tolerance_ms


def _partition_gap_by_cue_starts(
    block_cues: Sequence[SubtitleCue],
    gap: Sequence[TimedCanonicalOccurrence],
    right_anchor_start_ms: int,
    model: SongTimingModel,
    *,
    start_tolerance_ms: int,
    boundary_guard_ms: int,
    max_lines_per_cue: int,
) -> list[list[TimedCanonicalOccurrence]] | None:
    if not block_cues or not gap:
        return None
    if len(gap) < len(block_cues) or len(gap) > len(block_cues) * max_lines_per_cue:
        return None

    cue_starts = [_cue_times(cue)[0] for cue in block_cues]
    if any(right <= left for left, right in zip(cue_starts, cue_starts[1:])):
        return None
    if right_anchor_start_ms <= cue_starts[-1]:
        return None

    predicted = [model.source_to_mix_ms(item.anchor_time_ms) for item in gap]
    if any(right <= left for left, right in zip(predicted, predicted[1:])):
        return None

    result: list[list[TimedCanonicalOccurrence]] = []
    line_index = 0
    for cue_index, cue in enumerate(block_cues):
        if line_index >= len(gap):
            return None
        cue_start = cue_starts[cue_index]
        first_predicted = predicted[line_index]
        if abs(cue_start - first_predicted) > start_tolerance_ms:
            return None

        next_boundary = (
            cue_starts[cue_index + 1]
            if cue_index + 1 < len(block_cues)
            else right_anchor_start_ms
        )
        assigned = [gap[line_index]]
        line_index += 1

        while line_index < len(gap) and len(assigned) < max_lines_per_cue:
            remaining_lines = len(gap) - line_index
            remaining_cues = len(block_cues) - cue_index - 1
            if remaining_lines <= remaining_cues:
                break
            if predicted[line_index] >= next_boundary - boundary_guard_ms:
                break
            assigned.append(gap[line_index])
            line_index += 1
        result.append(assigned)

    if line_index != len(gap):
        return None
    return result


def recover_text_reviews_from_timing(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    decisions: Sequence[MatchDecision],
    models: Sequence[SongTimingModel],
    *,
    anchor_tolerance_ms: int = 750,
    start_tolerance_ms: int = 750,
    boundary_guard_ms: int = 250,
    max_lines_per_cue: int = 4,
    max_block_cues: int = 8,
) -> tuple[dict[int, str], list[MatchDecision], TextTimingRecoverySummary]:
    """Resolve bounded interior text reviews using canonical order + timing.

    A block is recoverable only when:
    - every cue in the block is already a Text Repair review;
    - the immediately adjacent cues on both sides are validated single-line text
      anchors (the recovery may not skip weaker/non-anchor cues to borrow a more
      distant anchor);
    - both anchors belong to the same canonical source/song;
    - that song has an already-ready affine timing model built without the
      review block;
    - the canonical lines strictly between the anchors can be partitioned onto
      the review cue starts with bounded timing residuals.

    This deliberately does not recover edge blocks, unmatched ad-libs, blocks
    crossing songs, or blocks whose timing/model evidence is weak.
    """

    if anchor_tolerance_ms < 0 or start_tolerance_ms < 0 or boundary_guard_ms < 0:
        raise ValueError("text timing recovery tolerances must be >= 0")
    if max_lines_per_cue < 1 or max_block_cues < 1:
        raise ValueError("text timing recovery bounds must be >= 1")

    canonical_by_ordinal = {item.ordinal: item for item in canonical}
    decisions_by_cue = {item.cue_ordinal: item for item in decisions}
    ready_models = _model_index(models)
    replacements: dict[int, str] = {}
    resolved: dict[int, MatchDecision] = {}
    resolved_blocks = 0

    for block_start, block_end in _review_blocks(cues, decisions_by_cue):
        if block_end - block_start > max_block_cues:
            continue
        left = _adjacent_anchor_before(block_start, decisions_by_cue)
        right = _adjacent_anchor_after(block_end, cues, decisions_by_cue)
        if left is None or right is None:
            continue
        left_ordinal, _, left_span = left
        right_ordinal, _, right_span = right
        if right_span[0] <= left_span[1]:
            continue

        left_occurrence = canonical_by_ordinal.get(left_span[0])
        right_occurrence = canonical_by_ordinal.get(right_span[0])
        if left_occurrence is None or right_occurrence is None:
            continue
        if left_occurrence.source_ordinal != right_occurrence.source_ordinal:
            continue
        source_ordinal = left_occurrence.source_ordinal
        model = ready_models.get(source_ordinal)
        if model is None:
            continue
        if not _anchor_matches_model(
            cues[left_ordinal],
            left_occurrence,
            model,
            tolerance_ms=anchor_tolerance_ms,
        ):
            continue
        if not _anchor_matches_model(
            cues[right_ordinal],
            right_occurrence,
            model,
            tolerance_ms=anchor_tolerance_ms,
        ):
            continue

        gap = [
            canonical_by_ordinal[ordinal]
            for ordinal in range(left_span[1], right_span[0])
            if ordinal in canonical_by_ordinal
        ]
        if not gap or any(item.source_ordinal != source_ordinal for item in gap):
            continue

        block_cues = list(cues[block_start:block_end])
        right_anchor_start_ms, _ = _cue_times(cues[right_ordinal])
        partition = _partition_gap_by_cue_starts(
            block_cues,
            gap,
            right_anchor_start_ms,
            model,
            start_tolerance_ms=start_tolerance_ms,
            boundary_guard_ms=boundary_guard_ms,
            max_lines_per_cue=max_lines_per_cue,
        )
        if partition is None:
            continue

        pending: dict[int, tuple[str, MatchDecision]] = {}
        for cue, assigned in zip(block_cues, partition):
            original = decisions_by_cue.get(cue.ordinal)
            if original is None or original.action != "review":
                pending = {}
                break
            target = (
                assigned[0].text
                if len(assigned) == 1
                else " ".join(item.text for item in assigned)
            )
            action = "unchanged" if target == cue.text else "replace"
            pending[cue.ordinal] = (
                target,
                replace(
                    original,
                    canonical_ordinal=assigned[0].ordinal,
                    action=action,
                    reason="timing_model_confirms_canonical_sequence",
                    cue_span=(cue.ordinal, cue.ordinal + 1),
                    canonical_span=(assigned[0].ordinal, assigned[-1].ordinal + 1),
                    canonical_text=target,
                    output_text=target,
                    edit_operations=(),
                ),
            )
        if not pending:
            continue

        resolved_blocks += 1
        for cue_ordinal, (target, decision) in pending.items():
            if target != cues[cue_ordinal].text:
                replacements[cue_ordinal] = target
            resolved[cue_ordinal] = decision

    output = [resolved.get(item.cue_ordinal, item) for item in decisions]
    output.sort(key=lambda item: item.cue_ordinal)
    return (
        replacements,
        output,
        TextTimingRecoverySummary(
            resolved_cue_count=len(resolved),
            resolved_block_count=resolved_blocks,
        ),
    )
