"""Versioned v4 algorithm/calibration configuration.

Alpha code originally carried bootstrap thresholds as function/CLI defaults.
That is acceptable for experiments but not for long-lived reproducibility.  A
profile is now a first-class identity that can be recorded in every artifact and
replaced by calibration/blind-test-derived profiles without editing algorithms.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class AssetResolverConfig:
    min_score: float = 0.76
    min_margin: float = 0.08


@dataclass(frozen=True)
class CoarseAlignmentConfig:
    sr: int = 11025
    hop_length: int = 1024
    window_seconds: float = 6.0
    step_seconds: float = 3.0
    candidate_step_seconds: float = 0.75
    slope_minimum: float = 0.65
    slope_maximum: float = 1.80
    slope_step: float = 0.10
    min_score: float = 0.72
    min_margin: float = 0.035


@dataclass(frozen=True)
class FineAlignmentConfig:
    sr: int = 16000
    hop_length: int = 256
    source_radius_seconds: float = 1.25
    slope_radius: float = 0.08
    slope_step: float = 0.02
    candidate_step_seconds: float = 0.05
    min_score: float = 0.62
    min_margin: float = 0.012


@dataclass(frozen=True)
class TransitionConfig:
    min_score: float = 0.72
    min_margin: float = 0.02
    min_overlap_seconds: float = 0.75


@dataclass(frozen=True)
class TimeWarpConfig:
    bpm_prior_strength: float = 0.02
    max_continuous_rate: float = 2.0
    min_excess_source_jump: float = 1.5
    min_piecewise_improvement: float = 0.25
    minimum_feature_families: int = 2
    drift_threshold: float = 0.30
    residual_threshold: float = 0.25
    complexity_penalty: float = 0.035


@dataclass(frozen=True)
class V4CalibrationProfile:
    """All tunable v4 bootstrap values with one reproducible profile identity."""

    profile_version: str = "bootstrap-2026-08-17"
    asset_resolver: AssetResolverConfig = field(default_factory=AssetResolverConfig)
    coarse: CoarseAlignmentConfig = field(default_factory=CoarseAlignmentConfig)
    fine: FineAlignmentConfig = field(default_factory=FineAlignmentConfig)
    transition: TransitionConfig = field(default_factory=TransitionConfig)
    timewarp: TimeWarpConfig = field(default_factory=TimeWarpConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def profile_id(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


DEFAULT_V4_PROFILE = V4CalibrationProfile()
