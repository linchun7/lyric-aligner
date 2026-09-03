"""Privacy-safe dataset identity, split isolation and boundary metrics.

This module intentionally hashes ground-truth material instead of exposing lyric
text. Dataset schema 1.1 adds an opaque ``source_group`` that must not cross
train/calibration/blind_test; clips from the same song/version family should use
the same source_group. Schema 1.0 remains readable for legacy regression, but
strict calibration/blind workflows can require source_group coverage.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from lyric_aligner.evaluation.structural_scenarios import (
    StructuralScenarioError,
    structural_scenarios,
)


EVALUATION_PROTOCOL_VERSION = "1.0"
SUPPORTED_DATASET_SCHEMAS = {"1.0", "1.1"}
ALLOWED_SPLITS = {"train", "calibration", "blind_test"}


class EvaluationProtocolError(ValueError):
    """Raised when a calibration/blind dataset violates evaluation contracts."""


def _case_structural_scenarios(case: dict[str, Any]) -> tuple[str, ...]:
    try:
        return structural_scenarios(case)
    except StructuralScenarioError as exc:
        case_id = str(case.get("id") or "<unknown>")
        raise EvaluationProtocolError(f"dataset case {case_id}: {exc}") from exc


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def load_dataset_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise EvaluationProtocolError("dataset manifest must be a JSON object")
    schema = str(payload.get("schema_version") or "")
    if schema not in SUPPORTED_DATASET_SCHEMAS:
        raise EvaluationProtocolError(
            "dataset schema_version must be one of "
            + ", ".join(sorted(SUPPORTED_DATASET_SCHEMAS))
        )
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationProtocolError("dataset manifest must contain cases")
    return payload


def validate_dataset_manifest(
    path: Path,
    payload: dict[str, Any],
    *,
    require_source_groups: bool = False,
) -> dict[str, Any]:
    """Validate privacy-safe case identity and cross-split isolation."""

    schema = str(payload.get("schema_version") or "")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationProtocolError("dataset manifest must contain cases")

    if schema == "1.0" and any(
        isinstance(case, dict) and "structural_scenarios" in case for case in cases
    ):
        raise EvaluationProtocolError(
            "structural_scenarios requires dataset schema_version 1.1"
        )

    ids: set[str] = set()
    group_splits: dict[str, set[str]] = defaultdict(set)
    source_group_missing = 0
    split_counts: dict[str, int] = defaultdict(int)
    languages: set[str] = set()
    structural_counts: dict[str, int] = defaultdict(int)

    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise EvaluationProtocolError(f"dataset case {index} must be an object")
        case_id = str(case.get("id") or "").strip()
        if not case_id:
            raise EvaluationProtocolError(f"dataset case {index} is missing id")
        if case_id in ids:
            raise EvaluationProtocolError(f"duplicate dataset case id {case_id}")
        ids.add(case_id)

        split = str(case.get("split") or "").strip()
        if split not in ALLOWED_SPLITS:
            raise EvaluationProtocolError(
                f"dataset case {case_id} has unsupported split {split!r}"
            )
        split_counts[split] += 1
        languages.add(str(case.get("language") or "unknown"))
        if schema == "1.1":
            for scenario in _case_structural_scenarios(case):
                structural_counts[scenario] += 1

        reference_value = str(case.get("reference_srt") or "").strip()
        predicted_value = str(case.get("predicted_srt") or "").strip()
        if not reference_value or not predicted_value:
            raise EvaluationProtocolError(
                f"dataset case {case_id} requires reference_srt and predicted_srt"
            )
        reference_path = _resolve(path.parent, reference_value)
        predicted_path = _resolve(path.parent, predicted_value)
        if not reference_path.is_file():
            raise EvaluationProtocolError(
                f"dataset case {case_id} reference_srt does not exist"
            )
        if not predicted_path.is_file():
            raise EvaluationProtocolError(
                f"dataset case {case_id} predicted_srt does not exist"
            )

        source_group = str(case.get("source_group") or "").strip()
        if source_group:
            group_splits[source_group].add(split)
        else:
            source_group_missing += 1
            if schema == "1.1" or require_source_groups:
                raise EvaluationProtocolError(
                    f"dataset case {case_id} is missing opaque source_group"
                )

    leaked = {
        group: sorted(splits)
        for group, splits in group_splits.items()
        if len(splits) > 1
    }
    if leaked:
        first_group = sorted(leaked)[0]
        raise EvaluationProtocolError(
            "source_group crosses dataset splits: "
            f"{first_group} -> {','.join(leaked[first_group])}"
        )

    return {
        "schema_version": schema,
        "case_count": len(cases),
        "split_counts": dict(sorted(split_counts.items())),
        "language_count": len(languages),
        "source_group_count": len(group_splits),
        "source_group_missing_count": source_group_missing,
        "source_group_isolation_enforced": source_group_missing == 0,
        **(
            {"structural_scenario_counts": dict(sorted(structural_counts.items()))}
            if schema == "1.1"
            else {}
        ),
    }


def _ground_truth_case_identity(base: Path, case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case["id"])
    reference_path = _resolve(base, str(case["reference_srt"]))
    identity = {
        "id": case_id,
        "split": str(case.get("split") or ""),
        "language": str(case.get("language") or "unknown"),
        "source_group": str(case.get("source_group") or ""),
        "reference_srt_sha256": _file_sha256(reference_path),
        "expected_cuts": deepcopy(case.get("expected_cuts", [])),
        "expected_cut_ids": sorted(str(value) for value in case.get("expected_cut_ids", [])),
        "expected_overlaps": deepcopy(case.get("expected_overlaps", [])),
        "expected_occurrences": deepcopy(case.get("expected_occurrences", [])),
        "audio_duration_seconds": float(case.get("audio_duration_seconds") or 0.0),
    }
    if "structural_scenarios" in case:
        identity["structural_scenarios"] = list(_case_structural_scenarios(case))
    return identity


def dataset_ground_truth_identity(
    path: Path,
    payload: dict[str, Any],
    *,
    split: str | None = None,
) -> dict[str, Any]:
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise EvaluationProtocolError("dataset cases must be a list")
    selected = [
        case
        for case in cases
        if split is None or str(case.get("split") or "") == split
    ]
    if not selected:
        raise EvaluationProtocolError(
            f"dataset contains no cases for split {split!r}"
        )
    case_rows = [
        _ground_truth_case_identity(path.parent, case)
        for case in selected
    ]
    case_rows.sort(key=lambda row: row["id"])
    core = {
        "protocol_version": EVALUATION_PROTOCOL_VERSION,
        "dataset": str(payload.get("dataset") or "private-dataset"),
        "split": split or "all",
        "cases": case_rows,
    }
    return {
        "dataset_ground_truth_sha256": _canonical_sha256(core),
        "case_count": len(case_rows),
        "case_ids_sha256": _canonical_sha256([row["id"] for row in case_rows]),
        "split": split or "all",
    }


def _cut_time_ms(value: Any) -> float:
    if isinstance(value, dict):
        for key in ("time_ms", "cut_time_ms", "mix_time_ms"):
            if key in value:
                return float(value[key])
        for key in ("time", "cut_time", "mix_time"):
            if key in value:
                return float(value[key]) * 1000.0
        raise EvaluationProtocolError("cut annotation has no supported time field")
    return float(value)


def match_time_errors(
    expected: Iterable[float],
    predicted: Iterable[float],
    tolerance_ms: float,
) -> list[float]:
    """Globally greedy one-to-one nearest matching under a tolerance."""

    expected_rows = list(float(value) for value in expected)
    predicted_rows = list(float(value) for value in predicted)
    candidates = sorted(
        (
            abs(truth - pred),
            truth_index,
            pred_index,
        )
        for truth_index, truth in enumerate(expected_rows)
        for pred_index, pred in enumerate(predicted_rows)
        if abs(truth - pred) <= tolerance_ms
    )
    used_truth: set[int] = set()
    used_predicted: set[int] = set()
    errors: list[float] = []
    for error, truth_index, pred_index in candidates:
        if truth_index in used_truth or pred_index in used_predicted:
            continue
        used_truth.add(truth_index)
        used_predicted.add(pred_index)
        errors.append(float(error))
    return errors


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction)


def _boundary_summary(errors: list[float]) -> dict[str, Any]:
    return {
        "cut_boundary_match_count": len(errors),
        "cut_boundary_mae_ms": round(statistics.fmean(errors), 3) if errors else 0.0,
        "cut_boundary_p50_ms": round(_percentile(errors, 0.50), 3),
        "cut_boundary_p90_ms": round(_percentile(errors, 0.90), 3),
        "cut_boundary_p95_ms": round(_percentile(errors, 0.95), 3),
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


def cut_boundary_metrics_by_scope(
    payload: dict[str, Any],
    *,
    selected_split: str | None = None,
) -> dict[str, dict[str, Any]]:
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise EvaluationProtocolError("dataset cases must be a list")
    errors_by_scope: dict[str, list[float]] = defaultdict(list)
    timed_cases_by_scope: dict[str, int] = defaultdict(int)
    legacy_cases_by_scope: dict[str, int] = defaultdict(int)

    for case in cases:
        if selected_split is not None and str(case.get("split") or "") != selected_split:
            continue
        language = str(case.get("language") or "unknown")
        scopes = ["overall", f"language:{language}"]
        if str(payload.get("schema_version") or "") == "1.1":
            scopes.extend(
                f"structural:{scenario}"
                for scenario in _case_structural_scenarios(case)
            )
        expected = case.get("expected_cuts")
        predicted = case.get("predicted_cuts")
        if expected is None and predicted is None:
            for scope in scopes:
                legacy_cases_by_scope[scope] += int(
                    bool(case.get("expected_cut_ids") or case.get("predicted_cut_ids"))
                )
            continue
        expected_times = [_cut_time_ms(value) for value in (expected or [])]
        predicted_times = [_cut_time_ms(value) for value in (predicted or [])]
        tolerance = float(case.get("cut_tolerance_ms", 500.0))
        errors = match_time_errors(expected_times, predicted_times, tolerance)
        for scope in scopes:
            errors_by_scope[scope].extend(errors)
            timed_cases_by_scope[scope] += 1

    scopes = set(errors_by_scope) | set(timed_cases_by_scope) | set(legacy_cases_by_scope)
    return {
        scope: {
            **_boundary_summary(errors_by_scope.get(scope, [])),
            "cut_time_annotation_case_count": timed_cases_by_scope.get(scope, 0),
            "cut_legacy_id_case_count": legacy_cases_by_scope.get(scope, 0),
        }
        for scope in sorted(scopes)
    }


def augment_evaluation(
    evaluation: dict[str, Any],
    *,
    dataset_path: Path,
    dataset_payload: dict[str, Any],
    selected_split: str,
    candidate_id: str,
    require_source_groups: bool,
) -> dict[str, Any]:
    """Bind an evaluation to immutable ground truth and add cut boundary metrics."""

    validation = validate_dataset_manifest(
        dataset_path,
        dataset_payload,
        require_source_groups=require_source_groups,
    )
    identity = dataset_ground_truth_identity(
        dataset_path,
        dataset_payload,
        split=selected_split,
    )
    boundary_scopes = cut_boundary_metrics_by_scope(
        dataset_payload,
        selected_split=selected_split,
    )
    result = deepcopy(evaluation)
    result["evaluation_protocol_version"] = EVALUATION_PROTOCOL_VERSION
    result["evaluated_split"] = selected_split
    result["candidate_id"] = candidate_id
    result["dataset_identity"] = identity
    result["dataset_validation"] = validation

    overall_boundary = boundary_scopes.get("overall", _boundary_summary([]))
    result.setdefault("overall", {}).update(overall_boundary)
    groups = result.setdefault("groups", {})
    for scope, metrics in boundary_scopes.items():
        if scope == "overall":
            continue
        groups.setdefault(scope, {}).update(metrics)
    result["privacy"] = (
        "aggregate metrics, opaque case IDs and SHA-256 identities only; lyric text omitted"
    )
    return result
