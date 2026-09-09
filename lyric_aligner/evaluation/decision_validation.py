"""Offline validation of automatic subtitle timing decisions on frozen human truth.

This module measures whether an automatic change helped or harmed the final boundary.
It is evaluation-only and never grants production timing authority.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "timing-decision-validation-1.0"


def _nonnegative_ms(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer ms value")
    return value


def _quantile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _error(value: int, gold: int, uncertainty: int) -> tuple[int, int]:
    raw = abs(value - gold)
    return raw, max(0, raw - uncertainty)


def _summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    manual_tolerance_ms: int,
    decision_margin_ms: int,
) -> dict[str, Any]:
    changed = [row for row in rows if row["changed"]]
    editor_rows = [row for row in rows if row.get("editor_error_ms") is not None]
    candidate_rows = [
        row for row in rows if row.get("best_alternative_gain_vs_baseline_ms") is not None
    ]
    gains = [row["gain_ms"] for row in changed]
    baseline_errors = [row["baseline_error_ms"] for row in rows]
    final_errors = [row["final_error_ms"] for row in rows]
    baseline_effective = [row["baseline_effective_error_ms"] for row in rows]
    final_effective = [row["final_effective_error_ms"] for row in rows]
    by_track: dict[str, list[int]] = defaultdict(list)
    for row in changed:
        by_track[str(row["track"])].append(int(row["gain_ms"]))
    track_means = [statistics.mean(values) for _, values in sorted(by_track.items())]
    return {
        "record_count": len(rows),
        "changed_count": len(changed),
        "unchanged_count": len(rows) - len(changed),
        "improved_count": sum(gain > 0 for gain in gains),
        "neutral_count": sum(gain == 0 for gain in gains),
        "regressed_count": sum(gain < 0 for gain in gains),
        "changed_improvement_rate": sum(gain > 0 for gain in gains) / len(changed) if changed else None,
        "harmful_over_100ms_count": sum(gain < -100 for gain in gains),
        "harmful_over_100ms_rate": sum(gain < -100 for gain in gains) / len(changed) if changed else None,
        "rescue_over_100ms_count": sum(gain > 100 for gain in gains),
        "rescue_over_500ms_count": sum(gain > 500 for gain in gains),
        "new_over_500ms_error_count": sum(
            row["final_error_ms"] > 500 and row["baseline_error_ms"] <= 500 for row in changed
        ),
        "new_over_1000ms_error_count": sum(
            row["final_error_ms"] > 1000 and row["baseline_error_ms"] <= 1000 for row in changed
        ),
        "baseline_mae_ms": statistics.mean(baseline_errors) if baseline_errors else None,
        "final_mae_ms": statistics.mean(final_errors) if final_errors else None,
        "baseline_p90_ms": _quantile(baseline_errors, 0.90),
        "final_p90_ms": _quantile(final_errors, 0.90),
        "baseline_max_ms": max(baseline_errors) if baseline_errors else None,
        "final_max_ms": max(final_errors) if final_errors else None,
        "baseline_effective_mae_ms": statistics.mean(baseline_effective) if baseline_effective else None,
        "final_effective_mae_ms": statistics.mean(final_effective) if final_effective else None,
        "manual_repair_needed_before_count": sum(error > manual_tolerance_ms for error in baseline_effective),
        "manual_repair_needed_after_count": sum(error > manual_tolerance_ms for error in final_effective),
        "manual_repair_reduction_count": (
            sum(error > manual_tolerance_ms for error in baseline_effective)
            - sum(error > manual_tolerance_ms for error in final_effective)
        ),
        "editor_observed_count": len(editor_rows),
        "missed_editor_rescue_over_100ms_count": (
            sum(
                not row["changed"] and row["editor_gain_vs_baseline_ms"] > 100
                for row in editor_rows
            )
            if editor_rows
            else None
        ),
        "candidate_observed_count": len(candidate_rows),
        "missed_any_candidate_rescue_over_100ms_count": (
            sum(
                not row["changed"] and row["best_alternative_gain_vs_baseline_ms"] > 100
                for row in candidate_rows
            )
            if candidate_rows
            else None
        ),
        "decision_margin_ms": decision_margin_ms,
        "automatic_change_confusion": {
            "beneficial_change_count": sum(
                row["changed"] and row["gain_ms"] > decision_margin_ms for row in rows
            ),
            "harmful_change_count": sum(
                row["changed"] and row["gain_ms"] < -decision_margin_ms for row in rows
            ),
            "borderline_change_count": sum(
                row["changed"] and abs(row["gain_ms"]) <= decision_margin_ms for row in rows
            ),
            "missed_beneficial_candidate_count": (
                sum(
                    not row["changed"]
                    and row["best_alternative_gain_vs_baseline_ms"] > decision_margin_ms
                    for row in candidate_rows
                )
                if candidate_rows
                else None
            ),
        },
        "editor_recovery_confusion": (
            {
                "true_positive_count": sum(
                    row["editor_recovery_selected"]
                    and row["editor_gain_vs_baseline_ms"] > decision_margin_ms
                    for row in editor_rows
                ),
                "false_positive_count": sum(
                    row["editor_recovery_selected"]
                    and row["editor_gain_vs_baseline_ms"] <= decision_margin_ms
                    for row in editor_rows
                ),
                "false_negative_count": sum(
                    not row["editor_recovery_selected"]
                    and row["editor_gain_vs_baseline_ms"] > decision_margin_ms
                    for row in editor_rows
                ),
                "true_negative_count": sum(
                    not row["editor_recovery_selected"]
                    and row["editor_gain_vs_baseline_ms"] <= decision_margin_ms
                    for row in editor_rows
                ),
            }
            if editor_rows
            else None
        ),
        "changed_mean_gain_ms": statistics.mean(gains) if gains else None,
        "changed_median_gain_ms": statistics.median(gains) if gains else None,
        "track_count_with_changes": len(track_means),
        "track_equal_mean_gain_ms": statistics.mean(track_means) if track_means else None,
    }


def evaluate_timing_decisions(
    records: Sequence[Mapping[str, Any]],
    *,
    manual_tolerance_ms: int = 100,
    decision_margin_ms: int = 100,
) -> dict[str, Any]:
    """Compare frozen old-final -> hybrid decisions against exact bound human truth.

    Required fields: id, track, partition, boundary_kind, gold_ms, uncertainty_ms,
    old_final_ms, hybrid_ms. editor_ms is optional and must remain absent when a
    trustworthy editor boundary mapping is unavailable. Optional alternatives maps candidate ids to
    integer ms values. `partition` is diagnostic metadata; callers remain responsible
    for freezing/locking the blind population before prediction.
    """
    if not records:
        raise ValueError("timing decision validation population is empty")
    if isinstance(manual_tolerance_ms, bool) or not isinstance(manual_tolerance_ms, int) or manual_tolerance_ms < 0:
        raise ValueError("manual_tolerance_ms must be a nonnegative integer")
    if isinstance(decision_margin_ms, bool) or not isinstance(decision_margin_ms, int) or decision_margin_ms < 0:
        raise ValueError("decision_margin_ms must be a nonnegative integer")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in records:
        row = dict(raw)
        record_id = row.get("id")
        if not isinstance(record_id, str) or not record_id or record_id in seen:
            raise ValueError("decision ids must be nonempty and unique")
        seen.add(record_id)
        if not isinstance(row.get("track"), str) or not row["track"]:
            raise ValueError("track identity is required")
        if row.get("partition") not in {"development", "calibration", "blind", "holdout", "regression"}:
            raise ValueError("partition is invalid")
        if row.get("boundary_kind") not in {"start", "end", "internal"}:
            raise ValueError("boundary_kind is invalid")
        for field in ("gold_ms", "uncertainty_ms", "old_final_ms", "hybrid_ms"):
            row[field] = _nonnegative_ms(row.get(field), field)
        if row.get("editor_ms") is not None:
            row["editor_ms"] = _nonnegative_ms(row.get("editor_ms"), "editor_ms")
        else:
            row["editor_ms"] = None
        alternatives = row.get("alternatives", {})
        if not isinstance(alternatives, Mapping):
            raise ValueError("alternatives must be a mapping")
        parsed_alternatives: dict[str, int] = {}
        for name, value in alternatives.items():
            if not isinstance(name, str) or not name or name in {"editor", "old_final", "hybrid"}:
                raise ValueError("alternative candidate id is invalid or reserved")
            parsed_alternatives[name] = _nonnegative_ms(value, f"alternatives.{name}")
        row["alternatives"] = parsed_alternatives

        baseline_error, baseline_effective = _error(row["old_final_ms"], row["gold_ms"], row["uncertainty_ms"])
        final_error, final_effective = _error(row["hybrid_ms"], row["gold_ms"], row["uncertainty_ms"])
        editor_error = (
            None
            if row["editor_ms"] is None
            else abs(row["editor_ms"] - row["gold_ms"])
        )
        alternative_errors = {
            name: abs(value - row["gold_ms"]) for name, value in parsed_alternatives.items()
        }
        alternative_pool = [*alternative_errors.values()]
        if editor_error is not None:
            alternative_pool.append(editor_error)
        best_alternative_error = min(alternative_pool) if alternative_pool else None
        row.update(
            changed=row["hybrid_ms"] != row["old_final_ms"],
            editor_recovery_selected=(
                row["editor_ms"] is not None
                and row["hybrid_ms"] != row["old_final_ms"]
                and row["hybrid_ms"] == row["editor_ms"]
            ),
            baseline_error_ms=baseline_error,
            final_error_ms=final_error,
            baseline_effective_error_ms=baseline_effective,
            final_effective_error_ms=final_effective,
            editor_error_ms=editor_error,
            gain_ms=baseline_error - final_error,
            editor_gain_vs_baseline_ms=(
                None if editor_error is None else baseline_error - editor_error
            ),
            best_alternative_gain_vs_baseline_ms=(
                None
                if best_alternative_error is None
                else baseline_error - best_alternative_error
            ),
        )
        normalized.append(row)

    scopes: dict[str, Any] = {
        "overall": _summary(
            normalized,
            manual_tolerance_ms=manual_tolerance_ms,
            decision_margin_ms=decision_margin_ms,
        )
    }
    for partition in sorted({row["partition"] for row in normalized}):
        subset = [row for row in normalized if row["partition"] == partition]
        scopes[f"partition:{partition}"] = _summary(
            subset,
            manual_tolerance_ms=manual_tolerance_ms,
            decision_margin_ms=decision_margin_ms,
        )
    for kind in sorted({row["boundary_kind"] for row in normalized}):
        subset = [row for row in normalized if row["boundary_kind"] == kind]
        scopes[f"boundary:{kind}"] = _summary(
            subset,
            manual_tolerance_ms=manual_tolerance_ms,
            decision_margin_ms=decision_margin_ms,
        )
    for field in ("language", "structural_scenario"):
        values = sorted({str(row[field]) for row in normalized if row.get(field) not in (None, "")})
        for value in values:
            subset = [row for row in normalized if str(row.get(field)) == value]
            scopes[f"{field}:{value}"] = _summary(
                subset,
                manual_tolerance_ms=manual_tolerance_ms,
                decision_margin_ms=decision_margin_ms,
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "purpose": "offline_selector_validation_never_production_authority",
        "manual_tolerance_ms": manual_tolerance_ms,
        "decision_margin_ms": decision_margin_ms,
        "record_count": len(normalized),
        "track_count": len({row["track"] for row in normalized}),
        "scopes": scopes,
        "records": normalized,
    }
