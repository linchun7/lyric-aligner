"""Production-first planning for Lyric Aligner v4.

The planner keeps two concepts separate:

* a primary occurrence interval used to build the current single-track timeline;
* an overlapping transition-search interval around each nominal boundary.

The latter deliberately lets both adjacent TrackOccurrences inspect the same mix
region.  A shared search window is evidence collection, not proof of overlap.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from lyric_aligner.assets.bindings import ResolvedAssetBinding
from lyric_aligner.audio.transition import transition_search_interval


class ProductionPlanError(ValueError):
    """Raised when a task cannot be represented as an ordered v4 production plan."""


@dataclass(frozen=True)
class OccurrencePlan:
    occurrence_id: str
    ordinal: int
    primary_start: float
    primary_end: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransitionPlan:
    left_occurrence_id: str
    right_occurrence_id: str
    nominal_boundary: float
    search_start: float
    search_end: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionPlan:
    mix_duration: float
    occurrences: tuple[OccurrencePlan, ...]
    transitions: tuple[TransitionPlan, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mix_duration": self.mix_duration,
            "occurrences": [item.to_dict() for item in self.occurrences],
            "transitions": [item.to_dict() for item in self.transitions],
        }


def build_production_plan(
    bindings: Iterable[ResolvedAssetBinding],
    *,
    mix_duration: float,
    transition_margin_seconds: float,
) -> ProductionPlan:
    """Build primary and shared-boundary search intervals for one real task."""

    if mix_duration <= 0:
        raise ProductionPlanError("mix_duration must be positive")
    if transition_margin_seconds <= 0:
        raise ProductionPlanError("transition_margin_seconds must be positive")

    ordered = sorted(bindings, key=lambda item: item.ordinal)
    if not ordered:
        raise ProductionPlanError("production plan requires at least one TrackOccurrence")
    if len({item.ordinal for item in ordered}) != len(ordered):
        raise ProductionPlanError("TrackOccurrence ordinals must be unique")
    if len({item.occurrence_id for item in ordered}) != len(ordered):
        raise ProductionPlanError("TrackOccurrence IDs must be unique")

    starts = [float(item.nominal_start_ms) / 1000.0 for item in ordered]
    if starts[0] < 0 or starts[0] >= mix_duration:
        raise ProductionPlanError("first nominal start is outside the mix")
    if any(right < left for left, right in zip(starts, starts[1:])):
        raise ProductionPlanError("nominal starts must be non-decreasing")
    if any(start >= mix_duration for start in starts):
        raise ProductionPlanError("a nominal start is outside the mix")

    occurrences: list[OccurrencePlan] = []
    for index, binding in enumerate(ordered):
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(starts) else mix_duration
        if end <= start:
            raise ProductionPlanError(
                f"occurrence {binding.occurrence_id} has no positive primary interval"
            )
        occurrences.append(
            OccurrencePlan(
                occurrence_id=binding.occurrence_id,
                ordinal=binding.ordinal,
                primary_start=start,
                primary_end=end,
            )
        )

    transitions: list[TransitionPlan] = []
    for index in range(len(ordered) - 1):
        boundary = starts[index + 1]
        search_start, search_end = transition_search_interval(
            boundary,
            mix_duration=mix_duration,
            margin_seconds=transition_margin_seconds,
        )
        transitions.append(
            TransitionPlan(
                left_occurrence_id=ordered[index].occurrence_id,
                right_occurrence_id=ordered[index + 1].occurrence_id,
                nominal_boundary=boundary,
                search_start=search_start,
                search_end=search_end,
            )
        )

    return ProductionPlan(
        mix_duration=mix_duration,
        occurrences=tuple(occurrences),
        transitions=tuple(transitions),
    )


def readiness_status(*, issues: Iterable[dict[str, Any]]) -> str:
    """Return the production-run state without silently falling back to legacy."""

    return "review_required" if any(True for _ in issues) else "ready_for_render"
