#!/usr/bin/env python3
"""Build an Independent Fine affine reference audit without consulting Independent Fine predictions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

import librosa
import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lyric_aligner.contracts.artifacts import atomic_write_json
from scripts.v4_evaluate_independent_fine_benchmark import _load_hashed
from scripts.v4_run_independent_fine_benchmark import _resolve_locked_file
from scripts.v4_select_independent_fine_holdout_pairs import SELECTION_SCHEMA_VERSION

AUDIT_SCHEMA_VERSION = "independent-fine-affine-truth-audit-1.0"
SAMPLE_RATE_HZ = 8000
FRAME_SECONDS = 0.05
WINDOW_SECONDS = 24.0
EDGE_GUARD_SECONDS = 15.0
OFFSET_MIN_SECONDS = -3.0
OFFSET_MAX_SECONDS = 3.0
OFFSET_STEP_SECONDS = 0.01
DISTANT_SECOND_SEPARATION_SECONDS = 0.20
MEDIAN_CORRELATION_MIN = 0.55
MINIMUM_CORRELATION_MIN = 0.40
MEDIAN_MARGIN_MIN = 0.03
OFFSET_RANGE_MAX_SECONDS = 0.20
ABS_SLOPE_DRIFT_MAX = 0.0025


class IndependentFineAffineAuditError(ValueError):
    pass


def _sha_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _robust_log_rms(audio: np.ndarray, *, sr: int) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(audio, dtype=np.float64)
    frame_length = int(round(FRAME_SECONDS * sr))
    if frame_length < 1 or y.ndim != 1 or y.size < frame_length * 8:
        raise IndependentFineAffineAuditError("audio is too short for RMS truth audit")
    frame_count = y.size // frame_length
    frames = y[: frame_count * frame_length].reshape(frame_count, frame_length)
    rms = np.sqrt(np.mean(np.square(frames), axis=1) + 1e-12)
    log_rms = np.log(np.maximum(rms, 1e-8))
    median = float(np.median(log_rms))
    mad = float(np.median(np.abs(log_rms - median)))
    scale = max(1.4826 * mad, 1e-6)
    normalized = np.clip((log_rms - median) / scale, -5.0, 5.0)
    centers = (np.arange(frame_count, dtype=np.float64) + 0.5) * FRAME_SECONDS
    return centers, normalized.astype(np.float64)


def _pearson(left: np.ndarray, right: np.ndarray) -> float:
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    if a.shape != b.shape or a.size < 8:
        return float("nan")
    a = a - np.mean(a)
    b = b - np.mean(b)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 1e-12:
        return float("nan")
    return float(np.dot(a, b) / denominator)


def _audit_window(
    *,
    mix_times: np.ndarray,
    mix_values: np.ndarray,
    source_times: np.ndarray,
    source_values: np.ndarray,
    start_s: float,
    end_s: float,
    nominal_slope: float,
) -> dict[str, Any]:
    mask = (mix_times >= start_s) & (mix_times < end_s)
    query_times = mix_times[mask]
    query_values = mix_values[mask]
    if query_values.size < 32:
        return {"valid": False, "failure_reason": "too_few_mix_frames"}
    offsets = np.arange(
        OFFSET_MIN_SECONDS,
        OFFSET_MAX_SECONDS + OFFSET_STEP_SECONDS * 0.5,
        OFFSET_STEP_SECONDS,
        dtype=np.float64,
    )
    scored: list[tuple[float, float]] = []
    for offset in offsets:
        projected = nominal_slope * query_times + float(offset)
        if projected[0] < source_times[0] or projected[-1] > source_times[-1]:
            continue
        sampled = np.interp(projected, source_times, source_values)
        correlation = _pearson(query_values, sampled)
        if math.isfinite(correlation):
            scored.append((float(offset), correlation))
    if not scored:
        return {"valid": False, "failure_reason": "no_valid_offset_candidate"}
    scored.sort(key=lambda item: item[1], reverse=True)
    best_offset, best_corr = scored[0]
    distant = [item for item in scored[1:] if abs(item[0] - best_offset) + 1e-12 >= DISTANT_SECOND_SEPARATION_SECONDS]
    if not distant:
        return {"valid": False, "failure_reason": "no_distant_second_candidate"}
    second_offset, second_corr = distant[0]
    return {
        "valid": True,
        "best_offset_seconds": best_offset,
        "best_correlation": best_corr,
        "distant_second_offset_seconds": second_offset,
        "distant_second_correlation": second_corr,
        "distant_second_margin": best_corr - second_corr,
    }


def audit_selection(*, selection_path: Path, expected_partition: str = "holdout") -> dict[str, Any]:
    selection = _load_hashed(selection_path)
    if selection.get("schema_version") != SELECTION_SCHEMA_VERSION:
        raise IndependentFineAffineAuditError("unexpected pair-selection schema")
    if selection.get("partition") != expected_partition:
        raise IndependentFineAffineAuditError("pair-selection partition mismatch")
    if bool(selection.get("independent_fine_predictions_consulted")):
        raise IndependentFineAffineAuditError("truth selection must not consult Independent Fine predictions")
    records = selection.get("records")
    if not isinstance(records, list) or len(records) < 4:
        raise IndependentFineAffineAuditError("truth audit requires at least four selected pairs")

    pairs: list[dict[str, Any]] = []
    for pair_index, row in enumerate(records, start=1):
        if not isinstance(row, Mapping):
            raise IndependentFineAffineAuditError("pair-selection row must be an object")
        source_sha = str(row.get("source_sha256") or "")
        adjusted_sha = str(row.get("adjusted_sha256") or "")
        source_path = str(row.get("source_path") or "")
        adjusted_path = str(row.get("adjusted_path") or "")
        source_bpm = float(row.get("source_bpm"))
        target_bpm = float(row.get("target_bpm"))
        nominal_slope = float(row.get("nominal_slope"))
        if source_bpm <= 0 or target_bpm <= 0 or nominal_slope <= 0:
            raise IndependentFineAffineAuditError("pair BPM/slope is invalid")
        if abs(nominal_slope - target_bpm / source_bpm) > 1e-12:
            raise IndependentFineAffineAuditError("pair nominal slope does not match BPM ratio")
        source_file = _resolve_locked_file(source_path, source_sha)
        adjusted_file = _resolve_locked_file(adjusted_path, adjusted_sha)
        source_audio, source_sr = librosa.load(source_file, sr=SAMPLE_RATE_HZ, mono=True)
        adjusted_audio, adjusted_sr = librosa.load(adjusted_file, sr=SAMPLE_RATE_HZ, mono=True)
        if int(source_sr) != SAMPLE_RATE_HZ or int(adjusted_sr) != SAMPLE_RATE_HZ:
            raise IndependentFineAffineAuditError("truth audit resample rate mismatch")
        source_times, source_env = _robust_log_rms(source_audio, sr=SAMPLE_RATE_HZ)
        adjusted_times, adjusted_env = _robust_log_rms(adjusted_audio, sr=SAMPLE_RATE_HZ)
        adjusted_duration = float(len(adjusted_audio) / SAMPLE_RATE_HZ)
        source_duration = float(len(source_audio) / SAMPLE_RATE_HZ)
        half_window = WINDOW_SECONDS / 2.0
        minimum_center = EDGE_GUARD_SECONDS + half_window
        maximum_center = adjusted_duration - EDGE_GUARD_SECONDS - half_window
        if maximum_center <= minimum_center:
            raise IndependentFineAffineAuditError("adjusted track is too short for three guarded windows")
        windows: list[dict[str, Any]] = []
        for window_index, fraction in enumerate((0.25, 0.50, 0.75), start=1):
            center = min(max(adjusted_duration * fraction, minimum_center), maximum_center)
            start = center - half_window
            end = center + half_window
            result = _audit_window(
                mix_times=adjusted_times,
                mix_values=adjusted_env,
                source_times=source_times,
                source_values=source_env,
                start_s=start,
                end_s=end,
                nominal_slope=nominal_slope,
            )
            windows.append({
                "window": window_index,
                "center_adjusted_seconds": center,
                "start_adjusted_seconds": start,
                "end_adjusted_seconds": end,
                **result,
            })
        valid_windows = [window for window in windows if window.get("valid") is True]
        if len(valid_windows) == 3:
            offsets = np.asarray([float(window["best_offset_seconds"]) for window in valid_windows], dtype=np.float64)
            centers = np.asarray([float(window["center_adjusted_seconds"]) for window in valid_windows], dtype=np.float64)
            correlations = [float(window["best_correlation"]) for window in valid_windows]
            margins = [float(window["distant_second_margin"]) for window in valid_windows]
            slope_correction, intercept = np.polyfit(centers, offsets, 1)
            summary = {
                "all_windows_valid": True,
                "median_offset_seconds": float(np.median(offsets)),
                "offset_range_seconds": float(np.max(offsets) - np.min(offsets)),
                "correlation_median": float(np.median(correlations)),
                "correlation_min": float(np.min(correlations)),
                "distant_second_margin_median": float(np.median(margins)),
                "distant_second_margin_min": float(np.min(margins)),
                "implied_slope_drift_correction": float(slope_correction),
                "implied_slope_intercept": float(intercept),
            }
        else:
            summary = {
                "all_windows_valid": False,
                "median_offset_seconds": None,
                "offset_range_seconds": None,
                "correlation_median": None,
                "correlation_min": None,
                "distant_second_margin_median": None,
                "distant_second_margin_min": None,
                "implied_slope_drift_correction": None,
                "implied_slope_intercept": None,
            }
        gate = {
            "all_three_windows_valid": bool(summary["all_windows_valid"]),
            "median_correlation_ge_0.55": bool(summary["all_windows_valid"] and float(summary["correlation_median"]) >= MEDIAN_CORRELATION_MIN),
            "minimum_correlation_ge_0.40": bool(summary["all_windows_valid"] and float(summary["correlation_min"]) >= MINIMUM_CORRELATION_MIN),
            "median_margin_ge_0.03": bool(summary["all_windows_valid"] and float(summary["distant_second_margin_median"]) >= MEDIAN_MARGIN_MIN),
            "offset_range_le_0.20": bool(summary["all_windows_valid"] and float(summary["offset_range_seconds"]) <= OFFSET_RANGE_MAX_SECONDS),
            "abs_slope_drift_le_0.0025": bool(summary["all_windows_valid"] and abs(float(summary["implied_slope_drift_correction"])) <= ABS_SLOPE_DRIFT_MAX),
        }
        pairs.append({
            "pair": pair_index,
            "title": str(row.get("title") or ""),
            "identities": {
                "source_path": source_path,
                "source_sha256": source_sha,
                "adjusted_path": adjusted_path,
                "adjusted_sha256": adjusted_sha,
                "source_bpm": source_bpm,
                "target_bpm": target_bpm,
                "source_seconds": source_duration,
                "adjusted_seconds": adjusted_duration,
                "nominal_slope": nominal_slope,
            },
            "windows": windows,
            "summary": summary,
            "gate": gate,
            "truth_usable": all(gate.values()),
        })

    truth_usable_count = sum(pair["truth_usable"] for pair in pairs)
    artifact: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "partition": expected_partition,
        "pair_selection_sha256": selection["artifact_sha256"],
        "frozen_policy_sha256": selection.get("frozen_policy_sha256"),
        "method": {
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "channels": "mono",
            "envelope": "broadband log-RMS",
            "frame_seconds": FRAME_SECONDS,
            "hop_seconds": FRAME_SECONDS,
            "normalization": "full-file median/MAD, clip [-5,5]",
            "search": "nominal slope fixed; source offset -3.0..3.0s in 0.01s increments; Pearson against interpolated source envelope",
            "windows": "three adjusted-time 24s windows centered at 25%, 50%, 75% of adjusted duration, clamped >=15s from content edges",
            "distant_second_separation_seconds": DISTANT_SECOND_SEPARATION_SECONDS,
        },
        "gate": {
            "all_three_windows_valid": True,
            "median_correlation_min": MEDIAN_CORRELATION_MIN,
            "minimum_correlation_min": MINIMUM_CORRELATION_MIN,
            "median_best_vs_distant_second_margin_min": MEDIAN_MARGIN_MIN,
            "offset_range_max_seconds": OFFSET_RANGE_MAX_SECONDS,
            "absolute_implied_slope_drift_correction_max": ABS_SLOPE_DRIFT_MAX,
        },
        "pair_count": len(pairs),
        "pairs": pairs,
        "truth_usable_count": truth_usable_count,
        "independent_fine_predictions_consulted": False,
    }
    artifact["artifact_sha256"] = _sha_json(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--expected-partition", choices=("calibration", "holdout"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f"affine truth audit output already exists: {args.out}")
    artifact = audit_selection(selection_path=args.selection, expected_partition=args.expected_partition)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.out, artifact)
    print(json.dumps({
        "out": str(args.out),
        "partition": artifact["partition"],
        "pair_count": artifact["pair_count"],
        "truth_usable_count": artifact["truth_usable_count"],
        "artifact_sha256": artifact["artifact_sha256"],
        "independent_fine_predictions_consulted": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
