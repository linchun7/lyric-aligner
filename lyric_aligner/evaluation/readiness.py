"""Privacy-safe helpers for preparing real calibration/blind-test datasets.

This module never invents reference subtitles, predictions, QA results or
accuracy metrics.  It creates deterministic *empty* scaffolds and reports which
opaque cases/files are still missing before the strict P1 workflow can run.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from lyric_aligner.evaluation.strict_workflow import (
    StrictEvaluationError,
    load_json,
    resolve,
    validate_manifest_metadata,
)


READINESS_SCHEMA_VERSION = "1.0"
_CANDIDATE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class DatasetReadinessError(ValueError):
    """Raised when a dataset scaffold/readiness request is unsafe or invalid."""


def _candidate_slug(value: str) -> str:
    value = str(value or "").strip()
    if not value or not _CANDIDATE_RE.fullmatch(value):
        raise DatasetReadinessError(
            "candidate-id must contain only letters, digits, dot, underscore or hyphen"
        )
    return value


def _case_rows(split: str, count: int, candidate_id: str) -> list[dict[str, Any]]:
    if count < 0:
        raise DatasetReadinessError("case counts must be >= 0")
    rows: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        case_id = f"{split}-{index:04d}"
        rows.append(
            {
                "id": case_id,
                "source_group": f"sg-{split}-{index:04d}",
                "split": split,
                "language": "auto",
                "reference_srt": f"reference/{case_id}.srt",
                "predicted_srt": f"predictions/{candidate_id}/{case_id}.srt",
                "qa_json": f"predictions/{candidate_id}/{case_id}.qa.json",
                "audio_duration_seconds": 0,
                "expected_cuts": [],
                "predicted_cuts": [],
                "expected_overlaps": [],
                "predicted_overlaps": [],
                "expected_occurrences": [],
            }
        )
    return rows


def scaffold_manifest(
    *,
    dataset: str,
    dataset_revision: str,
    candidate_id: str = "baseline",
    calibration_cases: int = 6,
    blind_cases: int = 6,
    train_cases: int = 0,
) -> dict[str, Any]:
    """Create a strict-schema manifest whose files intentionally do not exist yet."""

    dataset = str(dataset or "").strip()
    revision = str(dataset_revision or "").strip()
    candidate = _candidate_slug(candidate_id)
    if not dataset or not revision:
        raise DatasetReadinessError("dataset and dataset-revision must be non-empty")
    if calibration_cases < 1 or blind_cases < 1:
        raise DatasetReadinessError(
            "strict scaffold requires at least one calibration and one blind_test case"
        )
    cases = [
        *_case_rows("train", train_cases, candidate),
        *_case_rows("calibration", calibration_cases, candidate),
        *_case_rows("blind_test", blind_cases, candidate),
    ]
    payload = {
        "schema_version": "1.1",
        "dataset": dataset,
        "dataset_revision": revision,
        "scaffold": {
            "status": "empty_placeholders_only",
            "candidate_id": candidate,
            "notice": (
                "Generated paths are placeholders. Populate authorized real reference/prediction/QA "
                "files before evaluation; this scaffold contains no accuracy evidence."
            ),
        },
        "cases": cases,
    }
    # Metadata itself must already satisfy the strict split-isolation contract.
    validate_manifest_metadata(payload)
    return payload


def clone_candidate_manifest(
    payload: dict[str, Any], *, candidate_id: str
) -> dict[str, Any]:
    """Clone only prediction/QA destinations while preserving exact ground truth."""

    validate_manifest_metadata(payload)
    candidate = _candidate_slug(candidate_id)
    result = deepcopy(payload)
    result["scaffold"] = {
        "status": "candidate_paths_only",
        "candidate_id": candidate,
        "notice": "Ground-truth fields are unchanged; only prediction/QA destinations were rewritten.",
    }
    for case in result["cases"]:
        case_id = str(case["id"])
        case["predicted_srt"] = f"predictions/{candidate}/{case_id}.srt"
        case["qa_json"] = f"predictions/{candidate}/{case_id}.qa.json"
        # predicted annotations are candidate output, never inherited as truth.
        if "predicted_cuts" in case:
            case["predicted_cuts"] = []
        if "predicted_overlaps" in case:
            case["predicted_overlaps"] = []
    validate_manifest_metadata(result)
    return result


def default_policy(*, split: str) -> dict[str, Any]:
    """Return a conservative *template* policy, not calibrated production thresholds."""

    if split not in {"calibration", "blind_test"}:
        raise DatasetReadinessError("policy split must be calibration or blind_test")
    gates = [
        {
            "scope": "overall",
            "metric": "line_exact_recall",
            "direction": "higher",
            "max_regression_abs": 0.0,
        },
        {
            "scope": "overall",
            "metric": "boundary_p95_ms",
            "direction": "lower",
            "max_regression_abs": 0.0,
        },
        {
            "scope": "overall",
            "metric": "cut_recall",
            "direction": "higher",
            "max_regression_abs": 0.0,
        },
        {
            "scope": "overall",
            "metric": "cut_boundary_reference_coverage",
            "direction": "higher",
            "max_regression_abs": 0.0,
        },
    ]
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "policy_id": f"{split}-TEMPLATE-REQUIRES-REVIEW",
        "split": split,
        "template_status": "not_calibrated",
        "gates": gates,
    }
    if split == "calibration":
        payload["ranking"] = [
            {
                "scope": "overall",
                "metric": "line_exact_recall",
                "direction": "higher",
            },
            {
                "scope": "overall",
                "metric": "boundary_p95_ms",
                "direction": "lower",
            },
        ]
    return payload


def _scenario_names(case: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    if case.get("expected_cuts") or case.get("expected_cut_ids"):
        names.add("cut")
    if case.get("expected_overlaps"):
        names.add("overlap")
    if case.get("expected_occurrences"):
        names.add("occurrence")
    if not names:
        names.add("plain")
    return names


def _qa_identity(path: Path) -> tuple[str, str, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    identity = (
        str(payload.get("algorithm_version") or "").strip(),
        str(payload.get("calibration_profile_version") or "").strip(),
        str(payload.get("calibration_profile_id") or "").strip(),
    )
    return identity if all(identity) else None


def _split_report(manifest_path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    missing_reference: list[str] = []
    missing_prediction: list[str] = []
    missing_qa: list[str] = []
    invalid_qa: list[str] = []
    identities: set[tuple[str, str, str]] = set()
    languages = Counter()
    scenarios = Counter()
    cut_time_cases = 0
    overlap_cases = 0

    for case in rows:
        case_id = str(case["id"])
        languages[str(case.get("language") or "unknown")] += 1
        for scenario in _scenario_names(case):
            scenarios[scenario] += 1
        if "expected_cuts" in case:
            cut_time_cases += 1
        if case.get("expected_overlaps") is not None:
            overlap_cases += 1

        reference = resolve(manifest_path.parent, str(case["reference_srt"]))
        prediction = resolve(manifest_path.parent, str(case["predicted_srt"]))
        qa = resolve(manifest_path.parent, str(case["qa_json"]))
        if not reference.is_file():
            missing_reference.append(case_id)
        if not prediction.is_file():
            missing_prediction.append(case_id)
        if not qa.is_file():
            missing_qa.append(case_id)
        else:
            identity = _qa_identity(qa)
            if identity is None:
                invalid_qa.append(case_id)
            else:
                identities.add(identity)

    reference_ready = not missing_reference
    prediction_files_ready = not missing_prediction and not missing_qa
    runtime_identity_ready = prediction_files_ready and not invalid_qa and len(identities) == 1
    evaluation_ready = reference_ready and prediction_files_ready and runtime_identity_ready
    return {
        "case_count": len(rows),
        "languages": dict(sorted(languages.items())),
        "scenarios": dict(sorted(scenarios.items())),
        "annotation_coverage": {
            "cut_time_annotation_cases": cut_time_cases,
            "overlap_annotation_cases": overlap_cases,
        },
        "reference_ready": reference_ready,
        "prediction_files_ready": prediction_files_ready,
        "runtime_identity_ready": runtime_identity_ready,
        "evaluation_ready": evaluation_ready,
        "missing_reference_case_ids": sorted(missing_reference),
        "missing_prediction_case_ids": sorted(missing_prediction),
        "missing_qa_case_ids": sorted(missing_qa),
        "invalid_qa_identity_case_ids": sorted(invalid_qa),
        "runtime_identity_variant_count": len(identities),
    }


def inspect_dataset_readiness(
    manifest_path: Path, *, split: str | None = None
) -> dict[str, Any]:
    payload = load_json(manifest_path, label="dataset manifest")
    try:
        metadata = validate_manifest_metadata(payload)
    except StrictEvaluationError as exc:
        raise DatasetReadinessError(str(exc)) from exc

    by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in payload["cases"]:
        by_split[str(case["split"])].append(case)
    if split is not None:
        if split not in {"train", "calibration", "blind_test"}:
            raise DatasetReadinessError("split must be train, calibration or blind_test")
        if not by_split.get(split):
            raise DatasetReadinessError(f"dataset has no cases for split {split}")
        selected = {split: by_split[split]}
    else:
        selected = {key: by_split[key] for key in sorted(by_split)}

    reports = {
        name: _split_report(manifest_path, rows)
        for name, rows in selected.items()
    }
    return {
        "schema_version": READINESS_SCHEMA_VERSION,
        "dataset": metadata["dataset"],
        "dataset_revision": metadata["dataset_revision"],
        "metadata_ready": True,
        "source_group_isolation_enforced": metadata[
            "source_group_isolation_enforced"
        ],
        "splits": reports,
        "all_selected_references_ready": all(
            report["reference_ready"] for report in reports.values()
        ),
        "all_selected_evaluations_ready": all(
            report["evaluation_ready"] for report in reports.values()
        ),
        "privacy": "No lyric text or file-system paths are emitted; only opaque case IDs and counts.",
    }


def write_scaffold_directories(root: Path, payload: dict[str, Any]) -> None:
    """Create directories only; never create fake SRT/QA content."""

    root.mkdir(parents=True, exist_ok=True)
    (root / "reference").mkdir(exist_ok=True)
    candidates = {
        Path(str(case["predicted_srt"])).parts[1]
        for case in payload.get("cases", [])
        if len(Path(str(case["predicted_srt"])).parts) >= 2
    }
    for candidate in sorted(candidates):
        (root / "predictions" / candidate).mkdir(parents=True, exist_ok=True)
