"""Privacy-safe real-data evaluation for Partial Timeline Repair previews.

The evaluator compares selected-cue preview decisions against private human
mix-time truth. It never emits lyric text and never promotes timing authority;
its output is calibration/blind evidence only.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


PARTIAL_DATASET_SCHEMA_VERSION = "1.0"
PARTIAL_TRUTH_SCHEMA_VERSION = "1.0"
PARTIAL_REPORT_SCHEMA_VERSION = "1.0"
PARTIAL_EVALUATION_SCHEMA_VERSION = "1.0"


class PartialTimelineEvaluationError(ValueError):
    """Raised when preview/truth inputs cannot be compared safely."""


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise PartialTimelineEvaluationError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PartialTimelineEvaluationError(f"{label} must be a JSON object")
    return payload


def _resolve(base: Path, value: Any, *, label: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise PartialTimelineEvaluationError(f"{label} path is empty")
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _number(value: Any, *, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PartialTimelineEvaluationError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise PartialTimelineEvaluationError(f"{label} must be finite")
    return result


def _sha256(value: Any, *, label: str) -> str:
    result = str(value or "").strip().lower()
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise PartialTimelineEvaluationError(f"{label} must be a SHA-256 hex digest")
    return result


def _positive_boundary(start: Any, end: Any, *, label: str) -> tuple[float, float]:
    left = _number(start, label=f"{label}.start_ms")
    right = _number(end, label=f"{label}.end_ms")
    if right <= left:
        raise PartialTimelineEvaluationError(f"{label} end must exceed start")
    return left, right


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


def _boundary_error(
    boundary: tuple[float, float],
    truth: tuple[float, float],
) -> dict[str, float]:
    onset = abs(boundary[0] - truth[0])
    offset = abs(boundary[1] - truth[1])
    return {"onset": onset, "offset": offset, "max": max(onset, offset)}


def _truth_identity(payload: dict[str, Any]) -> dict[str, str]:
    occurrence_id = str(payload.get("occurrence_id") or "").strip()
    if not occurrence_id:
        raise PartialTimelineEvaluationError("truth requires occurrence_id")
    return {
        "source_srt_sha256": _sha256(
            payload.get("source_srt_sha256"), label="truth source_srt_sha256"
        ),
        "canonical_lrc_sha256": _sha256(
            payload.get("canonical_lrc_sha256"), label="truth canonical_lrc_sha256"
        ),
        "occurrence_id": occurrence_id,
    }


def _preview_identity(payload: dict[str, Any]) -> dict[str, str]:
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise PartialTimelineEvaluationError("preview requires inputs identity")
    mapping_identity = inputs.get("mapping_identity")
    if not isinstance(mapping_identity, dict):
        raise PartialTimelineEvaluationError("preview requires mapping_identity")
    occurrence_id = str(mapping_identity.get("occurrence_id") or "").strip()
    if not occurrence_id:
        raise PartialTimelineEvaluationError(
            "preview mapping_identity requires occurrence_id"
        )
    return {
        "source_srt_sha256": _sha256(
            inputs.get("source_srt_sha256"), label="preview source_srt_sha256"
        ),
        "canonical_lrc_sha256": _sha256(
            inputs.get("canonical_lrc_sha256"), label="preview canonical_lrc_sha256"
        ),
        "occurrence_id": occurrence_id,
    }


def _truth_index(payload: dict[str, Any]) -> dict[int, tuple[float, float]]:
    if str(payload.get("schema_version") or "") != PARTIAL_TRUTH_SCHEMA_VERSION:
        raise PartialTimelineEvaluationError(
            f"truth schema_version must be {PARTIAL_TRUTH_SCHEMA_VERSION}"
        )
    rows = payload.get("cues")
    if not isinstance(rows, list) or not rows:
        raise PartialTimelineEvaluationError("truth requires non-empty cues")
    result: dict[int, tuple[float, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PartialTimelineEvaluationError("truth cue must be an object")
        try:
            cue_number = int(row["cue_number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PartialTimelineEvaluationError("truth cue_number is invalid") from exc
        if cue_number in result:
            raise PartialTimelineEvaluationError("duplicate truth cue_number")
        result[cue_number] = _positive_boundary(
            row.get("truth_start_ms"),
            row.get("truth_end_ms"),
            label=f"truth cue {cue_number}",
        )
    return result


def _preview_index(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    if str(payload.get("schema_version") or "") != PARTIAL_REPORT_SCHEMA_VERSION:
        raise PartialTimelineEvaluationError(
            f"preview schema_version must be {PARTIAL_REPORT_SCHEMA_VERSION}"
        )
    if payload.get("mode") != "partial_timeline_repair_preview":
        raise PartialTimelineEvaluationError("input is not a partial timeline preview")
    if payload.get("releaseable") is not False:
        raise PartialTimelineEvaluationError("preview must remain non-releaseable")
    if payload.get("automatic_timing_change_allowed") is not False:
        raise PartialTimelineEvaluationError(
            "preview must keep automatic timing changes disabled"
        )
    if payload.get("subtitle_text_unchanged") is not True:
        raise PartialTimelineEvaluationError("preview must preserve subtitle text")
    rows = payload.get("decisions")
    if not isinstance(rows, list) or not rows:
        raise PartialTimelineEvaluationError("preview requires non-empty decisions")
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PartialTimelineEvaluationError("preview decision must be an object")
        try:
            cue_number = int(row["cue_number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PartialTimelineEvaluationError("preview cue_number is invalid") from exc
        if cue_number in result:
            raise PartialTimelineEvaluationError("duplicate preview cue_number")
        action = str(row.get("action") or "")
        if action not in {"propose", "unchanged", "review"}:
            raise PartialTimelineEvaluationError(
                f"preview cue {cue_number} has invalid action"
            )
        original = _positive_boundary(
            row.get("original_start_ms"),
            row.get("original_end_ms"),
            label=f"preview cue {cue_number} original",
        )
        suggested: tuple[float, float] | None = None
        if action in {"propose", "unchanged"}:
            suggested = _positive_boundary(
                row.get("suggested_start_ms"),
                row.get("suggested_end_ms"),
                label=f"preview cue {cue_number} suggested",
            )
        result[cue_number] = {
            "action": action,
            "original": original,
            "suggested": suggested,
            "reason": str(row.get("reason") or ""),
        }
    try:
        selected_count = int(payload.get("selected_cue_count"))
    except (TypeError, ValueError) as exc:
        raise PartialTimelineEvaluationError(
            "preview selected_cue_count is invalid"
        ) from exc
    if selected_count != len(result):
        raise PartialTimelineEvaluationError(
            "preview selected_cue_count does not match decisions"
        )
    return result


def _case_rows(
    case: dict[str, Any],
    *,
    base: Path,
    error_threshold_ms: float,
) -> list[dict[str, Any]]:
    case_id = str(case.get("id") or "").strip()
    language = str(case.get("language") or "unknown").strip() or "unknown"
    risks = case.get("risk_buckets", [])
    if not case_id:
        raise PartialTimelineEvaluationError("every case requires id")
    if not isinstance(risks, list) or any(not str(value).strip() for value in risks):
        raise PartialTimelineEvaluationError(
            f"case {case_id} risk_buckets must contain non-empty strings"
        )
    preview_path = _resolve(
        base, case.get("preview_report_json"), label=f"case {case_id} preview"
    )
    truth_path = _resolve(base, case.get("truth_json"), label=f"case {case_id} truth")
    if not preview_path.is_file() or not truth_path.is_file():
        raise PartialTimelineEvaluationError(
            f"case {case_id} preview/truth file does not exist"
        )

    preview_payload = _load_json(preview_path, label=f"preview for {case_id}")
    truth_payload = _load_json(truth_path, label=f"truth for {case_id}")
    if _preview_identity(preview_payload) != _truth_identity(truth_payload):
        raise PartialTimelineEvaluationError(
            f"case {case_id} preview/truth input identity mismatch"
        )
    preview = _preview_index(preview_payload)
    truth = _truth_index(truth_payload)
    if set(preview) != set(truth):
        raise PartialTimelineEvaluationError(
            f"case {case_id} preview selected cues and truth cues must match exactly"
        )

    rows: list[dict[str, Any]] = []
    for cue_number in sorted(preview):
        decision = preview[cue_number]
        truth_boundary = truth[cue_number]
        original_error = _boundary_error(decision["original"], truth_boundary)
        suggested_error = (
            _boundary_error(decision["suggested"], truth_boundary)
            if decision["suggested"] is not None
            else None
        )
        rows.append(
            {
                "case_id": case_id,
                "cue_number": cue_number,
                "language": language,
                "risk_buckets": sorted(set(str(value).strip() for value in risks)),
                "action": decision["action"],
                "reason": decision["reason"],
                "original_error": original_error,
                "suggested_error": suggested_error,
                "original_within_threshold": original_error["max"] <= error_threshold_ms,
                "suggested_within_threshold": (
                    suggested_error["max"] <= error_threshold_ms
                    if suggested_error is not None
                    else None
                ),
            }
        )
    return rows


def _aggregate(
    rows: list[dict[str, Any]],
    *,
    error_threshold_ms: float,
) -> dict[str, Any]:
    proposed = [row for row in rows if row["action"] == "propose"]
    reviewed = [row for row in rows if row["action"] == "review"]
    unchanged = [row for row in rows if row["action"] == "unchanged"]
    original_max = [row["original_error"]["max"] for row in rows]
    proposal_onset = [row["suggested_error"]["onset"] for row in proposed]
    proposal_offset = [row["suggested_error"]["offset"] for row in proposed]
    proposal_max = [row["suggested_error"]["max"] for row in proposed]

    unnecessary = [
        row for row in proposed if row["original_error"]["max"] <= error_threshold_ms
    ]
    harmful = [
        row
        for row in proposed
        if row["suggested_error"]["max"] > row["original_error"]["max"]
    ]
    bad = [
        row for row in proposed if row["suggested_error"]["max"] > error_threshold_ms
    ]
    missed = [
        row for row in unchanged if row["original_error"]["max"] > error_threshold_ms
    ]
    review_needed = [
        row for row in reviewed if row["original_error"]["max"] > error_threshold_ms
    ]

    return {
        "selected_cue_count": len(rows),
        "proposed_count": len(proposed),
        "proposal_rate": round(_rate(len(proposed), len(rows)), 6),
        "review_count": len(reviewed),
        "review_rate": round(_rate(len(reviewed), len(rows)), 6),
        "unchanged_count": len(unchanged),
        "unchanged_rate": round(_rate(len(unchanged), len(rows)), 6),
        "original_line_max_error_p95_ms": round(_percentile(original_max, 0.95), 3),
        "proposal_onset_mae_ms": (
            round(statistics.fmean(proposal_onset), 3) if proposal_onset else 0.0
        ),
        "proposal_offset_mae_ms": (
            round(statistics.fmean(proposal_offset), 3) if proposal_offset else 0.0
        ),
        "proposal_line_max_error_p50_ms": round(_percentile(proposal_max, 0.50), 3),
        "proposal_line_max_error_p90_ms": round(_percentile(proposal_max, 0.90), 3),
        "proposal_line_max_error_p95_ms": round(_percentile(proposal_max, 0.95), 3),
        "proposal_within_threshold_rate": round(
            _rate(len(proposed) - len(bad), len(proposed)), 6
        ),
        "bad_proposal_count": len(bad),
        "bad_proposal_rate": round(_rate(len(bad), len(proposed)), 6),
        "unnecessary_proposal_count": len(unnecessary),
        "unnecessary_proposal_rate": round(_rate(len(unnecessary), len(proposed)), 6),
        "harmful_proposal_count": len(harmful),
        "harmful_proposal_rate": round(_rate(len(harmful), len(proposed)), 6),
        "missed_needed_change_count": len(missed),
        "review_needed_change_count": len(review_needed),
        "evaluation_error_threshold_ms": error_threshold_ms,
    }


def evaluate_partial_timeline_dataset(
    manifest_path: Path,
    *,
    error_threshold_ms: float = 250.0,
) -> dict[str, Any]:
    """Evaluate preview decisions against private human timing truth."""

    threshold = _number(error_threshold_ms, label="error_threshold_ms")
    if threshold <= 0:
        raise PartialTimelineEvaluationError("error_threshold_ms must be positive")
    path = manifest_path.resolve()
    payload = _load_json(path, label="partial timeline dataset")
    if str(payload.get("schema_version") or "") != PARTIAL_DATASET_SCHEMA_VERSION:
        raise PartialTimelineEvaluationError(
            f"dataset schema_version must be {PARTIAL_DATASET_SCHEMA_VERSION}"
        )
    dataset = str(payload.get("dataset") or "").strip()
    revision = str(payload.get("dataset_revision") or "").strip()
    split = str(payload.get("split") or "").strip()
    cases = payload.get("cases")
    if not dataset or not revision:
        raise PartialTimelineEvaluationError("dataset and dataset_revision are required")
    if split not in {"calibration", "blind_test"}:
        raise PartialTimelineEvaluationError("split must be calibration or blind_test")
    if not isinstance(cases, list) or not cases:
        raise PartialTimelineEvaluationError("dataset requires non-empty cases")

    rows: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise PartialTimelineEvaluationError("dataset case must be an object")
        case_id = str(case.get("id") or "").strip()
        if not case_id or case_id in case_ids:
            raise PartialTimelineEvaluationError("case ids must be unique/non-empty")
        case_ids.add(case_id)
        rows.extend(_case_rows(case, base=path.parent, error_threshold_ms=threshold))

    by_language: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_risk: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_language[row["language"]].append(row)
        by_case[row["case_id"]].append(row)
        for risk in row["risk_buckets"]:
            by_risk[risk].append(row)

    return {
        "schema_version": PARTIAL_EVALUATION_SCHEMA_VERSION,
        "mode": "partial_timeline_preview_evaluation",
        "dataset": dataset,
        "dataset_revision": revision,
        "split": split,
        "releaseable": False,
        "automatic_timing_change_allowed": False,
        "evaluation_only": True,
        "overall": _aggregate(rows, error_threshold_ms=threshold),
        "by_language": {
            key: _aggregate(value, error_threshold_ms=threshold)
            for key, value in sorted(by_language.items())
        },
        "by_risk_bucket": {
            key: _aggregate(value, error_threshold_ms=threshold)
            for key, value in sorted(by_risk.items())
        },
        "by_case": {
            key: _aggregate(value, error_threshold_ms=threshold)
            for key, value in sorted(by_case.items())
        },
        "safety": (
            "privacy-safe evaluation only; no lyric text is emitted; metrics do not "
            "promote partial timing previews into authoritative timing or release"
        ),
    }
