"""Audio-timed canonical-unit ownership across fixed subtitle cue intervals.

Canonical text/order remains lexical truth.  This module never invents ASR text;
it only partitions an already-known ordered canonical-unit sequence across a fixed,
chronological set of subtitle cues using aligned unit timings.  The selected
partition is contiguous and lossless: every canonical unit is assigned exactly
once and cue order is preserved.

The first revision is evaluation-only.  It exposes a calibrated-quality candidate
and an ambiguity margin so a later production policy can decide when a text-only
ownership rewrite is safe.  Timing is never changed here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Sequence


AUDIO_TEXT_OWNERSHIP_VERSION = "1.0"
AUDIO_TEXT_OWNERSHIP_AUTHORITY = "evaluation_only_audio_timed_canonical_partition"


class AudioTextOwnershipError(ValueError):
    pass


@dataclass(frozen=True)
class TimedCanonicalUnit:
    unit_id: str
    text: str
    start_ms: int
    end_ms: int
    confidence: float = 1.0

    def validate(self) -> None:
        if not self.unit_id.strip() or not self.text:
            raise AudioTextOwnershipError("canonical unit identity/text must be non-empty")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise AudioTextOwnershipError("canonical unit interval must be positive/monotonic")
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise AudioTextOwnershipError("canonical unit confidence must be within [0,1]")


@dataclass(frozen=True)
class OwnershipCue:
    cue_id: str
    start_ms: int
    end_ms: int

    def validate(self) -> None:
        if not self.cue_id.strip():
            raise AudioTextOwnershipError("cue_id must be non-empty")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise AudioTextOwnershipError("cue interval must be positive/monotonic")


@dataclass(frozen=True)
class OwnershipPolicy:
    minimum_unit_confidence: float = 0.60
    maximum_mean_distance_ms: float = 250.0
    minimum_partition_margin_ms: float = 120.0
    minimum_units_per_cue: int = 1

    def validate(self) -> None:
        if not math.isfinite(self.minimum_unit_confidence) or not 0.0 <= self.minimum_unit_confidence <= 1.0:
            raise AudioTextOwnershipError("minimum_unit_confidence must be within [0,1]")
        if not math.isfinite(self.maximum_mean_distance_ms) or self.maximum_mean_distance_ms < 0:
            raise AudioTextOwnershipError("maximum_mean_distance_ms must be finite/nonnegative")
        if not math.isfinite(self.minimum_partition_margin_ms) or self.minimum_partition_margin_ms < 0:
            raise AudioTextOwnershipError("minimum_partition_margin_ms must be finite/nonnegative")
        if self.minimum_units_per_cue < 1:
            raise AudioTextOwnershipError("minimum_units_per_cue must be >= 1")


@dataclass(frozen=True)
class CueOwnershipAssignment:
    cue_id: str
    unit_start_index: int
    unit_end_index_exclusive: int
    unit_ids: tuple[str, ...]
    timing_cost_ms: float


@dataclass(frozen=True)
class AudioTextOwnershipResult:
    version: str
    authority: str
    feasible: bool
    assignments: tuple[CueOwnershipAssignment, ...]
    total_timing_cost_ms: float | None
    mean_timing_cost_ms: float | None
    second_best_total_timing_cost_ms: float | None
    partition_margin_ms: float | None
    minimum_unit_confidence: float | None
    automatic_eligible: bool
    timing_mutation_performed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["assignments"] = [
            {
                **asdict(assignment),
                "unit_ids": list(assignment.unit_ids),
            }
            for assignment in self.assignments
        ]
        return payload


def _distance_to_interval(midpoint_ms: float, cue: OwnershipCue) -> float:
    if midpoint_ms < cue.start_ms:
        return float(cue.start_ms) - midpoint_ms
    if midpoint_ms > cue.end_ms:
        return midpoint_ms - float(cue.end_ms)
    return 0.0


def _unit_cost(unit: TimedCanonicalUnit, cue: OwnershipCue) -> float:
    midpoint = (float(unit.start_ms) + float(unit.end_ms)) / 2.0
    distance = _distance_to_interval(midpoint, cue)
    # Low-confidence timings should be less decisive.  Keep a small floor so an
    # uncertain unit still contributes geometry, while the independent minimum
    # confidence gate prevents weak timing from receiving automatic authority.
    confidence_weight = max(0.10, float(unit.confidence))
    return distance * confidence_weight


def _validate_sequence(
    units: Sequence[TimedCanonicalUnit],
    cues: Sequence[OwnershipCue],
    policy: OwnershipPolicy,
) -> None:
    policy.validate()
    if not units or not cues:
        raise AudioTextOwnershipError("ownership requires non-empty units and cues")
    if len(units) < len(cues) * policy.minimum_units_per_cue:
        raise AudioTextOwnershipError("not enough canonical units to assign every cue")
    unit_ids: set[str] = set()
    previous_unit_start = -1
    for unit in units:
        unit.validate()
        if unit.unit_id in unit_ids:
            raise AudioTextOwnershipError("canonical unit ids must be unique")
        unit_ids.add(unit.unit_id)
        if unit.start_ms < previous_unit_start:
            raise AudioTextOwnershipError("canonical unit timings must be chronological")
        previous_unit_start = unit.start_ms
    cue_ids: set[str] = set()
    previous_end = -1
    for cue in cues:
        cue.validate()
        if cue.cue_id in cue_ids:
            raise AudioTextOwnershipError("cue ids must be unique")
        cue_ids.add(cue.cue_id)
        if cue.start_ms < previous_end:
            raise AudioTextOwnershipError(
                "overlapping/nonchronological cues require structural ownership handling"
            )
        previous_end = cue.end_ms


def _segment_cost(
    prefix_costs: Sequence[Sequence[float]],
    cue_index: int,
    start_index: int,
    end_index: int,
) -> float:
    return float(prefix_costs[cue_index][end_index] - prefix_costs[cue_index][start_index])


def solve_audio_text_ownership(
    *,
    units: Sequence[TimedCanonicalUnit],
    cues: Sequence[OwnershipCue],
    policy: OwnershipPolicy | None = None,
) -> AudioTextOwnershipResult:
    """Find the best and second-best contiguous canonical partition across cues."""

    policy = policy or OwnershipPolicy()
    _validate_sequence(units, cues, policy)
    unit_count = len(units)
    cue_count = len(cues)

    prefix_costs: list[list[float]] = []
    for cue in cues:
        values = [0.0]
        running = 0.0
        for unit in units:
            running += _unit_cost(unit, cue)
            values.append(running)
        prefix_costs.append(values)

    # Each DP cell stores up to two distinct best paths as
    # (total_cost, split_indices_tuple).  split_indices contains the exclusive end
    # index for every cue processed so far.
    states: dict[int, list[tuple[float, tuple[int, ...]]]] = {0: [(0.0, ())]}
    for cue_index in range(cue_count):
        next_states: dict[int, list[tuple[float, tuple[int, ...]]]] = {}
        remaining_cues = cue_count - cue_index - 1
        for start_index, paths in states.items():
            minimum_end = start_index + policy.minimum_units_per_cue
            maximum_end = unit_count - remaining_cues * policy.minimum_units_per_cue
            for end_index in range(minimum_end, maximum_end + 1):
                cost = _segment_cost(prefix_costs, cue_index, start_index, end_index)
                bucket = next_states.setdefault(end_index, [])
                for total_cost, splits in paths:
                    candidate = (total_cost + cost, (*splits, end_index))
                    if candidate not in bucket:
                        bucket.append(candidate)
                bucket.sort(key=lambda item: (round(item[0], 9), item[1]))
                del bucket[2:]
        states = next_states

    finals = states.get(unit_count, [])
    if not finals:
        return AudioTextOwnershipResult(
            version=AUDIO_TEXT_OWNERSHIP_VERSION,
            authority=AUDIO_TEXT_OWNERSHIP_AUTHORITY,
            feasible=False,
            assignments=(),
            total_timing_cost_ms=None,
            mean_timing_cost_ms=None,
            second_best_total_timing_cost_ms=None,
            partition_margin_ms=None,
            minimum_unit_confidence=None,
            automatic_eligible=False,
            timing_mutation_performed=False,
            reason="no_contiguous_lossless_partition_satisfies_cue_constraints",
        )
    finals.sort(key=lambda item: (round(item[0], 9), item[1]))
    best_cost, best_splits = finals[0]
    second_cost = finals[1][0] if len(finals) > 1 else None

    assignments: list[CueOwnershipAssignment] = []
    start = 0
    for cue, end in zip(cues, best_splits):
        selected_units = units[start:end]
        assignments.append(
            CueOwnershipAssignment(
                cue_id=cue.cue_id,
                unit_start_index=start,
                unit_end_index_exclusive=end,
                unit_ids=tuple(unit.unit_id for unit in selected_units),
                timing_cost_ms=_segment_cost(
                    prefix_costs,
                    len(assignments),
                    start,
                    end,
                ),
            )
        )
        start = end

    mean_cost = best_cost / unit_count
    margin = None if second_cost is None else float(second_cost - best_cost)
    minimum_confidence = min(float(unit.confidence) for unit in units)
    automatic_eligible = bool(
        minimum_confidence >= policy.minimum_unit_confidence
        and mean_cost <= policy.maximum_mean_distance_ms
        and (margin is None or margin >= policy.minimum_partition_margin_ms)
    )
    if minimum_confidence < policy.minimum_unit_confidence:
        reason = "unit_timing_confidence_below_policy"
    elif mean_cost > policy.maximum_mean_distance_ms:
        reason = "best_partition_timing_cost_exceeds_policy"
    elif margin is not None and margin < policy.minimum_partition_margin_ms:
        reason = "best_and_second_partition_are_too_close"
    else:
        reason = "unique_low_cost_contiguous_canonical_partition"

    return AudioTextOwnershipResult(
        version=AUDIO_TEXT_OWNERSHIP_VERSION,
        authority=AUDIO_TEXT_OWNERSHIP_AUTHORITY,
        feasible=True,
        assignments=tuple(assignments),
        total_timing_cost_ms=float(best_cost),
        mean_timing_cost_ms=float(mean_cost),
        second_best_total_timing_cost_ms=None if second_cost is None else float(second_cost),
        partition_margin_ms=margin,
        minimum_unit_confidence=minimum_confidence,
        automatic_eligible=automatic_eligible,
        timing_mutation_performed=False,
        reason=reason,
    )


__all__ = [
    "AUDIO_TEXT_OWNERSHIP_VERSION",
    "AUDIO_TEXT_OWNERSHIP_AUTHORITY",
    "AudioTextOwnershipError",
    "TimedCanonicalUnit",
    "OwnershipCue",
    "OwnershipPolicy",
    "CueOwnershipAssignment",
    "AudioTextOwnershipResult",
    "solve_audio_text_ownership",
]
