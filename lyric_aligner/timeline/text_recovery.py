"""Recover low-similarity Smart text reviews from already-ready timing evidence.

Text Repair V2 remains the primary text matcher and its similarity thresholds are
unchanged. Smart may revisit only text decisions that V2 deliberately left for
review. Interior recovery uses bilateral canonical anchors. Song-edge recovery
is stricter: it is limited to the first/last few canonical lyric occurrences,
requires an independently-ready affine model plus a consecutive one-sided chain
of validated text anchors, and may leave unmatched editor ad-libs untouched.

The recovery never creates timing anchors and never changes SRT timing. It only
replaces text when canonical order and independent timing evidence agree.
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
    resolved_edge_cue_count: int = 0
    resolved_edge_block_count: int = 0


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


def _canonical_rows_by_source(
    canonical: Sequence[TimedCanonicalOccurrence],
) -> dict[int, list[TimedCanonicalOccurrence]]:
    output: dict[int, list[TimedCanonicalOccurrence]] = {}
    for item in canonical:
        output.setdefault(item.source_ordinal, []).append(item)
    for rows in output.values():
        rows.sort(key=lambda item: item.ordinal)
    return output


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


def _transparent_unmapped_review(decision: MatchDecision | None) -> bool:
    """Return True only for review cues with no canonical claim at all.

    This is the narrow exception used for editor-only ad-libs near a song edge.
    A weak mapped cue is not transparent and blocks one-sided recovery.
    """

    return (
        decision is not None
        and decision.action == "review"
        and decision.canonical_ordinal is None
        and decision.canonical_span is None
    )


def _validated_anchor_chain_after(
    start: int,
    cues: Sequence[SubtitleCue],
    decisions_by_cue: dict[int, MatchDecision],
    canonical_by_ordinal: dict[int, TimedCanonicalOccurrence],
    source_position: dict[int, int],
    *,
    source_ordinal: int,
    model: SongTimingModel,
    tolerance_ms: int,
    minimum_count: int,
) -> bool:
    previous_position: int | None = None
    count = 0
    ordinal = start
    while ordinal < len(cues) and count < minimum_count:
        decision = decisions_by_cue.get(ordinal)
        span = _anchor_span(decision)
        if span is None:
            return False
        occurrence = canonical_by_ordinal.get(span[0])
        if occurrence is None or occurrence.source_ordinal != source_ordinal:
            return False
        position = source_position.get(occurrence.ordinal)
        if position is None:
            return False
        if previous_position is not None and position != previous_position + 1:
            return False
        if not _anchor_matches_model(
            cues[ordinal],
            occurrence,
            model,
            tolerance_ms=tolerance_ms,
        ):
            return False
        previous_position = position
        count += 1
        ordinal += 1
    return count >= minimum_count


def _validated_anchor_chain_before(
    end: int,
    cues: Sequence[SubtitleCue],
    decisions_by_cue: dict[int, MatchDecision],
    canonical_by_ordinal: dict[int, TimedCanonicalOccurrence],
    source_position: dict[int, int],
    *,
    source_ordinal: int,
    model: SongTimingModel,
    tolerance_ms: int,
    minimum_count: int,
) -> bool:
    previous_position: int | None = None
    count = 0
    ordinal = end - 1
    while ordinal >= 0 and count < minimum_count:
        decision = decisions_by_cue.get(ordinal)
        span = _anchor_span(decision)
        if span is None:
            return False
        occurrence = canonical_by_ordinal.get(span[0])
        if occurrence is None or occurrence.source_ordinal != source_ordinal:
            return False
        position = source_position.get(occurrence.ordinal)
        if position is None:
            return False
        if previous_position is not None and position != previous_position - 1:
            return False
        if not _anchor_matches_model(
            cues[ordinal],
            occurrence,
            model,
            tolerance_ms=tolerance_ms,
        ):
            return False
        previous_position = position
        count += 1
        ordinal -= 1
    return count >= minimum_count


def _edge_replacement_decision(
    cue: SubtitleCue,
    original: MatchDecision,
    occurrence: TimedCanonicalOccurrence,
) -> MatchDecision:
    target = occurrence.text
    return replace(
        original,
        canonical_ordinal=occurrence.ordinal,
        action="unchanged" if target == cue.text else "replace",
        reason="timing_model_confirms_song_edge_canonical",
        cue_span=(cue.ordinal, cue.ordinal + 1),
        canonical_span=(occurrence.ordinal, occurrence.ordinal + 1),
        canonical_text=target,
        output_text=target,
        edit_operations=(),
    )


def _recover_song_start_edge(
    block_start: int,
    block_end: int,
    cues: Sequence[SubtitleCue],
    decisions_by_cue: dict[int, MatchDecision],
    canonical_by_ordinal: dict[int, TimedCanonicalOccurrence],
    rows_by_source: dict[int, list[TimedCanonicalOccurrence]],
    ready_models: dict[int, SongTimingModel],
    *,
    anchor_tolerance_ms: int,
    edge_start_tolerance_ms: int,
    minimum_one_sided_anchors: int,
    edge_line_limit: int,
    max_unmapped_adlib_skips: int,
) -> dict[int, MatchDecision]:
    right = _adjacent_anchor_after(block_end, cues, decisions_by_cue)
    if right is None:
        return {}
    _, _, right_span = right
    right_occurrence = canonical_by_ordinal.get(right_span[0])
    if right_occurrence is None:
        return {}
    source_ordinal = right_occurrence.source_ordinal
    model = ready_models.get(source_ordinal)
    source_rows = rows_by_source.get(source_ordinal, [])
    if model is None or not source_rows:
        return {}
    source_position = {item.ordinal: index for index, item in enumerate(source_rows)}
    right_position = source_position.get(right_occurrence.ordinal)
    if right_position is None or right_position <= 0 or right_position > edge_line_limit:
        return {}
    if not _validated_anchor_chain_after(
        block_end,
        cues,
        decisions_by_cue,
        canonical_by_ordinal,
        source_position,
        source_ordinal=source_ordinal,
        model=model,
        tolerance_ms=anchor_tolerance_ms,
        minimum_count=minimum_one_sided_anchors,
    ):
        return {}

    pending: dict[int, MatchDecision] = {}
    candidate_position = right_position - 1
    skipped = 0
    for cue_ordinal in range(block_end - 1, block_start - 1, -1):
        if candidate_position < 0:
            break
        cue = cues[cue_ordinal]
        original = decisions_by_cue.get(cue_ordinal)
        if original is None or original.action != "review":
            break
        candidate = source_rows[candidate_position]
        cue_start, _ = _cue_times(cue)
        predicted = model.source_to_mix_ms(candidate.anchor_time_ms)
        if abs(cue_start - predicted) <= edge_start_tolerance_ms:
            pending[cue_ordinal] = _edge_replacement_decision(
                cue,
                original,
                candidate,
            )
            candidate_position -= 1
            skipped = 0
            continue
        if _transparent_unmapped_review(original) and skipped < max_unmapped_adlib_skips:
            skipped += 1
            continue
        break

    return pending


def _recover_song_end_edge(
    block_start: int,
    block_end: int,
    cues: Sequence[SubtitleCue],
    decisions_by_cue: dict[int, MatchDecision],
    canonical_by_ordinal: dict[int, TimedCanonicalOccurrence],
    rows_by_source: dict[int, list[TimedCanonicalOccurrence]],
    ready_models: dict[int, SongTimingModel],
    *,
    anchor_tolerance_ms: int,
    edge_start_tolerance_ms: int,
    minimum_one_sided_anchors: int,
    edge_line_limit: int,
    max_unmapped_adlib_skips: int,
) -> dict[int, MatchDecision]:
    left = _adjacent_anchor_before(block_start, decisions_by_cue)
    if left is None:
        return {}
    _, _, left_span = left
    left_occurrence = canonical_by_ordinal.get(left_span[0])
    if left_occurrence is None:
        return {}
    source_ordinal = left_occurrence.source_ordinal
    model = ready_models.get(source_ordinal)
    source_rows = rows_by_source.get(source_ordinal, [])
    if model is None or not source_rows:
        return {}
    source_position = {item.ordinal: index for index, item in enumerate(source_rows)}
    left_position = source_position.get(left_occurrence.ordinal)
    if (
        left_position is None
        or left_position >= len(source_rows) - 1
        or len(source_rows) - 1 - left_position > edge_line_limit
    ):
        return {}
    if not _validated_anchor_chain_before(
        block_start,
        cues,
        decisions_by_cue,
        canonical_by_ordinal,
        source_position,
        source_ordinal=source_ordinal,
        model=model,
        tolerance_ms=anchor_tolerance_ms,
        minimum_count=minimum_one_sided_anchors,
    ):
        return {}

    pending: dict[int, MatchDecision] = {}
    candidate_position = left_position + 1
    skipped = 0
    for cue_ordinal in range(block_start, block_end):
        if candidate_position >= len(source_rows):
            break
        cue = cues[cue_ordinal]
        original = decisions_by_cue.get(cue_ordinal)
        if original is None or original.action != "review":
            break
        candidate = source_rows[candidate_position]
        cue_start, _ = _cue_times(cue)
        predicted = model.source_to_mix_ms(candidate.anchor_time_ms)
        if abs(cue_start - predicted) <= edge_start_tolerance_ms:
            pending[cue_ordinal] = _edge_replacement_decision(
                cue,
                original,
                candidate,
            )
            candidate_position += 1
            skipped = 0
            continue
        if _transparent_unmapped_review(original) and skipped < max_unmapped_adlib_skips:
            skipped += 1
            continue
        break

    return pending


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
    edge_start_tolerance_ms: int = 500,
    minimum_one_sided_anchors: int = 2,
    edge_line_limit: int = 4,
    max_unmapped_adlib_skips: int = 3,
) -> tuple[dict[int, str], list[MatchDecision], TextTimingRecoverySummary]:
    """Resolve bounded text reviews using independent canonical timing evidence.

    Interior recovery keeps the v1.1.2 bilateral contract. A second, stricter
    song-edge pass may recover the first/last few canonical lines with only one
    side of text anchors, but only when:
    - the song model was already ready without the reviewed cue;
    - the neighbouring side contains a consecutive chain of validated anchors;
    - the candidate is within the first/last ``edge_line_limit`` canonical rows;
    - candidate onset agrees with the editor cue start within the tighter edge
      tolerance;
    - any skipped review cue is truly unmapped (no weak canonical claim), so an
      editor-only ad-lib may stay untouched but a weak mapped cue blocks the pass.

    Recovered text never becomes an A timing anchor and never changes cue timing.
    """

    if (
        anchor_tolerance_ms < 0
        or start_tolerance_ms < 0
        or boundary_guard_ms < 0
        or edge_start_tolerance_ms < 0
    ):
        raise ValueError("text timing recovery tolerances must be >= 0")
    if (
        max_lines_per_cue < 1
        or max_block_cues < 1
        or minimum_one_sided_anchors < 2
        or edge_line_limit < 1
        or max_unmapped_adlib_skips < 0
    ):
        raise ValueError("text timing recovery bounds are invalid")

    canonical_by_ordinal = {item.ordinal: item for item in canonical}
    rows_by_source = _canonical_rows_by_source(canonical)
    decisions_by_cue = {item.cue_ordinal: item for item in decisions}
    ready_models = _model_index(models)
    replacements: dict[int, str] = {}
    resolved: dict[int, MatchDecision] = {}
    resolved_blocks = 0
    resolved_edge_cues: set[int] = set()
    resolved_edge_blocks = 0

    # Strongest path first: bilateral interior recovery.
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

    # Recompute review blocks after bilateral recovery. This keeps edge recovery
    # from reprocessing text that the stronger two-sided path already resolved.
    interim_decisions = [resolved.get(item.cue_ordinal, item) for item in decisions]
    interim_decisions.sort(key=lambda item: item.cue_ordinal)
    interim_by_cue = {item.cue_ordinal: item for item in interim_decisions}

    for block_start, block_end in _review_blocks(cues, interim_by_cue):
        if block_end - block_start > max_block_cues:
            continue
        pending = _recover_song_start_edge(
            block_start,
            block_end,
            cues,
            interim_by_cue,
            canonical_by_ordinal,
            rows_by_source,
            ready_models,
            anchor_tolerance_ms=anchor_tolerance_ms,
            edge_start_tolerance_ms=edge_start_tolerance_ms,
            minimum_one_sided_anchors=minimum_one_sided_anchors,
            edge_line_limit=edge_line_limit,
            max_unmapped_adlib_skips=max_unmapped_adlib_skips,
        )
        if not pending:
            pending = _recover_song_end_edge(
                block_start,
                block_end,
                cues,
                interim_by_cue,
                canonical_by_ordinal,
                rows_by_source,
                ready_models,
                anchor_tolerance_ms=anchor_tolerance_ms,
                edge_start_tolerance_ms=edge_start_tolerance_ms,
                minimum_one_sided_anchors=minimum_one_sided_anchors,
                edge_line_limit=edge_line_limit,
                max_unmapped_adlib_skips=max_unmapped_adlib_skips,
            )
        if not pending:
            continue

        resolved_edge_blocks += 1
        resolved_blocks += 1
        for cue_ordinal, decision in pending.items():
            target = decision.output_text
            if target != cues[cue_ordinal].text:
                replacements[cue_ordinal] = target
            resolved[cue_ordinal] = decision
            resolved_edge_cues.add(cue_ordinal)

    output = [resolved.get(item.cue_ordinal, item) for item in decisions]
    output.sort(key=lambda item: item.cue_ordinal)
    return (
        replacements,
        output,
        TextTimingRecoverySummary(
            resolved_cue_count=len(resolved),
            resolved_block_count=resolved_blocks,
            resolved_edge_cue_count=len(resolved_edge_cues),
            resolved_edge_block_count=resolved_edge_blocks,
        ),
    )
