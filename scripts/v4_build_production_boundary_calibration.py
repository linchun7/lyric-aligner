#!/usr/bin/env python3
"""Build one production boundary calibration from locked human gold + blind predictions.

This command never invokes a model.  Its prediction input is intentionally narrow:
one immutable row per preselected gold ID, with only ``id`` and ``predicted_ms``.
Missing predictions are represented by null instead of deleting hard cases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.evaluation.production_calibration import (
    evaluate_human_gold_backend_calibration,
    validate_human_boundary_gold_artifact,
)


PREDICTION_SCHEMA_VERSION = "human-boundary-backend-predictions-1.1"
LEGACY_PREDICTION_SCHEMA_VERSION = "human-boundary-backend-predictions-1.0"
LEGACY_REQUIRED_SCOPE_FIELDS = (
    "boundary_kind",
    "audio_basis",
    "language",
    "backend_version",
    "model_id",
    "model_revision",
)
REQUIRED_SCOPE_FIELDS = (
    *LEGACY_REQUIRED_SCOPE_FIELDS,
    "implementation_revision",
    "adapter_contract_revision",
)
OPTIONAL_SCOPE_FIELDS = (
    "lexical_id",
    "lexical_revision",
    "backend_profile_id",
    "window_policy_id",
)
TOP_LEVEL_FIELDS = {
    "schema_version",
    "selection_lock_sha256",
    "backend_id",
    "family",
    "correlation_group",
    "scope",
    "record_count",
    "records",
    "predictions_sha256",
    "artifact_sha256",
}


class ProductionPredictionError(ValueError):
    pass


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductionPredictionError(f"{label} is unreadable JSON") from exc
    if not isinstance(payload, dict):
        raise ProductionPredictionError(f"{label} must be a JSON object")
    return payload


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError("production calibration output must be a new path")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_prediction_artifact(
    artifact: Mapping[str, Any],
    *,
    human_gold_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a blind prediction artifact without reading timing gold into it."""

    if set(artifact) != TOP_LEVEL_FIELDS:
        raise ProductionPredictionError("prediction artifact top-level fields are not the exact production schema")
    schema_version = str(artifact.get("schema_version") or "")
    if schema_version not in {PREDICTION_SCHEMA_VERSION, LEGACY_PREDICTION_SCHEMA_VERSION}:
        raise ProductionPredictionError("unsupported production prediction schema")

    claimed_artifact_sha = str(artifact.get("artifact_sha256") or "").strip()
    without_hash = dict(artifact)
    without_hash.pop("artifact_sha256", None)
    if len(claimed_artifact_sha) != 64 or claimed_artifact_sha != sha256_json(without_hash):
        raise ProductionPredictionError("prediction artifact SHA is invalid")

    gold = validate_human_boundary_gold_artifact(human_gold_artifact)
    if str(artifact.get("selection_lock_sha256") or "") != gold["selection_lock_sha256"]:
        raise ProductionPredictionError("prediction artifact belongs to another selection lock")

    backend_id = str(artifact.get("backend_id") or "").strip()
    family = str(artifact.get("family") or "").strip()
    correlation_group = str(artifact.get("correlation_group") or "").strip()
    if not backend_id or not family or not correlation_group:
        raise ProductionPredictionError("prediction backend identity is incomplete")

    scope = artifact.get("scope")
    if not isinstance(scope, Mapping):
        raise ProductionPredictionError("prediction scope must be an object")
    required_scope_fields = (
        REQUIRED_SCOPE_FIELDS
        if schema_version == PREDICTION_SCHEMA_VERSION
        else LEGACY_REQUIRED_SCOPE_FIELDS
    )
    allowed_scope = set(REQUIRED_SCOPE_FIELDS) | set(OPTIONAL_SCOPE_FIELDS)
    if set(scope) - allowed_scope or any(not str(scope.get(key) or "").strip() for key in required_scope_fields):
        raise ProductionPredictionError("prediction scope is incomplete or contains unexpected fields")
    if schema_version == PREDICTION_SCHEMA_VERSION:
        for key in ("implementation_revision", "adapter_contract_revision"):
            value = str(scope.get(key) or "")
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ProductionPredictionError(f"prediction scope {key} must be a lowercase SHA-256")
    boundary_kind = str(scope.get("boundary_kind") or "").strip()
    if boundary_kind not in {"start", "end", "internal"}:
        raise ProductionPredictionError("prediction boundary_kind is invalid")
    language = str(scope.get("language") or "").strip().lower()
    if language != str(scope.get("language") or "") or language != gold["language_scope"]:
        raise ProductionPredictionError("prediction language does not match human-gold language scope")
    if boundary_kind == "internal" and (
        not str(scope.get("lexical_id") or "").strip()
        or not str(scope.get("lexical_revision") or "").strip()
    ):
        raise ProductionPredictionError("internal prediction scope requires lexical identity")

    records = artifact.get("records")
    expected_count = int(gold["expected_count_per_kind"])
    if not isinstance(records, list) or len(records) != expected_count:
        raise ProductionPredictionError("prediction artifact has the wrong preselected row count")
    if int(artifact.get("record_count") or -1) != expected_count:
        raise ProductionPredictionError("prediction record_count mismatch")
    claimed_predictions_sha = str(artifact.get("predictions_sha256") or "").strip()
    if len(claimed_predictions_sha) != 64 or claimed_predictions_sha != sha256_json(records):
        raise ProductionPredictionError("prediction record SHA is invalid")

    expected_ids = {
        str(row["id"])
        for row in gold["records"]
        if str(row.get("boundary_kind") or "") == boundary_kind
    }
    actual_ids: set[str] = set()
    predictions_ms: dict[str, int | None] = {}
    for row in records:
        if not isinstance(row, Mapping) or set(row) != {"id", "predicted_ms"}:
            raise ProductionPredictionError("each prediction row must contain exactly id and predicted_ms")
        record_id = str(row.get("id") or "").strip()
        if not record_id or record_id in actual_ids:
            raise ProductionPredictionError("prediction IDs must be unique/non-empty")
        actual_ids.add(record_id)
        value = row.get("predicted_ms")
        if value is None:
            predictions_ms[record_id] = None
        else:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ProductionPredictionError("predicted_ms must be integer milliseconds or null")
            if value < 0:
                raise ProductionPredictionError("predicted_ms must be a nonnegative integer or null")
            predictions_ms[record_id] = value
    if actual_ids != expected_ids:
        raise ProductionPredictionError("prediction ID set does not exactly match the preselected gold scope")

    return {
        "backend_id": backend_id,
        "family": family,
        "correlation_group": correlation_group,
        "scope": dict(scope),
        "predictions_ms": predictions_ms,
        "prediction_artifact_sha256": claimed_artifact_sha,
        "predictions_sha256": claimed_predictions_sha,
        "schema_version": schema_version,
        "production_provenance_complete": schema_version == PREDICTION_SCHEMA_VERSION,
    }


def build_production_calibration_from_prediction_artifact(
    *,
    human_gold_artifact: Mapping[str, Any],
    prediction_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    verified = validate_prediction_artifact(
        prediction_artifact,
        human_gold_artifact=human_gold_artifact,
    )
    calibration = evaluate_human_gold_backend_calibration(
        backend_id=verified["backend_id"],
        family=verified["family"],
        correlation_group=verified["correlation_group"],
        scope=verified["scope"],
        human_gold_artifact=human_gold_artifact,
        predictions_ms=verified["predictions_ms"],
    )
    calibration.pop("artifact_sha256", None)
    calibration["prediction_provenance"] = {
        "schema_version": verified["schema_version"],
        "prediction_artifact_sha256": verified["prediction_artifact_sha256"],
        "predictions_sha256": verified["predictions_sha256"],
        "selection_lock_sha256": human_gold_artifact["selection_lock_sha256"],
        "production_provenance_complete": bool(verified["production_provenance_complete"]),
    }
    calibration["artifact_sha256"] = sha256_json(calibration)
    return calibration


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    human_gold = load_json(args.gold, label="human gold")
    prediction_artifact = load_json(args.predictions, label="prediction artifact")
    calibration = build_production_calibration_from_prediction_artifact(
        human_gold_artifact=human_gold,
        prediction_artifact=prediction_artifact,
    )
    atomic_json(args.out, calibration)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "passed": bool(calibration["passed"]),
                "boundary_kind": calibration["scope"]["boundary_kind"],
                "backend_id": calibration["backend_id"],
                "artifact_sha256": calibration["artifact_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
