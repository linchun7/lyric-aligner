"""Strict adapter between frozen v3.9 code and v4 domain contracts.

The legacy pipeline may call these helpers, but v4 modules must never depend on
the legacy monolith.  This keeps dependency direction one-way and makes v3.9 a
replaceable compatibility kernel rather than the architectural center.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lyric_aligner import __version__
from lyric_aligner.assets.bindings import ResolvedAssetBinding
from lyric_aligner.config import DEFAULT_V4_PROFILE, V4CalibrationProfile
from lyric_aligner.contracts.artifacts import validate_artifact_output
from lyric_aligner.pipeline.context import PipelineContext, build_pipeline_context
from lyric_aligner.text.canonical_lyrics import CanonicalLine, parse_canonical_lyrics

LEGACY_ALGORITHM_VERSION = "3.9"


class LegacyBridgeError(ValueError):
    """Raised when v3.9 cannot safely consume a v4 artifact."""


def load_bridge_context(
    *,
    expected_task_fingerprint: str,
    track_assets_path: Path,
    asset_artifact_path: Path,
    profile: V4CalibrationProfile = DEFAULT_V4_PROFILE,
    verify_asset_files: bool = True,
) -> PipelineContext:
    assets = json.loads(track_assets_path.read_text(encoding="utf-8-sig"))
    artifact = json.loads(asset_artifact_path.read_text(encoding="utf-8-sig"))
    output_issues = validate_artifact_output(
        artifact,
        role="track_assets",
        path=track_assets_path,
    )
    if output_issues:
        raise LegacyBridgeError(
            "track_assets artifact output mismatch: " + "; ".join(output_issues)
        )
    try:
        return build_pipeline_context(
            expected_task_fingerprint=expected_task_fingerprint,
            track_assets_payload=assets,
            asset_artifact=artifact,
            profile=profile,
            verify_asset_files=verify_asset_files,
        )
    except ValueError as exc:
        raise LegacyBridgeError(str(exc)) from exc


def binding_for_ordinal(
    context: PipelineContext,
    ordinal: int,
) -> ResolvedAssetBinding:
    binding = context.binding_by_ordinal.get(int(ordinal))
    if binding is None:
        raise LegacyBridgeError(f"no resolved TrackOccurrence ordinal {ordinal}")
    return binding


def canonical_lines_for_ordinal(
    context: PipelineContext,
    ordinal: int,
) -> list[CanonicalLine]:
    binding = binding_for_ordinal(context, ordinal)
    return parse_canonical_lyrics(
        Path(binding.canonical_lyric_path),
        original_index_by_timestamp=binding.original_index_by_timestamp,
    )


def legacy_bridge_metadata(context: PipelineContext) -> dict[str, Any]:
    """Fields legacy-produced artifacts should persist for lineage/auditing."""

    return {
        "legacy_algorithm_version": LEGACY_ALGORITHM_VERSION,
        "v4_algorithm_version": __version__,
        "calibration_profile_version": context.calibration_profile_version,
        "calibration_profile_id": context.calibration_profile_id,
        "asset_artifact_id": context.asset_artifact.artifact_id,
    }
