"""Production-first planning and stable review identity for Lyric Aligner v4."""

from __future__ import annotations

import hashlib
import json
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
    content_end: float
    occurrences: tuple[OccurrencePlan, ...]
    transitions: tuple[TransitionPlan, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mix_duration": self.mix_duration,
            "content_end": self.content_end,
            "occurrences": [item.to_dict() for item in self.occurrences],
            "transitions": [item.to_dict() for item in self.transitions],
        }


def build_production_plan(
    bindings: Iterable[ResolvedAssetBinding],
    *,
    mix_duration: float,
    content_end: float | None = None,
    transition_margin_seconds: float,
) -> ProductionPlan:
    """Build primary and shared-boundary search intervals for one real task."""

    if mix_duration <= 0:
        raise ProductionPlanError("mix_duration must be positive")
    effective_content_end = mix_duration if content_end is None else float(content_end)
    if effective_content_end <= 0 or effective_content_end > mix_duration:
        raise ProductionPlanError(
            "content_end must be positive and no greater than mix_duration"
        )
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
    if starts[0] < 0 or starts[0] >= effective_content_end:
        raise ProductionPlanError("first nominal start is outside effective mix content")
    if any(right < left for left, right in zip(starts, starts[1:])):
        raise ProductionPlanError("nominal starts must be non-decreasing")
    if any(start >= effective_content_end for start in starts):
        raise ProductionPlanError("a nominal start is outside effective mix content")

    occurrences: list[OccurrencePlan] = []
    for index, binding in enumerate(ordered):
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(starts) else effective_content_end
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
            mix_duration=effective_content_end,
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
        content_end=effective_content_end,
        occurrences=tuple(occurrences),
        transitions=tuple(transitions),
    )


def issue_identity_payload(issue: dict[str, Any]) -> dict[str, Any]:
    """Return stable semantic coordinates, excluding prose/diagnostic counters."""

    kind = str(issue.get("kind") or "")
    payload: dict[str, Any] = {"kind": kind}
    if kind == "timewarp":
        payload["occurrence_id"] = str(issue.get("occurrence_id") or "")
    elif kind == "transition":
        payload["left_occurrence_id"] = str(issue.get("left_occurrence_id") or "")
        payload["right_occurrence_id"] = str(issue.get("right_occurrence_id") or "")
    else:
        for key in (
            "occurrence_id",
            "left_occurrence_id",
            "right_occurrence_id",
        ):
            if issue.get(key) not in (None, ""):
                payload[key] = str(issue[key])
    if not kind:
        raise ProductionPlanError("review issue requires kind")
    if len(payload) == 1:
        raise ProductionPlanError("review issue has no stable semantic coordinates")
    return payload


def stable_issue_id(issue: dict[str, Any]) -> str:
    encoded = json.dumps(
        issue_identity_payload(issue),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def with_issue_id(issue: dict[str, Any]) -> dict[str, Any]:
    payload = dict(issue)
    payload["issue_id"] = stable_issue_id(payload)
    return payload


def readiness_status(*, issues: Iterable[dict[str, Any]]) -> str:
    """Return the production-run state without silently falling back to legacy."""

    return "review_required" if any(True for _ in issues) else "ready_for_render"
