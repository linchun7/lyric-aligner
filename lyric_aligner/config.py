"""Versioned v4 algorithm/calibration configuration.

Production stages consume one explicit calibration profile. Ad-hoc CLI tuning
may be used for experiments, but those overrides must be recorded separately
and are not considered a named/releasable calibration profile.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar


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
    search_margin_seconds: float = 10.0
    minimum_feature_agreement: int = 2
    merge_gap_seconds: float = 0.35


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
class RenderConfig:
    """Conservative bootstrap rules for package-native final subtitle rendering."""

    minimum_cue_duration_ms: int = 250
    maximum_line_duration_ms: int = 12000
    open_line_duration_ms: int = 5000
    word_timing_tail_ms: int = 120


@dataclass(frozen=True)
class V4CalibrationProfile:
    """All tunable v4 production-bootstrap values with one profile identity."""

    profile_version: str = "production-bootstrap-2026-08-17-a4"
    asset_resolver: AssetResolverConfig = field(default_factory=AssetResolverConfig)
    coarse: CoarseAlignmentConfig = field(default_factory=CoarseAlignmentConfig)
    fine: FineAlignmentConfig = field(default_factory=FineAlignmentConfig)
    transition: TransitionConfig = field(default_factory=TransitionConfig)
    timewarp: TimeWarpConfig = field(default_factory=TimeWarpConfig)
    render: RenderConfig = field(default_factory=RenderConfig)

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


class CalibrationProfileError(ValueError):
    """Raised when a calibration profile is incomplete or invalid."""


T = TypeVar("T")


def _strict_dataclass(cls: type[T], payload: Any, label: str) -> T:
    if not isinstance(payload, dict):
        raise CalibrationProfileError(f"{label} must be an object")
    expected = {item.name for item in fields(cls)}
    actual = set(payload)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise CalibrationProfileError(f"invalid {label}: " + "; ".join(details))
    try:
        return cls(**payload)
    except (TypeError, ValueError) as exc:
        raise CalibrationProfileError(f"invalid {label}: {exc}") from exc


def profile_from_dict(payload: Any) -> V4CalibrationProfile:
    if not isinstance(payload, dict):
        raise CalibrationProfileError("calibration profile must be an object")
    expected = {
        "profile_version",
        "asset_resolver",
        "coarse",
        "fine",
        "transition",
        "timewarp",
        "render",
    }
    actual = set(payload)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise CalibrationProfileError("invalid calibration profile: " + "; ".join(details))
    profile_version = str(payload["profile_version"]).strip()
    if not profile_version:
        raise CalibrationProfileError("profile_version must be non-empty")
    profile = V4CalibrationProfile(
        profile_version=profile_version,
        asset_resolver=_strict_dataclass(
            AssetResolverConfig, payload["asset_resolver"], "asset_resolver"
        ),
        coarse=_strict_dataclass(CoarseAlignmentConfig, payload["coarse"], "coarse"),
        fine=_strict_dataclass(FineAlignmentConfig, payload["fine"], "fine"),
        transition=_strict_dataclass(
            TransitionConfig, payload["transition"], "transition"
        ),
        timewarp=_strict_dataclass(TimeWarpConfig, payload["timewarp"], "timewarp"),
        render=_strict_dataclass(RenderConfig, payload["render"], "render"),
    )
    validate_profile(profile)
    return profile


def validate_profile(profile: V4CalibrationProfile) -> None:
    for label, value in (
        ("asset_resolver.min_score", profile.asset_resolver.min_score),
        ("asset_resolver.min_margin", profile.asset_resolver.min_margin),
        ("coarse.min_score", profile.coarse.min_score),
        ("coarse.min_margin", profile.coarse.min_margin),
        ("fine.min_score", profile.fine.min_score),
        ("fine.min_margin", profile.fine.min_margin),
        ("transition.min_score", profile.transition.min_score),
        ("transition.min_margin", profile.transition.min_margin),
        ("timewarp.min_piecewise_improvement", profile.timewarp.min_piecewise_improvement),
    ):
        if not 0.0 <= float(value) <= 1.0:
            raise CalibrationProfileError(f"{label} must be within [0,1]")
    if profile.coarse.slope_minimum <= 0 or profile.coarse.slope_maximum <= profile.coarse.slope_minimum:
        raise CalibrationProfileError("coarse slope range is invalid")
    if profile.coarse.slope_step <= 0:
        raise CalibrationProfileError("coarse.slope_step must be positive")
    if profile.transition.search_margin_seconds <= 0:
        raise CalibrationProfileError("transition.search_margin_seconds must be positive")
    if profile.transition.min_overlap_seconds <= 0:
        raise CalibrationProfileError("transition.min_overlap_seconds must be positive")
    if profile.transition.minimum_feature_agreement < 1:
        raise CalibrationProfileError("transition.minimum_feature_agreement must be >= 1")
    if profile.transition.merge_gap_seconds < 0:
        raise CalibrationProfileError("transition.merge_gap_seconds must be >= 0")
    if profile.timewarp.max_continuous_rate <= 0:
        raise CalibrationProfileError("timewarp.max_continuous_rate must be positive")
    if profile.timewarp.minimum_feature_families < 1:
        raise CalibrationProfileError("timewarp.minimum_feature_families must be >= 1")
    if profile.render.minimum_cue_duration_ms <= 0:
        raise CalibrationProfileError("render.minimum_cue_duration_ms must be positive")
    if profile.render.maximum_line_duration_ms < profile.render.minimum_cue_duration_ms:
        raise CalibrationProfileError(
            "render.maximum_line_duration_ms must be >= minimum_cue_duration_ms"
        )
    if not (
        profile.render.minimum_cue_duration_ms
        <= profile.render.open_line_duration_ms
        <= profile.render.maximum_line_duration_ms
    ):
        raise CalibrationProfileError(
            "render.open_line_duration_ms must be within cue duration bounds"
        )
    if profile.render.word_timing_tail_ms < 0:
        raise CalibrationProfileError("render.word_timing_tail_ms must be >= 0")


def load_profile(path: Path | None) -> V4CalibrationProfile:
    if path is None:
        return DEFAULT_V4_PROFILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationProfileError(f"cannot read calibration profile {path}: {exc}") from exc
    return profile_from_dict(payload)


def write_profile(path: Path, profile: V4CalibrationProfile = DEFAULT_V4_PROFILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def calibration_overrides(base: Any, effective: dict[str, Any]) -> dict[str, Any]:
    """Return only values that differ from a named profile section."""

    base_payload = asdict(base)
    return {
        key: value
        for key, value in effective.items()
        if key in base_payload and value != base_payload[key]
    }
