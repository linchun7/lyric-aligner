"""Text-only Smart recovery from local bilateral and exact-consensus evidence.

This module deliberately does not grant timing authority. It revisits Text
Repair review decisions only after the original editor/canonical alignment is
known, and it builds any affine evidence exclusively from ORIGINAL text-safe
mappings. Recovered text is marked with dedicated reasons so the timing layer
can keep it out of primary A-anchor authority.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Sequence

from lyric_aligner.text_repair import (
    MatchDecision,
    SubtitleCue,
    _assign_targets,
    _pair_score,
)
from lyric_aligner.timeline.anchor_repair import (
    AnchorObservation,
    SongTimingModel,
    TimedCanonicalOccurrence,
    _cue_times,
    _fit_model,
)
from lyric_aligner.timeline.text_recovery import _partition_gap_by_cue_starts


_LOCAL_BOUNDARY_SCORE = 0.80
_LOCAL_GROUPED_SCORE = 0.55
_CONSENSUS_MIN_ANCHORS = 6
_CONSENSUS_MIN_DISTINCT_TEXTS = 3
_CONSENSUS_MIN_INLIER_FRACTION = 0.80
_CONSENSUS_MAX_MEDIAN_RESIDUAL_MS = 150
_CONSENSUS_ONSET_MARGIN_MS = 250
_LEADING_ADLIB_MIN_LEAD_MS = 300
_LEADING_ADLIB_CHARS = frozenset("哟哦啊哎喂耶诶嗯呜嘿哈")


@dataclass(frozen=True)
class TextConsensusRecoverySummary:
    local_bilateral_cue_count: int = 0
    local_bilateral_block_count: int = 0
    local_segmentation_preserve_count: int = 0
    local_timing_partition_count: int = 0
    consensus_timing_cue_count: int = 0
    consensus_model_count: int = 0


def _canonical_by_ordinal(
    canonical: Sequence[TimedCanonicalOccurrence],
) -> dict[int, TimedCanonicalOccurrence]:
    return {item.ordinal: item for item in canonical}


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


def _single_boundary_occurrence(
    decision: MatchDecision | None,
    canonical_by_ordinal: dict[int, TimedCanonicalOccurrence],
    *,
    minimum_score: float,
) -> TimedCanonicalOccurrence | None:
    if (
        decision is None
        or decision.action == "review"
        or decision.score < minimum_score
        or decision.canonical_span is None
        or decision.cue_span is None
    ):
        return None
    canonical_start, canonical_end = decision.canonical_span
    cue_start, cue_end = decision.cue_span
    if canonical_end - canonical_start != 1 or cue_end - cue_start != 1:
        return None
    return canonical_by_ordinal.get(int(canonical_start))


def _build_exact_consensus_models(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    original_decisions: Sequence[MatchDecision],
) -> list[SongTimingModel]:
    """Build robust TEXT-RECOVERY models from original exact 1:1 matches.

    Repeated chorus text is allowed here because this model is never timing
    mutation authority. Monotonic Text Repair identity + a robust affine
    consensus lets repeated exact observations strengthen text recovery while
    remaining isolated from Smart's strict A-anchor model.
    """

    canonical_by_ordinal = _canonical_by_ordinal(canonical)
    observations: dict[int, list[AnchorObservation]] = defaultdict(list)
    distinct_texts: dict[int, set[str]] = defaultdict(set)

    for decision in original_decisions:
        if (
            decision.action != "unchanged"
            or decision.canonical_span is None
            or decision.cue_span is None
        ):
            continue
        canonical_start, canonical_end = decision.canonical_span
        cue_start, cue_end = decision.cue_span
        if canonical_end - canonical_start != 1 or cue_end - cue_start != 1:
            continue
        occurrence = canonical_by_ordinal.get(int(canonical_start))
        if occurrence is None:
            continue
        cue = cues[decision.cue_ordinal]
        if cue.normalized != occurrence.normalized:
            continue
        mix_start_ms, _ = _cue_times(cue)
        observations[occurrence.source_ordinal].append(
            AnchorObservation(
                cue_ordinal=cue.ordinal,
                canonical_ordinal=occurrence.ordinal,
                source_ordinal=occurrence.source_ordinal,
                source=occurrence.source,
                grade="TEXT_CONSENSUS",
                mix_start_ms=mix_start_ms,
                source_time_ms=occurrence.anchor_time_ms,
                score=decision.score,
                has_word_timing=occurrence.has_word_timing,
            )
        )
        distinct_texts[occurrence.source_ordinal].add(occurrence.normalized)

    output: list[SongTimingModel] = []
    for source_ordinal, rows in observations.items():
        if len(rows) < 3 or len(distinct_texts[source_ordinal]) < _CONSENSUS_MIN_DISTINCT_TEXTS:
            continue
        model = _fit_model(
            rows,
            rate_prior=None,
            source_ordinal=source_ordinal,
            source=rows[0].source,
            min_anchors=3,
        )
        if (
            model.status != "ready"
            or model.anchor_count < _CONSENSUS_MIN_ANCHORS
            or model.inlier_fraction < _CONSENSUS_MIN_INLIER_FRACTION
            or model.median_abs_residual_ms > _CONSENSUS_MAX_MEDIAN_RESIDUAL_MS
        ):
            continue
        output.append(replace(model, rate_source="text_exact_consensus_only"))
    output.sort(key=lambda item: item.source_ordinal)
    return output


def _local_bilateral_recovery(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    decisions: Sequence[MatchDecision],
    *,
    max_block_cues: int = 8,
    max_lines_per_cue: int = 4,
) -> tuple[dict[int, str], list[MatchDecision], int, int, int, int]:
    """Recover bounded review blocks from two already-resolved text boundaries.

    First prefer whole-span character ownership projection. That path is what
    preserves a trusted editor segmentation when a single LRC line crosses two
    editor cues. Only if lexical projection is too weak do canonical onsets and
    a local two-boundary affine interpolation partition the canonical gap.
    """

    canonical_by_ordinal = _canonical_by_ordinal(canonical)
    decisions_by_cue = {item.cue_ordinal: item for item in decisions}
    replacements: dict[int, str] = {}
    resolved: dict[int, MatchDecision] = {}
    block_count = 0
    segmentation_count = 0
    timing_partition_count = 0

    for block_start, block_end in _review_blocks(cues, decisions_by_cue):
        if (
            block_start <= 0
            or block_end >= len(cues)
            or block_end - block_start > max_block_cues
        ):
            continue
        left_decision = decisions_by_cue.get(block_start - 1)
        right_decision = decisions_by_cue.get(block_end)
        left = _single_boundary_occurrence(
            left_decision,
            canonical_by_ordinal,
            minimum_score=_LOCAL_BOUNDARY_SCORE,
        )
        right = _single_boundary_occurrence(
            right_decision,
            canonical_by_ordinal,
            minimum_score=_LOCAL_BOUNDARY_SCORE,
        )
        if (
            left is None
            or right is None
            or left.source_ordinal != right.source_ordinal
            or right.ordinal <= left.ordinal + 1
        ):
            continue

        gap = [
            canonical_by_ordinal[ordinal]
            for ordinal in range(left.ordinal + 1, right.ordinal)
            if ordinal in canonical_by_ordinal
        ]
        if (
            not gap
            or len(gap) > (block_end - block_start) * max_lines_per_cue
            or any(item.source_ordinal != left.source_ordinal for item in gap)
        ):
            continue

        left_mix_start, _ = _cue_times(cues[block_start - 1])
        right_mix_start, _ = _cue_times(cues[block_end])
        mix_delta = right_mix_start - left_mix_start
        source_delta = right.anchor_time_ms - left.anchor_time_ms
        if mix_delta <= 0 or source_delta <= 0:
            continue
        rate = source_delta / mix_delta
        if not 0.5 <= rate <= 2.0:
            continue
        offset = left.anchor_time_ms - rate * left_mix_start
        local_model = SongTimingModel(
            source_ordinal=left.source_ordinal,
            source=left.source,
            rate=rate,
            offset_ms=offset,
            rate_source="local_bilateral_text_only",
            anchor_count=2,
            inlier_count=2,
            median_abs_residual_ms=0.0,
            inlier_fraction=1.0,
            status="ready",
            word_timing_anchor_count=int(left.has_word_timing) + int(right.has_word_timing),
        )

        block_cues = list(cues[block_start:block_end])
        source_normalized = "".join(cue.normalized for cue in block_cues)
        target_normalized = "".join(item.normalized for item in gap)
        grouped_score = _pair_score(source_normalized, target_normalized)

        # Strong preference: preserve the exact editor character ownership.
        targets: list[str] | None = None
        if grouped_score >= _LOCAL_GROUPED_SCORE:
            projected, insertion_reason = _assign_targets(
                [cue.text for cue in block_cues],
                "".join(item.text for item in gap),
            )
            if insertion_reason is None and all(projected):
                targets = projected

        pending: dict[int, MatchDecision] = {}
        if targets is not None:
            canonical_text = "".join(item.text for item in gap)
            for cue, target in zip(block_cues, targets):
                original = decisions_by_cue.get(cue.ordinal)
                if original is None or original.action != "review":
                    pending = {}
                    break
                # No unique canonical timing identity is claimed here: one
                # canonical line may legitimately span multiple editor cues.
                pending[cue.ordinal] = replace(
                    original,
                    canonical_ordinal=None,
                    canonical_span=None,
                    action="unchanged" if target == cue.text else "replace",
                    reason="local_bilateral_span_preserves_editor_segmentation",
                    cue_span=(cue.ordinal, cue.ordinal + 1),
                    canonical_text=canonical_text,
                    output_text=target,
                    edit_operations=(),
                )
            if pending:
                segmentation_count += len(pending)
        else:
            partition = _partition_gap_by_cue_starts(
                block_cues,
                gap,
                right_mix_start,
                local_model,
                start_tolerance_ms=750,
                boundary_guard_ms=500,
                max_lines_per_cue=max_lines_per_cue,
            )
            if partition is None:
                continue
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
                pending[cue.ordinal] = replace(
                    original,
                    canonical_ordinal=assigned[0].ordinal,
                    canonical_span=(assigned[0].ordinal, assigned[-1].ordinal + 1),
                    action="unchanged" if target == cue.text else "replace",
                    reason="local_bilateral_timing_confirms_canonical_sequence",
                    cue_span=(cue.ordinal, cue.ordinal + 1),
                    canonical_text=target,
                    output_text=target,
                    edit_operations=(),
                )
            if pending:
                timing_partition_count += len(pending)

        if not pending:
            continue
        block_count += 1
        for cue_ordinal, decision in pending.items():
            resolved[cue_ordinal] = decision
            decisions_by_cue[cue_ordinal] = decision
            if decision.output_text != cues[cue_ordinal].text:
                replacements[cue_ordinal] = decision.output_text

    output = [resolved.get(item.cue_ordinal, item) for item in decisions]
    output.sort(key=lambda item: item.cue_ordinal)
    return (
        replacements,
        output,
        len(resolved),
        block_count,
        segmentation_count,
        timing_partition_count,
    )


def _consensus_direct_recovery(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    decisions: Sequence[MatchDecision],
    models: Sequence[SongTimingModel],
) -> tuple[dict[int, str], list[MatchDecision], int]:
    canonical_by_ordinal = _canonical_by_ordinal(canonical)
    model_by_source = {item.source_ordinal: item for item in models}
    replacements: dict[int, str] = {}
    resolved: dict[int, MatchDecision] = {}

    for decision in decisions:
        if (
            decision.action != "review"
            or decision.canonical_span is None
            or decision.canonical_span[1] - decision.canonical_span[0] != 1
        ):
            continue
        occurrence = canonical_by_ordinal.get(int(decision.canonical_span[0]))
        if occurrence is None:
            continue
        model = model_by_source.get(occurrence.source_ordinal)
        if model is None:
            continue
        cue = cues[decision.cue_ordinal]
        cue_start, cue_end = _cue_times(cue)
        predicted = model.source_to_mix_ms(occurrence.anchor_time_ms)
        if not (
            cue_start - _CONSENSUS_ONSET_MARGIN_MS
            <= predicted
            <= cue_end + _CONSENSUS_ONSET_MARGIN_MS
        ):
            continue

        target = occurrence.text
        stripped = cue.text.strip()
        if (
            stripped
            and stripped[0] in _LEADING_ADLIB_CHARS
            and (not target or target[0] != stripped[0])
            and predicted - cue_start >= _LEADING_ADLIB_MIN_LEAD_MS
        ):
            target = f"{stripped[0]} {target}"

        resolved[cue.ordinal] = replace(
            decision,
            canonical_ordinal=occurrence.ordinal,
            canonical_span=(occurrence.ordinal, occurrence.ordinal + 1),
            cue_span=(cue.ordinal, cue.ordinal + 1),
            action="unchanged" if target == cue.text else "replace",
            reason="exact_consensus_timing_confirms_mapped_canonical",
            canonical_text=target,
            output_text=target,
            edit_operations=(),
        )
        if target != cue.text:
            replacements[cue.ordinal] = target

    output = [resolved.get(item.cue_ordinal, item) for item in decisions]
    output.sort(key=lambda item: item.cue_ordinal)
    return replacements, output, len(resolved)


def recover_text_reviews_from_consensus(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    decisions: Sequence[MatchDecision],
) -> tuple[
    dict[int, str],
    list[MatchDecision],
    list[SongTimingModel],
    TextConsensusRecoverySummary,
]:
    """Run the v1.1.4 text-only recovery stages.

    The returned models are evidence models for subsequent TEXT recovery only.
    Callers must never pass them as Smart timing-mutation authority.
    """

    original_decisions = list(decisions)
    consensus_models = _build_exact_consensus_models(
        cues,
        canonical,
        original_decisions,
    )

    (
        local_replacements,
        local_decisions,
        local_cue_count,
        local_block_count,
        segmentation_count,
        timing_partition_count,
    ) = _local_bilateral_recovery(cues, canonical, decisions)

    direct_replacements, output_decisions, direct_count = _consensus_direct_recovery(
        cues,
        canonical,
        local_decisions,
        consensus_models,
    )
    replacements = dict(local_replacements)
    replacements.update(direct_replacements)

    return (
        replacements,
        output_decisions,
        consensus_models,
        TextConsensusRecoverySummary(
            local_bilateral_cue_count=local_cue_count,
            local_bilateral_block_count=local_block_count,
            local_segmentation_preserve_count=segmentation_count,
            local_timing_partition_count=timing_partition_count,
            consensus_timing_cue_count=direct_count,
            consensus_model_count=len(consensus_models),
        ),
    )
