"""Low-cost harmonic audio features for coarse Source-to-Mix retrieval."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable

import librosa
import numpy as np


@dataclass(frozen=True)
class FeatureBundle:
    sr: int
    hop_length: int
    duration_seconds: float
    chroma: np.ndarray
    mfcc: np.ndarray

    @property
    def frame_seconds(self) -> float:
        return self.hop_length / self.sr

    @property
    def frame_count(self) -> int:
        return int(self.chroma.shape[1])


@dataclass(frozen=True)
class RetrievalCandidate:
    source_start: float
    source_end: float
    source_center: float
    estimated_slope: float
    chroma_score: float
    mfcc_score: float
    fused_score: float
    feature_agreement: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalResult:
    mix_start: float
    mix_end: float
    mix_center: float
    top1: RetrievalCandidate
    top2: RetrievalCandidate | None
    margin: float
    ambiguous: bool
    min_score: float
    min_margin: float

    def to_dict(self) -> dict:
        return {
            "mix_start": self.mix_start,
            "mix_end": self.mix_end,
            "mix_center": self.mix_center,
            "top1": self.top1.to_dict(),
            "top2": self.top2.to_dict() if self.top2 else None,
            "margin": self.margin,
            "ambiguous": self.ambiguous,
            "min_score": self.min_score,
            "min_margin": self.min_margin,
        }


def _column_normalize(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=0, keepdims=True)
    return matrix / np.maximum(norms, 1e-8)


def extract_harmonic_features(
    audio: np.ndarray,
    *,
    sr: int,
    hop_length: int = 2048,
    n_mfcc: int = 13,
) -> FeatureBundle:
    """Extract click-resistant coarse features.

    HPSS suppresses much of a synthetic click/metronome before Chroma CENS and
    MFCC are computed. Percussive/onset evidence is intentionally not a primary
    feature here; it can be added later as a separately weighted family.
    """

    y = np.asarray(audio, dtype=np.float32)
    if y.ndim != 1 or y.size < max(2048, hop_length * 3):
        raise ValueError("audio must be a sufficiently long mono waveform")
    harmonic, _ = librosa.effects.hpss(y)
    # Keep the CQT basis below Nyquist for low-rate test/fast-path audio.
    c1_hz = 32.70319566257483
    n_octaves = max(3, min(7, int(math.floor(math.log2((sr / 2) / c1_hz)))))
    chroma = librosa.feature.chroma_cens(
        y=harmonic,
        sr=sr,
        hop_length=hop_length,
        n_octaves=n_octaves,
    ).astype(np.float32)
    mfcc = librosa.feature.mfcc(
        y=harmonic,
        sr=sr,
        n_mfcc=n_mfcc,
        hop_length=hop_length,
    ).astype(np.float32)
    if mfcc.shape[0] > 1:
        mfcc = mfcc[1:]
    return FeatureBundle(
        sr=sr,
        hop_length=hop_length,
        duration_seconds=float(len(y) / sr),
        chroma=_column_normalize(chroma),
        mfcc=_column_normalize(mfcc),
    )


def slope_grid(
    *,
    minimum: float = 0.65,
    maximum: float = 1.80,
    step: float = 0.10,
    bpm_prior: float | None = None,
) -> list[float]:
    if minimum <= 0 or maximum <= minimum or step <= 0:
        raise ValueError("invalid slope search range")
    values = set(float(value) for value in np.arange(minimum, maximum + step / 2, step))
    if bpm_prior is not None and minimum <= bpm_prior <= maximum:
        # Add denser local probes, but retain the global grid so a wrong BPM
        # prior cannot exclude the correct audio match.
        values.add(float(bpm_prior))
        values.add(float(max(minimum, bpm_prior - step / 2)))
        values.add(float(min(maximum, bpm_prior + step / 2)))
    return sorted(round(value, 6) for value in values)


def _resample_columns(matrix: np.ndarray, start: float, length: float, output_frames: int) -> np.ndarray:
    if output_frames < 2 or length <= 1:
        raise ValueError("feature window is too short")
    positions = np.linspace(start, start + length - 1, output_frames)
    base = np.arange(matrix.shape[1], dtype=np.float64)
    rows = [np.interp(positions, base, feature_row) for feature_row in matrix]
    return _column_normalize(np.asarray(rows, dtype=np.float32))


def _cosine_score(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape:
        raise ValueError("feature matrices must have equal shapes")
    cosine = np.sum(left * right, axis=0)
    return float(np.clip(np.mean((cosine + 1.0) / 2.0), 0.0, 1.0))


def _candidate_positions(
    source: FeatureBundle,
    *,
    source_length_frames: float,
    step_seconds: float,
    search_start: float,
    search_end: float,
) -> Iterable[float]:
    frame_step = max(1, int(round(step_seconds / source.frame_seconds)))
    first = max(0, int(math.floor(search_start / source.frame_seconds)))
    final_time = min(search_end, source.duration_seconds)
    last = int(math.floor(final_time / source.frame_seconds - source_length_frames))
    if last < first:
        return []
    return range(first, last + 1, frame_step)


def retrieve_coarse_window(
    mix: FeatureBundle,
    source: FeatureBundle,
    *,
    mix_start: float,
    mix_end: float,
    slopes: Iterable[float] | None = None,
    bpm_prior: float | None = None,
    source_search_start: float = 0.0,
    source_search_end: float | None = None,
    candidate_step_seconds: float = 0.75,
    top_k: int = 8,
    nms_separation_seconds: float = 2.0,
    min_score: float = 0.72,
    min_margin: float = 0.035,
) -> RetrievalResult:
    if mix.sr != source.sr or mix.hop_length != source.hop_length:
        raise ValueError("mix/source FeatureBundle sampling parameters must match")
    if mix_end <= mix_start:
        raise ValueError("mix_end must be after mix_start")
    mix_start_frame = int(round(mix_start / mix.frame_seconds))
    mix_end_frame = int(round(mix_end / mix.frame_seconds))
    if mix_start_frame < 0 or mix_end_frame > mix.frame_count:
        raise ValueError("mix retrieval window is outside the feature bundle")
    query_chroma = mix.chroma[:, mix_start_frame:mix_end_frame]
    query_mfcc = mix.mfcc[:, mix_start_frame:mix_end_frame]
    if query_chroma.shape[1] < 4:
        raise ValueError("mix retrieval window is too short")

    slope_values = list(slopes or slope_grid(bpm_prior=bpm_prior))
    search_end = source.duration_seconds if source_search_end is None else source_search_end
    raw: list[RetrievalCandidate] = []
    query_frames = query_chroma.shape[1]
    for slope in slope_values:
        if slope <= 0:
            continue
        source_length = query_frames * slope
        for start_frame in _candidate_positions(
            source,
            source_length_frames=source_length,
            step_seconds=candidate_step_seconds,
            search_start=source_search_start,
            search_end=search_end,
        ):
            candidate_chroma = _resample_columns(
                source.chroma, float(start_frame), float(source_length), query_frames
            )
            candidate_mfcc = _resample_columns(
                source.mfcc, float(start_frame), float(source_length), query_frames
            )
            chroma_score = _cosine_score(query_chroma, candidate_chroma)
            mfcc_score = _cosine_score(query_mfcc, candidate_mfcc)
            fused = 0.78 * chroma_score + 0.22 * mfcc_score
            agreement = int(chroma_score >= 0.68) + int(mfcc_score >= 0.58)
            source_start = start_frame * source.frame_seconds
            source_duration = (mix_end - mix_start) * slope
            raw.append(
                RetrievalCandidate(
                    source_start=source_start,
                    source_end=source_start + source_duration,
                    source_center=source_start + source_duration / 2,
                    estimated_slope=float(slope),
                    chroma_score=chroma_score,
                    mfcc_score=mfcc_score,
                    fused_score=fused,
                    feature_agreement=agreement,
                )
            )
    if not raw:
        raise ValueError("coarse retrieval produced no candidates")

    raw.sort(key=lambda item: item.fused_score, reverse=True)
    selected: list[RetrievalCandidate] = []
    for candidate in raw:
        if any(
            abs(candidate.source_center - kept.source_center) < nms_separation_seconds
            for kept in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= max(2, top_k):
            break
    top1 = selected[0]
    top2 = selected[1] if len(selected) > 1 else None
    margin = top1.fused_score - (top2.fused_score if top2 else 0.0)
    ambiguous = (
        top1.fused_score < min_score
        or top1.feature_agreement < 2
        or (top2 is not None and margin < min_margin)
    )
    return RetrievalResult(
        mix_start=mix_start,
        mix_end=mix_end,
        mix_center=(mix_start + mix_end) / 2,
        top1=top1,
        top2=top2,
        margin=margin,
        ambiguous=ambiguous,
        min_score=min_score,
        min_margin=min_margin,
    )
