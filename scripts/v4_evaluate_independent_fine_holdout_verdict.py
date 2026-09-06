#!/usr/bin/env python3
"""Apply the prediction-pre-frozen Independent Fine holdout protocol exactly once."""
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
from scripts.v4_freeze_independent_fine_holdout_protocol import PROTOCOL_SCHEMA_VERSION

VERDICT_SCHEMA_VERSION = "independent-fine-holdout-verdict-1.0"
VERDICT_AUTHORITY = "holdout_evaluation_only_no_direct_subtitle_timing_authority"


class IndependentFineHoldoutVerdictError(ValueError):
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


def evaluate_verdict(*, evaluation_path: Path, protocol_path: Path) -> dict[str, Any]:
    evaluation = _load_hashed(evaluation_path)
    protocol = _load_hashed(protocol_path)
    if evaluation.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise IndependentFineHoldoutVerdictError("unexpected holdout evaluation schema")
    if evaluation.get("partition") != "holdout":
        raise IndependentFineHoldoutVerdictError("verdict requires holdout evaluation")
    if evaluation.get("authority") != "evaluation_only_no_production_timing_authority":
        raise IndependentFineHoldoutVerdictError("holdout evaluation authority is invalid")
    if bool(evaluation.get("automatic_mutation_allowed")) or bool(evaluation.get("subtitle_mutation_performed")):
        raise IndependentFineHoldoutVerdictError("holdout evaluation must remain observer-only")
    if protocol.get("schema_version") != PROTOCOL_SCHEMA_VERSION:
        raise IndependentFineHoldoutVerdictError("unexpected holdout protocol schema")
    if protocol.get("holdout_predictions_status") != "not_run_at_protocol_freeze":
        raise IndependentFineHoldoutVerdictError("holdout protocol was not frozen before predictions")
    if bool(protocol.get("production_authoritative")) or bool(protocol.get("automatic_mutation_allowed")):
        raise IndependentFineHoldoutVerdictError("holdout protocol must remain non-authoritative")

    expected_links = {
        "holdout_protocol_sha256": protocol.get("artifact_sha256"),
        "source_audit_sha256": protocol.get("holdout_affine_truth_audit_sha256"),
        "frozen_policy_sha256": protocol.get("frozen_policy_sha256"),
        "pair_selection_sha256": protocol.get("pair_selection_sha256"),
    }
    for key, expected in expected_links.items():
        if str(evaluation.get(key) or "") != str(expected or ""):
            raise IndependentFineHoldoutVerdictError(f"holdout evaluation {key} does not match frozen protocol")

    selector = protocol.get("selector")
    gates = protocol.get("holdout_gate")
    cases = evaluation.get("cases")
    if not isinstance(selector, Mapping) or not isinstance(gates, Mapping):
        raise IndependentFineHoldoutVerdictError("holdout protocol selector/gate is invalid")
    if not isinstance(cases, list) or not cases or int(evaluation.get("record_count", -1)) != len(cases):
        raise IndependentFineHoldoutVerdictError("holdout evaluation case count is invalid")
    if selector.get("status") != "aligned" or selector.get("require_observer_ambiguous_false") is not True:
        raise IndependentFineHoldoutVerdictError("unsupported frozen holdout selector")
    minimum_margin = float(selector.get("minimum_top1_top2_margin"))
    if not math.isfinite(minimum_margin) or minimum_margin < 0:
        raise IndependentFineHoldoutVerdictError("frozen holdout margin is invalid")

    selected: list[Mapping[str, Any]] = []
    for row in cases:
        if not isinstance(row, Mapping):
            raise IndependentFineHoldoutVerdictError("holdout case must be an object")
        if row.get("status") != "aligned" or bool(row.get("ambiguous")):
            continue
        margin = float(row.get("margin") or 0.0)
        if not math.isfinite(margin):
            raise IndependentFineHoldoutVerdictError("holdout case margin is invalid")
        if margin + 1e-12 < minimum_margin:
            continue
        selected.append(row)

    selected_count = len(selected)
    selected_coverage = selected_count / len(cases)
    pair_ids = {int(row.get("pair_index", -1)) for row in selected}
    if any(pair < 1 for pair in pair_ids):
        raise IndependentFineHoldoutVerdictError("selected holdout pair identity is invalid")
    start_errors = [float(row["source_start_abs_error_ms"]) for row in selected]
    slope_errors = [float(row["slope_abs_error"]) for row in selected]
    if any(not math.isfinite(value) or value < 0 for value in start_errors + slope_errors):
        raise IndependentFineHoldoutVerdictError("selected holdout error is invalid")
    start_summary = _summary(start_errors)
    slope_summary = _summary(slope_errors)
    catastrophic_threshold = float(gates["catastrophic_threshold_ms"])
    catastrophic_count = sum(value >= catastrophic_threshold for value in start_errors)
    catastrophic_fraction = catastrophic_count / selected_count if selected_count else 1.0

    checks = {
        "selected_coverage_ge_min": selected_coverage >= float(gates["minimum_selected_coverage"]),
        "selected_distinct_pairs_ge_min": len(pair_ids) >= int(gates["minimum_selected_distinct_pairs"]),
        "median_start_error_le_max": selected_count > 0 and float(start_summary["median"]) <= float(gates["maximum_median_source_start_abs_error_ms"]),
        "p90_start_error_le_max": selected_count > 0 and float(start_summary["p90"]) <= float(gates["maximum_p90_source_start_abs_error_ms"]),
        "max_start_error_le_max": selected_count > 0 and float(start_summary["max"]) <= float(gates["maximum_source_start_abs_error_ms"]),
        "p90_slope_error_le_max": selected_count > 0 and float(slope_summary["p90"]) <= float(gates["maximum_p90_slope_abs_error"]),
        "catastrophic_fraction_le_max": catastrophic_fraction <= float(gates["maximum_catastrophic_fraction"]),
    }
    passed = all(checks.values())
    artifact: dict[str, Any] = {
        "schema_version": VERDICT_SCHEMA_VERSION,
        "authority": VERDICT_AUTHORITY,
        "partition": "holdout",
        "holdout_protocol_sha256": protocol["artifact_sha256"],
        "holdout_evaluation_sha256": evaluation["artifact_sha256"],
        "frozen_policy_sha256": protocol["frozen_policy_sha256"],
        "pair_selection_sha256": protocol["pair_selection_sha256"],
        "source_audit_sha256": protocol["holdout_affine_truth_audit_sha256"],
        "selector": dict(selector),
        "holdout_gate": dict(gates),
        "result": {
            "record_count": len(cases),
            "selected_count": selected_count,
            "selected_coverage": selected_coverage,
            "selected_distinct_pair_count": len(pair_ids),
            "source_start_abs_error_ms": start_summary,
            "slope_abs_error": slope_summary,
            "catastrophic_count": catastrophic_count,
            "catastrophic_fraction": catastrophic_fraction,
            "checks": checks,
            "passed": passed,
        },
        "verdict": "passed_frozen_holdout_protocol" if passed else "failed_frozen_holdout_protocol",
        "policy_retuning_from_holdout_allowed": False,
        "eligible_for_local_support_authority_review": passed,
        "production_authoritative": False,
        "direct_subtitle_timing_authority": False,
        "automatic_mutation_allowed": False,
        "subtitle_mutation_performed": False,
    }
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"holdout verdict output already exists: {args.out}")
    artifact = evaluate_verdict(evaluation_path=args.evaluation, protocol_path=args.protocol)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, artifact)
    print(json.dumps({
        "out": str(args.out),
        "verdict": artifact["verdict"],
        "selected_count": artifact["result"]["selected_count"],
        "selected_coverage": artifact["result"]["selected_coverage"],
        "median_error_ms": artifact["result"]["source_start_abs_error_ms"]["median"],
        "p90_error_ms": artifact["result"]["source_start_abs_error_ms"]["p90"],
        "max_error_ms": artifact["result"]["source_start_abs_error_ms"]["max"],
        "artifact_sha256": artifact["artifact_sha256"],
        "policy_retuning_from_holdout_allowed": False,
        "production_authoritative": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
