#!/usr/bin/env python3
"""Evaluate the 3B Mandarin singing observer on calibration-only Human Anchor gold."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import v4_evaluate_wav2vec2_ctc_calibration as metric_engine

SCHEMA_VERSION = "mandarin-lyricalignment-calibration-evaluation-1.0"
EXPECTED_RUN_SCHEMA = "mandarin-lyricalignment-observer-run-1.0"
EXPECTED_BACKEND_ID = "mandarin_lyricalignment_asru2023_ctc"


class MandarinLyricAlignmentCalibrationEvaluationError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_run(path: Path) -> dict[str, Any]:
    payload = metric_engine._load_json(path)
    metric_engine._validate_embedded_hash(payload, label="3B observer run")
    if payload.get("schema_version") != EXPECTED_RUN_SCHEMA:
        raise MandarinLyricAlignmentCalibrationEvaluationError("unexpected 3B run schema")
    if payload.get("backend_id") != EXPECTED_BACKEND_ID:
        raise MandarinLyricAlignmentCalibrationEvaluationError("unexpected 3B backend identity")
    if payload.get("partition") != "calibration":
        raise MandarinLyricAlignmentCalibrationEvaluationError("3B evaluator accepts calibration only")
    if bool(payload.get("automatic_mutation_allowed")) or bool(payload.get("subtitle_mutation_performed")):
        raise MandarinLyricAlignmentCalibrationEvaluationError("3B calibration run must remain observer-only")
    return payload


def _transform_case(case: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(case)
    result["observer"] = result.pop("ctc")
    result["observer_vs_editor_effective_delta_ms"] = result.pop(
        "ctc_vs_editor_effective_delta_ms"
    )
    result["observer_better_than_editor"] = result.pop("ctc_better_than_editor")
    result["observer_posterior"] = result.pop("ctc_posterior")
    return result


def _transform_boundary(boundary: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(boundary)
    result["observer_top1"] = result.pop("ctc_top1")
    result["observer_vs_editor"] = result.pop("ctc_vs_editor")
    result["observer_posterior"] = result.pop("ctc_posterior")
    cases = result.get("cases")
    if not isinstance(cases, list):
        raise MandarinLyricAlignmentCalibrationEvaluationError("metric engine omitted case rows")
    result["cases"] = [_transform_case(row) for row in cases if isinstance(row, Mapping)]
    if len(result["cases"]) != len(cases):
        raise MandarinLyricAlignmentCalibrationEvaluationError("metric engine returned malformed case row")
    return result


def evaluate(
    *,
    run_path: Path,
    gold_path: Path,
    outer_audit_path: Path,
    sofa_start_path: Path,
    sofa_end_path: Path,
    hubertfa_start_path: Path,
    hubertfa_end_path: Path,
) -> dict[str, Any]:
    run = _load_run(run_path)
    core = metric_engine.evaluate(
        run_path=run_path,
        gold_path=gold_path,
        outer_audit_path=outer_audit_path,
        sofa_start_path=sofa_start_path,
        sofa_end_path=sofa_end_path,
        hubertfa_start_path=hubertfa_start_path,
        hubertfa_end_path=hubertfa_end_path,
    )
    if bool(core.get("holdout_read_or_run")):
        raise MandarinLyricAlignmentCalibrationEvaluationError("metric engine reports holdout access")
    boundaries = core.get("boundaries")
    if not isinstance(boundaries, Mapping) or set(boundaries) != {"start", "end"}:
        raise MandarinLyricAlignmentCalibrationEvaluationError("metric engine boundary payload is invalid")
    wrapper_revision = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": "calibration_only_diagnostic_no_holdout_no_mutation",
        "evaluator_revision": wrapper_revision,
        "metric_engine_revision": core["evaluator_revision"],
        "observer_run_artifact_sha256": run["artifact_sha256"],
        "calibration_gold_view_artifact_sha256": core[
            "calibration_gold_view_artifact_sha256"
        ],
        "source_human_gold_artifact_sha256": core["source_human_gold_artifact_sha256"],
        "selection_lock_sha256": core["selection_lock_sha256"],
        "final_audio_sha256": core["final_audio_sha256"],
        "outer_audit_csv_sha256": core["outer_audit_csv_sha256"],
        "observer_revision": run["observer_revision"],
        "backend_id": run["backend_id"],
        "baseline_artifacts": core["baseline_artifacts"],
        "boundaries": {
            "start": _transform_boundary(boundaries["start"]),
            "end": _transform_boundary(boundaries["end"]),
        },
        "holdout_read_or_run": False,
        "automatic_mutation_allowed": False,
        "subtitle_mutation_performed": False,
    }
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"evaluation output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--outer-audit", type=Path, required=True)
    parser.add_argument("--sofa-start", type=Path, required=True)
    parser.add_argument("--sofa-end", type=Path, required=True)
    parser.add_argument("--hubertfa-start", type=Path, required=True)
    parser.add_argument("--hubertfa-end", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    artifact = evaluate(
        run_path=args.run,
        gold_path=args.gold,
        outer_audit_path=args.outer_audit,
        sofa_start_path=args.sofa_start,
        sofa_end_path=args.sofa_end,
        hubertfa_start_path=args.hubertfa_start,
        hubertfa_end_path=args.hubertfa_end,
    )
    _atomic_json(args.out, artifact)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "backend_id": artifact["backend_id"],
                "observer_revision": artifact["observer_revision"],
                "artifact_sha256": artifact["artifact_sha256"],
                "holdout_read_or_run": artifact["holdout_read_or_run"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
