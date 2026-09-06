#!/usr/bin/env python3
"""Evaluate a calibration-only Wav2Vec2 CTC observer run against Human Anchor gold.

This is research/evaluation only. It never grants production authority, never opens a
holdout partition, and never mutates subtitles. Besides top-1 boundary error, it keeps
N-best uncertainty diagnostics so a multimodal posterior is not misreported as a single
confident timestamp.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "wav2vec2-ctc-calibration-evaluation-1.0"
CALIBRATION_GOLD_VIEW_SCHEMA_VERSION = "human-boundary-gold-calibration-view-1.0"
FPS = 30.0
FRAME_MS = 1000.0 / FPS
CATASTROPHIC_FRAMES = 15.0


class Wav2Vec2CTCCalibrationEvaluationError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise Wav2Vec2CTCCalibrationEvaluationError(f"JSON root must be object: {path}")
    return payload


def _validate_embedded_hash(payload: Mapping[str, Any], *, label: str) -> None:
    claimed = str(payload.get("artifact_sha256") or "")
    if len(claimed) != 64:
        raise Wav2Vec2CTCCalibrationEvaluationError(f"{label} lacks artifact SHA")
    without_hash = dict(payload)
    without_hash.pop("artifact_sha256", None)
    if claimed != _sha_json(without_hash):
        raise Wav2Vec2CTCCalibrationEvaluationError(f"{label} artifact SHA mismatch")


def _p90(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(0.90 * len(ordered)))
    return ordered[rank - 1]


def _summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_count: int,
) -> dict[str, Any]:
    predicted = [row for row in rows if row.get("predicted_ms") is not None]
    raw_ms = [float(row["raw_abs_error_ms"]) for row in predicted]
    effective_ms = [float(row["effective_error_ms"]) for row in predicted]
    effective_frames = [value / FRAME_MS for value in effective_ms]
    return {
        "gold_boundary_count": expected_count,
        "prediction_count": len(predicted),
        "prediction_coverage": len(predicted) / expected_count if expected_count else 0.0,
        "mean_raw_abs_error_ms": mean(raw_ms) if raw_ms else None,
        "median_raw_abs_error_ms": median(raw_ms) if raw_ms else None,
        "p90_raw_abs_error_ms": _p90(raw_ms),
        "max_raw_abs_error_ms": max(raw_ms) if raw_ms else None,
        "mean_effective_error_ms": mean(effective_ms) if effective_ms else None,
        "median_effective_error_ms": median(effective_ms) if effective_ms else None,
        "p90_effective_error_ms": _p90(effective_ms),
        "max_effective_error_ms": max(effective_ms) if effective_ms else None,
        "median_effective_error_frames": median(effective_frames) if effective_frames else None,
        "p90_effective_error_frames": _p90(effective_frames),
        "max_effective_error_frames": max(effective_frames) if effective_frames else None,
        "catastrophic_count": sum(value >= CATASTROPHIC_FRAMES for value in effective_frames),
        "catastrophic_fraction": (
            sum(value >= CATASTROPHIC_FRAMES for value in effective_frames) / len(effective_frames)
            if effective_frames
            else None
        ),
    }


def _error_row(
    *,
    record_id: str,
    track: str,
    gold_ms: int,
    uncertainty_ms: int,
    predicted_ms: int | None,
) -> dict[str, Any]:
    if predicted_ms is None:
        return {
            "id": record_id,
            "track": track,
            "gold_ms": gold_ms,
            "gold_uncertainty_ms": uncertainty_ms,
            "predicted_ms": None,
            "raw_abs_error_ms": None,
            "effective_error_ms": None,
            "effective_error_frames": None,
        }
    raw = abs(int(predicted_ms) - int(gold_ms))
    effective = max(0, raw - int(uncertainty_ms))
    return {
        "id": record_id,
        "track": track,
        "gold_ms": gold_ms,
        "gold_uncertainty_ms": uncertainty_ms,
        "predicted_ms": int(predicted_ms),
        "raw_abs_error_ms": raw,
        "effective_error_ms": effective,
        "effective_error_frames": effective / FRAME_MS,
    }


def _load_prediction_map(path: Path) -> tuple[dict[str, int | None], dict[str, Any]]:
    payload = _load_json(path)
    _validate_embedded_hash(payload, label=str(path))
    records = payload.get("records")
    if not isinstance(records, list):
        raise Wav2Vec2CTCCalibrationEvaluationError("baseline prediction records must be list")
    result: dict[str, int | None] = {}
    for row in records:
        if not isinstance(row, Mapping):
            raise Wav2Vec2CTCCalibrationEvaluationError("baseline prediction row must be object")
        record_id = str(row.get("id") or "")
        value = row.get("predicted_ms")
        if not record_id or record_id in result:
            raise Wav2Vec2CTCCalibrationEvaluationError("baseline prediction IDs must be unique")
        result[record_id] = None if value is None else int(value)
    return result, payload


def _load_editor_map(path: Path) -> tuple[dict[str, dict[str, int]], str]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    result: dict[str, dict[str, int]] = {}
    for row in rows:
        case_id = str(row.get("case_id") or "")
        if not case_id or case_id in result:
            raise Wav2Vec2CTCCalibrationEvaluationError("editor audit case IDs must be unique")
        result[case_id] = {
            "start": int(row["editor_start_ms_reference_only"]),
            "end": int(row["editor_end_ms_reference_only"]),
        }
    return result, digest


def _posterior_case(
    *,
    hypothesis_payload: Mapping[str, Any],
    gold_ms: int,
    uncertainty_ms: int,
) -> dict[str, Any]:
    hypotheses = hypothesis_payload.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        raise Wav2Vec2CTCCalibrationEvaluationError("aligned CTC evidence lacks hypotheses")
    parsed: list[tuple[int, float]] = []
    for row in hypotheses:
        if not isinstance(row, Mapping):
            raise Wav2Vec2CTCCalibrationEvaluationError("CTC hypothesis row must be object")
        parsed.append((int(row["boundary_ms"]), float(row["probability"])))
    oracle_raw = min(abs(boundary_ms - gold_ms) for boundary_ms, _ in parsed)
    oracle_effective = max(0, oracle_raw - uncertainty_ms)
    result: dict[str, Any] = {
        "top1_probability": float(hypotheses[0]["probability"]),
        "residual_probability": float(hypothesis_payload.get("residual_probability") or 0.0),
        "posterior_entropy_bits": float(hypothesis_payload.get("posterior_entropy_bits") or 0.0),
        "top1_top2_margin": hypothesis_payload.get("top1_top2_margin"),
        "topk_oracle_raw_abs_error_ms": oracle_raw,
        "topk_oracle_effective_error_ms": oracle_effective,
        "topk_oracle_effective_error_frames": oracle_effective / FRAME_MS,
    }
    for frames in (1, 3, 5):
        radius = frames * FRAME_MS + uncertainty_ms
        result[f"selected_probability_mass_within_{frames}_frames"] = sum(
            probability
            for boundary_ms, probability in parsed
            if abs(boundary_ms - gold_ms) <= radius
        )
    return result


def _posterior_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"case_count": 0}
    oracle_frames = [float(row["topk_oracle_effective_error_frames"]) for row in rows]
    margins = [
        float(row["top1_top2_margin"])
        for row in rows
        if row.get("top1_top2_margin") is not None
    ]
    result = {
        "case_count": len(rows),
        "mean_top1_probability": mean(float(row["top1_probability"]) for row in rows),
        "median_posterior_entropy_bits": median(float(row["posterior_entropy_bits"]) for row in rows),
        "median_top1_top2_margin": median(margins) if margins else None,
        "mean_residual_probability": mean(float(row["residual_probability"]) for row in rows),
        "topk_oracle_median_effective_error_frames": median(oracle_frames),
        "topk_oracle_p90_effective_error_frames": _p90(oracle_frames),
        "topk_oracle_max_effective_error_frames": max(oracle_frames),
    }
    for frames in (1, 3, 5):
        key = f"selected_probability_mass_within_{frames}_frames"
        result[f"mean_{key}"] = mean(float(row[key]) for row in rows)
    return result


def _comparison_summary(
    ctc_rows: Sequence[Mapping[str, Any]],
    editor_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if len(ctc_rows) != len(editor_rows):
        raise Wav2Vec2CTCCalibrationEvaluationError("comparison rows must have equal length")
    deltas: list[float] = []
    for ctc, editor in zip(ctc_rows, editor_rows):
        ctc_error = ctc.get("effective_error_ms")
        editor_error = editor.get("effective_error_ms")
        if ctc_error is None or editor_error is None:
            continue
        deltas.append(float(ctc_error) - float(editor_error))
    return {
        "locked_case_count": len(ctc_rows),
        "comparable_count": len(deltas),
        "ctc_unavailable_count": len(ctc_rows) - len(deltas),
        "ctc_wins": sum(delta < 0.0 for delta in deltas),
        "ties": sum(delta == 0.0 for delta in deltas),
        "ctc_losses": sum(delta > 0.0 for delta in deltas),
        "mean_ctc_minus_editor_effective_error_ms": mean(deltas) if deltas else None,
        "median_ctc_minus_editor_effective_error_ms": median(deltas) if deltas else None,
        "best_ctc_improvement_ms": max((max(-delta, 0.0) for delta in deltas), default=None),
        "worst_ctc_degradation_ms": max((max(delta, 0.0) for delta in deltas), default=None),
    }


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
    run = _load_json(run_path)
    _validate_embedded_hash(run, label="CTC run")
    if str(run.get("partition") or "") != "calibration":
        raise Wav2Vec2CTCCalibrationEvaluationError("this evaluator accepts calibration runs only")
    if bool(run.get("automatic_mutation_allowed")) or bool(run.get("subtitle_mutation_performed")):
        raise Wav2Vec2CTCCalibrationEvaluationError("CTC calibration run must be observer-only")
    gold = _load_json(gold_path)
    _validate_embedded_hash(gold, label="human gold")
    if gold.get("schema_version") != CALIBRATION_GOLD_VIEW_SCHEMA_VERSION:
        raise Wav2Vec2CTCCalibrationEvaluationError("evaluator requires a calibration-only human gold view")
    gold_records = gold.get("records")
    if not isinstance(gold_records, list) or len(gold_records) != 16:
        raise Wav2Vec2CTCCalibrationEvaluationError("calibration gold view must contain exactly 16 rows")
    if any(
        not isinstance(row, Mapping)
        or row.get("partition") != "calibration"
        or row.get("population") != "outer"
        or row.get("boundary_kind") not in {"start", "end"}
        for row in gold_records
    ):
        raise Wav2Vec2CTCCalibrationEvaluationError("calibration gold view contains non-calibration records")
    gold_case_ids = {str(row.get("case_id") or "") for row in gold_records}
    if len(gold_case_ids) != 8 or "" in gold_case_ids:
        raise Wav2Vec2CTCCalibrationEvaluationError("calibration gold view must contain 8 unique cases")
    if sum(row.get("boundary_kind") == "start" for row in gold_records) != 8 or sum(
        row.get("boundary_kind") == "end" for row in gold_records
    ) != 8:
        raise Wav2Vec2CTCCalibrationEvaluationError("calibration gold view start/end counts are invalid")
    if bool(gold.get("contains_holdout_records")) or bool(gold.get("contains_internal_records")):
        raise Wav2Vec2CTCCalibrationEvaluationError("calibration gold view declares leaked records")
    source_gold_sha = str(gold.get("source_human_gold_artifact_sha256") or "")
    if len(source_gold_sha) != 64 or any(char not in "0123456789abcdef" for char in source_gold_sha):
        raise Wav2Vec2CTCCalibrationEvaluationError("calibration gold view lacks source artifact provenance")
    production_policy = gold.get("production_policy")
    if not isinstance(production_policy, Mapping):
        raise Wav2Vec2CTCCalibrationEvaluationError("calibration gold view lacks production policy")
    if float(production_policy.get("fps", -1.0)) != FPS or float(
        production_policy.get("catastrophic_error_frames", -1.0)
    ) != CATASTROPHIC_FRAMES:
        raise Wav2Vec2CTCCalibrationEvaluationError("calibration gold production policy disagrees with evaluator")
    if run.get("selection_lock_sha256") != gold.get("selection_lock_sha256"):
        raise Wav2Vec2CTCCalibrationEvaluationError("run/gold selection lock mismatch")
    if run.get("final_audio_sha256") != gold.get("final_audio_sha256"):
        raise Wav2Vec2CTCCalibrationEvaluationError("run/gold final audio mismatch")

    editor, audit_sha = _load_editor_map(outer_audit_path)
    if audit_sha != run.get("outer_audit_csv_sha256") or audit_sha != gold.get("outer_audit_csv_sha256"):
        raise Wav2Vec2CTCCalibrationEvaluationError("outer audit SHA mismatch")
    sofa_start, sofa_start_payload = _load_prediction_map(sofa_start_path)
    sofa_end, sofa_end_payload = _load_prediction_map(sofa_end_path)
    hubert_start, hubert_start_payload = _load_prediction_map(hubertfa_start_path)
    hubert_end, hubert_end_payload = _load_prediction_map(hubertfa_end_path)

    run_records = run.get("records")
    if not isinstance(run_records, list):
        raise Wav2Vec2CTCCalibrationEvaluationError("CTC run records must be list")
    run_by_case = {str(row.get("case_id") or ""): row for row in run_records if isinstance(row, Mapping)}
    if len(run_by_case) != len(run_records):
        raise Wav2Vec2CTCCalibrationEvaluationError("CTC run case IDs must be unique/non-empty")

    boundary_results: dict[str, Any] = {}
    for boundary_kind in ("start", "end"):
        scoped_gold = [
            row
            for row in gold.get("records", [])
            if isinstance(row, Mapping)
            and row.get("population") == "outer"
            and row.get("partition") == "calibration"
            and row.get("boundary_kind") == boundary_kind
        ]
        if len(scoped_gold) != 8:
            raise Wav2Vec2CTCCalibrationEvaluationError("expected exactly 8 calibration outer gold rows per kind")
        ctc_rows: list[dict[str, Any]] = []
        editor_rows: list[dict[str, Any]] = []
        sofa_rows: list[dict[str, Any]] = []
        hubert_rows: list[dict[str, Any]] = []
        posterior_rows: list[dict[str, Any]] = []
        case_rows: list[dict[str, Any]] = []
        baseline_map = sofa_start if boundary_kind == "start" else sofa_end
        hubert_map = hubert_start if boundary_kind == "start" else hubert_end
        for gold_row in scoped_gold:
            case_id = str(gold_row["case_id"])
            record_id = str(gold_row["id"])
            gold_ms = int(gold_row["gold_ms"])
            uncertainty = int(gold_row.get("gold_uncertainty_ms", 0))
            track = str(gold_row["track"])
            ctc_record = run_by_case.get(case_id)
            if ctc_record is None:
                raise Wav2Vec2CTCCalibrationEvaluationError("CTC run omitted a locked calibration case")
            predicted_key = f"predicted_{boundary_kind}_ms"
            ctc_predicted = ctc_record.get(predicted_key)
            ctc_predicted = None if ctc_predicted is None else int(ctc_predicted)
            ctc_error = _error_row(
                record_id=record_id,
                track=track,
                gold_ms=gold_ms,
                uncertainty_ms=uncertainty,
                predicted_ms=ctc_predicted,
            )
            editor_predicted = editor[case_id][boundary_kind]
            editor_error = _error_row(
                record_id=record_id,
                track=track,
                gold_ms=gold_ms,
                uncertainty_ms=uncertainty,
                predicted_ms=editor_predicted,
            )
            sofa_error = _error_row(
                record_id=record_id,
                track=track,
                gold_ms=gold_ms,
                uncertainty_ms=uncertainty,
                predicted_ms=baseline_map.get(record_id),
            )
            hubert_error = _error_row(
                record_id=record_id,
                track=track,
                gold_ms=gold_ms,
                uncertainty_ms=uncertainty,
                predicted_ms=hubert_map.get(record_id),
            )
            ctc_rows.append(ctc_error)
            editor_rows.append(editor_error)
            sofa_rows.append(sofa_error)
            hubert_rows.append(hubert_error)
            posterior = None
            evidence = ctc_record.get("evidence")
            if ctc_predicted is not None:
                if not isinstance(evidence, Mapping):
                    raise Wav2Vec2CTCCalibrationEvaluationError("aligned CTC row lacks evidence")
                boundary_evidence = evidence.get(boundary_kind)
                if not isinstance(boundary_evidence, Mapping):
                    raise Wav2Vec2CTCCalibrationEvaluationError("aligned CTC row lacks boundary posterior")
                posterior = _posterior_case(
                    hypothesis_payload=boundary_evidence,
                    gold_ms=gold_ms,
                    uncertainty_ms=uncertainty,
                )
                posterior_rows.append(posterior)
            delta = None
            ctc_better = None
            if ctc_error["effective_error_ms"] is not None:
                delta = float(ctc_error["effective_error_ms"]) - float(editor_error["effective_error_ms"])
                ctc_better = delta < 0.0
            case_rows.append(
                {
                    "id": record_id,
                    "case_id": case_id,
                    "track": track,
                    "gold_ms": gold_ms,
                    "gold_uncertainty_ms": uncertainty,
                    "ctc": ctc_error,
                    "editor": editor_error,
                    "sofa": sofa_error,
                    "hubertfa": hubert_error,
                    "ctc_vs_editor_effective_delta_ms": delta,
                    "ctc_better_than_editor": ctc_better,
                    "ctc_posterior": posterior,
                }
            )
        boundary_results[boundary_kind] = {
            "ctc_top1": _summary(ctc_rows, expected_count=8),
            "editor": _summary(editor_rows, expected_count=8),
            "sofa": _summary(sofa_rows, expected_count=8),
            "hubertfa": _summary(hubert_rows, expected_count=8),
            "ctc_vs_editor": _comparison_summary(ctc_rows, editor_rows),
            "ctc_posterior": _posterior_summary(posterior_rows),
            "cases": case_rows,
        }

    evaluator_revision = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": "calibration_only_diagnostic_no_holdout_no_mutation",
        "evaluator_revision": evaluator_revision,
        "ctc_run_artifact_sha256": run["artifact_sha256"],
        "calibration_gold_view_artifact_sha256": gold["artifact_sha256"],
        "source_human_gold_artifact_sha256": source_gold_sha,
        "selection_lock_sha256": gold["selection_lock_sha256"],
        "final_audio_sha256": gold["final_audio_sha256"],
        "outer_audit_csv_sha256": audit_sha,
        "ctc_observer_revision": run["observer_revision"],
        "baseline_artifacts": {
            "sofa_start": sofa_start_payload["artifact_sha256"],
            "sofa_end": sofa_end_payload["artifact_sha256"],
            "hubertfa_start": hubert_start_payload["artifact_sha256"],
            "hubertfa_end": hubert_end_payload["artifact_sha256"],
        },
        "boundaries": boundary_results,
        "holdout_read_or_run": False,
        "automatic_mutation_allowed": False,
        "subtitle_mutation_performed": False,
    }
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


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
    if args.out.exists():
        raise FileExistsError("calibration evaluation output must be a new path")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + ".tmp")
    temporary.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.out)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "artifact_sha256": artifact["artifact_sha256"],
                "start_ctc": artifact["boundaries"]["start"]["ctc_top1"],
                "end_ctc": artifact["boundaries"]["end"]["ctc_top1"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
