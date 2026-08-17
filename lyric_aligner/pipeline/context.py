"""Typed v4 pipeline context and stage identity."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from lyric_aligner import __version__
from lyric_aligner.assets.bindings import ResolvedAssetBinding, bindings_from_payload
from lyric_aligner.config import (
    DEFAULT_V4_PROFILE,
    V4CalibrationProfile,
    profile_from_dict,
)
from lyric_aligner.contracts.artifacts import validate_upstream_artifact


class PipelineContextError(ValueError):
    """Raised when stage identities cannot be assembled into one v4 context."""


@dataclass(frozen=True)
class StageArtifactRef:
    stage: str
    artifact_id: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PipelineContext:
    task_fingerprint_sha256: str
    algorithm_version: str
    profile: V4CalibrationProfile
    calibration_profile_version: str
    calibration_profile_id: str
    asset_artifact: StageArtifactRef
    bindings: tuple[ResolvedAssetBinding, ...]

    @property
    def binding_by_ordinal(self) -> dict[int, ResolvedAssetBinding]:
        return {item.ordinal: item for item in self.bindings}

    @property
    def binding_by_occurrence_id(self) -> dict[str, ResolvedAssetBinding]:
        return {item.occurrence_id: item for item in self.bindings}

    def artifact_config(self) -> dict[str, Any]:
        return {
            "calibration_profile_version": self.calibration_profile_version,
            "calibration_profile_id": self.calibration_profile_id,
            "asset_artifact_id": self.asset_artifact.artifact_id,
        }


def _payload_profile(track_assets_payload: dict) -> V4CalibrationProfile:
    embedded = track_assets_payload.get("calibration_profile")
    if embedded is not None:
        return profile_from_dict(embedded)
    # Backward compatibility for early a2 artifacts that predate embedded
    # profiles. Only the exact built-in bootstrap identity can be reconstructed.
    recorded = str(track_assets_payload.get("calibration_profile_id") or "")
    if recorded == DEFAULT_V4_PROFILE.profile_id:
        return DEFAULT_V4_PROFILE
    raise PipelineContextError(
        "track_assets does not embed its calibration profile; rerun v4_resolve_assets"
    )


def build_pipeline_context(
    *,
    expected_task_fingerprint: str,
    track_assets_payload: dict,
    asset_artifact: dict,
    profile: V4CalibrationProfile | None = None,
    verify_asset_files: bool = False,
) -> PipelineContext:
    issues = validate_upstream_artifact(
        asset_artifact,
        expected_task_fingerprint=expected_task_fingerprint,
        expected_algorithm_version=__version__,
        expected_stage="asset_resolution",
    )
    if issues:
        raise PipelineContextError("invalid asset artifact: " + "; ".join(issues))
    if str(track_assets_payload.get("task_fingerprint_sha256")) != expected_task_fingerprint:
        raise PipelineContextError("track_assets task fingerprint mismatch")
    if str(track_assets_payload.get("algorithm_version")) != __version__:
        raise PipelineContextError("track_assets algorithm version mismatch")

    embedded_profile = _payload_profile(track_assets_payload)
    if profile is not None and profile.profile_id != embedded_profile.profile_id:
        raise PipelineContextError(
            "supplied calibration profile differs from the TrackAsset profile"
        )
    profile = embedded_profile
    recorded_profile_id = str(track_assets_payload.get("calibration_profile_id") or "")
    recorded_profile_version = str(track_assets_payload.get("calibration_profile_version") or "")
    if recorded_profile_id != profile.profile_id:
        raise PipelineContextError("track_assets calibration profile hash mismatch")
    if recorded_profile_version != profile.profile_version:
        raise PipelineContextError("track_assets calibration profile version mismatch")
    artifact_config = asset_artifact.get("normalized_config", {})
    if str(artifact_config.get("calibration_profile_id") or "") != profile.profile_id:
        raise PipelineContextError("asset artifact calibration profile mismatch")

    bindings = tuple(
        bindings_from_payload(track_assets_payload, verify_files=verify_asset_files)
    )
    return PipelineContext(
        task_fingerprint_sha256=expected_task_fingerprint,
        algorithm_version=__version__,
        profile=profile,
        calibration_profile_version=profile.profile_version,
        calibration_profile_id=profile.profile_id,
        asset_artifact=StageArtifactRef(
            stage="asset_resolution",
            artifact_id=str(asset_artifact["artifact_id"]),
        ),
        bindings=bindings,
    )
