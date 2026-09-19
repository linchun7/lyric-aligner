"""Human-gold calibration for automatic lyric-boundary evidence backends.

Independence alone is not timing authority.  A backend must prove its boundary
error distribution on manually audited audio, including a held-out partition,
before any of its predictions may participate in automatic subtitle mutation.
Calibration is deliberately scoped to one boundary kind, language, model build,
and audio basis so that a good start detector cannot silently authorize ends (or
a Mandarin model authorize English, etc.).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from math import ceil
from statistics import median
from typing import Any, Mapping, Sequence


BOUNDARY_CALIBRATION_SCHEMA_VERSION = "boundary-backend-calibration-2.0"
REQUIRED_SCOPE_KEYS = (
    "boundary_kind",
    "audio_basis",
    "language",
    "backend_version",
    "model_id",
    "model_revision",
)


class BoundaryCalibrationError(ValueError):
    pass


@dataclass(frozen=True)
class BoundaryCalibrationPolicy:
    """Production authority policy.

    The default full-benchmark policy requires 30 human-audited boundaries split
    into calibration and holdout sets. A smaller policy is production-authoritative
    only when a higher-level, versioned human-gold schema explicitly binds that exact
    policy; arbitrary caller-supplied smaller policies remain non-authoritative.
    """

    fps: float = 30.0
    min_calibration_samples: int = 20
    min_holdout_samples: int = 10
    min_distinct_tracks: int = 5
    min_prediction_coverage: float = 0.75
    max_median_effective_error_frames: float = 2.0
    max_p90_effective_error_frames: float = 5.0
    max_single_effective_error_frames: float = 12.0
    catastrophic_error_frames: float = 15.0
    max_catastrophic_fraction: float = 0.02
    max_gold_uncertainty_ms: int = 100

    def validate(self) -> None:
        if not math.isfinite(self.fps) or self.fps <= 0:
            raise BoundaryCalibrationError("fps must be finite and > 0")
        if self.min_calibration_samples < 1:
            raise BoundaryCalibrationError("min_calibration_samples must be >= 1")
        if self.min_holdout_samples < 1:
            raise BoundaryCalibrationError("min_holdout_samples must be >= 1")
        if self.min_distinct_tracks < 1:
            raise BoundaryCalibrationError("min_distinct_tracks must be >= 1")
        if not 0.0 < self.min_prediction_coverage <= 1.0:
            raise BoundaryCalibrationError("min_prediction_coverage must be within (0,1]")
        thresholds = (
            self.max_median_effective_error_frames,
            self.max_p90_effective_error_frames,
            self.max_single_effective_error_frames,
            self.catastrophic_error_frames,
        )
        if any(not math.isfinite(value) or value < 0 for value in thresholds):
            raise BoundaryCalibrationError("frame thresholds must be finite/nonnegative")
        if not (
            self.max_median_effective_error_frames
            <= self.max_p90_effective_error_frames
            <= self.max_single_effective_error_frames
            <= self.catastrophic_error_frames
        ):
            raise BoundaryCalibrationError("calibration frame thresholds must be monotonic")
        if not 0.0 <= self.max_catastrophic_fraction <= 1.0:
            raise BoundaryCalibrationError("max_catastrophic_fraction must be within [0,1]")
        if self.max_gold_uncertainty_ms < 0:
            raise BoundaryCalibrationError("max_gold_uncertainty_ms must be nonnegative")

    @property
    def frame_ms(self) -> float:
        return 1000.0 / self.fps


def _canonical_json_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _p90(values: Sequence[float]) -> float:
    if not values:
        raise BoundaryCalibrationError("p90 requires at least one value")
    ordered = sorted(float(value) for value in values)
    rank = max(1, ceil(0.90 * len(ordered)))
    return ordered[rank - 1]


def _effective_error_ms(predicted_ms: int, gold_ms: int, uncertainty_ms: int) -> int:
    return max(0, abs(int(predicted_ms) - int(gold_ms)) - max(0, int(uncertainty_ms)))


def _partition_summary(
    rows: Sequence[Mapping[str, Any]], *, policy: BoundaryCalibrationPolicy
) -> dict[str, Any]:
    gold_count = len(rows)
    predicted_rows = [row for row in rows if row.get("predicted_ms") is not None]
    effective_ms = [float(row["effective_error_ms"]) for row in predicted_rows]
    effective_frames = [value / policy.frame_ms for value in effective_ms]
    catastrophic = [value >= policy.catastrophic_error_frames for value in effective_frames]
    return {
        "gold_boundary_count": gold_count,
        "prediction_count": len(predicted_rows),
        "prediction_coverage": round(len(predicted_rows) / gold_count, 6) if gold_count else 0.0,
        "distinct_track_count": len({str(row.get("track") or "") for row in rows if str(row.get("track") or "")}),
        "median_effective_error_ms": float(median(effective_ms)) if effective_ms else None,
        "p90_effective_error_ms": _p90(effective_ms) if effective_ms else None,
        "max_effective_error_ms": max(effective_ms) if effective_ms else None,
        "median_effective_error_frames": float(median(effective_frames)) if effective_frames else None,
        "p90_effective_error_frames": _p90(effective_frames) if effective_frames else None,
        "max_effective_error_frames": max(effective_frames) if effective_frames else None,
        "within_1_frame_fraction": (
            sum(value <= 1.0 for value in effective_frames) / len(effective_frames)
            if effective_frames
            else None
        ),
        "within_3_frames_fraction": (
            sum(value <= 3.0 for value in effective_frames) / len(effective_frames)
            if effective_frames
            else None
        ),
        "catastrophic_fraction": (
            sum(catastrophic) / len(catastrophic) if catastrophic else None
        ),
    }


def _quality_pass(summary: Mapping[str, Any], *, policy: BoundaryCalibrationPolicy) -> bool:
    return bool(
        summary.get("prediction_count")
        and float(summary.get("prediction_coverage") or 0.0) >= policy.min_prediction_coverage
        and summary.get("median_effective_error_frames") is not None
        and float(summary["median_effective_error_frames"])
        <= policy.max_median_effective_error_frames
        and summary.get("p90_effective_error_frames") is not None
        and float(summary["p90_effective_error_frames"])
        <= policy.max_p90_effective_error_frames
        and summary.get("max_effective_error_frames") is not None
        and float(summary["max_effective_error_frames"])
        <= policy.max_single_effective_error_frames
        and summary.get("catastrophic_fraction") is not None
        and float(summary["catastrophic_fraction"])
        <= policy.max_catastrophic_fraction
    )


def evaluate_boundary_backend_calibration(
    *,
    backend_id: str,
    family: str,
    correlation_group: str,
    scope: Mapping[str, Any],
    gold_records: Sequence[Mapping[str, Any]],
    predictions_ms: Mapping[str, int | None],
    policy: BoundaryCalibrationPolicy | None = None,
) -> dict[str, Any]:
    """Evaluate one backend/scope against manual boundary gold.

    Gold records must explicitly state ``partition`` (calibration/holdout),
    ``track`` and ``boundary_kind``.  Human annotation uncertainty is subtracted
    from raw error before frame-level quality thresholds are applied.  Missing
    predictions reduce partition coverage rather than disappearing from metrics.
    """

    policy = policy or BoundaryCalibrationPolicy()
    policy.validate()
    backend_id = str(backend_id or "").strip()
    family = str(family or "").strip()
    correlation_group = str(correlation_group or "").strip()
    if not backend_id or not family or not correlation_group:
        raise BoundaryCalibrationError("backend/family/correlation_group must be non-empty")
    if not isinstance(scope, Mapping) or not scope:
        raise BoundaryCalibrationError("calibration scope must be a non-empty object")
    normalized_scope = {key: str(scope.get(key) or "").strip() for key in REQUIRED_SCOPE_KEYS}
    if normalized_scope["boundary_kind"] not in {"start", "end", "internal"}:
        raise BoundaryCalibrationError(
            "calibration scope must bind exactly one boundary_kind: start, end, or internal"
        )
    missing_scope = [key for key, value in normalized_scope.items() if not value]
    if missing_scope:
        raise BoundaryCalibrationError(
            "calibration scope is missing required keys: " + ", ".join(missing_scope)
        )
    # Preserve optional future scope keys, but canonicalize required strings.
    scope_dict = dict(scope)
    scope_dict.update(normalized_scope)

    boundary_gold: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in gold_records:
        if str(raw.get("kind") or "") != "boundary":
            continue
        record_id = str(raw.get("id") or "").strip()
        if not record_id or record_id in seen_ids:
            raise BoundaryCalibrationError("boundary gold ids must be unique/non-empty")
        seen_ids.add(record_id)
        raw_boundary_kind = str(raw.get("boundary_kind") or "").strip()
        if raw_boundary_kind != normalized_scope["boundary_kind"]:
            raise BoundaryCalibrationError(
                f"gold record {record_id} boundary_kind does not match calibration scope"
            )
        partition = str(raw.get("partition") or "").strip()
        if partition not in {"calibration", "holdout"}:
            raise BoundaryCalibrationError(
                f"gold record {record_id} partition must be calibration or holdout"
            )
        track = str(raw.get("track") or "").strip()
        if not track:
            raise BoundaryCalibrationError(f"gold record {record_id} must identify its track")
        try:
            gold_ms = int(raw["gold_ms"])
            uncertainty_ms = max(0, int(raw.get("gold_uncertainty_ms", 0)))
        except (KeyError, TypeError, ValueError) as exc:
            raise BoundaryCalibrationError(f"gold record {record_id} has invalid timing") from exc
        if gold_ms < 0:
            raise BoundaryCalibrationError(f"gold record {record_id} has negative gold_ms")
        if uncertainty_ms > policy.max_gold_uncertainty_ms:
            raise BoundaryCalibrationError(
                f"gold record {record_id} uncertainty exceeds production policy"
            )
        boundary_gold.append(
            {
                "id": record_id,
                "gold_ms": gold_ms,
                "gold_uncertainty_ms": uncertainty_ms,
                "partition": partition,
                "track": track,
                "boundary_kind": raw_boundary_kind,
            }
        )

    rows: list[dict[str, Any]] = []
    for record in boundary_gold:
        record_id = record["id"]
        predicted = predictions_ms.get(record_id)
        row = dict(record)
        if predicted is None:
            row.update(
                {
                    "predicted_ms": None,
                    "raw_abs_error_ms": None,
                    "effective_error_ms": None,
                    "effective_error_frames": None,
                }
            )
            rows.append(row)
            continue
        try:
            predicted_ms = int(predicted)
        except (TypeError, ValueError) as exc:
            raise BoundaryCalibrationError(f"prediction {record_id} is invalid") from exc
        if predicted_ms < 0:
            raise BoundaryCalibrationError(f"prediction {record_id} is negative")
        raw_error = abs(predicted_ms - int(record["gold_ms"]))
        effective = _effective_error_ms(
            predicted_ms,
            int(record["gold_ms"]),
            int(record["gold_uncertainty_ms"]),
        )
        row.update(
            {
                "predicted_ms": predicted_ms,
                "raw_abs_error_ms": raw_error,
                "effective_error_ms": effective,
                "effective_error_frames": effective / policy.frame_ms,
            }
        )
        rows.append(row)

    calibration_rows = [row for row in rows if row["partition"] == "calibration"]
    holdout_rows = [row for row in rows if row["partition"] == "holdout"]
    calibration_summary = _partition_summary(calibration_rows, policy=policy)
    holdout_summary = _partition_summary(holdout_rows, policy=policy)
    all_summary = _partition_summary(rows, policy=policy)

    enough_calibration = len(calibration_rows) >= policy.min_calibration_samples
    enough_holdout = len(holdout_rows) >= policy.min_holdout_samples
    enough_tracks = (
        calibration_summary["distinct_track_count"] >= policy.min_distinct_tracks
        and holdout_summary["distinct_track_count"] >= policy.min_distinct_tracks
        and all_summary["distinct_track_count"] >= policy.min_distinct_tracks
    )
    calibration_quality = _quality_pass(calibration_summary, policy=policy)
    holdout_quality = _quality_pass(holdout_summary, policy=policy)
    passed = bool(
        enough_calibration
        and enough_holdout
        and enough_tracks
        and calibration_quality
        and holdout_quality
    )

    reasons: list[str] = []
    if not enough_calibration:
        reasons.append("insufficient_calibration_samples")
    if not enough_holdout:
        reasons.append("insufficient_holdout_samples")
    if not enough_tracks:
        reasons.append("insufficient_distinct_track_coverage")
    if not calibration_quality:
        reasons.append("calibration_partition_exceeds_policy")
    if not holdout_quality:
        reasons.append("holdout_partition_exceeds_policy")

    artifact = {
        "schema_version": BOUNDARY_CALIBRATION_SCHEMA_VERSION,
        "backend_id": backend_id,
        "family": family,
        "correlation_group": correlation_group,
        "scope": scope_dict,
        "policy": asdict(policy),
        "gold_sha256": _canonical_json_sha(boundary_gold),
        "predictions_sha256": _canonical_json_sha(rows),
        "passed": passed,
        "reasons": reasons,
        "summary": {
            "calibration": calibration_summary,
            "holdout": holdout_summary,
            "all": all_summary,
        },
        "records": rows,
    }
    artifact["artifact_sha256"] = _canonical_json_sha(artifact)
    return artifact


def calibration_artifact_is_authoritative(
    artifact: Mapping[str, Any],
    *,
    backend_id: str,
    family: str,
    correlation_group: str,
    expected_scope: Mapping[str, Any] | None = None,
    policy: BoundaryCalibrationPolicy | None = None,
) -> bool:
    """Strictly validate an artifact before it is referenced by evidence."""

    policy = policy or BoundaryCalibrationPolicy()
    try:
        policy.validate()
    except BoundaryCalibrationError:
        return False
    if artifact.get("schema_version") != BOUNDARY_CALIBRATION_SCHEMA_VERSION:
        return False
    if not bool(artifact.get("passed")):
        return False
    if artifact.get("policy") != asdict(policy):
        return False
    if str(artifact.get("backend_id") or "") != str(backend_id):
        return False
    if str(artifact.get("family") or "") != str(family):
        return False
    if str(artifact.get("correlation_group") or "") != str(correlation_group):
        return False
    scope = artifact.get("scope")
    if not isinstance(scope, Mapping):
        return False
    for key in REQUIRED_SCOPE_KEYS:
        if not str(scope.get(key) or "").strip():
            return False
    if str(scope.get("boundary_kind") or "") not in {"start", "end", "internal"}:
        return False
    if expected_scope is not None:
        for key, expected in expected_scope.items():
            if scope.get(key) != expected:
                return False
    claimed = str(artifact.get("artifact_sha256") or "")
    if len(claimed) != 64:
        return False
    without_hash = dict(artifact)
    without_hash.pop("artifact_sha256", None)
    return claimed == _canonical_json_sha(without_hash)
