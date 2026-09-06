#!/usr/bin/env python3
"""Evaluate Independent Fine predictions against separate known-transform truth."""

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
from scripts.v4_run_independent_fine_benchmark import (
    BENCHMARK_AUTHORITY,
    RUN_SCHEMA_VERSION,
)

TRUTH_SCHEMA_VERSION = "independent-fine-known-transform-truth-1.0"
EVALUATION_SCHEMA_VERSION = "independent-fine-known-transform-evaluation-1.0"


class IndependentFineBenchmarkEvaluationError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_hashed(path: Path, *, hash_field: str = "artifact_sha256") -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise IndependentFineBenchmarkEvaluationError(f"JSON root must be object: {path}")
    claimed = str(payload.get(hash_field) or "")
    unsigned = dict(payload)
    unsigned.pop(hash_field, None)
    if len(claimed) != 64 or claimed != _sha_json(unsigned):
        raise IndependentFineBenchmarkEvaluationError(f"artifact hash mismatch: {path}")
    return payload


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


def evaluate(*, run_path: Path, truth_path: Path) -> dict[str, Any]:
    run = _load_hashed(run_path)
    truth = _load_hashed(truth_path)
    if run.get("schema_version") != RUN_SCHEMA_VERSION or run.get("authority") != BENCHMARK_AUTHORITY:
        raise IndependentFineBenchmarkEvaluationError("unexpected benchmark run identity")
    if truth.get("schema_version") != TRUTH_SCHEMA_VERSION:
        raise IndependentFineBenchmarkEvaluationError("unexpected benchmark truth schema")
    if run.get("partition") != truth.get("partition"):
        raise IndependentFineBenchmarkEvaluationError("run/truth partition mismatch")
    if truth.get("partition") not in {"calibration", "holdout"}:
        raise IndependentFineBenchmarkEvaluationError("truth partition is invalid")
    run_audit_sha = str(run.get("source_audit_sha256") or "")
    truth_audit_sha = str(truth.get("source_audit_sha256") or "")
    if len(run_audit_sha) != 64 or run_audit_sha != truth_audit_sha:
        raise IndependentFineBenchmarkEvaluationError("run/truth source audit SHA mismatch")
    if any(character not in "0123456789abcdef" for character in run_audit_sha):
        raise IndependentFineBenchmarkEvaluationError("source audit SHA is invalid")
    holdout_provenance: dict[str, str] = {}
    if truth.get("partition") == "holdout":
        for key in ("source_audit_file_sha256", "frozen_policy_sha256", "pair_selection_sha256"):
            run_value = str(run.get(key) or "")
            truth_value = str(truth.get(key) or "")
            if len(run_value) != 64 or run_value != truth_value or any(ch not in "0123456789abcdef" for ch in run_value):
                raise IndependentFineBenchmarkEvaluationError(f"holdout run/truth {key} mismatch")
            holdout_provenance[key] = run_value
        protocol_sha = str(run.get("holdout_protocol_sha256") or "")
        if len(protocol_sha) != 64 or any(ch not in "0123456789abcdef" for ch in protocol_sha):
            raise IndependentFineBenchmarkEvaluationError("holdout run protocol SHA is invalid")
        holdout_provenance["holdout_protocol_sha256"] = protocol_sha
    if bool(run.get("automatic_mutation_allowed")) or bool(run.get("subtitle_mutation_performed")):
        raise IndependentFineBenchmarkEvaluationError("benchmark run must remain observer-only")
    truth_records = truth.get("records")
    run_records = run.get("records")
    if not isinstance(truth_records, list) or not isinstance(run_records, list):
        raise IndependentFineBenchmarkEvaluationError("run/truth records must be lists")
    if int(truth.get("record_count", -1)) != len(truth_records):
        raise IndependentFineBenchmarkEvaluationError("truth record count is invalid")
    truth_by_case = {
        str(row.get("case_id") or ""): row
        for row in truth_records
        if isinstance(row, Mapping)
    }
    run_by_case = {
        str(row.get("case_id") or ""): row
        for row in run_records
        if isinstance(row, Mapping)
    }
    if len(truth_by_case) != len(truth_records) or len(run_by_case) != len(run_records):
        raise IndependentFineBenchmarkEvaluationError("run/truth case IDs must be unique/non-empty")
    if set(truth_by_case) != set(run_by_case):
        raise IndependentFineBenchmarkEvaluationError("run/truth case set mismatch")

    start_errors_ms: list[float] = []
    slope_abs_errors: list[float] = []
    slope_relative_errors: list[float] = []
    margins: list[float] = []
    scores: list[float] = []
    cases: list[dict[str, Any]] = []
    aligned = 0
    ambiguous = 0
    for case_id in sorted(truth_by_case):
        truth_row = truth_by_case[case_id]
        run_row = run_by_case[case_id]
        if str(run_row.get("source_sha256") or "") != str(truth_row.get("source_sha256") or ""):
            raise IndependentFineBenchmarkEvaluationError("truth source SHA does not match run")
        if str(run_row.get("mix_sha256") or "") != str(truth_row.get("mix_sha256") or ""):
            raise IndependentFineBenchmarkEvaluationError("truth mix SHA does not match run")
        if str(truth_row.get("source_audit_sha256") or "") != truth_audit_sha:
            raise IndependentFineBenchmarkEvaluationError("truth record source audit SHA mismatch")
        expected_start = float(truth_row["expected_source_start_s"])
        expected_slope = float(truth_row["expected_slope"])
        pair_index = int(truth_row.get("pair_index", -1))
        window_index = int(truth_row.get("window_index", -1))
        if expected_start < 0 or not math.isfinite(expected_slope) or expected_slope <= 0:
            raise IndependentFineBenchmarkEvaluationError("truth timing values are invalid")
        if pair_index < 1 or window_index < 1:
            raise IndependentFineBenchmarkEvaluationError("truth pair/window identity is invalid")
        prediction = run_row.get("prediction")
        case_payload: dict[str, Any] = {
            "case_id": case_id,
            "truth_basis": str(truth_row.get("truth_basis") or ""),
            "pair_index": pair_index,
            "window_index": window_index,
            "expected_source_start_s": expected_start,
            "expected_slope": expected_slope,
            "status": str(run_row.get("status") or ""),
        }
        if run_row.get("status") == "aligned" and isinstance(prediction, Mapping):
            top1 = prediction.get("top1")
            if not isinstance(top1, Mapping):
                raise IndependentFineBenchmarkEvaluationError("aligned run row lacks top1 candidate")
            predicted_start = float(top1["source_start"])
            predicted_slope = float(top1["estimated_slope"])
            start_error = abs(predicted_start - expected_start) * 1000.0
            slope_error = abs(predicted_slope - expected_slope)
            slope_relative = slope_error / expected_slope
            margin = float(prediction.get("margin") or 0.0)
            score = float(top1.get("fused_score") or 0.0)
            is_ambiguous = bool(prediction.get("ambiguous"))
            aligned += 1
            ambiguous += int(is_ambiguous)
            start_errors_ms.append(start_error)
            slope_abs_errors.append(slope_error)
            slope_relative_errors.append(slope_relative)
            margins.append(margin)
            scores.append(score)
            case_payload.update({
                "predicted_source_start_s": predicted_start,
                "predicted_slope": predicted_slope,
                "source_start_abs_error_ms": start_error,
                "slope_abs_error": slope_error,
                "slope_relative_error": slope_relative,
                "fused_score": score,
                "margin": margin,
                "ambiguous": is_ambiguous,
            })
        else:
            case_payload.update({
                "predicted_source_start_s": None,
                "predicted_slope": None,
                "source_start_abs_error_ms": None,
                "slope_abs_error": None,
                "slope_relative_error": None,
                "fused_score": None,
                "margin": None,
                "ambiguous": None,
                "failure_reason": run_row.get("failure_reason"),
            })
        cases.append(case_payload)

    total = len(cases)
    evaluator_revision = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    artifact: dict[str, Any] = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "authority": "evaluation_only_no_production_timing_authority",
        "partition": truth["partition"],
        "source_audit_sha256": truth_audit_sha,
        **holdout_provenance,
        "run_artifact_sha256": run["artifact_sha256"],
        "truth_artifact_sha256": truth["artifact_sha256"],
        "evaluator_revision": evaluator_revision,
        "record_count": total,
        "aligned_count": aligned,
        "prediction_coverage": aligned / total if total else 0.0,
        "ambiguous_count": ambiguous,
        "ambiguous_fraction_of_aligned": ambiguous / aligned if aligned else None,
        "source_start_abs_error_ms": _summary(start_errors_ms),
        "slope_abs_error": _summary(slope_abs_errors),
        "slope_relative_error": _summary(slope_relative_errors),
        "fused_score": _summary(scores),
        "margin": _summary(margins),
        "cases": cases,
        "automatic_mutation_allowed": False,
        "subtitle_mutation_performed": False,
    }
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"evaluation output already exists: {args.out}")
    artifact = evaluate(run_path=args.run, truth_path=args.truth)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, artifact)
    print(json.dumps({
        "out": str(args.out),
        "partition": artifact["partition"],
        "prediction_coverage": artifact["prediction_coverage"],
        "median_source_start_abs_error_ms": artifact["source_start_abs_error_ms"]["median"],
        "p90_source_start_abs_error_ms": artifact["source_start_abs_error_ms"]["p90"],
        "artifact_sha256": artifact["artifact_sha256"],
        "automatic_mutation_allowed": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
