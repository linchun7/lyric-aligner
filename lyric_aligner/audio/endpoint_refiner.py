"""Backend-neutral start/end refinement from masked target-sequence scores.

Full-sequence singing/forced aligners are strong at locating a lyric sequence but
may let the first or last lexical token absorb padded context.  This module keeps
those two jobs separate: an aligner establishes the lexical sequence and an
endpoint scorer then asks how much audio can be masked while the same target
sequence remains supported.

The algorithm is intentionally evaluation-only.  It does not know whether a
score comes from SOFA, HuBERT/CTC, a future singing model, or another backend and
it never grants subtitle mutation authority.  Its result must be calibrated on
human-gold boundaries before :mod:`boundary_risk` or another production policy
may consume it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Callable, Literal, Mapping, Sequence

from lyric_aligner.audio.forced_alignment import AlignmentWord, lexical_words


ENDPOINT_REFINER_POLICY_VERSION = "1.0"
ENDPOINT_REFINER_AUTHORITY = "evaluation_only_never_direct_timing_authority"


class EndpointRefinementError(ValueError):
    """Raised when endpoint refinement inputs or scorer results are unsafe."""


@dataclass(frozen=True)
class EndpointRefinementPolicy:
    """Search/score policy for model-backed endpoint refinement."""

    coarse_step_ms: int = 100
    precision_ms: int = 20
    max_boundary_shift_ms: int = 2500
    minimum_baseline_score: float = 0.50
    absolute_score_floor: float = 0.45
    max_absolute_score_drop: float = 0.08
    max_relative_score_drop: float = 0.12
    consecutive_failures_to_stop: int = 2

    def validate(self) -> None:
        if self.coarse_step_ms < 1 or self.precision_ms < 1:
            raise EndpointRefinementError("endpoint search steps must be positive")
        if self.precision_ms > self.coarse_step_ms:
            raise EndpointRefinementError("precision_ms cannot exceed coarse_step_ms")
        if self.max_boundary_shift_ms < self.precision_ms:
            raise EndpointRefinementError("max_boundary_shift_ms is too small")
        scores = (
            self.minimum_baseline_score,
            self.absolute_score_floor,
            self.max_absolute_score_drop,
            self.max_relative_score_drop,
        )
        if any(not math.isfinite(value) for value in scores):
            raise EndpointRefinementError("endpoint score thresholds must be finite")
        if not 0.0 <= self.minimum_baseline_score <= 1.0:
            raise EndpointRefinementError("minimum_baseline_score must be within [0,1]")
        if not 0.0 <= self.absolute_score_floor <= 1.0:
            raise EndpointRefinementError("absolute_score_floor must be within [0,1]")
        if not 0.0 <= self.max_absolute_score_drop <= 1.0:
            raise EndpointRefinementError("max_absolute_score_drop must be within [0,1]")
        if not 0.0 <= self.max_relative_score_drop <= 1.0:
            raise EndpointRefinementError("max_relative_score_drop must be within [0,1]")
        if self.consecutive_failures_to_stop < 1:
            raise EndpointRefinementError("consecutive_failures_to_stop must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EndpointRefinementResult:
    policy_version: str
    authority: str
    boundary_kind: str
    available: bool
    initial_boundary_ms: int
    refined_boundary_ms: int
    search_interval_ms: tuple[int, int]
    baseline_score: float
    selected_score: float
    acceptance_floor: float
    shift_ms: int
    uncertainty_ms: int
    score_call_count: int
    stop_reason: str
    automatic_mutation_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["search_interval_ms"] = list(self.search_interval_ms)
        return payload


def _probability(value: Any, *, label: str) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise EndpointRefinementError(f"{label} must be numeric") from exc
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise EndpointRefinementError(f"{label} must be within [0,1]")
    return score


def _nonnegative_ms(value: Any, *, label: str) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise EndpointRefinementError(f"{label} must be numeric") from exc
    if number < 0:
        raise EndpointRefinementError(f"{label} must be nonnegative")
    return number


def endpoint_search_interval_from_alignment(
    words: Sequence[Mapping[str, Any] | AlignmentWord],
    *,
    window_start_ms: int,
    window_end_ms: int,
    boundary_kind: Literal["start", "end"],
    max_token_span_ms: int = 3000,
) -> tuple[int, int]:
    """Build a local endpoint search interval from the *interior* side of a token.

    For an early-clamped first token, its end is often more useful than its start;
    for a late-clamped last token, its start is often more useful than its end.
    The unreliable outer edge is therefore ignored when constructing the interval.
    """

    start = _nonnegative_ms(window_start_ms, label="window_start_ms")
    end = _nonnegative_ms(window_end_ms, label="window_end_ms")
    span = _nonnegative_ms(max_token_span_ms, label="max_token_span_ms")
    if end <= start:
        raise EndpointRefinementError("alignment window must be monotonic")
    if span < 100:
        raise EndpointRefinementError("max_token_span_ms must be at least 100ms")
    if boundary_kind not in {"start", "end"}:
        raise EndpointRefinementError("boundary_kind must be start or end")

    lexical = lexical_words(words)
    if not lexical:
        raise EndpointRefinementError("alignment contains no lexical words")
    if boundary_kind == "start":
        interior = start + int(round(lexical[0].end_s * 1000.0))
        interior = max(start, min(end, interior))
        lower = max(start, interior - span)
        upper = interior
    else:
        interior = start + int(round(lexical[-1].start_s * 1000.0))
        interior = max(start, min(end, interior))
        lower = interior
        upper = min(end, interior + span)
    if upper - lower < 40:
        raise EndpointRefinementError("alignment-derived endpoint search interval is too narrow")
    return lower, upper


def _grid_inclusive(low: int, high: int, step: int) -> list[int]:
    if high < low:
        return []
    values = list(range(low, high + 1, step))
    if not values or values[-1] != high:
        values.append(high)
    return values


def _acceptance_floor(baseline: float, policy: EndpointRefinementPolicy) -> float:
    return max(
        policy.absolute_score_floor,
        baseline - policy.max_absolute_score_drop,
        baseline * (1.0 - policy.max_relative_score_drop),
    )


def _run_directional_search(
    *,
    candidates: Sequence[int],
    evaluate: Callable[[int], float],
    floor: float,
    consecutive_failures_to_stop: int,
) -> tuple[int | None, float | None, int, int | None]:
    """Return last accepted candidate before sustained score collapse.

    The candidate sequence must progress from least aggressive masking to more
    aggressive masking.  Later score recovery after sustained failure is ignored,
    because target-sequence scores are expected to be approximately monotonic as
    actual target audio is removed.
    """

    accepted_candidate: int | None = None
    accepted_score: float | None = None
    calls = 0
    failures = 0
    first_failed: int | None = None
    for candidate in candidates:
        score = _probability(evaluate(candidate), label="endpoint scorer result")
        calls += 1
        if score >= floor:
            if failures >= consecutive_failures_to_stop:
                break
            accepted_candidate = int(candidate)
            accepted_score = score
            failures = 0
            continue
        if first_failed is None:
            first_failed = int(candidate)
        failures += 1
        if failures >= consecutive_failures_to_stop:
            break
    return accepted_candidate, accepted_score, calls, first_failed


def refine_boundary_by_masked_score(
    *,
    boundary_kind: Literal["start", "end"],
    initial_boundary_ms: int,
    search_interval_ms: Sequence[int],
    score_visible_interval: Callable[[int, int], float],
    policy: EndpointRefinementPolicy | None = None,
) -> EndpointRefinementResult:
    """Refine one endpoint by progressively masking target-adjacent audio.

    ``score_visible_interval(start_ms, end_ms)`` must score the *same known target
    lexical sequence* when only that interval remains visible to the backend.  The
    score scale must stay stable within one refinement call.  The algorithm finds
    the latest start or earliest end that still preserves the target score.
    """

    policy = policy or EndpointRefinementPolicy()
    policy.validate()
    if boundary_kind not in {"start", "end"}:
        raise EndpointRefinementError("boundary_kind must be start or end")
    initial = _nonnegative_ms(initial_boundary_ms, label="initial_boundary_ms")
    if not isinstance(search_interval_ms, Sequence) or len(search_interval_ms) != 2:
        raise EndpointRefinementError("search_interval_ms must contain two points")
    low = _nonnegative_ms(search_interval_ms[0], label="search interval start")
    high = _nonnegative_ms(search_interval_ms[1], label="search interval end")
    if high <= low:
        raise EndpointRefinementError("search interval must be monotonic")
    if not low <= initial <= high:
        raise EndpointRefinementError("initial boundary must lie inside search interval")

    baseline = _probability(
        score_visible_interval(low, high),
        label="endpoint baseline score",
    )
    calls = 1
    floor = _acceptance_floor(baseline, policy)
    if baseline < policy.minimum_baseline_score:
        return EndpointRefinementResult(
            policy_version=ENDPOINT_REFINER_POLICY_VERSION,
            authority=ENDPOINT_REFINER_AUTHORITY,
            boundary_kind=boundary_kind,
            available=False,
            initial_boundary_ms=initial,
            refined_boundary_ms=initial,
            search_interval_ms=(low, high),
            baseline_score=baseline,
            selected_score=baseline,
            acceptance_floor=floor,
            shift_ms=0,
            uncertainty_ms=high - low,
            score_call_count=calls,
            stop_reason="baseline_target_score_below_minimum",
        )

    if boundary_kind == "start":
        max_candidate = min(high, initial + policy.max_boundary_shift_ms)
        coarse = _grid_inclusive(initial, max_candidate, policy.coarse_step_ms)

        def coarse_score(candidate: int) -> float:
            return score_visible_interval(candidate, high)

        selected, selected_score, used, first_failed = _run_directional_search(
            candidates=coarse,
            evaluate=coarse_score,
            floor=floor,
            consecutive_failures_to_stop=policy.consecutive_failures_to_stop,
        )
        calls += used
        if selected is None:
            selected = initial
            selected_score = baseline
        refine_low = selected
        refine_high = min(max_candidate, first_failed if first_failed is not None else max_candidate)
        fine_candidates = [
            value
            for value in _grid_inclusive(refine_low, refine_high, policy.precision_ms)
            if value > selected
        ]

        def fine_score(candidate: int) -> float:
            return score_visible_interval(candidate, high)

        if fine_candidates:
            fine_selected, fine_score_value, used, _ = _run_directional_search(
                candidates=fine_candidates,
                evaluate=fine_score,
                floor=floor,
                consecutive_failures_to_stop=1,
            )
            calls += used
            if fine_selected is not None:
                selected = fine_selected
                selected_score = fine_score_value
    else:
        min_candidate = max(low, initial - policy.max_boundary_shift_ms)
        coarse = list(reversed(_grid_inclusive(min_candidate, initial, policy.coarse_step_ms)))

        def coarse_score(candidate: int) -> float:
            return score_visible_interval(low, candidate)

        selected, selected_score, used, first_failed = _run_directional_search(
            candidates=coarse,
            evaluate=coarse_score,
            floor=floor,
            consecutive_failures_to_stop=policy.consecutive_failures_to_stop,
        )
        calls += used
        if selected is None:
            selected = initial
            selected_score = baseline
        refine_low = max(min_candidate, first_failed if first_failed is not None else min_candidate)
        refine_high = selected
        fine_candidates = [
            value
            for value in reversed(_grid_inclusive(refine_low, refine_high, policy.precision_ms))
            if value < selected
        ]

        def fine_score(candidate: int) -> float:
            return score_visible_interval(low, candidate)

        if fine_candidates:
            fine_selected, fine_score_value, used, _ = _run_directional_search(
                candidates=fine_candidates,
                evaluate=fine_score,
                floor=floor,
                consecutive_failures_to_stop=1,
            )
            calls += used
            if fine_selected is not None:
                selected = fine_selected
                selected_score = fine_score_value

    assert selected_score is not None
    shift = abs(int(selected) - initial)
    return EndpointRefinementResult(
        policy_version=ENDPOINT_REFINER_POLICY_VERSION,
        authority=ENDPOINT_REFINER_AUTHORITY,
        boundary_kind=boundary_kind,
        available=True,
        initial_boundary_ms=initial,
        refined_boundary_ms=int(selected),
        search_interval_ms=(low, high),
        baseline_score=baseline,
        selected_score=float(selected_score),
        acceptance_floor=floor,
        shift_ms=shift,
        uncertainty_ms=max(policy.precision_ms, policy.coarse_step_ms // 2),
        score_call_count=calls,
        stop_reason=(
            "refined_with_target_score_preserved"
            if shift >= policy.precision_ms
            else "no_material_refinement"
        ),
    )


__all__ = [
    "ENDPOINT_REFINER_POLICY_VERSION",
    "ENDPOINT_REFINER_AUTHORITY",
    "EndpointRefinementError",
    "EndpointRefinementPolicy",
    "EndpointRefinementResult",
    "endpoint_search_interval_from_alignment",
    "refine_boundary_by_masked_score",
]
