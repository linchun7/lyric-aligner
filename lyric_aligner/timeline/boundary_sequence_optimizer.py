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
from typing import Any, Iterable, Mapping, Sequence


BOUNDARY_SEQUENCE_OPTIMIZER_VERSION = "1.0"
BOUNDARY_SEQUENCE_OPTIMIZER_AUTHORITY = "evaluation_only_global_constraint_proposal"
INTERVAL_SEQUENCE_OPTIMIZER_VERSION = "1.0"
INTERVAL_SEQUENCE_OPTIMIZER_AUTHORITY = "evaluation_only_interval_constraint_proposal"


class BoundarySequenceOptimizationError(ValueError):
    pass


@dataclass(frozen=True)
class IntervalCandidate:
    """One inseparable timing proposal for one displayed interval.

    ``start_ms`` and ``end_ms`` deliberately live on the same candidate.  An
    interval selector must never take the start from one lyric occurrence and
    the end from another.  ``selection_cost`` is an experimental ranking score
    only; unlike :class:`BoundaryCandidate.expected_loss_ms`, it has no unit or
    calibrated-error interpretation.
    """

    candidate_id: str
    start_ms: int
    end_ms: int
    lane_id: str
    occurrence_path_id: str
    map_path_id: str
    is_baseline: bool
    selection_cost: float

    def validate(self) -> None:
        identities = (
            self.candidate_id,
            self.lane_id,
            self.occurrence_path_id,
            self.map_path_id,
        )
        if any(not isinstance(value, str) or not value.strip() for value in identities):
            raise BoundarySequenceOptimizationError(
                "interval candidate identity/lane/occurrence/map paths must be non-empty strings"
            )
        if type(self.start_ms) is not int or type(self.end_ms) is not int:
            raise BoundarySequenceOptimizationError("interval candidate endpoints must be integer milliseconds")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise BoundarySequenceOptimizationError("interval candidate must have a positive nonnegative interval")
        if type(self.is_baseline) is not bool:
            raise BoundarySequenceOptimizationError("interval candidate is_baseline must be boolean")
        if (
            isinstance(self.selection_cost, bool)
            or not isinstance(self.selection_cost, (int, float))
            or not math.isfinite(self.selection_cost)
            or self.selection_cost < 0.0
        ):
            raise BoundarySequenceOptimizationError("interval selection_cost must be finite/nonnegative")


@dataclass(frozen=True)
class IntervalNode:
    """Candidates for one SRT interval.

    Nodes in the same ``continuity_group_id`` may only be selected from one
    occurrence/map path pair.  ``None`` explicitly means no cross-node path
    assertion is available for this node.
    """

    node_id: str
    candidates: tuple[IntervalCandidate, ...]
    continuity_group_id: str | None = None

    def validate(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise BoundarySequenceOptimizationError("interval node_id must be a non-empty string")
        if self.continuity_group_id is not None and (
            not isinstance(self.continuity_group_id, str) or not self.continuity_group_id.strip()
        ):
            raise BoundarySequenceOptimizationError(
                "interval continuity_group_id must be a non-empty string or None"
            )
        if not self.candidates:
            raise BoundarySequenceOptimizationError("interval node must contain candidates")
        candidate_ids: set[str] = set()
        baseline_count = 0
        for candidate in self.candidates:
            candidate.validate()
            if candidate.candidate_id in candidate_ids:
                raise BoundarySequenceOptimizationError(
                    "interval candidate ids must be unique within a node"
                )
            candidate_ids.add(candidate.candidate_id)
            baseline_count += int(candidate.is_baseline)
        if baseline_count != 1:
            raise BoundarySequenceOptimizationError(
                "interval node must contain exactly one KEEP baseline candidate"
            )


@dataclass(frozen=True)
class IntervalSequenceOptimizationResult:
    """Experimental, read-only interval selection result.

    ``selected_*`` fields are all in *input node order*.  The two
    ``selection_order_*`` fields expose the deterministic internal order so a
    caller never has to infer which selected interval belongs to which source
    row after the optimizer has used an active-frontier traversal.
    """

    version: str
    authority: str
    feasible: bool
    selected_node_ids: tuple[str, ...]
    selected_candidate_ids: tuple[str, ...]
    selected_intervals_ms: tuple[tuple[int, int], ...]
    total_selection_cost: float | None
    baseline_selection_count: int
    node_count: int
    reason: str
    selection_order_node_ids: tuple[str, ...]
    input_order_by_selection_order: tuple[int, ...]
    globally_optimal: bool = True
    automatic_mutation_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["selected_node_ids"] = list(self.selected_node_ids)
        payload["selected_candidate_ids"] = list(self.selected_candidate_ids)
        payload["selected_intervals_ms"] = [list(interval) for interval in self.selected_intervals_ms]
        payload["selection_order_node_ids"] = list(self.selection_order_node_ids)
        payload["input_order_by_selection_order"] = list(self.input_order_by_selection_order)
        return payload


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


def _interval_overlap_ms(first: IntervalCandidate, second: IntervalCandidate) -> int:
    return max(0, min(first.end_ms, second.end_ms) - max(first.start_ms, second.start_ms))


def _interval_pair_allowed(
    *,
    previous: IntervalCandidate,
    current: IntervalCandidate,
    previous_baseline: IntervalCandidate,
    current_baseline: IntervalCandidate,
    same_continuity_group: bool,
) -> bool:
    """Preserve original interval geometry without serializing real overlaps.

    Disjoint baseline intervals retain their direction, which prevents a late
    candidate from silently passing a following cue.  Same-lane or
    same-continuity-group intervals also retain their baseline start order,
    even when their editor intervals overlapped.  This blocks a swap of two
    lyric occurrences while preserving legitimate nested or cross-lane
    overlaps.  When baseline intervals overlap, overlap remains permitted,
    but its duration may not grow.  The latter rule also forbids an unapproved
    new cross-lane overlap because a zero baseline overlap has a zero
    allowance.
    """

    preserve_start_order = (
        previous_baseline.lane_id == current_baseline.lane_id
        or same_continuity_group
    )
    if preserve_start_order:
        if (
            previous_baseline.start_ms < current_baseline.start_ms
            and previous.start_ms > current.start_ms
        ):
            return False
        if (
            current_baseline.start_ms < previous_baseline.start_ms
            and current.start_ms > previous.start_ms
        ):
            return False
    if previous_baseline.end_ms <= current_baseline.start_ms:
        return previous.end_ms <= current.start_ms
    if current_baseline.end_ms <= previous_baseline.start_ms:
        return current.end_ms <= previous.start_ms
    return _interval_overlap_ms(previous, current) <= _interval_overlap_ms(
        previous_baseline, current_baseline
    )


def _interval_result(
    *,
    feasible: bool,
    nodes: Sequence[IntervalNode],
    selection_order: Sequence[int],
    reason: str,
    selected: Sequence[IntervalCandidate] = (),
    total_selection_cost: float | None = None,
    globally_optimal: bool = True,
) -> IntervalSequenceOptimizationResult:
    return IntervalSequenceOptimizationResult(
        version=INTERVAL_SEQUENCE_OPTIMIZER_VERSION,
        authority=INTERVAL_SEQUENCE_OPTIMIZER_AUTHORITY,
        feasible=feasible,
        selected_node_ids=tuple(node.node_id for node in nodes) if feasible else (),
        selected_candidate_ids=tuple(candidate.candidate_id for candidate in selected),
        selected_intervals_ms=tuple((candidate.start_ms, candidate.end_ms) for candidate in selected),
        total_selection_cost=total_selection_cost,
        baseline_selection_count=sum(candidate.is_baseline for candidate in selected),
        node_count=len(nodes),
        reason=reason,
        selection_order_node_ids=tuple(nodes[index].node_id for index in selection_order),
        input_order_by_selection_order=tuple(selection_order),
        globally_optimal=globally_optimal,
    )


def _interval_trace_candidate_indices(
    trace: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    return trace


def _interval_state_tie_key(
    state: tuple[float, int, int, tuple[tuple[int, int], ...]],
    ordered_nodes: Sequence[IntervalNode],
) -> tuple[Any, ...]:
    """Stable deterministic ordering for otherwise equivalent active states."""

    cost, nonbaseline_count, baseline_shift, trace = state
    return (
        round(float(cost), 9),
        int(nonbaseline_count),
        int(baseline_shift),
        tuple(ordered_nodes[position].candidates[candidate_index].candidate_id for position, candidate_index in trace),
    )


def _validate_interval_nodes(nodes: Sequence[IntervalNode]) -> tuple[IntervalCandidate, ...]:
    seen_nodes: set[str] = set()
    seen_candidates: set[str] = set()
    baselines: list[IntervalCandidate] = []
    baseline_paths_by_group: dict[str, tuple[str, str]] = {}
    for node in nodes:
        node.validate()
        if node.node_id in seen_nodes:
            raise BoundarySequenceOptimizationError("interval node ids must be unique")
        seen_nodes.add(node.node_id)
        baseline = next(candidate for candidate in node.candidates if candidate.is_baseline)
        baselines.append(baseline)
        for candidate in node.candidates:
            if candidate.candidate_id in seen_candidates:
                raise BoundarySequenceOptimizationError("interval candidate ids must be globally unique")
            seen_candidates.add(candidate.candidate_id)
        if node.continuity_group_id is not None:
            path = (baseline.occurrence_path_id, baseline.map_path_id)
            prior = baseline_paths_by_group.setdefault(node.continuity_group_id, path)
            if prior != path:
                raise BoundarySequenceOptimizationError(
                    "KEEP baselines in one continuity group must use one occurrence/map path"
                )
    return tuple(baselines)


def _validate_atomic_interval_proposals(
    atomic_proposals: Mapping[str, Sequence[tuple[str, str]]] | None,
    nodes: Sequence[IntervalNode],
    selection_order: Sequence[int],
) -> tuple[dict[int, tuple[tuple[str, int], ...]], dict[str, int]]:
    """Bind optional all-or-none candidate sets to concrete node candidates.

    The proposal contract is deliberately strict: it links existing automatic
    candidates only, every member belongs to a distinct node, and a candidate
    is owned by one proposal at most.  Baseline candidates remain independent
    KEEP options, so rejecting every proposal always leaves the validated
    baseline path available.
    """

    if atomic_proposals is None:
        return {}, {}
    if not isinstance(atomic_proposals, Mapping):
        raise BoundarySequenceOptimizationError("atomic_proposals must be a mapping when provided")

    node_indices = {node.node_id: index for index, node in enumerate(nodes)}
    candidate_indices = {
        node.node_id: {candidate.candidate_id: candidate_index for candidate_index, candidate in enumerate(node.candidates)}
        for node in nodes
    }
    candidate_membership: set[str] = set()
    position_by_input_index = {input_index: position for position, input_index in enumerate(selection_order)}
    members_by_position: dict[int, list[tuple[str, int]]] = {}
    last_position: dict[str, int] = {}

    for proposal_id, raw_members in sorted(atomic_proposals.items(), key=lambda item: str(item[0])):
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            raise BoundarySequenceOptimizationError("atomic proposal id must be a non-empty string")
        if isinstance(raw_members, (str, bytes)) or not isinstance(raw_members, Sequence) or len(raw_members) < 2:
            raise BoundarySequenceOptimizationError("atomic proposal must contain at least two members")
        seen_nodes: set[str] = set()
        proposal_positions: list[int] = []
        for raw_member in raw_members:
            if (not isinstance(raw_member, (tuple, list)) or len(raw_member) != 2
                    or not isinstance(raw_member[0], str) or not raw_member[0].strip()
                    or not isinstance(raw_member[1], str) or not raw_member[1].strip()):
                raise BoundarySequenceOptimizationError(
                    "atomic proposal member must be a (node_id, candidate_id) pair"
                )
            node_id, candidate_id = raw_member
            if node_id in seen_nodes:
                raise BoundarySequenceOptimizationError("atomic proposal cannot contain multiple candidates from one node")
            seen_nodes.add(node_id)
            node_index = node_indices.get(node_id)
            if node_index is None:
                raise BoundarySequenceOptimizationError("atomic proposal references an unknown node")
            candidate_index = candidate_indices[node_id].get(candidate_id)
            if candidate_index is None:
                raise BoundarySequenceOptimizationError("atomic proposal references an unknown candidate")
            candidate = nodes[node_index].candidates[candidate_index]
            if candidate.is_baseline:
                raise BoundarySequenceOptimizationError("atomic proposal cannot reference a KEEP baseline candidate")
            if candidate_id in candidate_membership:
                raise BoundarySequenceOptimizationError("atomic proposal candidate belongs to multiple proposals")
            candidate_membership.add(candidate_id)
            position = position_by_input_index[node_index]
            proposal_positions.append(position)
            members_by_position.setdefault(position, []).append((proposal_id, candidate_index))
        last_position[proposal_id] = max(proposal_positions)

    return (
        {position: tuple(sorted(members, key=lambda item: item[0]))
         for position, members in members_by_position.items()},
        last_position,
    )


def optimize_interval_sequence(
    nodes: Sequence[IntervalNode],
    *,
    max_active_states: int | None = 4096,
    atomic_proposals: Mapping[str, Sequence[tuple[str, str]]] | None = None,
) -> IntervalSequenceOptimizationResult:
    """Choose compatible whole-interval candidates with an exact frontier DP.

    This is experimental and evaluation-only.  It never confers write
    authority.  A node has exactly one ``is_baseline`` candidate, and the
    complete baseline path is checked up front, so KEEP is always a feasible
    path whenever this function returns a selection.  The optional state limit
    is a fail-closed resource guard: if it is reached, the function returns
    the complete KEEP baseline with ``globally_optimal=False`` instead of a
    beam result mislabeled as a global optimum.
    """

    if not nodes:
        raise BoundarySequenceOptimizationError("interval optimizer requires at least one node")
    if max_active_states is not None and (
        type(max_active_states) is not int or max_active_states <= 0
    ):
        raise BoundarySequenceOptimizationError("max_active_states must be a positive integer or None")

    baselines = _validate_interval_nodes(nodes)
    input_order = tuple(range(len(nodes)))
    # Baseline timing produces the only ordering that the selector is allowed
    # to use for traversal.  Candidate alternatives are checked against every
    # still-relevant earlier interval, including any nested interval.
    selection_order = tuple(
        sorted(
            input_order,
            key=lambda index: (
                baselines[index].start_ms,
                baselines[index].end_ms,
                nodes[index].node_id,
                index,
            ),
        )
    )
    ordered_nodes = tuple(nodes[index] for index in selection_order)
    ordered_baselines = tuple(baselines[index] for index in selection_order)
    atomic_members_by_position, atomic_last_position = _validate_atomic_interval_proposals(
        atomic_proposals, nodes, selection_order
    )

    group_last_position: dict[str, int] = {}
    for position, node_value in enumerate(ordered_nodes):
        if node_value.continuity_group_id is not None:
            group_last_position[node_value.continuity_group_id] = position

    # An active interval can only constrain a later choice if a future
    # candidate can still start before it ends.  The suffix minimum keeps true
    # cross-lane/nested geometry while allowing distant settled cues to merge.
    suffix_min_candidate_start: list[float] = [float("inf")] * (len(ordered_nodes) + 1)
    for position in range(len(ordered_nodes) - 1, -1, -1):
        suffix_min_candidate_start[position] = min(
            min(candidate.start_ms for candidate in ordered_nodes[position].candidates),
            suffix_min_candidate_start[position + 1],
        )

    # key = (active (position, candidate-index) pairs, active continuity paths,
    #        active atomic all-or-none decisions)
    # value = (cost, nonbaseline count, movement from baseline, trace)
    states: dict[
        tuple[
            tuple[tuple[int, int], ...],
            tuple[tuple[str, str, str], ...],
            tuple[tuple[str, bool], ...],
        ],
        tuple[float, int, int, tuple[tuple[int, int], ...]],
    ] = {((), (), ()): (0.0, 0, 0, ())}

    for position, node_value in enumerate(ordered_nodes):
        next_states: dict[
            tuple[
                tuple[tuple[int, int], ...],
                tuple[tuple[str, str, str], ...],
                tuple[tuple[str, bool], ...],
            ],
            tuple[float, int, int, tuple[tuple[int, int], ...]],
        ] = {}
        baseline = ordered_baselines[position]
        group = node_value.continuity_group_id
        for state_key, state in states.items():
            active, group_paths, atomic_decisions = state_key
            cost, nonbaseline_count, baseline_shift, trace = state
            paths = dict((group_id, (occurrence_path_id, map_path_id)) for group_id, occurrence_path_id, map_path_id in group_paths)
            for candidate_index, candidate in enumerate(node_value.candidates):
                next_atomic = dict(atomic_decisions)
                atomic_allowed = True
                for proposal_id, member_candidate_index in atomic_members_by_position.get(position, ()):
                    decision = next_atomic.get(proposal_id)
                    is_member = candidate_index == member_candidate_index
                    if (decision is True and not is_member) or (decision is False and is_member):
                        atomic_allowed = False
                        break
                    if decision is None:
                        # At a proposal's first encountered member, choosing
                        # it starts an all-member commitment; choosing any
                        # other candidate rejects all later members.
                        next_atomic[proposal_id] = is_member
                if not atomic_allowed:
                    continue
                if group is not None:
                    candidate_path = (candidate.occurrence_path_id, candidate.map_path_id)
                    assigned_path = paths.get(group)
                    if assigned_path is not None and assigned_path != candidate_path:
                        continue

                allowed = True
                for previous_position, previous_candidate_index in active:
                    previous = ordered_nodes[previous_position].candidates[previous_candidate_index]
                    if not _interval_pair_allowed(
                        previous=previous,
                        current=candidate,
                        previous_baseline=ordered_baselines[previous_position],
                        current_baseline=baseline,
                        same_continuity_group=(
                            ordered_nodes[previous_position].continuity_group_id is not None
                            and ordered_nodes[previous_position].continuity_group_id
                            == node_value.continuity_group_id
                        ),
                    ):
                        allowed = False
                        break
                if not allowed:
                    continue

                next_paths = dict(paths)
                if group is not None:
                    next_paths[group] = (candidate.occurrence_path_id, candidate.map_path_id)
                    if group_last_position[group] == position:
                        del next_paths[group]

                # Once a proposal's final baseline-ordered member has been
                # processed, its outcome has already been enforced and no
                # longer needs to increase frontier state cardinality.
                for proposal_id, _member_candidate_index in atomic_members_by_position.get(position, ()):
                    if atomic_last_position[proposal_id] == position:
                        del next_atomic[proposal_id]

                combined_active = (*active, (position, candidate_index))
                next_active = tuple(
                    active_entry
                    for active_entry in combined_active
                    if ordered_nodes[active_entry[0]].candidates[active_entry[1]].end_ms
                    > suffix_min_candidate_start[position + 1]
                )
                next_group_paths = tuple(
                    sorted(
                        (group_id, path[0], path[1])
                        for group_id, path in next_paths.items()
                    )
                )
                next_key = (
                    next_active,
                    next_group_paths,
                    tuple(sorted(next_atomic.items())),
                )
                next_state = (
                    cost + float(candidate.selection_cost),
                    nonbaseline_count + int(not candidate.is_baseline),
                    baseline_shift
                    + abs(candidate.start_ms - baseline.start_ms)
                    + abs(candidate.end_ms - baseline.end_ms),
                    (*trace, (position, candidate_index)),
                )
                prior = next_states.get(next_key)
                if prior is None or _interval_state_tie_key(next_state, ordered_nodes) < _interval_state_tie_key(
                    prior, ordered_nodes
                ):
                    next_states[next_key] = next_state
        if not next_states:
            # This should be unreachable because validated baselines preserve
            # both their timing geometry and group path.  Preserve a readable
            # result rather than silently choosing a partial path.
            return _interval_result(
                feasible=False,
                nodes=nodes,
                selection_order=selection_order,
                reason=f"no_globally_feasible_interval_path_at_node:{node_value.node_id}",
            )
        if max_active_states is not None and len(next_states) > max_active_states:
            return _interval_result(
                feasible=True,
                nodes=nodes,
                selection_order=selection_order,
                reason="active_state_limit_exceeded_keep_baseline_not_globally_optimized",
                selected=baselines,
                total_selection_cost=float(sum(candidate.selection_cost for candidate in baselines)),
                globally_optimal=False,
            )
        states = next_states

    best = min(states.values(), key=lambda state: _interval_state_tie_key(state, ordered_nodes))
    total_cost, _nonbaseline_count, _baseline_shift, trace = best
    selected_by_input: list[IntervalCandidate | None] = [None] * len(nodes)
    for position, candidate_index in _interval_trace_candidate_indices(trace):
        selected_by_input[selection_order[position]] = ordered_nodes[position].candidates[candidate_index]
    if any(candidate is None for candidate in selected_by_input):
        raise AssertionError("interval optimizer lost a selected node")
    selected = tuple(candidate for candidate in selected_by_input if candidate is not None)
    return _interval_result(
        feasible=True,
        nodes=nodes,
        selection_order=selection_order,
        reason="minimum_selection_cost_path_under_path_and_interval_constraints",
        selected=selected,
        total_selection_cost=float(total_cost),
    )


def interval_node(
    node_id: str,
    candidates: Iterable[IntervalCandidate],
    *,
    continuity_group_id: str | None = None,
) -> IntervalNode:
    """Convenience constructor that freezes interval candidate iterables."""

    return IntervalNode(
        node_id=node_id,
        candidates=tuple(candidates),
        continuity_group_id=continuity_group_id,
    )


__all__ = [
    "BOUNDARY_SEQUENCE_OPTIMIZER_VERSION",
    "BOUNDARY_SEQUENCE_OPTIMIZER_AUTHORITY",
    "BoundarySequenceOptimizationError",
    "INTERVAL_SEQUENCE_OPTIMIZER_VERSION",
    "INTERVAL_SEQUENCE_OPTIMIZER_AUTHORITY",
    "BoundaryCandidate",
    "BoundaryNode",
    "BoundarySequenceOptimizationResult",
    "IntervalCandidate",
    "IntervalNode",
    "IntervalSequenceOptimizationResult",
    "optimize_boundary_sequence",
    "optimize_interval_sequence",
    "node",
    "interval_node",
]
