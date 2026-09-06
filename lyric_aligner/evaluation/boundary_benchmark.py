"""Measurement-only human-gold benchmark for subtitle boundary backends.

This module reports backend error against manually audited boundaries and the
editor baseline.  It deliberately does *not* grant timing authority.  The sole
production authority gate lives in ``timeline.boundary_calibration`` so there is
only one calibration policy and one signed calibration artifact format.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any, Mapping, Sequence


BOUNDARY_GOLD_SCHEMA_VERSION = "1.0"
BOUNDARY_BENCHMARK_SCHEMA_VERSION = "2.0"


class BoundaryBenchmarkError(ValueError):
    """Raised when gold/evidence lineage or metrics are invalid."""


@dataclass(frozen=True)
class BoundaryBenchmarkConfig:
    fps: float = 30.0

    def validate(self) -> None:
        if not math.isfinite(self.fps) or self.fps <= 0:
            raise BoundaryBenchmarkError("fps must be finite and > 0")

    @property
    def frame_ms(self) -> float:
        return 1000.0 / self.fps

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return ordered[low]
    weight = pos - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _validate_lineage(
    plan: Mapping[str, Any], evidence: Mapping[str, Any], gold: Mapping[str, Any]
) -> None:
    if gold.get("schema_version") != BOUNDARY_GOLD_SCHEMA_VERSION:
        raise BoundaryBenchmarkError("unsupported boundary gold schema")
    for key in ("task_fingerprint_sha256", "source_srt_sha256", "final_audio_sha256"):
        values = {str(obj.get(key) or "") for obj in (plan, evidence, gold)}
        if len(values) != 1 or "" in values:
            raise BoundaryBenchmarkError(f"boundary benchmark lineage mismatch for {key}")


def _effective_error_ms(predicted_ms: int, gold_ms: int, uncertainty_ms: int) -> int:
    return max(0, abs(int(predicted_ms) - int(gold_ms)) - max(0, int(uncertainty_ms)))


def _summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    errors = [float(row["effective_error_ms"]) for row in rows]
    frames = [float(row["effective_error_frames"]) for row in rows]
    return {
        "sample_count": len(rows),
        "distinct_track_count": len({str(row.get("track") or "") for row in rows if str(row.get("track") or "")}),
        "median_effective_error_ms": median(errors) if errors else None,
        "p90_effective_error_ms": _percentile(errors, 0.90),
        "worst_effective_error_ms": max(errors) if errors else None,
        "median_effective_error_frames": median(frames) if frames else None,
        "p90_effective_error_frames": _percentile(frames, 0.90),
        "within_1_frame_fraction": (
            sum(value <= 1.0 for value in frames) / len(frames) if frames else None
        ),
        "within_3_frames_fraction": (
            sum(value <= 3.0 for value in frames) / len(frames) if frames else None
        ),
    }


def evaluate_boundary_evidence_against_gold(
    *,
    plan: Mapping[str, Any],
    evidence: Mapping[str, Any],
    gold: Mapping[str, Any],
    config: BoundaryBenchmarkConfig | None = None,
) -> dict[str, Any]:
    """Measure evidence vs human gold without creating timing authority."""

    config = config or BoundaryBenchmarkConfig()
    config.validate()
    _validate_lineage(plan, evidence, gold)

    jobs_raw = plan.get("jobs")
    records_raw = evidence.get("records")
    gold_raw = gold.get("records")
    if not isinstance(jobs_raw, list) or not isinstance(records_raw, list) or not isinstance(gold_raw, list):
        raise BoundaryBenchmarkError("boundary benchmark artifacts must contain lists")

    job_by_identity: dict[tuple[int, str], Mapping[str, Any]] = {}
    job_by_id: dict[str, Mapping[str, Any]] = {}
    for job in jobs_raw:
        if not isinstance(job, Mapping):
            raise BoundaryBenchmarkError("boundary plan job must be an object")
        identity = (int(job["cue_number"]), str(job["boundary_kind"]))
        boundary_id = str(job["boundary_id"])
        if identity in job_by_identity or boundary_id in job_by_id:
            raise BoundaryBenchmarkError("duplicate boundary plan identity")
        job_by_identity[identity] = job
        job_by_id[boundary_id] = job

    record_by_id: dict[str, Mapping[str, Any]] = {}
    for record in records_raw:
        if not isinstance(record, Mapping):
            raise BoundaryBenchmarkError("boundary evidence record must be an object")
        boundary_id = str(record.get("boundary_id") or "")
        if not boundary_id or boundary_id in record_by_id:
            raise BoundaryBenchmarkError("duplicate/blank boundary evidence id")
        record_by_id[boundary_id] = record
    if set(record_by_id) != set(job_by_id):
        raise BoundaryBenchmarkError("boundary evidence does not cover exact plan")

    samples: list[dict[str, Any]] = []
    seen_gold: set[tuple[int, str]] = set()
    for row in gold_raw:
        if not isinstance(row, Mapping):
            raise BoundaryBenchmarkError("gold record must be an object")
        identity = (int(row["cue_number"]), str(row["boundary_kind"]))
        if identity in seen_gold:
            raise BoundaryBenchmarkError("duplicate gold boundary identity")
        seen_gold.add(identity)
        if identity not in job_by_identity:
            raise BoundaryBenchmarkError("gold boundary is not present in plan")
        partition = str(row.get("partition") or "calibration")
        if partition not in {"calibration", "holdout"}:
            raise BoundaryBenchmarkError("gold partition must be calibration or holdout")
        gold_ms = int(row["gold_ms"])
        uncertainty_ms = max(0, int(row.get("gold_uncertainty_ms", 0)))
        if gold_ms < 0:
            raise BoundaryBenchmarkError("gold_ms must be >= 0")
        job = job_by_identity[identity]
        editor_ms = int(job["editor_ms"])
        editor_effective = _effective_error_ms(editor_ms, gold_ms, uncertainty_ms)
        evidence_record = record_by_id[str(job["boundary_id"])]
        points = evidence_record.get("points")
        if not isinstance(points, list):
            raise BoundaryBenchmarkError("evidence points must be a list")
        for point in points:
            if not isinstance(point, Mapping):
                raise BoundaryBenchmarkError("boundary evidence point must be an object")
            predicted_ms = int(point["boundary_ms"])
            effective = _effective_error_ms(predicted_ms, gold_ms, uncertainty_ms)
            samples.append(
                {
                    "cue_number": identity[0],
                    "boundary_kind": identity[1],
                    "track": str(row.get("track") or ""),
                    "partition": partition,
                    "gold_ms": gold_ms,
                    "gold_uncertainty_ms": uncertainty_ms,
                    "editor_ms": editor_ms,
                    "editor_effective_error_ms": editor_effective,
                    "family": str(point.get("family") or ""),
                    "correlation_group": str(point.get("correlation_group") or ""),
                    "predicted_ms": predicted_ms,
                    "raw_abs_error_ms": abs(predicted_ms - gold_ms),
                    "effective_error_ms": effective,
                    "effective_error_frames": effective / config.frame_ms,
                    "improvement_vs_editor_ms": editor_effective - effective,
                }
            )

    scopes = sorted(
        {
            (str(row["family"]), str(row["boundary_kind"]))
            for row in samples
            if str(row["family"])
        }
    )
    scope_summaries: dict[str, Any] = {}
    for family, boundary_kind in scopes:
        scoped = [
            row
            for row in samples
            if row["family"] == family and row["boundary_kind"] == boundary_kind
        ]
        key = f"{family}::{boundary_kind}"
        scope_summaries[key] = {
            "family": family,
            "boundary_kind": boundary_kind,
            "partitions": {
                "calibration": _summary([row for row in scoped if row["partition"] == "calibration"]),
                "holdout": _summary([row for row in scoped if row["partition"] == "holdout"]),
                "all": _summary(scoped),
            },
            "authority_granted": False,
            "authority_note": "measurement_only; use boundary_calibration artifact for authority",
        }

    return {
        "schema_version": BOUNDARY_BENCHMARK_SCHEMA_VERSION,
        "mode": "human_gold_measurement_only",
        "task_fingerprint_sha256": str(plan["task_fingerprint_sha256"]),
        "source_srt_sha256": str(plan["source_srt_sha256"]),
        "final_audio_sha256": str(plan["final_audio_sha256"]),
        "config": config.to_dict(),
        "gold_record_count": len(gold_raw),
        "evidence_sample_count": len(samples),
        "scope_summaries": scope_summaries,
        "samples": samples,
    }
