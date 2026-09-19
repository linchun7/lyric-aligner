"""Conservative final-mix acoustic boundary evidence for lyric timing.

This backend does **not** recognize lyric text.  It looks only for a local
harmonic-energy boundary near an editor prior and therefore can corroborate a
singing/forced aligner without sharing its semantic failure mode.  Its output is
never timing authority by itself; :mod:`lyric_aligner.timeline.boundary_authority`
requires another independent high-confidence acoustic family before mutation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import librosa
import numpy as np


ACOUSTIC_BOUNDARY_BACKEND_ID = "librosa_harmonic_boundary_v1"
ACOUSTIC_BOUNDARY_CORRELATION_GROUP = "librosa_final_mix_acoustic_v1"


class AcousticBoundaryError(ValueError):
    """Raised when a local acoustic boundary cannot be analyzed safely."""


@dataclass(frozen=True)
class AcousticBoundaryConfig:
    sample_rate: int = 22050
    search_ms: int = 900
    analysis_padding_ms: int = 350
    frame_length: int = 1024
    hop_length: int = 128
    smoothing_frames: int = 5
    min_peak_z: float = 1.8
    max_candidate_disagreement_ms: int = 220

    def validate(self) -> None:
        if self.sample_rate < 8000:
            raise AcousticBoundaryError("sample_rate must be >= 8000")
        if self.search_ms < 100:
            raise AcousticBoundaryError("search_ms must be >= 100")
        if self.analysis_padding_ms < 0:
            raise AcousticBoundaryError("analysis_padding_ms must be >= 0")
        if self.frame_length < 128 or self.hop_length < 1:
            raise AcousticBoundaryError("invalid frame/hop length")
        if self.smoothing_frames < 1:
            raise AcousticBoundaryError("smoothing_frames must be >= 1")
        if self.min_peak_z <= 0:
            raise AcousticBoundaryError("min_peak_z must be > 0")
        if self.max_candidate_disagreement_ms < 0:
            raise AcousticBoundaryError("max_candidate_disagreement_ms must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _robust_z(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return values
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    scale = max(1e-9, 1.4826 * mad)
    return (values - median) / scale


def _smooth(values: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or values.size < width:
        return values.astype(np.float64, copy=True)
    kernel = np.ones(width, dtype=np.float64) / float(width)
    return np.convolve(values, kernel, mode="same")


def _nearest_distinct_second(score: np.ndarray, peak_index: int, *, guard_frames: int) -> float:
    if score.size <= 1:
        return float("-inf")
    mask = np.ones(score.shape, dtype=bool)
    left = max(0, peak_index - guard_frames)
    right = min(score.size, peak_index + guard_frames + 1)
    mask[left:right] = False
    if not np.any(mask):
        return float("-inf")
    return float(np.max(score[mask]))


def _analyze_array(
    y: np.ndarray,
    *,
    sr: int,
    window_start_ms: int,
    editor_ms: int,
    boundary_kind: Literal["start", "end"],
    config: AcousticBoundaryConfig,
) -> dict[str, Any]:
    if y.ndim != 1 or y.size < config.frame_length * 2:
        raise AcousticBoundaryError("analysis window is too short")
    if not np.any(np.isfinite(y)):
        raise AcousticBoundaryError("analysis window contains no finite audio")
    y = np.nan_to_num(y.astype(np.float32, copy=False))
    peak_abs = float(np.max(np.abs(y)))
    if peak_abs < 1e-6:
        return {
            "available": False,
            "reason": "near_silence_window",
            "confidence": 0.0,
            "uncertainty_ms": config.search_ms,
        }

    # HPSS is deliberately local.  Harmonic energy is a better proxy for a sung
    # syllable than broadband/percussive onset strength, while remaining fully
    # independent of the lyric text/alignment model.
    harmonic, _ = librosa.effects.hpss(y)
    rms = librosa.feature.rms(
        y=harmonic,
        frame_length=config.frame_length,
        hop_length=config.hop_length,
        center=True,
    )[0]
    log_rms = np.log(np.maximum(rms, 1e-8))
    log_rms = _smooth(log_rms, config.smoothing_frames)
    derivative = np.gradient(log_rms)
    directional = derivative if boundary_kind == "start" else -derivative
    directional_z = _robust_z(directional)

    onset = librosa.onset.onset_strength(
        y=harmonic,
        sr=sr,
        hop_length=config.hop_length,
        center=True,
    )
    if onset.size != directional_z.size:
        common = min(onset.size, directional_z.size)
        onset = onset[:common]
        directional_z = directional_z[:common]
    onset_z = _robust_z(onset)

    frame_times_ms = (
        window_start_ms
        + librosa.frames_to_time(
            np.arange(directional_z.size), sr=sr, hop_length=config.hop_length
        )
        * 1000.0
    )
    search_mask = np.abs(frame_times_ms - float(editor_ms)) <= float(config.search_ms)
    if not np.any(search_mask):
        raise AcousticBoundaryError("no frames fall inside boundary search window")

    # A weak editor-centered prior breaks musical-beat ties but cannot override
    # the local acoustic derivative.  Sigma == search window, so a candidate at
    # the edge loses only 0.5 score rather than being excluded.
    prior = -0.5 * ((frame_times_ms - float(editor_ms)) / float(config.search_ms)) ** 2
    combined = directional_z + 0.25 * onset_z + 0.20 * prior
    eligible_indices = np.flatnonzero(search_mask)
    local_scores = combined[eligible_indices]
    best_local = int(np.argmax(local_scores))
    best_index = int(eligible_indices[best_local])
    boundary_ms = int(round(float(frame_times_ms[best_index])))
    peak_z = float(directional_z[best_index])

    # Cross-check the derivative-selected point against an onset-enhanced point.
    enhanced_index = int(eligible_indices[int(np.argmax(local_scores))])
    derivative_index = int(
        eligible_indices[int(np.argmax(directional_z[eligible_indices] + 0.10 * prior[eligible_indices]))]
    )
    enhanced_ms = int(round(float(frame_times_ms[enhanced_index])))
    derivative_ms = int(round(float(frame_times_ms[derivative_index])))
    disagreement_ms = abs(enhanced_ms - derivative_ms)

    hop_ms = 1000.0 * config.hop_length / float(sr)
    guard_frames = max(1, int(round(100.0 / hop_ms)))
    second = _nearest_distinct_second(combined[eligible_indices], best_local, guard_frames=guard_frames)
    prominence = float(local_scores[best_local] - second) if np.isfinite(second) else float(local_scores[best_local])

    # Confidence intentionally tops out below 1.0.  A strong monotonic boundary
    # with a distinct local peak can exceed the 0.75 authority filter, but only
    # another independent family may then authorize mutation.
    strength_component = np.clip((peak_z - config.min_peak_z) / 3.0, 0.0, 1.0)
    prominence_component = np.clip(prominence / 2.5, 0.0, 1.0)
    agreement_component = np.clip(
        1.0 - disagreement_ms / max(1.0, float(config.max_candidate_disagreement_ms)),
        0.0,
        1.0,
    )
    confidence = float(
        np.clip(
            0.45 * strength_component
            + 0.30 * prominence_component
            + 0.25 * agreement_component,
            0.0,
            0.92,
        )
    )
    uncertainty_ms = max(
        int(round(hop_ms * 2.0)),
        disagreement_ms,
        int(round(180.0 * (1.0 - confidence))),
    )
    available = peak_z >= config.min_peak_z and confidence >= 0.50
    if not available:
        return {
            "available": False,
            "reason": "weak_or_ambiguous_harmonic_boundary",
            "boundary_ms": boundary_ms,
            "confidence": round(confidence, 6),
            "uncertainty_ms": uncertainty_ms,
            "peak_z": round(peak_z, 6),
            "prominence": round(prominence, 6),
            "candidate_disagreement_ms": disagreement_ms,
        }
    return {
        "available": True,
        "boundary_ms": boundary_ms,
        "confidence": round(confidence, 6),
        "uncertainty_ms": uncertainty_ms,
        "peak_z": round(peak_z, 6),
        "prominence": round(prominence, 6),
        "candidate_disagreement_ms": disagreement_ms,
    }


def analyze_final_mix_boundary(
    audio_path: str | Path,
    *,
    editor_ms: int,
    boundary_kind: Literal["start", "end"],
    config: AcousticBoundaryConfig | None = None,
) -> dict[str, Any]:
    """Analyze one local final-mix boundary and return a backend evidence record."""

    config = config or AcousticBoundaryConfig()
    config.validate()
    if boundary_kind not in {"start", "end"}:
        raise AcousticBoundaryError("boundary_kind must be 'start' or 'end'")
    editor_ms = int(editor_ms)
    if editor_ms < 0:
        raise AcousticBoundaryError("editor_ms must be >= 0")
    path = Path(audio_path)
    if not path.is_file():
        raise AcousticBoundaryError(f"audio file does not exist: {path}")

    total_context = config.search_ms + config.analysis_padding_ms
    window_start_ms = max(0, editor_ms - total_context)
    window_end_ms = editor_ms + total_context
    y, sr = librosa.load(
        path,
        sr=config.sample_rate,
        mono=True,
        offset=window_start_ms / 1000.0,
        duration=max(0.1, (window_end_ms - window_start_ms) / 1000.0),
    )
    result = _analyze_array(
        y,
        sr=sr,
        window_start_ms=window_start_ms,
        editor_ms=editor_ms,
        boundary_kind=boundary_kind,
        config=config,
    )
    family = "final_mix_acoustic_onset" if boundary_kind == "start" else "final_mix_acoustic_offset"
    base = {
        "family": family,
        "correlation_group": ACOUSTIC_BOUNDARY_CORRELATION_GROUP,
        "backend_id": ACOUSTIC_BOUNDARY_BACKEND_ID,
        "backend_version": librosa.__version__,
        "model_id": "none_signal_processing",
        "model_revision": "v1",
        "audio_basis": "final_mix",
        "editor_ms": editor_ms,
        "boundary_kind": boundary_kind,
        "analysis_window_ms": [window_start_ms, window_end_ms],
        "config": config.to_dict(),
    }
    base.update(result)
    return base


def evidence_point_from_acoustic_result(result: dict[str, Any]) -> dict[str, Any] | None:
    """Convert an available diagnostic result into boundary-authority evidence."""

    if not result.get("available"):
        return None
    return {
        "family": str(result["family"]),
        "correlation_group": str(result["correlation_group"]),
        "boundary_ms": int(result["boundary_ms"]),
        "confidence": float(result["confidence"]),
        "uncertainty_ms": int(result["uncertainty_ms"]),
        "backend_id": str(result["backend_id"]),
        "backend_version": str(result["backend_version"]),
        "model_id": str(result["model_id"]),
        "model_revision": str(result["model_revision"]),
        "audio_basis": "final_mix",
        "evidence_sha256": "",  # artifact writer may bind a persisted diagnostic SHA
        "calibration_passed": False,
        "calibration_sha256": "",
    }
