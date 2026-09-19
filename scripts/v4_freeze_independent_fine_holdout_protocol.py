#!/usr/bin/env python3
"""Freeze Independent Fine holdout acceptance before any holdout prediction run."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import sys
from typing import Any
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
from lyric_aligner.contracts.artifacts import atomic_write_json
from scripts.v4_evaluate_independent_fine_benchmark import _load_hashed, observer_identity
from scripts.v4_freeze_independent_fine_local_support_policy import POLICY_SCHEMA_VERSION
from scripts.v4_audit_independent_fine_affine_truth import AUDIT_SCHEMA_VERSION

PROTOCOL_SCHEMA_VERSION = "independent-fine-holdout-protocol-1.0"
PROTOCOL_AUTHORITY = "frozen_before_holdout_predictions_evaluation_only"
MIN_TRUTH_PAIR_COUNT = 4
MIN_TRUTH_USABLE_FRACTION = 0.75
MIN_SELECTED_DISTINCT_PAIRS = 4

class IndependentFineHoldoutProtocolError(ValueError):
    pass

def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def freeze_protocol(*, policy_path: Path, audit_path: Path) -> dict[str, Any]:
    policy = _load_hashed(policy_path)
    audit = _load_hashed(audit_path)
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise IndependentFineHoldoutProtocolError("unexpected local-support policy schema")
    if policy.get("holdout_status") != "not_run_at_policy_freeze":
        raise IndependentFineHoldoutProtocolError("local-support policy is not pre-holdout")
    if bool(policy.get("production_authoritative")) or bool(policy.get("automatic_mutation_allowed")):
        raise IndependentFineHoldoutProtocolError("local-support policy must remain non-authoritative")
    if audit.get("schema_version") != AUDIT_SCHEMA_VERSION or audit.get("partition") != "holdout":
        raise IndependentFineHoldoutProtocolError("unexpected holdout affine audit identity")
    if bool(audit.get("independent_fine_predictions_consulted")):
        raise IndependentFineHoldoutProtocolError("holdout truth audit consulted Independent Fine predictions")
    if str(audit.get("frozen_policy_sha256") or "") != str(policy.get("artifact_sha256") or ""):
        raise IndependentFineHoldoutProtocolError("holdout truth audit does not bind frozen policy")
    pair_count = int(audit.get("pair_count", -1))
    usable_count = int(audit.get("truth_usable_count", -1))
    if pair_count < MIN_TRUTH_PAIR_COUNT or usable_count < MIN_TRUTH_PAIR_COUNT:
        raise IndependentFineHoldoutProtocolError("insufficient holdout truth pair count")
    truth_usable_fraction = usable_count / pair_count
    if truth_usable_fraction < MIN_TRUTH_USABLE_FRACTION:
        raise IndependentFineHoldoutProtocolError("insufficient holdout truth usability")
    selector = policy.get("selector")
    calibration_gate = policy.get("calibration_gate")
    if not isinstance(selector, dict) or not isinstance(calibration_gate, dict):
        raise IndependentFineHoldoutProtocolError("frozen policy selector/gate is invalid")
    required = (
        "minimum_selected_coverage",
        "maximum_median_source_start_abs_error_ms",
        "maximum_p90_source_start_abs_error_ms",
        "maximum_source_start_abs_error_ms",
        "maximum_p90_slope_abs_error",
        "catastrophic_threshold_ms",
        "maximum_catastrophic_fraction",
    )
    if any(key not in calibration_gate for key in required):
        raise IndependentFineHoldoutProtocolError("frozen policy calibration gate is incomplete")
    artifact: dict[str, Any] = {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "authority": PROTOCOL_AUTHORITY,
        **observer_identity(policy),
        "frozen_policy_sha256": policy["artifact_sha256"],
        "holdout_affine_truth_audit_sha256": audit["artifact_sha256"],
        "pair_selection_sha256": audit["pair_selection_sha256"],
        "truth_pair_count": pair_count,
        "truth_usable_count": usable_count,
        "truth_usable_fraction": truth_usable_fraction,
        "selector": dict(selector),
        "holdout_gate": {
            "minimum_selected_coverage": calibration_gate["minimum_selected_coverage"],
            "minimum_selected_distinct_pairs": MIN_SELECTED_DISTINCT_PAIRS,
            "maximum_median_source_start_abs_error_ms": calibration_gate["maximum_median_source_start_abs_error_ms"],
            "maximum_p90_source_start_abs_error_ms": calibration_gate["maximum_p90_source_start_abs_error_ms"],
            "maximum_source_start_abs_error_ms": calibration_gate["maximum_source_start_abs_error_ms"],
            "maximum_p90_slope_abs_error": calibration_gate["maximum_p90_slope_abs_error"],
            "catastrophic_threshold_ms": calibration_gate["catastrophic_threshold_ms"],
            "maximum_catastrophic_fraction": calibration_gate["maximum_catastrophic_fraction"],
        },
        "holdout_predictions_status": "not_run_at_protocol_freeze",
        "production_authoritative": False,
        "direct_subtitle_timing_authority": False,
        "automatic_mutation_allowed": False,
    }
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--frozen-policy", type=Path, required=True)
    p.add_argument("--holdout-affine-audit", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if args.out.exists():
        raise FileExistsError(f"holdout protocol output already exists: {args.out}")
    artifact = freeze_protocol(policy_path=args.frozen_policy, audit_path=args.holdout_affine_audit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, artifact)
    print(json.dumps({"out": str(args.out), "artifact_sha256": artifact["artifact_sha256"], "truth_pair_count": artifact["truth_pair_count"], "truth_usable_count": artifact["truth_usable_count"], "holdout_predictions_status": artifact["holdout_predictions_status"], "production_authoritative": False}, ensure_ascii=False, sort_keys=True))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
