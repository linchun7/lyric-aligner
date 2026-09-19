"""Conservative character correspondence from a globally anchored edit path.

This module intentionally aligns only two complete strings.  It has no audio,
time, language, or lyric authority.  Its purpose is narrower: after callers
have fixed both textual anchors, identify character correspondences that are
invariant across *all* sufficiently good global Levenshtein paths.

An arbitrary optimal traceback is useful for diagnostics, but is not evidence
that a repeated character has a unique correspondence.  ``align_anchored_characters``
therefore returns an observed index only when every path within the requested
edit slack uses the same ``equal`` edge for that canonical character.
"""

from __future__ import annotations

from typing import Any


def _validate_inputs(canonical: str, observed: str, max_cells: int, ambiguity_slack: int) -> None:
    if not isinstance(canonical, str):
        raise TypeError("canonical must be a string")
    if not isinstance(observed, str):
        raise TypeError("observed must be a string")
    if isinstance(max_cells, bool) or not isinstance(max_cells, int) or max_cells < 1:
        raise ValueError("max_cells must be a positive integer")
    if isinstance(ambiguity_slack, bool) or not isinstance(ambiguity_slack, int) or ambiguity_slack < 0:
        raise ValueError("ambiguity_slack must be a non-negative integer")


def _forward_costs(canonical: str, observed: str) -> list[list[int]]:
    """Return minimum prefix costs at every edit-graph vertex."""

    canonical_length = len(canonical)
    observed_length = len(observed)
    costs = [[0] * (observed_length + 1) for _ in range(canonical_length + 1)]
    for canonical_index in range(1, canonical_length + 1):
        costs[canonical_index][0] = canonical_index
    for observed_index in range(1, observed_length + 1):
        costs[0][observed_index] = observed_index

    for canonical_index in range(1, canonical_length + 1):
        canonical_character = canonical[canonical_index - 1]
        for observed_index in range(1, observed_length + 1):
            substitution_cost = 0 if canonical_character == observed[observed_index - 1] else 1
            costs[canonical_index][observed_index] = min(
                costs[canonical_index - 1][observed_index] + 1,
                costs[canonical_index][observed_index - 1] + 1,
                costs[canonical_index - 1][observed_index - 1] + substitution_cost,
            )
    return costs


def _backward_costs(canonical: str, observed: str) -> list[list[int]]:
    """Return minimum suffix costs at every edit-graph vertex."""

    canonical_length = len(canonical)
    observed_length = len(observed)
    costs = [[0] * (observed_length + 1) for _ in range(canonical_length + 1)]
    for canonical_index in range(canonical_length - 1, -1, -1):
        costs[canonical_index][observed_length] = canonical_length - canonical_index
    for observed_index in range(observed_length - 1, -1, -1):
        costs[canonical_length][observed_index] = observed_length - observed_index

    for canonical_index in range(canonical_length - 1, -1, -1):
        canonical_character = canonical[canonical_index]
        for observed_index in range(observed_length - 1, -1, -1):
            substitution_cost = 0 if canonical_character == observed[observed_index] else 1
            costs[canonical_index][observed_index] = min(
                costs[canonical_index + 1][observed_index] + 1,
                costs[canonical_index][observed_index + 1] + 1,
                costs[canonical_index + 1][observed_index + 1] + substitution_cost,
            )
    return costs


def _diagnostic_trace(canonical: str, observed: str, backward: list[list[int]]) -> list[dict[str, Any]]:
    """Choose one stable minimum-cost trace for explanation only.

    Equal/substitute diagonals win ties, followed by deletion and insertion.
    The returned trace is explicitly diagnostic: consensus is calculated from
    all admissible paths below, never from this choice.
    """

    trace: list[dict[str, Any]] = []
    canonical_index = 0
    observed_index = 0
    canonical_length = len(canonical)
    observed_length = len(observed)

    while canonical_index < canonical_length or observed_index < observed_length:
        current_cost = backward[canonical_index][observed_index]
        if canonical_index < canonical_length and observed_index < observed_length:
            canonical_character = canonical[canonical_index]
            observed_character = observed[observed_index]
            edge_cost = 0 if canonical_character == observed_character else 1
            if edge_cost + backward[canonical_index + 1][observed_index + 1] == current_cost:
                trace.append(
                    {
                        "operation": "equal" if edge_cost == 0 else "substitute",
                        "canonical_index": canonical_index,
                        "observed_index": observed_index,
                        "canonical_character": canonical_character,
                        "observed_character": observed_character,
                    }
                )
                canonical_index += 1
                observed_index += 1
                continue
        if (
            canonical_index < canonical_length
            and 1 + backward[canonical_index + 1][observed_index] == current_cost
        ):
            trace.append(
                {
                    "operation": "delete",
                    "canonical_index": canonical_index,
                    "observed_index": None,
                    "canonical_character": canonical[canonical_index],
                    "observed_character": None,
                }
            )
            canonical_index += 1
            continue
        if (
            observed_index < observed_length
            and 1 + backward[canonical_index][observed_index + 1] == current_cost
        ):
            trace.append(
                {
                    "operation": "insert",
                    "canonical_index": None,
                    "observed_index": observed_index,
                    "canonical_character": None,
                    "observed_character": observed[observed_index],
                }
            )
            observed_index += 1
            continue
        raise AssertionError("backward edit-cost matrix has no minimum-cost successor")
    return trace


def align_anchored_characters(
    canonical: str,
    observed: str,
    *,
    max_cells: int = 250_000,
    ambiguity_slack: int = 1,
) -> dict[str, Any]:
    """Find conservative character-level equal-edge consensus.

    ``canonical`` and ``observed`` are treated as sequences of Python Unicode
    characters without normalization.  The edit graph is globally anchored at
    ``(0, 0)`` and ``(len(canonical), len(observed))``.  Edge costs are equal
    ``0`` and substitution, insertion, deletion ``1``.

    The result contains:

    ``minimum_edit_distance``
        The global Levenshtein distance, or ``None`` when bounded before DP.
    ``consensus_observed_indices``
        One entry per canonical character.  An entry is an observed index only
        if every complete path with total cost at most
        ``minimum_edit_distance + ambiguity_slack`` uses the same equal edge.
        Otherwise it is ``None``.
    ``character_states``
        The complete local evidence for each entry: equal edges that occur on
        an admissible complete path and whether a delete/substitute edge also
        occurs on one.  This makes ambiguity auditable without treating the
        diagnostic trace as a correspondence claim.
    ``diagnostic_trace``
        A deterministic *minimum-cost* traceback, suitable for inspection but
        never used to decide the consensus mapping.

    A product of string lengths above ``max_cells`` returns
    ``status == 'too_large'`` before allocating dynamic-programming matrices.
    """

    _validate_inputs(canonical, observed, max_cells, ambiguity_slack)
    matrix_cells = (len(canonical) + 1) * (len(observed) + 1)
    common: dict[str, Any] = {
        "canonical": canonical,
        "observed": observed,
        "ambiguity_slack": ambiguity_slack,
        "max_cells": max_cells,
        "matrix_cells": matrix_cells,
    }
    if matrix_cells > max_cells:
        return {
            **common,
            "status": "too_large",
            "minimum_edit_distance": None,
            "consensus_observed_indices": None,
            "character_states": None,
            "diagnostic_trace": None,
        }

    forward = _forward_costs(canonical, observed)
    backward = _backward_costs(canonical, observed)
    minimum_edit_distance = forward[-1][-1]
    if backward[0][0] != minimum_edit_distance:
        raise AssertionError("forward and backward edit-cost matrices disagree")
    admissible_cost = minimum_edit_distance + ambiguity_slack

    character_states: list[dict[str, Any]] = []
    consensus_observed_indices: list[int | None] = []
    for canonical_index, canonical_character in enumerate(canonical):
        equal_observed_indices: list[int] = []
        can_be_non_equal = False
        for observed_index, observed_character in enumerate(observed):
            prefix_cost = forward[canonical_index][observed_index]
            if canonical_character == observed_character:
                if prefix_cost + backward[canonical_index + 1][observed_index + 1] <= admissible_cost:
                    equal_observed_indices.append(observed_index)
            elif prefix_cost + 1 + backward[canonical_index + 1][observed_index + 1] <= admissible_cost:
                can_be_non_equal = True
        for observed_index in range(len(observed) + 1):
            if forward[canonical_index][observed_index] + 1 + backward[canonical_index + 1][observed_index] <= admissible_cost:
                can_be_non_equal = True
                break

        consensus_observed_index = (
            equal_observed_indices[0]
            if len(equal_observed_indices) == 1 and not can_be_non_equal
            else None
        )
        consensus_observed_indices.append(consensus_observed_index)
        character_states.append(
            {
                "canonical_index": canonical_index,
                "equal_observed_indices": equal_observed_indices,
                "can_be_non_equal": can_be_non_equal,
                "consensus_observed_index": consensus_observed_index,
            }
        )

    return {
        **common,
        "status": "ok",
        "minimum_edit_distance": minimum_edit_distance,
        "consensus_observed_indices": consensus_observed_indices,
        "character_states": character_states,
        "diagnostic_trace": _diagnostic_trace(canonical, observed, backward),
    }


__all__ = ["align_anchored_characters"]
