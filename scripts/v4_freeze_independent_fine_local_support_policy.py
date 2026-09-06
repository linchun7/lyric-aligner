#!/usr/bin/env python3
"""Freeze a calibration-derived Independent Fine local-support selector before holdout."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.contracts.artifacts import atomic_write_json
from scripts.v4_evaluate_independent_fine_benchmark import EVALUATION_SCHEMA_VERSION, _load_hashed

POLICY_SCHEMA_VERSION = "independent-fine-local-support-policy-1.0"
POLICY_AUTHORITY = "frozen_before_holdout_local_support_only_no_direct_timing_authority"
POLICY_ID = "independent_fine_margin_0p05_v1"
MIN_MARGIN = 0.05
MIN_SELECTED_COVERAGE = 0.75
MIN_SELECTED_DISTINCT_PAIRS = 8
MAX_MEDIAN_START_ERROR_MS = 50.0
MAX_P90_START_ERROR_MS = 100.0
MAX_START_ERROR_MS = 250.0
MAX_P90_SLOPE_ABS_ERROR = 0.005
CATASTROPHIC_THRESHOLD_MS = 500.0
MAX_CATASTROPHIC_FRACTION = 0.0


class IndependentFinePolicyFreezeError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _p90(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    return ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)]


def _summary(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p90": None, "max": None}
    parsed = [float(value) for value in values]
    return {
        "count": len(parsed),
        "mean": statistics.fmean(parsed),
        "median": statistics.median(parsed),
        "p90": _p90(parsed),
        "max": max(parsed),
    }


def freeze_policy(*, evaluation_path: Path, expected_evaluation_sha256: str | None = None) -> dict[str, Any]:
    evaluation = _load_hashed(evaluation_path)
    if evaluation.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise IndependentFinePolicyFreezeError("unexpected Independent Fine evaluation schema")
    if evaluation.get("partition") != "calibration":
        raise IndependentFinePolicyFreezeError("policy may only be frozen from calibration")
    if evaluation.get("authority") != "evaluation_only_no_production_timing_authority":
        raise IndependentFinePolicyFreezeError("calibration evaluation authority is invalid")
    evaluation_sha = str(evaluation.get("artifact_sha256") or "")
    if expected_evaluation_sha256 is not None and evaluation_sha != expected_evaluation_sha256:
        raise IndependentFinePolicyFreezeError("calibration evaluation SHA mismatch")
    if bool(evaluation.get("automatic_mutation_allowed")) or bool(evaluation.get("subtitle_mutation_performed")):
        raise IndependentFinePolicyFreezeError("calibration evaluation must remain observer-only")
    cases = evaluation.get("cases")
    if not isinstance(cases, list) or not cases or int(evaluation.get("record_count", -1)) != len(cases):
        raise IndependentFinePolicyFreezeError("calibration evaluation case count is invalid")

    selected: list[Mapping[str, Any]] = []
    for row in cases:
        if not isinstance(row, Mapping):
            raise IndependentFinePolicyFreezeError("calibration case must be an object")
        if row.get("status") != "aligned":
            continue
        if bool(row.get("ambiguous")):
            continue
        margin = float(row.get("margin") or 0.0)
        if not math.isfinite(margin):
            raise IndependentFinePolicyFreezeError("calibration margin is invalid")
        if margin + 1e-12 < MIN_MARGIN:
            continue
        selected.append(row)

    selected_count = len(selected)
    selected_coverage = selected_count / len(cases)
    pair_ids = {int(row.get("pair_index", -1)) for row in selected}
    if any(pair < 1 for pair in pair_ids):
        raise IndependentFinePolicyFreezeError("selected calibration pair identity is invalid")
    start_errors = [float(row["source_start_abs_error_ms"]) for row in selected]
    slope_errors = [float(row["slope_abs_error"]) for row in selected]
    if any(not math.isfinite(value) or value < 0 for value in start_errors + slope_errors):
        raise IndependentFinePolicyFreezeError("selected calibration error is invalid")
    start_summary = _summary(start_errors)
    slope_summary = _summary(slope_errors)
    catastrophic_count = sum(value >= CATASTROPHIC_THRESHOLD_MS for value in start_errors)
    catastrophic_fraction = catastrophic_count / selected_count if selected_count else 1.0

    gates = {
        "selected_coverage_ge_min": selected_coverage >= MIN_SELECTED_COVERAGE,
        "selected_distinct_pairs_ge_min": len(pair_ids) >= MIN_SELECTED_DISTINCT_PAIRS,
        "median_start_error_le_max": selected_count > 0 and float(start_summary["median"]) <= MAX_MEDIAN_START_ERROR_MS,
        "p90_start_error_le_max": selected_count > 0 and float(start_summary["p90"]) <= MAX_P90_START_ERROR_MS,
        "max_start_error_le_max": selected_count > 0 and float(start_summary["max"]) <= MAX_START_ERROR_MS,
        "p90_slope_error_le_max": selected_count > 0 and float(slope_summary["p90"]) <= MAX_P90_SLOPE_ABS_ERROR,
        "catastrophic_fraction_le_max": catastrophic_fraction <= MAX_CATASTROPHIC_FRACTION,
    }
    calibration_passed = all(gates.values())
    if not calibration_passed:
        failed = [key for key, value in gates.items() if not value]
        raise IndependentFinePolicyFreezeError("calibration selector gate failed: " + ",".join(failed))

    artifact: dict[str, Any] = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "authority": POLICY_AUTHORITY,
        "source_audit_sha256": str(evaluation.get("source_audit_sha256") or ""),
        "calibration_evaluation_sha256": evaluation_sha,
        "calibration_run_artifact_sha256": str(evaluation.get("run_artifact_sha256") or ""),
        "selector": {
            "status": "aligned",
            "require_observer_ambiguous_false": True,
            "minimum_top1_top2_margin": MIN_MARGIN,
            "note": "retains the observer's frozen score/feature-agreement ambiguity conditions and tightens only margin",
        },
        "calibration_gate": {
            "minimum_selected_coverage": MIN_SELECTED_COVERAGE,
            "minimum_selected_distinct_pairs": MIN_SELECTED_DISTINCT_PAIRS,
            "maximum_median_source_start_abs_error_ms": MAX_MEDIAN_START_ERROR_MS,
            "maximum_p90_source_start_abs_error_ms": MAX_P90_START_ERROR_MS,
            "maximum_source_start_abs_error_ms": MAX_START_ERROR_MS,
            "maximum_p90_slope_abs_error": MAX_P90_SLOPE_ABS_ERROR,
            "catastrophic_threshold_ms": CATASTROPHIC_THRESHOLD_MS,
            "maximum_catastrophic_fraction": MAX_CATASTROPHIC_FRACTION,
        },
        "calibration_result": {
            "record_count": len(cases),
            "selected_count": selected_count,
            "selected_coverage": selected_coverage,
            "selected_distinct_pair_count": len(pair_ids),
            "source_start_abs_error_ms": start_summary,
            "slope_abs_error": slope_summary,
            "catastrophic_count": catastrophic_count,
            "catastrophic_fraction": catastrophic_fraction,
            "gates": gates,
            "passed": True,
        },
        "holdout_status": "not_run_at_policy_freeze",
        "production_authoritative": False,
        "direct_subtitle_timing_authority": False,
        "automatic_mutation_allowed": False,
    }
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--expected-evaluation-sha256")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"policy output already exists: {args.out}")
    artifact = freeze_policy(
        evaluation_path=args.evaluation,
        expected_evaluation_sha256=args.expected_evaluation_sha256,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, artifact)
    print(json.dumps({
        "out": str(args.out),
        "policy_id": artifact["policy_id"],
        "selected_count": artifact["calibration_result"]["selected_count"],
        "selected_coverage": artifact["calibration_result"]["selected_coverage"],
        "median_error_ms": artifact["calibration_result"]["source_start_abs_error_ms"]["median"],
        "p90_error_ms": artifact["calibration_result"]["source_start_abs_error_ms"]["p90"],
        "max_error_ms": artifact["calibration_result"]["source_start_abs_error_ms"]["max"],
        "artifact_sha256": artifact["artifact_sha256"],
        "holdout_status": artifact["holdout_status"],
        "production_authoritative": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
