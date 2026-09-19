"""Canonical structural-scenario labels for private calibration/blind datasets.

The labels are evaluation metadata only. They do not grant timing authority and
must never be inferred from prediction output. Missing metadata remains
``unspecified`` so legacy datasets stay readable without fabricating truth.
"""

from __future__ import annotations

from typing import Any


UNSPECIFIED_STRUCTURAL_SCENARIO = "unspecified"
STRUCTURAL_SCENARIOS = frozenset(
    {
        "none",
        "hard_cut",
        "same_track_splice",
        "crossfade",
        "true_overlap",
        "sequential_transition",
        "piecewise_rate",
        "reorder",
        "detached_tail",
    }
)


class StructuralScenarioError(ValueError):
    """Raised when structural benchmark metadata is malformed."""


def structural_scenarios(case: dict[str, Any]) -> tuple[str, ...]:
    """Return validated structural scenario labels for one dataset case."""

    raw = case.get("structural_scenarios")
    if raw is None:
        return (UNSPECIFIED_STRUCTURAL_SCENARIO,)
    if not isinstance(raw, list) or not raw:
        raise StructuralScenarioError("structural_scenarios must be a non-empty list")

    values: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if not isinstance(value, str):
            raise StructuralScenarioError("structural_scenarios values must be strings")
        label = value.strip()
        if label not in STRUCTURAL_SCENARIOS:
            raise StructuralScenarioError(
                f"unsupported structural scenario {value!r}; expected one of "
                + ", ".join(sorted(STRUCTURAL_SCENARIOS))
            )
        if label in seen:
            raise StructuralScenarioError(f"duplicate structural scenario {label!r}")
        seen.add(label)
        values.append(label)

    if "none" in seen and len(seen) != 1:
        raise StructuralScenarioError(
            "structural scenario 'none' cannot be combined with another scenario"
        )
    return tuple(sorted(values))
