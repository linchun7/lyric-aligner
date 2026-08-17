"""Typed v4 pipeline context and stage identity.

The context is deliberately small: it does not execute a stage.  It binds one
task fingerprint, one calibration profile, one resolved-asset artifact and the
exact per-occurrence asset identities that every downstream stage must consume.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from lyric_aligner import __version__
from lyric_aligner.assets.bindings import ResolvedAssetBinding, bindings_from_payload
from lyric_aligner.config import DEFAULT_V4_PROFILE, V4CalibrationProfile
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
        """Stable config fields every downstream artifact should record."""

        return {
            "calibration_profile_version": self.calibration_profile_version,
            "calibration_profile_id": self.calibration_profile_id,
            "asset_artifact_id": self.asset_artifact.artifact_id,
        }


def build_pipeline_context(
    *,
    expected_task_fingerprint: str,
    track_assets_payload: dict,
    asset_artifact: dict,
    profile: V4CalibrationProfile = DEFAULT_V4_PROFILE,
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

    bindings = tuple(
        bindings_from_payload(
            track_assets_payload,
            verify_files=verify_asset_files,
        )
    )
    return PipelineContext(
        task_fingerprint_sha256=expected_task_fingerprint,
        algorithm_version=__version__,
        calibration_profile_version=profile.profile_version,
        calibration_profile_id=profile.profile_id,
        asset_artifact=StageArtifactRef(
            stage="asset_resolution",
            artifact_id=str(asset_artifact["artifact_id"]),
        ),
        bindings=bindings,
    )
