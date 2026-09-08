"""Paired product diagnostics; oracle candidates are never production decisions.

All errors are relative to the annotated centre. Tolerance-adjusted errors are
reported separately. Missing candidates remain in the population denominator.
Track bootstrap weights tracks equally, rather than treating adjacent cues as
independent observations. This report does not confer alignment authority.
"""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "product-boundary-quality-1.0"


def _time(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("timestamps and uncertainty must be nonnegative integer ms")
    return value


def _quantile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = math.floor(index)
    high = math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def error_summary(errors: Sequence[float], population: int) -> dict[str, Any]:
    return {
        "count": len(errors), "population_count": population,
        "coverage": len(errors) / population if population else None,
        "mae_ms": statistics.mean(errors) if errors else None,
        "median_ms": _quantile(errors, .5),
        "p90_ms": _quantile(errors, .9), "p95_ms": _quantile(errors, .95),
        "max_ms": max(errors) if errors else None,
        "above_ms": {str(t): sum(e > t for e in errors) for t in (100, 250, 500, 1000)},
    }


def _paired(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    available = [r for r in rows if r.get(key) is not None]
    changed = [r for r in available if r[key] != r["editor_ms"]]
    gains = [abs(r["editor_ms"] - r["gold_ms"]) - abs(r[key] - r["gold_ms"]) for r in changed]
    by_track: dict[str, list[float]] = defaultdict(list)
    for r in available:
        by_track[r["track"]].append(abs(r["editor_ms"] - r["gold_ms"]) - abs(r[key] - r["gold_ms"]))
    means = [statistics.mean(v) for _, v in sorted(by_track.items())]
    interval = None
    if len(means) >= 2:
        rng = random.Random(20260907)
        samples = [statistics.mean(rng.choices(means, k=len(means))) for _ in range(2000)]
        interval = [_quantile(samples, .025), _quantile(samples, .975)]
    return {
        "paired_count": len(available), "changed_count": len(changed),
        "improved_count": sum(g > 0 for g in gains),
        "regressed_count": sum(g < 0 for g in gains),
        "harmful_over_100ms_count": sum(g < -100 for g in gains),
        "harmful_change_rate": sum(g < -100 for g in gains) / len(changed) if changed else None,
        "changed_improvement_rate": sum(g > 0 for g in gains) / len(changed) if changed else None,
        "new_over_500ms_count": sum(abs(r[key] - r["gold_ms"]) > 500 and abs(r["editor_ms"] - r["gold_ms"]) <= 500 for r in changed),
        "track_count": len(means),
        "track_equal_mean_improvement_ms": statistics.mean(means) if means else None,
        "track_bootstrap_95ci_ms": interval,
    }


def evaluate_boundaries(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate explicit boundary identities, never align records by cue number.

    Required: id, track (recording-group identity), partition, boundary_kind,
    gold_ms, uncertainty_ms, editor_ms, candidates {backend: ms | None}.
    Optional selected_ms/final_ms require independent, bound inputs from callers.
    Without them selection/writeback losses stay unknown, never assumed zero.
    """
    if not records:
        raise ValueError("quality population is empty")
    rows = []
    seen = set()
    partitions: dict[str, set[str]] = defaultdict(set)
    for raw in records:
        row = dict(raw)
        if not isinstance(row.get("id"), str) or not row["id"] or row["id"] in seen:
            raise ValueError("boundary identities must be nonempty and unique")
        seen.add(row["id"])
        if not row.get("track") or row.get("partition") not in {"calibration", "holdout", "regression"}:
            raise ValueError("track/partition missing or invalid")
        if row.get("boundary_kind") not in {"start", "end", "internal"}:
            raise ValueError("invalid boundary kind")
        partitions[row["track"]].add(row["partition"])
        for field in ("editor_ms", "gold_ms", "uncertainty_ms"):
            _time(row[field])
        for field in ("selected_ms", "final_ms"):
            if row.get(field) is not None:
                _time(row[field])
        candidates = row.get("candidates")
        if not isinstance(candidates, Mapping) or "editor" in candidates:
            raise ValueError("candidates must be a mapping excluding reserved editor")
        for name, value in candidates.items():
            if not isinstance(name, str) or not name:
                raise ValueError("candidate identity missing")
            if value is not None:
                _time(value)
        all_candidates = {"editor": row["editor_ms"], **{k: v for k, v in candidates.items() if v is not None}}
        if row.get("selected_ms") is not None and row["selected_ms"] not in all_candidates.values():
            raise ValueError("selected boundary is absent from the evaluated candidate set")
        best = min(all_candidates, key=lambda k: (abs(all_candidates[k] - row["gold_ms"]), k != "editor", k))
        row["oracle_ms"] = all_candidates[best]
        row["oracle_id"] = best
        rows.append(row)

    scopes = {}
    for kind, partition in sorted({(r["boundary_kind"], r["partition"]) for r in rows}):
        subset = [r for r in rows if (r["boundary_kind"], r["partition"]) == (kind, partition)]
        methods = {"editor": "editor_ms", "oracle_diagnostic_only": "oracle_ms", "selected": "selected_ms", "final": "final_ms"}
        summaries = {}
        for label, key in methods.items():
            present = [r for r in subset if r.get(key) is not None]
            raw_errors = [abs(r[key] - r["gold_ms"]) for r in present]
            effective = [max(0, abs(r[key] - r["gold_ms"]) - r["uncertainty_ms"]) for r in present]
            summaries[label] = {"raw": error_summary(raw_errors, len(subset)), "tolerance_adjusted": error_summary(effective, len(subset)), "paired_vs_editor": _paired(subset, key)}
        candidate_names = sorted({k for r in subset for k in r["candidates"]})
        for name in candidate_names:
            mapped = [{**r, "backend_ms": r["candidates"].get(name)} for r in subset]
            summaries["backend:" + name] = {
                "raw": error_summary([abs(r["backend_ms"] - r["gold_ms"]) for r in mapped if r["backend_ms"] is not None], len(subset)),
                "paired_vs_editor": _paired(mapped, "backend_ms"),
            }
        selected = [r for r in subset if r.get("selected_ms") is not None]
        written = [r for r in selected if r.get("final_ms") is not None]
        scopes[kind + ":" + partition] = {
            "methods": summaries,
            "candidate_better_than_editor_count": sum(r["oracle_id"] != "editor" for r in subset),
            "oracle_over_250ms_count": sum(abs(r["oracle_ms"] - r["gold_ms"]) > 250 for r in subset),
            "selection_observed_count": len(selected),
            "selection_regret_mean_ms": statistics.mean(abs(r["selected_ms"] - r["gold_ms"]) - abs(r["oracle_ms"] - r["gold_ms"]) for r in selected) if selected else None,
            "writeback_observed_count": len(written),
            "writeback_mismatch_count": sum(r["selected_ms"] != r["final_ms"] for r in written) if written else None,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "purpose": "offline_product_diagnostics_never_production_authority",
        "record_count": len(rows), "track_count": len(partitions),
        "calibration_holdout_shared_tracks": sorted(t for t, p in partitions.items() if {"calibration", "holdout"} <= p),
        "scopes": scopes, "records": rows,
    }
