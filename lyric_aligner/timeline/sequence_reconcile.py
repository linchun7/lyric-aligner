"""Song-local canonical sequence reconciliation for Smart text recovery.

This module breaks the text-first bootstrap deadlock without weakening Text
Repair thresholds or Smart timing gates. It builds an independent text-only
affine projection from baseline high-confidence lyric identities. The
projection may reconcile weak/review text inside a strongly bounded canonical
sequence or cautiously propagate from the outermost strong anchor until timing
or text evidence stops agreeing.

Sequence-projected text is final-text evidence only. Its decision score is
capped below B grade, so it can never become an A/B timing anchor or bootstrap
its own timing authority. Results already recovered by the stronger,
independently-ready four-A timing path are immutable to this lower-authority
layer.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from functools import lru_cache
from statistics import median
from typing import Mapping, Sequence

from lyric_aligner.text_repair import (
    MatchDecision,
    SubtitleCue,
    _normalize_for_match,
    _pair_score,
)
from lyric_aligner.timeline.anchor_repair import TimedCanonicalOccurrence, _cue_times

_PROTECTED_STRONGER_RECOVERY_REASONS = frozenset(
    {
        "timing_model_confirms_canonical_sequence",
        "timing_model_confirms_song_edge_canonical",
    }
)
_FRONTIER_SINGLE_LINE_MIN_SIMILARITY = 0.30
_FRONTIER_MULTI_LINE_MIN_SIMILARITY = 0.42
_FRONTIER_SHORT_CUE_MIN_SIMILARITY = 0.80


@dataclass(frozen=True)
class SequenceProjectionModel:
    source_ordinal: int
    source: str
    rate: float
    offset_ms: float
    rate_source: str
    strong_anchor_count: int
    a_anchor_count: int
    inlier_count: int
    median_abs_residual_ms: float
    inlier_fraction: float
    status: str

    def source_to_mix_ms(self, source_ms: int) -> int:
        return int(round((source_ms - self.offset_ms) / self.rate))


@dataclass(frozen=True)
class SequenceRecoverySummary:
    reconciled_cue_count: int = 0
    reconciled_region_count: int = 0
    resolved_review_cue_count: int = 0
    frontier_cue_count: int = 0
    frontier_run_count: int = 0


@dataclass(frozen=True)
class _TextAnchor:
    cue_ordinal: int
    canonical_ordinal: int
    source_ordinal: int
    source: str
    mix_start_ms: int
    source_time_ms: int
    score: float
    grade: str


def _protected(decision: MatchDecision | None) -> bool:
    return decision is not None and decision.reason in _PROTECTED_STRONGER_RECOVERY_REASONS


def _single_span(decision: MatchDecision | None) -> tuple[int, int] | None:
    if (
        decision is None
        or decision.action == "review"
        or _protected(decision)
        or decision.canonical_span is None
    ):
        return None
    start, end = decision.canonical_span
    if end - start != 1:
        return None
    return int(start), int(end)


def _anchor_inventory(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    decisions: Sequence[MatchDecision],
) -> list[_TextAnchor]:
    canonical_by_ordinal = {item.ordinal: item for item in canonical}
    canonical_counts: dict[int, Counter[str]] = defaultdict(Counter)
    for item in canonical:
        canonical_counts[item.source_ordinal][item.normalized] += 1

    cue_source: dict[int, int] = {}
    for decision in decisions:
        span = _single_span(decision)
        if span is None:
            continue
        occurrence = canonical_by_ordinal.get(span[0])
        if occurrence is not None:
            cue_source[decision.cue_ordinal] = occurrence.source_ordinal

    cue_counts: dict[int, Counter[str]] = defaultdict(Counter)
    for cue in cues:
        source_ordinal = cue_source.get(cue.ordinal)
        if source_ordinal is not None:
            cue_counts[source_ordinal][cue.normalized] += 1

    anchors: list[_TextAnchor] = []
    decision_by_cue = {item.cue_ordinal: item for item in decisions}
    for cue in cues:
        decision = decision_by_cue.get(cue.ordinal)
        span = _single_span(decision)
        if decision is None or span is None or decision.score < 0.92:
            continue
        occurrence = canonical_by_ordinal.get(span[0])
        if occurrence is None:
            continue
        exact_unique = (
            decision.action == "unchanged"
            and decision.score >= 0.995
            and cue.normalized == occurrence.normalized
            and canonical_counts[occurrence.source_ordinal][occurrence.normalized] == 1
            and cue_counts[occurrence.source_ordinal][cue.normalized] == 1
        )
        start_ms, _ = _cue_times(cue)
        anchors.append(
            _TextAnchor(
                cue_ordinal=cue.ordinal,
                canonical_ordinal=occurrence.ordinal,
                source_ordinal=occurrence.source_ordinal,
                source=occurrence.source,
                mix_start_ms=start_ms,
                source_time_ms=occurrence.anchor_time_ms,
                score=float(decision.score),
                grade="A" if exact_unique else "B",
            )
        )
    return anchors


def _pairwise_rate(anchors: Sequence[_TextAnchor]) -> float | None:
    slopes: list[float] = []
    for index, left in enumerate(anchors):
        for right in anchors[index + 1 :]:
            mix_delta = right.mix_start_ms - left.mix_start_ms
            source_delta = right.source_time_ms - left.source_time_ms
            if mix_delta < 3000 or source_delta <= 0:
                continue
            slope = source_delta / mix_delta
            if 0.5 <= slope <= 2.0:
                slopes.append(slope)
    return float(median(slopes)) if slopes else None


def build_sequence_projection_models(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    decisions: Sequence[MatchDecision],
    *,
    rate_prior_by_source: Mapping[int, float] | None = None,
    inlier_threshold_ms: int = 750,
) -> tuple[list[SequenceProjectionModel], list[_TextAnchor]]:
    """Build text-only affine projections from baseline strong identities.

    Without an exact hard rate prior a model needs at least three unique exact
    A anchors plus one additional A/B anchor, with useful time span and stable
    residuals. This gate is intentionally weaker than Smart timing's four-A
    gate, but its output has no timing-mutation authority.
    """

    priors = rate_prior_by_source or {}
    anchors = _anchor_inventory(cues, canonical, decisions)
    grouped: dict[int, list[_TextAnchor]] = defaultdict(list)
    for item in anchors:
        grouped[item.source_ordinal].append(item)
    source_names = {item.source_ordinal: item.source for item in canonical}

    models: list[SequenceProjectionModel] = []
    for source_ordinal in sorted(source_names):
        rows = sorted(grouped.get(source_ordinal, []), key=lambda item: item.cue_ordinal)
        a_count = sum(item.grade == "A" for item in rows)
        prior = priors.get(source_ordinal)
        enough = (
            (prior is not None and len(rows) >= 2 and a_count >= 2)
            or (prior is None and len(rows) >= 4 and a_count >= 3)
        )
        if not enough:
            models.append(
                SequenceProjectionModel(
                    source_ordinal,
                    source_names[source_ordinal],
                    1.0,
                    0.0,
                    "none",
                    len(rows),
                    a_count,
                    0,
                    float("inf"),
                    0.0,
                    "insufficient_strong_anchors",
                )
            )
            continue
        if (
            max(item.mix_start_ms for item in rows) - min(item.mix_start_ms for item in rows) < 8000
            or max(item.source_time_ms for item in rows) - min(item.source_time_ms for item in rows) < 8000
        ):
            models.append(
                SequenceProjectionModel(
                    source_ordinal,
                    source_names[source_ordinal],
                    1.0,
                    0.0,
                    "none",
                    len(rows),
                    a_count,
                    0,
                    float("inf"),
                    0.0,
                    "insufficient_anchor_span",
                )
            )
            continue

        estimated = _pairwise_rate(rows)
        if prior is not None:
            rate = float(prior)
            rate_source = "exact_rate_prior"
            status = "candidate" if 0.5 <= rate <= 2.0 else "invalid_rate_prior"
            if status != "candidate":
                rate = 1.0
        elif estimated is not None:
            rate = estimated
            rate_source = "robust_strong_text_anchors"
            status = "candidate"
        else:
            rate = 1.0
            rate_source = "none"
            status = "insufficient_rate_evidence"

        if status != "candidate":
            models.append(
                SequenceProjectionModel(
                    source_ordinal,
                    source_names[source_ordinal],
                    rate,
                    0.0,
                    rate_source,
                    len(rows),
                    a_count,
                    0,
                    float("inf"),
                    0.0,
                    status,
                )
            )
            continue

        offset = float(
            median([item.source_time_ms - rate * item.mix_start_ms for item in rows])
        )
        residuals = [
            item.source_time_ms - (offset + rate * item.mix_start_ms) for item in rows
        ]
        inliers = [value for value in residuals if abs(value) <= inlier_threshold_ms]
        med = float(median(abs(value) for value in residuals))
        fraction = len(inliers) / len(rows)
        ready = med <= 450 and fraction >= 0.75
        if prior is not None and estimated is not None:
            if abs(estimated - float(prior)) / float(prior) > 0.03:
                ready = False
                status = "rate_prior_conflict"
        if ready:
            status = "ready"
        elif status == "candidate":
            status = "unstable"
        models.append(
            SequenceProjectionModel(
                source_ordinal=source_ordinal,
                source=source_names[source_ordinal],
                rate=rate,
                offset_ms=offset,
                rate_source=rate_source,
                strong_anchor_count=len(rows),
                a_anchor_count=a_count,
                inlier_count=len(inliers),
                median_abs_residual_ms=med,
                inlier_fraction=fraction,
                status=status,
            )
        )
    return models, anchors


def _consistent_anchor(
    anchor: _TextAnchor,
    model: SequenceProjectionModel,
    tolerance_ms: int = 750,
) -> bool:
    return (
        abs(anchor.mix_start_ms - model.source_to_mix_ms(anchor.source_time_ms))
        <= tolerance_ms
    )


def _source_rows(
    canonical: Sequence[TimedCanonicalOccurrence],
) -> dict[int, list[TimedCanonicalOccurrence]]:
    result: dict[int, list[TimedCanonicalOccurrence]] = defaultdict(list)
    for item in canonical:
        result[item.source_ordinal].append(item)
    for rows in result.values():
        rows.sort(key=lambda item: item.ordinal)
    return dict(result)


def _partition_bounded_region(
    block_cues: Sequence[SubtitleCue],
    gap: Sequence[TimedCanonicalOccurrence],
    right_anchor: TimedCanonicalOccurrence,
    right_anchor_mix_start_ms: int,
    model: SequenceProjectionModel,
    *,
    max_lines_per_cue: int = 4,
    start_tolerance_ms: int = 1300,
    boundary_tolerance_ms: int = 2500,
) -> list[list[TimedCanonicalOccurrence]] | None:
    """Partition a complete canonical gap while keeping editor cue ownership."""

    if not block_cues or not gap:
        return None
    if len(gap) < len(block_cues) or len(gap) > len(block_cues) * max_lines_per_cue:
        return None
    starts = [_cue_times(cue)[0] for cue in block_cues]
    if any(right <= left for left, right in zip(starts, starts[1:])):
        return None
    predicted = [model.source_to_mix_ms(item.anchor_time_ms) for item in gap]
    if any(right <= left for left, right in zip(predicted, predicted[1:])):
        return None
    right_predicted = model.source_to_mix_ms(right_anchor.anchor_time_ms)
    if abs(right_predicted - right_anchor_mix_start_ms) > 750:
        return None

    @lru_cache(maxsize=None)
    def solve(cue_index: int, line_index: int) -> tuple[float, tuple[int, ...]] | None:
        if cue_index == len(block_cues):
            return (0.0, ()) if line_index == len(gap) else None
        remaining_cues = len(block_cues) - cue_index - 1
        best: tuple[float, tuple[int, ...]] | None = None
        cue = block_cues[cue_index]
        cue_start, cue_end = _cue_times(cue)
        for count in range(1, max_lines_per_cue + 1):
            next_line = line_index + count
            if next_line > len(gap):
                break
            remaining_lines = len(gap) - next_line
            if (
                remaining_lines < remaining_cues
                or remaining_lines > remaining_cues * max_lines_per_cue
            ):
                continue
            first_predicted = predicted[line_index]
            last_predicted = predicted[next_line - 1]
            start_delta = abs(first_predicted - cue_start)
            if start_delta > start_tolerance_ms or last_predicted > cue_end + 1500:
                continue
            next_predicted = (
                predicted[next_line] if next_line < len(gap) else right_predicted
            )
            next_cue_start = (
                starts[cue_index + 1]
                if cue_index + 1 < len(block_cues)
                else right_anchor_mix_start_ms
            )
            boundary_delta = abs(next_predicted - next_cue_start)
            if boundary_delta > boundary_tolerance_ms:
                continue
            source_length = max(1, len(cue.normalized))
            target_length = max(
                1,
                sum(len(item.normalized) for item in gap[line_index:next_line]),
            )
            length_penalty = abs(math.log(target_length / source_length))
            local_cost = (
                start_delta / 900.0
                + boundary_delta / 900.0
                + 0.35 * length_penalty
            )
            tail = solve(cue_index + 1, next_line)
            if tail is None:
                continue
            candidate = (local_cost + tail[0], (count, *tail[1]))
            if best is None or candidate[0] < best[0]:
                best = candidate
        return best

    solution = solve(0, 0)
    if solution is None:
        return None
    output: list[list[TimedCanonicalOccurrence]] = []
    cursor = 0
    for count in solution[1]:
        output.append(list(gap[cursor : cursor + count]))
        cursor += count
    return output


def _projected_decision(
    cue: SubtitleCue,
    original: MatchDecision,
    rows: Sequence[TimedCanonicalOccurrence],
    *,
    reason: str,
) -> MatchDecision:
    target = " ".join(item.text for item in rows)
    normalized_target = _normalize_for_match(target)
    return replace(
        original,
        canonical_ordinal=rows[0].ordinal,
        score=min(float(original.score), 0.91),
        action="unchanged" if cue.normalized == normalized_target else "replace",
        reason=reason,
        cue_span=(cue.ordinal, cue.ordinal + 1),
        canonical_span=(rows[0].ordinal, rows[-1].ordinal + 1),
        canonical_text=target,
        output_text=target,
        edit_operations=(),
    )


def _frontier_choice(
    cue: SubtitleCue,
    rows: Sequence[TimedCanonicalOccurrence],
    model: SequenceProjectionModel,
    next_cue_start_ms: int | None,
    *,
    max_lines_per_cue: int = 4,
    reverse: bool = False,
) -> tuple[list[TimedCanonicalOccurrence], int | None] | None:
    """Choose a one-sided frontier assignment with lexical anti-ad-lib guards.

    Frontier evidence is weaker than a two-anchor bounded gap. Timing proximity
    alone is therefore never sufficient to turn a short/generic editor cue into
    a full canonical lyric. Single-line assignments need at least modest lexical
    support; multi-line assignments keep the stronger existing floor, and very
    short cues require near-identity before they may be consumed.
    """

    if not rows:
        return None
    candidates: list[tuple[float, list[TimedCanonicalOccurrence], int | None]] = []
    limit = min(max_lines_per_cue, len(rows))
    for count in range(1, limit + 1):
        assigned = list(rows[-count:] if reverse else rows[:count])
        first_predicted = model.source_to_mix_ms(assigned[0].anchor_time_ms)
        cue_start, cue_end = _cue_times(cue)
        start_delta = abs(first_predicted - cue_start)
        if start_delta > 900:
            continue
        last_predicted = model.source_to_mix_ms(assigned[-1].anchor_time_ms)
        if last_predicted > cue_end + 1200:
            continue
        target_normalized = "".join(item.normalized for item in assigned)
        similarity = _pair_score(cue.normalized, target_normalized)
        minimum_similarity = (
            _FRONTIER_MULTI_LINE_MIN_SIMILARITY
            if count > 1
            else _FRONTIER_SINGLE_LINE_MIN_SIMILARITY
        )
        if similarity < minimum_similarity:
            continue
        if (
            len(cue.normalized) <= 2
            and similarity < _FRONTIER_SHORT_CUE_MIN_SIMILARITY
        ):
            continue
        boundary_delta: int | None = None
        if next_cue_start_ms is not None:
            next_row = (
                rows[-count - 1]
                if reverse and len(rows) > count
                else rows[count]
                if not reverse and len(rows) > count
                else None
            )
            if next_row is not None:
                boundary_delta = abs(
                    model.source_to_mix_ms(next_row.anchor_time_ms) - next_cue_start_ms
                )
        source_length = max(1, len(cue.normalized))
        target_length = max(1, len(target_normalized))
        length_penalty = abs(math.log(target_length / source_length))
        cost = (
            start_delta / 900.0
            + 0.50 * (1.0 - similarity)
            + 0.25 * length_penalty
        )
        if boundary_delta is not None:
            cost += min(boundary_delta, 2500) / 1200.0
        candidates.append((cost, assigned, boundary_delta))
    if not candidates:
        return None
    _, assigned, boundary_delta = min(candidates, key=lambda item: item[0])
    return assigned, boundary_delta


def reconcile_text_from_sequence_projection(
    cues: Sequence[SubtitleCue],
    canonical: Sequence[TimedCanonicalOccurrence],
    decisions: Sequence[MatchDecision],
    *,
    rate_prior_by_source: Mapping[int, float] | None = None,
    max_bounded_cues: int = 16,
) -> tuple[
    dict[int, str],
    list[MatchDecision],
    SequenceRecoverySummary,
    list[SequenceProjectionModel],
]:
    """Reconcile weak Smart text with song-local canonical order and timing.

    Stronger ready-model recoveries and baseline strong identities are
    immutable. Weak/review decisions may be replaced only inside a fully
    bounded same-song sequence or during a cautious outer-frontier walk. The
    frontier stops at the first timing break instead of chasing LRC across a
    cut/editor-only region.
    """

    models, baseline_anchors = build_sequence_projection_models(
        cues,
        canonical,
        decisions,
        rate_prior_by_source=rate_prior_by_source,
    )
    ready_models = {
        item.source_ordinal: item for item in models if item.status == "ready"
    }
    if not ready_models:
        return {}, list(decisions), SequenceRecoverySummary(), models

    canonical_by_ordinal = {item.ordinal: item for item in canonical}
    rows_by_source = _source_rows(canonical)
    source_position = {
        source: {item.ordinal: index for index, item in enumerate(rows)}
        for source, rows in rows_by_source.items()
    }
    decision_by_cue = {item.cue_ordinal: item for item in decisions}
    output_by_cue = dict(decision_by_cue)
    replacements: dict[int, str] = {}
    reconciled: set[int] = set()
    resolved_reviews: set[int] = set()
    region_count = 0
    frontier_count = 0
    frontier_runs = 0

    consistent: list[_TextAnchor] = []
    for anchor in baseline_anchors:
        model = ready_models.get(anchor.source_ordinal)
        if model is not None and _consistent_anchor(anchor, model):
            consistent.append(anchor)
    consistent.sort(key=lambda item: item.cue_ordinal)
    strong_cues = {item.cue_ordinal for item in consistent}

    # Complete gaps between adjacent model-consistent strong anchors. If a
    # stronger ready-model recovery already exists anywhere inside the block,
    # this lower-authority layer leaves the entire block untouched.
    for left, right in zip(consistent, consistent[1:]):
        if left.source_ordinal != right.source_ordinal:
            continue
        if right.cue_ordinal - left.cue_ordinal <= 1:
            continue
        block = list(cues[left.cue_ordinal + 1 : right.cue_ordinal])
        if not block or len(block) > max_bounded_cues:
            continue
        block_decisions = [decision_by_cue.get(cue.ordinal) for cue in block]
        if any(_protected(item) for item in block_decisions):
            continue
        if not any(
            item is not None and item.action == "review" for item in block_decisions
        ):
            continue

        positions = source_position[left.source_ordinal]
        left_pos = positions.get(left.canonical_ordinal)
        right_pos = positions.get(right.canonical_ordinal)
        if left_pos is None or right_pos is None or right_pos <= left_pos + 1:
            continue
        gap = rows_by_source[left.source_ordinal][left_pos + 1 : right_pos]
        right_occurrence = canonical_by_ordinal.get(right.canonical_ordinal)
        if right_occurrence is None:
            continue
        partition = _partition_bounded_region(
            block,
            gap,
            right_occurrence,
            right.mix_start_ms,
            ready_models[left.source_ordinal],
        )
        if partition is None:
            continue

        pending: list[
            tuple[SubtitleCue, MatchDecision, list[TimedCanonicalOccurrence]]
        ] = []
        for cue, assigned in zip(block, partition):
            original = output_by_cue.get(cue.ordinal)
            if original is None or cue.ordinal in strong_cues or _protected(original):
                pending = []
                break
            pending.append((cue, original, assigned))
        if not pending:
            continue
        for cue, original, assigned in pending:
            decision = _projected_decision(
                cue,
                original,
                assigned,
                reason="sequence_projection_confirms_bounded_canonical",
            )
            output_by_cue[cue.ordinal] = decision
            replacements[cue.ordinal] = decision.output_text
            reconciled.add(cue.ordinal)
            if original.action == "review":
                resolved_reviews.add(cue.ordinal)
        region_count += 1

    # Walk only outside each source's outermost strong anchors. Any stronger
    # recovered decision is a hard stop, not something to be relabelled.
    by_source: dict[int, list[_TextAnchor]] = defaultdict(list)
    for anchor in consistent:
        by_source[anchor.source_ordinal].append(anchor)
    global_strong = {item.cue_ordinal: item for item in consistent}

    for source_ordinal, anchors in by_source.items():
        model = ready_models[source_ordinal]
        rows = rows_by_source[source_ordinal]
        positions = source_position[source_ordinal]
        anchors.sort(key=lambda item: item.cue_ordinal)

        last = anchors[-1]
        pos = positions[last.canonical_ordinal] + 1
        cue_index = last.cue_ordinal + 1
        run_changed = False
        while cue_index < len(cues) and pos < len(rows):
            if cue_index in global_strong:
                break
            cue = cues[cue_index]
            original = output_by_cue.get(cue.ordinal)
            if original is None or _protected(original):
                break
            previous_start, _ = _cue_times(cues[cue_index - 1])
            cue_start, _ = _cue_times(cue)
            if cue_start <= previous_start:
                break
            next_start = None
            if cue_index + 1 < len(cues):
                candidate_next, _ = _cue_times(cues[cue_index + 1])
                if candidate_next > cue_start:
                    next_start = candidate_next
            choice = _frontier_choice(cue, rows[pos:], model, next_start)
            if choice is None:
                break
            assigned, boundary_delta = choice
            decision = _projected_decision(
                cue,
                original,
                assigned,
                reason="sequence_projection_confirms_frontier_canonical",
            )
            output_by_cue[cue.ordinal] = decision
            replacements[cue.ordinal] = decision.output_text
            reconciled.add(cue.ordinal)
            if original.action == "review":
                resolved_reviews.add(cue.ordinal)
            frontier_count += 1
            run_changed = True
            pos += len(assigned)
            cue_index += 1
            if boundary_delta is not None and boundary_delta > 1600:
                break
        if run_changed:
            frontier_runs += 1

        first = anchors[0]
        pos = positions[first.canonical_ordinal] - 1
        cue_index = first.cue_ordinal - 1
        run_changed = False
        while cue_index >= 0 and pos >= 0:
            if cue_index in global_strong:
                break
            cue = cues[cue_index]
            original = output_by_cue.get(cue.ordinal)
            if original is None or _protected(original):
                break
            cue_start, _ = _cue_times(cue)
            if cue_index + 1 < len(cues):
                next_start, _ = _cue_times(cues[cue_index + 1])
                if cue_start >= next_start:
                    break
            choice = _frontier_choice(cue, rows[: pos + 1], model, None, reverse=True)
            if choice is None:
                break
            assigned, _ = choice
            decision = _projected_decision(
                cue,
                original,
                assigned,
                reason="sequence_projection_confirms_frontier_canonical",
            )
            output_by_cue[cue.ordinal] = decision
            replacements[cue.ordinal] = decision.output_text
            reconciled.add(cue.ordinal)
            if original.action == "review":
                resolved_reviews.add(cue.ordinal)
            frontier_count += 1
            run_changed = True
            pos -= len(assigned)
            cue_index -= 1
        if run_changed:
            frontier_runs += 1

    ordered = [output_by_cue.get(item.cue_ordinal, item) for item in decisions]
    return (
        replacements,
        ordered,
        SequenceRecoverySummary(
            reconciled_cue_count=len(reconciled),
            reconciled_region_count=region_count,
            resolved_review_cue_count=len(resolved_reviews),
            frontier_cue_count=frontier_count,
            frontier_run_count=frontier_runs,
        ),
        models,
    )
