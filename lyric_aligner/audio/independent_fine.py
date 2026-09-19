"""Independent percussive/onset evidence for shadow Source-to-Mix refinement.

The production coarse/fine mapper intentionally relies on harmonic Chroma CENS and
MFCC.  This module supplies a genuinely different evidence family: HPSS percussive
energy followed by onset strength and multi-band positive spectral flux.  It is a
shadow observer only; no result from this module may directly mutate a production
timeline until it has passed locked real-song calibration/holdout gates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Iterable

import librosa
import numpy as np


INDEPENDENT_FINE_VERSION = "1.0"
INDEPENDENT_FINE_AUTHORITY = "evaluation_only_never_direct_timing_authority"
INDEPENDENT_FINE_CORRELATION_GROUP = "percussive_multiband_onset_v1"


@dataclass(frozen=True)
class OnsetFeatureBundle:
    sr: int
    hop_length: int
    duration_seconds: float
    onset: np.ndarray
    multiband_flux: np.ndarray

    @property
    def frame_seconds(self) -> float:
        return self.hop_length / self.sr

    @property
    def frame_count(self) -> int:
        return int(self.multiband_flux.shape[1])


@dataclass(frozen=True)
class IndependentFineCandidate:
    source_start: float
    source_end: float
    source_center: float
    estimated_slope: float
    onset_score: float
    multiband_score: float
    fused_score: float
    feature_agreement: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class IndependentFineResult:
    version: str
    authority: str
    correlation_group: str
    mix_start: float
    mix_end: float
    top1: IndependentFineCandidate
    top2: IndependentFineCandidate | None
    candidates: tuple[IndependentFineCandidate, ...]
    margin: float
    ambiguous: bool
    min_score: float
    min_margin: float
    automatic_mutation_allowed: bool = False

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "authority": self.authority,
            "correlation_group": self.correlation_group,
            "mix_start": self.mix_start,
            "mix_end": self.mix_end,
            "top1": self.top1.to_dict(),
            "top2": None if self.top2 is None else self.top2.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "margin": self.margin,
            "ambiguous": self.ambiguous,
            "min_score": self.min_score,
            "min_margin": self.min_margin,
            "automatic_mutation_allowed": self.automatic_mutation_allowed,
        }


def _scale_rows_nonnegative(matrix: np.ndarray) -> np.ndarray:
    values = np.asarray(matrix, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] < 2:
        raise ValueError("onset feature matrix must be 2-D with at least two frames")
    values = np.maximum(values, 0.0)
    scales = np.percentile(values, 95.0, axis=1, keepdims=True)
    scales = np.maximum(scales, 1e-6)
    return np.clip(values / scales, 0.0, 4.0).astype(np.float32)


def _resample_columns(matrix: np.ndarray, start: float, length: float, output_frames: int) -> np.ndarray:
    if output_frames < 2 or length <= 1:
        raise ValueError("feature window is too short")
    positions = np.linspace(start, start + length - 1, output_frames)
    base = np.arange(matrix.shape[1], dtype=np.float64)
    rows = [np.interp(positions, base, feature_row) for feature_row in matrix]
    return np.asarray(rows, dtype=np.float32)


def _flattened_cosine(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape:
        raise ValueError("feature matrices must have equal shapes")
    a = np.asarray(left, dtype=np.float64).ravel()
    b = np.asarray(right, dtype=np.float64).ravel()
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 1e-12:
        return 0.0
    return float(np.clip(np.dot(a, b) / denominator, 0.0, 1.0))


def extract_percussive_onset_features(
    audio: np.ndarray,
    *,
    sr: int,
    hop_length: int = 256,
    n_mels: int = 32,
    flux_band_count: int = 8,
) -> OnsetFeatureBundle:
    """Extract attack-focused features independent from harmonic Chroma/MFCC."""

    y = np.asarray(audio, dtype=np.float32)
    if sr <= 0 or hop_length < 1:
        raise ValueError("sampling parameters must be positive")
    if y.ndim != 1 or y.size < max(2048, hop_length * 4):
        raise ValueError("audio must be a sufficiently long mono waveform")
    if n_mels < flux_band_count or flux_band_count < 2:
        raise ValueError("invalid mel/flux band configuration")

    _, percussive = librosa.effects.hpss(y)
    mel = librosa.feature.melspectrogram(
        y=percussive,
        sr=sr,
        hop_length=hop_length,
        n_mels=n_mels,
        fmin=40.0,
        fmax=float(sr) / 2.0,
        power=2.0,
    ).astype(np.float32)
    log_mel = librosa.power_to_db(np.maximum(mel, 1e-10), ref=np.max).astype(np.float32)
    positive_flux = np.maximum(
        0.0,
        np.diff(log_mel, axis=1, prepend=log_mel[:, :1]),
    )
    groups = np.array_split(np.arange(n_mels), flux_band_count)
    multiband = np.stack(
        [np.mean(positive_flux[indexes, :], axis=0) for indexes in groups],
        axis=0,
    )
    onset = librosa.onset.onset_strength(
        S=log_mel,
        sr=sr,
        hop_length=hop_length,
        aggregate=np.median,
    ).astype(np.float32)[None, :]

    frame_count = min(onset.shape[1], multiband.shape[1])
    if frame_count < 4:
        raise ValueError("audio produced too few onset frames")
    onset = _scale_rows_nonnegative(onset[:, :frame_count])
    multiband = _scale_rows_nonnegative(multiband[:, :frame_count])
    return OnsetFeatureBundle(
        sr=sr,
        hop_length=hop_length,
        duration_seconds=float(len(y) / sr),
        onset=onset,
        multiband_flux=multiband,
    )


def _candidate_positions(
    source: OnsetFeatureBundle,
    *,
    source_length_frames: float,
    step_seconds: float,
    search_start: float,
    search_end: float,
) -> Iterable[int]:
    frame_step = max(1, int(round(step_seconds / source.frame_seconds)))
    first = max(0, int(math.floor(search_start / source.frame_seconds)))
    last = int(math.floor(min(search_end, source.duration_seconds) / source.frame_seconds - source_length_frames))
    if last < first:
        return []
    return range(first, last + 1, frame_step)


def retrieve_independent_onset_window(
    mix: OnsetFeatureBundle,
    source: OnsetFeatureBundle,
    *,
    mix_start: float,
    mix_end: float,
    slopes: Iterable[float],
    source_search_start: float,
    source_search_end: float,
    candidate_step_seconds: float = 0.05,
    top_k: int = 5,
    nms_separation_seconds: float = 0.20,
    min_score: float = 0.48,
    min_margin: float = 0.025,
) -> IndependentFineResult:
    """Retrieve one local mapping using attack-focused evidence only."""

    if mix.sr != source.sr or mix.hop_length != source.hop_length:
        raise ValueError("mix/source onset feature sampling parameters must match")
    if mix_end <= mix_start:
        raise ValueError("mix_end must be after mix_start")
    if source_search_end <= source_search_start:
        raise ValueError("source search interval must be monotonic")
    mix_start_frame = int(round(mix_start / mix.frame_seconds))
    mix_end_frame = int(round(mix_end / mix.frame_seconds))
    if mix_start_frame < 0 or mix_end_frame > mix.frame_count:
        raise ValueError("mix retrieval window is outside feature bundle")
    query_onset = mix.onset[:, mix_start_frame:mix_end_frame]
    query_multiband = mix.multiband_flux[:, mix_start_frame:mix_end_frame]
    if query_onset.shape[1] < 4:
        raise ValueError("mix retrieval window is too short")

    raw: list[IndependentFineCandidate] = []
    query_frames = query_onset.shape[1]
    for raw_slope in slopes:
        slope = float(raw_slope)
        if not math.isfinite(slope) or slope <= 0.0:
            continue
        source_length = query_frames * slope
        for start_frame in _candidate_positions(
            source,
            source_length_frames=source_length,
            step_seconds=candidate_step_seconds,
            search_start=source_search_start,
            search_end=source_search_end,
        ):
            onset_candidate = _resample_columns(source.onset, float(start_frame), float(source_length), query_frames)
            multiband_candidate = _resample_columns(source.multiband_flux, float(start_frame), float(source_length), query_frames)
            onset_score = _flattened_cosine(query_onset, onset_candidate)
            multiband_score = _flattened_cosine(query_multiband, multiband_candidate)
            # Equal-ish weighting is deliberate: both families originate from
            # percussive attack evidence and neither should dominate by scale.
            fused = 0.45 * onset_score + 0.55 * multiband_score
            agreement = int(onset_score >= 0.40) + int(multiband_score >= 0.40)
            source_start = start_frame * source.frame_seconds
            source_duration = (mix_end - mix_start) * slope
            raw.append(
                IndependentFineCandidate(
                    source_start=source_start,
                    source_end=source_start + source_duration,
                    source_center=source_start + source_duration / 2.0,
                    estimated_slope=slope,
                    onset_score=onset_score,
                    multiband_score=multiband_score,
                    fused_score=fused,
                    feature_agreement=agreement,
                )
            )
    if not raw:
        raise ValueError("independent onset retrieval produced no candidates")
    raw.sort(key=lambda item: item.fused_score, reverse=True)
    selected: list[IndependentFineCandidate] = []
    for candidate in raw:
        if any(abs(candidate.source_center - kept.source_center) < nms_separation_seconds for kept in selected):
            continue
        selected.append(candidate)
        if len(selected) >= max(2, int(top_k)):
            break
    top1 = selected[0]
    top2 = selected[1] if len(selected) > 1 else None
    margin = top1.fused_score - (top2.fused_score if top2 is not None else 0.0)
    ambiguous = bool(
        top1.fused_score < min_score
        or top1.feature_agreement < 2
        or (top2 is not None and margin < min_margin)
    )
    return IndependentFineResult(
        version=INDEPENDENT_FINE_VERSION,
        authority=INDEPENDENT_FINE_AUTHORITY,
        correlation_group=INDEPENDENT_FINE_CORRELATION_GROUP,
        mix_start=float(mix_start),
        mix_end=float(mix_end),
        top1=top1,
        top2=top2,
        candidates=tuple(selected),
        margin=float(margin),
        ambiguous=ambiguous,
        min_score=float(min_score),
        min_margin=float(min_margin),
    )


__all__ = [
    "INDEPENDENT_FINE_VERSION",
    "INDEPENDENT_FINE_AUTHORITY",
    "INDEPENDENT_FINE_CORRELATION_GROUP",
    "OnsetFeatureBundle",
    "IndependentFineCandidate",
    "IndependentFineResult",
    "extract_percussive_onset_features",
    "retrieve_independent_onset_window",
]
