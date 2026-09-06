"""Global constrained selection of subtitle boundary candidates.

Per-boundary confidence is not enough to produce a coherent subtitle timeline.
A locally attractive timestamp can create a too-short cue, reverse chronology, or
an unapproved overlap.  This module solves a small dynamic-programming problem over
N-best boundary candidates and chooses the minimum expected-loss path that obeys
explicit temporal constraints.

It is evaluation-only in its first revision.  The selected path is a proposal; a
production materializer must separately verify the candidate provenance and the
appropriate precise/risk authority before changing SRT timing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Iterable, Sequence


BOUNDARY_SEQUENCE_OPTIMIZER_VERSION = "1.0"
BOUNDARY_SEQUENCE_OPTIMIZER_AUTHORITY = "evaluation_only_global_constraint_proposal"


class BoundarySequenceOptimizationError(ValueError):
    pass


@dataclass(frozen=True)
class BoundaryCandidate:
    candidate_id: str
    boundary_ms: int
    expected_loss_ms: float
    source: str
    authority_tier: str
    is_editor: bool = False
    automatic_eligible: bool = False

    def validate(self) -> None:
        if not self.candidate_id.strip() or not self.source.strip() or not self.authority_tier.strip():
            raise BoundarySequenceOptimizationError("candidate identity/source/tier must be non-empty")
        if self.boundary_ms < 0:
            raise BoundarySequenceOptimizationError("candidate boundary_ms must be nonnegative")
        if not math.isfinite(self.expected_loss_ms) or self.expected_loss_ms < 0.0:
            raise BoundarySequenceOptimizationError("candidate expected_loss_ms must be finite/nonnegative")


@dataclass(frozen=True)
class BoundaryNode:
    node_id: str
    boundary_kind: str
    candidates: tuple[BoundaryCandidate, ...]
    minimum_delta_from_previous_ms: int = 0
    maximum_delta_from_previous_ms: int | None = None
    lower_bound_ms: int | None = None
    upper_bound_ms: int | None = None
    structural_lock_to_editor: bool = False

    def validate(self) -> None:
        if not self.node_id.strip():
            raise BoundarySequenceOptimizationError("node_id must be non-empty")
        if self.boundary_kind not in {"start", "end", "internal", "generic"}:
            raise BoundarySequenceOptimizationError("unsupported boundary_kind")
        if not self.candidates:
            raise BoundarySequenceOptimizationError("boundary node must contain candidates")
        ids: set[str] = set()
        for candidate in self.candidates:
            candidate.validate()
            if candidate.candidate_id in ids:
                raise BoundarySequenceOptimizationError("candidate ids must be unique within a node")
            ids.add(candidate.candidate_id)
        if self.minimum_delta_from_previous_ms < 0:
            raise BoundarySequenceOptimizationError("minimum delta must be nonnegative")
        if self.maximum_delta_from_previous_ms is not None:
            if self.maximum_delta_from_previous_ms < self.minimum_delta_from_previous_ms:
                raise BoundarySequenceOptimizationError("maximum delta cannot be below minimum delta")
        if self.lower_bound_ms is not None and self.lower_bound_ms < 0:
            raise BoundarySequenceOptimizationError("lower bound must be nonnegative")
        if self.upper_bound_ms is not None and self.upper_bound_ms < 0:
            raise BoundarySequenceOptimizationError("upper bound must be nonnegative")
        if (
            self.lower_bound_ms is not None
            and self.upper_bound_ms is not None
            and self.upper_bound_ms < self.lower_bound_ms
        ):
            raise BoundarySequenceOptimizationError("node absolute bounds are inverted")
        if self.structural_lock_to_editor and not any(candidate.is_editor for candidate in self.candidates):
            raise BoundarySequenceOptimizationError("structural lock requires an editor candidate")


@dataclass(frozen=True)
class BoundarySequenceOptimizationResult:
    version: str
    authority: str
    feasible: bool
    selected_candidate_ids: tuple[str, ...]
    selected_boundaries_ms: tuple[int, ...]
    total_expected_loss_ms: float | None
    non_editor_selection_count: int
    automatic_eligible_selection_count: int
    node_count: int
    reason: str
    automatic_mutation_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["selected_candidate_ids"] = list(self.selected_candidate_ids)
        payload["selected_boundaries_ms"] = list(self.selected_boundaries_ms)
        return payload


def _candidate_allowed_by_node(candidate: BoundaryCandidate, node: BoundaryNode) -> bool:
    if node.structural_lock_to_editor and not candidate.is_editor:
        return False
    if node.lower_bound_ms is not None and candidate.boundary_ms < node.lower_bound_ms:
        return False
    if node.upper_bound_ms is not None and candidate.boundary_ms > node.upper_bound_ms:
        return False
    return True


def _transition_allowed(previous: BoundaryCandidate, current: BoundaryCandidate, node: BoundaryNode) -> bool:
    delta = current.boundary_ms - previous.boundary_ms
    if delta < node.minimum_delta_from_previous_ms:
        return False
    if node.maximum_delta_from_previous_ms is not None and delta > node.maximum_delta_from_previous_ms:
        return False
    return True


def _path_key(
    *,
    total_loss: float,
    non_editor_count: int,
    total_editor_shift_ms: int,
    path_candidate_ids: tuple[str, ...],
) -> tuple[Any, ...]:
    """Deterministic tie-break: lower loss, less churn, less shift, stable ids."""

    return (
        round(float(total_loss), 9),
        int(non_editor_count),
        int(total_editor_shift_ms),
        path_candidate_ids,
    )


def optimize_boundary_sequence(nodes: Sequence[BoundaryNode]) -> BoundarySequenceOptimizationResult:
    """Select a globally feasible minimum-loss path through boundary candidates."""

    if not nodes:
        raise BoundarySequenceOptimizationError("optimizer requires at least one node")
    seen_nodes: set[str] = set()
    for node in nodes:
        node.validate()
        if node.node_id in seen_nodes:
            raise BoundarySequenceOptimizationError("node ids must be unique")
        seen_nodes.add(node.node_id)

    # State: candidate index -> (loss, non_editor_count, editor_shift, ids, indices)
    states: dict[int, tuple[float, int, int, tuple[str, ...], tuple[int, ...]]] = {}
    first = nodes[0]
    editor_times = [
        next((candidate.boundary_ms for candidate in node.candidates if candidate.is_editor), None)
        for node in nodes
    ]
    for index, candidate in enumerate(first.candidates):
        if not _candidate_allowed_by_node(candidate, first):
            continue
        editor_time = editor_times[0]
        shift = 0 if editor_time is None else abs(candidate.boundary_ms - editor_time)
        states[index] = (
            float(candidate.expected_loss_ms),
            0 if candidate.is_editor else 1,
            shift,
            (candidate.candidate_id,),
            (index,),
        )

    if not states:
        return BoundarySequenceOptimizationResult(
            version=BOUNDARY_SEQUENCE_OPTIMIZER_VERSION,
            authority=BOUNDARY_SEQUENCE_OPTIMIZER_AUTHORITY,
            feasible=False,
            selected_candidate_ids=(),
            selected_boundaries_ms=(),
            total_expected_loss_ms=None,
            non_editor_selection_count=0,
            automatic_eligible_selection_count=0,
            node_count=len(nodes),
            reason="no_candidate_survives_first_node_constraints",
        )

    for node_index in range(1, len(nodes)):
        previous_node = nodes[node_index - 1]
        node = nodes[node_index]
        next_states: dict[int, tuple[float, int, int, tuple[str, ...], tuple[int, ...]]] = {}
        editor_time = editor_times[node_index]
        for current_index, current in enumerate(node.candidates):
            if not _candidate_allowed_by_node(current, node):
                continue
            best_state = None
            best_key = None
            for previous_index, state in states.items():
                previous = previous_node.candidates[previous_index]
                if not _transition_allowed(previous, current, node):
                    continue
                loss, non_editor_count, editor_shift, ids, indices = state
                new_loss = loss + float(current.expected_loss_ms)
                new_non_editor = non_editor_count + (0 if current.is_editor else 1)
                new_shift = editor_shift + (
                    0 if editor_time is None else abs(current.boundary_ms - editor_time)
                )
                new_ids = (*ids, current.candidate_id)
                new_indices = (*indices, current_index)
                key = _path_key(
                    total_loss=new_loss,
                    non_editor_count=new_non_editor,
                    total_editor_shift_ms=new_shift,
                    path_candidate_ids=new_ids,
                )
                if best_key is None or key < best_key:
                    best_key = key
                    best_state = (new_loss, new_non_editor, new_shift, new_ids, new_indices)
            if best_state is not None:
                next_states[current_index] = best_state
        states = next_states
        if not states:
            return BoundarySequenceOptimizationResult(
                version=BOUNDARY_SEQUENCE_OPTIMIZER_VERSION,
                authority=BOUNDARY_SEQUENCE_OPTIMIZER_AUTHORITY,
                feasible=False,
                selected_candidate_ids=(),
                selected_boundaries_ms=(),
                total_expected_loss_ms=None,
                non_editor_selection_count=0,
                automatic_eligible_selection_count=0,
                node_count=len(nodes),
                reason=f"no_globally_feasible_path_at_node:{node.node_id}",
            )

    best = min(
        states.values(),
        key=lambda state: _path_key(
            total_loss=state[0],
            non_editor_count=state[1],
            total_editor_shift_ms=state[2],
            path_candidate_ids=state[3],
        ),
    )
    total_loss, non_editor_count, _editor_shift, ids, indices = best
    selected: list[BoundaryCandidate] = [
        nodes[node_index].candidates[candidate_index]
        for node_index, candidate_index in enumerate(indices)
    ]
    return BoundarySequenceOptimizationResult(
        version=BOUNDARY_SEQUENCE_OPTIMIZER_VERSION,
        authority=BOUNDARY_SEQUENCE_OPTIMIZER_AUTHORITY,
        feasible=True,
        selected_candidate_ids=ids,
        selected_boundaries_ms=tuple(candidate.boundary_ms for candidate in selected),
        total_expected_loss_ms=float(total_loss),
        non_editor_selection_count=non_editor_count,
        automatic_eligible_selection_count=sum(candidate.automatic_eligible for candidate in selected),
        node_count=len(nodes),
        reason="minimum_expected_loss_path_under_explicit_temporal_constraints",
    )


def node(
    node_id: str,
    boundary_kind: str,
    candidates: Iterable[BoundaryCandidate],
    **kwargs: Any,
) -> BoundaryNode:
    """Small convenience constructor that freezes candidate iterables."""

    return BoundaryNode(
        node_id=node_id,
        boundary_kind=boundary_kind,
        candidates=tuple(candidates),
        **kwargs,
    )


__all__ = [
    "BOUNDARY_SEQUENCE_OPTIMIZER_VERSION",
    "BOUNDARY_SEQUENCE_OPTIMIZER_AUTHORITY",
    "BoundarySequenceOptimizationError",
    "BoundaryCandidate",
    "BoundaryNode",
    "BoundarySequenceOptimizationResult",
    "optimize_boundary_sequence",
    "node",
]
