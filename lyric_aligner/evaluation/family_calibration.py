"""Privacy-safe real-data evaluation of v4 timing evidence families.

This module compares Source-to-Mix, editor, ASR and projected forced-alignment
boundaries against private line-level ground truth.  It consumes P9 fusion
artifacts because they already normalize all auxiliary families into edited-mix
time.  Raw lyric text is neither required nor emitted.

Results are diagnostic/calibration evidence only.  They do not promote any
auxiliary family into authoritative timing or a release gate.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


FAMILY_DATASET_SCHEMA_VERSION = "1.0"
FAMILY_TRUTH_SCHEMA_VERSION = "1.0"
FAMILY_REPORT_SCHEMA_VERSION = "1.0"
FAMILIES = ("source_timeline", "editor", "asr", "forced_alignment")


class FamilyCalibrationError(ValueError):
    """Raised when ground truth/fusion identity cannot be compared safely."""


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise FamilyCalibrationError(f"{label} must be a JSON object")
    return payload


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _finite(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FamilyCalibrationError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise FamilyCalibrationError(f"{label} must be finite")
    return number


def _boundary(value: Any, *, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise FamilyCalibrationError(f"{label} must be [start_ms, end_ms]")
    start = _finite(value[0], label=f"{label}.start_ms")
    end = _finite(value[1], label=f"{label}.end_ms")
    if end <= start:
        raise FamilyCalibrationError(f"{label} end must be greater than start")
    return start, end


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _rate(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _truth_index(payload: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    if str(payload.get("schema_version") or "") != FAMILY_TRUTH_SCHEMA_VERSION:
        raise FamilyCalibrationError(
            f"truth schema_version must be {FAMILY_TRUTH_SCHEMA_VERSION}"
        )
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        raise FamilyCalibrationError("truth payload requires non-empty lines")
    output: dict[tuple[str, int], dict[str, Any]] = {}
    for row in lines:
        if not isinstance(row, dict):
            raise FamilyCalibrationError("truth line must be an object")
        occurrence_id = str(row.get("occurrence_id") or "").strip()
        if not occurrence_id:
            raise FamilyCalibrationError("truth line requires occurrence_id")
        try:
            line_index = int(row["canonical_line_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FamilyCalibrationError("truth canonical_line_index is invalid") from exc
        text_sha = str(row.get("canonical_text_sha256") or "").lower()
        if len(text_sha) != 64 or any(char not in "0123456789abcdef" for char in text_sha):
            raise FamilyCalibrationError("truth canonical_text_sha256 must be a SHA-256 hex digest")
        start = _finite(row.get("truth_start_ms"), label="truth_start_ms")
        end = _finite(row.get("truth_end_ms"), label="truth_end_ms")
        if end <= start:
            raise FamilyCalibrationError("truth_end_ms must be greater than truth_start_ms")
        key = (occurrence_id, line_index)
        if key in output:
            raise FamilyCalibrationError("duplicate truth canonical line identity")
        output[key] = {
            "occurrence_id": occurrence_id,
            "canonical_line_index": line_index,
            "canonical_text_sha256": text_sha,
            "truth_boundary_ms": (start, end),
        }
    return output


def _fusion_index(payload: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    if payload.get("mode") != "shadow_only":
        raise FamilyCalibrationError("fusion must be shadow_only")
    if payload.get("policy_calibrated") is not False:
        raise FamilyCalibrationError("family evaluator requires uncalibrated fusion input")
    if payload.get("release_gate_eligible") is not False:
        raise FamilyCalibrationError("fusion must not already be release-gate eligible")
    lines = payload.get("lines")
    if not isinstance(lines, list):
        raise FamilyCalibrationError("fusion lines must be a list")
    output: dict[tuple[str, int], dict[str, Any]] = {}
    for row in lines:
        if not isinstance(row, dict):
            raise FamilyCalibrationError("fusion line must be an object")
        occurrence_id = str(row.get("occurrence_id") or "").strip()
        try:
            line_index = int(row["canonical_line_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FamilyCalibrationError("fusion canonical line identity is invalid") from exc
        key = (occurrence_id, line_index)
        if not occurrence_id or key in output:
            raise FamilyCalibrationError("fusion line identities must be unique/non-empty")
        output[key] = row
    return output


def _family_boundaries(row: dict[str, Any]) -> tuple[dict[str, tuple[float, float]], bool]:
    boundaries = {
        "source_timeline": _boundary(
            row.get("source_timeline_boundary_ms"),
            label="source_timeline_boundary_ms",
        )
    }
    forced_unprojectable = False
    seen: set[str] = set()
    families = row.get("families")
    if not isinstance(families, list):
        raise FamilyCalibrationError("fusion families must be a list")
    for family in families:
        if not isinstance(family, dict):
            raise FamilyCalibrationError("fusion family must be an object")
        name = str(family.get("family") or "")
        if name == "source_timeline":
            continue
        if name not in FAMILIES:
            continue
        if name in seen:
            raise FamilyCalibrationError(f"duplicate fusion family {name}")
        seen.add(name)
        available = bool(family.get("available"))
        if name == "forced_alignment" and not available:
            forced_unprojectable = family.get("projection_status") == "unprojectable"
        if not available:
            continue
        boundaries[name] = _boundary(family.get("boundary_ms"), label=f"{name}.boundary_ms")
    return boundaries, forced_unprojectable


def _family_metrics(rows: list[dict[str, Any]], family: str) -> dict[str, Any]:
    available = [row for row in rows if family in row["families"]]
    onset = [abs(row["families"][family][0] - row["truth"][0]) for row in available]
    offset = [abs(row["families"][family][1] - row["truth"][1]) for row in available]
    endpoints = onset + offset
    max_errors = [max(on, off) for on, off in zip(onset, offset)]
    return {
        "truth_line_count": len(rows),
        "available_line_count": len(available),
        "coverage_rate": round(_rate(len(available), len(rows)), 6),
        "onset_mae_ms": round(statistics.fmean(onset), 3) if onset else 0.0,
        "offset_mae_ms": round(statistics.fmean(offset), 3) if offset else 0.0,
        "boundary_mae_ms": round(statistics.fmean(endpoints), 3) if endpoints else 0.0,
        "boundary_p50_ms": round(_percentile(endpoints, 0.50), 3),
        "boundary_p90_ms": round(_percentile(endpoints, 0.90), 3),
        "boundary_p95_ms": round(_percentile(endpoints, 0.95), 3),
        "line_max_error_p95_ms": round(_percentile(max_errors, 0.95), 3),
        "within_250ms_rate": round(
            _rate(sum(value <= 250.0 for value in max_errors), len(max_errors)), 6
        ),
        "within_500ms_rate": round(
            _rate(sum(value <= 500.0 for value in max_errors), len(max_errors)), 6
        ),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "truth_line_count": 0,
            "conflict_line_count": 0,
            "conflict_rate": 0.0,
            "forced_unprojectable_line_count": 0,
            "forced_unprojectable_rate": 0.0,
            "families": {family: _family_metrics([], family) for family in FAMILIES},
        }
    forced_unprojectable = sum(bool(row["forced_unprojectable"]) for row in rows)
    conflicts = sum(row["shadow_level"] == "CONFLICT" for row in rows)
    return {
        "truth_line_count": len(rows),
        "conflict_line_count": conflicts,
        "conflict_rate": round(_rate(conflicts, len(rows)), 6),
        "forced_unprojectable_line_count": forced_unprojectable,
        "forced_unprojectable_rate": round(_rate(forced_unprojectable, len(rows)), 6),
        "families": {family: _family_metrics(rows, family) for family in FAMILIES},
    }


def _case_rows(
    *,
    case: dict[str, Any],
    base: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    case_id = str(case.get("id") or "").strip()
    source_group = str(case.get("source_group") or "").strip()
    language = str(case.get("language") or "unknown").strip() or "unknown"
    risk_buckets = case.get("risk_buckets", [])
    if not case_id or not source_group:
        raise FamilyCalibrationError("every family-evaluation case requires id and source_group")
    if not isinstance(risk_buckets, list) or any(not str(value).strip() for value in risk_buckets):
        raise FamilyCalibrationError(f"case {case_id} risk_buckets must be a list of non-empty strings")
    truth_path = _resolve(base, str(case.get("truth_json") or ""))
    fusion_path = _resolve(base, str(case.get("fusion_json") or ""))
    if not truth_path.is_file() or not fusion_path.is_file():
        raise FamilyCalibrationError(f"case {case_id} truth_json/fusion_json must exist")
    truth_payload = _load_json(truth_path, label=f"truth for {case_id}")
    fusion_payload = _load_json(fusion_path, label=f"fusion for {case_id}")
    truth = _truth_index(truth_payload)
    fusion = _fusion_index(fusion_payload)

    rows: list[dict[str, Any]] = []
    for key in sorted(truth):
        truth_row = truth[key]
        fusion_row = fusion.get(key)
        if fusion_row is None:
            raise FamilyCalibrationError(f"case {case_id} truth line has no matching fusion line")
        if str(fusion_row.get("canonical_text_sha256") or "").lower() != truth_row["canonical_text_sha256"]:
            raise FamilyCalibrationError(f"case {case_id} canonical text identity mismatch")
        boundaries, forced_unprojectable = _family_boundaries(fusion_row)
        rows.append(
            {
                "case_id": case_id,
                "source_group": source_group,
                "language": language,
                "risk_buckets": sorted(set(str(value).strip() for value in risk_buckets)),
                "truth": truth_row["truth_boundary_ms"],
                "families": boundaries,
                "forced_unprojectable": forced_unprojectable,
                "shadow_level": str(fusion_row.get("shadow_level") or ""),
            }
        )
    identities = {
        "ground_truth_sha256": _canonical_sha256(
            [
                {
                    "occurrence_id": truth[key]["occurrence_id"],
                    "canonical_line_index": truth[key]["canonical_line_index"],
                    "canonical_text_sha256": truth[key]["canonical_text_sha256"],
                    "truth_boundary_ms": list(truth[key]["truth_boundary_ms"]),
                }
                for key in sorted(truth)
            ]
        ),
        "fusion_sha256": _canonical_sha256(fusion_payload),
    }
    return rows, identities


def evaluate_family_dataset(manifest_path: Path) -> dict[str, Any]:
    """Evaluate one isolated calibration or blind-test family manifest."""

    path = manifest_path.resolve()
    payload = _load_json(path, label="family dataset manifest")
    if str(payload.get("schema_version") or "") != FAMILY_DATASET_SCHEMA_VERSION:
        raise FamilyCalibrationError(
            f"family dataset schema_version must be {FAMILY_DATASET_SCHEMA_VERSION}"
        )
    dataset = str(payload.get("dataset") or "").strip()
    revision = str(payload.get("dataset_revision") or "").strip()
    split = str(payload.get("split") or "").strip()
    cases = payload.get("cases")
    if not dataset or not revision:
        raise FamilyCalibrationError("family dataset requires dataset and dataset_revision")
    if split not in {"calibration", "blind_test"}:
        raise FamilyCalibrationError("family dataset split must be calibration or blind_test")
    if not isinstance(cases, list) or not cases:
        raise FamilyCalibrationError("family dataset requires non-empty cases")

    all_rows: list[dict[str, Any]] = []
    case_reports: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise FamilyCalibrationError("family dataset case must be an object")
        case_id = str(case.get("id") or "").strip()
        if not case_id or case_id in seen_ids:
            raise FamilyCalibrationError("family dataset case ids must be unique/non-empty")
        seen_ids.add(case_id)
        rows, identities = _case_rows(case=case, base=path.parent)
        all_rows.extend(rows)
        case_reports.append(
            {
                "id": case_id,
                "source_group": str(case["source_group"]),
                "language": str(case.get("language") or "unknown"),
                "risk_buckets": sorted(set(str(value) for value in case.get("risk_buckets", []))),
                **identities,
                "metrics": _aggregate(rows),
            }
        )

    language_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    risk_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        language_groups[row["language"]].append(row)
        for bucket in row["risk_buckets"]:
            risk_groups[bucket].append(row)

    manifest_identity = {
        "dataset": dataset,
        "dataset_revision": revision,
        "split": split,
        "cases": [
            {
                "id": row["id"],
                "source_group": row["source_group"],
                "ground_truth_sha256": row["ground_truth_sha256"],
                "fusion_sha256": row["fusion_sha256"],
            }
            for row in case_reports
        ],
    }
    return {
        "schema_version": FAMILY_REPORT_SCHEMA_VERSION,
        "mode": "private_family_calibration_evaluation",
        "dataset": dataset,
        "dataset_revision": revision,
        "split": split,
        "evaluation_identity_sha256": _canonical_sha256(manifest_identity),
        "case_count": len(case_reports),
        "overall": _aggregate(all_rows),
        "groups": {
            "language": {key: _aggregate(value) for key, value in sorted(language_groups.items())},
            "risk_bucket": {key: _aggregate(value) for key, value in sorted(risk_groups.items())},
        },
        "cases": case_reports,
        "policy_calibrated": False,
        "release_gate_eligible": False,
        "automatic_timing_change_allowed": False,
        "authority": {
            "canonical_text": "canonical_lyrics_only",
            "primary_timing": "source_to_mix_only",
            "auxiliary_families": "evaluation_only",
        },
        "privacy": "aggregate metrics and opaque hashes/ids only; lyric text and local paths are omitted",
        "accuracy_boundary": "calibration metrics do not authorize timing mutation; blind-test evidence is required before promotion",
    }
