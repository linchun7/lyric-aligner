"""Bounded source<->mix acoustic evidence for Pro selective repair.

Unlike Full V4 coarse alignment, this executor starts from a Smart-selected cue,
a narrow source window near the canonical lyric time, and an optional known
stretch ratio. It never scans the whole 40-60 minute program or whole source
track merely to investigate one unresolved cue.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import librosa
import numpy as np

from lyric_aligner.audio.features import extract_harmonic_features, retrieve_coarse_window


LOCAL_ACOUSTIC_EVIDENCE_SCHEMA_VERSION = "1.0"


class LocalAcousticMatchError(ValueError):
    """Raised when a bounded acoustic job cannot be executed safely."""


@dataclass(frozen=True)
class LocalAcousticMatchConfig:
    sr: int = 16000
    hop_length: int = 512
    slope_radius: float = 0.06
    slope_step: float = 0.01
    no_prior_min_slope: float = 0.75
    no_prior_max_slope: float = 1.35
    no_prior_step: float = 0.05
    candidate_step_seconds: float = 0.05
    source_boundary_margin_seconds: float = 0.25
    min_score: float = 0.62
    min_margin: float = 0.012

    def validate(self) -> None:
        if self.sr < 8000:
            raise LocalAcousticMatchError("sr must be >= 8000")
        if self.hop_length < 64:
            raise LocalAcousticMatchError("hop_length must be >= 64")
        for label, value in (
            ("slope_radius", self.slope_radius),
            ("slope_step", self.slope_step),
            ("no_prior_min_slope", self.no_prior_min_slope),
            ("no_prior_max_slope", self.no_prior_max_slope),
            ("no_prior_step", self.no_prior_step),
            ("candidate_step_seconds", self.candidate_step_seconds),
            ("source_boundary_margin_seconds", self.source_boundary_margin_seconds),
            ("min_score", self.min_score),
            ("min_margin", self.min_margin),
        ):
            if not math.isfinite(float(value)):
                raise LocalAcousticMatchError(f"{label} must be finite")
        if self.slope_radius <= 0 or self.slope_step <= 0:
            raise LocalAcousticMatchError("slope radius/step must be positive")
        if not 0.45 <= self.no_prior_min_slope < self.no_prior_max_slope <= 2.2:
            raise LocalAcousticMatchError("invalid no-prior slope range")
        if (
            self.no_prior_step <= 0
            or self.candidate_step_seconds <= 0
            or self.source_boundary_margin_seconds <= 0
        ):
            raise LocalAcousticMatchError("search steps must be positive")
        if not 0.0 <= self.min_score <= 1.0 or not 0.0 <= self.min_margin <= 1.0:
            raise LocalAcousticMatchError("score/margin thresholds must be within [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _window(row: Mapping[str, Any], key: str) -> tuple[int, int]:
    raw = row.get(key)
    if not isinstance(raw, list) or len(raw) != 2:
        raise LocalAcousticMatchError(f"job has no valid {key}")
    try:
        start, end = int(raw[0]), int(raw[1])
    except (TypeError, ValueError) as exc:
        raise LocalAcousticMatchError(f"job {key} is invalid") from exc
    if start < 0 or end <= start:
        raise LocalAcousticMatchError(f"job {key} is invalid")
    return start, end


def _rate(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0.5 <= number <= 2.0:
        return None
    return number


def _slope_candidates(rate_prior: float | None, config: LocalAcousticMatchConfig) -> list[float]:
    if rate_prior is None:
        minimum = config.no_prior_min_slope
        maximum = config.no_prior_max_slope
        step = config.no_prior_step
    else:
        minimum = max(0.45, rate_prior - config.slope_radius)
        maximum = min(2.2, rate_prior + config.slope_radius)
        step = config.slope_step
    values: list[float] = []
    current = minimum
    while current <= maximum + step / 2:
        values.append(round(current, 6))
        current += step
    if rate_prior is not None:
        values.append(round(rate_prior, 6))
    return sorted(set(values))


def _default_audio_loader(path: Path, *, sr: int, start_ms: int, end_ms: int) -> np.ndarray:
    try:
        audio, _ = librosa.load(
            path,
            sr=sr,
            mono=True,
            offset=start_ms / 1000.0,
            duration=(end_ms - start_ms) / 1000.0,
        )
    except Exception as exc:
        raise LocalAcousticMatchError(f"cannot decode bounded audio {path}: {exc}") from exc
    return np.asarray(audio, dtype=np.float32)


def execute_local_source_match_jobs(
    *,
    mix_audio_path: Path,
    plan: Mapping[str, Any],
    source_audio_by_source_ordinal: Mapping[int, Path],
    config: LocalAcousticMatchConfig | None = None,
    audio_loader: Callable[..., np.ndarray] | None = None,
) -> dict[str, Any]:
    """Execute only jobs requesting ``source_local_acoustic_match``.

    The returned predicted cue time is acoustic evidence only. This executor
    does not rewrite SRT and does not claim release authority.
    """

    config = config or LocalAcousticMatchConfig()
    config.validate()
    if plan.get("mode") != "plan_only" or plan.get("backend_execution_performed") is not False:
        raise LocalAcousticMatchError("input is not an unexecuted plan")
    if not mix_audio_path.is_file():
        raise LocalAcousticMatchError(f"mix audio does not exist: {mix_audio_path}")
    jobs = plan.get("jobs")
    if not isinstance(jobs, list):
        raise LocalAcousticMatchError("plan jobs must be a list")

    selected = [
        row
        for row in jobs
        if isinstance(row, Mapping)
        and "source_local_acoustic_match" in (row.get("requested_capabilities") or [])
    ]
    loader = audio_loader or _default_audio_loader
    results: list[dict[str, Any]] = []
    for job in selected:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            raise LocalAcousticMatchError("local acoustic job is missing job_id")
        try:
            source_ordinal = int(job["source_ordinal"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LocalAcousticMatchError(f"job {job_id} has no source ordinal") from exc
        source_path = source_audio_by_source_ordinal.get(source_ordinal)
        if source_path is None or not source_path.is_file():
            raise LocalAcousticMatchError(
                f"job {job_id} source audio missing for source ordinal {source_ordinal}"
            )

        mix_start_ms, mix_end_ms = _window(job, "mix_window_ms")
        source_start_ms, source_end_ms = _window(job, "source_window_ms")
        expected_source = job.get("expected_source_time_ms")
        if expected_source is None:
            raise LocalAcousticMatchError(f"job {job_id} has no expected source time")
        try:
            expected_source_ms = int(expected_source)
        except (TypeError, ValueError) as exc:
            raise LocalAcousticMatchError(f"job {job_id} expected source time is invalid") from exc
        if not source_start_ms <= expected_source_ms <= source_end_ms:
            raise LocalAcousticMatchError(
                f"job {job_id} expected source time is outside source window"
            )

        mix_audio = loader(
            mix_audio_path,
            sr=config.sr,
            start_ms=mix_start_ms,
            end_ms=mix_end_ms,
        )
        source_audio = loader(
            source_path,
            sr=config.sr,
            start_ms=source_start_ms,
            end_ms=source_end_ms,
        )
        if len(mix_audio) < config.sr * 2 or len(source_audio) < config.sr * 2:
            raise LocalAcousticMatchError(f"job {job_id} bounded audio is too short")

        mix_features = extract_harmonic_features(
            mix_audio,
            sr=config.sr,
            hop_length=config.hop_length,
        )
        source_features = extract_harmonic_features(
            source_audio,
            sr=config.sr,
            hop_length=config.hop_length,
        )
        mix_duration = len(mix_audio) / config.sr
        rate_prior = _rate(job.get("rate_prior"))
        retrieval = retrieve_coarse_window(
            mix_features,
            source_features,
            mix_start=0.0,
            mix_end=mix_duration,
            slopes=_slope_candidates(rate_prior, config),
            source_search_start=0.0,
            source_search_end=source_features.duration_seconds,
            candidate_step_seconds=config.candidate_step_seconds,
            top_k=5,
            nms_separation_seconds=0.20,
            min_score=config.min_score,
            min_margin=config.min_margin,
        )
        best = retrieval.top1
        matched_source_start_ms = source_start_ms + int(round(best.source_start * 1000.0))
        predicted_mix_start_ms = mix_start_ms + int(
            round((expected_source_ms - matched_source_start_ms) / best.estimated_slope)
        )
        editor_start = job.get("editor_cue_start_ms")
        residual = None
        if editor_start is not None:
            residual = int(editor_start) - predicted_mix_start_ms
        reliable = not retrieval.ambiguous and best.feature_agreement >= 2
        results.append(
            {
                "job_id": job_id,
                "source_ordinal": source_ordinal,
                "cue_ordinal": job.get("cue_ordinal"),
                "mix_window_ms": [mix_start_ms, mix_end_ms],
                "source_window_ms": [source_start_ms, source_end_ms],
                "rate_prior": rate_prior,
                "estimated_slope": round(float(best.estimated_slope), 6),
                "fused_score": round(float(best.fused_score), 6),
                "chroma_score": round(float(best.chroma_score), 6),
                "mfcc_score": round(float(best.mfcc_score), 6),
                "feature_agreement": int(best.feature_agreement),
                "margin": round(float(retrieval.margin), 6),
                "ambiguous": bool(retrieval.ambiguous),
                "reliable_local_match": reliable,
                "matched_source_start_ms": matched_source_start_ms,
                "expected_source_time_ms": expected_source_ms,
                "predicted_mix_start_ms": predicted_mix_start_ms,
                "editor_start_residual_ms": residual,
                "timing_mutation_performed": False,
            }
        )

    return {
        "schema_version": LOCAL_ACOUSTIC_EVIDENCE_SCHEMA_VERSION,
        "backend": "bounded_source_mix_harmonic_retrieval",
        "config": config.to_dict(),
        "job_count": len(results),
        "timing_mutation_performed": False,
        "jobs": results,
    }
