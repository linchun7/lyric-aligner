"""Shared fail-closed projection-domain contract for acoustic evidence readers."""
from __future__ import annotations

from typing import Any, Mapping


def projection_domain_qualified(row: Mapping[str, Any] | None) -> bool:
    """Require an explicit grant consistent with the actual query coordinates."""
    if row is None or row.get("projection_within_mix_window") is not True:
        return False
    distance = row.get("projection_extrapolation_ms")
    predicted = row.get("predicted_mix_start_ms")
    window = row.get("mix_window_ms")
    return bool(
        type(distance) is int and distance == 0
        and type(predicted) is int
        and isinstance(window, list) and len(window) == 2
        and all(type(value) is int for value in window)
        and window[0] < window[1]
        and window[0] <= predicted <= window[1]
    )


def timing_evidence_qualified(row: Mapping[str, Any] | None) -> bool:
    """Recheck every explicit eligibility condition instead of trusting a grant."""
    return bool(
        row is not None
        and row.get("timing_fusion_evidence_eligible") is True
        and row.get("local_match_gate_passed") is True
        and row.get("ambiguous") is False
        and type(row.get("feature_agreement")) is int
        and row["feature_agreement"] >= 2
        and row.get("slope_search_boundary_hit") is False
        and row.get("source_search_boundary_hit") is False
        and projection_domain_qualified(row)
    )
