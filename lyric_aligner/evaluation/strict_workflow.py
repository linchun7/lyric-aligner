"""Strict, privacy-safe calibration and blind-test workflow contracts.

This is the canonical P1 workflow. It deliberately does not read prediction or
QA files from any split other than the split being evaluated. Cross-split
leakage checks use opaque manifest metadata only. Calibration selection locks
candidate id, candidate revision and runtime identity before blind_test is run.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable


STRICT_EVALUATION_SCHEMA = "3.0"
STRICT_DATASET_SCHEMA = "1.1"
STRICT_SELECTION_SCHEMA = "1.0"
STRICT_GATE_SCHEMA = "1.0"
ALLOWED_SPLITS = {"train", "calibration", "blind_test"}


class StrictEvaluationError(ValueError):
    """Raised when a private evaluation workflow violates isolation contracts."""


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise StrictEvaluationError(f"{label} must be a JSON object")
    return payload


def resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def validate_manifest_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    if str(payload.get("schema_version") or "") != STRICT_DATASET_SCHEMA:
        raise StrictEvaluationError(
            f"strict calibration dataset schema_version must be {STRICT_DATASET_SCHEMA}"
        )
    dataset = str(payload.get("dataset") or "").strip()
    revision = str(payload.get("dataset_revision") or "").strip()
    if not dataset or not revision:
        raise StrictEvaluationError("dataset and dataset_revision are required")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise StrictEvaluationError("dataset manifest requires cases")

    ids: set[str] = set()
    group_splits: dict[str, set[str]] = defaultdict(set)
    split_counts: dict[str, int] = defaultdict(int)
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise StrictEvaluationError(f"case {index} must be an object")
        case_id = str(case.get("id") or "").strip()
        split = str(case.get("split") or "").strip()
        source_group = str(case.get("source_group") or "").strip()
        if not case_id or case_id in ids:
            raise StrictEvaluationError("case ids must be unique/non-empty")
        if split not in ALLOWED_SPLITS:
            raise StrictEvaluationError(f"case {case_id} has invalid split")
        if not source_group:
            raise StrictEvaluationError(f"case {case_id} is missing opaque source_group")
        for required in ("reference_srt", "predicted_srt", "qa_json"):
            if not str(case.get(required) or "").strip():
                raise StrictEvaluationError(f"case {case_id} is missing {required}")
        ids.add(case_id)
        split_counts[split] += 1
        group_splits[source_group].add(split)

    leaked = {
        group: sorted(splits)
        for group, splits in group_splits.items()
        if len(splits) > 1
    }
    if leaked:
        group = sorted(leaked)[0]
        raise StrictEvaluationError(
            f"source_group crosses dataset splits: {group} -> {','.join(leaked[group])}"
        )
    return {
        "dataset": dataset,
        "dataset_revision": revision,
        "case_count": len(cases),
        "split_counts": dict(sorted(split_counts.items())),
        "source_group_count": len(group_splits),
        "source_group_isolation_enforced": True,
    }


def selected_cases(payload: dict[str, Any], split: str) -> list[dict[str, Any]]:
    if split not in ALLOWED_SPLITS:
        raise StrictEvaluationError(f"unsupported split {split!r}")
    rows = [case for case in payload["cases"] if case.get("split") == split]
    if not rows:
        raise StrictEvaluationError(f"dataset contains no cases for split {split!r}")
    return rows


def validate_selected_files(
    manifest_path: Path,
    cases: Iterable[dict[str, Any]],
) -> None:
    """Only selected-split prediction/QA files are resolved or required."""

    for case in cases:
        case_id = str(case["id"])
        for key in ("reference_srt", "predicted_srt", "qa_json"):
            path = resolve(manifest_path.parent, str(case[key]))
            if not path.is_file():
                raise StrictEvaluationError(
                    f"selected case {case_id} {key} does not exist"
                )


def runtime_identity(
    manifest_path: Path,
    cases: Iterable[dict[str, Any]],
) -> dict[str, str]:
    identities: set[tuple[str, str, str]] = set()
    for case in cases:
        qa = load_json(
            resolve(manifest_path.parent, str(case["qa_json"])),
            label=f"QA for {case['id']}",
        )
        identity = (
            str(qa.get("algorithm_version") or "").strip(),
            str(qa.get("calibration_profile_version") or "").strip(),
            str(qa.get("calibration_profile_id") or "").strip(),
        )
        if not all(identity):
            raise StrictEvaluationError(
                f"QA for {case['id']} is missing runtime identity"
            )
        identities.add(identity)
    if len(identities) != 1:
        raise StrictEvaluationError(
            "selected split mixes algorithm/calibration runtime identities"
        )
    algorithm, profile_version, profile_id = next(iter(identities))
    return {
        "algorithm_version": algorithm,
        "calibration_profile_version": profile_version,
        "calibration_profile_id": profile_id,
    }


def ground_truth_identity(
    manifest_path: Path,
    payload: dict[str, Any],
    split: str,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in selected_cases(payload, split):
        reference = resolve(manifest_path.parent, str(case["reference_srt"]))
        if not reference.is_file():
            raise StrictEvaluationError(
                f"selected case {case['id']} reference_srt does not exist"
            )
        rows.append(
            {
                "id": str(case["id"]),
                "split": split,
                "language": str(case.get("language") or "unknown"),
                "source_group": str(case["source_group"]),
                "reference_srt_sha256": file_sha256(reference),
                "expected_cuts": deepcopy(case.get("expected_cuts", [])),
                "expected_cut_ids": sorted(
                    str(value) for value in case.get("expected_cut_ids", [])
                ),
                "expected_overlaps": deepcopy(case.get("expected_overlaps", [])),
                "expected_occurrences": deepcopy(case.get("expected_occurrences", [])),
                "audio_duration_seconds": float(case.get("audio_duration_seconds") or 0.0),
            }
        )
    rows.sort(key=lambda row: row["id"])
    core = {
        "dataset": str(payload["dataset"]),
        "dataset_revision": str(payload["dataset_revision"]),
        "split": split,
        "cases": rows,
    }
    return {
        "dataset_ground_truth_sha256": canonical_sha256(core),
        "case_ids_sha256": canonical_sha256([row["id"] for row in rows]),
        "case_count": len(rows),
    }


def _cut_time_ms(value: Any) -> float:
    if isinstance(value, dict):
        for key in ("time_ms", "cut_time_ms", "mix_time_ms"):
            if key in value:
                return float(value[key])
        for key in ("time", "cut_time", "mix_time"):
            if key in value:
                return float(value[key]) * 1000.0
        raise StrictEvaluationError("cut annotation has no supported time field")
    return float(value)


def match_cut_errors(expected: list[float], predicted: list[float], tolerance: float) -> list[float]:
    pairs = sorted(
        (abs(truth - pred), i, j)
        for i, truth in enumerate(expected)
        for j, pred in enumerate(predicted)
        if abs(truth - pred) <= tolerance
    )
    used_expected: set[int] = set()
    used_predicted: set[int] = set()
    errors: list[float] = []
    for error, i, j in pairs:
        if i in used_expected or j in used_predicted:
            continue
        used_expected.add(i)
        used_predicted.add(j)
        errors.append(float(error))
    return errors


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def cut_boundary_metrics(cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    errors: list[float] = []
    timed_cases = 0
    for case in cases:
        if "expected_cuts" not in case and "predicted_cuts" not in case:
            continue
        expected = [_cut_time_ms(value) for value in case.get("expected_cuts", [])]
        predicted = [_cut_time_ms(value) for value in case.get("predicted_cuts", [])]
        tolerance = float(case.get("cut_tolerance_ms", 500.0))
        errors.extend(match_cut_errors(expected, predicted, tolerance))
        timed_cases += 1
    return {
        "cut_time_annotation_case_count": timed_cases,
        "cut_boundary_match_count": len(errors),
        "cut_boundary_mae_ms": round(statistics.fmean(errors), 3) if errors else 0.0,
        "cut_boundary_p50_ms": round(percentile(errors, 0.50), 3),
        "cut_boundary_p90_ms": round(percentile(errors, 0.90), 3),
        "cut_boundary_p95_ms": round(percentile(errors, 0.95), 3),
        "cut_boundary_within_250ms_rate": round(
            sum(value <= 250.0 for value in errors) / len(errors), 6
        )
        if errors
        else 0.0,
        "cut_boundary_within_500ms_rate": round(
            sum(value <= 500.0 for value in errors) / len(errors), 6
        )
        if errors
        else 0.0,
    }


def load_strict_evaluation(path: Path) -> dict[str, Any]:
    payload = load_json(path, label="strict evaluation")
    if payload.get("schema_version") != STRICT_EVALUATION_SCHEMA:
        raise StrictEvaluationError("strict evaluation schema_version mismatch")
    for key in ("candidate_id", "candidate_revision", "dataset_revision"):
        if not str(payload.get(key) or "").strip():
            raise StrictEvaluationError(f"strict evaluation is missing {key}")
    if not isinstance(payload.get("runtime_identity"), dict):
        raise StrictEvaluationError("strict evaluation is missing runtime_identity")
    if not isinstance(payload.get("dataset_identity"), dict):
        raise StrictEvaluationError("strict evaluation is missing dataset_identity")
    return payload


def load_policy(path: Path, expected_split: str) -> dict[str, Any]:
    policy = load_json(path, label="gate policy")
    if policy.get("schema_version") != STRICT_GATE_SCHEMA:
        raise StrictEvaluationError("gate policy schema_version mismatch")
    if policy.get("split") != expected_split:
        raise StrictEvaluationError("gate policy split mismatch")
    gates = policy.get("gates")
    if not isinstance(gates, list) or not gates:
        raise StrictEvaluationError("gate policy requires gates")
    if expected_split == "calibration" and not policy.get("ranking"):
        raise StrictEvaluationError("calibration policy requires deterministic ranking")
    for gate in gates:
        if not isinstance(gate, dict):
            raise StrictEvaluationError("gate must be an object")
        if gate.get("direction") not in {"higher", "lower"}:
            raise StrictEvaluationError("gate direction must be higher/lower")
        if "max_regression_abs" not in gate:
            raise StrictEvaluationError("gate must explicitly declare max_regression_abs")
    return policy


def metric_value(evaluation: dict[str, Any], scope: str, metric: str) -> float:
    metrics = evaluation.get("overall") if scope == "overall" else evaluation.get("groups", {}).get(scope)
    if not isinstance(metrics, dict) or metric not in metrics:
        raise StrictEvaluationError(f"missing metric {scope}/{metric}")
    return float(metrics[metric])


def validate_comparable(baseline: dict[str, Any], candidate: dict[str, Any], split: str) -> None:
    if baseline.get("evaluated_split") != split or candidate.get("evaluated_split") != split:
        raise StrictEvaluationError("evaluation split mismatch")
    if baseline.get("dataset_revision") != candidate.get("dataset_revision"):
        raise StrictEvaluationError("baseline/candidate dataset_revision differs")
    for key in ("dataset_ground_truth_sha256", "case_ids_sha256", "case_count"):
        if baseline["dataset_identity"].get(key) != candidate["dataset_identity"].get(key):
            raise StrictEvaluationError("baseline/candidate ground-truth identity differs")


def evaluate_gates(baseline: dict[str, Any], candidate: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    split = str(policy["split"])
    validate_comparable(baseline, candidate, split)
    rows: list[dict[str, Any]] = []
    passed = True
    for gate in policy["gates"]:
        scope = str(gate["scope"])
        metric = str(gate["metric"])
        direction = str(gate["direction"])
        baseline_value = metric_value(baseline, scope, metric)
        candidate_value = metric_value(candidate, scope, metric)
        delta = candidate_value - baseline_value
        regression = (
            max(0.0, baseline_value - candidate_value)
            if direction == "higher"
            else max(0.0, candidate_value - baseline_value)
        )
        reasons: list[str] = []
        if regression > float(gate["max_regression_abs"]) + 1e-12:
            reasons.append("regression exceeds max_regression_abs")
        if "min_candidate" in gate and candidate_value < float(gate["min_candidate"]):
            reasons.append("candidate below min_candidate")
        if "max_candidate" in gate and candidate_value > float(gate["max_candidate"]):
            reasons.append("candidate above max_candidate")
        if "min_improvement_abs" in gate:
            improvement = delta if direction == "higher" else -delta
            if improvement < float(gate["min_improvement_abs"]) - 1e-12:
                reasons.append("improvement below min_improvement_abs")
        row_passed = not reasons
        passed = passed and row_passed
        rows.append(
            {
                "scope": scope,
                "metric": metric,
                "direction": direction,
                "baseline": baseline_value,
                "candidate": candidate_value,
                "delta": delta,
                "regression_abs": regression,
                "passed": row_passed,
                "reasons": reasons,
            }
        )
    return {"passed": passed, "gates": rows}


def ranking_key(evaluation: dict[str, Any], ranking: Iterable[dict[str, Any]]) -> tuple[float, ...]:
    values: list[float] = []
    for item in ranking:
        value = metric_value(evaluation, str(item["scope"]), str(item["metric"]))
        values.append(value if item["direction"] == "higher" else -value)
    return tuple(values)


def select_candidate(baseline: dict[str, Any], candidates: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    passing: list[tuple[dict[str, Any], dict[str, Any]]] = []
    gate_results: dict[str, Any] = {}
    ids: set[str] = set()
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        if candidate_id in ids:
            raise StrictEvaluationError("candidate_id must be unique")
        ids.add(candidate_id)
        result = evaluate_gates(baseline, candidate, policy)
        gate_results[candidate_id] = result
        if result["passed"]:
            passing.append((candidate, result))
    if not passing:
        raise StrictEvaluationError("no calibration candidate passes all gates")
    ranking = policy.get("ranking") or []
    passing.sort(
        key=lambda pair: (ranking_key(pair[0], ranking), str(pair[0]["candidate_id"])),
        reverse=True,
    )
    selected, selected_gate = passing[0]
    return {
        "selected_candidate_id": selected["candidate_id"],
        "selected_candidate_revision": selected["candidate_revision"],
        "selected_runtime_identity": deepcopy(selected["runtime_identity"]),
        "selected_gate": selected_gate,
        "candidate_gate_results": gate_results,
    }


def selection_hash(payload: dict[str, Any]) -> str:
    return canonical_sha256({key: value for key, value in payload.items() if key != "selection_payload_sha256"})


def load_selection(path: Path) -> dict[str, Any]:
    payload = load_json(path, label="selection artifact")
    if payload.get("schema_version") != STRICT_SELECTION_SCHEMA:
        raise StrictEvaluationError("selection artifact schema_version mismatch")
    expected = str(payload.get("selection_payload_sha256") or "")
    if not expected or expected != selection_hash(payload):
        raise StrictEvaluationError("selection artifact payload hash mismatch")
    return payload


def validate_blind_lock(selection: dict[str, Any], candidate: dict[str, Any]) -> None:
    if candidate.get("candidate_id") != selection.get("selected_candidate_id"):
        raise StrictEvaluationError("blind candidate id differs from calibration selection")
    if candidate.get("candidate_revision") != selection.get("selected_candidate_revision"):
        raise StrictEvaluationError("blind candidate revision differs from calibration selection")
    if candidate.get("runtime_identity") != selection.get("selected_runtime_identity"):
        raise StrictEvaluationError("blind runtime identity differs from calibration selection")
    if candidate.get("dataset_revision") != selection.get("dataset_revision"):
        raise StrictEvaluationError("blind dataset_revision differs from calibration selection")
